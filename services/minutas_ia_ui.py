"""Initial private UI for the Minutas IA pilot."""

import streamlit as st

from services.ui_theme import badge, render_html, section_label


SUBCATEGORIES = (
    ("Prefeitura", "Administração municipal"),
    ("Câmara Municipal", "Poder Legislativo municipal"),
    ("Instituto/Autarquia", "Entidades da administração indireta"),
    (
        "Secretaria Municipal ou Estadual",
        "Órgãos das administrações municipal e estadual",
    ),
)


def render():
    st.title("Gerador de Minutas de Pareceres — Projeto-Piloto")
    render_html(badge("AMBIENTE EXPERIMENTAL", "warning"))
    st.caption("Projeto-piloto privado do MPC-PB")

    with st.container(border=True):
        st.markdown("**Procuradora responsável**")
        st.markdown("Elvira Samara")

    section_label("Categoria principal")
    st.subheader("Prestações de Contas Anuais (PCA)")

    for start in range(0, len(SUBCATEGORIES), 2):
        columns = st.columns(2)
        group = SUBCATEGORIES[start : start + 2]
        for column, (title, description) in zip(columns, group):
            with column:
                with st.container(border=True):
                    st.markdown(f"**{title}**")
                    st.caption(description)

    st.info(
        "A biblioteca de pareceres e a geração assistida por inteligência artificial "
        "serão implementadas em uma etapa posterior.",
        icon=":material/info:",
    )
