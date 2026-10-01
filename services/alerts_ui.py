"""Internal alerts UI. Bell in the sidebar; full view is a special overlay."""

from datetime import date
import time
import streamlit as st

from services.access import has_permission, require_permission
from services.alerts import (
    ATENCAO,
    ALTO,
    BELL_CACHE_KEY,
    BELL_CACHE_SECONDS,
    CRITICO,
    INFORMATIVO,
    PERIODS,
    SEVERITY_ORDER,
    alerts_revision,
    can_view_alertas,
    collect_alerts,
    get_alert_summary,
)
from services.audit import format_local
from services.pending import PAGE_SIZE, visible_cabinets
from services.pending_ui import open_origin
from services.ui_theme import badge, badges, card_container, empty_state, record_html, render_html, render_record, status_tone

MODULE_OPTIONS = (
    ("oficios", "Ofícios"),
    ("agenda", "Agenda e Afastamentos"),
    ("memorandos", "Memorandos"),
    ("tarefas", "Tarefas"),
    ("representacoes", "Representações"),
    ("ouvidoria", "Ouvidoria"),
    ("sistema", "Sistema"),
    ("access_requests", "Solicitações"),
)
BELL_OPEN_KEY = "_alerts_bell_open"
BELL_EPOCH = "_alerts_bell_epoch"
BELL_INTENT = "_alerts_bell_intent"


def _require(principal):
    if not can_view_alertas(principal):
        raise ValueError("Acesso não autorizado a este módulo.")
    require_permission(principal, "alertas")


def _severity_label(severity):
    color = {
        CRITICO: "red",
        ALTO: "orange",
        ATENCAO: "orange",
        INFORMATIVO: "blue",
    }.get(severity, "gray")
    return f":{color}[**{severity}**] · {severity}"


def _date_text(item):
    if item.datetime:
        return item.datetime.strftime("%d/%m/%Y %H:%M")
    if item.date:
        if isinstance(item.date, date):
            return item.date.strftime("%d/%m/%Y")
        return format_local(item.date)[:10]
    return "—"


def _module_label(code):
    return dict(MODULE_OPTIONS).get(code, code)


def _open_label(item):
    if (item.metadata or {}).get("origin_module"):
        return "Abrir origem"
    return {
        "oficios": "Ver em Ofícios",
        "agenda": "Ver na Agenda",
        "memorandos": "Ver em Memorandos",
        "tarefas": "Ver em Tarefas",
        "representacoes": "Ver em Representações",
        "ouvidoria": "Ver na Ouvidoria",
        "sistema": "Ver Saúde do Sistema",
        "access_requests": "Ver Solicitações",
    }.get(item.source_module, "Ver origem")


def _bell_item_markdown(item):
    meta_parts = [item.gabinete, _date_text(item)]
    if item.source_module == "agenda":
        local = (item.metadata or {}).get("local")
        if local and local.strip():
            meta_parts.append(local.strip())
    meta = " · ".join(part for part in meta_parts if part)
    block = record_html(
        item.title or "",
        badges_html=badge(item.severity, status_tone(item.severity)),
        secondary=item.description or "—",
        meta=meta,
        accent=status_tone(item.severity),
        surface=status_tone(item.severity),
        boxed=True,
    )
    block = block.replace(
        '<div class="mpc-record ',
        '<div class="mpc-sidebar-alert-card mpc-record ',
        1,
    )
    return '<div class="mpc-bell-alert">' + block + "</div>"


def _health_cache():
    if "sistema_health" in st.session_state:
        return st.session_state["sistema_health"]
    return None


def load_bell_summary(store, principal):
    cache = (
        st.session_state[BELL_CACHE_KEY] if BELL_CACHE_KEY in st.session_state else None
    )
    now = time.monotonic()
    revision = alerts_revision()
    # A permission change or a different account must never reuse old alerts.
    scope = (id(store), principal)
    if (
        isinstance(cache, dict)
        and cache.get("scope") == scope
        and cache.get("revision") == revision
        and now - cache.get("at", 0) < BELL_CACHE_SECONDS
    ):
        return cache["payload"]
    payload = get_alert_summary(
        store,
        principal,
        cached_health=_health_cache(),
        revision=revision,
    )
    st.session_state[BELL_CACHE_KEY] = {
        "at": now,
        "revision": revision,
        "scope": scope,
        "payload": payload,
    }
    return payload


def _state_get(state, key, default=None):
    if state is None:
        state = st.session_state
    return state[key] if key in state else default


def bell_widget_key(state=None):
    """Key of the popover currently mounted.

    The popover itself does not track open/closed state, so clicking outside
    closes it in the browser without a script rerun. An internal action still
    retires this key. A later rerun cannot reopen the panel by replaying the
    browser value stored for the old widget.
    """
    epoch = int(_state_get(state, BELL_EPOCH, 0) or 0)
    if epoch <= 0:
        return BELL_OPEN_KEY
    return f"{BELL_OPEN_KEY}_{epoch}"


def bell_is_open(state=None):
    return bool(_state_get(state, bell_widget_key(state), False))


def close_bell():
    st.session_state[BELL_INTENT] = False
    st.session_state.pop(bell_widget_key(), None)
    st.session_state[BELL_EPOCH] = int(st.session_state.get(BELL_EPOCH, 0) or 0) + 1


def open_alert_origin(item, store=None, principal=None, *, rerun=True):
    from portal import PORTAL_SPECIAL_RETURN, clear_alerts_overlay

    clear_alerts_overlay()
    if PORTAL_SPECIAL_RETURN in st.session_state:
        st.session_state.pop(PORTAL_SPECIAL_RETURN)
    origin_module = (item.metadata or {}).get("origin_module")
    if origin_module and store is not None and principal is not None:
        # Engagement alerts carry origem_modulo; navigate by that key so Ofícios
        # enviado/recebido open the correct page and permissions are rechecked.
        from services.record_engagement_ui import open_linked_origin

        origin_id = (item.metadata or {}).get("origin_id") or item.source_id
        open_linked_origin(origin_module, origin_id, store, principal, rerun=rerun)
        return
    open_origin(item, rerun=rerun)


def _open_bell_origin(item, store, principal):
    close_bell()
    try:
        open_alert_origin(item, store, principal, rerun=False)
    except ValueError:
        st.session_state["_alerts_bell_navigation_error"] = True


def _open_all_from_bell():
    from portal import queue_alerts_view

    close_bell()
    queue_alerts_view()


def _notice_actions(store, principal, item, key):
    metadata = item.metadata or {}
    notice_id = metadata.get("notice_id")
    if not notice_id:
        return
    from services.record_engagement_ui import render_notice_actions

    render_notice_actions(
        store, principal, notice_id, metadata.get("notice_type"), key
    )


def render_bell(store, principal):
    if not can_view_alertas(principal):
        return
    try:
        summary = load_bell_summary(store, principal)
    except Exception:
        summary = {"total": 0, "top": []}
    total = int(summary.get("total") or 0)
    label = f"🔔 {total}" if total else "🔔"
    widget_key = bell_widget_key()
    if st.session_state.get(BELL_INTENT) is False:
        st.session_state[widget_key] = False
    with st.popover(
        label,
        use_container_width=True,
        key=widget_key,
        on_change="ignore",
    ):
        if st.session_state.pop("_alerts_bell_navigation_error", None):
            st.error("A origem não existe ou você não possui mais acesso.")
        top = summary.get("top") or []
        if not top:
            st.caption("Nenhum alerta ativo no momento.")
            return
        last = len(top) - 1
        for index, item in enumerate(top):
            st.markdown(_bell_item_markdown(item), unsafe_allow_html=True)
            st.button(
                _open_label(item),
                key=f"bell_open_{index}",
                on_click=_open_bell_origin,
                args=(item, store, principal),
            )
            if index != last:
                st.markdown(
                    '<hr style="margin:0.45rem 0;border:none;'
                    'border-top:1px solid rgba(49,51,63,.12)">',
                    unsafe_allow_html=True,
                )
        st.markdown(
            '<hr style="margin:0.55rem 0 0.2rem;border:none;'
            'border-top:1px solid rgba(49,51,63,.1)">',
            unsafe_allow_html=True,
        )
        st.button(
            "Ver todos os alertas",
            key="bell_open_all",
            type="tertiary",
            on_click=_open_all_from_bell,
        )


def render(store, principal):
    _require(principal)
    if st.button("← Voltar", key="alerts_back"):
        from portal import close_alerts_view

        close_alerts_view()
    st.subheader("ALERTAS")
    st.caption(
        "Os alertas são atualizados automaticamente conforme os registros dos módulos."
    )
    cabinets = visible_cabinets(principal)
    period = st.radio(
        "Período",
        [p[0] for p in PERIODS],
        format_func=lambda k: dict(PERIODS)[k],
        horizontal=True,
        key="alerts_period",
    )
    a, b, c = st.columns(3)
    module_choices = [None, "oficios", "agenda", "memorandos", "tarefas"]
    if has_permission(principal, "admin"):
        module_choices.append("sistema")
    module = a.selectbox(
        "Módulo",
        module_choices,
        format_func=lambda k: dict(MODULE_OPTIONS).get(k, "Todos"),
        key="alerts_module",
    )
    gabinete_options = [None, *cabinets] if cabinets else [None]
    gabinete = b.selectbox(
        "Gabinete",
        gabinete_options,
        format_func=lambda g: g or "Todos permitidos",
        key="alerts_gabinete",
        disabled=not cabinets,
    )
    severity = c.selectbox(
        "Severidade",
        [None, *SEVERITY_ORDER],
        format_func=lambda u: u or "Todas",
        key="alerts_severity",
    )
    modules = (module,) if module else None
    items, errors, _today = collect_alerts(
        store,
        principal,
        modules=modules,
        gabinete=gabinete,
        severity=severity,
        period=period,
        cached_health=_health_cache(),
    )
    cards = st.columns(4)
    cards[0].metric("Críticos", sum(1 for i in items if i.severity == CRITICO))
    cards[1].metric("Altos", sum(1 for i in items if i.severity == ALTO))
    cards[2].metric("Atenção", sum(1 for i in items if i.severity == ATENCAO))
    cards[3].metric("Total", len(items))
    labels = {
        "oficios": "Ofícios",
        "agenda": "Agenda e Afastamentos",
        "memorandos": "Memorandos",
        "sistema": "Sistema",
    }
    for key, name in labels.items():
        if key == "sistema" and not has_permission(principal, "admin"):
            continue
        if errors.get(key):
            st.warning(f"Não foi possível carregar alertas da {name}.")
    if not items:
        empty_state("Nenhum alerta ativo no momento.")
        return
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = st.number_input("Página", min_value=1, max_value=pages, value=1, step=1)
    start = (int(page) - 1) * PAGE_SIZE
    view = items[start : start + PAGE_SIZE]
    st.caption(f"{len(items)} alerta(s) · página {int(page)} de {pages}")
    for offset, item in enumerate(view):
        with card_container(offset, f"al_{start}_{offset}"):
            render_record(
                item.title,
                badges_html=badges(
                    (item.severity, status_tone(item.severity)),
                    (_module_label(item.source_module), "neutral"),
                ),
                secondary=item.description,
                meta=" · ".join(
                    part
                    for part in (_module_label(item.source_module), item.gabinete, _date_text(item))
                    if part
                ),
                accent=status_tone(item.severity),
            )
            if st.button(_open_label(item), key=f"alert_open_{start + offset}"):
                try:
                    open_alert_origin(item, store, principal)
                except ValueError:
                    st.error("A origem não existe ou você não possui mais acesso.")
            _notice_actions(store, principal, item, f"alert_notice_{start + offset}")
