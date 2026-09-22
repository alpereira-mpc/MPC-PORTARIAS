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
    objeto_humano,
    overview,
    period_bounds,
    result_label,
    resumo_humano,
    user_overview,
)
from services.ui_theme import (
    badges,
    card_container,
    definition_block,
    empty_state,
    filter_mark,
    kpi_mark,
    render_record,
    section_label,
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


def _render_kpis(store, principal):
    data = dashboard_hoje(store, principal)
    items = (
        ("Atividades hoje", data["atividades"], "brand"),
        ("Usuários ativos", data["usuarios_ativos"], "info"),
        ("Alterações administrativas", data["admin"], "warning"),
        ("Downloads de documentos", data["downloads"], "success"),
        ("Falhas/erros registrados", data["falhas"], "danger" if data["falhas"] else "muted"),
    )
    columns = st.columns(len(items))
    for column, (title, value, tone) in zip(columns, items):
        with column:
            kpi_mark(tone)
            st.metric(title, value)


def _render_overview(store, principal):
    # The page-level KPI strip already loaded today's dashboard.
    data = overview(store, principal, include_dashboard=False)
    st.caption("Indicadores calculados no fuso institucional America/Recife. Logs em UTC.")
    section_label("Indicadores")
    today, week, month = st.columns(3)
    with today:
        st.markdown("**Hoje**")
        st.metric("Usuários que acessaram", data["hoje"]["usuarios"])
        st.metric("Sessões", data["hoje"]["sessoes"])
        st.metric("Ações registradas", data["hoje"]["acoes"])
    with week:
        st.markdown("**Últimos 7 dias**")
        st.metric("Usuários distintos", data["semana"]["usuarios"])
        st.metric("Acessos", data["semana"]["sessoes"])
        st.metric("Ações", data["semana"]["acoes"])
    with month:
        st.markdown("**Últimos 30 dias**")
        st.metric("Usuários distintos", data["mes"]["usuarios"])
        st.metric("Acessos", data["mes"]["sessoes"])
        st.metric("Ações", data["mes"]["acoes"])
    latest = data["ultimo_acesso"]
    used = data["modulo_mais_usado"]
    a, b, c, d = st.columns(4)
    a.metric("Usuários ativos cadastrados", data["usuarios_ativos"])
    b.write("**Usuário mais recente**")
    b.write(latest["nome"] if latest else "—")
    b.caption(latest["email"] if latest else "")
    c.write("**Último acesso ao sistema**")
    c.write(format_local(latest["criado_em"]) if latest else "—")
    d.write("**Módulo mais utilizado (30 dias)**")
    d.write(module_label(used["modulo"]) if used else "—")
    rows = user_overview(
        store,
        principal,
        {"inicio": period_bounds("30d")[0]},
        users=data["usuarios"],
    )
    st.subheader("Usuários")
    st.dataframe(
        [
            {
                "Usuário": f"{r['nome']} ({r['email']})" if r["email"] else r["nome"],
                "Último acesso": format_local(r.get("ultimo_acesso")),
                "Acessos": r.get("sessoes") or 0,
                "Última atividade": format_local(r.get("ultima_atividade")),
            }
            for r in rows
        ],
        hide_index=True,
        use_container_width=True,
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
        [
            {
                "Nome": r["nome"],
                "E-mail": r["email"],
                "Perfil": r.get("perfil") or "—",
                "Ativo": "Sim" if r.get("ativo") else ("Não" if r.get("ativo") is False else "—"),
                "Primeiro acesso": format_local(r.get("primeiro_acesso")),
                "Último acesso": format_local(r.get("ultimo_acesso")),
                "Sessões": r.get("sessoes") or 0,
                "Dias distintos": r.get("dias_distintos") or 0,
                "Última atividade": format_local(r.get("ultima_atividade")),
                "Módulos": ", ".join(module_label(m) for m in r.get("modulos") or []) or "—",
            }
            for r in rows
        ],
        hide_index=True,
        use_container_width=True,
    )
    options = {r["email"]: r["nome"] + " · " + r["email"] for r in rows if r.get("email")}
    selected = st.selectbox(
        "Histórico do usuário",
        [None, *options],
        format_func=lambda e: options.get(e, "Selecione"),
        key="acc_timeline",
    )
    if selected:
        events = AuditStore(store).user_timeline(selected)
        for index, event in enumerate(events):
            with card_container(index, f"acc_tl_{event['id']}"):
                render_record(
                    format_local_short(event["criado_em"]),
                    badges_html=badges(
                        (result_label(event.get("resultado")), _result_tone(event.get("resultado"))),
                        (action_label(event.get("acao") or event.get("evento")), "brand"),
                    ),
                    secondary=(event.get("usuario_nome") or "—")
                    + " · "
                    + resumo_humano(event),
                    meta=module_label(event.get("modulo"))
                    + " · "
                    + objeto_humano(event),
                )


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
    for index, row in enumerate(rows):
        with card_container(index, f"audit_{row['id']}"):
            render_record(
                format_local_short(row["criado_em"]),
                badges_html=badges(
                    (result_label(row.get("resultado")), _result_tone(row.get("resultado"))),
                    (action_label(row.get("acao") or row.get("evento")), "brand"),
                ),
                secondary=(row.get("usuario_nome") or "—") + " · " + resumo_humano(row),
                meta=" · ".join(
                    part
                    for part in (
                        module_label(row.get("modulo")),
                        objeto_humano(row),
                    )
                    if part and part != "—"
                ),
                accent=_result_tone(row.get("resultado")),
            )
            with st.expander("Detalhes"):
                _event_details(row)
    nav_a, nav_b, nav_c = st.columns([1, 2, 1])
    if nav_a.button("Anterior", disabled=page <= 1, key="audit_prev"):
        st.session_state["log_page"] = page - 1
        st.rerun()
    nav_b.caption(f"Página {page} de {pages}")
    if nav_c.button("Próxima", disabled=page >= pages, key="audit_next"):
        st.session_state["log_page"] = page + 1
        st.rerun()
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
