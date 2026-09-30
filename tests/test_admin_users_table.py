import inspect

from services.access_ui import ADMIN_SECTIONS, style_user_table, user_rows
from services.themes import THEMES
from services.ui_theme import DANGER, SUCCESS


def _user():
    return {
        "nome": "Ana",
        "email": "ana@mpc.pb.gov.br",
        "perfil": "USUARIO",
        "ativo": True,
        "pode_portarias": True,
        "pode_agenda": False,
        "pode_oficios": True,
        "pode_memorandos": False,
        "pode_relatorios": True,
        "pode_representacoes": False,
        "pode_ouvidoria": True,
        "pode_admin": False,
        "gabinetes": ["Gabinete"],
    }


def test_user_rows_keep_columns_and_sim_nao_values():
    rows = user_rows([_user()])
    assert list(rows[0]) == [
        "Nome",
        "E-mail",
        "Perfil",
        "Ativo",
        "Portarias",
        "Agenda e Afastamentos",
        "Ofícios",
        "Memorandos",
        "Relatórios",
        "Representações",
        "Ouvidoria",
        "Admin",
        "Gabinetes",
    ]
    assert rows[0]["Ativo"] == "Sim"
    assert rows[0]["Agenda e Afastamentos"] == "Não"
    assert rows[0]["Nome"] == "Ana"


def test_user_table_style_keeps_values_and_theme_surface():
    rows = user_rows([_user()])
    for name, tokens in THEMES.items():
        styler = style_user_table(rows, name)
        assert styler.data.loc[0, "Ativo"] == "Sim"
        assert styler.data.loc[0, "Admin"] == "Não"
        html = styler.to_html()
        assert tokens["themed_table_bg"].lower() in html.lower()
        assert tokens["themed_table_header_bg"].lower() in html.lower()
        assert SUCCESS.lower() in html.lower()
        assert DANGER.lower() in html.lower()
        assert "#ffffff" not in html.lower()
        assert "background-color: #fff;" not in html.lower()
        assert "background-color: white" not in html.lower()


def test_function_table_alternates_theme_row_surfaces():
    from datetime import date

    from services.access_ui import style_function_table

    rows = [
        {"Função": "Procurador-Geral", "Titular atual": "Ana", "Desde": date(2026, 1, 2)},
        {"Função": "Corregedor", "Titular atual": "Bruno", "Desde": date(2026, 3, 4)},
        {"Função": "Ouvidor", "Titular atual": "—", "Desde": None},
    ]
    for name, tokens in THEMES.items():
        styler = style_function_table(rows, name)
        assert list(styler.data.columns) == ["Função", "Titular atual", "Desde"]
        assert styler.data.loc[0, "Titular atual"] == "Ana"
        assert styler.data.loc[2, "Titular atual"] == "—"
        html = styler.to_html().lower()
        base = tokens["themed_table_bg"].lower()
        stripe = tokens["themed_table_stripe_bg"].lower()
        foreground = tokens["themed_table_fg"].lower()
        assert base != stripe
        odd = html[html.find("row0_col0") : html.find("}", html.find("row0_col0"))]
        even = html[html.find("row1_col0") : html.find("}", html.find("row1_col0"))]
        assert base in odd and stripe not in odd
        assert stripe in even and base not in even
        assert "row2_col0" in odd
        assert foreground in html
        assert not styler.table_styles


def test_audit_dataframes_reuse_the_striped_table():
    from inspect import getsource

    from services import audit_ui
    from services.ui_theme import style_striped_table

    overview = getsource(audit_ui._render_overview)
    accesses = getsource(audit_ui._render_accesses)
    log = getsource(audit_ui._render_log)
    assert "style_striped_table(" in overview
    assert "style_striped_table(" in accesses
    assert "style_striped_table(" not in log
    assert "st.dataframe" not in log
    rows = [
        {"Nome": "Ana", "E-mail": "ana@mpc.pb.gov.br", "Módulos": "Ofícios"},
        {"Nome": "Bruno", "E-mail": "bruno@mpc.pb.gov.br", "Módulos": "Agenda"},
    ]
    for name, tokens in THEMES.items():
        styler = style_striped_table(rows, name)
        assert list(styler.data["Nome"]) == ["Ana", "Bruno"]
        html = styler.to_html().lower()
        base = tokens["themed_table_bg"].lower()
        stripe = tokens["themed_table_stripe_bg"].lower()
        first = html[html.find("row0_col0") : html.find("}", html.find("row0_col0"))]
        second = html[html.find("row1_col0") : html.find("}", html.find("row1_col0"))]
        assert base in first and stripe not in first
        assert stripe in second and base not in second
        assert not styler.table_styles


def test_other_admin_sections_remain():
    from services.access_ui import render

    source = inspect.getsource(render)
    assert ADMIN_SECTIONS == (
        "Usuários",
        "Solicitações",
        "Funções Institucionais",
        "Acessos e Auditoria",
        "Sistema",
    )
    for section in ADMIN_SECTIONS:
        assert section in source or section == "Usuários"


def test_protocol_notice_table_reuses_theme_stripes():
    from services.notification_ui import render_admin_recipients
    from services.ui_theme import style_striped_table

    source = inspect.getsource(render_admin_recipients)
    assert "style_striped_table(" in source
    assert "Comunicação de protocolo" in source
    rows = [
        {"Nome": "Ana", "Função": "Ouvidora", "E-mail": "ana@mpc.pb.gov.br", "Recebe aviso": "Sim"},
        {"Nome": "Bruno", "Função": "Corregedor", "E-mail": "bruno@mpc.pb.gov.br", "Recebe aviso": "Não"},
    ]
    for name, tokens in THEMES.items():
        styler = style_striped_table(rows, name)
        assert list(styler.data.columns) == ["Nome", "Função", "E-mail", "Recebe aviso"]
        assert list(styler.data["Recebe aviso"]) == ["Sim", "Não"]
        html = styler.to_html().lower()
        base = tokens["themed_table_bg"].lower()
        stripe = tokens["themed_table_stripe_bg"].lower()
        first = html[html.find("row0_col0") : html.find("}", html.find("row0_col0"))]
        second = html[html.find("row1_col0") : html.find("}", html.find("row1_col0"))]
        assert base in first and stripe not in first
        assert stripe in second and base not in second
        assert tokens["themed_table_fg"].lower() in html
        assert not styler.table_styles
