"""Public access-request rules. Authorization remains manual."""

from dataclasses import dataclass
from database.access import EMAIL_RE, AccessStore, normalize_email
from database.access_requests import AccessRequestStore

INSTITUTIONAL_DOMAIN = "tce.pb.gov.br"
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
SUCCESS_TITLE = "Solicitação registrada com sucesso"
SUCCESS_BODY = (
    "Sua solicitação de acesso foi encaminhada à administração do MPC-PB.\n\n"
    "Após a análise e liberação do cadastro, você poderá acessar o portal "
    "utilizando sua conta institucional @tce.pb.gov.br."
)


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
    return AccessRequestOutcome(True, "created", SUCCESS_BODY, SUCCESS_TITLE, record)


def count_new_access_requests(store):
    return AccessRequestStore(store).count_new()


def list_access_requests(store, status=None):
    return AccessRequestStore(store).list(status)


def mark_access_request_viewed(store, identifier):
    return AccessRequestStore(store).mark_viewed(identifier)


def approve_access_request(store, identifier, administrator):
    return AccessRequestStore(store).process(identifier, "aprovado", administrator)


def reject_access_request(store, identifier, administrator):
    return AccessRequestStore(store).process(identifier, "recusado", administrator)
