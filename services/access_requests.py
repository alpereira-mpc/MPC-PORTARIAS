"""Public access-request rules. Authorization remains manual."""

from dataclasses import dataclass

from database.access import EMAIL_RE, AccessStore, normalize_email
from database.access_requests import (
    STATUS_APPROVED,
    STATUS_PENDING,
    STATUS_REJECTED,
    STATUSES,
    AccessRequestStore,
)

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
    "Sua solicitação de acesso foi encaminhada à administração do MPC-PB. "
    "Após a análise e liberação do cadastro, você poderá acessar o portal "
    "utilizando sua conta institucional @tce.pb.gov.br."
)
SUCCESS_BODY_ESTAGIARIO = (
    "Sua solicitação foi encaminhada à administração do MPC-PB. "
    "O acesso ao Minutas IA só será liberado depois da autorização administrativa "
    "e da confirmação do vínculo de estágio. O envio não cria usuário nem libera o login."
)
MISSING_ADMIN = "Administrador não identificado."
FILTER_PENDING = "Pendentes"
FILTER_APPROVED = "Aprovadas"
FILTER_REJECTED = "Recusadas"
FILTER_ALL = "Todas"
ADMIN_FILTERS = (FILTER_PENDING, FILTER_APPROVED, FILTER_REJECTED, FILTER_ALL)
_FILTER_STATUS = {
    FILTER_PENDING: STATUS_PENDING,
    FILTER_APPROVED: STATUS_APPROVED,
    FILTER_REJECTED: STATUS_REJECTED,
    FILTER_ALL: None,
}


@dataclass(frozen=True)
class AccessRequestOutcome:
    ok: bool
    code: str
    message: str
    title: str = ""
    record: dict | None = None


def normalize_request_email(value, tipo="INSTITUCIONAL"):
    from services.estagiarios_acesso import TIPO_ESTAGIARIO

    email = normalize_email(value)
    local, separator, domain = email.partition("@")
    if tipo == TIPO_ESTAGIARIO:
        if not email or not EMAIL_RE.match(email) or not separator or not local or not domain:
            raise ValueError("Informe o e-mail da Conta Google.")
        return email
    if not email or not EMAIL_RE.match(email):
        raise ValueError("Informe um e-mail institucional válido.")
    if not separator or not local or domain != INSTITUTIONAL_DOMAIN:
        raise ValueError("Informe um e-mail do domínio @tce.pb.gov.br.")
    return email


def validate_access_request(nome, email, gabinete, unidade_outro=None, tipo="INSTITUCIONAL"):
    from services.estagiarios_acesso import TIPO_ESTAGIARIO, sigla_da_opcao

    name = " ".join((nome or "").split())
    if not name or len(name) < MIN_NAME_LENGTH:
        raise ValueError("Informe o nome completo.")
    if tipo not in ("INSTITUCIONAL", TIPO_ESTAGIARIO):
        raise ValueError("Tipo de solicitante inválido.")
    address = normalize_request_email(email, tipo)
    office = (gabinete or "").strip()
    extra = " ".join((unidade_outro or "").split())
    if tipo == TIPO_ESTAGIARIO:
        sigla = sigla_da_opcao(office)
        if not sigla:
            raise ValueError("Selecione o gabinete de estágio.")
        office = sigla
        extra = None
    else:
        if office not in GABINETE_OPTIONS:
            raise ValueError("Selecione o gabinete ou unidade.")
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
        "tipo_solicitante": tipo,
    }


def submit_access_request(store, nome, email, gabinete, unidade_outro=None, tipo="INSTITUCIONAL"):
    try:
        payload = validate_access_request(nome, email, gabinete, unidade_outro, tipo)
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
            payload["tipo_solicitante"],
        )
    except ValueError as exc:
        return AccessRequestOutcome(False, "duplicate_pending", str(exc))
    from services.estagiarios_acesso import TIPO_ESTAGIARIO

    mensagem = SUCCESS_BODY_ESTAGIARIO if payload["tipo_solicitante"] == TIPO_ESTAGIARIO else SUCCESS_BODY
    return AccessRequestOutcome(True, "created", mensagem, SUCCESS_TITLE, record)


def _actor_email(processed_by):
    email = normalize_email(processed_by)
    if not email:
        raise ValueError(MISSING_ADMIN)
    return email


def list_access_requests(store, status=None):
    if status is not None and status not in STATUSES:
        raise ValueError("Status de solicitação inválido.")
    return AccessRequestStore(store).list_requests(status)


def list_access_requests_by_filter(store, filtro=FILTER_PENDING):
    if filtro not in ADMIN_FILTERS:
        raise ValueError("Filtro de solicitações inválido.")
    return list_access_requests(store, _FILTER_STATUS[filtro])


def count_pending_access_requests(store):
    return AccessRequestStore(store).count_pending()


def approve_access_request(store, identifier, processed_by):
    email = _actor_email(processed_by)
    record = AccessRequestStore(store).mark_processed(
        identifier, STATUS_APPROVED, email
    )
    from services.audit import registrar_evento

    registrar_evento(
        store,
        evento="SOLICITACAO_APROVADA",
        modulo="admin",
        acao="ALTERAR",
        identity={"email": email, "name": email},
        entidade_tipo="solicitacao_acesso",
        entidade_id=identifier,
        detalhes={
            "usuario_alvo": record.get("email"),
            "nome_alvo": record.get("nome"),
        },
    )
    return record


def reject_access_request(store, identifier, processed_by):
    email = _actor_email(processed_by)
    record = AccessRequestStore(store).mark_processed(
        identifier, STATUS_REJECTED, email
    )
    from services.audit import registrar_evento

    registrar_evento(
        store,
        evento="SOLICITACAO_RECUSADA",
        modulo="admin",
        acao="ALTERAR",
        identity={"email": email, "name": email},
        entidade_tipo="solicitacao_acesso",
        entidade_id=identifier,
        detalhes={
            "usuario_alvo": record.get("email"),
            "nome_alvo": record.get("nome"),
        },
    )
    return record


def delete_access_request(store, identifier, processed_by=None):
    requests = AccessRequestStore(store)
    current = requests.get(identifier)
    removed = requests.delete(identifier) > 0
    if removed:
        from services.audit import registrar_evento

        identity = None
        if processed_by:
            try:
                email = _actor_email(processed_by)
                identity = {"email": email, "name": email}
            except ValueError:
                identity = None
        registrar_evento(
            store,
            evento="SOLICITACAO_EXCLUIDA",
            modulo="admin",
            acao="EXCLUIR",
            identity=identity,
            entidade_tipo="solicitacao_acesso",
            entidade_id=identifier,
            detalhes={
                "usuario_alvo": (current or {}).get("email"),
                "nome_alvo": (current or {}).get("nome"),
            },
        )
    return removed


def autorizar_estagiario(store, identifier, lotacao_id, processed_by):
    """Aprova a solicitação e cria o usuário restrito ao Minutas IA."""
    from database.access import AccessStore
    from services.audit import registrar_evento
    from services.estagiarios_acesso import TIPO_ESTAGIARIO, carregar_vinculo

    email = _actor_email(processed_by)
    pedido = AccessRequestStore(store).get(identifier)
    if not pedido or pedido.get("status") != STATUS_PENDING:
        raise ValueError("Solicitação pendente não encontrada.")
    if pedido.get("tipo_solicitante") != TIPO_ESTAGIARIO:
        raise ValueError("Esta solicitação não é de estagiário.")
    if AccessStore(store).get_by_email(pedido["email"]):
        raise ValueError(ALREADY_REGISTERED)
    vinculo = carregar_vinculo(store, lotacao_id)
    usuario = AccessStore(store).save_user(
        {
            "nome": pedido["nome"],
            "email": pedido["email"],
            "perfil": "USUARIO",
            "ativo": True,
            "categoria_acesso": TIPO_ESTAGIARIO,
            "estagio_lotacao_id": lotacao_id,
            "minutas_gabinetes": [vinculo["lotacao"]] if vinculo else [],
            "pode_admin": True,
        }
    )
    record = approve_access_request(store, identifier, email)
    registrar_evento(
        store,
        evento="ESTAGIARIO_ACESSO_AUTORIZADO",
        modulo="admin",
        acao="AUTORIZACAO",
        identity={"email": email, "name": email},
        entidade_tipo="usuario",
        entidade_id=usuario,
        detalhes={
            "usuario_alvo": pedido["email"],
            "nome_alvo": pedido["nome"],
            "estagio_lotacao_id": lotacao_id,
            "gabinete": (vinculo or {}).get("lotacao"),
        },
    )
    return {"solicitacao": record, "usuario_id": usuario}
