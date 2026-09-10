import sqlite3

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from database.store import ROOT
from portal import MODULES
from tests.cases import sample
from tests.test_postgresql import pg_url, pg_store


def test_home_is_default_and_never_initializes_database(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Home must not initialize Portarias or open a database")

    monkeypatch.setattr("database.store.Store", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(psycopg.Connection, "connect", forbidden)
    monkeypatch.setenv("DATABASE_URL", "invalid-but-home-does-not-read-it")
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception and not app.error
    assert app.title[0].value == "FERRAMENTAS MPC-PB"
    assert app.sidebar.radio(key="portal_module").value == "Início"
    assert not any(x.key == "nav" for x in app.radio)
    assert len(app.button) == 3
    assert app.button(key="open_portarias").label == "Acessar Portarias"
    for module in MODULES:
        assert any(module.title in x.value for x in app.markdown)
    assert sum(x.value == "EM BREVE" for x in app.caption) == 2
    app.run()
    assert not app.exception and not app.error


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_portal_navigation_and_lazy_return(store, pg_store, monkeypatch, backend):
    database = pg_store if backend == "postgresql" else store
    calls = []

    def factory():
        calls.append(True)
        return database

    monkeypatch.setattr("database.store.Store", factory)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not calls
    app.button(key="open_portarias").click().run()
    assert len(calls) == 1
    assert app.sidebar.radio(key="nav").value == "Nova Portaria"
    for page in ("Histórico", "Procuradores", "Configurações", "Nova Portaria"):
        app.sidebar.radio(key="nav").set_value(page).run()
        assert not app.exception and not app.error
    seed = sample(database)
    app.session_state["editor_seed"] = seed
    count = len(calls)
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    assert len(calls) == count
    assert app.session_state["editor_seed"] == seed
    app.sidebar.radio(key="portal_module").set_value("Portarias").run()
    assert len(calls) == count + 1
    assert app.session_state["editor_seed"] == seed
    assert not app.exception and not app.error


def test_future_modules_have_no_routes_or_side_effects():
    assert [module.key for module in MODULES if module.active] == [
        "portarias",
        "oficios",
        "agenda",
    ]
    assert len({module.key for module in MODULES}) == 5
