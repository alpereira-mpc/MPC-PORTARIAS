"""Palette isolation, persistence, and the portal's theme lifecycle."""

from hashlib import sha256
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from database.access import AccessStore
from services.themes import THEMES, valid_theme
from services.ui_theme import _css
from tests.access_testing import enable_login, seed_access


ROOT = Path(__file__).resolve().parents[1]
RED_CSS_SHA256 = "00c68e0d25ef0b3ad9ae31ddeef77bcefb92dde05b54545a0b583f843a61ff75"


def test_red_palette_reproduces_approved_css():
    red = _css("vermelho")
    assert sha256(red.encode()).hexdigest() == RED_CSS_SHA256
    assert _css(None) == red
    assert _css("desconhecido") == red
    assert valid_theme("") == "vermelho"


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
