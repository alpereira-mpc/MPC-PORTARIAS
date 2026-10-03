"""Interface administrativa de Relatórios e Indicadores."""

import logging
import math
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from database.tramita_reports import TramitaReportsStore
from database.institutional_reports import InstitutionalReportsStore
from services import ai_service
from services.access import require_permission
from services.audit import registrar_evento
from services.date_format import format_date_br, format_datetime_br
from services.institutional_reports import (
    EMPTY_STRUCTURED_CONTENT,
    build_report_snapshot,
    can_finalize,
)
from services.institutional_report_content import (
    AI_SECTIONS,
    SECTIONS,
    InstitutionalReportContentService,
    normalize_content,
)
from services.themes import theme_tokens
from services.tramita_reports import (
    file_hash,
    import_reference_reports,
    parse_movements,
    parse_stock,
    turnaround_days,
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
CHART_COLORS = {
    "Distribuídos": "#1F77B4",
    "Produção": "#2CA02C",
    "Pareceres": "#9467BD",
    "Cotas": "#FF7F0E",
    "Mediana de permanência": "#D62728",
    "Produção/Distribuições": "#17A2B8",
}
COUNT_COLUMNS = frozenset(
    {
        "Entradas",
        "Saídas",
        "Distribuídos",
        "Produção",
        "Pareceres",
        "Cotas",
        "Quantidade",
        "Processos",
        "Eventos",
        "Protocolos",
        "Estoque",
        "Processos atualmente distribuídos",
        "Processos atualmente no MPC-PB",
        "Procuradores com processos",
        "Processos há mais de 30 dias",
        "+30 dias",
        "+60 dias",
        "+90 dias",
    }
)
INTEGER_DAY_COLUMNS = frozenset(
    {
        "Maior permanência atual",
        "Dias com Procurador",
        "Dias com Assistente",
        "Dias no MPC-PB",
    }
)
FRACTIONAL_DAY_COLUMNS = (
    "Tempo médio até devolução",
    "Tempo médio com procurador",
    "Mediana de permanência",
    "Mediana de permanência em dias",
)
COUNT_METRICS = frozenset({"Distribuídos", "Produção", "Pareceres", "Cotas"})


def _format_integer(value):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.0f}"


def _format_decimal_br(value, suffix=""):
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.1f}".replace(".", ",") + suffix


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
    formatters = {
        column: _format_integer
        for column in COUNT_COLUMNS | INTEGER_DAY_COLUMNS
        if column in frame.columns
    }
    formatters.update(
        {
            column: _format_decimal_br
            for column in FRACTIONAL_DAY_COLUMNS
            if column in frame.columns
        }
    )
    if "Produção/Distribuições" in frame.columns:
        formatters["Produção/Distribuições"] = lambda value: _format_decimal_br(
            value, "%"
        )
    if formatters:
        # Styler changes presentation only: dataframe values stay numeric for
        # Streamlit sorting and any future table interactions.
        styler = styler.format(formatters)
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
    return _format_decimal_br(value, suffix)


def _period_delta(current, previous, field):
    if previous is None or current[field] is None or previous[field] is None:
        return None
    if field == "production_rate":
        return f"{current[field] - previous[field]:+.1f}".replace(".", ",") + " p.p."
    if field == "median_days":
        return f"{current[field] - previous[field]:+.1f}".replace(".", ",") + " dias"
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
        ("Distribuídos", _format_integer(summary["distributed"]), "distributed"),
        ("Produção", _format_integer(summary["production"]), "production"),
        ("Pareceres", _format_integer(summary["opinions"]), "opinions"),
        ("Cotas", _format_integer(summary["quotas"]), "quotas"),
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


def _render_chart(name, render):
    """Keep a chart fault local while retaining actionable server diagnostics."""
    try:
        render()
    except Exception:
        LOGGER.exception("Falha ao renderizar o gráfico de relatórios: %s", name)
        st.warning("Não foi possível exibir este gráfico no momento.")


def _chart_values(rows, fields):
    values = []
    for row in rows:
        for field in fields:
            value = row[field]
            if value is None:
                continue
            try:
                numeric = float(value)
            except (TypeError, ValueError):
                LOGGER.warning(
                    "Valor não numérico ignorado no gráfico %s: %r", field, value
                )
                continue
            if not math.isfinite(numeric):
                continue
            values.append({"Mês": str(row["Mês"]), "Métrica": field, "Valor": numeric})
    return values


def _line_chart(target, rows, fields, y_title, key):
    values = _chart_values(rows, fields)
    if not values:
        return
    number_format = ".0f" if all(field in COUNT_METRICS for field in fields) else ".1f"
    target.vega_lite_chart(
        values,
        {
            "mark": {"type": "line", "point": True},
            "encoding": {
                "x": {
                    "field": "Mês",
                    "type": "ordinal",
                    "sort": [row["Mês"] for row in rows],
                    "axis": {"title": "Mês", "labelAngle": 0},
                },
                "y": {
                    "field": "Valor",
                    "type": "quantitative",
                    "axis": {"title": y_title, "format": number_format},
                },
                "color": {
                    "field": "Métrica",
                    "type": "nominal",
                    "scale": {
                        "domain": fields,
                        "range": [CHART_COLORS[field] for field in fields],
                    },
                    "legend": {"title": None},
                },
                "tooltip": [
                    {"field": "Mês", "type": "nominal"},
                    {"field": "Métrica", "type": "nominal"},
                    {
                        "field": "Valor",
                        "type": "quantitative",
                        "format": number_format,
                        "title": y_title,
                    },
                ],
            },
        },
        use_container_width=True,
        key=key,
    )


def _temporal_charts(monthly, key_prefix, compact_period=False):
    rows = _monthly_rows(monthly)
    if not rows:
        return
    st.subheader("Evolução da produção")
    left, right = st.columns(2)
    if compact_period:
        _render_chart(
            key_prefix + "_fluxo",
            lambda: _category_bar_chart(
                left,
                rows,
                "Mês",
                ["Distribuídos", "Produção"],
                key_prefix + "_fluxo",
            ),
        )
        _render_chart(
            key_prefix + "_tipos",
            lambda: _category_bar_chart(
                right,
                rows,
                "Mês",
                ["Pareceres", "Cotas"],
                key_prefix + "_tipos",
                stacked=True,
            ),
        )
    else:
        _render_chart(
            key_prefix + "_fluxo",
            lambda: _line_chart(
                left,
                rows,
                ["Distribuídos", "Produção"],
                "Quantidade",
                key_prefix + "_fluxo",
            ),
        )
        _render_chart(
            key_prefix + "_tipos",
            lambda: _line_chart(
                right,
                rows,
                ["Pareceres", "Cotas"],
                "Quantidade",
                key_prefix + "_tipos",
            ),
        )
    st.subheader("Permanência e relação entre fluxos")
    left, right = st.columns(2)
    _render_chart(
        key_prefix + "_permanencia",
        lambda: _line_chart(
            left,
            rows,
            ["Mediana de permanência"],
            "Mediana de permanência (dias)",
            key_prefix + "_permanencia",
        ),
    )
    _render_chart(
        key_prefix + "_relacao",
        lambda: _line_chart(
            right,
            rows,
            ["Produção/Distribuições"],
            "Produção/Distribuições (%)",
            key_prefix + "_relacao",
        ),
    )


def _procurador_chart(target, rows, fields, stacked, key):
    values = [
        {
            "Procurador": row["Procurador"],
            "Métrica": field,
            "Quantidade": row[field],
            "Total": (
                sum(_numeric_chart_value(row[item]) or 0 for item in fields)
                if stacked
                else None
            ),
        }
        for row in rows
        for field in fields
    ]
    encoding = {
        "y": {
            "field": "Procurador",
            "type": "nominal",
            "sort": [row["Procurador"] for row in rows],
            "axis": {"title": "Procurador", "labelLimit": 0},
        },
        "x": {
            "field": "Quantidade",
            "type": "quantitative",
            "axis": {"title": "Quantidade", "format": ".0f"},
        },
        "color": {
            "field": "Métrica",
            "type": "nominal",
            "scale": {
                "domain": fields,
                "range": [CHART_COLORS[field] for field in fields],
            },
            "legend": {"title": None},
        },
        "tooltip": [
            {"field": "Procurador", "type": "nominal"},
            {"field": "Métrica", "type": "nominal"},
            {"field": "Quantidade", "type": "quantitative", "format": ".0f"},
            *(
                [{"field": "Total", "type": "quantitative", "format": ".0f"}]
                if stacked
                else []
            ),
        ],
    }
    if not stacked:
        encoding["yOffset"] = {"field": "Métrica"}
    target.vega_lite_chart(
        values,
        {
            "mark": "bar",
            "height": max(240, 34 * len(rows)),
            "encoding": encoding,
        },
        use_container_width=True,
        key=key,
    )


def _numeric_chart_value(value):
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _composition_values(rows, category_field, fields):
    values = []
    for row in rows:
        quantities = {
            field: _numeric_chart_value(row.get(field)) or 0 for field in fields
        }
        total = sum(quantities.values())
        if not total:
            continue
        for field, quantity in quantities.items():
            values.append(
                {
                    category_field: str(row[category_field]),
                    "Métrica": field,
                    "Quantidade": quantity,
                    "Percentual": quantity / total * 100,
                }
            )
    return values


def _composition_chart(target, rows, category_field, fields, key, horizontal=False):
    values = _composition_values(rows, category_field, fields)
    if not values:
        return
    category_encoding = {
        "field": category_field,
        "type": "nominal",
        "sort": [str(row[category_field]) for row in rows],
        "axis": {"title": category_field, "labelLimit": 0, "labelAngle": 0},
    }
    value_encoding = {
        "field": "Percentual",
        "type": "quantitative",
        "stack": "zero",
        "scale": {"domain": [0, 100]},
        "axis": {"title": "Participação na produção (%)", "format": ".0f"},
    }
    encoding = {
        "y": category_encoding if horizontal else value_encoding,
        "x": value_encoding if horizontal else category_encoding,
        "color": {
            "field": "Métrica",
            "type": "nominal",
            "scale": {
                "domain": fields,
                "range": [CHART_COLORS[field] for field in fields],
            },
            "legend": {"title": None},
        },
        "tooltip": [
            {"field": category_field, "type": "nominal"},
            {"field": "Métrica", "type": "nominal"},
            {"field": "Quantidade", "type": "quantitative", "format": ".0f"},
            {"field": "Percentual", "type": "quantitative", "format": ".1f"},
        ],
    }
    target.vega_lite_chart(
        values,
        {
            "mark": "bar",
            "height": max(220, 34 * len(rows)) if horizontal else 260,
            "encoding": encoding,
        },
        use_container_width=True,
        key=key,
    )


def _category_values(rows, category_field, fields):
    values = []
    for row in rows:
        total = sum(_numeric_chart_value(row.get(field)) or 0 for field in fields)
        for field in fields:
            value = _numeric_chart_value(row.get(field))
            if value is not None:
                values.append(
                    {
                        category_field: str(row[category_field]),
                        "Métrica": field,
                        "Quantidade": value,
                        "Total": total,
                    }
                )
    return values


def _category_bar_chart(target, rows, category_field, fields, key, stacked=False):
    values = _category_values(rows, category_field, fields)
    if not values:
        return
    encoding = {
        "x": {
            "field": category_field,
            "type": "nominal",
            "sort": [str(row[category_field]) for row in rows],
            "axis": {"title": category_field, "labelAngle": 0},
        },
        "y": {
            "field": "Quantidade",
            "type": "quantitative",
            "axis": {"title": "Quantidade", "format": ".0f"},
        },
        "color": {
            "field": "Métrica",
            "type": "nominal",
            "scale": {
                "domain": fields,
                "range": [CHART_COLORS[field] for field in fields],
            },
            "legend": {"title": None},
        },
        "tooltip": [
            {"field": category_field, "type": "nominal"},
            {"field": "Métrica", "type": "nominal"},
            {"field": "Quantidade", "type": "quantitative", "format": ".0f"},
            {"field": "Total", "type": "quantitative", "format": ".0f"},
        ],
    }
    if not stacked:
        encoding["xOffset"] = {"field": "Métrica"}
    target.vega_lite_chart(
        values,
        {"mark": "bar", "height": 260, "encoding": encoding},
        use_container_width=True,
        key=key,
    )


HISTORICAL_DURATION_BANDS = (
    ("Até 7 dias", 7),
    ("8 a 15 dias", 15),
    ("16 a 30 dias", 30),
    ("31 a 60 dias", 60),
    ("Acima de 60 dias", None),
)


def _productive_event_rows(events):
    rows = []
    for event in events:
        if event["tipo_movimentacao"] != "SAIDA" or event[
            "classificacao_producao"
        ] not in ("PARECER", "COTA"):
            continue
        days = _numeric_chart_value(turnaround_days(event))
        if days is None:
            continue
        rows.append({**event, "dias": days})
    return rows


def _duration_distribution(events):
    counts = {label: 0 for label, _ in HISTORICAL_DURATION_BANDS}
    for event in _productive_event_rows(events):
        for label, upper_bound in HISTORICAL_DURATION_BANDS:
            if upper_bound is None or event["dias"] <= upper_bound:
                counts[label] += 1
                break
    total = sum(counts.values())
    return [
        {
            "Faixa": label,
            "Produções": count,
            "Percentual": count / total * 100 if total else None,
        }
        for label, count in counts.items()
    ]


def _duration_chart(target, events, key):
    values = _duration_distribution(events)
    if not any(row["Produções"] for row in values):
        return
    target.vega_lite_chart(
        values,
        {
            "mark": "bar",
            "encoding": {
                "x": {
                    "field": "Faixa",
                    "type": "ordinal",
                    "sort": [label for label, _ in HISTORICAL_DURATION_BANDS],
                    "axis": {"title": "Faixa de permanência", "labelAngle": 0},
                },
                "y": {
                    "field": "Produções",
                    "type": "quantitative",
                    "axis": {"title": "Quantidade de produções", "format": ".0f"},
                },
                "color": {"value": CHART_COLORS["Mediana de permanência"]},
                "tooltip": [
                    {"field": "Faixa", "type": "nominal"},
                    {"field": "Produções", "type": "quantitative", "format": ".0f"},
                    {"field": "Percentual", "type": "quantitative", "format": ".1f"},
                ],
            },
        },
        use_container_width=True,
        key=key,
    )


def _distinct_protocols(events):
    return len(
        {
            event["protocolo"]
            for event in events
            if event["tipo_movimentacao"] == "ENTRADA" and event["protocolo"]
        }
    )


def _cumulative_monthly_rows(monthly):
    distributed = production = 0
    rows = []
    for row in _monthly_rows(monthly):
        distributed += row["Distribuídos"]
        production += row["Produção"]
        rows.append(
            {
                "Mês": row["Mês"],
                "Distribuídos": distributed,
                "Produção": production,
            }
        )
    return rows


def _production_heatmap(target, events, key):
    by_cell = {}
    for event in _productive_event_rows(events):
        month = MONTHS[int(event["data_evento"][5:7]) - 1][:3]
        procurador = event["procurador"] or "Não identificado"
        cell = by_cell.setdefault(
            (month, procurador),
            {
                "Mês": month,
                "Procurador": procurador,
                "Produção": 0,
                "Pareceres": 0,
                "Cotas": 0,
            },
        )
        cell["Produção"] += 1
        cell[
            "Pareceres" if event["classificacao_producao"] == "PARECER" else "Cotas"
        ] += 1
    values = list(by_cell.values())
    if not values:
        return
    people = sorted(
        {row["Procurador"] for row in values},
        key=lambda name: (
            -sum(row["Produção"] for row in values if row["Procurador"] == name),
            name,
        ),
    )
    months = [month[:3] for month in MONTHS]
    target.vega_lite_chart(
        values,
        {
            "mark": "rect",
            "height": max(220, 30 * len(people)),
            "encoding": {
                "x": {
                    "field": "Mês",
                    "type": "ordinal",
                    "sort": months,
                    "axis": {"title": "Mês", "labelAngle": 0},
                },
                "y": {
                    "field": "Procurador",
                    "type": "nominal",
                    "sort": people,
                    "axis": {"title": "Procurador", "labelLimit": 0},
                },
                "color": {
                    "field": "Produção",
                    "type": "quantitative",
                    "scale": {"range": ["#E8F3EC", CHART_COLORS["Produção"]]},
                    "legend": {"title": "Produção"},
                },
                "tooltip": [
                    {"field": "Procurador", "type": "nominal"},
                    {"field": "Mês", "type": "nominal"},
                    {"field": "Produção", "type": "quantitative", "format": ".0f"},
                    {"field": "Pareceres", "type": "quantitative", "format": ".0f"},
                    {"field": "Cotas", "type": "quantitative", "format": ".0f"},
                ],
            },
        },
        use_container_width=True,
        key=key,
    )


STOCK_DURATION_BANDS = (
    ("0 a 15 dias", 15),
    ("16 a 30 dias", 30),
    ("31 a 60 dias", 60),
    ("61 a 90 dias", 90),
    ("91 a 120 dias", 120),
    ("Acima de 120 dias", None),
)
STOCK_BAND_COLORS = (
    CHART_COLORS["Distribuídos"],
    CHART_COLORS["Produção"],
    CHART_COLORS["Pareceres"],
    CHART_COLORS["Cotas"],
    CHART_COLORS["Mediana de permanência"],
    CHART_COLORS["Produção/Distribuições"],
)


def _stock_band(days):
    number = _numeric_chart_value(days)
    if number is None:
        return None
    for label, upper_bound in STOCK_DURATION_BANDS:
        if upper_bound is None or number <= upper_bound:
            return label
    return None


def _stock_band_rows(stock_rows):
    counts = {label: 0 for label, _ in STOCK_DURATION_BANDS}
    for row in stock_rows:
        band = _stock_band(row["dias_com_procurador"])
        if band:
            counts[band] += 1
    return [{"Faixa": label, "Processos": count} for label, count in counts.items()]


def _stock_band_chart(target, stock_rows, key):
    values = _stock_band_rows(stock_rows)
    if not any(row["Processos"] for row in values):
        return
    target.vega_lite_chart(
        values,
        {
            "mark": "bar",
            "encoding": {
                "x": {
                    "field": "Faixa",
                    "type": "ordinal",
                    "sort": [label for label, _ in STOCK_DURATION_BANDS],
                    "axis": {"title": "Faixa de permanência", "labelAngle": 0},
                },
                "y": {
                    "field": "Processos",
                    "type": "quantitative",
                    "axis": {"title": "Quantidade de processos", "format": ".0f"},
                },
                "color": {"value": CHART_COLORS["Mediana de permanência"]},
                "tooltip": [
                    {"field": "Faixa", "type": "nominal"},
                    {"field": "Processos", "type": "quantitative", "format": ".0f"},
                ],
            },
        },
        use_container_width=True,
        key=key,
    )


def _stock_by_procurador_chart(target, stock_rows, key):
    counts = {}
    for row in stock_rows:
        band = _stock_band(row["dias_com_procurador"])
        if band and row["procurador"]:
            identifier = (row["procurador"], band)
            counts[identifier] = counts.get(identifier, 0) + 1
    values = [
        {"Procurador": procurador, "Faixa": band, "Processos": count}
        for (procurador, band), count in counts.items()
    ]
    if not values:
        return
    people = sorted({row["Procurador"] for row in values})
    target.vega_lite_chart(
        values,
        {
            "mark": "bar",
            "height": max(240, 34 * len(people)),
            "encoding": {
                "y": {
                    "field": "Procurador",
                    "type": "nominal",
                    "sort": people,
                    "axis": {"title": "Procurador", "labelLimit": 0},
                },
                "x": {
                    "field": "Processos",
                    "type": "quantitative",
                    "axis": {"title": "Quantidade de processos", "format": ".0f"},
                },
                "color": {
                    "field": "Faixa",
                    "type": "nominal",
                    "scale": {
                        "domain": [label for label, _ in STOCK_DURATION_BANDS],
                        "range": list(STOCK_BAND_COLORS),
                    },
                    "legend": {"title": None},
                },
                "tooltip": [
                    {"field": "Procurador", "type": "nominal"},
                    {"field": "Faixa", "type": "nominal"},
                    {"field": "Processos", "type": "quantitative", "format": ".0f"},
                ],
            },
        },
        use_container_width=True,
        key=key,
    )


def _stock_top_chart(target, stock_rows, key):
    values = [
        {
            "Protocolo": row["protocolo"],
            "Dias com Procurador": _numeric_chart_value(row["dias_com_procurador"]),
        }
        for row in stock_rows
        if _numeric_chart_value(row["dias_com_procurador"]) is not None
    ]
    values = sorted(values, key=lambda row: row["Dias com Procurador"], reverse=True)[
        :10
    ]
    if not values:
        return
    target.vega_lite_chart(
        values,
        {
            "mark": "bar",
            "height": max(240, 30 * len(values)),
            "encoding": {
                "y": {
                    "field": "Protocolo",
                    "type": "nominal",
                    "sort": [row["Protocolo"] for row in values],
                    "axis": {"title": "Protocolo", "labelLimit": 0},
                },
                "x": {
                    "field": "Dias com Procurador",
                    "type": "quantitative",
                    "axis": {"title": "Dias com Procurador", "format": ".0f"},
                },
                "color": {"value": CHART_COLORS["Mediana de permanência"]},
                "tooltip": [
                    {"field": "Protocolo", "type": "nominal"},
                    {
                        "field": "Dias com Procurador",
                        "type": "quantitative",
                        "format": ".0f",
                    },
                ],
            },
        },
        use_container_width=True,
        key=key,
    )


def _procurador_comparison(report, key_prefix):
    rows = _procurador_rows(report)
    st.subheader("Comparativo por Procurador")
    rows = sorted(rows, key=lambda row: (-row["Produção"], row["Procurador"]))
    _report_table(st, rows)
    if not rows:
        return
    left, right = st.columns(2)
    _render_chart(
        key_prefix + "_procuradores_fluxo",
        lambda: _procurador_chart(
            left,
            rows,
            ["Distribuídos", "Produção"],
            False,
            key_prefix + "_procuradores_fluxo",
        ),
    )
    _render_chart(
        key_prefix + "_procuradores_tipos",
        lambda: _procurador_chart(
            right,
            rows,
            ["Pareceres", "Cotas"],
            True,
            key_prefix + "_procuradores_tipos",
        ),
    )
    st.subheader("Composição percentual da produção por Procurador")
    _render_chart(
        key_prefix + "_procuradores_percentual",
        lambda: _composition_chart(
            st,
            rows,
            "Procurador",
            ["Pareceres", "Cotas"],
            key_prefix + "_procuradores_percentual",
            horizontal=True,
        ),
    )


def _quarterly_summary(reports, key_prefix):
    st.subheader("Resumo por trimestre")
    rows = [
        {
            "Trimestre": f"{item['quarter']}º trimestre",
            "Distribuídos": item["summary"]["distributed"],
            "Produção": item["summary"]["production"],
            "Pareceres": item["summary"]["opinions"],
            "Cotas": item["summary"]["quotas"],
            "Produção/Distribuições": item["summary"]["production_rate"],
            "Mediana de permanência": item["summary"]["median_days"],
        }
        for item in reports
        if item["summary"]["distributed"] or item["summary"]["production"]
    ]
    _report_table(st, rows)
    if not rows:
        return

    def render():
        values = [
            {
                "Trimestre": row["Trimestre"],
                "Métrica": field,
                "Quantidade": row[field],
            }
            for row in rows
            for field in ("Distribuídos", "Produção")
        ]
        st.vega_lite_chart(
            values,
            {
                "mark": "bar",
                "encoding": {
                    "x": {
                        "field": "Trimestre",
                        "type": "nominal",
                        "sort": [row["Trimestre"] for row in rows],
                        "axis": {"title": "Trimestre", "labelAngle": 0},
                    },
                    "xOffset": {"field": "Métrica"},
                    "y": {
                        "field": "Quantidade",
                        "type": "quantitative",
                        "axis": {"title": "Quantidade", "format": ".0f"},
                    },
                    "color": {
                        "field": "Métrica",
                        "type": "nominal",
                        "scale": {
                            "domain": ["Distribuídos", "Produção"],
                            "range": [
                                CHART_COLORS["Distribuídos"],
                                CHART_COLORS["Produção"],
                            ],
                        },
                        "legend": {"title": None},
                    },
                    "tooltip": [
                        {"field": "Trimestre", "type": "nominal"},
                        {"field": "Métrica", "type": "nominal"},
                        {
                            "field": "Quantidade",
                            "type": "quantitative",
                            "format": ".0f",
                        },
                    ],
                },
            },
            use_container_width=True,
            key=key_prefix + "_resumo_trimestres",
        )

    _render_chart(key_prefix + "_resumo_trimestres", render)
    _render_chart(
        key_prefix + "_resumo_trimestres_tipos",
        lambda: _category_bar_chart(
            st,
            rows,
            "Trimestre",
            ["Pareceres", "Cotas"],
            key_prefix + "_resumo_trimestres_tipos",
            stacked=True,
        ),
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
        "Mês",
        months,
        index=len(months) - 1,
        format_func=lambda value: MONTHS[value - 1],
        key="rel_prod_month",
    )
    period = read(
        ("period_data", year, month),
        lambda: reports.period_data(
            f"{year}-{month:02d}-01",
            f"{year + (month == 12):04d}-{1 if month == 12 else month + 1:02d}-01",
        ),
    )
    report, events = period["report"], period["events"]
    _indicator_cards(report["summary"])
    st.caption(
        f"Protocolos distintos no período: {_format_integer(_distinct_protocols(events))}."
    )
    _procurador_comparison(report, f"relatorios_mensal_{year}_{month:02d}")
    st.subheader("Distribuição da permanência")
    _render_chart(
        f"relatorios_mensal_{year}_{month:02d}_permanencia_faixas",
        lambda: _duration_chart(
            st,
            events,
            f"relatorios_mensal_{year}_{month:02d}_permanencia_faixas",
        ),
    )


INSTITUTIONAL_STATUS_LABELS = {
    "RASCUNHO": "Rascunho",
    "EM_REVISAO": "Em revisão",
    "FINALIZADO": "Finalizado",
    "ENVIADO": "Enviado",
}


def _institutional_month_span(months, year):
    """Present stored coverage in Portuguese without exposing query boundaries."""
    months = sorted({int(month) for month in months or []})
    if not months:
        return f"sem meses disponíveis em {year}"
    names = [MONTHS[month - 1].lower() for month in months]
    if months == list(range(months[0], months[-1] + 1)):
        return f"{names[0]} a {names[-1]} de {year}"
    return f"{', '.join(names)} de {year}"


def _institutional_covered_period(snapshot):
    """Return the human period covered by the frozen data, never its SQL end."""
    metadata = snapshot["metadados"]
    coverage = snapshot["cobertura_historica"]
    start = date.fromisoformat(metadata["data_inicio"])
    months = coverage.get("meses_disponiveis") or []
    if not months:
        return f"Período previsto: {start.strftime('%d/%m/%Y')}"
    last_month = max(int(month) for month in months)
    end = date(start.year, last_month, monthrange(start.year, last_month)[1])
    return f"Período coberto: {start.strftime('%d/%m/%Y')} a {end.strftime('%d/%m/%Y')}"


def _institutional_coverage_text(snapshot):
    metadata = snapshot["metadados"]
    coverage = snapshot["cobertura_historica"]
    span = _institutional_month_span(coverage.get("meses_disponiveis"), metadata["ano"])
    gaps = coverage.get("meses_ausentes") or coverage.get("lacunas_no_ano") or []
    condition = "sem lacunas" if not gaps else "com lacunas"
    partial = (
        "acumulado parcial" if metadata.get("periodo_parcial") else "período completo"
    )
    return f"Cobertura: {span} — {condition}; {partial}."


def _institutional_snapshot_rows(snapshot):
    """Adapt frozen data for existing table formatting; never read current reports."""
    rows = []
    for entry in snapshot.get("serie_mensal", []):
        summary = entry["summary"]
        rows.append(
            {
                "Mês": MONTHS[int(entry["month"]) - 1],
                "Distribuídos": summary["distributed"],
                "Produção": summary["production"],
                "Pareceres": summary["opinions"],
                "Cotas": summary["quotas"],
                "Produção/Distribuições": summary["production_rate"],
                "Mediana de permanência": summary["median_days"],
            }
        )
    return rows


def _institutional_preview(report, principal, store):
    """Friendly, read-only rendering of the persisted snapshot."""
    snapshot = report["snapshot_dados"]
    metadata = snapshot["metadados"]
    summary = snapshot["indicadores_gerais"]
    type_label = (
        "Relatório Trimestral de Produção"
        if metadata["tipo"] == "TRIMESTRAL"
        else f"Relatório Anual de Produção — {metadata['ano']}"
    )
    period_label = (
        f"{metadata['trimestre']}º trimestre de {metadata['ano']}"
        if metadata["tipo"] == "TRIMESTRAL"
        else "Acumulado de "
        + _institutional_month_span(
            snapshot["cobertura_historica"].get("meses_disponiveis"), metadata["ano"]
        )
    )
    st.markdown(f"#### {type_label}")
    st.caption(period_label)
    st.caption(
        f"Versão {report['versao']} · {INSTITUTIONAL_STATUS_LABELS.get(report['status'], report['status'])}"
    )
    st.caption(_institutional_covered_period(snapshot))
    st.caption(f"Data de corte: {format_datetime_br(report['data_corte'])}")
    st.caption(f"Responsável: {report['criado_por'] or 'Não informado'}")

    labels = ("Distribuídos", "Produção", "Pareceres", "Cotas")
    values = (
        summary["distributed"],
        summary["production"],
        summary["opinions"],
        summary["quotas"],
    )
    for column, label, value in zip(st.columns(4), labels, values):
        with column:
            st.metric(label, _format_integer(value))
    ratio, duration = st.columns(2)
    ratio.metric(
        "Produção/Distribuições", _format_decimal_br(summary["production_rate"], "%")
    )
    duration.metric(
        "Mediana de permanência", _format_decimal_br(summary["median_days"], " dias")
    )

    st.subheader("Cobertura dos dados")
    st.caption(_institutional_coverage_text(snapshot))
    st.subheader("Produção por Procurador")
    _report_table(
        st,
        [
            {
                "Procurador": row["procurador"],
                "Distribuídos": row["distributed"],
                "Produção": row["production"],
                "Pareceres": row["opinions"],
                "Cotas": row["quotas"],
                "Produção/Distribuições": row["production_rate"],
                "Mediana de permanência": row["median_days"],
            }
            for row in snapshot.get("por_procurador", [])
        ],
    )
    st.subheader("Série do período")
    _report_table(st, _institutional_snapshot_rows(snapshot))
    with st.expander("Sobre os dados deste relatório"):
        for text in snapshot.get("nota_metodologica", {}).values():
            if isinstance(text, str) and text:
                st.write(text)
    if getattr(principal, "administrator", False):
        with st.expander("Diagnóstico técnico"):
            st.json({"metadados": metadata, "snapshot": snapshot})

    _institutional_text_content(report, principal, store)


def _institutional_text_content(report, principal, store):
    """Read/write content separately from the frozen numerical preview."""
    content = normalize_content(
        report["conteudo_estruturado"], report["snapshot_dados"]
    )
    editable = getattr(principal, "administrator", False) and report["status"] in (
        "RASCUNHO",
        "EM_REVISAO",
    )
    service = InstitutionalReportContentService(store) if editable else None
    st.subheader("Conteúdo textual")
    if editable and service:
        actions = st.columns(3)
        if actions[0].button(
            "Gerar conteúdo com IA", key=f"institutional_ai_all_{report['id']}"
        ):
            try:
                service.generate_all(report["id"], principal)
                st.success("Conteúdo gerado e encaminhado para revisão.")
                st.rerun()
            except (ValueError, PermissionError, ai_service.GeminiErro) as exc:
                st.error(str(exc))
        if actions[1].button(
            "Salvar alterações", key=f"institutional_save_{report['id']}"
        ):
            try:
                texts = {
                    key: st.session_state.get(
                        f"institutional_text_{report['id']}_{key}", value["texto"]
                    )
                    for key, value in content.items()
                }
                service.save_manual(report["id"], texts, principal)
                st.success("Alterações salvas.")
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
        if actions[2].button(
            "Marcar para revisão", key=f"institutional_review_{report['id']}"
        ):
            try:
                service.mark_for_review(report["id"], principal)
                st.success("Relatório marcado como Em revisão.")
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.warning(str(exc))
    for index, (key, label) in enumerate(SECTIONS):
        section = content[key]
        with st.expander(label, expanded=index == 0):
            if editable:
                st.text_area(
                    label,
                    section["texto"],
                    key=f"institutional_text_{report['id']}_{key}",
                    height=150,
                    label_visibility="collapsed",
                )
                if (
                    key in AI_SECTIONS
                    and service
                    and st.button(
                        "Regenerar com IA",
                        key=f"institutional_regen_{report['id']}_{key}",
                    )
                ):
                    try:
                        service.regenerate(report["id"], key, principal)
                        st.success(f"{label} regenerada.")
                        st.rerun()
                    except (ValueError, PermissionError, ai_service.GeminiErro) as exc:
                        st.error(str(exc))
            else:
                st.write(section["texto"] or "Seção ainda não preenchida.")
            if section.get("requer_revisao"):
                st.warning("Esta seção contém valores que devem ser revisados.")


def _institutional_period_report(store, principal, tipo, year, quarter=None):
    """Small Phase 1 entry point; rendering never rebuilds a saved snapshot."""
    repository = InstitutionalReportsStore(store)
    current = repository.latest_for_period(tipo, year, quarter)
    label = f"{quarter}º trimestre de {year}" if quarter else str(year)
    st.subheader("Relatório do período")
    if not current:
        st.caption(f"Nenhum relatório institucional criado para {label}.")
    else:
        snapshot = current["snapshot_dados"]
        title = (
            "Relatório Trimestral de Produção"
            if tipo == "TRIMESTRAL"
            else f"Relatório Anual de Produção — {year}"
        )
        period = (
            f"{quarter}º trimestre de {year}"
            if tipo == "TRIMESTRAL"
            else "Acumulado de "
            + _institutional_month_span(
                snapshot["cobertura_historica"].get("meses_disponiveis"), year
            )
        )
        st.markdown(f"**{title}**")
        st.caption(period)
        st.caption(
            f"Status: {INSTITUTIONAL_STATUS_LABELS.get(current['status'], current['status'])} · "
            f"Versão {current['versao']} · "
            f"criado em {format_datetime_br(current['criado_em'])} · "
            f"corte: {format_datetime_br(current['data_corte'])}"
        )
        open_key = f"institutional_open_{tipo}_{year}_{quarter}"
        open_state_key = f"{open_key}_visible"
        if st.button("Abrir relatório", key=open_key):
            st.session_state[open_state_key] = True
        if st.session_state.get(open_state_key):
            _institutional_preview(current, principal, store)
    if not getattr(principal, "administrator", False):
        return

    creating_new = bool(current and current["status"] in ("FINALIZADO", "ENVIADO"))
    create_label = "Criar nova versão" if creating_new else "Criar relatório"
    if not current or creating_new:
        if st.button(create_label, key=f"institutional_create_{tipo}_{year}_{quarter}"):
            try:
                snapshot = build_report_snapshot(
                    store, tipo=tipo, ano=year, trimestre=quarter
                )
                metadata = snapshot["metadados"]
                params = {
                    "tipo": tipo,
                    "ano": year,
                    "trimestre": quarter,
                    "data_inicio": metadata["data_inicio"],
                    "data_fim": metadata["data_fim"],
                    "periodo_parcial": metadata["periodo_parcial"],
                    "descricao_periodo": metadata["descricao_periodo"],
                    "snapshot_dados": snapshot,
                    "conteudo_estruturado": EMPTY_STRUCTURED_CONTENT,
                    "actor": principal.email,
                }
                report = (
                    repository.create_new_version(**params)
                    if creating_new
                    else repository.create(**params)
                )
                registrar_evento(
                    store,
                    evento=(
                        "RELATORIO_INSTITUCIONAL_NOVA_VERSAO"
                        if creating_new
                        else "RELATORIO_INSTITUCIONAL_CRIADO"
                    ),
                    modulo="relatorios",
                    acao="CRIAR_NOVA_VERSAO" if creating_new else "CRIAR",
                    principal=principal,
                    entidade_tipo="relatorio_institucional",
                    entidade_id=report["id"],
                    detalhes={
                        "tipo": tipo,
                        "ano": year,
                        "trimestre": quarter,
                        "versao": report["versao"],
                    },
                )
                st.success(f"Relatório institucional versão {report['versao']} criado.")
                st.rerun()
            except ValueError as exc:
                st.warning(str(exc))

    if current and current["status"] in ("RASCUNHO", "EM_REVISAO"):
        content = normalize_content(
            current["conteudo_estruturado"], current["snapshot_dados"]
        )
        unsaved_text = any(
            st.session_state.get(
                f"institutional_text_{current['id']}_{key}", section["texto"]
            )
            != section["texto"]
            for key, section in content.items()
        )
        if unsaved_text:
            st.warning("Salve as alterações textuais antes de finalizar o relatório.")
        if any(not section["texto"].strip() for section in content.values()):
            st.info("Há seções textuais vazias; a finalização continua disponível.")
        if any(section.get("requer_revisao") for section in content.values()):
            st.warning(
                "Há seções com valores que devem ser revisados antes da finalização."
            )
        eligible = can_finalize(current["snapshot_dados"])
        if not eligible:
            st.warning(
                "Finalização indisponível: há lacunas na cobertura de dados do período."
            )
        confirmed = st.checkbox(
            "Confirmo a finalização e o congelamento deste snapshot.",
            key=f"institutional_confirm_{current['id']}",
            disabled=not eligible or unsaved_text,
        )
        if st.button(
            "Finalizar relatório",
            key=f"institutional_finalize_{current['id']}",
            disabled=not (eligible and confirmed and not unsaved_text),
        ):
            report = repository.finalize(current["id"], principal.email)
            registrar_evento(
                store,
                evento="RELATORIO_INSTITUCIONAL_FINALIZADO",
                modulo="relatorios",
                acao="FINALIZAR",
                principal=principal,
                entidade_tipo="relatorio_institucional",
                entidade_id=report["id"],
                detalhes={
                    "tipo": tipo,
                    "ano": year,
                    "trimestre": quarter,
                    "versao": report["versao"],
                },
            )
            st.success(
                "Relatório finalizado; o snapshot desta versão permanece congelado."
            )
            st.rerun()


def _render_institutional_safely(store, principal, tipo, year, quarter=None):
    """A complementary report must never interrupt the analytical panel."""
    try:
        _institutional_period_report(store, principal, tipo, year, quarter)
    except Exception:
        LOGGER.exception("Falha ao renderizar relatório institucional")
        st.error(
            "Não foi possível carregar o relatório institucional neste momento. "
            "Os indicadores do período permanecem disponíveis."
        )


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
        index=len(quarters) - 1,
        format_func=lambda value: f"{value}º trimestre",
        key="rel_quarter",
    )
    months = [month for month in available if (quarter - 1) * 3 < month <= quarter * 3]
    monthly = read(
        ("quarter_monthly", year, quarter),
        lambda: reports.monthly_reports(year, months),
    )
    period = read(
        ("quarter_data", year, quarter),
        lambda: reports.period_data(
            f"{year}-{months[0]:02d}-01",
            f"{year + (months[-1] == 12):04d}-{1 if months[-1] == 12 else months[-1] + 1:02d}-01",
        ),
    )
    report, events = period["report"], period["events"]
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
    _render_institutional_safely(store, principal, "TRIMESTRAL", year, quarter)
    _temporal_charts(
        monthly, f"relatorios_trimestral_{year}_t{quarter}", compact_period=True
    )
    st.subheader("Perfil mensal da produção")
    _render_chart(
        f"relatorios_trimestral_{year}_t{quarter}_perfil_mensal",
        lambda: _composition_chart(
            st,
            _monthly_rows(monthly),
            "Mês",
            ["Pareceres", "Cotas"],
            f"relatorios_trimestral_{year}_t{quarter}_perfil_mensal",
        ),
    )
    st.subheader("Distribuição da permanência")
    _render_chart(
        f"relatorios_trimestral_{year}_t{quarter}_permanencia_faixas",
        lambda: _duration_chart(
            st,
            events,
            f"relatorios_trimestral_{year}_t{quarter}_permanencia_faixas",
        ),
    )
    _procurador_comparison(report, f"relatorios_trimestral_{year}_t{quarter}")
    if previous:
        comparison_rows = [
            {
                "Trimestre": f"{prior_quarter}º trimestre",
                "Distribuídos": previous["distributed"],
                "Produção": previous["production"],
                "Pareceres": previous["opinions"],
                "Cotas": previous["quotas"],
            },
            {
                "Trimestre": f"{quarter}º trimestre",
                "Distribuídos": report["summary"]["distributed"],
                "Produção": report["summary"]["production"],
                "Pareceres": report["summary"]["opinions"],
                "Cotas": report["summary"]["quotas"],
            },
        ]
        st.subheader("Comparação com o trimestre anterior")
        _render_chart(
            f"relatorios_trimestral_{year}_t{quarter}_comparacao_anterior",
            lambda: _category_bar_chart(
                st,
                comparison_rows,
                "Trimestre",
                ["Distribuídos", "Produção", "Pareceres", "Cotas"],
                f"relatorios_trimestral_{year}_t{quarter}_comparacao_anterior",
            ),
        )
        st.caption(
            "Mediana de permanência: "
            f"{_number(previous['median_days'], ' dias')} no trimestre anterior e "
            f"{_number(report['summary']['median_days'], ' dias')} no trimestre selecionado."
        )


def annual(store, principal=None):
    reports = TramitaReportsStore(store)
    year, read = _select_year(reports, principal, "rel_annual_year")
    if year is None:
        return
    months = read(("historical_months", year), lambda: reports.months_for_year(year))
    coverage = read(
        ("historical_coverage", year), lambda: reports.historical_coverage(year)
    )
    if coverage["complete_through_last_month"]:
        st.caption(
            f"{year} — acumulado até {MONTHS[coverage['last_month'] - 1].lower()}"
        )
    else:
        st.warning(
            "Cobertura histórica incompleta: existem meses sem dados entre janeiro e "
            f"{MONTHS[coverage['last_month'] - 1].lower()}."
        )
    period = read(
        ("annual_data", year),
        lambda: reports.period_data(f"{year}-01-01", f"{year + 1}-01-01"),
    )
    report, events = period["report"], period["events"]
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
    _render_institutional_safely(store, principal, "ANUAL", year)
    _temporal_charts(monthly, f"relatorios_anual_{year}")
    st.subheader("Evolução acumulada no ano")
    st.caption(
        "As séries representam volumes acumulados de distribuições e produção; a diferença entre elas não corresponde ao estoque processual."
    )
    _render_chart(
        f"relatorios_anual_{year}_acumulado",
        lambda: _line_chart(
            st,
            _cumulative_monthly_rows(monthly),
            ["Distribuídos", "Produção"],
            "Quantidade acumulada",
            f"relatorios_anual_{year}_acumulado",
        ),
    )
    st.subheader("Composição mensal da produção")
    composition_left, composition_right = st.columns(2)
    _render_chart(
        f"relatorios_anual_{year}_composicao_mensal",
        lambda: _category_bar_chart(
            composition_left,
            _monthly_rows(monthly),
            "Mês",
            ["Pareceres", "Cotas"],
            f"relatorios_anual_{year}_composicao_mensal",
            stacked=True,
        ),
    )
    _render_chart(
        f"relatorios_anual_{year}_perfil_mensal",
        lambda: _composition_chart(
            composition_right,
            _monthly_rows(monthly),
            "Mês",
            ["Pareceres", "Cotas"],
            f"relatorios_anual_{year}_perfil_mensal",
        ),
    )
    quarterly = read(("annual_quarters", year), lambda: reports.quarterly_reports(year))
    _quarterly_summary(quarterly, f"relatorios_anual_{year}")
    _procurador_comparison(report, f"relatorios_anual_{year}")
    st.subheader("Mapa mensal da produção")
    _render_chart(
        f"relatorios_anual_{year}_mapa_producao",
        lambda: _production_heatmap(
            st, events, f"relatorios_anual_{year}_mapa_producao"
        ),
    )
    st.subheader("Distribuição da permanência no ano")
    _render_chart(
        f"relatorios_anual_{year}_permanencia_faixas",
        lambda: _duration_chart(
            st, events, f"relatorios_anual_{year}_permanencia_faixas"
        ),
    )


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
        _format_integer(sum(row["n"] for row in summary)),
        _format_integer(len(people)),
        _format_decimal_br(days / timed, " dias") if timed else "—",
        _format_integer(sum(row["over30"] for row in summary)),
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
    stock_visuals = read(
        ("stock_visual_data", snapshot), lambda: reports.stock_visual_data(snapshot)
    )
    st.subheader("Permanência do estoque atual")
    _render_chart(
        "relatorios_estoque_faixas",
        lambda: _stock_band_chart(st, stock_visuals, "relatorios_estoque_faixas"),
    )
    st.subheader("Permanência por Procurador")
    st.caption(
        "O gráfico representa a permanência dos processos atualmente vinculados a cada Procurador e não constitui indicador isolado de desempenho."
    )
    _render_chart(
        "relatorios_estoque_por_procurador",
        lambda: _stock_by_procurador_chart(
            st, stock_visuals, "relatorios_estoque_por_procurador"
        ),
    )
    st.subheader("Maiores permanências atuais")
    _render_chart(
        "relatorios_estoque_maiores_permanencias",
        lambda: _stock_top_chart(
            st, stock_visuals, "relatorios_estoque_maiores_permanencias"
        ),
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
        "Reconcilia os 18 relatórios de janeiro a setembro de 2026. O hash é auditável; "
        "a proteção contra duplicidade é a identidade de cada evento. Depois, a aplicação consulta somente a base local."
    )
    source_dir = Path(__file__).resolve().parents[1] / "referencias"
    if source_dir.exists():
        if st.button("Reconciliar histórico de janeiro a setembro de 2026"):
            results = import_reference_reports(store, source_dir, principal.email)
            imported = sum(item["inserted"] for item in results)
            duplicates = sum(item["duplicates"] for item in results)
            skipped = sum(item["already_imported"] for item in results)
            registrar_evento(
                store,
                evento="TRAMITA_HISTORICO_RECONCILIADO",
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
                f"Histórico reconciliado: {imported} eventos inseridos e {duplicates} já existentes."
            )
            st.rerun()
    else:
        st.info(
            "A fonte de migração não está disponível neste ambiente. O histórico já importado continua funcionando normalmente."
        )
    st.subheader("Cobertura histórica")
    available_years = reports.historical_years()
    coverage_years = sorted({2026, *available_years}, reverse=True)
    coverage_year = st.selectbox(
        "Ano da cobertura", coverage_years, key="rel_coverage_year"
    )
    coverage = reports.historical_coverage(coverage_year)
    if coverage["last_month"]:
        coverage_rows = _monthly_rows(
            reports.monthly_reports(
                coverage_year, list(range(1, coverage["last_month"] + 1))
            )
        )
        _report_table(
            st,
            [
                {
                    key: row[key]
                    for key in ("Mês", "Distribuídos", "Produção", "Pareceres", "Cotas")
                }
                for row in coverage_rows
            ],
        )
        if not coverage["complete_through_last_month"]:
            st.caption(
                "Cobertura histórica incompleta: existem meses sem dados entre janeiro e "
                f"{MONTHS[coverage['last_month'] - 1].lower()}."
            )
    else:
        st.caption(
            "Ainda não há eventos históricos normalizados para o ano selecionado."
        )
    with st.expander("Diagnóstico de eventos importados"):
        audit_rows = reports.historical_audit(coverage_year)
        if audit_rows:
            _report_table(
                st,
                [
                    {
                        "Mês": row["period"],
                        "Origem": (
                            "Referência histórica reconciliada"
                            if row["origem_historica"] == "REFERENCIA_TRAMITA_2026"
                            else "Legado ou importação não reconciliada"
                        ),
                        "Movimentação": row["tipo_movimentacao"],
                        "Classificação de produção": row["classificacao_producao"]
                        or "Não produtiva / distribuição",
                        "Eventos": row["total"],
                    }
                    for row in audit_rows
                ],
            )
        else:
            st.caption("Não há eventos importados para o ano selecionado.")
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
