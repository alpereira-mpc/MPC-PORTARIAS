"""Internal alerts UI. Bell in the sidebar; full view is a special overlay."""

from datetime import date, datetime, time, timedelta
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
    active_attention_items,
    alerts_revision,
    can_view_alertas,
    collect_alerts,
    get_alert_summary,
    invalidate_alert_summary,
)
from services.audit import INSTITUTIONAL_TZ, format_local
from services.pending import PAGE_SIZE, visible_cabinets
from services.pending_ui import open_origin
from services.ui_theme import (
    badge,
    badges,
    card_container,
    empty_state,
    record_html,
    render_html,
    render_record,
    status_tone,
)

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


def _attention(item, key, default=None):
    return (item.metadata or {}).get(key, default)


def _attention_badge(item):
    urgency = _attention(item, "alert_urgency", "Normal")
    tone = {"Vencido": "danger", "Hoje": "warning", "Em breve": "brand"}.get(
        urgency, "neutral"
    )
    return _attention(item, "alert_type", "Providência"), urgency, tone


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
    alert_type, urgency, tone = _attention_badge(item)
    block = record_html(
        item.title or "",
        badges_html=badges((alert_type, "neutral"), (urgency, tone)),
        secondary=item.description or "—",
        meta=meta,
        accent=tone,
        surface="muted",
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


def _refresh_attention():
    invalidate_alert_summary()
    st.session_state.pop(BELL_CACHE_KEY, None)


def _mark(item, store, principal, *, read):
    from database.alert_attention import AlertAttentionStore

    AlertAttentionStore(store).mark_read(
        principal.id, _attention(item, "alert_key"), read=read
    )
    _refresh_attention()


def _snooze(item, store, principal, until):
    from database.alert_attention import AlertAttentionStore

    AlertAttentionStore(store).snooze(
        principal.id, _attention(item, "alert_key"), until
    )
    _refresh_attention()


def _mark_all(store, principal):
    from database.alert_attention import AlertAttentionStore

    items, _errors, _today = active_attention_items(
        store, principal, cached_health=_health_cache()
    )
    AlertAttentionStore(store).mark_all_read(
        principal.id, [_attention(item, "alert_key") for item in items]
    )
    _refresh_attention()


def _snooze_until(days):
    return datetime.combine(
        datetime.now(INSTITUTIONAL_TZ).date() + timedelta(days=days),
        time(9),
        INSTITUTIONAL_TZ,
    )


def _render_item_menu(item, store, principal, key):
    """Discrete secondary actions shared by bell and Central cards."""
    with st.popover("⋯", key=key, help="Ações do alerta"):
        if _attention(item, "read"):
            if st.button("Marcar como não lido", key=key + "_unread"):
                _mark(item, store, principal, read=False)
                st.rerun()
        elif st.button("Marcar como lido", key=key + "_read"):
            _mark(item, store, principal, read=True)
            st.rerun()
        if st.button("Lembrar amanhã", key=key + "_tomorrow"):
            _snooze(item, store, principal, _snooze_until(1))
            st.rerun()
        if st.button("Lembrar em 3 dias", key=key + "_3days"):
            _snooze(item, store, principal, _snooze_until(3))
            st.rerun()
        if st.button("Lembrar na próxima semana", key=key + "_week"):
            _snooze(item, store, principal, _snooze_until(7))
            st.rerun()
        selected = st.date_input(
            "Escolher data", min_value=date.today(), key=key + "_date"
        )
        if st.button("Adiar até esta data", key=key + "_custom"):
            _snooze(
                item,
                store,
                principal,
                datetime.combine(selected, time(9), INSTITUTIONAL_TZ),
            )
            st.rerun()


def _notice_actions(store, principal, item, key):
    metadata = item.metadata or {}
    notice_id = metadata.get("notice_id")
    if not notice_id:
        return
    from services.record_engagement_ui import render_notice_actions

    render_notice_actions(store, principal, notice_id, metadata.get("notice_type"), key)


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
        st.markdown(f"**Alertas** · {total} não lido(s)")
        if not top:
            st.caption("Nenhum alerta ativo no momento.")
            return
        bell_filter = st.radio(
            "Filtro rápido",
            ("Todos", "Não lidos", "Prazos"),
            horizontal=True,
            label_visibility="collapsed",
            key="bell_filter",
        )
        if bell_filter == "Não lidos":
            top = [item for item in top if not _attention(item, "read")]
        elif bell_filter == "Prazos":
            top = [item for item in top if _attention(item, "alert_type") == "Prazo"]
        if not top:
            st.caption("Nenhum alerta para este filtro.")
            return
        previous_group = None
        last = len(top) - 1
        for index, item in enumerate(top):
            group = _attention(item, "alert_urgency", "Normal").upper()
            if group != previous_group:
                st.caption(group)
                previous_group = group
            st.markdown(_bell_item_markdown(item), unsafe_allow_html=True)
            open_col, more_col = st.columns([4, 1])
            with open_col:
                st.button(
                    "Abrir",
                    key=f"bell_open_{index}",
                    on_click=_open_bell_origin,
                    args=(item, store, principal),
                )
            with more_col:
                _render_item_menu(item, store, principal, f"bell_more_{index}")
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
        mark_col, all_col = st.columns(2)
        with mark_col:
            if st.button("Marcar todos como lidos", key="bell_mark_all"):
                _mark_all(store, principal)
                close_bell()
                st.rerun()
        with all_col:
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
    st.subheader("CENTRAL DE ALERTAS")
    st.caption(
        "Itens ativos são derivados dos módulos de origem. Ler ou adiar um alerta não altera o registro original."
    )
    cabinets = visible_cabinets(principal)
    view = st.radio(
        "Visão",
        ("Todos", "Não lidos", "Hoje", "Próximos", "Adiados", "Histórico"),
        horizontal=True,
        key="alerts_view",
    )
    a, b, c, d, e = st.columns(5)
    module_choices = [
        None,
        *(code for code, _label in MODULE_OPTIONS if code != "sistema"),
    ]
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
    kind = c.selectbox(
        "Tipo",
        [None, "Prazo", "Providência", "Tarefa", "Compromisso", "Comunicação"],
        format_func=lambda value: value or "Todos",
        key="alerts_type",
    )
    urgency = d.selectbox(
        "Urgência",
        [None, "Vencido", "Hoje", "Em breve", "Normal"],
        format_func=lambda value: value or "Todas",
        key="alerts_urgency",
    )
    pesquisa = e.text_input(
        "Busca", key="alerts_q", placeholder="Descrição ou gabinete"
    )
    modules = (module,) if module else None
    items, errors, today = active_attention_items(
        store,
        principal,
        modules=modules,
        gabinete=gabinete,
        period="todos",
        cached_health=_health_cache(),
    )
    needle = pesquisa.strip().casefold()
    filtered = []
    for item in items:
        metadata = item.metadata or {}
        if kind and metadata.get("alert_type") != kind:
            continue
        if urgency and metadata.get("alert_urgency") != urgency:
            continue
        if (
            needle
            and needle
            not in " ".join(
                str(value or "")
                for value in (
                    item.title,
                    item.description,
                    item.gabinete,
                    item.source_module,
                )
            ).casefold()
        ):
            continue
        snoozed = bool(metadata.get("snoozed"))
        if view == "Não lidos" and (metadata.get("read") or snoozed):
            continue
        if view == "Hoje" and (metadata.get("alert_urgency") != "Hoje" or snoozed):
            continue
        if view == "Próximos" and (
            metadata.get("alert_urgency") != "Em breve" or snoozed
        ):
            continue
        if view == "Adiados" and not snoozed:
            continue
        # History is deliberately limited to readable state on alerts still
        # derivable from authorized sources; no redundant record snapshots.
        if view == "Histórico" and not metadata.get("read"):
            continue
        if view == "Todos" and snoozed:
            continue
        filtered.append(item)
    items = filtered
    cards = st.columns(4)
    cards[0].metric(
        "Vencidos", sum(1 for i in items if _attention(i, "alert_urgency") == "Vencido")
    )
    cards[1].metric(
        "Hoje", sum(1 for i in items if _attention(i, "alert_urgency") == "Hoje")
    )
    cards[2].metric("Não lidos", sum(1 for i in items if not _attention(i, "read")))
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
    signature = (view, module, gabinete, kind, urgency, pesquisa)
    if st.session_state.get("alerts_page_sig") != signature:
        st.session_state["alerts_page_sig"] = signature
        st.session_state["alerts_page"] = 1
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(int(st.session_state.get("alerts_page") or 1), pages)
    start = (page - 1) * PAGE_SIZE
    view = items[start : start + PAGE_SIZE]
    st.caption(f"{len(items)} alerta(s) · página {page} de {pages}")
    for offset, item in enumerate(view):
        with card_container(offset, f"al_{start}_{offset}"):
            alert_type, urgency, tone = _attention_badge(item)
            render_record(
                item.title,
                badges_html=badges(
                    (alert_type, "neutral"),
                    (urgency, tone),
                    (_module_label(item.source_module), "neutral"),
                ),
                secondary=item.description,
                meta=" · ".join(
                    part
                    for part in (
                        _module_label(item.source_module),
                        item.gabinete,
                        _date_text(item),
                    )
                    if part
                ),
                accent=tone,
            )
            open_col, more_col = st.columns([4, 1])
            with open_col:
                if st.button("Abrir", key=f"alert_open_{start + offset}"):
                    try:
                        open_alert_origin(item, store, principal)
                    except ValueError:
                        st.error("A origem não existe ou você não possui mais acesso.")
            with more_col:
                _render_item_menu(
                    item, store, principal, f"alert_more_{start + offset}"
                )
            _notice_actions(store, principal, item, f"alert_notice_{start + offset}")
    nav_a, nav_b, nav_c = st.columns([1, 2, 1])
    if nav_a.button("Anterior", disabled=page <= 1, key="alerts_prev"):
        st.session_state["alerts_page"] = page - 1
        st.rerun()
    nav_b.caption(f"Página {page} de {pages}")
    if nav_c.button("Próxima", disabled=page >= pages, key="alerts_next"):
        st.session_state["alerts_page"] = page + 1
        st.rerun()
