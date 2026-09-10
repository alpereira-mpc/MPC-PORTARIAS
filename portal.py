"""Lightweight portal navigation. Modules are initialized only after selection."""

from dataclasses import dataclass
from pathlib import Path

import streamlit as st

from services.ui_store import asset

ROOT = Path(__file__).resolve().parent


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
        "Controle de Ofícios",
        "Registro, organização e acompanhamento de ofícios recebidos e expedidos.",
        "mail",
    ),
    Module(
        "agenda",
        "Agenda",
        "Agenda dos Procuradores (em teste)",
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
                on_click=open_portarias if module.key == "portarias" else open_agenda,
            )
        else:
            st.caption("EM BREVE")


def home():
    st.write("Selecione uma ferramenta para iniciar.")
    card(MODULES[0])
    for start in (1, 3):
        for column, module in zip(st.columns(2), MODULES[start : start + 2]):
            with column:
                card(module)


def render_portal():
    st.set_page_config(
        page_title="Ferramentas MPC-PB",
        page_icon=str(ROOT / "assets/logo.jpeg"),
        layout="wide",
    )
    with st.sidebar:
        st.image(asset(ROOT / "assets/logo.jpeg"), width=110)
        st.markdown("**Ferramentas MPC-PB**")
        selected = st.radio(
            "Portal", ["Início", "Portarias", "Agenda"], key="portal_module"
        )
        with st.expander("Outras ferramentas"):
            for module in MODULES[1:]:
                if not module.active:
                    st.caption(f"{module.label} · Em breve")
    st.title("FERRAMENTAS MPC-PB")
    st.caption("Ministério Público de Contas do Estado da Paraíba")
    if selected == "Início":
        home()
        # Stop before importing or constructing the Portarias database/document stack.
        st.stop()

    if selected == "Agenda":
        from services.agenda_ui import render

        render()
        st.stop()
