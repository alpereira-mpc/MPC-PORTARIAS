import inspect

from services.relatorios_ui import production


def test_reference_month_label_replaces_competence_selector():
    source = inspect.getsource(production)
    assert "selector.selectbox(" in source
    assert '"Mês de referência:"' in source
    assert "Competência" not in source


def test_reference_month_keeps_competence_selection():
    source = inspect.getsource(production)
    assert 'format_func=lambda value: f"{value[5:7]}/{value[:4]}"' in source
    assert "reports.production_summary(competence)" in source
    assert "st.columns([1.45, 4.55])" in source
