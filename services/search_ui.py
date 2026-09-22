"""Home global search UI. Read-only; opening origin reuses Pendências navigation."""

from datetime import date
import streamlit as st

from services.audit import format_local
from services.search import (
    MIN_CHARS,
    MODULE_LABELS,
    global_search,
    group_hits,
    normalize_term,
)
from services.ui_theme import (
    badges,
    card_container,
    empty_state,
    render_record,
    section_label,
    status_tone,
    stripe_index,
)

OPEN_LABELS = {
    "portarias": "Ver em Portarias",
    "oficios": "Ver em Ofícios",
    "agenda": "Ver na Agenda",
    "memorandos": "Ver em Memorandos",
    "tarefas": "Ver em Tarefas",
    "representacoes": "Ver em Representações",
    "ouvidoria": "Ver na Ouvidoria",
    "admin": "Ver em Administração",
}


def _date_text(value):
    if not value:
        return ""
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return format_local(value)[:10] if value else ""


def _open(item):
    from services.pending_ui import open_origin

    open_origin(item)


def render_home_search(store, principal):
    section_label("Busca global")
    st.caption(
        "Localize Ofícios, Portarias, Agenda, Representações, Notícias de Fato "
        "e outros registros permitidos."
    )
    with st.form("global_search_form", border=False):
        query = st.text_input(
            "Busca",
            key="global_search_q",
            label_visibility="collapsed",
            placeholder="Pesquisar no Ferramentas MPC-PB...",
        )
        submitted = st.form_submit_button("Buscar", type="primary")
    if submitted:
        st.session_state["global_search_run"] = normalize_term(query)
    term = st.session_state.get("global_search_run")
    if not term:
        st.caption(f"Digite pelo menos {MIN_CHARS} caracteres para pesquisar.")
        return
    hits, errors, meta = global_search(store, principal, term)
    status = meta.get("status")
    if status == "empty":
        st.caption(f"Digite pelo menos {MIN_CHARS} caracteres para pesquisar.")
        return
    if status == "short":
        empty_state(f"Informe ao menos {MIN_CHARS} caracteres.")
        return
    if any(errors.values()) and not hits:
        st.error("Não foi possível concluir a busca agora.")
        return
    for key, failed in errors.items():
        if failed:
            st.warning(
                "Não foi possível buscar em "
                + MODULE_LABELS.get(key, key)
                + "."
            )
    st.caption(f"Resultados para: “{meta.get('term') or term}”")
    if not hits:
        empty_state("Nenhum resultado encontrado para os filtros e permissões atuais.")
        return
    groups = group_hits(hits)
    index = 0
    for module, rows in groups:
        section_label(MODULE_LABELS.get(module, module))
        st.caption(f"{len(rows)} resultado(s)")
        for row in rows:
            with card_container(index, f"search_{module}_{row.source_id}"):
                render_record(
                    row.title,
                    badges_html=badges(
                        (MODULE_LABELS.get(module, module), "neutral"),
                        (row.status, status_tone(row.status)) if row.status else None,
                    ),
                    secondary=row.subtitle or row.description or row.navigation,
                    meta=" · ".join(
                        part
                        for part in (
                            _date_text(row.date),
                            row.gabinete if row.gabinete and row.gabinete != "—" else "",
                            row.description if row.description and row.description != row.subtitle else "",
                        )
                        if part
                    ),
                    accent=status_tone(row.status),
                    stripe=stripe_index(index),
                )
                if st.button(
                    OPEN_LABELS.get(module, "Abrir origem"),
                    key=f"search_open_{module}_{row.source_id}_{index}",
                ):
                    _open(row)
            index += 1
