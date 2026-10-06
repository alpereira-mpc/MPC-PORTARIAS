"""Interface administrativa de Relatórios e Indicadores."""

import logging
import math
import re
import hashlib
from uuid import uuid4
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
    delete_institutional_report_period,
    delete_editable_report_version,
    deletion_eligibility,
    report_workflow_state,
    can_finalize,
)
from services.institutional_report_content import (
    AI_SECTIONS,
    SECTIONS,
    InstitutionalReportContentService,
    normalize_content,
    validate_numbers,
)
from services.themes import DEFAULT_THEME, theme_tokens
from services.tramita_reports import (
    file_hash,
    import_reference_reports,
    parse_movements,
    parse_stock,
    turnaround_days,
)
from services.ui_theme import (
    empty_state,
    filter_mark,
    html_text,
    institutional_card_mark,
    kpi_mark,
    render_html,
    section_label,
)


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


def style_report_table(rows, theme_name=DEFAULT_THEME):
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
    selected_theme = st.session_state.get("_portal_theme", {}).get("name", DEFAULT_THEME)
    target.dataframe(
        style_report_table(rows, selected_theme),
        hide_index=True,
        width="stretch",
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
        width="stretch",
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
        width="stretch",
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
        width="stretch",
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
        width="stretch",
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
        width="stretch",
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
        width="stretch",
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
        width="stretch",
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
        width="stretch",
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
        width="stretch",
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
            width="stretch",
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


def _institutional_title(snapshot, *, formal=False):
    metadata = snapshot.get("metadados") or {}
    if metadata.get("tipo") == "TRIMESTRAL":
        return (
            "RELATÓRIO TRIMESTRAL DE PRODUÇÃO"
            if formal
            else "Relatório Trimestral de Produção"
        )
    if formal:
        return "RELATÓRIO ANUAL DE PRODUÇÃO"
    return f"Relatório Anual de Produção — {metadata.get('ano')}"


def _institutional_period_label(snapshot):
    """Human period from frozen coverage. Stored wording is left untouched."""
    from services.institutional_presentation import format_report_period

    return format_report_period(snapshot)


def _institutional_status_label(status):
    return INSTITUTIONAL_STATUS_LABELS.get(status, status or "—")


def _version_label(report):
    return f"{report['versao']} - {_institutional_status_label(report['status'])}"


def _drop_invalid_widget(key, valid):
    """Forget a widget value before it is instantiated, never after."""
    if key in st.session_state and st.session_state[key] not in valid:
        del st.session_state[key]


def _period_token(tipo, year, quarter):
    return f"{tipo}_{year}_{0 if quarter is None else quarter}"


def _text_widget_key(report_id, section):
    return (
        f"inst_text_{report_id}_{section}_"
        f"{_editor_epoch(report_id)}_{_section_epoch(report_id, section)}"
    )


def _editor_epoch_key(report_id):
    return f"institutional_editor_epoch_{report_id}"


def _section_epoch_key(report_id, section):
    return f"institutional_section_epoch_{report_id}_{section}"


def _editor_epoch(report_id):
    return int(st.session_state.get(_editor_epoch_key(report_id), 0))


def _section_epoch(report_id, section):
    return int(st.session_state.get(_section_epoch_key(report_id, section), 0))


def _advance_editor_epoch(report_id):
    """Make every text widget for a report a new Streamlit identity."""
    st.session_state[_editor_epoch_key(report_id)] = _editor_epoch(report_id) + 1


def _advance_section_epoch(report_id, section):
    """Refresh only one generated section, retaining other local drafts."""
    key = _section_epoch_key(report_id, section)
    st.session_state[key] = _section_epoch(report_id, section) + 1


def _prefer_latest_version():
    """Ask the next run to select the newest version before the widget exists."""
    st.session_state["inst_prefer_latest"] = True


def _editable_tail(versions):
    """Newest editable versions, stopping at the first frozen version."""
    tail = []
    for item in versions or []:
        if item.get("status") not in ("RASCUNHO", "EM_REVISAO"):
            break
        tail.append(item)
    return tail


def _forget_deleted_reports(identifiers):
    """Clear the deleted ids on the next run, before their widgets are created."""
    pending = list(st.session_state.get("inst_forget_reports") or [])
    for identifier in identifiers:
        if identifier not in pending:
            pending.append(identifier)
    st.session_state["inst_forget_reports"] = pending
    st.session_state.pop("inst_delete_pending", None)
    st.session_state.pop("inst_ai_replace_pending", None)
    st.session_state["inst_return_to_created_after_delete"] = True
    _prefer_latest_version()


def _apply_forgotten_reports():
    identifiers = st.session_state.pop("inst_forget_reports", None) or []
    return_to_created = st.session_state.pop(
        "inst_return_to_created_after_delete", False
    )
    doomed = []
    for identifier in identifiers:
        token = str(identifier)
        prefixes = (
            f"inst_text_{token}_",
            f"inst_regen_{token}_",
        )
        exact = {
            f"inst_ai_all_{token}",
            f"inst_ai_replace_confirm_{token}",
            f"inst_ai_replace_cancel_{token}",
            f"inst_save_{token}",
            f"inst_review_{token}",
            f"inst_confirm_{token}",
            f"inst_new_{token}",
            f"inst_finalize_{token}",
            f"inst_pdf_generate_{token}",
            f"inst_pdf_download_{token}",
            f"inst_pdf_preview_generate_{token}",
            f"inst_pdf_preview_download_{token}",
            f"inst_delete_ask_header_{token}",
            f"inst_delete_ask_versions_{token}",
            f"inst_delete_confirm_{token}",
            f"inst_delete_cancel_{token}",
            f"institutional_governance_attention_{token}",
            f"institutional_governance_pdf_{token}",
            f"institutional_governance_pdf_result_{token}",
        }
        for key in list(st.session_state.keys()):
            text = str(key)
            if text in exact or text.startswith(prefixes):
                doomed.append(key)
    for key in doomed:
        st.session_state.pop(key, None)
    preview = st.session_state.get("inst_pdf_preview")
    if isinstance(preview, dict) and any(
        str(preview.get("signature", "")).startswith(f"{identifier}:")
        for identifier in identifiers
    ):
        st.session_state.pop("inst_pdf_preview", None)
    if return_to_created:
        st.session_state.pop("inst_selected_period", None)
        st.session_state.pop("inst_selected_version_id", None)
        st.session_state.pop("inst_versao", None)
        st.session_state.pop("inst_version_history", None)
        st.session_state.pop("inst_exibicao", None)
        st.session_state.pop("inst_delete_pending", None)
        st.session_state["inst_home_section"] = "Relatórios criados"


def _delete_identity(report):
    snapshot = report.get("snapshot_dados") or {}
    kind = (
        "Relatório trimestral"
        if report.get("tipo") == "TRIMESTRAL"
        else "Relatório anual"
    )
    return (
        f"{kind} · {_institutional_period_label(snapshot)} · "
        f"Versão {report.get('versao')} · "
        f"{_institutional_status_label(report.get('status'))}"
    )


def _render_delete_confirmation(store, principal, versions):
    """Second step only. The first button never deletes."""
    pending = st.session_state.get("inst_delete_pending")
    target = next((item for item in versions or [] if item.get("id") == pending), None)
    if pending and target is None:
        st.session_state.pop("inst_delete_pending", None)
    if target:
        st.warning(
            "Esta versão será excluída permanentemente. Esta ação não pode ser desfeita."
        )
        st.caption(_delete_identity(target))
        if target.get("status") == "FINALIZADO":
            st.caption(
                "Esta versão está finalizada, mas não possui PDF oficial nem distribuição."
            )
        confirm, cancel = st.columns(2)
        if confirm.button(
            "Confirmar exclusão definitiva", key=f"inst_delete_confirm_{target['id']}"
        ):
            try:
                removed = delete_editable_report_version(store, target["id"], principal)
            except (ValueError, PermissionError) as exc:
                st.session_state.pop("inst_delete_pending", None)
                st.error(str(exc))
            else:
                _forget_deleted_reports([removed["id"]])
                _flash("success", f"Versão {removed['versao']} excluída.")
                st.rerun()
        if cancel.button("Cancelar", key=f"inst_delete_cancel_{target['id']}"):
            st.session_state.pop("inst_delete_pending", None)
            st.rerun()


def _render_delete_buttons(report, versions, *, placement, repository=None):
    """Offer one administrative deletion path only when policy allows it."""
    if not versions:
        return
    latest = versions[0]
    if report and report.get("id") == latest.get("id"):
        eligible = latest.get("status") in ("RASCUNHO", "EM_REVISAO", "FINALIZADO")
        if repository is not None and eligible:
            try:
                deletion_eligibility(repository, latest["id"])
            except ValueError:
                eligible = False
        if not eligible:
            return
        label = (
            "Excluir relatório em elaboração"
            if latest.get("status") in ("RASCUNHO", "EM_REVISAO")
            else "Excluir versão não publicada"
        )
        if st.button(label, key=f"inst_delete_ask_{placement}_{latest['id']}"):
            st.session_state["inst_delete_pending"] = latest["id"]
            st.rerun()


def _flash(level, message):
    st.session_state["inst_flash"] = (level, message)


def _consume_flash():
    item = st.session_state.pop("inst_flash", None)
    if not item:
        return
    level, message = item
    getattr(st, level)(message)


def _text_sync_value(value):
    text = str(value or "")
    return len(text), hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _log_text_sync(report, content, section="resumo_executivo"):
    """Diagnose DB → normalized content → widget state without exposing prose."""
    stored = (report.get("conteudo_estruturado") or {}).get(section, "")
    if isinstance(stored, dict):
        stored = stored.get("texto") or ""
    normalized = (content.get(section) or {}).get("texto") or ""
    widget_key = _text_widget_key(report["id"], section)
    session_exists = widget_key in st.session_state
    session_value = st.session_state.get(widget_key, "")
    db_len, db_hash = _text_sync_value(stored)
    normalized_len, normalized_hash = _text_sync_value(normalized)
    session_len, session_hash = _text_sync_value(session_value)
    LOGGER.info(
        "RELATORIO_IA | etapa=text_sync | report_id=%s | secao=%s | "
        "db_len=%s | db_hash=%s | normalized_len=%s | normalized_hash=%s | "
        "widget_key=%s | editor_epoch=%s | section_epoch=%s | "
        "widget_value_len=%s | widget_hash=%s | session_exists=%s",
        report["id"],
        section,
        db_len,
        db_hash,
        normalized_len,
        normalized_hash,
        widget_key,
        _editor_epoch(report["id"]),
        _section_epoch(report["id"], section),
        session_len,
        session_hash,
        session_exists,
    )


def _initialize_text_widget(report_id, section, persisted_text):
    """Seed a fresh widget identity from the persisted source of truth."""
    widget_key = _text_widget_key(report_id, section)
    if widget_key not in st.session_state:
        st.session_state[widget_key] = persisted_text or ""
    return widget_key


def _texts_from_state(report_id, content):
    return {
        key: st.session_state.get(
            _text_widget_key(report_id, key), section.get("texto") or ""
        )
        for key, section in content.items()
    }


def _has_unsaved_text(report_id, content):
    return any(
        st.session_state.get(
            _text_widget_key(report_id, key), section.get("texto") or ""
        )
        != (section.get("texto") or "")
        for key, section in content.items()
    )


def _safe_monthly_rows(snapshot):
    """Chart rows copied from the frozen series. Incomplete months are omitted."""
    required = (
        "distributed",
        "production",
        "opinions",
        "quotas",
        "median_days",
        "production_rate",
    )
    usable = []
    for item in snapshot.get("serie_mensal") or []:
        summary = item.get("summary") if isinstance(item, dict) else None
        month = item.get("month") if isinstance(item, dict) else None
        if not summary or not month:
            continue
        if any(key not in summary for key in required):
            continue
        usable.append({"month": int(month), "summary": summary})
    return _monthly_rows(usable)


def _snapshot_procurador_rows(snapshot):
    rows = []
    for row in snapshot.get("por_procurador") or []:
        if not isinstance(row, dict):
            continue
        rows.append(
            {
                "Procurador": row.get("procurador") or "Não identificado",
                "Distribuídos": row.get("distributed"),
                "Produção": row.get("production"),
                "Pareceres": row.get("opinions"),
                "Cotas": row.get("quotas"),
                "Produção/Distribuições": row.get("production_rate"),
                "Mediana de permanência": row.get("median_days"),
            }
        )
    return rows


def _comparison_rows(snapshot):
    """Pair the frozen current summary with the frozen previous period, if any."""
    prior = snapshot.get("comparacao_periodo_anterior")
    summary = snapshot.get("indicadores_gerais") or {}
    metadata = snapshot.get("metadados") or {}
    if not isinstance(prior, dict):
        return None
    needed = ("distributed", "production", "opinions", "quotas")
    if any(key not in prior or key not in summary for key in needed):
        return None
    if metadata.get("tipo") == "TRIMESTRAL":
        quarter = int(metadata.get("trimestre") or 0)
        prior_quarter = quarter - 1 if quarter > 1 else 4
        prior_label = f"{prior_quarter}º trimestre"
        current_label = f"{quarter}º trimestre"
    else:
        year = int(metadata.get("ano") or 0)
        prior_label = str(year - 1)
        current_label = str(year)

    def pack(label, source):
        return {
            "Período": label,
            "Distribuídos": source["distributed"],
            "Produção": source["production"],
            "Pareceres": source["opinions"],
            "Cotas": source["quotas"],
        }

    return [pack(prior_label, prior), pack(current_label, summary)]


def _render_table_safely(rows, empty):
    if not rows:
        st.caption(empty)
        return
    try:
        _report_table(st, rows)
    except Exception:
        LOGGER.exception("Falha ao renderizar tabela do relatório institucional")
        st.warning("Não foi possível exibir esta tabela do relatório.")


def _render_frozen_bands(target, bands, key):
    rows = []
    for item in bands or []:
        if not isinstance(item, dict):
            continue
        label = item.get("faixa")
        quantity = item.get("quantidade")
        if not label or quantity is None:
            continue
        rows.append({"Faixa": str(label), "Quantidade": quantity})
    if not rows:
        return
    target.vega_lite_chart(
        rows,
        {
            "mark": "bar",
            "encoding": {
                "x": {
                    "field": "Faixa",
                    "type": "ordinal",
                    "sort": [row["Faixa"] for row in rows],
                    "axis": {"title": "Faixa de permanência", "labelAngle": 0},
                },
                "y": {
                    "field": "Quantidade",
                    "type": "quantitative",
                    "axis": {"title": "Quantidade", "format": ".0f"},
                },
                "color": {"value": CHART_COLORS["Mediana de permanência"]},
                "tooltip": [
                    {"field": "Faixa", "type": "nominal"},
                    {"field": "Quantidade", "type": "quantitative", "format": ".0f"},
                ],
            },
        },
        width="stretch",
        key=key,
    )


_HTML_TAG = re.compile(r"</?[^>\n]+>")


def _document_prose(content, key, empty):
    text = ""
    section = content.get(key) if isinstance(content, dict) else None
    if isinstance(section, dict):
        text = section.get("texto") or ""
    text = _HTML_TAG.sub("", str(text))
    if text.strip():
        st.markdown(text)
    else:
        st.caption(empty)


def _snapshot_indicator_cards(summary):
    from services.institutional_presentation import (
        format_count,
        format_days,
        format_percent,
    )

    def shown(value, formatter):
        formatted = formatter(value)
        return formatted if formatted is not None else "—"

    labels = (
        ("Distribuídos", shown(summary["distributed"], format_count)),
        ("Produção", shown(summary["production"], format_count)),
        ("Pareceres", shown(summary["opinions"], format_count)),
        ("Cotas", shown(summary["quotas"], format_count)),
        ("Produção/Distribuições", shown(summary["production_rate"], format_percent)),
        ("Mediana de permanência", shown(summary["median_days"], format_days)),
    )
    for column, (label, value) in zip(st.columns(len(labels)), labels):
        with column:
            kpi_mark("brand")
            st.metric(label, value)


def _render_institutional_pdf_action(store, principal, report):
    """Render a real one-click download when an official artifact exists."""
    official = report.get("status") in ("FINALIZADO", "ENVIADO")
    repository = InstitutionalReportsStore(store)
    artifact = repository.pdf_artifact(report["id"]) if official else None
    if artifact:
        def audit_download():
            try:
                # Keep the existing audit event and stored artifact semantics.
                from services.institutional_report_pdf import deliver_institutional_pdf

                deliver_institutional_pdf(store, report, principal, official=True)
            except Exception:
                LOGGER.exception("Falha ao registrar download do PDF institucional")

        st.download_button(
            "Baixar PDF",
            artifact["conteudo"],
            artifact["nome_arquivo"],
            mime="application/pdf",
            key=f"inst_pdf_download_{report.get('id')}",
            on_click=audit_download,
        )
        return
    if official:
        if st.button("Gerar PDF", key=f"inst_pdf_generate_{report.get('id')}"):
            try:
                from services.institutional_report_pdf import deliver_institutional_pdf

                deliver_institutional_pdf(store, report, principal, official=True)
            except Exception:
                LOGGER.exception(
                    "Falha ao gerar o PDF do relatório institucional %s versão %s",
                    report.get("id"),
                    report.get("versao"),
                )
                st.error("Não foi possível gerar o PDF desta versão no momento.")
            else:
                st.rerun()
        return
    signature = f"{report.get('id')}:{report.get('versao')}:{report.get('status')}"
    preview = st.session_state.get("inst_pdf_preview")
    if preview and preview.get("signature") == signature:
        st.download_button(
            "Baixar prévia em PDF",
            preview["data"],
            preview["name"],
            mime="application/pdf",
            key=f"inst_pdf_preview_download_{report.get('id')}",
        )
        return
    if st.button("Gerar prévia em PDF", key=f"inst_pdf_preview_generate_{report.get('id')}"):
        try:
            from services.institutional_report_pdf import deliver_institutional_pdf

            payload = deliver_institutional_pdf(store, report, principal, official=False)
        except Exception:
            LOGGER.exception("Falha ao gerar prévia do relatório institucional")
            st.error("Não foi possível gerar a prévia em PDF neste momento.")
        else:
            st.session_state["inst_pdf_preview"] = {
                "signature": signature,
                "data": payload["conteudo"],
                "name": payload["nome"],
            }
            st.rerun()


def _render_institutional_document(report):
    """Phase 3 document. Uses only this version's snapshot and persisted prose."""
    snapshot = report.get("snapshot_dados") or {}
    metadata = snapshot.get("metadados") or {}
    content = normalize_content(report.get("conteudo_estruturado"), snapshot)
    summary = snapshot.get("indicadores_gerais") or {}
    annual = metadata.get("tipo") == "ANUAL"
    report_id = report.get("id") or "preview"
    render_html(
        '<div style="border-top:3px solid var(--mpc-brand,#8E1B2C);'
        'padding:4px 0 8px;margin:4px 0 12px">'
        '<p style="letter-spacing:.12em;font-size:.78rem;margin:0;'
        'color:var(--mpc-text-secondary,#5C6570)">MPC-PB</p>'
        '<p style="font-weight:700;margin:2px 0 0;color:var(--mpc-text-primary,#202832)">'
        "MINISTÉRIO PÚBLICO DE CONTAS DO ESTADO DA PARAÍBA</p>"
        '<p style="font-size:1.2rem;font-weight:700;margin:10px 0 0">'
        f"{html_text(_institutional_title(snapshot, formal=True))}</p>"
        '<p style="margin:4px 0 0">'
        f"{html_text(_institutional_period_label(snapshot))}</p>"
        '<p style="margin:8px 0 0;font-size:.85rem;color:var(--mpc-text-secondary,#5C6570)">'
        f"Versão {html_text(report.get('versao'))} · "
        f"{html_text(_institutional_status_label(report.get('status')))} · "
        f"Data de corte: {html_text(format_datetime_br(report.get('data_corte')))}"
        "</p></div>"
    )

    st.subheader("Resumo executivo")
    _document_prose(
        content, "resumo_executivo", "Resumo executivo ainda não elaborado."
    )

    st.subheader("Indicadores do período")
    required = (
        "distributed",
        "production",
        "opinions",
        "quotas",
        "production_rate",
        "median_days",
    )
    if all(key in summary for key in required):
        _snapshot_indicator_cards(summary)
    else:
        st.caption("Os indicadores deste snapshot estão incompletos.")

    st.subheader("Evolução do período")
    _document_prose(
        content, "evolucao_periodo", "Evolução do período ainda não elaborada."
    )
    monthly = _safe_monthly_rows(snapshot)
    if not monthly:
        st.caption("A série mensal deste snapshot não está disponível.")
    elif annual:
        left, right = st.columns(2)
        _render_chart(
            f"inst_doc_{report_id}_fluxo",
            lambda: _line_chart(
                left,
                monthly,
                ["Distribuídos", "Produção"],
                "Quantidade",
                f"inst_doc_{report_id}_fluxo",
            ),
        )
        _render_chart(
            f"inst_doc_{report_id}_tipos",
            lambda: _category_bar_chart(
                right,
                monthly,
                "Mês",
                ["Pareceres", "Cotas"],
                f"inst_doc_{report_id}_tipos",
                stacked=True,
            ),
        )
    else:
        left, right = st.columns(2)
        _render_chart(
            f"inst_doc_{report_id}_fluxo",
            lambda: _category_bar_chart(
                left,
                monthly,
                "Mês",
                ["Distribuídos", "Produção"],
                f"inst_doc_{report_id}_fluxo",
            ),
        )
        _render_chart(
            f"inst_doc_{report_id}_tipos",
            lambda: _category_bar_chart(
                right,
                monthly,
                "Mês",
                ["Pareceres", "Cotas"],
                f"inst_doc_{report_id}_tipos",
                stacked=True,
            ),
        )

    st.subheader("Composição da produção")
    _document_prose(
        content, "composicao_producao", "Composição da produção ainda não elaborada."
    )
    composition = snapshot.get("composicao_producao") or {}
    if "opinions" in summary and "quotas" in summary:
        _render_chart(
            f"inst_doc_{report_id}_composicao",
            lambda: _category_bar_chart(
                st,
                [
                    {
                        "Composição": "Produção",
                        "Pareceres": summary["opinions"],
                        "Cotas": summary["quotas"],
                    }
                ],
                "Composição",
                ["Pareceres", "Cotas"],
                f"inst_doc_{report_id}_composicao",
                stacked=True,
            ),
        )
    opinions_pct = (
        composition.get("pareceres_percentual")
        if isinstance(composition, dict)
        else None
    )
    quotas_pct = (
        composition.get("cotas_percentual") if isinstance(composition, dict) else None
    )
    if opinions_pct is not None or quotas_pct is not None:
        st.caption(
            "Composição percentual registrada: "
            f"Pareceres {_format_decimal_br(opinions_pct, '%')} · "
            f"Cotas {_format_decimal_br(quotas_pct, '%')}."
        )

    st.subheader("Permanência")
    _document_prose(content, "permanencia", "Permanência ainda não elaborada.")
    if "median_days" in summary:
        st.metric(
            "Mediana de permanência",
            _format_decimal_br(summary["median_days"], " dias"),
        )
    bands = snapshot.get("faixas_permanencia") or []
    if bands:
        _render_chart(
            f"inst_doc_{report_id}_faixas",
            lambda: _render_frozen_bands(st, bands, f"inst_doc_{report_id}_faixas"),
        )
    if monthly:
        _render_chart(
            f"inst_doc_{report_id}_mediana",
            lambda: _line_chart(
                st,
                monthly,
                ["Mediana de permanência"],
                "Mediana de permanência (dias)",
                f"inst_doc_{report_id}_mediana",
            ),
        )

    st.subheader("Produção por Procurador")
    _document_prose(
        content,
        "producao_procurador",
        "Produção por Procurador ainda não elaborada.",
    )
    procuradores = _snapshot_procurador_rows(snapshot)
    _render_table_safely(procuradores, "Não há produção por Procurador neste snapshot.")
    if procuradores:
        left, right = st.columns(2)
        _render_chart(
            f"inst_doc_{report_id}_proc_fluxo",
            lambda: _procurador_chart(
                left,
                procuradores,
                ["Distribuídos", "Produção"],
                False,
                f"inst_doc_{report_id}_proc_fluxo",
            ),
        )
        _render_chart(
            f"inst_doc_{report_id}_proc_tipos",
            lambda: _procurador_chart(
                right,
                procuradores,
                ["Pareceres", "Cotas"],
                True,
                f"inst_doc_{report_id}_proc_tipos",
            ),
        )

    st.subheader("Comparação com período anterior")
    comparison_section = content.get("comparacao_periodo_anterior")
    comparison_text = (
        comparison_section.get("texto") if isinstance(comparison_section, dict) else ""
    )
    if str(comparison_text or "").strip():
        st.markdown(comparison_text)
    comparison = _comparison_rows(snapshot)
    if not str(comparison_text or "").strip() and comparison:
        st.caption("Comparação com período anterior ainda não elaborada.")
    if comparison:
        _render_chart(
            f"inst_doc_{report_id}_comparacao",
            lambda: _category_bar_chart(
                st,
                comparison,
                "Período",
                ["Distribuídos", "Produção", "Pareceres", "Cotas"],
                f"inst_doc_{report_id}_comparacao",
            ),
        )
        prior = snapshot.get("comparacao_periodo_anterior") or {}
        if (
            prior.get("median_days") is not None
            and summary.get("median_days") is not None
        ):
            st.caption(
                "Mediana de permanência: "
                f"{_format_decimal_br(prior['median_days'], ' dias')} no período anterior e "
                f"{_format_decimal_br(summary['median_days'], ' dias')} no período selecionado."
            )
    elif annual:
        st.caption("Não há período anual anterior disponível para comparação.")
    else:
        st.caption("Não há trimestre anterior disponível para comparação.")

    st.subheader("Síntese do período")
    _document_prose(
        content,
        "sintese_pontos_atencao",
        "Síntese do período ainda não elaborada.",
    )

    with st.expander("Nota metodológica"):
        _document_prose(
            content,
            "nota_metodologica",
            "Nota metodológica ainda não registrada.",
        )


def _create_institutional_version(
    store, repository, principal, tipo, year, quarter, *, new_version
):
    snapshot = build_report_snapshot(store, tipo=tipo, ano=year, trimestre=quarter)
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
        if new_version
        else repository.create(**params)
    )
    registrar_evento(
        store,
        evento=(
            "RELATORIO_INSTITUCIONAL_NOVA_VERSAO"
            if new_version
            else "RELATORIO_INSTITUCIONAL_CRIADO"
        ),
        modulo="relatorios",
        acao="CRIAR_NOVA_VERSAO" if new_version else "CRIAR",
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
    return report


AI_ICON = ":material/auto_awesome:"
AI_GENERATE_LABEL = "Gerar conteúdo com IA"
AI_REGENERATE_LABEL = "Regenerar com IA"
AI_GENERATE_SUCCESS = "Conteúdo gerado com IA com sucesso."
AI_REGENERATE_SUCCESS = "Seção regenerada com IA com sucesso."
AI_GENERATE_FAILURE = "Não foi possível gerar o conteúdo com IA neste momento."
AI_REGENERATE_FAILURE = "Não foi possível regenerar esta seção com IA neste momento."
_AI_ACTION_KEY = "institutional_ai_action"
_AI_FLASH_KEY = "institutional_ai_flash"
_AI_LAST_ATTEMPT_KEY = "institutional_ai_last_attempt"


def _ai_button(container, label, key, *, disabled=False, on_click=None):
    """Material icon in the button slot. The label itself stays plain text."""
    return container.button(
        label, key=key, icon=AI_ICON, disabled=disabled, on_click=on_click
    )


def _comparison_can_regenerate(report):
    from services.institutional_presentation import comparison_available

    return comparison_available(report.get("snapshot_dados"))


def _set_ai_attempt(**values):
    st.session_state[_AI_LAST_ATTEMPT_KEY] = {
        "quando": datetime.now().isoformat(timespec="seconds"),
        **values,
    }


def _render_last_institutional_ai_attempt(principal, report_id):
    if not getattr(principal, "administrator", False):
        return
    item = st.session_state.get(_AI_LAST_ATTEMPT_KEY) or {}
    if item.get("report_id") != report_id:
        return
    with st.expander("Diagnóstico técnico da IA"):
        st.caption(
            "Última tentativa de IA · "
            f"{format_datetime_br(item.get('quando')) or item.get('quando')} · "
            f"{item.get('operacao')} · etapa: {item.get('etapa')} · "
            f"resultado: {item.get('resultado')}"
        )
        if item.get("modelo"):
            st.caption(f"Modelo: {item['modelo']}")
        if item.get("erro"):
            st.caption(f"Erro: {item['erro']}")


def _request_institutional_ai_action(report, action, *, section=None):
    """Callback only: retain an intent through Streamlit's next script run."""
    current = st.session_state.get(_AI_ACTION_KEY)
    if current and current.get("status") in ("PENDING", "RUNNING"):
        LOGGER.info("RELATORIO_IA | etapa=click_ignored | report_id=%s", report["id"])
        return
    drafts = {}
    if action == "REGENERATE_SECTION":
        for candidate, _label in SECTIONS:
            if candidate == section:
                continue
            widget_key = _text_widget_key(report["id"], candidate)
            if widget_key in st.session_state:
                drafts[candidate] = st.session_state[widget_key]
    payload = {
        "action": action,
        "report_id": report["id"],
        "version": report["versao"],
        "section": section,
        "nonce": uuid4().hex,
        "status": "PENDING",
        "drafts": drafts,
    }
    st.session_state[_AI_ACTION_KEY] = payload
    _set_ai_attempt(
        report_id=report["id"],
        operacao=action,
        etapa="action_pending",
        resultado="PENDENTE",
        modelo="",
        erro="",
    )
    LOGGER.info(
        "RELATORIO_IA | etapa=click_requested | report_id=%s | operacao=%s | secao=%s",
        report["id"],
        action,
        section or "",
    )
    LOGGER.info("RELATORIO_IA | etapa=action_pending | report_id=%s", report["id"])


def _render_institutional_ai_flash(report_id):
    flash = st.session_state.get(_AI_FLASH_KEY)
    if not flash or flash.get("report_id") != report_id:
        return
    st.session_state.pop(_AI_FLASH_KEY, None)
    getattr(st, flash["level"])(flash["message"])
    if flash.get("detail"):
        st.caption(flash["detail"])
    LOGGER.info(
        "RELATORIO_IA | etapa=render_after_%s | report_id=%s", flash["level"], report_id
    )


def _process_pending_institutional_ai_action(service, principal, report):
    """Execute exactly one persisted AI intent, then rerun to render its result."""
    action = st.session_state.get(_AI_ACTION_KEY)
    if not action:
        return
    if (
        action.get("report_id") != report["id"]
        or action.get("version") != report["versao"]
    ):
        LOGGER.info("RELATORIO_IA | etapa=action_cancelled | motivo=troca_relatorio")
        st.session_state.pop(_AI_ACTION_KEY, None)
        return
    if action.get("status") != "PENDING":
        return
    action["status"] = "RUNNING"
    st.session_state[_AI_ACTION_KEY] = action
    operation = action["action"]
    section = action.get("section")
    if operation == "GENERATE_ALL":
        call = lambda: service.generate_all(report["id"], principal)
        success, waiting = (
            AI_GENERATE_SUCCESS,
            "Gerando conteúdo institucional com IA...",
        )
    elif operation == "REGENERATE_SECTION" and section in AI_SECTIONS:
        call = lambda: service.regenerate(report["id"], section, principal)
        success, waiting = (
            AI_REGENERATE_SUCCESS,
            "Regenerando a seção com IA...",
        )
    else:
        st.session_state.pop(_AI_ACTION_KEY, None)
        return
    LOGGER.info(
        "RELATORIO_IA | etapa=action_processing | report_id=%s | operacao=%s",
        report["id"],
        operation,
    )
    _set_ai_attempt(
        report_id=report["id"],
        operacao=operation,
        etapa="action_processing",
        resultado="EXECUTANDO",
        modelo="",
        erro="",
    )
    try:
        with st.spinner(waiting):
            updated = call()
    except ai_service.GeminiNaoConfigurada:
        LOGGER.exception("RELATORIO_IA | etapa=error | report_id=%s", report["id"])
        detail = "Serviço de IA não configurado neste ambiente."
    except ai_service.GeminiErro as exc:
        LOGGER.exception("RELATORIO_IA | etapa=error | report_id=%s", report["id"])
        detail = str(exc)
    except (ValueError, PermissionError) as exc:
        LOGGER.exception("RELATORIO_IA | etapa=error | report_id=%s", report["id"])
        detail = str(exc)
    except Exception:
        LOGGER.exception("RELATORIO_IA | etapa=error | report_id=%s", report["id"])
        detail = "Ocorreu um erro inesperado."
    else:
        # Read the saved row again.  The following epoch identifies a new
        # widget, so a stale Streamlit value can never mask this persisted text.
        updated = service.reports.get(report["id"])
        if updated is None:
            raise ValueError("Relatório não encontrado após salvar o conteúdo.")
        if operation == "GENERATE_ALL":
            _advance_editor_epoch(report["id"])
        else:
            _advance_section_epoch(report["id"], section)
            for candidate, draft in (action.get("drafts") or {}).items():
                if candidate != section:
                    st.session_state[_text_widget_key(report["id"], candidate)] = draft
        _log_text_sync(
            updated,
            normalize_content(
                updated.get("conteudo_estruturado"), updated.get("snapshot_dados")
            ),
        )
        st.session_state[_AI_FLASH_KEY] = {
            "report_id": report["id"],
            "level": "success",
            "message": success,
            "detail": "",
        }
        _set_ai_attempt(
            report_id=report["id"],
            operacao=operation,
            etapa="persist_success",
            resultado="SUCESSO",
            modelo=next(
                (
                    item.get("modelo")
                    for item in (updated.get("conteudo_estruturado") or {}).values()
                    if isinstance(item, dict) and item.get("modelo")
                ),
                "",
            ),
            erro="",
        )
        st.session_state.pop(_AI_ACTION_KEY, None)
        LOGGER.info("RELATORIO_IA | etapa=rerun_requested | report_id=%s", report["id"])
        st.rerun()
    st.session_state[_AI_FLASH_KEY] = {
        "report_id": report["id"],
        "level": "error",
        "message": "Não foi possível gerar o conteúdo com IA.",
        "detail": detail,
    }
    _set_ai_attempt(
        report_id=report["id"],
        operacao=operation,
        etapa="error",
        resultado="ERRO",
        modelo="",
        erro=detail,
    )
    st.session_state.pop(_AI_ACTION_KEY, None)
    st.rerun()


def _remember_open_editor():
    """Next run selects the latest version and opens Conteúdo e revisão."""
    _prefer_latest_version()
    st.session_state["inst_open_editor"] = True


def _latest_open_version(report, versions):
    """Editable successor of a closed version, when one already exists."""
    ordered = list(versions or [])
    if not ordered:
        return None
    latest = ordered[0]
    if latest.get("id") == report.get("id"):
        return None
    if latest.get("status") in ("RASCUNHO", "EM_REVISAO"):
        return latest
    return None


def _selected_is_latest_closed(report, versions):
    ordered = list(versions or [])
    latest = ordered[0] if ordered else report
    return report.get("status") in ("FINALIZADO", "ENVIADO") and latest.get(
        "id"
    ) == report.get("id")


def _render_version_continuation(
    store, principal, report, versions, *, tipo, year, quarter, placement
):
    """Offer the next step for a closed version without editing it."""
    if not getattr(principal, "administrator", False):
        return
    workflow = report_workflow_state(versions or [report], report.get("id"))
    if report.get("status") not in ("FINALIZADO", "ENVIADO"):
        return
    successor = workflow["editable"] if workflow["has_editable_successor"] else None
    if successor:
        st.caption("Já existe uma versão em elaboração.")
        label = "Abrir versão em elaboração"
        if st.button(label, key=f"inst_open_{placement}_{report['id']}"):
            _remember_open_editor()
            st.rerun()
        return
    if not workflow["can_create_new"]:
        return
    if placement == "editor":
        st.caption(
            "Para alterar ou gerar o conteúdo desta versão, "
            "crie uma nova versão em elaboração."
        )
    label = "Criar nova versão para editar"
    key = (
        f"inst_new_{report['id']}"
        if placement == "editor"
        else f"inst_new_header_{report['id']}"
    )
    if st.button(label, key=key):
        try:
            repository = InstitutionalReportsStore(store)
            created = _create_institutional_version(
                store,
                repository,
                principal,
                tipo,
                year,
                quarter,
                new_version=True,
            )
            _flash(
                "success",
                f"Relatório institucional versão {created['versao']} criado.",
            )
            _remember_open_editor()
            st.rerun()
        except ValueError as exc:
            st.warning(str(exc))


def _render_institutional_editor(
    store, principal, report, *, is_latest, tipo, year, quarter, versions=None
):
    """Phase 2 controls. Finalized and sent versions stay read-only."""
    content = normalize_content(
        report["conteudo_estruturado"], report["snapshot_dados"]
    )
    locked = report["status"] in ("FINALIZADO", "ENVIADO")
    editable = getattr(principal, "administrator", False) and not locked
    if locked:
        st.caption("Versão encerrada, disponível somente para consulta.")
        if report["status"] == "ENVIADO":
            st.caption(
                "Relatório enviado. O texto e o snapshot desta versão não podem ser alterados."
            )
        _render_version_continuation(
            store,
            principal,
            report,
            versions,
            tipo=tipo,
            year=year,
            quarter=quarter,
            placement="editor",
        )
        for key, label in SECTIONS:
            section = content[key]
            with st.expander(label, expanded=False):
                text = section.get("texto") or ""
                if text.strip():
                    st.markdown(text)
                else:
                    st.caption("Seção não preenchida nesta versão.")
        return
    elif editable:
        repository = InstitutionalReportsStore(store)
        _render_institutional_ai_flash(report["id"])
        _render_last_institutional_ai_attempt(principal, report["id"])
        unsaved = _has_unsaved_text(report["id"], content)
        service = InstitutionalReportContentService(store)
        _process_pending_institutional_ai_action(service, principal, report)
        ai_configured = ai_service.gemini_disponivel()
        ai_busy = bool(st.session_state.get(_AI_ACTION_KEY))
        actions = st.columns(3)
        has_content = any(content[key].get("texto", "").strip() for key in AI_SECTIONS)
        generate_label = "Gerar novamente com IA" if has_content else AI_GENERATE_LABEL
        _ai_button(
            actions[0],
            generate_label,
            f"inst_ai_all_{report['id']}",
            disabled=not ai_configured or ai_busy,
            on_click=(
                (
                    lambda report=report: _request_institutional_ai_action(
                        report, "GENERATE_ALL"
                    )
                )
                if not has_content
                else (
                    lambda report=report: st.session_state.__setitem__(
                        "inst_ai_replace_pending", report["id"]
                    )
                )
            ),
        )
        if not ai_configured:
            st.caption("Serviço de IA não configurado neste ambiente.")
        pending_replace = (
            st.session_state.get("inst_ai_replace_pending") == report["id"]
        )
        if pending_replace:
            st.warning(
                "Gerar novamente o conteúdo com IA? Os textos atuais serão substituídos."
            )
            confirm, cancel = st.columns(2)
            if confirm.button(
                "Confirmar geração com IA",
                key=f"inst_ai_replace_confirm_{report['id']}",
                icon=AI_ICON,
                disabled=ai_busy or not ai_configured,
                on_click=lambda report=report: _request_institutional_ai_action(
                    report, "GENERATE_ALL"
                ),
            ):
                st.session_state.pop("inst_ai_replace_pending", None)
            if cancel.button("Cancelar", key=f"inst_ai_replace_cancel_{report['id']}"):
                st.session_state.pop("inst_ai_replace_pending", None)
                st.rerun()
        if actions[1].button("Salvar alterações", key=f"inst_save_{report['id']}"):
            try:
                service.save_manual(
                    report["id"], _texts_from_state(report["id"], content), principal
                )
                _flash("success", "Alterações salvas.")
                st.rerun()
            except (ValueError, PermissionError) as exc:
                st.error(str(exc))
        if report["status"] == "RASCUNHO":
            if actions[2].button(
                "Marcar para revisão", key=f"inst_review_{report['id']}"
            ):
                try:
                    service.mark_for_review(report["id"], principal)
                    _flash("success", "Relatório marcado como Em revisão.")
                    st.rerun()
                except (ValueError, PermissionError) as exc:
                    st.warning(str(exc))
        else:
            actions[2].caption("Em revisão")
        for index, (key, label) in enumerate(SECTIONS):
            section = content[key]
            text = section.get("texto") or ""
            with st.expander(label, expanded=index == 0):
                widget_key = _initialize_text_widget(report["id"], key, text)
                if key == "resumo_executivo":
                    _log_text_sync(report, content, key)
                st.text_area(
                    label,
                    key=widget_key,
                    height=150,
                    label_visibility="collapsed",
                )
                if (
                    key in AI_SECTIONS
                    and text.strip()
                    and (
                        key != "comparacao_periodo_anterior"
                        or _comparison_can_regenerate(report)
                    )
                ):
                    _ai_button(
                        st,
                        AI_REGENERATE_LABEL,
                        f"inst_regen_{report['id']}_{key}",
                        disabled=not ai_configured or ai_busy,
                        on_click=lambda report=report, section=key: _request_institutional_ai_action(
                            report, "REGENERATE_SECTION", section=section
                        ),
                    )
                if validate_numbers(
                    section.get("texto") or "", report["snapshot_dados"]
                ):
                    st.warning("Esta seção contém valores que devem ser revisados.")
        if unsaved:
            st.warning("Salve as alterações textuais antes de finalizar o relatório.")
        if any(
            validate_numbers(section.get("texto") or "", report["snapshot_dados"])
            for section in content.values()
        ):
            st.warning(
                "Há seções com valores que devem ser revisados antes da finalização."
            )
        from services.institutional_report_validation import (
            finalization_readiness,
            validate_institutional_report_version,
        )

        summaries = repository.distribution_summaries(report["id"])
        validation = validate_institutional_report_version(
            report, distributions=summaries, versions=versions
        )
        readiness = finalization_readiness(
            validation, status=report["status"], unsaved=unsaved
        )
        coverage_ready = can_finalize(report["snapshot_dados"])
        if readiness["bloqueado"] or not coverage_ready:
            st.error("Não é possível finalizar.")
            if readiness["bloqueado"]:
                st.caption(readiness["rotulo"])
                for item in readiness["erros"]:
                    st.caption(item["mensagem"])
            elif not coverage_ready:
                st.caption(
                    "Finalização indisponível: há lacunas na cobertura de dados do período."
                )
            return
        if readiness["requer_confirmacao"]:
            st.warning(readiness["rotulo"])
        confirmed = st.checkbox(
            "Confirmo a finalização e o congelamento deste snapshot.",
            key=f"inst_confirm_{report['id']}",
        )
        attention_confirmed = True
        if readiness["requer_confirmacao"]:
            attention_confirmed = st.checkbox(
                f"Existem {len(readiness['atencoes'])} pontos de atenção. Deseja finalizar mesmo assim?",
                key=f"institutional_governance_attention_{report['id']}",
            )
        if st.button(
            "Finalizar relatório",
            key=f"inst_finalize_{report['id']}",
            disabled=not (confirmed and attention_confirmed),
        ):
            fresh = repository.get(report["id"])
            fresh_validation = validate_institutional_report_version(
                fresh,
                distributions=repository.distribution_summaries(report["id"]),
                versions=versions,
            )
            fresh_ready = finalization_readiness(
                fresh_validation,
                status=fresh["status"],
                unsaved=_has_unsaved_text(report["id"], content),
            )
            if fresh_ready["bloqueado"] or not can_finalize(fresh["snapshot_dados"]):
                st.error("Não é possível finalizar.")
                for item in fresh_ready["erros"]:
                    st.caption(item["mensagem"])
                return
            if fresh_ready["requer_confirmacao"] and not attention_confirmed:
                st.warning(fresh_ready["rotulo"])
                return
            finalized = repository.finalize(report["id"], principal.email)
            registrar_evento(
                store,
                evento="RELATORIO_INSTITUCIONAL_FINALIZADO",
                modulo="relatorios",
                acao="FINALIZAR",
                principal=principal,
                entidade_tipo="relatorio_institucional",
                entidade_id=finalized["id"],
                detalhes={
                    "tipo": tipo,
                    "ano": year,
                    "trimestre": quarter,
                    "versao": finalized["versao"],
                },
            )
            _flash(
                "success",
                "Relatório finalizado; o snapshot desta versão permanece congelado.",
            )
            st.rerun()
        return
    for key, label in SECTIONS:
        section = content[key]
        with st.expander(label, expanded=False):
            text = section.get("texto") or ""
            if text.strip():
                st.markdown(text)
            else:
                st.caption("Seção ainda não preenchida.")


def _covered_period_or_blank(snapshot):
    try:
        return _institutional_covered_period(snapshot).replace("Período coberto: ", "")
    except (KeyError, TypeError, ValueError):
        return "—"


def _render_version_history(versions):
    rows = []
    for item in versions:
        snapshot = item.get("snapshot_dados") or {}
        rows.append(
            {
                "Versão": item["versao"],
                "Status": _institutional_status_label(item["status"]),
                "Data de corte": format_datetime_br(item.get("data_corte")),
                "Criado em": format_datetime_br(item.get("criado_em")),
                "Responsável": item.get("criado_por") or "Não informado",
                "Período coberto": _covered_period_or_blank(snapshot),
            }
        )
    _render_table_safely(rows, "Nenhuma versão encontrada para este período.")
    st.caption(
        "A versão mais recente vem selecionada. Versões finalizadas permanecem congeladas."
    )


def _render_report_identity(report, snapshot):
    st.markdown(f"#### {_institutional_title(snapshot)}")
    st.caption(_institutional_period_label(snapshot))
    st.caption(
        f"Status: {_institutional_status_label(report['status'])} · "
        f"Versão {report['versao']} · "
        f"criado em {format_datetime_br(report.get('criado_em'))} · "
        f"corte: {format_datetime_br(report.get('data_corte'))}"
    )
    st.caption(f"Responsável: {report.get('criado_por') or 'Não informado'}")
    if report.get("status") in ("FINALIZADO", "ENVIADO"):
        st.caption("Esta versão está encerrada e não pode mais ser alterada.")
    covered = _covered_period_or_blank(snapshot)
    if covered != "—":
        st.caption(f"Período coberto: {covered}")
    try:
        st.caption(_institutional_coverage_text(snapshot))
    except (KeyError, TypeError, ValueError):
        st.caption("Cobertura indisponível neste snapshot.")


def _render_institutional_detail_header(report):
    """Keep the operational context visible without exposing internal selectors."""
    from services.institutional_report_validation import overview_row

    snapshot = report.get("snapshot_dados") or {}
    summary = overview_row(report)
    st.markdown(f"## {_institutional_title(snapshot)}")
    st.caption(_institutional_period_label(snapshot))
    states = [summary["Status"], f"Validação: {summary['Validação']}"]
    if summary["Distribuição"] != "—":
        states.append(f"Distribuição: {summary['Distribuição']}")
    st.caption(" · ".join(states))
    created = format_datetime_br(report.get("criado_em"))
    owner = report.get("criado_por") or "Não informado"
    st.caption(f"Versão {report['versao']} · Responsável: {owner}" + (f" · criada em {created}" if created else ""))
    coverage = snapshot.get("cobertura_historica") or {}
    months = coverage.get("meses_disponiveis")
    if months:
        st.caption(f"Cobertura: {_institutional_month_span(months, report.get('ano'))}")
    missing = coverage.get("meses_ausentes") or coverage.get("lacunas_no_ano") or []
    if missing:
        st.warning("A cobertura deste relatório possui lacunas.")


def _render_period_suggestions(
    store,
    reports,
    read,
    years,
    latest_rows,
    principal,
    repository,
    tipo,
    year,
    quarter,
):
    """Offer ready periods. Creation still requires the existing explicit action."""
    try:
        from services.institutional_governance_ui import (
            period_plan,
            render_ready_periods,
        )

        coverage = {}
        for item in years:
            item = int(item)
            coverage[item] = read(
                ("historical_months", item),
                lambda item=item: reports.months_for_year(item),
            )
        clicked = render_ready_periods(
            period_plan(coverage, latest_rows),
            administrator=getattr(principal, "administrator", False),
            selected_token=_period_token(tipo, year, quarter),
            tipo=tipo,
        )
        if not clicked:
            return
        created = _create_institutional_version(
            store,
            repository,
            principal,
            clicked["tipo"],
            clicked["ano"],
            clicked["trimestre"],
            new_version=False,
        )
        st.session_state["institutional_governance_open"] = {
            "tipo": clicked["tipo"],
            "ano": int(clicked["ano"]),
            "trimestre": clicked["trimestre"],
        }
        _flash(
            "success",
            f"Relatório institucional versão {created['versao']} criado.",
        )
        _prefer_latest_version()
        st.rerun()
    except ValueError as exc:
        st.warning(str(exc))
    except Exception:
        LOGGER.exception("Falha ao identificar períodos prontos para relatório")


def _open_institutional_report(report, *, editor=False):
    """Reuse the established report selection state from a compact index."""
    st.session_state["inst_tipo"] = (
        "Relatório Trimestral"
        if report.get("tipo") == "TRIMESTRAL"
        else "Relatório Anual"
    )
    st.session_state["inst_ano"] = int(report["ano"])
    if report.get("trimestre") is not None:
        st.session_state["inst_trimestre"] = int(report["trimestre"])
    st.session_state["inst_selected_period"] = {
        "tipo": report["tipo"],
        "ano": int(report["ano"]),
        "trimestre": report.get("trimestre"),
    }
    st.session_state["inst_prefer_latest"] = True
    st.session_state.pop("inst_selected_version_id", None)
    if editor:
        st.session_state["inst_open_editor"] = True


def _render_institutional_created_reports(store, principal, repository, latest_rows):
    """Operational index; it deliberately uses the already-loaded lightweight rows."""
    from services.institutional_report_validation import overview_row

    st.markdown("#### Relatórios criados")
    if not latest_rows:
        empty_state("Nenhum relatório institucional foi criado até o momento.")
        return
    for report in latest_rows:
        summary = overview_row(report)
        period = summary["Período"]
        details = [
            "Trimestral" if report.get("tipo") == "TRIMESTRAL" else "Anual",
            summary["Status"],
            f"Validação: {summary['Validação']}",
        ]
        if summary["Distribuição"] != "—":
            details.append(f"Distribuição: {summary['Distribuição']}")
        with st.container(border=True):
            institutional_card_mark()
            st.markdown(f"**{period}**")
            snapshot = report.get("snapshot_dados") or {}
            coverage = (snapshot.get("cobertura_historica") or {}).get(
                "meses_disponiveis"
            )
            if coverage:
                st.caption(_institutional_month_span(coverage, report.get("ano")))
            st.caption(" · ".join(details))
            actions = st.columns(3 if getattr(principal, "administrator", False) else 2)
            if actions[0].button("Abrir", key=f"inst_open_created_{report['id']}"):
                _open_institutional_report(report)
                st.rerun()
            if report.get("pdf_sha256"):
                with actions[1]:
                    # The existing action keeps PDF bytes lazy and audits downloads.
                    _render_institutional_pdf_action(store, principal, report)
            if getattr(principal, "administrator", False) and actions[2].button(
                "Excluir", key=f"inst_admin_delete_ask_{report['id']}"
            ):
                st.session_state["inst_admin_delete_pending"] = {
                    "tipo": report["tipo"],
                    "ano": report["ano"],
                    "trimestre": report.get("trimestre"),
                    "periodo": period,
                }
                st.rerun()


def _render_administrative_delete_confirmation(store, principal):
    """Administrator-only confirmation for removing a whole report period."""
    pending = st.session_state.get("inst_admin_delete_pending")
    if not pending or not getattr(principal, "administrator", False):
        return
    st.warning("Excluir relatório institucional?")
    st.caption(
        f"Esta ação excluirá definitivamente o relatório '{pending['periodo']}' "
        "e todas as suas versões. Os dados originais dos módulos do MPC-PB não serão excluídos."
    )
    cancel, confirm = st.columns(2)
    if cancel.button("Cancelar", key="inst_admin_delete_cancel"):
        st.session_state.pop("inst_admin_delete_pending", None)
        st.rerun()
    if confirm.button("Excluir definitivamente", key="inst_admin_delete_confirm"):
        try:
            removed = delete_institutional_report_period(
                store,
                pending["tipo"],
                pending["ano"],
                pending.get("trimestre"),
                principal,
            )
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("inst_admin_delete_pending", None)
            _forget_deleted_reports([item["id"] for item in removed])
            _flash("success", "Relatório institucional excluído com sucesso.")
            st.rerun()


def _render_institutional_creation(store, principal, repository, reports, read, years, latest_rows):
    """Show every currently creatable period directly, without a quarter picker."""
    from services.institutional_governance_ui import period_plan

    st.markdown("#### Criar novo relatório")
    tipo_label = st.radio(
        "Tipo",
        ("Relatório Trimestral", "Relatório Anual"),
        horizontal=True,
        key="inst_tipo",
    )
    tipo = "TRIMESTRAL" if tipo_label == "Relatório Trimestral" else "ANUAL"
    _drop_invalid_widget("inst_ano", years)
    year = st.selectbox("Ano", years, key="inst_ano")
    available = read(
        ("historical_months", year), lambda: reports.months_for_year(year)
    )
    plan = period_plan({year: available}, latest_rows)
    suggestions = [
        item for item in plan.get("sugestoes") or [] if item.get("tipo") == tipo
    ]
    st.markdown("##### Períodos disponíveis")
    if not suggestions:
        st.caption("Não há períodos disponíveis para criação neste ano.")
        return
    for item in suggestions:
        token = _period_token(item["tipo"], item["ano"], item["trimestre"])
        with st.container(border=True):
            institutional_card_mark()
            st.markdown(f"**{item['titulo']}**")
            st.caption(item["cobertura"].capitalize())
            if getattr(principal, "administrator", False) and st.button(
                "Criar relatório", key=f"inst_create_{token}"
            ):
                try:
                    created = _create_institutional_version(
                        store,
                        repository,
                        principal,
                        item["tipo"],
                        item["ano"],
                        item["trimestre"],
                        new_version=False,
                    )
                except ValueError as exc:
                    st.warning(str(exc))
                else:
                    # Type and year are current widgets in this run, so only
                    # store non-widget routing state before the normal rerun.
                    st.session_state["inst_selected_period"] = {
                        "tipo": created["tipo"],
                        "ano": int(created["ano"]),
                        "trimestre": created.get("trimestre"),
                    }
                    st.session_state["inst_prefer_latest"] = True
                    st.session_state["inst_open_editor"] = True
                    _flash(
                        "success",
                        f"Relatório institucional versão {created['versao']} criado.",
                    )
                    st.rerun()


def _render_institutional_detail(store, principal, repository, tipo, year, quarter):
    """The pre-existing editor/review flow, reached from the reports index."""
    versions = repository.list_for_period(tipo, year, quarter)
    if not versions:
        st.warning("O relatório selecionado não está mais disponível.")
        return
    open_editor = st.session_state.pop("inst_open_editor", False)
    if st.session_state.pop("inst_prefer_latest", False) or open_editor:
        st.session_state.pop("inst_selected_version_id", None)
    selected_version_id = st.session_state.get("inst_selected_version_id")
    report = next(
        (item for item in versions if item["id"] == selected_version_id), versions[0]
    )
    latest = versions[0]
    snapshot = report.get("snapshot_dados") or {}
    _render_institutional_detail_header(report)
    actions = st.columns(2)
    with actions[0]:
        _render_institutional_pdf_action(store, principal, report)
    if getattr(principal, "administrator", False):
        with actions[1]:
            _render_version_continuation(
                store,
                principal,
                report,
                versions,
                tipo=tipo,
                year=year,
                quarter=quarter,
                placement="header",
            )
        _render_delete_confirmation(store, principal, versions)
    if open_editor:
        st.session_state["inst_exibicao"] = "Conteúdo e revisão"
    if st.session_state.get("inst_exibicao") == "Validação e governança":
        st.session_state["inst_exibicao"] = "Validação"
    view = st.radio(
        "Navegação do relatório",
        ("Visualização", "Conteúdo e revisão", "Validação", "Versões", "Distribuição"),
        horizontal=True,
        key="inst_exibicao",
        label_visibility="collapsed",
    )
    if view == "Visualização":
        _render_institutional_document(report)
    elif view == "Conteúdo e revisão":
        _render_institutional_editor(store, principal, report, is_latest=report["id"] == latest["id"], tipo=tipo, year=year, quarter=quarter, versions=versions)
    elif view == "Validação":
        from services.institutional_governance_ui import render_governance
        render_governance(store, report, versions, unsaved=_has_unsaved_text(report["id"], normalize_content(report.get("conteudo_estruturado"), snapshot)), administrator=getattr(principal, "administrator", False))
    elif view == "Distribuição":
        from services.institutional_distribution_ui import render_distribution
        render_distribution(store, principal, report)
    else:
        _render_version_history(versions)
        labels = [_version_label(item) for item in versions]
        selected = st.selectbox("Consultar versão", labels, index=labels.index(_version_label(report)), key="inst_version_history")
        candidate = versions[labels.index(selected)]
        if candidate["id"] != report["id"]:
            def open_version():
                st.session_state["inst_selected_version_id"] = candidate["id"]
                st.session_state["inst_exibicao"] = "Visualização"

            st.button("Abrir esta versão", key=f"inst_open_version_{candidate['id']}", on_click=open_version)
        if getattr(principal, "administrator", False):
            _render_delete_buttons(versions[0], versions, placement="versions", repository=repository)
        from services.institutional_governance_ui import render_version_comparison
        render_version_comparison(store, versions)


def _institutional_workspace(store, principal):
    _apply_forgotten_reports()
    reports = TramitaReportsStore(store)
    read = lambda key, load: _cached_report(store, principal, key, load)
    years = read(("historical_years",), reports.historical_years)
    if not years:
        empty_state(
            "Nenhum histórico Tramita foi importado. Utilize a área Importações para carregar a base inicial."
        )
        return
    pending = st.session_state.pop("institutional_governance_open", None)
    if isinstance(pending, dict):
        st.session_state["inst_tipo"] = (
            "Relatório Trimestral"
            if pending.get("tipo") == "TRIMESTRAL"
            else "Relatório Anual"
        )
        if pending.get("ano") in years:
            st.session_state["inst_ano"] = pending["ano"]
        if pending.get("trimestre"):
            st.session_state["inst_trimestre"] = int(pending["trimestre"])
        st.session_state["inst_prefer_latest"] = True
        st.session_state["inst_selected_period"] = pending
    _consume_flash()
    repository = InstitutionalReportsStore(store)
    latest_rows = []
    try:
        latest_rows = repository.list_latest_versions()
    except Exception:
        LOGGER.exception("Falha ao resumir os relatórios institucionais")
    section = st.radio(
        "Navegação de relatórios",
        ("Relatórios criados", "Criar novo"),
        horizontal=True,
        key="inst_home_section",
        label_visibility="collapsed",
    )
    selected_period = st.session_state.get("inst_selected_period")
    if selected_period:
        back, title = st.columns((1, 5))

        def back_to_created():
            st.session_state.pop("inst_selected_period", None)
            st.session_state["inst_home_section"] = "Relatórios criados"

        back.button(
            "← Relatórios criados",
            key="inst_back_to_created",
            on_click=back_to_created,
        )
        title.markdown("#### Relatório selecionado")
        _render_institutional_detail(
            store,
            principal,
            repository,
            selected_period["tipo"],
            selected_period["ano"],
            selected_period.get("trimestre"),
        )
    elif section == "Relatórios criados":
        _render_administrative_delete_confirmation(store, principal)
        _render_institutional_created_reports(store, principal, repository, latest_rows)
    else:
        _render_institutional_creation(store, principal, repository, reports, read, years, latest_rows)


def institutional_reports(store, principal):
    """Dedicated home for institutional reports. Analytical panels do not call this."""
    try:
        st.subheader("Relatórios Institucionais")
        st.caption(
            "Geração, revisão e histórico dos relatórios trimestrais e anuais do MPC-PB"
        )
        _institutional_workspace(store, principal)
    except Exception:
        LOGGER.exception("Falha ao renderizar a seção Relatórios Institucionais")
        st.error(
            "Não foi possível carregar os relatórios institucionais neste momento."
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
        "Relatórios Institucionais",
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
    elif section == "Relatórios Institucionais":
        institutional_reports(store, principal)
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
