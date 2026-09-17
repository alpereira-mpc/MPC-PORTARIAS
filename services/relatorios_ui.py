"""Interface administrativa de Relatórios e Indicadores."""

import logging
from datetime import date, datetime

import streamlit as st

from database.tramita_reports import TramitaReportsStore
from services.access import require_permission
from services.audit import registrar_evento
from services.tramita_reports import file_hash, parse_movements, parse_stock
from services.ui_theme import filter_mark, kpi_mark, section_label


MONTHS = (
    "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
    "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro",
)
LOGGER = logging.getLogger(__name__)


def _format_date(value):
    if not value:
        return "—"
    return datetime.fromisoformat(value).strftime("%d/%m/%Y")


def _cached_report(store, principal, key, load):
    """Cache limitado de leitura da tela, sem reflexão sobre métodos do repositório."""
    import time

    tables = ("tramita_importacoes", "tramita_movimentacoes", "tramita_estoque")
    if store.backend == "postgresql":
        revision = store.read_cache_key(tables)
    else:
        from pathlib import Path

        wal = Path(str(store.path) + "-wal")
        try:
            wal_stat = wal.stat()
            wal_revision = (wal_stat.st_mtime_ns, wal_stat.st_size)
        except FileNotFoundError:
            wal_revision = None
        revision = (store.path.stat().st_mtime_ns, wal_revision)
    scope = (id(store), principal, revision)
    cache = st.session_state.get("_reports_read_cache")
    if not cache or cache["scope"] != scope:
        cache = {"scope": scope, "entries": {}}
        st.session_state["_reports_read_cache"] = cache
    key = repr(key)
    now = time.monotonic()
    entry = cache["entries"].get(key)
    if entry and now - entry[0] < 30:
        return entry[1]
    value = load()
    if len(cache["entries"]) >= 24:
        cache["entries"].pop(next(iter(cache["entries"])))
    cache["entries"][key] = (time.monotonic(), value)
    return value


def _page_offset(key, signature):
    if st.session_state.get(key + "_filters") != signature:
        st.session_state[key + "_filters"] = signature
        st.session_state[key] = 0
    return st.session_state.get(key, 0)


def _page_controls(key, offset, rows):
    def move(delta):
        st.session_state[key] = max(0, offset + delta)

    st.caption(f"Página {offset // 100 + 1} · até 100 registros")
    left, right = st.columns(2)
    left.button("Anterior", key=key + "_previous", disabled=offset == 0, on_click=move, args=(-100,))
    right.button("Próxima", key=key + "_next", disabled=len(rows) <= 100, on_click=move, args=(100,))


def production(store, principal=None):
    reports = TramitaReportsStore(store)
    read = lambda key, load: _cached_report(reports.store, principal, key, load)
    options = read(("competences",), reports.competences)
    if not options:
        st.info("Nenhuma competência processual foi importada. Utilize a área Importações para carregar os relatórios do Tramita.")
        return
    with st.container(border=True):
        filter_mark()
        competence = st.selectbox("Competência", options, format_func=lambda value: f"{value[5:7]}/{value[:4]}")
    summary = read(("production_summary", competence), lambda: reports.production_summary(competence))
    totals = {key: sum(row[key] for row in summary.values()) for key in ("entries", "exits", "opinions", "quotas", "days", "timed")}
    average = totals["days"] / totals["timed"] if totals["timed"] else None
    section_label("Indicadores")
    for col, label, value in zip(st.columns(5), ("Entradas", "Saídas", "Pareceres", "Cotas", "Tempo médio até devolução"),
                                (totals["entries"], totals["exits"], totals["opinions"], totals["quotas"], f"{average:.1f} dias" if average is not None else "—")):
        with col:
            kpi_mark("brand")
            st.metric(label, value)
    selected = st.selectbox("Filtrar por procurador", ["Todos", *sorted(name for name in summary if name)], key="rel_prod_procurador")
    person = None if selected == "Todos" else selected
    grouped = []
    for name, row in sorted(summary.items()):
        if name and (person is None or name == person):
            grouped.append({"Procurador": name, "Entradas": row["entries"], "Saídas": row["exits"],
                            "Pareceres": row["opinions"], "Cotas": row["quotas"],
                            "Tempo médio até devolução": round(row["days"] / row["timed"], 1) if row["timed"] else None})
    st.subheader("Comparativo por Procurador")
    st.dataframe(grouped, hide_index=True, use_container_width=True)
    if grouped:
        st.bar_chart(grouped, x="Procurador", y=["Entradas", "Saídas"])
    facets = read(("movement_facets", competence, person), lambda: reports.movement_facets(competence, person))
    by_field = {field: [row for row in facets if row["field"] == field and row["value"]] for field in ("subcategoria", "origem")}
    st.subheader("Naturezas e jurisdicionados")
    for col, field, label in zip(st.columns(2), ("subcategoria", "origem"), ("Natureza", "Jurisdicionado/Origem")):
        col.dataframe([{label: row["value"], "Quantidade": row["n"]} for row in sorted(by_field[field], key=lambda row: (-row["n"], row["value"]))[:10]], hide_index=True, use_container_width=True)
    st.subheader("Detalhamento mensal")
    a, b, c = st.columns(3)
    natureza = a.selectbox("Natureza", ["Todas", *sorted(row["value"] for row in by_field["subcategoria"])])
    origem = b.selectbox("Jurisdicionado", ["Todos", *sorted(row["value"] for row in by_field["origem"])])
    kind = c.selectbox("Tipo", ["Todos", "Entrada", "Saída"])
    filters = {"procurador": person, "subcategoria": None if natureza == "Todas" else natureza,
               "origem": None if origem == "Todos" else origem,
               "tipo_movimentacao": {"Todos": None, "Entrada": "ENTRADA", "Saída": "SAIDA"}[kind]}
    offset = _page_offset("rel_prod_page", (competence, repr(filters)))
    rows = read(("movement_page", competence, filters, offset), lambda: reports.movement_page(competence, filters, offset))
    st.dataframe([{"Protocolo": row["protocolo"], "Natureza": row["subcategoria"], "Jurisdicionado": row["origem"],
                   "Procurador": row["procurador"], "Entrada": _format_date(row["data_realizacao"]) if row["tipo_movimentacao"] == "ENTRADA" else "",
                   "Saída": _format_date(row["data_devolucao"]) if row["tipo_movimentacao"] == "SAIDA" else "",
                   "Resultado": row["motivo_devolucao"]} for row in rows[:100]], hide_index=True, use_container_width=True)
    _page_controls("rel_prod_page", offset, rows)


def current_view(store, principal=None):
    reports = TramitaReportsStore(store)
    read = lambda key, load: _cached_report(reports.store, principal, key, load)
    snapshot = read(("latest_snapshot",), reports.latest_snapshot)
    if not snapshot:
        st.info("Nenhuma fotografia atual do estoque processual foi importada.")
        return
    summary = read(("stock_summary", snapshot), lambda: reports.stock_summary(snapshot))
    people = sorted(row["procurador"] for row in summary if row["procurador"])
    days = sum(float(row["days"] or 0) for row in summary)
    timed = sum(row["timed"] for row in summary)
    st.caption("Dados atualizados em " + datetime.fromisoformat(snapshot).strftime("%d/%m/%Y"))
    values = (sum(row["n"] for row in summary), len(people), f"{days / timed:.1f} dias" if timed else "—", sum(row["over30"] for row in summary))
    section_label("Indicadores")
    for col, label, value in zip(st.columns(4), ("Processos atualmente no MPC-PB", "Procuradores com processos", "Tempo médio com procurador", "Processos há mais de 30 dias"), values):
        with col:
            kpi_mark("brand")
            st.metric(label, value)
    selected = st.selectbox("Procurador", ["Todos", *people], key="rel_stock_procurador")
    st.subheader("Estoque por Procurador")
    st.dataframe([{"Procurador": row["procurador"], "Processos atualmente distribuídos": row["n"],
                   "Tempo médio com procurador": round(float(row["days"]) / row["timed"], 1) if row["timed"] else None,
                   "Maior permanência atual": row["maximum"], "+30 dias": row["over30"],
                   "+60 dias": row["over60"], "+90 dias": row["over90"]}
                  for row in sorted(summary, key=lambda row: row["procurador"]) if row["procurador"]], hide_index=True, use_container_width=True)
    fields = (("natureza", "Natureza", "subcategoria"), ("jurisdicionado", "Jurisdicionado", "jurisdicionado"),
              ("fase", "Fase", "fase"), ("assistente", "Assistente", "assistente"))
    filters = {"procurador": None if selected == "Todos" else selected}
    filters.update({field: None if st.session_state.get("stock_" + key, "Todos") == "Todos" else st.session_state["stock_" + key] for key, _, field in fields})
    # Normalize obsolete downstream choices before rendering their widgets.
    # At most one corrective query per changed ancestor; cached on normal reruns.
    facets = read(("stock_options", snapshot, filters), lambda: reports.stock_options(snapshot, filters))
    columns = st.columns(5)
    for col, (key, label, field) in zip(columns, fields):
        choices = ["Todos", *sorted(row["value"] for row in facets if row["field"] == field)]
        if st.session_state.get("stock_" + key, "Todos") not in choices:
            st.session_state["stock_" + key] = "Todos"
            filters[field] = None
            facets = read(("stock_options", snapshot, filters), lambda: reports.stock_options(snapshot, filters))
        choice = col.selectbox(label, choices, key="stock_" + key)
        filters[field] = None if choice == "Todos" else choice
    band = columns[4].selectbox("Faixa de dias", ["Todas", "0–7 dias", "8–15 dias", "16–30 dias", "31–60 dias", "61–90 dias", "Mais de 90 dias"])
    offset = _page_offset("rel_stock_page", (snapshot, repr(filters), band))
    bands, rows = read(
        ("stock_details", snapshot, filters, None if band == "Todas" else band, offset),
        lambda: reports.stock_details(snapshot, filters, None if band == "Todas" else band, offset),
    )
    st.subheader("Faixas de permanência")
    st.dataframe([{"Faixa": row["faixa"], "Processos": row["n"]} for row in bands if row["faixa"]], hide_index=True, use_container_width=True)
    st.subheader("Processos há mais tempo com o Procurador")
    st.dataframe([{"Protocolo": row["protocolo"], "Procurador": row["procurador"], "Natureza": row["subcategoria"],
                   "Jurisdicionado": row["jurisdicionado"], "Fase": row["fase"], "Dias com Procurador": row["dias_com_procurador"],
                   "Dias no MPC-PB": row["dias_no_mpc"], "Assistente": row["assistente"], "Prescrição": row["prescricao"]}
                  for row in rows[:100]], hide_index=True, use_container_width=True)
    _page_controls("rel_stock_page", offset, rows)


def _uploaded_preview(kind, uploaded, principal):
    content = uploaded.getvalue()
    digest = file_hash(content)
    scope = (principal.id, principal.email)
    cache = st.session_state.get("_tramita_previews")
    if not cache or cache["scope"] != scope:
        cache = {"scope": scope, "files": {}}
        st.session_state["_tramita_previews"] = cache
    entry = cache["files"].get(kind)
    if not entry or entry[0] != digest:
        parser = parse_stock if kind == "ESTOQUE" else parse_movements
        entry = (digest, parser(content))
        cache["files"][kind] = entry
    return entry[1]


def imports(store, principal):
    if not principal.administrator:
        raise ValueError("Apenas administradores podem importar dados do Tramita.")
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
            rows, unknown = _uploaded_preview(kind, uploaded, principal)
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
            st.session_state.pop("_reports_read_cache", None)
            st.success("Produção mensal importada.")
            st.rerun()
    st.divider(); st.subheader("Importar Estoque Atual")
    snapshot = st.date_input("Data da fotografia", value=date.today(), format="DD/MM/YYYY")
    uploaded = st.file_uploader("Arquivo de processos atuais", type=["xls"], key="tramita_stock")
    if uploaded:
        try:
            rows, unknown = _uploaded_preview("ESTOQUE", uploaded, principal)
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
            st.session_state.pop("_reports_read_cache", None)
            st.success("Estoque atual importado."); st.rerun()


def render(store, principal):
    require_permission(principal, "relatorios")
    st.subheader("RELATÓRIOS E INDICADORES")
    st.caption("Acompanhamento da movimentação e do estoque processual do MPC-PB")
    sections = ("Produção Mensal", "Visão Atual")
    if principal.administrator:
        sections += ("Importações",)
    section = st.radio("Seção", sections, horizontal=True)
    if section != "Importações":
        st.session_state.pop("_tramita_previews", None)
    if section == "Produção Mensal":
        _render_indicators("produção mensal", production, store, principal)
    elif section == "Visão Atual":
        _render_indicators("visão atual", current_view, store, principal)
    else: imports(store, principal)


def _render_indicators(name, renderer, store, principal):
    try:
        renderer(store, principal)
    except Exception:
        LOGGER.exception("Falha ao carregar indicadores de %s", name)
        st.error("Não foi possível carregar os indicadores neste momento. Tente novamente mais tarde.")
