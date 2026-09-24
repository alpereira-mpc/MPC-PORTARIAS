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
    item_in_period,
    summarize,
    visible_cabinets,
)
from services.ui_theme import (
    badges,
    card_container,
    empty_state,
    filter_mark,
    kpi_mark,
    record_html,
    render_record,
    section_label,
    status_tone,
    stripe_index,
)

MODULE_OPTIONS = (
    ("oficios", "Ofícios"),
    ("agenda", "Agenda e Afastamentos"),
    ("memorandos", "Memorandos"),
    ("tarefas", "Tarefas"),
    ("representacoes", "Representações"),
    ("ouvidoria", "Ouvidoria"),
    ("lembretes", "Lembretes e avisos"),
    ("access_requests", "Administração"),
)
PERIOD_CAPTIONS = dict(PERIODS)
CARD_PERIODS = (
    ("vencidas", "Vencidas", "danger", "vencidas"),
    ("hoje", "Hoje", "warning", "hoje"),
    ("proximos_3", "Próximos 3 dias", "info", "3d"),
    ("proximos_7", "Próximos 7 dias", "brand", "7d"),
    ("total", "Total de pendências ativas", "muted", "todas"),
)
URGENCY_LABELS = {
    "VENCIDA": "Vencida",
    "HOJE": "Hoje",
    "URGENTE": "Urgente",
    "PRÓXIMA": "Próxima",
    "FUTURA": "Agendada",
    "SEM PRAZO": "Sem prazo",
}
OPEN_LABELS = {
    "oficios": "Ver em Ofícios",
    "agenda": "Ver na Agenda",
    "memorandos": "Ver em Memorandos",
    "tarefas": "Ver em Tarefas",
    "representacoes": "Ver em Representações",
    "ouvidoria": "Ver na Ouvidoria",
    "lembretes": "Abrir origem",
    "access_requests": "Ver em Administração",
    "sistema": "Ver em Administração",
}


def _require(principal):
    if not can_view_pendencias(principal):
        raise ValueError("Acesso não autorizado a este módulo.")
    require_permission(principal, "pendencias")


def _date_text(value):
    if not value:
        return "—"
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return format_local(value)[:10] if value else "—"


def open_origin(item, store=None, principal=None, *, rerun=True):
    from portal import queue_portal_navigation, request_portal_navigation

    navigate = request_portal_navigation if rerun else queue_portal_navigation

    module = getattr(item, "source_module", "")
    source_id = getattr(item, "source_id", None)
    gabinete = getattr(item, "gabinete", None)
    metadata = getattr(item, "metadata", None) or {}
    if module == "lembretes":
        origin_module = metadata.get("origin_module")
        origin_id = metadata.get("origin_id")
        if store is None or principal is None:
            return
        from services.record_engagement_ui import open_linked_origin

        open_linked_origin(origin_module, origin_id, store, principal, rerun=rerun)
        return
    if module == "portarias":
        navigate("Portarias", nav="Histórico", portaria_open_id=source_id)
        return
    if module == "oficios":
        code = gabinete if gabinete and gabinete != "—" else None
        page = (
            "Acompanhamento"
            if metadata.get("direcao") == "ENVIADO"
            else "Recebidos"
        )
        navigate(
            "Ofícios",
            pending_open_oficio={"id": source_id, "gabinete": code, "page": page},
        )
        return
    if module == "agenda":
        navigate(
            "Agenda",
            pending_open_agenda=metadata.get("compromisso_id")
            or metadata.get("afastamento_id")
            or source_id,
        )
        return
    if module == "memorandos":
        navigate(
            "Memorandos",
            pending_open_memorando={"id": source_id, "page": "Histórico"},
        )
        return
    if module == "tarefas":
        identifier = int(source_id) if str(source_id).isdigit() else source_id
        navigate("Tarefas", tarefas_open_id=identifier)
        return
    if module == "representacoes":
        identifier = int(source_id) if str(source_id).isdigit() else source_id
        navigate("Representações", representacoes_view=identifier)
        return
    if module == "ouvidoria":
        identifier = int(source_id) if str(source_id).isdigit() else source_id
        navigate("Ouvidoria", ouvidoria_open_id=identifier)
        return
    if module == "sistema":
        navigate(
            "Administração",
            pending_open_admin={
                "secao": metadata.get("secao") or "Sistema",
                "aba": metadata.get("aba") or "Saúde",
            },
        )
        return
    if module == "admin":
        navigate(
            "Administração",
            pending_open_admin={"secao": metadata.get("secao") or "Acessos e Auditoria"},
        )
        return
    if module == "access_requests":
        navigate(
            "Administração",
            pending_open_admin={"secao": metadata.get("secao") or "Solicitações"},
        )


def _open(item, store=None, principal=None):
    open_origin(item, store, principal)


def _set_period(code):
    """Apply a listing period from a card. Safe as on_click (before widgets)."""
    st.session_state["pending_period"] = code
    st.session_state["pending_page"] = 1


def render(store, principal):
    _require(principal)
    st.subheader("CENTRAL DE PENDÊNCIAS")
    cabinets = visible_cabinets(principal)
    with st.container(border=True):
        filter_mark()
        period = st.radio(
            "Período da listagem",
            [p[0] for p in PERIODS],
            format_func=lambda k: dict(PERIODS)[k],
            horizontal=True,
            key="pending_period",
        )
        a, b, c, d = st.columns(4)
        module = a.selectbox(
            "Módulo",
            [None, *(item[0] for item in MODULE_OPTIONS)],
            format_func=lambda k: dict(MODULE_OPTIONS).get(k, "Todos os módulos"),
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
            "Situação (prazo)",
            [None, *URGENCY_ORDER],
            format_func=lambda u: URGENCY_LABELS.get(u, u or "Todas"),
            key="pending_urgency",
        )
        pesquisa = d.text_input("Busca", key="pending_q", placeholder="Título ou gabinete")
    modules = (module,) if module else None
    if module == "access_requests":
        modules = ("admin",)
    all_items, errors, today = collect_pending(
        store,
        principal,
        modules=modules,
        gabinete=gabinete,
        urgency=urgency,
        period="todas",
        pesquisa=pesquisa,
    )
    items = [row for row in all_items if item_in_period(row, period, today)]
    counts = summarize(all_items, today)
    section_label("Resumo geral das pendências ativas")
    st.caption(
        "Os totais abaixo não mudam com o período da listagem. "
        "Respeitam módulo, gabinete, situação e busca. Clique em um card para filtrar a lista."
    )
    cards = st.columns(5)
    for column, (field, label, tone, code) in zip(cards, CARD_PERIODS):
        selected = period == code
        with column:
            kpi_mark(tone if selected else "muted")
            st.metric(label, counts[field])
            st.button(
                "Selecionado" if selected else "Ver",
                key="pending_card_" + code,
                disabled=selected,
                type="primary" if selected else "secondary",
                on_click=_set_period,
                args=(code,),
            )
    for key, label in MODULE_OPTIONS:
        err_key = "admin" if key == "access_requests" else key
        if errors.get(err_key) or errors.get(key):
            st.warning(f"Não foi possível carregar pendências de {label}.")
    listing_label = PERIOD_CAPTIONS.get(period, period)
    st.caption(f"Exibindo pendências: {listing_label}")
    st.caption(
        "Situação (prazo) indica vencida, hoje, urgente ou próxima. "
        "Status é a situação do registro no módulo de origem."
    )
    if not items:
        empty_state("Nenhuma pendência encontrada para o filtro selecionado.")
        if all_items and period not in ("todas", "todos"):
            st.caption("Altere o período para visualizar outras pendências ativas.")
        return
    signature = (period, module, gabinete, urgency, pesquisa)
    if st.session_state.get("pending_page_sig") != signature:
        st.session_state["pending_page_sig"] = signature
        st.session_state["pending_page"] = 1
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = int(st.session_state.get("pending_page") or 1)
    if page > pages:
        page = pages
        st.session_state["pending_page"] = page
    start = (page - 1) * PAGE_SIZE
    view = items[start : start + PAGE_SIZE]
    st.caption(f"{len(items)} pendência(s) · página {page} de {pages}")
    module_names = dict(MODULE_OPTIONS)
    for index, row in enumerate(view):
        with card_container(index, f"pend_{row.source_module}_{row.source_id}"):
            render_record(
                row.title,
                badges_html=badges(
                    (URGENCY_LABELS.get(row.urgency, row.urgency), status_tone(row.urgency)),
                    (module_names.get(row.source_module, row.navigation), "neutral"),
                ),
                secondary=row.subtitle or row.navigation,
                meta=" · ".join(
                    part
                    for part in (
                        _date_text(row.due_date),
                        row.gabinete if row.gabinete and row.gabinete != "—" else "",
                        "Status: " + row.status_original if row.status_original else "",
                    )
                    if part
                ),
                accent=status_tone(row.urgency),
                stripe=stripe_index(index),
            )
            if st.button(
                OPEN_LABELS.get(row.source_module, "Abrir origem"),
                key=f"pending_go_{row.source_module}_{row.source_id}_{index}",
            ):
                try:
                    _open(row, store, principal)
                except ValueError:
                    st.error("A origem não existe ou você não possui mais acesso.")
            if row.source_module == "lembretes":
                from services.record_engagement_ui import render_notice_actions

                meta = row.metadata or {}
                render_notice_actions(
                    store,
                    principal,
                    meta.get("notice_id"),
                    meta.get("notice_type"),
                    f"pending_notice_{row.source_id}_{index}",
                )
    nav_a, nav_b, nav_c = st.columns([1, 2, 1])
    if nav_a.button("Anterior", disabled=page <= 1, key="pending_prev"):
        st.session_state["pending_page"] = page - 1
        st.rerun()
    nav_b.caption(f"Página {page} de {pages}")
    if nav_c.button("Próxima", disabled=page >= pages, key="pending_next"):
        st.session_state["pending_page"] = page + 1
        st.rerun()
    labels = {
        i: f"{_date_text(item.due_date)} · {item.title} · {URGENCY_LABELS.get(item.urgency, item.urgency)}"
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
        label = OPEN_LABELS.get(item.source_module, "Abrir origem")
        if st.button(label, key="pending_open_go"):
            try:
                _open(item, store, principal)
            except ValueError:
                st.error("A origem não existe ou você não possui mais acesso.")
