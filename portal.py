"""Lightweight portal navigation. Modules are initialized only after selection."""

from dataclasses import dataclass
from pathlib import Path
import base64

import streamlit as st

from services.branding import (
    SIDEBAR_LOGO,
    render_institutional_header,
    render_sidebar_brand,
)
from services.ui_store import asset

ROOT = Path(__file__).resolve().parent


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
        "Agenda dos Procuradores",
        "Organização de eventos, reuniões e despachos dos procuradores.",
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


def open_portarias():
    st.session_state["portal_module"] = "Portarias"
    st.session_state["nav"] = "Nova Portaria"


def open_agenda():
    st.session_state["portal_module"] = "Agenda"


def open_oficios():
    st.session_state["portal_module"] = "Ofícios"


def open_memorandos():
    st.session_state["portal_module"] = "Memorandos"


def open_admin():
    st.session_state["portal_module"] = "Administração"


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


def home(principal):
    st.write("Selecione uma ferramenta para iniciar.")
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
        "max-width:48rem;width:100%;margin-left:0;margin-right:auto;padding-top:2.25rem;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHeading'] h1{"
        "font-size:3.375rem;line-height:1.15;margin:0 0 .45rem 0;font-weight:700;"
        "}"
        "section[data-testid='stMain'] [data-testid='stCaptionContainer'] p,"
        "section[data-testid='stMain'] [data-testid='stCaption'] p{"
        "font-size:1.2rem;line-height:1.45;margin:0 0 .85rem 0;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHeading'] h2,"
        "section[data-testid='stMain'] [data-testid='stHeading'] h3{"
        "font-size:2.25rem;line-height:1.25;margin:.35rem 0 1.05rem 0;font-weight:650;"
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
    st.title("Acesso restrito")
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
    if "_access_cache" in st.session_state:
        del st.session_state["_access_cache"]
    if "portal_module" in st.session_state:
        del st.session_state["portal_module"]
    st.logout()


def render_denied(identity):
    st.error("Acesso não autorizado.")
    st.write(
        "A conta "
        + identity["email"]
        + " foi autenticada pelo Google, mas não possui autorização "
        "para acessar o Ferramentas MPC-PB."
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
        page_title="Ferramentas MPC-PB",
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
        st.error("Não foi possível verificar a autorização. Tente novamente.")
        if st.button("Sair"):
            _logout()
        st.stop()
    if principal is None:
        with st.sidebar:
            render_sidebar_brand()
        render_institutional_header()
        render_denied(identity)
        st.stop()
    options = ["Início"]
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
    if _session_get("portal_module") not in options:
        st.session_state["portal_module"] = "Início"
    with st.sidebar:
        render_sidebar_brand()
        st.caption(principal.nome + " · " + principal.email)
        if st.button("Sair", key="portal_logout"):
            _logout()
        selected = st.radio("Portal", options, key="portal_module")
        with st.expander("Outras ferramentas"):
            for module in MODULES[1:]:
                if not module.active:
                    st.caption(f"{module.label} · Em breve")
    render_institutional_header()
    if selected == "Início":
        st.subheader("Início")
        home(principal)
        st.stop()
    try:
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
        st.error(str(exc))
        st.stop()
