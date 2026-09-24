import inspect


def test_official_product_version_is_the_semantic_baseline():
    from services.versioning import APP_VERSION

    assert APP_VERSION == "1.0.0"


def test_version_has_one_python_source_and_no_runtime_git_or_disk_lookup():
    from services import system_health, versioning

    assert inspect.getsource(versioning).count('APP_VERSION = "1.0.0"') == 1
    health_source = inspect.getsource(system_health)
    assert "read_text" not in inspect.getsource(system_health.app_version)
    assert "subprocess" not in health_source
    assert system_health.app_version() == versioning.APP_VERSION
    assert system_health.git_build() is None


def test_sidebar_renders_the_official_version(store, monkeypatch):
    from database.store import ROOT
    from streamlit.testing.v1 import AppTest
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()

    assert not app.exception and not app.error
    assert any(caption.value == "Versão 1.0.0" for caption in app.sidebar.caption)
