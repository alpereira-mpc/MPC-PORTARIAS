"""Public access-request rules. Authorization remains manual."""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from database.access import EMAIL_RE, AccessStore, normalize_email
from database.access_requests import AccessRequestStore
from services.mail import MailError, send_mail

INSTITUTIONAL_DOMAIN = "tce.pb.gov.br"
INSTITUTIONAL_TZ = ZoneInfo("America/Recife")
ADMIN_INBOX = "mpc@tce.pb.gov.br"
MIN_NAME_LENGTH = 3
MIN_UNIT_LENGTH = 3
OTHER_UNIT = "Outra unidade do MPC-PB"
GABINETE_OPTIONS = (
    "Procuradoria-Geral — PROGE",
    "Gabinete Luciano Andrade Farias — LAF",
    "Gabinete Bradson Tibério Luna Camelo — BTLC",
    "Gabinete Isabella Barbosa Marinho Falcão — IBMF",
    "Gabinete Sheyla Barreto Braga de Queiroz — SBBQ",
    "Gabinete Marcílio Toscano Franca Filho — MTFF",
    "Gabinete Manoel Antônio dos Santos Neto — MASN",
    OTHER_UNIT,
)
ALREADY_REGISTERED = (
    "Este e-mail já possui cadastro no portal. Utilize a opção de login."
)
DUPLICATE_PENDING = (
    "Já existe uma solicitação de acesso pendente para este e-mail."
)
SUCCESS_TITLE = "Solicitação enviada com sucesso"
SUCCESS_BODY = (
    "Sua solicitação de acesso foi encaminhada à administração do MPC-PB. "
    "Após a análise e liberação do cadastro, você poderá acessar o portal "
    "utilizando sua conta institucional @tce.pb.gov.br."
)
NOTIFY_FAILED = (
    "Sua solicitação foi registrada, mas houve um problema na notificação administrativa."
)
MAIL_SUBJECT = "[Ferramentas MPC-PB] Nova solicitação de acesso"


@dataclass(frozen=True)
class AccessRequestOutcome:
    ok: bool
    code: str
    message: str
    title: str = ""
    record: dict | None = None


def normalize_request_email(value):
    email = normalize_email(value)
    if not email or not EMAIL_RE.match(email):
        raise ValueError("Informe um e-mail institucional válido.")
    local, separator, domain = email.partition("@")
    if not separator or not local or domain != INSTITUTIONAL_DOMAIN:
        raise ValueError("Informe um e-mail do domínio @tce.pb.gov.br.")
    return email


def validate_access_request(nome, email, gabinete, unidade_outro=None):
    name = " ".join((nome or "").split())
    if not name or len(name) < MIN_NAME_LENGTH:
        raise ValueError("Informe o nome completo.")
    address = normalize_request_email(email)
    office = (gabinete or "").strip()
    if office not in GABINETE_OPTIONS:
        raise ValueError("Selecione o gabinete ou unidade.")
    extra = " ".join((unidade_outro or "").split())
    if office == OTHER_UNIT:
        if not extra or len(extra) < MIN_UNIT_LENGTH:
            raise ValueError("Informe a unidade.")
    else:
        extra = None
    return {
        "nome": name,
        "email": address,
        "gabinete": office,
        "unidade_outro": extra,
    }


def _mail_body(record):
    created = record.get("created_at") or ""
    try:
        moment = datetime.fromisoformat(created)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=INSTITUTIONAL_TZ)
        stamped = moment.astimezone(INSTITUTIONAL_TZ).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        stamped = created
    lines = [
        "Nova solicitação de acesso ao Ferramentas MPC-PB.",
        "",
        f"Nome: {record['nome']}",
        f"E-mail: {record['email']}",
        f"Gabinete / Unidade: {record['gabinete']}",
    ]
    if record.get("unidade_outro"):
        lines.append(f"Unidade informada: {record['unidade_outro']}")
    lines.extend(
        [
            f"Data da solicitação: {stamped}",
            "",
            "O cadastro e as permissões deverão ser realizados manualmente pelo administrador.",
        ]
    )
    return "\n".join(lines)


def notify_access_request(record):
    send_mail(MAIL_SUBJECT, _mail_body(record), ADMIN_INBOX)


def submit_access_request(store, nome, email, gabinete, unidade_outro=None):
    try:
        payload = validate_access_request(nome, email, gabinete, unidade_outro)
    except ValueError as exc:
        return AccessRequestOutcome(False, "invalid", str(exc))
    if AccessStore(store).get_by_email(payload["email"]):
        return AccessRequestOutcome(False, "already_registered", ALREADY_REGISTERED)
    requests = AccessRequestStore(store)
    if requests.get_pending_by_email(payload["email"]):
        return AccessRequestOutcome(False, "duplicate_pending", DUPLICATE_PENDING)
    try:
        record = requests.create(
            payload["nome"],
            payload["email"],
            payload["gabinete"],
            payload["unidade_outro"],
        )
    except ValueError as exc:
        return AccessRequestOutcome(False, "duplicate_pending", str(exc))
    try:
        notify_access_request(record)
    except MailError:
        return AccessRequestOutcome(
            True,
            "created_notify_failed",
            NOTIFY_FAILED,
            SUCCESS_TITLE,
            record,
        )
    return AccessRequestOutcome(True, "created", SUCCESS_BODY, SUCCESS_TITLE, record)
