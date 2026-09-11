"""Lightweight portal navigation. Modules are initialized only after selection."""

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

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


def open_portarias():
    st.session_state["portal_module"] = "Portarias"
    st.session_state["nav"] = "Nova Portaria"


def open_agenda():
    st.session_state["portal_module"] = "Agenda"


def open_oficios():
    st.session_state["portal_module"] = "Ofícios"


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
                    "admin": open_admin,
                }[module.key],
            )
        else:
            st.caption("EM BREVE")


def home(principal):
    from services.access import has_permission

    st.write("Selecione uma ferramenta para iniciar.")
    visible = []
    for module in MODULES:
        if module.active and not has_permission(principal, module.key):
            continue
        visible.append(module)
    if has_permission(principal, "admin"):
        visible.append(
            Module(
                "admin",
                "Administração",
                "Usuários e Acessos",
                "Cadastro de contas autorizadas, módulos e gabinetes de Ofícios.",
                "manage_accounts",
                True,
            )
        )
    if not visible:
        st.info("Nenhum módulo disponível para este usuário.")
        return
    card(visible[0])
    rest = visible[1:]
    for start in range(0, len(rest), 2):
        columns = st.columns(2)
        for column, module in zip(columns, rest[start : start + 2]):
            with column:
                card(module)


def render_login():
    st.title("FERRAMENTAS MPC-PB")
    st.caption("Ministério Público de Contas do Estado da Paraíba")
    st.subheader("Acesso restrito")
    if st.button("Entrar com Google", type="primary"):
        try:
            st.login()
        except Exception:
            st.error(
                "Não foi possível iniciar o login com Google. Confira a configuração OIDC nos Secrets."
            )
    st.caption("Utilize uma conta previamente autorizada.")


def render_denied(identity):
    st.title("FERRAMENTAS MPC-PB")
    st.error("Acesso não autorizado.")
    st.write(
        "A conta "
        + identity["email"]
        + " foi autenticada pelo Google, mas não possui autorização "
        "para acessar o Ferramentas MPC-PB."
    )
    st.write("Entre em contato com o administrador do sistema.")
    if st.button("Sair"):
        st.session_state.pop("_access_cache", None)
        st.logout()


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
        page_icon=str(ROOT / "assets/logo.jpeg"),
        layout="wide",
    )
    identity = oidc_identity()
    if identity is None:
        with st.sidebar:
            st.image(asset(ROOT / "assets/logo.jpeg"), width=110)
            st.markdown("**Ferramentas MPC-PB**")
        render_login()
        st.stop()
    try:
        store = _application_store()
        principal = current_user(store)
    except Exception:
        with st.sidebar:
            st.image(asset(ROOT / "assets/logo.jpeg"), width=110)
            st.markdown("**Ferramentas MPC-PB**")
        st.error("Não foi possível verificar a autorização. Tente novamente.")
        if st.button("Sair"):
            _logout()
        st.stop()
    if principal is None:
        with st.sidebar:
            st.image(asset(ROOT / "assets/logo.jpeg"), width=110)
            st.markdown("**Ferramentas MPC-PB**")
        render_denied(identity)
        st.stop()
    options = ["Início"]
    if has_permission(principal, "portarias"):
        options.append("Portarias")
    if has_permission(principal, "agenda"):
        options.append("Agenda")
    if has_permission(principal, "oficios"):
        options.append("Ofícios")
    if has_permission(principal, "admin"):
        options.append("Administração")
    if _session_get("portal_module") not in options:
        st.session_state["portal_module"] = "Início"
    with st.sidebar:
        st.image(asset(ROOT / "assets/logo.jpeg"), width=110)
        st.markdown("**Ferramentas MPC-PB**")
        st.caption(principal.nome + " · " + principal.email)
        if st.button("Sair", key="portal_logout"):
            _logout()
        selected = st.radio("Portal", options, key="portal_module")
        with st.expander("Outras ferramentas"):
            for module in MODULES[1:]:
                if not module.active:
                    st.caption(f"{module.label} · Em breve")
    st.title("FERRAMENTAS MPC-PB")
    st.caption("Ministério Público de Contas do Estado da Paraíba")
    if selected == "Início":
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
        if selected == "Administração":
            require_permission(principal, "admin")
            from services.access_ui import render

            render(store, principal)
            st.stop()
        require_permission(principal, "portarias")
    except ValueError as exc:
        st.error(str(exc))
        st.stop()
