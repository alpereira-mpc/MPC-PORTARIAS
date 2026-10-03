import inspect

from services.relatorios_ui import _indicator_cards, annual, production, quarterly


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


def test_indicator_cards_share_methodology_help_and_nonproductive_diagnostic():
    source = inspect.getsource(_indicator_cards)
    assert "Produção/Distribuições" in source
    assert "Quantidade de eventos de distribuição" in source
    assert "Devoluções produtivas registradas" in source
    assert "Relação entre devoluções produtivas" in source
    assert "Intervalo mediano entre a distribuição" in source
    assert "Devoluções não produtivas no período" in source
