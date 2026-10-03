"""Interface administrativa de Relatórios e Indicadores."""

import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from database.tramita_reports import TramitaReportsStore
from services.access import require_permission
from services.audit import registrar_evento
from services.date_format import format_date_br
from services.themes import theme_tokens
from services.tramita_reports import (
    file_hash,
    import_reference_reports,
    parse_movements,
    parse_stock,
)
from services.ui_theme import empty_state, filter_mark, kpi_mark, section_label


MONTHS = (
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)
LOGGER = logging.getLogger(__name__)
DAY_COLUMNS = (
    "Tempo médio até devolução",
    "Tempo médio com procurador",
    "Maior permanência atual",
    "Mediana de permanência em dias",
)


def style_report_table(rows, theme_name="vermelho"):
    """Apply the active theme to report data without changing its values."""
    palette = theme_tokens(theme_name)
    frame = pd.DataFrame(rows)
    styler = frame.style.apply(
        lambda row: [
            (
                f"background-color:{palette['themed_table_bg']};"
                if row.name % 2 == 0
                else f"background-color:{palette['themed_table_stripe_bg']};"
            )
            + f"color:{palette['themed_table_fg']};"
            + f"border-bottom:1px solid {palette['themed_table_border']}"
        ]
        * len(row),
        axis=1,
    )
    day_formatters = {
        column: "{:.1f}" for column in DAY_COLUMNS if column in frame.columns
    }
    if "Produção/Distribuições" in frame.columns:
        day_formatters["Produção/Distribuições"] = "{:.1f}%"
    if day_formatters:
        # Styler changes presentation only: dataframe values stay numeric for
        # Streamlit sorting and any future table interactions.
        styler = styler.format(day_formatters)
    return styler.set_table_styles(
        [
            {
                "selector": "th.col_heading",
                "props": [
                    ("background-color", palette["themed_table_header_bg"]),
                    ("color", palette["themed_table_header_fg"]),
                    ("font-weight", "700"),
                    ("border-bottom", f"1px solid {palette['themed_table_border']}"),
                ],
            }
        ]
    )


def _report_table(target, rows):
    selected_theme = st.session_state.get("_portal_theme", {}).get("name", "vermelho")
    target.dataframe(
        style_report_table(rows, selected_theme),
        hide_index=True,
        use_container_width=True,
    )


def _format_date(value):
    return format_date_br(value)


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
    left.button(
        "Anterior",
        key=key + "_previous",
        disabled=offset == 0,
        on_click=move,
        args=(-100,),
    )
    right.button(
        "Próxima",
        key=key + "_next",
        disabled=len(rows) <= 100,
        on_click=move,
        args=(100,),
    )


def _number(value, suffix=""):
    return "—" if value is None else f"{value:.1f}{suffix}"


def _period_delta(current, previous, field):
    if previous is None or current[field] is None or previous[field] is None:
        return None
    if field == "production_rate":
        return f"{current[field] - previous[field]:+.1f} p.p."
    if field == "median_days":
        return f"{current[field] - previous[field]:+.1f} dias"
    return f"{current[field] - previous[field]:+d}"


def _indicator_cards(summary, previous=None):
    help_text = {
        "distributed": "Quantidade de eventos de distribuição registrados no Tramita no período. Um mesmo protocolo pode possuir mais de uma distribuição.",
        "production": "Devoluções produtivas registradas no período com Parecer ou Cota.",
        "production_rate": "Relação entre devoluções produtivas e distribuições registradas no mesmo período. Não representa percentual de processos concluídos nem variação direta do estoque.",
        "median_days": "Intervalo mediano entre a distribuição registrada e a devolução produtiva. Movimentações intermediárias podem influenciar esse período.",
    }
    section_label("Indicadores")
    values = (
        ("Distribuídos", summary["distributed"], "distributed"),
        ("Produção", summary["production"], "production"),
        ("Pareceres", summary["opinions"], "opinions"),
        ("Cotas", summary["quotas"], "quotas"),
        (
            "Produção/Distribuições",
            _number(summary["production_rate"], "%"),
            "production_rate",
        ),
        (
            "Mediana de permanência",
            _number(summary["median_days"], " dias"),
            "median_days",
        ),
    )
    for column, (label, value, field) in zip(st.columns(6), values):
        with column:
            kpi_mark("brand")
            st.metric(
                label,
                value,
                delta=_period_delta(summary, previous, field),
                help=help_text.get(field),
            )
    with st.expander("Sobre os indicadores"):
        st.caption(
            "Os indicadores refletem as movimentações registradas no Tramita. Um mesmo protocolo pode retornar ao MPC ou passar por mais de um Procurador em diferentes etapas. A produção considera exclusivamente devoluções registradas com Parecer ou Cota e é atribuída ao Procurador associado ao respectivo evento. Outras devoluções não são contabilizadas como produção."
        )
        st.caption(
            "Produção/Distribuições compara o volume de devoluções produtivas com as distribuições registradas no período. Valores acima de 100% não representam, isoladamente, redução equivalente do estoque."
        )
        st.caption(
            "Permanência corresponde ao intervalo entre a distribuição registrada e a devolução produtiva; movimentações intermediárias podem influenciar esse período."
        )
        st.caption(
            f"Devoluções não produtivas no período: {summary['nonproductive_returns']}. Movimentações de saída que não foram classificadas como Parecer ou Cota e, por isso, não integram o indicador de Produção."
        )


def _procurador_rows(report):
    return [
        {
            "Procurador": row["procurador"],
            "Distribuídos": row["distributed"],
            "Produção": row["production"],
            "Pareceres": row["opinions"],
            "Cotas": row["quotas"],
            "Produção/Distribuições": row["production_rate"],
            "Mediana de permanência em dias": row["median_days"],
        }
        for row in report["by_procurador"]
    ]


def _monthly_rows(reports):
    return [
        {
            "Mês": MONTHS[item["month"] - 1],
            "Distribuídos": item["summary"]["distributed"],
            "Produção": item["summary"]["production"],
            "Pareceres": item["summary"]["opinions"],
            "Cotas": item["summary"]["quotas"],
            "Mediana de permanência": item["summary"]["median_days"],
            "Produção/Distribuições": item["summary"]["production_rate"],
        }
        for item in reports
    ]


def _temporal_charts(monthly):
    rows = _monthly_rows(monthly)
    if not rows:
        return
    st.subheader("Evolução da produção")
    left, right = st.columns(2)
    left.line_chart(
        rows, x="Mês", y=["Distribuídos", "Produção"], use_container_width=True
    )
    right.line_chart(rows, x="Mês", y=["Pareceres", "Cotas"], use_container_width=True)
    st.subheader("Eficiência temporal")
    left, right = st.columns(2)
    left.line_chart(rows, x="Mês", y="Mediana de permanência", use_container_width=True)
    right.line_chart(
        rows, x="Mês", y="Produção/Distribuições", use_container_width=True
    )


def _procurador_comparison(report, annual=False):
    rows = _procurador_rows(report)
    st.subheader("Comparativo por Procurador")
    _report_table(st, rows)
    if not rows:
        return
    left, right = st.columns(2)
    chart_rows = sorted(rows, key=lambda row: row["Produção"], reverse=annual)
    left.bar_chart(
        chart_rows,
        x="Procurador",
        y=["Distribuídos", "Produção"],
        horizontal=annual,
        use_container_width=True,
    )
    right.bar_chart(
        chart_rows,
        x="Procurador",
        y=["Pareceres", "Cotas"],
        stack="normal",
        horizontal=annual,
        use_container_width=True,
    )


def _select_year(reports, principal, key):
    read = lambda cache_key, load: _cached_report(
        reports.store, principal, cache_key, load
    )
    years = read(("historical_years",), reports.historical_years)
    if not years:
        empty_state(
            "Nenhum histórico Tramita foi importado. Utilize a área Importações para carregar a base inicial."
        )
        return None, read
    return st.selectbox("Ano", years, key=key), read


def production(store, principal=None):
    reports = TramitaReportsStore(store)
    year, read = _select_year(reports, principal, "rel_prod_year")
    if year is None:
        return
    months = read(("historical_months", year), lambda: reports.months_for_year(year))
    month = st.selectbox(
        "Mês", months, format_func=lambda value: MONTHS[value - 1], key="rel_prod_month"
    )
    report = read(
        ("period", year, month), lambda: reports.monthly_reports(year, [month])[0]
    )
    _indicator_cards(report["summary"])
    _procurador_comparison(report)


def quarterly(store, principal=None):
    reports = TramitaReportsStore(store)
    year, read = _select_year(reports, principal, "rel_quarter_year")
    if year is None:
        return
    available = read(("historical_months", year), lambda: reports.months_for_year(year))
    quarters = [
        quarter
        for quarter in range(1, 5)
        if any((quarter - 1) * 3 < month <= quarter * 3 for month in available)
    ]
    quarter = st.selectbox(
        "Trimestre",
        quarters,
        format_func=lambda value: f"{value}º trimestre",
        key="rel_quarter",
    )
    months = [month for month in available if (quarter - 1) * 3 < month <= quarter * 3]
    monthly = read(
        ("quarter_monthly", year, quarter),
        lambda: reports.monthly_reports(year, months),
    )
    report = read(
        ("quarter", year, quarter),
        lambda: reports.period_report(
            f"{year}-{months[0]:02d}-01",
            f"{year + (months[-1] == 12):04d}-{1 if months[-1] == 12 else months[-1] + 1:02d}-01",
        ),
    )
    previous = None
    prior_year = year if quarter > 1 else year - 1
    prior_quarter = quarter - 1 if quarter > 1 else 4
    prior_available = (
        available
        if prior_year == year
        else read(
            ("historical_months", prior_year),
            lambda: reports.months_for_year(prior_year),
        )
    )
    prior_months = [
        month
        for month in prior_available
        if (prior_quarter - 1) * 3 < month <= prior_quarter * 3
    ]
    if prior_months:
        previous = read(
            ("quarter", prior_year, prior_quarter),
            lambda: reports.period_report(
                f"{prior_year}-{prior_months[0]:02d}-01",
                f"{prior_year + (prior_months[-1] == 12):04d}-{1 if prior_months[-1] == 12 else prior_months[-1] + 1:02d}-01",
            ),
        )["summary"]
    _indicator_cards(report["summary"], previous)
    _temporal_charts(monthly)
    _procurador_comparison(report)


def annual(store, principal=None):
    reports = TramitaReportsStore(store)
    year, read = _select_year(reports, principal, "rel_annual_year")
    if year is None:
        return
    months = read(("historical_months", year), lambda: reports.months_for_year(year))
    st.caption(f"{year} — acumulado até {MONTHS[max(months) - 1].lower()}")
    report = read(
        ("annual", year),
        lambda: reports.period_report(f"{year}-01-01", f"{year + 1}-01-01"),
    )
    previous = None
    if reports.months_for_year(year - 1):
        previous = read(
            ("annual", year - 1),
            lambda: reports.period_report(f"{year - 1}-01-01", f"{year}-01-01"),
        )["summary"]
    monthly = read(
        ("annual_monthly", year), lambda: reports.monthly_reports(year, months)
    )
    _indicator_cards(report["summary"], previous)
    _temporal_charts(monthly)
    _procurador_comparison(report, annual=True)


def current_view(store, principal=None):
    reports = TramitaReportsStore(store)
    read = lambda key, load: _cached_report(reports.store, principal, key, load)
    snapshot = read(("latest_snapshot",), reports.latest_snapshot)
    if not snapshot:
        empty_state("Nenhuma fotografia atual do estoque processual foi importada.")
        return
    summary = read(("stock_summary", snapshot), lambda: reports.stock_summary(snapshot))
    people = sorted(row["procurador"] for row in summary if row["procurador"])
    days = sum(float(row["days"] or 0) for row in summary)
    timed = sum(row["timed"] for row in summary)
    st.caption(
        "Dados atualizados em " + datetime.fromisoformat(snapshot).strftime("%d/%m/%Y")
    )
    values = (
        sum(row["n"] for row in summary),
        len(people),
        f"{days / timed:.1f} dias" if timed else "—",
        sum(row["over30"] for row in summary),
    )
    section_label("Indicadores")
    for col, label, value in zip(
        st.columns(4),
        (
            "Processos atualmente no MPC-PB",
            "Procuradores com processos",
            "Tempo médio com procurador",
            "Processos há mais de 30 dias",
        ),
        values,
    ):
        with col:
            kpi_mark("brand")
            st.metric(label, value)
    selected = st.selectbox(
        "Procurador", ["Todos", *people], key="rel_stock_procurador"
    )
    st.subheader("Estoque por Procurador")
    _report_table(
        st,
        [
            {
                "Procurador": row["procurador"],
                "Processos atualmente distribuídos": row["n"],
                "Tempo médio com procurador": (
                    round(float(row["days"]) / row["timed"], 1)
                    if row["timed"]
                    else None
                ),
                "Maior permanência atual": row["maximum"],
                "+30 dias": row["over30"],
                "+60 dias": row["over60"],
                "+90 dias": row["over90"],
            }
            for row in sorted(summary, key=lambda row: row["procurador"])
            if row["procurador"]
        ],
    )
    fields = (
        ("natureza", "Natureza", "subcategoria"),
        ("jurisdicionado", "Jurisdicionado", "jurisdicionado"),
        ("fase", "Fase", "fase"),
        ("assistente", "Assistente", "assistente"),
    )
    filters = {"procurador": None if selected == "Todos" else selected}
    filters.update(
        {
            field: (
                None
                if st.session_state.get("stock_" + key, "Todos") == "Todos"
                else st.session_state["stock_" + key]
            )
            for key, _, field in fields
        }
    )
    # Normalize obsolete downstream choices before rendering their widgets.
    # At most one corrective query per changed ancestor; cached on normal reruns.
    facets = read(
        ("stock_options", snapshot, filters),
        lambda: reports.stock_options(snapshot, filters),
    )
    columns = st.columns(5)
    for col, (key, label, field) in zip(columns, fields):
        choices = [
            "Todos",
            *sorted(row["value"] for row in facets if row["field"] == field),
        ]
        if st.session_state.get("stock_" + key, "Todos") not in choices:
            st.session_state["stock_" + key] = "Todos"
            filters[field] = None
            facets = read(
                ("stock_options", snapshot, filters),
                lambda: reports.stock_options(snapshot, filters),
            )
        choice = col.selectbox(label, choices, key="stock_" + key)
        filters[field] = None if choice == "Todos" else choice
    band = columns[4].selectbox(
        "Faixa de dias",
        [
            "Todas",
            "0–7 dias",
            "8–15 dias",
            "16–30 dias",
            "31–60 dias",
            "61–90 dias",
            "Mais de 90 dias",
        ],
    )
    offset = _page_offset("rel_stock_page", (snapshot, repr(filters), band))
    bands, rows = read(
        ("stock_details", snapshot, filters, None if band == "Todas" else band, offset),
        lambda: reports.stock_details(
            snapshot, filters, None if band == "Todas" else band, offset
        ),
    )
    st.subheader("Faixas de permanência")
    _report_table(
        st,
        [
            {"Faixa": row["faixa"], "Processos": row["n"]}
            for row in bands
            if row["faixa"]
        ],
    )
    st.subheader("Processos há mais tempo com o Procurador")
    _report_table(
        st,
        [
            {
                "Protocolo": row["protocolo"],
                "Procurador": row["procurador"],
                "Natureza": row["subcategoria"],
                "Jurisdicionado": row["jurisdicionado"],
                "Fase": row["fase"],
                "Dias com Procurador": row["dias_com_procurador"],
                "Dias no MPC-PB": row["dias_no_mpc"],
                "Assistente": row["assistente"],
                "Prescrição": row["prescricao"],
            }
            for row in rows[:100]
        ],
    )
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
    st.subheader("Importar Histórico Inicial")
    st.caption(
        "Importa os 18 relatórios de janeiro a setembro de 2026 uma única vez. Depois da importação, a aplicação consulta somente a base local."
    )
    source_dir = Path(__file__).resolve().parents[1] / "referencias"
    if source_dir.exists():
        if st.button("Importar histórico de janeiro a setembro de 2026"):
            results = import_reference_reports(store, source_dir, principal.email)
            imported = sum(item["inserted"] for item in results)
            duplicates = sum(item["duplicates"] for item in results)
            skipped = sum(item["already_imported"] for item in results)
            registrar_evento(
                store,
                evento="TRAMITA_HISTORICO_IMPORTADO",
                modulo="relatorios",
                acao="IMPORTAR",
                principal=principal,
                detalhes={
                    "arquivos": len(results),
                    "inseridos": imported,
                    "duplicados": duplicates,
                    "ja_importados": skipped,
                },
            )
            st.session_state.pop("_reports_read_cache", None)
            st.success(
                f"Histórico processado: {imported} eventos inseridos e {duplicates} duplicidades evitadas."
            )
            st.rerun()
    else:
        st.info(
            "A fonte de migração não está disponível neste ambiente. O histórico já importado continua funcionando normalmente."
        )
    st.divider()
    st.subheader("Importar Produção Mensal")
    st.caption("Competência")
    month_column, year_column = st.columns(2)
    today = date.today()
    month_name = month_column.selectbox("Mês", MONTHS, index=today.month - 1)
    year = year_column.selectbox(
        "Ano", list(range(2020, today.year + 2)), index=today.year - 2020
    )
    competence = f"{year}-{MONTHS.index(month_name) + 1:02d}"
    competence_display = f"{month_name}/{year}"
    first, second = st.columns(2)
    incoming = first.file_uploader(
        "Relatório de Entradas", type=["xls"], key="tramita_entries"
    )
    outgoing = second.file_uploader(
        "Relatório de Saídas", type=["xls"], key="tramita_exits"
    )
    previews = []
    for kind, uploaded in (("ENTRADAS", incoming), ("SAIDAS", outgoing)):
        if uploaded:
            rows, unknown = _uploaded_preview(kind, uploaded, principal)
            previews.append((kind, uploaded, rows, unknown))
    if previews:
        st.write(
            " · ".join(f"{kind.title()}: {len(rows)}" for kind, _, rows, _ in previews)
        )
        st.caption(
            f"Procuradores identificados: {len({row['procurador'] for _, _, rows, _ in previews for row in rows})} · Competência: {competence_display}"
        )
        unknown = sorted({name for _, _, _, names in previews for name in names})
        if unknown:
            st.error("Procurador não reconhecido: " + ", ".join(unknown))
        repeated = [
            uploaded.name
            for _, uploaded, _, _ in previews
            if reports.imported_hash(file_hash(uploaded.getvalue()))
        ]
        if repeated:
            st.error(
                "Este arquivo já foi importado anteriormente: " + ", ".join(repeated)
            )
        if st.button(
            "Confirmar importação de produção",
            disabled=bool(unknown) or bool(repeated) or len(previews) != 2,
        ):
            for kind, uploaded, rows, _ in previews:
                reports.import_rows(
                    kind=kind,
                    file_name=uploaded.name,
                    file_hash=file_hash(uploaded.getvalue()),
                    actor=principal.email,
                    competence=competence,
                    rows=rows,
                )
                registrar_evento(
                    store,
                    evento="TRAMITA_" + kind + "_IMPORTADAS",
                    modulo="relatorios",
                    acao="IMPORTAR",
                    principal=principal,
                    detalhes={
                        "competencia": competence,
                        "arquivo": uploaded.name,
                        "quantidade": len(rows),
                        "hash": file_hash(uploaded.getvalue()),
                    },
                )
            st.session_state.pop("_reports_read_cache", None)
            st.success("Produção mensal importada.")
            st.rerun()
    st.divider()
    st.subheader("Importar Estoque Atual")
    snapshot = st.date_input(
        "Data da fotografia", value=date.today(), format="DD/MM/YYYY"
    )
    uploaded = st.file_uploader(
        "Arquivo de processos atuais", type=["xls"], key="tramita_stock"
    )
    if uploaded:
        try:
            rows, unknown = _uploaded_preview("ESTOQUE", uploaded, principal)
        except ValueError as exc:
            st.error(str(exc))
            return
        st.write(
            f"Processos identificados: {len(rows)} · Procuradores identificados: {len({row['procurador'] for row in rows})}"
        )
        if unknown:
            st.error("Procurador não reconhecido: " + ", ".join(unknown))
        repeated = reports.imported_hash(file_hash(uploaded.getvalue()))
        if repeated:
            st.error("Este arquivo já foi importado anteriormente.")
        if st.button(
            "Confirmar importação do estoque", disabled=bool(unknown) or repeated
        ):
            reports.import_rows(
                kind="ESTOQUE",
                file_name=uploaded.name,
                file_hash=file_hash(uploaded.getvalue()),
                actor=principal.email,
                snapshot_date=snapshot.isoformat(),
                rows=rows,
            )
            registrar_evento(
                store,
                evento="TRAMITA_ESTOQUE_IMPORTADO",
                modulo="relatorios",
                acao="IMPORTAR",
                principal=principal,
                detalhes={
                    "data_snapshot": snapshot.isoformat(),
                    "arquivo": uploaded.name,
                    "quantidade": len(rows),
                    "hash": file_hash(uploaded.getvalue()),
                },
            )
            st.session_state.pop("_reports_read_cache", None)
            st.success("Estoque atual importado.")
            st.rerun()


def render(store, principal):
    require_permission(principal, "relatorios")
    st.subheader("RELATÓRIOS E INDICADORES")
    st.caption("Acompanhamento da movimentação e do estoque processual do MPC-PB")
    sections = (
        "Visão Atual",
        "Produção Mensal",
        "Avaliação Trimestral",
        "Avaliação Anual",
    )
    if principal.administrator:
        sections += ("Importações",)
    if st.session_state.get("relatorios_section") not in sections:
        st.session_state.pop("relatorios_section", None)
    section = st.radio(
        "Seção", sections, horizontal=True, key="relatorios_section", index=0
    )
    if section != "Importações":
        st.session_state.pop("_tramita_previews", None)
    if section == "Produção Mensal":
        _render_indicators("produção mensal", production, store, principal)
    elif section == "Visão Atual":
        _render_indicators("visão atual", current_view, store, principal)
    elif section == "Avaliação Trimestral":
        _render_indicators("avaliação trimestral", quarterly, store, principal)
    elif section == "Avaliação Anual":
        _render_indicators("avaliação anual", annual, store, principal)
    else:
        imports(store, principal)


def _render_indicators(name, renderer, store, principal):
    try:
        renderer(store, principal)
    except Exception:
        LOGGER.exception("Falha ao carregar indicadores de %s", name)
        st.error(
            "Não foi possível carregar os indicadores neste momento. Tente novamente mais tarde."
        )
