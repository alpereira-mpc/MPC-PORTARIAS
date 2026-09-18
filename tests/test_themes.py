"""Palette isolation, persistence, and the portal's theme lifecycle."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from database.access import AccessStore
from services.themes import THEMES, valid_theme
from services.ui_theme import _css
from tests.access_testing import enable_login, seed_access


ROOT = Path(__file__).resolve().parents[1]
def test_red_palette_is_the_fallback():
    red = _css("vermelho")
    assert "--mpc-brand:#9B1724" in red
    assert "--mpc-sidebar:#D48792" in red
    assert _css(None) == red
    assert _css("desconhecido") == red
    assert valid_theme("") == "vermelho"


def _css_token(css, name):
    marker = f"--{name}:"
    start = css.index(marker) + len(marker)
    return css[start : css.index(";", start)]


def _contrast(left, right):
    def luminance(color):
        channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
        linear = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in channels]
        return sum(weight * value for weight, value in zip((0.2126, 0.7152, 0.0722), linear))

    high, low = sorted((luminance(left), luminance(right)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_all_palettes_have_required_visual_tokens():
    required = {
        "primary", "primary_hover", "sidebar_bg", "card_operational_bg",
        "card_institutional_bg", "card_b_bg", "themed_control_bg",
        "themed_control_border", "themed_control_hover", "themed_control_fg",
        "themed_control_placeholder", "themed_table_bg",
        "themed_table_header_bg", "themed_table_border", "themed_table_fg",
        "themed_table_header_fg", "themed_table_stripe_bg",
    }
    assert len(THEMES) == 5
    for palette in THEMES.values():
        assert required <= palette.keys()
        assert all(palette[key] for key in required)


def test_themed_control_tokens_differ_across_all_themes():
    backgrounds = {}
    for name in THEMES:
        css = _css(name)
        value = _css_token(css, "mpc-themed-control-bg")
        assert value == THEMES[name]["themed_control_bg"]
        assert f"--mpc-themed-control-border:{THEMES[name]['themed_control_border']}" in css
        assert f"--mpc-themed-control-hover:{THEMES[name]['themed_control_hover']}" in css
        assert value != "#F5F2F1"
        assert _css_token(css, "mpc-control-bg") == value
        assert _css_token(css, "mpc-control-border") == THEMES[name]["themed_control_border"]
        assert _css_token(css, "mpc-control-fg") == THEMES[name]["themed_control_fg"]
        assert _contrast(THEMES[name]["themed_control_bg"], THEMES[name]["themed_control_fg"]) >= 4.5
        assert _contrast(THEMES[name]["themed_table_bg"], THEMES[name]["themed_table_fg"]) >= 4.5
        assert _contrast(THEMES[name]["themed_table_stripe_bg"], THEMES[name]["themed_table_fg"]) >= 4.5
        assert _contrast(THEMES[name]["themed_table_header_bg"], THEMES[name]["themed_table_header_fg"]) >= 4.5
        assert _css_token(css, "mpc-expander") == "var(--mpc-card-b)"
        assert '[data-testid="stSelectbox"] [data-baseweb="select"] > div' in css
        assert '[data-testid="stDateInput"] [data-baseweb="base-input"]' in css
        assert '[data-testid="stTextArea"] [data-baseweb="textarea"]' in css
        backgrounds[name] = value
        oficios = css[css.find('[class*="st-key-oficios_recebidos_acompanhamento_"]') :]
        oficios = oficios[: oficios.find('[class*="st-key-oficios_recebidos_historico_"]')]
        assert "var(--mpc-themed-control-bg)" in oficios
        assert "var(--mpc-brand-soft)" in oficios
        for block in oficios.split("{")[:-1]:
            selector = block.rsplit("}", 1)[-1].strip()
            if not selector:
                continue
            for part in selector.split(","):
                assert 'st-key-oficios_recebidos_acompanhamento_' in part
        global_css = css.split('[class*="st-key-oficios_recebidos_acompanhamento_"]', 1)[0]
        assert "background:var(--mpc-themed-control-bg)" not in global_css
        assert "background-color:var(--mpc-themed-control-bg)" not in global_css
        assert '[data-testid="stSelectbox"] > div > div{\nbackground:var(--mpc-themed-control-bg)' not in global_css
        assert '[data-testid="stDateInput"] > div > div{\nbackground:var(--mpc-themed-control-bg)' not in global_css
        assert '[data-testid="stTextArea"] > div > div{\nbackground:var(--mpc-themed-control-bg)' not in global_css
        assert '[data-testid="stTextArea"] textarea{\nbackground:var(--mpc-themed-control-bg)' not in global_css
        assert "\ninput{\nbackground:var(--mpc-themed-control-bg)" not in global_css
        assert "\ntextarea{\nbackground:var(--mpc-themed-control-bg)" not in global_css
    assert len(set(backgrounds.values())) == len(THEMES)
    assert backgrounds["dourado"] != backgrounds["verde"]
    assert backgrounds["verde"] != backgrounds["vermelho"]
    assert backgrounds["vermelho"] != backgrounds["azul"]
    assert backgrounds["azul"] != backgrounds["vermelho_escuro"]


@pytest.mark.parametrize("name", ("azul", "verde", "dourado", "vermelho_escuro"))
def test_other_palettes_share_css_and_keep_status_colors(name):
    css = _css(name)
    palette = THEMES[name]
    assert f"--mpc-brand:{palette['primary']}" in css
    assert f"--mpc-sidebar:{palette['sidebar_bg']}" in css
    assert f"--mpc-card-institutional-bg:{palette['card_institutional_bg']}" in css
    assert "--mpc-success:#2E7D4F" in css
    assert "--mpc-danger:#B02A2A" in css


def test_user_themes_are_independent_and_survive_reopening(store):
    access = AccessStore(store)
    first = access.get_by_email("admin@test.local")["id"]
    second = seed_access(store, email="outro@test.local", perfil="USUARIO")
    assert access.get_theme(first) == "vermelho"
    assert access.get_theme(second) == "vermelho"
    access.set_theme(first, "azul")
    access.set_theme(second, "verde")
    with pytest.raises(ValueError, match="Tema inválido"):
        access.set_theme(first, "inexistente")
    from database.store import Store

    reopened = AccessStore(Store(store.path))
    assert reopened.get_theme_by_email("admin@test.local") == "azul"
    assert reopened.get_theme_by_email("outro@test.local") == "verde"


@pytest.mark.parametrize("name", ("vermelho", "azul", "verde"))
def test_portal_theme_survives_navigation(store, monkeypatch, name):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    selector = next(item for item in app.sidebar.selectbox if item.label == "Tema")
    if name != "vermelho":
        selector.set_value(name).run()
    for module in (
        "Início",
        "Portarias",
        "Agenda",
        "Ofícios",
        "Memorandos",
        "Tarefas",
        "Relatórios e Indicadores",
        "Administração",
        "Início",
    ):
        app.sidebar.radio(key="portal_module").set_value(module).run()
        assert not app.exception
        assert app.session_state["_portal_theme"]["name"] == name
        selector = next(item for item in app.sidebar.selectbox if item.label == "Tema")
        assert selector.value == name
    assert AccessStore(store).get_theme_by_email("admin@test.local") == name


def test_saved_theme_loads_before_portal_render(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    access = AccessStore(store)
    access.set_theme(access.get_by_email("admin@test.local")["id"], "azul")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    assert app.session_state["_portal_theme"]["name"] == "azul"
    selector = next(item for item in app.sidebar.selectbox if item.label == "Tema")
    assert selector.value == "azul"


def test_invalid_or_unreadable_preference_falls_back_to_red(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    with store.connection() as connection:
        connection.execute(
            "UPDATE usuarios_acesso SET tema=? WHERE email=?",
            ("tema_antigo", "admin@test.local"),
        )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    assert app.session_state["_portal_theme"]["name"] == "vermelho"

    def unavailable(*args):
        raise OSError("Falha temporária")

    monkeypatch.setattr(AccessStore, "get_theme_by_email", unavailable)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    assert app.session_state["_portal_theme"]["name"] == "vermelho"


def test_failed_save_keeps_previous_theme(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()

    def unavailable(*args):
        raise OSError("Falha temporária")

    monkeypatch.setattr(AccessStore, "set_theme", unavailable)
    selector = next(item for item in app.sidebar.selectbox if item.label == "Tema")
    selector.set_value("azul").run()
    assert not app.exception
    assert app.session_state["_portal_theme"]["name"] == "vermelho"
    assert AccessStore(store).get_theme_by_email("admin@test.local") == "vermelho"
