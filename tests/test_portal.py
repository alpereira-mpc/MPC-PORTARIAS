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
    assert app.title[0].value == "Acesso restrito"
    assert not any(
        getattr(t, "value", "") == "FERRAMENTAS MPC-PB" for t in app.title
    )
    assert any(b.label == "Entrar com Gmail" for b in app.button)
    assert any(getattr(b, "key", None) == "oidc_gmail_login" for b in app.button)
    assert any(
        getattr(c, "value", "")
        == "Utilize uma conta previamente autorizada do domínio @tce.pb.gov.br."
        for c in app.caption
    )
    assert not any(getattr(b, "key", None) == "open_portarias" for b in app.button)


def test_brand_assets_are_packaged_with_the_repository():
    from services import branding

    assert branding.SIDEBAR_LOGO.is_file()
    assert branding.HEADER_IMAGE.is_file()
    assert branding.SIDEBAR_LOGO.relative_to(branding.ROOT).parts[0] == "assets"
    assert branding.HEADER_IMAGE.relative_to(branding.ROOT).parts[0] == "assets"
    assert branding.SIDEBAR_LOGO.name == "mpcpb_logo_sidebar.png"
    assert branding.HEADER_IMAGE.name == "mpcpb_header_horizontal.png"


def test_home_uses_page_title_not_generic_banner(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    headings = [h.value for h in app.subheader] + [h.value for h in app.title]
    assert "Início" in headings
    assert "FERRAMENTAS MPC-PB" not in headings
    assert "AGENDA DOS PROCURADORES" not in headings

    import streamlit as st

    monkeypatch.setattr("services.access.oidc_identity", lambda: None)
    started = []
    monkeypatch.setattr(st, "login", lambda: started.append(True))
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="oidc_gmail_login").click().run()
    assert not app.exception
    assert started == [True]
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

    from portal import ADMIN_MODULE, home, visible_modules

    known = {
        "description",
        "article",
        "mail",
        "calendar_month",
        "bar_chart",
        "manage_accounts",
    }
    assert {module.icon for module in MODULES} | {ADMIN_MODULE.icon} <= known
    source = inspect.getsource(visible_modules) + inspect.getsource(home)
    assert "ADMIN_MODULE" in source
    assert "admin_panel" not in source


def _principal(**flags):
    from services.access import Principal

    return Principal(
        id=1,
        nome="Teste",
        email="layout@test.local",
        perfil="USUARIO",
        ativo=True,
        pode_portarias=flags.get("portarias", False),
        pode_agenda=flags.get("agenda", False),
        pode_oficios=flags.get("oficios", False),
        pode_admin=flags.get("admin", False),
        gabinetes=(),
        pode_memorandos=flags.get("memorandos", False),
    )


def test_home_visible_modules_active_first_and_authorization():
    from portal import visible_modules

    admin = visible_modules(
        _principal(portarias=True, agenda=True, oficios=True, admin=True)
    )
    assert [m.key for m in admin] == [
        "portarias",
        "oficios",
        "agenda",
        "admin",
        "relatorios",
    ]
    assert [m.active for m in admin] == [True, True, True, True, False]
    # Memorandos is hidden without its independent permission, so an odd final row is valid.
    assert len(admin) == 5

    no_admin = visible_modules(
        _principal(portarias=True, agenda=True, oficios=True, admin=False)
    )
    assert [m.key for m in no_admin] == [
        "portarias",
        "oficios",
        "agenda",
        "relatorios",
    ]
    assert "admin" not in {m.key for m in no_admin}
    assert len(no_admin) == 4

    partial = visible_modules(_principal(agenda=True))
    assert [m.key for m in partial] == ["agenda", "relatorios"]
    assert all(m.active for m in partial[:1])
    assert not any(m.active for m in partial[1:])


def test_home_grid_uses_two_columns_from_first_row():
    import inspect

    from portal import _home_layout_style, card, home

    source = inspect.getsource(home)
    assert "st.columns(2)" in source
    assert "card(visible[0])" not in source
    assert "mpc-home-soon-slot" in inspect.getsource(card)
    assert "min-height:19.5rem" in inspect.getsource(_home_layout_style)


def test_home_cards_render_in_authorized_active_first_order(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    captions = [c.value for c in app.caption]
    labels = [
        "PORTARIAS",
        "OFÍCIOS",
        "AGENDA",
        "ADMINISTRAÇÃO",
        "MEMORANDOS",
        "RELATÓRIOS",
    ]
    indexes = [captions.index(label) for label in labels]
    assert indexes == sorted(indexes)
    keys = [getattr(b, "key", None) for b in app.button]
    assert keys.index("open_portarias") < keys.index("open_oficios")
    assert keys.index("open_oficios") < keys.index("open_agenda")
    assert keys.index("open_agenda") < keys.index("open_admin")



def test_home_portarias_agenda_oficios_admin_roundtrip(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception and not app.error
    for module in (
        "Portarias",
        "Início",
        "Agenda",
        "Início",
        "Ofícios",
        "Administração",
        "Início",
    ):
        app.sidebar.radio(key="portal_module").set_value(module).run()
        assert not app.exception, module
        assert not app.error, module
    assert app.session_state["_mpc_store"] is store
    if "agenda_store" in app.session_state:
        assert app.session_state["agenda_store"].store is store


def test_authenticated_user_can_logout_to_restricted_screen(store, monkeypatch):
    import streamlit as st

    from tests.access_testing import TEST_IDENTITY, enable_login

    session = {"identity": dict(TEST_IDENTITY)}
    enable_login(monkeypatch, store)
    monkeypatch.setattr(
        "services.access.oidc_identity", lambda: session["identity"]
    )
    monkeypatch.setattr("database.store.Store", lambda: store)
    ended = []

    def fake_logout():
        session["identity"] = None
        ended.append(True)

    monkeypatch.setattr(st, "logout", fake_logout)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception and not app.error
    assert any(getattr(b, "key", None) == "portal_logout" for b in app.button)
    assert "_access_cache" in app.session_state
    app.sidebar.radio(key="portal_module").set_value("Agenda").run()
    assert not app.exception
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    app.button(key="portal_logout").click().run()
    if not any(b.label == "Entrar com Gmail" for b in app.button):
        app.run()
    assert not app.exception and not app.error
    assert ended == [True]
    assert any(b.label == "Entrar com Gmail" for b in app.button)
    assert not any(getattr(b, "key", None) == "portal_logout" for b in app.button)
    assert "_access_cache" not in app.session_state
    session["identity"] = dict(TEST_IDENTITY)
    app.run()
    assert not app.exception
    assert any(getattr(b, "key", None) == "portal_logout" for b in app.button)


def test_denied_user_logout_uses_native_oidc(store, monkeypatch):
    import streamlit as st

    session = {
        "identity": {
            "email": "intruso@test.local",
            "name": "Intruso",
            "email_verified": True,
        }
    }
    monkeypatch.setattr(
        "services.access.oidc_identity", lambda: session["identity"]
    )
    monkeypatch.setattr("database.store.Store", lambda: store)
    ended = []

    def fake_logout():
        session["identity"] = None
        ended.append(True)

    monkeypatch.setattr(st, "logout", fake_logout)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert "Acesso não autorizado" in "".join(e.value for e in app.error)
    next(b for b in app.button if b.label == "Sair").click().run()
    if not any(b.label == "Entrar com Gmail" for b in app.button):
        app.run()
    assert not app.exception
    assert ended == [True]
    assert any(b.label == "Entrar com Gmail" for b in app.button)


def test_logout_helper_is_defined_and_delegates_to_streamlit():
    import inspect

    import portal

    source = inspect.getsource(portal._logout)
    assert "st.logout()" in source
    assert "st.session_state" in source
    portal_source = inspect.getsource(portal.render_portal)
    assert "_logout()" in portal_source
    assert "\n            logout()" not in portal_source

