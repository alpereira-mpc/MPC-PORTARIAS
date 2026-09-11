import os
import subprocess
import sys

from database.agenda import AgendaStore
from database.store import ROOT, schema_key_of, unwrap_store
from services.ui_store import DisplayStore, display_store, persistence_store


def test_fresh_process_exports_display_and_persistence_store():
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    script = (
        "from services.ui_store import display_store, persistence_store\n"
        "from database.store import unwrap_store\n"
        "assert callable(display_store) and callable(persistence_store)\n"
        "assert persistence_store(None) is None\n"
        "assert unwrap_store(None) is None\n"
    )
    subprocess.check_call([sys.executable, "-c", script], cwd=str(ROOT), env=env)


def test_app_entrypoint_does_not_depend_on_ui_store_persistence_symbol():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    assert "from services.ui_store import display_store, persistence_store" not in source
    assert "unwrap_store" in source
    subprocess.check_call(
        [sys.executable, "-m", "py_compile", str(ROOT / "app.py")],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT)},
    )


def test_unwrap_and_schema_key_survive_display_proxy(store):
    wrapped = DisplayStore(store)
    assert persistence_store(wrapped) is store
    assert unwrap_store(wrapped) is store
    assert wrapped.schema_key == store.schema_key
    assert schema_key_of(wrapped) == store.schema_key
    shown = display_store(wrapped)
    if store.backend == "postgresql":
        assert shown._store is store
    else:
        assert shown is store


def test_schema_key_fallback_without_property(store):
    class Legacy:
        backend = "sqlite"
        path = store.path
        _postgres = None

    assert schema_key_of(Legacy()) == "sqlite:" + str(store.path)


def test_agenda_store_accepts_display_proxy(store):
    agenda = AgendaStore(DisplayStore(store))
    assert agenda.store is store
    assert "elvira" in agenda.bindings or agenda.bindings is not None
