"""Authorization independent of the Streamlit OIDC identity source."""

from dataclasses import dataclass
import time
from database.access import AccessStore, normalize_email
from services.oficios import GABINETES

MODULES = ("portarias", "agenda", "oficios", "memorandos", "admin")
CACHE_SECONDS = 20


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


def principal_from_record(record):
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
            pode_admin=True,
            gabinetes=tuple(GABINETES),
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
        pode_admin=bool(record["pode_admin"]),
        gabinetes=gabinetes,
    )


def resolve_principal(store, identity):
    if not identity or not identity.get("email"):
        return None
    record = AccessStore(store).get_by_email(identity["email"])
    if not record or not record["ativo"]:
        return None
    return principal_from_record(record)


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
        if (
            cache
            and cache.get("email") == identity["email"]
            and time.monotonic() - cache.get("at", 0) < CACHE_SECONDS
        ):
            return cache.get("principal")
    except Exception:
        cache = None
    principal = resolve_principal(store, identity)
    try:
        import streamlit as st

        st.session_state["_access_cache"] = {
            "email": identity["email"],
            "at": time.monotonic(),
            "principal": principal,
        }
    except Exception:
        pass
    return principal


def has_permission(principal, module):
    if principal is None or not principal.ativo:
        return False
    if module == "portarias":
        return principal.pode_portarias
    if module == "agenda":
        return principal.pode_agenda
    if module == "oficios":
        return principal.pode_oficios
    if module == "memorandos":
        return principal.pode_memorandos
    if module == "admin":
        return principal.pode_admin
    return False


def allowed_gabinetes(principal):
    if principal is None or not principal.pode_oficios:
        return ()
    return principal.gabinetes


def can_use_gabinete(principal, code):
    return code in allowed_gabinetes(principal)


def require_permission(principal, module):
    if not has_permission(principal, module):
        raise ValueError("Acesso não autorizado a este módulo.")


def require_gabinete(principal, code):
    if not can_use_gabinete(principal, code):
        raise ValueError("Acesso não autorizado a este gabinete.")
