"""Administration screens for access analytics and append-only audit logs."""

from datetime import date
import streamlit as st
from database.access import AccessStore
from database.audit import PAGE_SIZE, EXPORT_LIMIT, AuditStore
from services.access import require_permission
from services.audit import (
    ACTION_TYPE_OPTIONS,
    MODULE_LABELS,
    apply_action_type,
    dashboard_hoje,
    details_dict,
    entity_label,
    event_label,
    action_label,
    export_csv,
    format_local,
    format_local_short,
    module_label,
    overview,
    period_bounds,
    result_label,
    resumo_humano,
    user_overview,
)
from services.themes import valid_theme
from services.ui_theme import (
    DANGER,
    INFO,
    WARNING,
    definition_block,
    empty_state,
    filter_mark,
    kpi_mark,
    section_label,
    style_striped_table,
)


PERIODS = (
    ("7d", "Últimos 7 dias"),
    ("30d", "Últimos 30 dias"),
    ("hoje", "Hoje"),
    ("personalizado", "Personalizado"),
)
RESULT_OPTIONS = (
    ("", "Todos"),
    ("OK", "Sucesso"),
    ("ERRO", "Falha"),
    ("NEGADO", "Recusado"),
)


def _theme_name():
    cached = st.session_state.get("_portal_theme") or {}
    return valid_theme(cached.get("name"))


def _set_log_page(page):
    """Set the requested page before Streamlit performs its widget rerun."""
    st.session_state["log_page"] = page


def _period_filter(prefix):
    choice = st.radio(
        "Período",
        [p[0] for p in PERIODS],
        format_func=lambda k: dict(PERIODS)[k],
        horizontal=True,
        key=prefix + "periodo",
    )
    start = end = None
    if choice == "personalizado":
        a, b = st.columns(2)
        start = a.date_input("De", value=date.today(), format="DD/MM/YYYY", key=prefix + "de")
        end = b.date_input("Até", value=date.today(), format="DD/MM/YYYY", key=prefix + "ate")
    try:
        inicio, fim = period_bounds(choice, start, end)
    except ValueError as exc:
        st.error(str(exc))
        return None
    return {"inicio": inicio, "fim": fim}


def _result_tone(code):
    if code in ("ERRO", "FALHA"):
        return "danger"
    if code == "NEGADO":
        return "warning"
    return "success"


def event_highlight(record):
    """Return the most relevant administrative signal for one audit event."""
    result = record.get("resultado") or ""
    event = record.get("evento") or ""
    action = record.get("acao") or ""
    if result in ("ERRO", "FALHA"):
        return "Falha", "danger"
    if result == "NEGADO" or "NEGADO" in event or "BLOQUEADO" in event:
        return "Acesso negado", "warning"
    if action in ("EXCLUIR", "REMOVER") or "EXCLUID" in event:
        return "Exclusão", "danger"
    if action == "PERMISSOES" or "PERMISSO" in event:
        return "Permissões", "warning"
    if action == "EXPORTAR" or event in ("DOCUMENTO_BAIXADO", "BACKUP_DISPONIBILIZADO"):
        return "Download", "info"
    return "", "neutral"


def event_rows(rows):
    return [
        {
            "Data/hora": format_local_short(row.get("criado_em")),
            "Usuário": row.get("usuario_nome") or row.get("usuario_email") or "—",
            "Módulo": module_label(row.get("modulo")),
            "Ação": action_label(row.get("acao") or row.get("evento")),
            "Resultado": result_label(row.get("resultado")),
            "Destaque": event_highlight(row)[0] or "—",
            "Resumo": resumo_humano(row),
        }
        for row in rows
    ]


def style_event_table(rows, theme_name):
    """Theme-aware compact log with semantic emphasis limited to one column."""
    import pandas as pd
    from services.themes import theme_tokens

    frame = pd.DataFrame(event_rows(rows))
    tokens = theme_tokens(valid_theme(theme_name))
    base = tokens["themed_table_bg"]
    stripe = tokens["themed_table_stripe_bg"]
    foreground = tokens["themed_table_fg"]
    tones = {
        "Falha": DANGER,
        "Acesso negado": WARNING,
        "Exclusão": DANGER,
        "Permissões": WARNING,
        "Download": INFO,
    }

    def paint(row):
        fill = base if row.name % 2 == 0 else stripe
        styles = [f"background-color: {fill}; color: {foreground}"] * len(row)
        highlight = row.get("Destaque")
        if highlight in tones:
            position = frame.columns.get_loc("Destaque")
            styles[position] += f"; color: {tones[highlight]}; font-weight: 700"
        return styles

    return frame.style.apply(paint, axis=1)


def compact_modules(modules, limit=2):
    labels = [module_label(module) for module in modules or ()]
    if len(labels) <= limit:
        return ", ".join(labels) or "—"
    return ", ".join(labels[:limit]) + f" +{len(labels) - limit}"


def access_event_rows(rows):
    def historical_identity(row):
        name = row.get("usuario_nome") or ""
        email = row.get("usuario_email") or ""
        return " · ".join(item for item in (name, email) if item) or "—"

    return [
        {
            "Data/hora": format_local_short(row.get("criado_em")),
            "Identidade histórica": historical_identity(row),
            "Módulo": module_label(row.get("modulo")),
            "Evento": event_label(row.get("evento")),
            "Resultado": result_label(row.get("resultado")),
            "Resumo": resumo_humano(row),
        }
        for row in rows
    ]


def _render_kpis(store, principal):
    data = dashboard_hoje(store, principal)
    items = (
        ("Atividades hoje", data["atividades"], "brand"),
        ("Usuários que acessaram hoje", data["usuarios_ativos"], "info"),
        ("Alterações administrativas", data["admin"], "warning"),
        ("Downloads de documentos", data["downloads"], "success"),
        ("Falhas/erros registrados", data["falhas"], "danger" if data["falhas"] else "muted"),
    )
    for group in (items[:3], items[3:]):
        columns = st.columns(len(group))
        for column, (title, value, tone) in zip(columns, group):
            with column:
                kpi_mark(tone)
                st.metric(title, value)


def _render_overview(store, principal):
    # The page-level KPI strip already loaded today's dashboard.
    data = overview(store, principal, include_dashboard=False)
    st.caption("Indicadores calculados no fuso institucional America/Recife. Logs em UTC.")
    section_label("Períodos comparados")
    st.dataframe(
        style_striped_table(
            [
                {
                    "Período": label,
                    "Usuários que acessaram": data[key]["usuarios"],
                    "Sessões": data[key]["sessoes"],
                    "Atividades": data[key]["acoes"],
                }
                for key, label in (
                    ("hoje", "Hoje"),
                    ("semana", "Últimos 7 dias"),
                    ("mes", "Últimos 30 dias"),
                )
            ],
            _theme_name(),
        ),
        hide_index=True,
        width="stretch",
        key="audit_period_comparison",
    )
    latest = data["ultimo_acesso"]
    used = data["modulo_mais_usado"]
    section_label("Resumo operacional")
    a, b, c = st.columns(3)
    a.metric("Usuários ativos cadastrados", data["usuarios_ativos"])
    b.write("**Último usuário que acessou**")
    b.write(latest["nome"] if latest else "—")
    b.caption(latest["email"] if latest else "")
    b.caption(format_local(latest["criado_em"]) if latest else "")
    c.write("**Módulo mais utilizado · últimos 30 dias**")
    c.write(module_label(used["modulo"]) if used else "—")
    rows = user_overview(
        store,
        principal,
        {"inicio": period_bounds("30d")[0]},
        users=data["usuarios"],
    )
    st.subheader("Usuários")
    st.dataframe(
        style_striped_table(
            [
                {
                    "Usuário": f"{r['nome']} ({r['email']})" if r["email"] else r["nome"],
                    "Último acesso": format_local(r.get("ultimo_acesso")),
                    "Sessões": r.get("sessoes") or 0,
                    "Última atividade": format_local(r.get("ultima_atividade")),
                }
                for r in rows
            ],
            _theme_name(),
        ),
        hide_index=True,
        width="stretch",
    )


def _render_accesses(store, principal):
    users = AccessStore(store).list_users()
    emails = {u["email"]: u["nome"] + " · " + u["email"] for u in users}
    filters = _period_filter("acc_") or {}
    a, b, c = st.columns(3)
    picked = a.selectbox(
        "Usuário",
        [None, *sorted(emails)],
        format_func=lambda e: emails.get(e, "Todos"),
        key="acc_user",
    )
    perfil = b.selectbox(
        "Perfil",
        [None, "ADMINISTRADOR", "USUARIO"],
        format_func=lambda p: p or "Todos",
        key="acc_perfil",
    )
    modulo = c.selectbox(
        "Módulo",
        [None, *MODULE_LABELS],
        format_func=lambda m: module_label(m) if m else "Todos",
        key="acc_modulo",
    )
    if picked:
        filters["usuario_email"] = picked
    if modulo:
        filters["modulo"] = modulo
    rows = user_overview(store, principal, filters)
    if perfil:
        rows = [r for r in rows if r.get("perfil") == perfil]
    if not rows:
        empty_state("Nenhum acesso no período filtrado.")
        return
    st.dataframe(
        style_striped_table(
            [
                {
                    "Usuário": f"{r['nome']} · {r['email']}" if r["email"] else r["nome"],
                    "Perfil": r.get("perfil") or "—",
                    "Situação": "Ativo" if r.get("ativo") else ("Inativo" if r.get("ativo") is False else "—"),
                    "Sessões": r.get("sessoes") or 0,
                    "Último acesso": format_local(r.get("ultimo_acesso")),
                    "Módulos": compact_modules(r.get("modulos")),
                }
                for r in rows
            ],
            _theme_name(),
        ),
        hide_index=True,
        width="stretch",
    )
    options = {r["email"]: r["nome"] + " · " + r["email"] for r in rows if r.get("email")}
    selected = st.selectbox(
        "Histórico do usuário",
        [None, *options],
        format_func=lambda e: options.get(e, "Selecione"),
        key="acc_timeline",
    )
    if selected:
        selected_row = next((row for row in rows if row.get("email") == selected), None)
        if selected_row:
            module_names = ", ".join(
                module_label(item) for item in selected_row.get("modulos") or []
            )
            st.caption(
                "Módulos no período: " + (module_names or "—")
            )
        st.caption("O histórico abaixo respeita o período e o módulo selecionados nos filtros.")
        events = AuditStore(store).user_timeline(selected, filters=filters)
        if events:
            st.dataframe(
                style_striped_table(access_event_rows(events), _theme_name()),
                hide_index=True,
                width="stretch",
                key="access_timeline_table",
            )
        else:
            empty_state("Nenhum evento do usuário corresponde aos filtros informados.")


def _event_details(record):
    details = details_dict(record)
    rows = [
        ("Data/hora", format_local(record.get("criado_em"))),
        ("Usuário", record.get("usuario_nome") or "—"),
        ("E-mail", record.get("usuario_email") or "—"),
        ("Função", details.get("perfil_ator") or "—"),
        (
            "Gabinete",
            ", ".join(details.get("gabinetes_ator") or []) or "—",
        ),
        ("Módulo", module_label(record.get("modulo"))),
        ("Ação", action_label(record.get("acao") or record.get("evento"))),
        ("Descrição", event_label(record.get("evento"))),
        ("Entidade", entity_label(record.get("entidade_tipo")) if record.get("entidade_tipo") else "—"),
        ("Identificador", record.get("entidade_id") or "—"),
        ("Resultado", result_label(record.get("resultado"))),
        ("Resumo", details.get("resumo") or resumo_humano(record)),
    ]
    changes = details.get("alteracoes")
    if isinstance(changes, list) and changes:
        rows.append(("Alterações", "; ".join(str(item) for item in changes)))
    technical = [
        ("Código interno", record.get("evento") or "—"),
        ("Sessão", record.get("sessao_id") or "—"),
        ("ID do evento", record.get("id")),
    ]
    for key, value in details.items():
        if key in (
            "resumo",
            "alteracoes",
            "perfil_ator",
            "gabinetes_ator",
            "titulo",
            "arquivo",
            "formato",
        ):
            continue
        if key in ("mensagem", "motivo", "tipo"):
            rows.append((key.replace("_", " ").capitalize(), value))
    definition_block("Detalhes", rows)
    definition_block("Informações técnicas", technical)


def _render_log(store, principal):
    audit = AuditStore(store)
    users = AccessStore(store).list_users()
    emails = {u["email"]: u["nome"] + " · " + u["email"] for u in users}
    filter_mark()
    section_label("Filtros")
    filters = _period_filter("log_") or {}
    a, b, c = st.columns(3)
    picked = a.selectbox(
        "Usuário",
        [None, *sorted(emails)],
        format_func=lambda e: emails.get(e, "Todos"),
        key="log_user",
    )
    modulo = b.selectbox(
        "Módulo",
        [None, *MODULE_LABELS],
        format_func=lambda m: module_label(m) if m else "Todos",
        key="log_modulo",
    )
    tipo = c.selectbox(
        "Tipo de ação",
        [item[0] for item in ACTION_TYPE_OPTIONS],
        format_func=lambda k: dict(ACTION_TYPE_OPTIONS)[k],
        key="log_tipo",
    )
    d, e, f = st.columns(3)
    resultado = d.selectbox(
        "Resultado",
        [item[0] for item in RESULT_OPTIONS],
        format_func=lambda k: dict(RESULT_OPTIONS)[k],
        key="log_resultado",
    )
    admin_only = e.checkbox("Somente ações administrativas", key="log_admin")
    query = f.text_input("Busca", key="log_q", placeholder="Nome, e-mail, documento…")
    if picked:
        filters["usuario_email"] = picked
    if modulo:
        filters["modulo"] = modulo
    if resultado:
        filters["resultado"] = resultado
    if admin_only:
        filters["somente_admin"] = True
    if query:
        filters["q"] = query.strip()[:80]
    if tipo:
        filters = apply_action_type(filters, tipo)
    signature = (
        filters.get("inicio"),
        filters.get("fim"),
        picked,
        modulo,
        tipo,
        resultado,
        admin_only,
        query,
    )
    if st.session_state.get("audit_log_sig") != signature:
        st.session_state["audit_log_sig"] = signature
        st.session_state["log_page"] = 1
    page = int(st.session_state.get("log_page") or 1)
    total = audit.count(filters)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        page = pages
        st.session_state["log_page"] = page
    offset = (page - 1) * PAGE_SIZE
    rows = audit.list_events(filters, limit=PAGE_SIZE, offset=offset)
    st.caption(
        f"{total} registro(s) · página {page} de {pages} · {PAGE_SIZE} por página"
    )
    if not rows:
        empty_state("Nenhum evento no período filtrado.")
    if rows:
        st.dataframe(
            style_event_table(rows, _theme_name()),
            hide_index=True,
            width="stretch",
            key="audit_events_table",
            column_config={
                "Data/hora": st.column_config.TextColumn("Data/hora", width="small"),
                "Usuário": st.column_config.TextColumn("Usuário", width="medium"),
                "Módulo": st.column_config.TextColumn("Módulo", width="small"),
                "Ação": st.column_config.TextColumn("Ação", width="small"),
                "Resultado": st.column_config.TextColumn("Resultado", width="small"),
                "Destaque": st.column_config.TextColumn("Destaque", width="small"),
                "Resumo": st.column_config.TextColumn("Resumo", width="large"),
            },
        )
        by_id = {row["id"]: row for row in rows}
        detail_key = "audit_detail_id"
        if st.session_state.get(detail_key) not in (None, *by_id):
            st.session_state.pop(detail_key, None)
        selected_id = st.selectbox(
            "Detalhes do evento",
            [None, *by_id],
            format_func=lambda identifier: (
                "Selecione um evento"
                if identifier is None
                else format_local_short(by_id[identifier]["criado_em"])
                + " · "
                + (by_id[identifier].get("usuario_nome") or "—")
                + " · "
                + resumo_humano(by_id[identifier])
            ),
            key=detail_key,
        )
        if selected_id is not None:
            _event_details(by_id[selected_id])
    nav_a, nav_b, nav_c = st.columns([1, 2, 1])
    nav_a.button(
        "Anterior",
        disabled=page <= 1,
        key="audit_prev",
        on_click=_set_log_page,
        args=(page - 1,),
    )
    nav_b.caption(f"Página {page} de {pages}")
    nav_c.button(
        "Próxima",
        disabled=page >= pages,
        key="audit_next",
        on_click=_set_log_page,
        args=(page + 1,),
    )
    csv_text, exported, truncated = export_csv(store, principal, filters)
    if truncated:
        st.warning(
            f"A exportação está limitada a {EXPORT_LIMIT} linhas. "
            f"Há {exported} registros no filtro; refine o período ou os critérios."
        )
    st.download_button(
        "Exportar auditoria (CSV)",
        csv_text.encode("utf-8-sig"),
        file_name="auditoria_mpc.csv",
        mime="text/csv",
        key="audit_export",
    )
    st.caption("Os registros são apenas acréscimo. Não há edição ou exclusão de logs nesta tela.")


def render(store, principal):
    require_permission(principal, "admin")
    st.subheader("Acessos e Auditoria")
    _render_kpis(store, principal)
    tab = st.radio(
        "Visão",
        ["Visão Geral", "Acessos", "Auditoria"],
        horizontal=True,
        key="audit_tab",
    )
    if tab == "Visão Geral":
        _render_overview(store, principal)
    elif tab == "Acessos":
        _render_accesses(store, principal)
    else:
        _render_log(store, principal)
