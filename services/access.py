"""Authorization independent of the Streamlit OIDC identity source."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import logging
from database.access import AccessStore, normalize_email
from database.store import unwrap_store
from services.oficios import GABINETES

MODULES = ("portarias", "agenda", "oficios", "memorandos", "tarefas", "relatorios", "representacoes", "peticoes", "ouvidoria", "admin")
# Capacidades delegáveis para ações que podem produzir efeito institucional externo.
CAPABILITIES = (
    "representacoes_registrar_protocolo",
    "representacoes_enviar_comunicacao",
    "peticoes_cadastrar", "peticoes_editar", "peticoes_registrar_andamento", "peticoes_registrar_resultado", "peticoes_concluir",
    "comunicacoes_configurar_destinatarios",
    "comunicacoes_enviar_teste",
)
_AUTHORIZATION_SCOPE = ContextVar("authorization_scope", default=None)
LOGGER = logging.getLogger("mpc.access")


@dataclass(frozen=True)
class Principal:
    id: int
    nome: str
    email: str
    perfil: str
    ativo: bool
    pode_portarias: bool
    pode_agenda: bool
    pode_oficios: bool
    pode_admin: bool
    gabinetes: tuple
    pode_memorandos: bool = False
    pode_relatorios: bool = False
    pode_representacoes: bool = False
    pode_peticoes: bool = False
    pode_ouvidoria: bool = False
    pode_representacoes_registrar_protocolo: bool = False
    pode_representacoes_enviar_comunicacao: bool = False
    pode_peticoes_cadastrar: bool = False
    pode_peticoes_editar: bool = False
    pode_peticoes_registrar_andamento: bool = False
    pode_peticoes_registrar_resultado: bool = False
    pode_peticoes_concluir: bool = False
    pode_comunicacoes_configurar_destinatarios: bool = False
    pode_comunicacoes_enviar_teste: bool = False
    _authorization_version: tuple | None = field(default=None, repr=False, compare=False)
    _store: object = field(default=None, repr=False, compare=False)

    @property
    def administrator(self):
        return self.perfil == "ADMINISTRADOR"


def oidc_identity():
    """Read the native Streamlit OIDC identity. Isolated for tests/mocks."""
    try:
        import streamlit as st

        user = st.user
    except Exception:
        return None
    logged = getattr(user, "is_logged_in", None)
    if logged is None:
        try:
            logged = bool(user.get("email") or user.get("is_logged_in"))
        except Exception:
            logged = False
    if not logged:
        return None
    email = getattr(user, "email", None)
    if email is None:
        try:
            email = user.get("email")
        except Exception:
            email = None
    email = normalize_email(email)
    if not email:
        return None
    verified = getattr(user, "email_verified", None)
    if verified is None:
        try:
            verified = user.get("email_verified")
        except Exception:
            verified = None
    if verified is False:
        return None
    name = getattr(user, "name", None) or getattr(user, "given_name", None)
    if not name:
        try:
            name = user.get("name") or user.get("given_name")
        except Exception:
            name = None
    return {"email": email, "name": name or email, "email_verified": verified}


def principal_from_record(record, store=None):
    version = (
        int(record["id"]),
        bool(record["ativo"]),
        record.get("atualizado_em"),
    )
    source = unwrap_store(store) if store is not None else None
    if record["perfil"] == "ADMINISTRADOR":
        return Principal(
            id=record["id"],
            nome=record["nome"],
            email=record["email"],
            perfil="ADMINISTRADOR",
            ativo=record["ativo"],
            pode_portarias=True,
            pode_agenda=True,
            pode_oficios=True,
            pode_memorandos=True,
            pode_relatorios=True,
            pode_representacoes=True,
            pode_peticoes=True,
            pode_ouvidoria=True,
            pode_representacoes_registrar_protocolo=True,
            pode_representacoes_enviar_comunicacao=True,
            pode_peticoes_cadastrar=True, pode_peticoes_editar=True, pode_peticoes_registrar_andamento=True, pode_peticoes_registrar_resultado=True, pode_peticoes_concluir=True,
            pode_comunicacoes_configurar_destinatarios=True,
            pode_comunicacoes_enviar_teste=True,
            pode_admin=True,
            gabinetes=tuple(GABINETES),
            _authorization_version=version,
            _store=source,
        )
    gabinetes = tuple(
        code for code in GABINETES if code in set(record.get("gabinetes") or [])
    )
    if not record["pode_oficios"]:
        gabinetes = ()
    return Principal(
        id=record["id"],
        nome=record["nome"],
        email=record["email"],
        perfil="USUARIO",
        ativo=record["ativo"],
        pode_portarias=bool(record["pode_portarias"]),
        pode_agenda=bool(record["pode_agenda"]),
        pode_oficios=bool(record["pode_oficios"]),
        pode_memorandos=bool(record.get("pode_memorandos", False)),
        pode_relatorios=bool(record.get("pode_relatorios", False)),
        pode_representacoes=bool(record.get("pode_representacoes", False)),
        pode_peticoes=bool(record.get("pode_peticoes", False)),
        pode_ouvidoria=bool(record.get("pode_ouvidoria", False)),
        pode_representacoes_registrar_protocolo=bool(record.get("pode_representacoes_registrar_protocolo", False)),
        pode_representacoes_enviar_comunicacao=bool(record.get("pode_representacoes_enviar_comunicacao", False)),
        pode_peticoes_cadastrar=bool(record.get("pode_peticoes_cadastrar", False)), pode_peticoes_editar=bool(record.get("pode_peticoes_editar", False)), pode_peticoes_registrar_andamento=bool(record.get("pode_peticoes_registrar_andamento", False)), pode_peticoes_registrar_resultado=bool(record.get("pode_peticoes_registrar_resultado", False)), pode_peticoes_concluir=bool(record.get("pode_peticoes_concluir", False)),
        pode_comunicacoes_configurar_destinatarios=bool(record.get("pode_comunicacoes_configurar_destinatarios", False)),
        pode_comunicacoes_enviar_teste=bool(record.get("pode_comunicacoes_enviar_teste", False)),
        pode_admin=bool(record["pode_admin"]),
        gabinetes=gabinetes,
        _authorization_version=version,
        _store=source,
    )


def resolve_principal(store, identity):
    if not identity or not identity.get("email"):
        return None
    access = AccessStore(store)
    record = access.get_by_email(identity["email"])
    if not record or not record["ativo"]:
        return None
    return principal_from_record(record, access.store)


def confirm_principal(principal, store=None):
    """Refresh a principal only when its persisted authorization version changed."""
    if principal is None:
        return None
    source = unwrap_store(store) if store is not None else principal._store
    if source is None:
        return principal
    access = AccessStore(source)
    version = access.authorization_version(principal.email)
    if version is None or not version[1]:
        return None
    if version == principal._authorization_version:
        return principal
    record = access.get_by_email(principal.email)
    if not record or not record["ativo"]:
        return None
    return principal_from_record(record, access.store)


@contextmanager
def authorization_scope(store, principal):
    """Confirm once and reuse the result during one module or fragment render."""
    scoped = _AUTHORIZATION_SCOPE.get()
    source = unwrap_store(store)
    if (
        scoped is not None
        and principal is not None
        and scoped.id == principal.id
        and scoped.email == principal.email
        and scoped._store is source
    ):
        current = scoped
    else:
        try:
            current = confirm_principal(principal, store)
        except Exception as exc:
            LOGGER.warning(
                "Falha ao confirmar autorização do fragmento (%s).",
                type(exc).__name__,
            )
            current = None
    token = _AUTHORIZATION_SCOPE.set(current)
    try:
        yield current
    finally:
        _AUTHORIZATION_SCOPE.reset(token)


def _confirmed_for_operation(principal):
    scoped = _AUTHORIZATION_SCOPE.get()
    if scoped is not None and principal is not None:
        if scoped.id == principal.id and scoped.email == principal.email:
            return scoped
    return confirm_principal(principal)


def current_user(store):
    identity = oidc_identity()
    if identity is None:
        return None
    cache = None
    try:
        import streamlit as st

        cache = (
            st.session_state["_access_cache"]
            if "_access_cache" in st.session_state
            else None
        )
    except Exception:
        cache = None
    if (
        cache
        and cache.get("store_id") == id(store)
        and cache.get("email") == identity["email"]
        and cache.get("principal") is not None
    ):
        principal = confirm_principal(cache.get("principal"), store)
    else:
        principal = resolve_principal(store, identity)
    try:
        import streamlit as st

        st.session_state["_access_cache"] = {
            "store_id": id(store),
            "email": identity["email"],
            "principal": principal,
        }
    except Exception:
        pass
    return principal


def has_permission(principal, module):
    if principal is None or not principal.ativo:
        return False
    if module == "pendencias":
        return any(
            has_permission(principal, name)
            for name in (
                "oficios",
                "agenda",
                "memorandos",
                "representacoes",
                "ouvidoria",
                "admin",
            )
        )
    if module == "alertas":
        # Tarefas is always allowed for active accounts and must not open Alertas
        # by itself. Same rule as docs/ALERTAS_INTERNOS.md: Ofícios, Agenda or Memorandos.
        return any(
            has_permission(principal, name)
            for name in ("oficios", "agenda", "memorandos")
        )
    if module == "portarias":
        return principal.pode_portarias
    if module == "agenda":
        return principal.pode_agenda
    if module == "oficios":
        return principal.pode_oficios
    if module == "memorandos":
        return principal.pode_memorandos
    if module == "tarefas":
        # Personal tasks are available to every authenticated active account.
        return True
    if module == "relatorios":
        return principal.administrator or principal.pode_relatorios
    if module == "representacoes":
        return principal.pode_representacoes
    if module == "peticoes":
        return principal.pode_peticoes
    if module == "ouvidoria":
        return principal.pode_ouvidoria
    if module == "admin":
        return principal.pode_admin
    if module in CAPABILITIES:
        return principal.administrator or bool(getattr(principal, "pode_" + module, False))
    return False


def can_access_representacoes(principal):
    return has_permission(principal, "representacoes")


def can_access_ouvidoria(principal):
    return has_permission(principal, "ouvidoria")


def allowed_gabinetes(principal):
    if principal is None or not principal.pode_oficios:
        return ()
    return principal.gabinetes


def can_use_gabinete(principal, code):
    return code in allowed_gabinetes(principal)


def require_permission(principal, module):
    try:
        current = _confirmed_for_operation(principal)
    except Exception:
        raise ValueError("Não foi possível confirmar a autorização desta operação.") from None
    if not has_permission(current, module):
        raise ValueError("Acesso não autorizado a este módulo.")
    return current


def require_gabinete(principal, code):
    try:
        current = _confirmed_for_operation(principal)
    except Exception:
        raise ValueError("Não foi possível confirmar a autorização desta operação.") from None
    if not can_use_gabinete(current, code):
        raise ValueError("Acesso não autorizado a este gabinete.")
    return current
