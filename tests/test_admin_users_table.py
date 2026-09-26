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


def test_other_admin_sections_remain():
    from services.access_ui import render

    source = inspect.getsource(render)
    assert ADMIN_SECTIONS == (
        "Usuários",
        "Solicitações",
        "Funções Institucionais",
        "Acessos e Auditoria",
        "Sistema",
        "Laboratório de IA",
    )
    for section in ADMIN_SECTIONS:
        assert section in source or section == "Usuários"
