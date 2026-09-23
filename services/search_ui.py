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

HOME_SEARCH_ACTIVE = "_global_search_home_active"
HOME_SEARCH_INPUT_VERSION = "_global_search_input_version"
HOME_SEARCH_STATE_KEYS = (
    "global_search_q",
    "global_search_run",
    "global_search_error_state",
    "global_search_error",
    "global_search_errors",
    "global_search_results",
    "global_search_status",
)


def _date_text(value):
    if not value:
        return ""
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return format_local(value)[:10] if value else ""


def _open(item):
    from services.pending_ui import open_origin

    open_origin(item)


def _prepare_home_search():
    """Discard search state left by an earlier visit before rendering Home."""
    if st.session_state.get(HOME_SEARCH_ACTIVE):
        return
    _discard_home_search_state()
    st.session_state[HOME_SEARCH_ACTIVE] = True


def _discard_home_search_state():
    for key in HOME_SEARCH_STATE_KEYS:
        st.session_state.pop(key, None)
    for key in list(st.session_state):
        if key.startswith("global_search_q_"):
            st.session_state.pop(key, None)


def _clear_home_search():
    """Clear state and force a fresh browser input on the ensuing full rerun."""
    version = int(st.session_state.get(HOME_SEARCH_INPUT_VERSION, 0)) + 1
    _discard_home_search_state()
    st.session_state[HOME_SEARCH_INPUT_VERSION] = version


def _submit_home_search(input_key):
    """Commit the current input from either Enter or the Search button."""
    st.session_state["global_search_run"] = normalize_term(
        st.session_state.get(input_key, "")
    )


def _persistent_errors(term, errors):
    """Confirm a source failure on two consecutive renders before showing it."""
    failed = tuple(sorted(key for key, value in errors.items() if value))
    state_key = "global_search_error_state"
    if not failed:
        st.session_state.pop(state_key, None)
        return ()
    previous = st.session_state.get(state_key) or {}
    occurrences = (
        int(previous.get("occurrences", 0)) + 1
        if previous.get("term") == term and tuple(previous.get("sources", ())) == failed
        else 1
    )
    st.session_state[state_key] = {
        "term": term,
        "sources": failed,
        "occurrences": occurrences,
    }
    return failed if occurrences >= 2 else ()


def render_home_search(store, principal):
    _prepare_home_search()
    section_label("Busca global")
    st.caption(
        "Localize Ofícios, Portarias, Agenda, Representações, Notícias de Fato "
        "e outros registros permitidos."
    )
    input_version = int(st.session_state.get(HOME_SEARCH_INPUT_VERSION, 0))
    input_key = (
        "global_search_q"
        if input_version == 0
        else f"global_search_q_{input_version}"
    )
    search_active = any(
        st.session_state.get(key)
        for key in HOME_SEARCH_STATE_KEYS
        if key != "global_search_q"
    )
    query = st.text_input(
        "Busca",
        key=input_key,
        label_visibility="collapsed",
        placeholder="Pesquisar no Ferramentas MPC-PB...",
        on_change=_submit_home_search,
        args=(input_key,),
    )
    search_column, clear_column = st.columns(2)
    with search_column:
        st.button(
            "Buscar",
            type="primary",
            key="global_search_submit",
            on_click=_submit_home_search,
            args=(input_key,),
        )
    with clear_column:
        if search_active or normalize_term(query):
            st.button(
                "Limpar busca",
                key="global_search_clear",
                on_click=_clear_home_search,
            )
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
    persistent_errors = _persistent_errors(term, errors)
    if persistent_errors and not hits:
        st.error("Não foi possível concluir a busca agora.")
        return
    for key in persistent_errors:
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
