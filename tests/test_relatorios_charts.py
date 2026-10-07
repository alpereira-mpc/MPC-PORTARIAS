from contextlib import contextmanager

from services.relatorios_ui import _category_bar_chart, _line_chart


class _ChartTarget:
    def __init__(self):
        self.chart_values = None
        self.table_values = None
        self.expanders = []

    def vega_lite_chart(self, values, _spec, **_kwargs):
        self.chart_values = values

    @contextmanager
    def expander(self, label, *, expanded):
        self.expanders.append((label, expanded))
        yield self

    def dataframe(self, values, **_kwargs):
        self.table_values = values


def test_evolution_line_chart_keeps_chart_and_exposes_same_data_in_expander():
    target = _ChartTarget()
    rows = [{"Mês": "Janeiro", "Distribuídos": 12, "Produção": 9}]
    _line_chart(
        target,
        rows,
        ["Distribuídos", "Produção"],
        "Quantidade",
        "evolucao_fluxo",
        show_data=True,
    )
    assert target.expanders == [("Ver dados do gráfico", False)]
    assert target.table_values == target.chart_values
    assert len(target.chart_values) == 2


def test_evolution_bar_chart_keeps_chart_and_exposes_same_data_in_expander():
    target = _ChartTarget()
    rows = [{"Mês": "Janeiro", "Pareceres": 7, "Cotas": 2}]
    _category_bar_chart(
        target,
        rows,
        "Mês",
        ["Pareceres", "Cotas"],
        "evolucao_tipos",
        stacked=True,
        show_data=True,
    )
    assert target.expanders == [("Ver dados do gráfico", False)]
    assert target.table_values == target.chart_values
    assert len(target.chart_values) == 2
