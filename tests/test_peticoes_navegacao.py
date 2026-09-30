"""Petições opens on Acompanhamento only when the module is entered again."""

from streamlit.testing.v1 import AppTest

from database.store import ROOT
from portal import PORTAL_LAST_MODULE, enter_portal_module
from tests.access_testing import enable_login


def _enter(state, monkeypatch, allowed):
    import portal

    monkeypatch.setattr(portal.st, "session_state", state)
    return enter_portal_module(allowed)


def test_sidebar_entry_resets_to_acompanhamento_but_rerun_does_not(monkeypatch):
    allowed = ["Ofícios", "Agenda", "Petições"]
    state = {
        PORTAL_LAST_MODULE: "Ofícios",
        "portal_module": "Petições",
        "peticoes_secao": "Cadastrar Petição",
        "peticoes_edit_id": 9,
        "peticoes_painel": {"d1": True},
    }
    _enter(state, monkeypatch, allowed)
    assert state["peticoes_secao"] == "Acompanhamento"
    assert "peticoes_edit_id" not in state
    assert "peticoes_painel" not in state

    state = {
        PORTAL_LAST_MODULE: "Petições",
        "portal_module": "Petições",
        "peticoes_secao": "Cadastrar Petição",
        "peticoes_edit_id": 9,
    }
    _enter(state, monkeypatch, allowed)
    assert state["peticoes_secao"] == "Cadastrar Petição"
    assert state["peticoes_edit_id"] == 9

    state = {
        PORTAL_LAST_MODULE: "Agenda",
        "portal_module": "Petições",
        "peticoes_secao": "Histórico",
    }
    _enter(state, monkeypatch, allowed)
    assert state["peticoes_secao"] == "Acompanhamento"


def test_portal_sidebar_returns_to_acompanhamento(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not app.exception
    app.sidebar.radio(key="portal_module").set_value("Petições").run()
    assert not app.exception
    assert app.radio(key="peticoes_secao").value == "Acompanhamento"
    assert list(app.radio(key="peticoes_secao").options) == [
        "Acompanhamento",
        "Cadastrar Petição",
        "Histórico",
    ]
    app.radio(key="peticoes_secao").set_value("Cadastrar Petição").run()
    assert not app.exception
    assert app.radio(key="peticoes_secao").value == "Cadastrar Petição"
    app.run()
    assert app.radio(key="peticoes_secao").value == "Cadastrar Petição"
    app.sidebar.radio(key="portal_module").set_value("Agenda").run()
    assert not app.exception
    app.sidebar.radio(key="portal_module").set_value("Petições").run()
    assert not app.exception
    assert app.radio(key="peticoes_secao").value == "Acompanhamento"
