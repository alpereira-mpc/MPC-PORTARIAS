"""Central de Pendências UI. Aggregation only; actions remain in origin modules."""

from datetime import date
import streamlit as st

from services.access import require_permission
from services.audit import format_local
from services.pending import (
    PAGE_SIZE,
    PERIODS,
    URGENCY_ORDER,
    can_view_pendencias,
    collect_pending,
    summarize,
    visible_cabinets,
)

MODULE_OPTIONS = (
    ("oficios", "Ofícios"),
    ("agenda", "Agenda"),
    ("memorandos", "Memorandos"),
)


def _require(principal):
    if not can_view_pendencias(principal):
        raise ValueError("Acesso não autorizado a este módulo.")
    require_permission(principal, "pendencias")


def _label(urgency):
    color = {
        "VENCIDA": "red",
        "HOJE": "orange",
        "URGENTE": "orange",
        "PRÓXIMA": "blue",
        "FUTURA": "gray",
        "SEM PRAZO": "gray",
    }.get(urgency, "gray")
    return f":{color}[**{urgency}**] · {urgency}"


def _date_text(value):
    if not value:
        return "—"
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return format_local(value)[:10] if value else "—"


def open_origin(item):
    from portal import request_portal_navigation

    module = getattr(item, "source_module", "")
    source_id = getattr(item, "source_id", None)
    gabinete = getattr(item, "gabinete", None)
    metadata = getattr(item, "metadata", None) or {}
    if module == "oficios":
        code = gabinete if gabinete and gabinete != "—" else None
        page = (
            "Acompanhamento"
            if metadata.get("direcao") == "ENVIADO"
            else "Recebidos"
        )
        request_portal_navigation(
            "Ofícios",
            pending_open_oficio={"id": source_id, "gabinete": code, "page": page},
        )
        return
    if module == "agenda":
        request_portal_navigation("Agenda", pending_open_agenda=source_id)
        return
    if module == "memorandos":
        request_portal_navigation(
            "Memorandos",
            pending_open_memorando={"id": source_id, "page": "Em andamento"},
        )
        return
    if module == "sistema":
        request_portal_navigation(
            "Administração",
            pending_open_admin={
                "secao": metadata.get("secao") or "Sistema",
                "aba": metadata.get("aba") or "Saúde",
            },
        )


def _open(item):
    open_origin(item)


def render(store, principal):
    _require(principal)
    st.subheader("CENTRAL DE PENDÊNCIAS")
    cabinets = visible_cabinets(principal)
    period = st.radio(
        "Período",
        [p[0] for p in PERIODS],
        format_func=lambda k: dict(PERIODS)[k],
        horizontal=True,
        key="pending_period",
    )
    a, b, c = st.columns(3)
    module = a.selectbox(
        "Módulo",
        [None, "oficios", "agenda", "memorandos"],
        format_func=lambda k: dict(MODULE_OPTIONS).get(k, "Todos"),
        key="pending_module",
    )
    gabinete_options = [None, *cabinets] if cabinets else [None]
    gabinete = b.selectbox(
        "Gabinete",
        gabinete_options,
        format_func=lambda g: g or "Todos permitidos",
        key="pending_gabinete",
        disabled=not cabinets,
    )
    urgency = c.selectbox(
        "Situação",
        [None, *URGENCY_ORDER],
        format_func=lambda u: u or "Todas",
        key="pending_urgency",
    )
    modules = (module,) if module else None
    items, errors, today = collect_pending(
        store,
        principal,
        modules=modules,
        gabinete=gabinete,
        urgency=urgency,
        period=period,
    )
    counts = summarize(items, today)
    cards = st.columns(5)
    cards[0].metric("Vencidas", counts["vencidas"])
    cards[1].metric("Hoje", counts["hoje"])
    cards[2].metric("Próximos 3 dias", counts["proximos_3"])
    cards[3].metric("Próximos 7 dias", counts["proximos_7"])
    cards[4].metric("Total ativo", counts["total"])
    for key, label in (
        ("oficios", "Ofícios"),
        ("agenda", "Agenda"),
        ("memorandos", "Memorandos"),
    ):
        if errors.get(key):
            st.warning(f"Não foi possível carregar pendências de {label}.")
    if not items:
        st.info("Nenhuma pendência encontrada.")
        return
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = st.number_input("Página", min_value=1, max_value=pages, value=1, step=1)
    start = (int(page) - 1) * PAGE_SIZE
    view = items[start : start + PAGE_SIZE]
    st.caption(f"{len(items)} pendência(s) · página {int(page)} de {pages}")
    st.dataframe(
        [
            {
                "Prazo/Data": _date_text(row.due_date),
                "Pendência": row.title,
                "Módulo": row.navigation,
                "Gabinete": row.gabinete,
                "Situação": row.urgency,
                "Status": row.status_original,
            }
            for row in view
        ],
        hide_index=True,
        use_container_width=True,
    )
    labels = {
        i: f"{_date_text(item.due_date)} · {item.title} · {item.urgency}"
        for i, item in enumerate(view)
    }
    choice = st.selectbox(
        "Abrir origem",
        [None, *labels],
        format_func=lambda i: labels.get(i, "Selecione"),
        key="pending_open_pick",
    )
    if choice is not None:
        item = view[choice]
        label = {
            "oficios": "Ver em Ofícios",
            "agenda": "Ver na Agenda",
            "memorandos": "Ver em Memorandos",
        }[item.source_module]
        if st.button(label, key="pending_open_go"):
            _open(item)
