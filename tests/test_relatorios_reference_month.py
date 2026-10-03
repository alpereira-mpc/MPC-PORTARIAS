import inspect
from decimal import Decimal

import pandas as pd

from services.relatorios_ui import (
    _chart_values,
    _indicator_cards,
    _line_chart,
    _procurador_chart,
    _temporal_charts,
    annual,
    production,
    quarterly,
    render,
)


def test_monthly_view_uses_year_and_month_instead_of_import_competence():
    source = inspect.getsource(production)
    assert '"Ano"' not in source  # shared year selector owns this control
    assert '"Mês"' in source
    assert "Competência" not in source


def test_historical_views_share_period_aggregation_and_required_charts():
    source = inspect.getsource(production)
    assert "reports.monthly_reports(year, [month])" in source
    assert "period_report" in inspect.getsource(quarterly)
    assert "_temporal_charts" in inspect.getsource(quarterly)
    assert "_temporal_charts" in inspect.getsource(annual)
    assert "quarterly_reports" in inspect.getsource(annual)


def test_historical_charts_use_clear_semantics_and_unique_keys():
    temporal = inspect.getsource(_temporal_charts)
    procuradores = inspect.getsource(_procurador_chart)
    assert "Permanência e relação entre fluxos" in temporal
    assert '"yOffset"' in procuradores
    assert "labelLimit" in procuradores
    assert 'stack="normal"' not in procuradores


def test_chart_specs_render_grouped_and_stacked_comparisons_without_exceptions():
    class ChartTarget:
        def __init__(self):
            self.calls = []

        def vega_lite_chart(self, values, spec, **kwargs):
            self.calls.append((values, spec, kwargs))

    target = ChartTarget()
    _line_chart(
        target,
        [{"Mês": "Setembro", "Distribuídos": 2, "Produção": 2}],
        ["Distribuídos", "Produção"],
        "Quantidade",
        "test_fluxo",
    )
    rows = [
        {
            "Procurador": "Nome completo do Procurador",
            "Distribuídos": 2,
            "Produção": 2,
            "Pareceres": 1,
            "Cotas": 1,
        }
    ]
    _procurador_chart(target, rows, ["Distribuídos", "Produção"], False, "grouped")
    _procurador_chart(target, rows, ["Pareceres", "Cotas"], True, "stacked")
    assert [call[2]["key"] for call in target.calls] == [
        "test_fluxo",
        "grouped",
        "stacked",
    ]
    assert "yOffset" in target.calls[1][1]["encoding"]
    assert "yOffset" not in target.calls[2][1]["encoding"]


def test_annual_time_series_normalize_nine_numeric_points_and_use_distinct_keys():
    months = [
        "Janeiro",
        "Fevereiro",
        "Março",
        "Abril",
        "Maio",
        "Junho",
        "Julho",
        "Agosto",
        "Setembro",
    ]
    rows = [
        {
            "Mês": month,
            "Mediana de permanência": Decimal(str(median)),
            "Produção/Distribuições": production / distributed * 100,
        }
        for month, median, distributed, production in zip(
            months,
            (19.1, 10.0, 11.0, 8.1, 15.2, 15.9, 7.1, 7.9, 9.1),
            (140, 188, 200, 266, 228, 193, 221, 155, 169),
            (146, 220, 189, 181, 259, 260, 227, 212, 155),
        )
    ]
    median_values = _chart_values(rows, ["Mediana de permanência"])
    ratio_values = _chart_values(rows, ["Produção/Distribuições"])
    assert len(median_values) == len(ratio_values) == 9
    assert [row["Mês"] for row in median_values] == months
    assert [row["Mês"] for row in ratio_values] == months
    assert all(isinstance(row["Valor"], float) for row in median_values)
    assert all(isinstance(row["Valor"], float) for row in ratio_values)
    assert (
        _chart_values(
            [
                {"Mês": "Outubro", "Produção/Distribuições": None},
                {"Mês": "Novembro", "Produção/Distribuições": float("nan")},
                {"Mês": "Dezembro", "Produção/Distribuições": pd.NA},
            ],
            ["Produção/Distribuições"],
        )
        == []
    )

    class ChartTarget:
        def __init__(self):
            self.calls = []

        def vega_lite_chart(self, values, spec, **kwargs):
            self.calls.append((values, spec, kwargs))

    target = ChartTarget()
    _line_chart(
        target,
        rows,
        ["Mediana de permanência"],
        "Mediana de permanência (dias)",
        "relatorios_anual_permanencia_2026",
    )
    _line_chart(
        target,
        rows,
        ["Produção/Distribuições"],
        "Produção/Distribuições (%)",
        "relatorios_anual_relacao_2026",
    )
    keys = [call[2]["key"] for call in target.calls]
    assert keys == [
        "relatorios_anual_permanencia_2026",
        "relatorios_anual_relacao_2026",
    ]
    assert len(set(keys)) == 2
    assert "streamlit-generated" not in repr(target.calls)


def test_indicator_cards_share_methodology_help_and_nonproductive_diagnostic():
    source = inspect.getsource(_indicator_cards)
    assert "Produção/Distribuições" in source
    assert "Quantidade de eventos de distribuição" in source
    assert "Devoluções produtivas registradas" in source
    assert "Relação entre devoluções produtivas" in source
    assert "Intervalo mediano entre a distribuição" in source
    assert "Devoluções não produtivas no período" in source


def test_reports_section_defaults_to_current_view_in_the_requested_order():
    source = inspect.getsource(render)
    assert source.index('"Visão Atual"') < source.index('"Produção Mensal"')
    assert source.index('"Produção Mensal"') < source.index('"Avaliação Trimestral"')
    assert source.index('"Avaliação Trimestral"') < source.index('"Avaliação Anual"')
    assert 'key="relatorios_section"' in source
    assert "index=0" in source
