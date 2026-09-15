"""Lightweight portal navigation. Modules are initialized only after selection."""

from dataclasses import dataclass
from pathlib import Path
import base64

import streamlit as st

from services.branding import (
    APP_NAME,
    SIDEBAR_LOGO,
    render_app_identity,
    render_institutional_header,
    render_sidebar_brand,
)
from services.ui_store import asset

ROOT = Path(__file__).resolve().parent

PORTAL_NAV_REQUEST = "portal_navigation_request"
PORTAL_ALERTS_REQUEST = "portal_alerts_request"
PORTAL_SPECIAL_VIEW = "portal_special_view"
PORTAL_SPECIAL_RETURN = "portal_special_return"
PORTAL_SPECIAL_ANCHOR = "portal_special_anchor"
ALERTS_VIEW = "alerts"
PORTAL_NAV_STATE_KEYS = frozenset(
    {
        "nav",
        "memorandos_nav",
        "pending_open_oficio",
        "pending_open_agenda",
        "pending_open_memorando",
        "pending_open_admin",
    }
)


def _session_get(key, default=None):
    state = st.session_state
    return state[key] if key in state else default


@dataclass(frozen=True)
class Module:
    key: str
    label: str
    title: str
    description: str
    icon: str
    active: bool = False


MODULES = (
    Module(
        "portarias",
        "Portarias",
        "Gerador de Portarias PROGE",
        "Elaboração, numeração, geração e gerenciamento de Portarias de substituição.",
        "description",
        True,
    ),
    Module(
        "memorandos",
        "Memorandos",
        "Memorandos de Substituição",
        "Geração e gerenciamento de memorandos relacionados às substituições de servidores.",
        "article",
        True,
    ),
    Module(
        "oficios",
        "Ofícios",
        "Ofícios — Geração e Controle",
        "Geração, registro e acompanhamento de ofícios enviados e recebidos.",
        "mail",
        True,
    ),
    Module(
        "agenda",
        "Agenda",
        "Agenda e Afastamentos dos Procuradores",
        "Organização de eventos, reuniões, despachos, afastamentos e substituições dos procuradores.",
        "calendar_month",
        True,
    ),
    Module(
        "relatorios",
        "Relatórios",
        "Relatórios e Indicadores",
        "Consultas, estatísticas e relatórios administrativos do MPC-PB.",
        "bar_chart",
    ),
)

ADMIN_MODULE = Module(
    "admin",
    "Administração",
    "Usuários e Acessos",
    "Cadastro de contas autorizadas, módulos e gabinetes de Ofícios.",
    "manage_accounts",
    True,
)


def queue_portal_navigation(module, **state):
    """Enqueue a module change. Safe inside on_click/on_change.

    Only mutate transient session state here. Streamlit already reruns after a
    callback; invoking the rerun API inside one is a no-op and surfaces a warning.
    """
    payload = {"module": module, "state": dict(state)}
    st.session_state[PORTAL_NAV_REQUEST] = payload
    return payload


def request_portal_navigation(module, **state):
    """Enqueue a module change and rerun. Use only in script body.

    Example: ``if st.button(...): request_portal_navigation(...)``.
    Never pass this function to on_click/on_change.
    """
    queue_portal_navigation(module, **state)
    st.rerun()


def apply_portal_navigation(allowed):
    request = st.session_state.pop(PORTAL_NAV_REQUEST, None)
    if not request:
        return None
    if isinstance(request, str):
        module, extra = request, {}
    else:
        module = request.get("module")
        extra = request.get("state") or {}
    if module not in allowed:
        return None
    st.session_state["portal_module"] = module
    for key, value in extra.items():
        if key in PORTAL_NAV_STATE_KEYS:
            st.session_state[key] = value
    return module


def clear_alerts_overlay():
    st.session_state.pop(PORTAL_ALERTS_REQUEST, None)
    st.session_state.pop(PORTAL_SPECIAL_VIEW, None)
    st.session_state.pop(PORTAL_SPECIAL_ANCHOR, None)


def queue_alerts_view():
    """Enqueue the alerts overlay. Safe inside on_click/on_change (no rerun call)."""
    current = _session_get("portal_module")
    if current and current != "Alertas" and PORTAL_SPECIAL_VIEW not in st.session_state:
        st.session_state[PORTAL_SPECIAL_RETURN] = current
    st.session_state[PORTAL_ALERTS_REQUEST] = True


def request_alerts_view():
    """Enqueue the alerts overlay and rerun. Use only outside callbacks."""
    queue_alerts_view()
    st.rerun()


def apply_alerts_view_request():
    if not st.session_state.pop(PORTAL_ALERTS_REQUEST, None):
        return None
    current = _session_get("portal_module")
    if current == "Alertas":
        current = "Início"
    st.session_state[PORTAL_SPECIAL_VIEW] = ALERTS_VIEW
    st.session_state[PORTAL_SPECIAL_ANCHOR] = current or "Início"
    if current and PORTAL_SPECIAL_RETURN not in st.session_state:
        st.session_state[PORTAL_SPECIAL_RETURN] = current
    return ALERTS_VIEW


def close_alerts_view():
    target = st.session_state.pop(PORTAL_SPECIAL_RETURN, None) or "Início"
    clear_alerts_overlay()
    current = _session_get("portal_module")
    if target != current:
        queue_portal_navigation(target)
    st.rerun()


def alerts_overlay_active(selected):
    if _session_get(PORTAL_SPECIAL_VIEW) != ALERTS_VIEW:
        return False
    anchor = _session_get(PORTAL_SPECIAL_ANCHOR) or "Início"
    if selected != anchor:
        clear_alerts_overlay()
        st.session_state.pop(PORTAL_SPECIAL_RETURN, None)
        return False
    return True


def open_portarias():
    queue_portal_navigation("Portarias", nav="Nova Portaria")


def open_agenda():
    queue_portal_navigation("Agenda")


def open_oficios():
    queue_portal_navigation("Ofícios")


def open_memorandos():
    queue_portal_navigation("Memorandos", memorandos_nav="Visão Geral")


def open_admin():
    queue_portal_navigation("Administração")


def card(module):
    with st.container(border=True):
        st.caption(module.label.upper())
        st.markdown(f"### :material/{module.icon}: {module.title}")
        st.write(module.description)
        if module.active:
            st.markdown(":green[**● ATIVO**]")
            st.button(
                f"Acessar {module.label}",
                key=f"open_{module.key}",
                type="primary",
                icon=":material/arrow_forward:",
                on_click={
                    "portarias": open_portarias,
                    "agenda": open_agenda,
                    "oficios": open_oficios,
                    "memorandos": open_memorandos,
                    "admin": open_admin,
                }[module.key],
            )
        else:
            st.caption("EM BREVE")
            st.markdown(
                '<div class="mpc-home-soon-slot" aria-hidden="true"></div>',
                unsafe_allow_html=True,
            )


def visible_modules(principal):
    from services.access import has_permission

    visible = []
    for module in MODULES:
        if module.active and not has_permission(principal, module.key):
            continue
        visible.append(module)
    if has_permission(principal, "admin"):
        visible.append(ADMIN_MODULE)
    visible.sort(key=lambda module: not module.active)
    return visible


def _home_layout_style():
    st.markdown(
        "<style>"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock']{"
        "align-items:stretch;gap:1rem;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock']>div{"
        "display:flex;min-width:0;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock'] "
        "[data-testid='stVerticalBlockBorderWrapper']{"
        "flex:1 1 auto;width:100%;min-height:19.5rem;height:100%;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock'] "
        "[data-testid='stVerticalBlockBorderWrapper']>div{"
        "height:100%;display:flex;flex-direction:column;"
        "}"
        "section[data-testid='stMain'] .mpc-home-soon-slot{"
        "min-height:2.85rem;flex:1 1 auto;"
        "}"
        "@media (max-width:768px){"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock']{"
        "flex-direction:column;flex-wrap:nowrap;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock']>div{"
        "width:100%;flex:1 1 auto;"
        "}"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )


def open_pendencias():
    request_portal_navigation("Pendências")


def open_alertas():
    request_alerts_view()


def home(principal, store=None):
    render_app_identity(
        variant="home",
        prompt="Selecione uma ferramenta para iniciar.",
    )
    visible = visible_modules(principal)
    if not visible:
        st.info("Nenhum módulo disponível para este usuário.")
        return
    _home_layout_style()
    for start in range(0, len(visible), 2):
        columns = st.columns(2)
        for column, module in zip(columns, visible[start : start + 2]):
            with column:
                card(module)


def render_login():
    icon = base64.standard_b64encode(asset(ROOT / "assets/gmail.png")).decode("ascii")
    st.markdown(
        "<style>"
        "section[data-testid='stMain'] [data-testid='stMainBlockContainer']{"
        "max-width:50rem;width:100%;margin-left:0;margin-right:auto;padding-top:2.25rem;"
        "}"
        "section[data-testid='stMain'] div.st-key-oidc_gmail_login{"
        "width:fit-content;max-width:100%;margin:.15rem 0 .95rem 0;"
        "}"
        "div.st-key-oidc_gmail_login button{"
        "display:inline-flex;align-items:center;justify-content:center;"
        "gap:.975rem;min-height:4.125rem;width:auto;max-width:100%;"
        "padding:.825rem 1.875rem .825rem 1.575rem;"
        "font-size:1.5rem;font-weight:650;cursor:pointer;"
        "}"
        "div.st-key-oidc_gmail_login button::before{"
        "content:'';width:1.875rem;height:1.875rem;flex:0 0 1.875rem;"
        "background-image:url('data:image/png;base64," + icon + "');"
        "background-size:contain;background-repeat:no-repeat;background-position:center;"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )
    render_app_identity(variant="login", action="Acesso restrito")
    if st.button("Entrar com Gmail", type="primary", key="oidc_gmail_login"):
        try:
            st.login()
        except Exception:
            st.error(
                "Não foi possível iniciar o login com Google. Confira a configuração OIDC nos Secrets."
            )
    st.caption("Utilize uma conta previamente autorizada do domínio @tce.pb.gov.br.")


def _logout():
    """End the native OIDC session and drop this user's authorization cache."""
    try:
        from services.audit import registrar_logout

        registrar_logout(
            st.session_state["_mpc_store"] if "_mpc_store" in st.session_state else None,
            st.session_state.get("_audit_actor"),
            identity=st.session_state.get("_audit_identity"),
        )
    except Exception:
        pass
    if "_access_cache" in st.session_state:
        del st.session_state["_access_cache"]
    if "portal_module" in st.session_state:
        del st.session_state["portal_module"]
    for key in (
        "_audit_actor",
        "_audit_identity",
        "audit_sessao_registrada",
        "audit_modulo_atual",
        "audit_negado_registrado",
        PORTAL_ALERTS_REQUEST,
        PORTAL_SPECIAL_VIEW,
        PORTAL_SPECIAL_RETURN,
        PORTAL_SPECIAL_ANCHOR,
        "_alerts_bell_cache",
    ):
        st.session_state.pop(key, None)
    st.logout()


def render_denied(identity):
    render_app_identity()
    st.error("Acesso não autorizado.")
    st.write(
        "A conta "
        + identity["email"]
        + " foi autenticada pelo Google, mas não possui autorização "
        "para acessar o " + APP_NAME + "."
    )
    st.write("Entre em contato com o administrador do sistema.")
    if st.button("Sair"):
        _logout()


def _application_store():
    from database.store import Store, unwrap_store

    store = _session_get("_mpc_store")
    if store is not None:
        store = unwrap_store(store)
        st.session_state["_mpc_store"] = store
        return store
    store = Store()
    st.session_state["_mpc_store"] = store
    return store


def render_portal():
    from services.access import (
        current_user,
        has_permission,
        oidc_identity,
        require_permission,
    )

    st.set_page_config(
        page_title=APP_NAME,
        page_icon=str(SIDEBAR_LOGO),
        layout="wide",
    )
    identity = oidc_identity()
    if identity is None:
        with st.sidebar:
            render_sidebar_brand()
        render_institutional_header()
        render_login()
        st.stop()
    try:
        store = _application_store()
        principal = current_user(store)
    except Exception:
        with st.sidebar:
            render_sidebar_brand()
        render_institutional_header()
        render_app_identity()
        st.error("Não foi possível verificar a autorização. Tente novamente.")
        if st.button("Sair"):
            _logout()
        st.stop()
    st.session_state["_audit_identity"] = identity
    if principal is None:
        from database.access import AccessStore
        from services.audit import registrar_acesso_negado

        record = AccessStore(store).get_by_email(identity["email"])
        registrar_acesso_negado(
            store, identity, inativo=bool(record and not record["ativo"])
        )
        with st.sidebar:
            render_sidebar_brand()
        render_institutional_header()
        render_denied(identity)
        st.stop()
    st.session_state["_audit_actor"] = principal
    from services.audit import iniciar_sessao_autorizada, registrar_modulo

    iniciar_sessao_autorizada(store, principal)
    options = ["Início"]
    if has_permission(principal, "pendencias"):
        options.append("Pendências")
    if has_permission(principal, "portarias"):
        options.append("Portarias")
    if has_permission(principal, "agenda"):
        options.append("Agenda")
    if has_permission(principal, "oficios"):
        options.append("Ofícios")
    if has_permission(principal, "memorandos"):
        options.append("Memorandos")
    if has_permission(principal, "admin"):
        options.append("Administração")
    apply_portal_navigation(options)
    if _session_get("portal_module") not in options:
        st.session_state["portal_module"] = "Início"
    apply_alerts_view_request()
    with st.sidebar:
        render_sidebar_brand()
        st.caption(principal.nome + " · " + principal.email)
        from services.alerts import can_view_alertas
        from services.alerts_ui import render_bell

        _, logout_column = st.columns([3, 2])
        if logout_column.button("Sair", key="portal_logout"):
            _logout()
        if can_view_alertas(principal):
            render_bell(store, principal)
        selected = st.radio("Portal", options, key="portal_module")
        if selected != "Memorandos":
            st.session_state["memorando_form_active"] = False
        with st.expander("Outras ferramentas"):
            for module in MODULES[1:]:
                if not module.active:
                    st.caption(f"{module.label} · Em breve")
    render_institutional_header(home=selected == "Início")
    if alerts_overlay_active(selected):
        from services.alerts_ui import render as render_alerts

        try:
            require_permission(principal, "alertas")
        except ValueError as exc:
            clear_alerts_overlay()
            st.session_state.pop(PORTAL_SPECIAL_RETURN, None)
            st.error(str(exc))
            st.stop()
        registrar_modulo(store, principal, "Alertas")
        render_alerts(store, principal)
        st.stop()
    if selected == "Início":
        st.session_state["audit_modulo_atual"] = None
        home(principal, store)
        st.stop()
    registrar_modulo(store, principal, selected)
    try:
        if selected == "Pendências":
            require_permission(principal, "pendencias")
            from services.pending_ui import render

            render(store, principal)
            st.stop()
        if selected == "Agenda":
            require_permission(principal, "agenda")
            from services.agenda_ui import render

            render(store, principal)
            st.stop()
        if selected == "Ofícios":
            require_permission(principal, "oficios")
            from services.oficios_ui import render

            render(store, principal)
            st.stop()
        if selected == "Memorandos":
            require_permission(principal, "memorandos")
            from services.memorandos_ui import render

            render(store, principal)
            st.stop()
        if selected == "Administração":
            require_permission(principal, "admin")
            from services.access_ui import render

            render(store, principal)
            st.stop()
        require_permission(principal, "portarias")
    except ValueError as exc:
        from services.audit import MODULE_KEYS, registrar_evento

        registrar_evento(
            store,
            evento="PERMISSAO_NEGADA",
            modulo=MODULE_KEYS.get(selected, ""),
            acao="ACESSAR",
            resultado="NEGADO",
            principal=principal,
            detalhes={"modulo": selected, "motivo": str(exc)[:200]},
        )
        st.error(str(exc))
        st.stop()
