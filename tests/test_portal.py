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
    monkeypatch.setattr("services.access.oidc_identity", lambda: None)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception and not app.error
    assert app.title[0].value == "FERRAMENTAS MPC-PB"
    assert any(b.label == "Entrar com Google" for b in app.button)
    assert not any(getattr(b, "key", None) == "open_portarias" for b in app.button)


@pytest.mark.parametrize("backend", ["sqlite", "postgresql"])
def test_portal_navigation_and_lazy_return(store, pg_store, monkeypatch, backend):
    database = pg_store if backend == "postgresql" else store
    from tests.access_testing import enable_login

    enable_login(monkeypatch, database)
    calls = []

    def factory():
        calls.append(True)
        return database

    monkeypatch.setattr("database.store.Store", factory)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert calls
    app.button(key="open_portarias").click().run()
    assert app.sidebar.radio(key="nav").value == "Nova Portaria"
    for page in ("Histórico", "Procuradores", "Configurações", "Nova Portaria"):
        app.sidebar.radio(key="nav").set_value(page).run()
        assert not app.exception and not app.error
    seed = sample(database)
    app.session_state["editor_seed"] = seed
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    assert app.session_state["editor_seed"] == seed
    app.sidebar.radio(key="portal_module").set_value("Portarias").run()
    assert app.session_state["editor_seed"] == seed
    assert not app.exception and not app.error


def test_home_shows_only_authorized_modules(store, monkeypatch):
    from tests.access_testing import seed_access

    seed_access(
        store,
        email="parcial@test.local",
        perfil="USUARIO",
        pode_portarias=False,
        pode_agenda=True,
        pode_oficios=False,
        pode_admin=False,
    )
    monkeypatch.setattr(
        "services.access.oidc_identity",
        lambda: {
            "email": "parcial@test.local",
            "name": "Parcial",
            "email_verified": True,
        },
    )
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception and not app.error
    keys = {getattr(b, "key", None) for b in app.button}
    assert "open_agenda" in keys
    assert "open_portarias" not in keys
    assert "open_oficios" not in keys
    assert "open_admin" not in keys
    portal = next(
        r for r in app.sidebar.radio if getattr(r, "key", None) == "portal_module"
    )
    assert "Agenda" in portal.options
    assert "Ofícios" not in portal.options
    assert "Portarias" not in portal.options


def test_denied_google_account_does_not_open_portal(store, monkeypatch):
    monkeypatch.setattr(
        "services.access.oidc_identity",
        lambda: {
            "email": "intruso@test.local",
            "name": "Intruso",
            "email_verified": True,
        },
    )
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert "Acesso não autorizado" in "".join(e.value for e in app.error)
    assert not any(getattr(b, "key", None) == "open_portarias" for b in app.button)


def test_future_modules_have_no_routes_or_side_effects():
    assert [module.key for module in MODULES if module.active] == [
        "portarias",
        "oficios",
        "agenda",
    ]
    assert len({module.key for module in MODULES}) == 5


def test_home_card_icons_are_complete_material_names():
    import inspect

    from portal import home

    known = {
        "description",
        "article",
        "mail",
        "calendar_month",
        "bar_chart",
        "manage_accounts",
    }
    assert {module.icon for module in MODULES} <= known
    source = inspect.getsource(home)
    assert "manage_accounts" in source
    assert "admin_panel" not in source
