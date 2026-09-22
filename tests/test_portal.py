import sqlite3

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from database.store import ROOT
from portal import MODULES
from services.branding import APP_NAME, APP_SHORT_SUBTITLE, APP_SUBTITLE
from tests.cases import sample
from tests.test_postgresql import pg_store, pg_url


def _visible_text(app):
    parts = []
    for attr in ("markdown", "title", "subheader", "caption", "text"):
        for item in getattr(app, attr, []) or []:
            parts.append(str(getattr(item, "value", item)))
    return "\n".join(parts)


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
    visible = _visible_text(app)
    assert APP_NAME in visible
    assert APP_SUBTITLE in visible
    assert "Acesso restrito" in visible
    assert not any(getattr(t, "value", "") == "FERRAMENTAS MPC-PB" for t in app.title)
    assert any(b.label == "Entrar com Gmail" for b in app.button)
    assert any(getattr(b, "key", None) == "oidc_gmail_login" for b in app.button)
    assert any(b.label == "Solicitar acesso" for b in app.button)
    assert "Ainda não possui acesso?" in visible
    assert "Cadastre-se" not in visible
    assert "Utilize uma conta previamente autorizada do domínio @tce.pb.gov.br." in visible
    app.button(key="access_request_open").click().run()
    assert not app.exception and not app.error
    assert any(i.label == "Nome completo" for i in app.text_input)
    assert not any(getattr(b, "key", None) == "open_portarias" for b in app.button)


def test_brand_assets_are_packaged_with_the_repository():
    from inspect import getsource

    from services import branding

    assert branding.SIDEBAR_LOGO.is_file()
    assert branding.HEADER_IMAGE.is_file()
    assert branding.SIDEBAR_LOGO.relative_to(branding.ROOT).parts[0] == "assets"
    assert branding.HEADER_IMAGE.relative_to(branding.ROOT).parts[0] == "assets"
    assert branding.SIDEBAR_LOGO.name == "mpcpb_logo_sidebar_transparent.png"
    assert branding.HEADER_IMAGE.name == "mpcpb_header_horizontal_transparent.png"
    assert branding.APP_NAME == "Ferramentas MPC-PB"
    assert branding.APP_SHORT_SUBTITLE == "Portal Integrado de Gestão e Apoio Operacional"
    assert branding.APP_SUBTITLE == (
        branding.APP_SHORT_SUBTITLE + " do Ministério Público de Contas da Paraíba"
    )
    assert "MPC-PB Tools" not in branding.APP_NAME
    assert "MPC-PB Tools" not in branding.APP_SUBTITLE
    sidebar_src = getsource(branding.render_sidebar_brand)
    assert "APP_NAME" not in sidebar_src
    assert "mpc-sidebar-name" not in sidebar_src
    identity_src = getsource(branding.render_app_identity)
    assert "mpc-identity-name" in identity_src
    assert 'variant == "login"' in identity_src
    assert 'variant == "home"' in identity_src
    styles = getsource(branding._brand_styles)
    assert ".mpc-identity--login .mpc-identity-name" in styles
    assert "3.125rem" in styles
    assert ".mpc-identity--home .mpc-identity-name" in styles
    assert "2.375rem" in styles
    from portal import render_login

    login_src = getsource(render_login)
    assert 'variant="login"' in login_src
    assert "max-width:50rem" in login_src
    assert 'class="login-domain-hint"' in login_src
    assert "font-size:18px!important;font-weight:400!important" in login_src
    assert "color:#000000!important" in login_src


def test_home_uses_page_title_not_generic_banner(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    headings = [h.value for h in app.subheader] + [h.value for h in app.title]
    visible = _visible_text(app)
    assert APP_NAME in visible
    assert APP_SHORT_SUBTITLE in visible
    assert APP_SUBTITLE not in visible
    assert "Selecione uma ferramenta para iniciar." in visible
    assert app.sidebar.radio(key="portal_module").value == "Início"
    assert "FERRAMENTAS MPC-PB" not in headings
    assert "AGENDA DOS PROCURADORES" not in headings
    assert "AGENDA E AFASTAMENTOS DOS PROCURADORES" not in headings

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


def test_portarias_sidebar_initializes_navigation_from_fresh_or_legacy_state(
    store, monkeypatch
):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio(key="portal_module").set_value("Portarias").run()
    assert not app.exception
    assert app.sidebar.radio(key="nav").value == "Nova Portaria"
    assert len([radio for radio in app.sidebar.radio if radio.key == "nav"]) == 1
    assert len([box for box in app.sidebar.selectbox if box.label == "Tema"]) == 1
    assert "Nova Portaria" in _visible_text(app)

    app.sidebar.radio(key="portal_module").set_value("Início").run()
    app.session_state["nav"] = "Página legada"
    app.sidebar.radio(key="portal_module").set_value("Portarias").run()
    assert not app.exception
    assert app.sidebar.radio(key="nav").value == "Nova Portaria"


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
    assert "Agenda e Afastamentos" in portal.options
    assert "Agenda" not in portal.options
    assert "Ofícios" not in portal.options
    assert "Portarias" not in portal.options
    from inspect import getsource
    from portal import render_portal

    assert '"Agenda e Afastamentos" if option == "Agenda"' in getsource(render_portal)


def test_relatorios_card_menu_and_route_follow_module_permission(store, monkeypatch):
    from tests.access_testing import seed_access

    seed_access(
        store,
        email="relatorios.portal@test.local",
        nome="Leitor de Relatórios",
        perfil="USUARIO",
        pode_portarias=False,
        pode_agenda=False,
        pode_oficios=False,
        pode_memorandos=False,
        pode_admin=False,
        pode_relatorios=True,
    )
    monkeypatch.setattr(
        "services.access.oidc_identity",
        lambda: {"email": "relatorios.portal@test.local", "name": "Leitor", "email_verified": True},
    )
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert any(getattr(button, "key", None) == "open_relatorios" for button in app.button)
    portal = next(radio for radio in app.sidebar.radio if radio.key == "portal_module")
    assert "Relatórios e Indicadores" in portal.options
    portal.set_value("Relatórios e Indicadores").run()
    assert not app.exception
    assert next(radio for radio in app.radio if radio.label == "Seção").options == [
        "Produção Mensal",
        "Visão Atual",
    ]


def test_relatorios_is_hidden_and_manipulated_navigation_is_reset_without_permission(store, monkeypatch):
    from tests.access_testing import seed_access

    seed_access(
        store,
        email="sem.relatorios@test.local",
        nome="Sem Relatórios",
        perfil="USUARIO",
        pode_portarias=False,
        pode_agenda=True,
        pode_oficios=False,
        pode_memorandos=False,
        pode_admin=False,
        pode_relatorios=False,
    )
    monkeypatch.setattr(
        "services.access.oidc_identity",
        lambda: {"email": "sem.relatorios@test.local", "name": "Sem", "email_verified": True},
    )
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not any(getattr(button, "key", None) == "open_relatorios" for button in app.button)
    portal = next(radio for radio in app.sidebar.radio if radio.key == "portal_module")
    assert "Relatórios e Indicadores" not in portal.options
    from portal import PORTAL_NAV_REQUEST

    app.session_state[PORTAL_NAV_REQUEST] = {
        "module": "Relatórios e Indicadores",
        "state": {},
    }
    app.run()
    assert next(radio for radio in app.sidebar.radio if radio.key == "portal_module").value == "Início"


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
    visible = _visible_text(app)
    assert APP_NAME in visible
    assert APP_SUBTITLE in visible
    assert not any(getattr(b, "key", None) == "open_portarias" for b in app.button)


def test_future_modules_have_no_routes_or_side_effects():
    assert [module.key for module in MODULES if module.active] == [
        "portarias",
        "memorandos",
        "oficios",
        "agenda",
        "tarefas",
        "relatorios",
        "representacoes",
        "ouvidoria",
    ]
    assert [module.key for module in MODULES if not module.active] == []
    assert len({module.key for module in MODULES}) == 8


def test_home_card_icons_are_complete_material_names():
    import inspect

    from portal import ADMIN_MODULE, home, visible_modules

    known = {
        "description",
        "article",
        "mail",
        "calendar_month",
        "bar_chart",
        "check_circle",
        "manage_accounts",
        "gavel",
        "forum",
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
        pode_relatorios=flags.get("relatorios", False),
        pode_representacoes=flags.get("representacoes", False),
        pode_ouvidoria=flags.get("ouvidoria", False),
    )


def test_home_visible_modules_active_first_and_authorization():
    from portal import visible_modules

    admin = visible_modules(
        _principal(portarias=True, agenda=True, oficios=True, admin=True, relatorios=True)
    )
    assert [m.key for m in admin] == [
        "portarias",
        "oficios",
        "agenda",
        "tarefas",
        "relatorios",
        "admin",
    ]
    assert [m.active for m in admin] == [True, True, True, True, True, True]
    # Memorandos is hidden without its independent permission, so an odd final row is valid.
    assert len(admin) == 6

    with_memo = visible_modules(
        _principal(portarias=True, agenda=True, oficios=True, memorandos=True, admin=True, relatorios=True)
    )
    assert [m.key for m in with_memo] == [
        "portarias",
        "memorandos",
        "oficios",
        "agenda",
        "tarefas",
        "relatorios",
        "admin",
    ]

    no_admin = visible_modules(
        _principal(portarias=True, agenda=True, oficios=True, admin=False)
    )
    assert [m.key for m in no_admin] == [
        "portarias",
        "oficios",
        "agenda",
        "tarefas",
    ]
    assert "admin" not in {m.key for m in no_admin}
    assert len(no_admin) == 4

    partial = visible_modules(_principal(agenda=True, relatorios=True))
    assert [m.key for m in partial] == ["agenda", "tarefas", "relatorios"]
    assert all(m.active for m in partial)


def test_home_grid_uses_two_columns_from_first_row():
    import inspect

    from portal import _home_layout_style, card, home

    source = inspect.getsource(home)
    assert "st.columns(2)" in source
    assert "card(visible[0])" not in source
    assert "mpc-home-soon-slot" in inspect.getsource(card)
    layout = inspect.getsource(_home_layout_style).replace(" ", "")
    assert "min-height:19.5rem" in layout
    mobile = layout[layout.index("@media(max-width:768px)") :]
    assert "height:auto;min-height:0;flex-grow:0" in mobile
    assert "height:auto;min-height:0;justify-content:flex-start" in mobile


def test_home_cards_render_in_authorized_active_first_order(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    captions = [c.value for c in app.caption]
    labels = [
        "PORTARIAS",
        "MEMORANDOS",
        "OFÍCIOS",
        "AGENDA",
        "TAREFAS",
        "RELATÓRIOS",
        "REPRESENTAÇÕES",
        "OUVIDORIA",
        "ADMINISTRAÇÃO",
    ]
    indexes = [captions.index(label) for label in labels]
    assert indexes == sorted(indexes)
    keys = [getattr(b, "key", None) for b in app.button]
    assert keys.index("open_portarias") < keys.index("open_memorandos")
    assert keys.index("open_memorandos") < keys.index("open_oficios")
    assert keys.index("open_oficios") < keys.index("open_agenda")
    assert "open_representacoes" in keys
    assert "open_ouvidoria" in keys
    assert keys.index("open_representacoes") < keys.index("open_ouvidoria")
    assert keys.index("open_ouvidoria") < keys.index("open_admin")



def test_home_portarias_agenda_oficios_admin_roundtrip(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception and not app.error
    for module in (
        "Portarias",
        "Início",
        "Memorandos",
        "Ofícios",
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


def _no_callback_rerun_warning(app):
    texts = []
    for attr in ("warning", "error", "exception"):
        for item in getattr(app, attr, []) or []:
            texts.append(str(getattr(item, "value", item)))
    blob = "\n".join(texts)
    assert "within a callback is a no-op" not in blob


def test_queue_portal_navigation_does_not_rerun(monkeypatch):
    import portal

    reruns = []
    monkeypatch.setattr(portal.st, "session_state", {})
    monkeypatch.setattr(portal.st, "rerun", lambda **kwargs: reruns.append(kwargs))
    portal.queue_portal_navigation("Memorandos", memorandos_nav="Visão Geral")
    assert not reruns
    assert portal.PORTAL_NAV_REQUEST in portal.st.session_state
    portal.request_portal_navigation("Agenda")
    assert reruns == [{"scope": "app"}]


def test_home_card_callbacks_only_queue_navigation():
    import inspect

    import portal

    for fn in (
        portal.open_portarias,
        portal.open_home,
        portal.open_agenda,
        portal.open_oficios,
        portal.open_memorandos,
        portal.open_admin,
    ):
        source = inspect.getsource(fn)
        assert "queue_portal_navigation" in source
        assert "st.rerun()" not in source
        assert "request_portal_navigation" not in source
    card_source = inspect.getsource(portal.card)
    assert "on_click=" in card_source
    assert "open_memorandos" in card_source
    assert inspect.getsource(portal.open_pendencias).count("request_portal_navigation")
    assert 'st.rerun(scope="app")' in inspect.getsource(
        portal.request_portal_navigation
    )
    assert "st.rerun()" not in inspect.getsource(portal.queue_portal_navigation)
    assert "st.rerun()" not in inspect.getsource(portal.queue_alerts_view)
    assert 'st.rerun(scope="app")' in inspect.getsource(portal.request_alerts_view)
    assert 'st.rerun(scope="app")' in inspect.getsource(portal.close_alerts_view)


def test_sidebar_logo_queues_the_same_home_navigation(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    for module in ("Agenda", "Ofícios", "Memorandos", "Relatórios e Indicadores"):
        app.sidebar.radio(key="portal_module").set_value(module).run()
        app.button(key="sidebar_home").click().run()
        assert not app.exception
        assert app.sidebar.radio(key="portal_module").value == "Início"


def test_home_cards_and_refresh_do_not_reapply_navigation(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    from portal import PORTAL_NAV_REQUEST

    app.button(key="open_memorandos").click().run()
    assert not app.exception
    _no_callback_rerun_warning(app)
    assert app.sidebar.radio(key="portal_module").value == "Memorandos"
    assert PORTAL_NAV_REQUEST not in app.session_state
    app.run()
    assert app.sidebar.radio(key="portal_module").value == "Memorandos"
    assert PORTAL_NAV_REQUEST not in app.session_state
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    app.button(key="open_portarias").click().run()
    assert not app.exception
    _no_callback_rerun_warning(app)
    assert app.sidebar.radio(key="portal_module").value == "Portarias"


def test_logout_helper_is_defined_and_delegates_to_streamlit():
    import inspect

    import portal

    source = inspect.getsource(portal._logout)
    assert "st.logout()" in source
    assert "st.session_state" in source
    portal_source = inspect.getsource(portal.render_portal)
    assert "_logout()" in portal_source
    assert "\n            logout()" not in portal_source

