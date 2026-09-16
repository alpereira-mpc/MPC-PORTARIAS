"""Interface administrativa de Relatórios e Indicadores."""

from collections import Counter, defaultdict
from datetime import date, datetime

import streamlit as st

from database.tramita_reports import TramitaReportsStore
from services.access import require_permission
from services.audit import registrar_evento
from services.tramita_reports import aging_band, file_hash, is_result, parse_movements, parse_stock, turnaround_days


MONTHS = (
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
)


def _format_date(value):
    if not value:
        return "—"
    return datetime.fromisoformat(value).strftime("%d/%m/%Y")


def _read_rows(store, sql, values=()):
    with store.connection(read_only=True) as c:
        return [dict(row) for row in c.execute(sql, values)]


def _movements(store, competence):
    return _read_rows(store, "SELECT * FROM tramita_movimentacoes WHERE competencia=? ORDER BY protocolo", (competence,))


def _kpis(rows):
    entries = [row for row in rows if row["tipo_movimentacao"] == "ENTRADA"]
    exits = [row for row in rows if row["tipo_movimentacao"] == "SAIDA"]
    turns = [turnaround_days(row) for row in exits]
    turns = [value for value in turns if value is not None]
    return len(entries), len(exits), sum(is_result(row["motivo_devolucao"], "Analisado Com Parecer") for row in exits), sum(is_result(row["motivo_devolucao"], "Analisado Com Cota") for row in exits), sum(turns) / len(turns) if turns else None


def production(store):
    reports = TramitaReportsStore(store)
    options = reports.competences()
    if not options:
        st.info("Nenhuma competência processual foi importada. Utilize a área Importações para carregar os relatórios do Tramita.")
        return
    competence = st.selectbox("Competência", options, format_func=lambda value: f"{value[5:7]}/{value[:4]}")
    rows = _movements(store, competence)
    entries, exits, opinions, quotas, average = _kpis(rows)
    cols = st.columns(5)
    for col, label, value in zip(cols, ("Entradas", "Saídas", "Pareceres", "Cotas", "Tempo médio até devolução"), (entries, exits, opinions, quotas, f"{average:.1f} dias" if average is not None else "—")):
        col.metric(label, value)
    procuradores = sorted({row["procurador"] for row in rows if row["procurador"]})
    selected = st.selectbox("Filtrar por procurador", ["Todos", *procuradores], key="rel_prod_procurador")
    filtered = [row for row in rows if selected == "Todos" or row["procurador"] == selected]
    grouped = []
    for name in procuradores:
        subset = [row for row in filtered if row["procurador"] == name]
        if not subset:
            continue
        e, s, p, c, t = _kpis(subset)
        grouped.append({"Procurador": name, "Entradas": e, "Saídas": s, "Pareceres": p, "Cotas": c, "Tempo médio até devolução": round(t, 1) if t is not None else None})
    st.subheader("Comparativo por Procurador")
    st.dataframe(grouped, hide_index=True, use_container_width=True)
    if grouped:
        st.bar_chart(grouped, x="Procurador", y=["Entradas", "Saídas"])
    st.subheader("Naturezas e jurisdicionados")
    left, right = st.columns(2)
    left.dataframe([{"Natureza": name, "Quantidade": count} for name, count in Counter(row["subcategoria"] for row in filtered if row["subcategoria"]).most_common(10)], hide_index=True, use_container_width=True)
    right.dataframe([{"Jurisdicionado/Origem": name, "Quantidade": count} for name, count in Counter(row["origem"] for row in filtered if row["origem"]).most_common(10)], hide_index=True, use_container_width=True)
    st.subheader("Detalhamento mensal")
    naturezas = sorted({row["subcategoria"] for row in filtered if row["subcategoria"]})
    origens = sorted({row["origem"] for row in filtered if row["origem"]})
    a, b, c = st.columns(3)
    natureza = a.selectbox("Natureza", ["Todas", *naturezas])
    origem = b.selectbox("Jurisdicionado", ["Todos", *origens])
    kind = c.selectbox("Tipo", ["Todos", "Entrada", "Saída"])
    filtered = [row for row in filtered if (natureza == "Todas" or row["subcategoria"] == natureza) and (origem == "Todos" or row["origem"] == origem) and (kind == "Todos" or row["tipo_movimentacao"] == kind.upper())]
    st.dataframe([{"Protocolo": row["protocolo"], "Natureza": row["subcategoria"], "Jurisdicionado": row["origem"], "Procurador": row["procurador"], "Entrada": _format_date(row["data_realizacao"]) if row["tipo_movimentacao"] == "ENTRADA" else "", "Saída": _format_date(row["data_devolucao"]) if row["tipo_movimentacao"] == "SAIDA" else "", "Resultado": row["motivo_devolucao"]} for row in filtered], hide_index=True, use_container_width=True)


def current_view(store):
    reports = TramitaReportsStore(store)
    snapshot = reports.latest_snapshot()
    if not snapshot:
        st.info("Nenhuma fotografia atual do estoque processual foi importada.")
        return
    rows = _read_rows(store, "SELECT * FROM tramita_estoque WHERE data_snapshot=?", (snapshot,))
    procuradores = sorted({row["procurador"] for row in rows if row["procurador"]})
    days = [row["dias_com_procurador"] for row in rows if row["dias_com_procurador"] is not None]
    st.caption("Dados atualizados em " + datetime.fromisoformat(snapshot).strftime("%d/%m/%Y"))
    cols = st.columns(4)
    values = (len(rows), len(procuradores), f"{sum(days) / len(days):.1f} dias" if days else "—", sum(value > 30 for value in days))
    for col, label, value in zip(cols, ("Processos atualmente no MPC-PB", "Procuradores com processos", "Tempo médio com procurador", "Processos há mais de 30 dias"), values): col.metric(label, value)
    selected = st.selectbox("Procurador", ["Todos", *procuradores], key="rel_stock_procurador")
    filtered = [row for row in rows if selected == "Todos" or row["procurador"] == selected]
    st.subheader("Estoque por Procurador")
    overview = []
    for name in procuradores:
        values = [row["dias_com_procurador"] for row in rows if row["procurador"] == name and row["dias_com_procurador"] is not None]
        overview.append({"Procurador": name, "Processos atualmente distribuídos": sum(row["procurador"] == name for row in rows), "Tempo médio com procurador": round(sum(values) / len(values), 1) if values else None, "Maior permanência atual": max(values) if values else None, "+30 dias": sum(value > 30 for value in values), "+60 dias": sum(value > 60 for value in values), "+90 dias": sum(value > 90 for value in values)})
    st.dataframe(overview, hide_index=True, use_container_width=True)
    filters = st.columns(5)
    for key, label, field in (("natureza", "Natureza", "subcategoria"), ("jurisdicionado", "Jurisdicionado", "jurisdicionado"), ("fase", "Fase", "fase"), ("assistente", "Assistente", "assistente")):
        options = sorted({row[field] for row in filtered if row[field]})
        choice = filters[["natureza", "jurisdicionado", "fase", "assistente"].index(key)].selectbox(label, ["Todos", *options], key="stock_" + key)
        filtered = [row for row in filtered if choice == "Todos" or row[field] == choice]
    band = filters[4].selectbox("Faixa de dias", ["Todas", "0–7 dias", "8–15 dias", "16–30 dias", "31–60 dias", "61–90 dias", "Mais de 90 dias"])
    filtered = [row for row in filtered if band == "Todas" or aging_band(row["dias_com_procurador"]) == band]
    st.subheader("Faixas de permanência")
    st.dataframe([{"Faixa": name, "Processos": count} for name, count in Counter(aging_band(row["dias_com_procurador"]) for row in filtered).items() if name], hide_index=True, use_container_width=True)
    st.subheader("Processos há mais tempo com o Procurador")
    st.dataframe([{"Protocolo": row["protocolo"], "Procurador": row["procurador"], "Natureza": row["subcategoria"], "Jurisdicionado": row["jurisdicionado"], "Fase": row["fase"], "Dias com Procurador": row["dias_com_procurador"], "Dias no MPC-PB": row["dias_no_mpc"], "Assistente": row["assistente"], "Prescrição": row["prescricao"]} for row in sorted(filtered, key=lambda row: row["dias_com_procurador"] or -1, reverse=True)], hide_index=True, use_container_width=True)


def imports(store, principal):
    reports = TramitaReportsStore(store)
    st.subheader("Importar Produção Mensal")
    st.caption("Competência")
    month_column, year_column = st.columns(2)
    today = date.today()
    month_name = month_column.selectbox("Mês", MONTHS, index=today.month - 1)
    year = year_column.selectbox("Ano", list(range(2020, today.year + 2)), index=today.year - 2020)
    competence = f"{year}-{MONTHS.index(month_name) + 1:02d}"
    competence_display = f"{month_name}/{year}"
    first, second = st.columns(2)
    incoming = first.file_uploader("Relatório de Entradas", type=["xls"], key="tramita_entries")
    outgoing = second.file_uploader("Relatório de Saídas", type=["xls"], key="tramita_exits")
    previews = []
    for kind, uploaded in (("ENTRADAS", incoming), ("SAIDAS", outgoing)):
        if uploaded:
            rows, unknown = parse_movements(uploaded.getvalue())
            previews.append((kind, uploaded, rows, unknown))
    if previews:
        st.write(" · ".join(f"{kind.title()}: {len(rows)}" for kind, _, rows, _ in previews))
        st.caption(f"Procuradores identificados: {len({row['procurador'] for _, _, rows, _ in previews for row in rows})} · Competência: {competence_display}")
        unknown = sorted({name for _, _, _, names in previews for name in names})
        if unknown: st.error("Procurador não reconhecido: " + ", ".join(unknown))
        repeated = [uploaded.name for _, uploaded, _, _ in previews if reports.imported_hash(file_hash(uploaded.getvalue()))]
        if repeated: st.error("Este arquivo já foi importado anteriormente: " + ", ".join(repeated))
        if st.button("Confirmar importação de produção", disabled=bool(unknown) or bool(repeated) or len(previews) != 2):
            for kind, uploaded, rows, _ in previews:
                reports.import_rows(kind=kind, file_name=uploaded.name, file_hash=file_hash(uploaded.getvalue()), actor=principal.email, competence=competence, rows=rows)
                registrar_evento(store, evento="TRAMITA_" + kind + "_IMPORTADAS", modulo="relatorios", acao="IMPORTAR", principal=principal, detalhes={"competencia": competence, "arquivo": uploaded.name, "quantidade": len(rows), "hash": file_hash(uploaded.getvalue())})
            st.success("Produção mensal importada.")
            st.rerun()
    st.divider(); st.subheader("Importar Estoque Atual")
    snapshot = st.date_input("Data da fotografia", value=date.today(), format="DD/MM/YYYY")
    uploaded = st.file_uploader("Arquivo de processos atuais", type=["xls"], key="tramita_stock")
    if uploaded:
        try:
            rows, unknown = parse_stock(uploaded.getvalue())
        except ValueError as exc:
            st.error(str(exc))
            return
        st.write(f"Processos identificados: {len(rows)} · Procuradores identificados: {len({row['procurador'] for row in rows})}")
        if unknown: st.error("Procurador não reconhecido: " + ", ".join(unknown))
        repeated = reports.imported_hash(file_hash(uploaded.getvalue()))
        if repeated: st.error("Este arquivo já foi importado anteriormente.")
        if st.button("Confirmar importação do estoque", disabled=bool(unknown) or repeated):
            reports.import_rows(kind="ESTOQUE", file_name=uploaded.name, file_hash=file_hash(uploaded.getvalue()), actor=principal.email, snapshot_date=snapshot.isoformat(), rows=rows)
            registrar_evento(store, evento="TRAMITA_ESTOQUE_IMPORTADO", modulo="relatorios", acao="IMPORTAR", principal=principal, detalhes={"data_snapshot": snapshot.isoformat(), "arquivo": uploaded.name, "quantidade": len(rows), "hash": file_hash(uploaded.getvalue())})
            st.success("Estoque atual importado."); st.rerun()


def render(store, principal):
    require_permission(principal, "relatorios")
    st.subheader("RELATÓRIOS E INDICADORES")
    st.caption("Acompanhamento da movimentação e do estoque processual do MPC-PB")
    section = st.radio("Seção", ("Produção Mensal", "Visão Atual", "Importações"), horizontal=True)
    if section == "Produção Mensal": production(store)
    elif section == "Visão Atual": current_view(store)
    else: imports(store, principal)
