"""Administration screens for access analytics and append-only audit logs."""

from datetime import date
import streamlit as st
from database.access import AccessStore
from database.audit import PAGE_SIZE, EXPORT_LIMIT, AuditStore
from services.access import require_permission
from services.audit import (
    MODULE_LABELS,
    details_dict,
    export_csv,
    format_local,
    overview,
    period_bounds,
    user_overview,
)
from services.ui_theme import section_label


PERIODS = (
    ("hoje", "Hoje"),
    ("7d", "7 dias"),
    ("30d", "30 dias"),
    ("personalizado", "Personalizado"),
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


def _module_label(code):
    return MODULE_LABELS.get(code, code or "—")


def _render_overview(store, principal):
    data = overview(store, principal)
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
    d.write(_module_label(used["modulo"]) if used else "—")
    rows = user_overview(store, principal, {"inicio": period_bounds("30d")[0]})
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
        format_func=lambda m: _module_label(m) if m else "Todos",
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
        st.info("Nenhum acesso no período filtrado.")
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
                "Módulos": ", ".join(_module_label(m) for m in r.get("modulos") or []) or "—",
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
        st.dataframe(
            [
                {
                    "Data/hora": format_local(e["criado_em"]),
                    "Módulo": _module_label(e["modulo"]),
                    "Evento": e["evento"],
                    "Ação": e["acao"],
                    "Resultado": e["resultado"],
                    "Entidade": " ".join(
                        p for p in (e.get("entidade_tipo"), e.get("entidade_id")) if p
                    )
                    or "—",
                }
                for e in events
            ],
            hide_index=True,
            use_container_width=True,
        )


def _render_log(store, principal):
    audit = AuditStore(store)
    filters = _period_filter("log_") or {}
    a, b, c = st.columns(3)
    users = audit.distinct_values("usuario_email")
    eventos = audit.distinct_values("evento")
    resultados = audit.distinct_values("resultado")
    picked = a.selectbox(
        "Usuário",
        [None, *users],
        format_func=lambda e: e or "Todos",
        key="log_user",
    )
    modulo = b.selectbox(
        "Módulo",
        [None, *MODULE_LABELS],
        format_func=lambda m: _module_label(m) if m else "Todos",
        key="log_modulo",
    )
    evento = c.selectbox(
        "Evento",
        [None, *eventos],
        format_func=lambda e: e or "Todos",
        key="log_evento",
    )
    d, e, f = st.columns(3)
    resultado = d.selectbox(
        "Resultado",
        [None, *resultados],
        format_func=lambda r: r or "Todos",
        key="log_resultado",
    )
    admin_only = e.checkbox("Somente ações administrativas", key="log_admin")
    page = int(
        f.number_input("Página", min_value=1, value=1, step=1, key="log_page")
    )
    if picked:
        filters["usuario_email"] = picked
    if modulo:
        filters["modulo"] = modulo
    if evento:
        filters["evento"] = evento
    if resultado:
        filters["resultado"] = resultado
    if admin_only:
        filters["somente_admin"] = True
    total = audit.count(filters)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > pages:
        page = pages
    offset = (page - 1) * PAGE_SIZE
    rows = audit.list_events(filters, limit=PAGE_SIZE, offset=offset)
    st.caption(f"{total} registro(s) · página {page} de {pages} · {PAGE_SIZE} por página")
    st.dataframe(
        [
            {
                "Data/hora": format_local(r["criado_em"]),
                "Usuário": (r.get("usuario_nome") or "—")
                + ((" · " + r["usuario_email"]) if r.get("usuario_email") else ""),
                "Módulo": _module_label(r["modulo"]),
                "Ação": r.get("acao") or r.get("evento"),
                "Resultado": r.get("resultado") or "—",
                "Entidade": " ".join(
                    p for p in (r.get("entidade_tipo"), r.get("entidade_id")) if p
                )
                or "—",
            }
            for r in rows
        ],
        hide_index=True,
        use_container_width=True,
    )
    if rows:
        choices = {r["id"]: format_local(r["criado_em"]) + " · " + r["evento"] for r in rows}
        opened = st.selectbox(
            "Abrir registro",
            [None, *choices],
            format_func=lambda i: choices.get(i, "Selecione"),
            key="log_open",
        )
        if opened:
            record = audit.get(opened)
            if record:
                st.json(
                    {
                        "data_hora": format_local(record["criado_em"]),
                        "usuario": record.get("usuario_email"),
                        "nome": record.get("usuario_nome"),
                        "sessao": record.get("sessao_id"),
                        "evento": record.get("evento"),
                        "modulo": record.get("modulo"),
                        "acao": record.get("acao"),
                        "resultado": record.get("resultado"),
                        "entidade_tipo": record.get("entidade_tipo"),
                        "entidade_id": record.get("entidade_id"),
                        "detalhes": details_dict(record),
                    }
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
