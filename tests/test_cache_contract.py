"""Exercise the production Store/cache API against guarded, real PostgreSQL."""

import os
import subprocess
import sys

import pytest

from database.postgresql import PostgresBackend
from database.store import Store, ROOT
from services.ui_store import DisplayStore
from tests.test_postgresql import pg_url, pg_store


@pytest.mark.parametrize("missing_backend_method", [False, True])
def test_real_store_display_cache_contract(
    pg_store, monkeypatch, missing_backend_method
):
    assert type(pg_store) is Store
    assert type(pg_store._postgres) is PostgresBackend
    if missing_backend_method:
        # Reproduce the exact Cloud defect on the real class, without replacing
        # Store, its connection, SQL execution, or returned database data.
        monkeypatch.delattr(PostgresBackend, "cache_key")
        with pytest.raises(AttributeError, match="has no attribute 'cache_key'"):
            pg_store._postgres.cache_key(("configuracoes",))

    ui = DisplayStore(pg_store)
    before = pg_store.read_cache_key(("configuracoes",))
    people_key = pg_store.read_cache_key(("procuradores",))
    assert ui.settings()["sequence_confirmed"] == "1"
    assert len(ui.catalog("procuradores")) == 7
    pg_store.configure(cache_contract="updated")
    assert pg_store.read_cache_key(("configuracoes",)) != before
    assert pg_store.read_cache_key(("procuradores",)) == people_key
    assert ui.settings()["cache_contract"] == "updated"
    member = ui.catalog("procuradores")[0]
    member["nome"] = "Teste de contrato"
    pg_store.save_member(member)
    assert ui.catalog("procuradores")[0]["nome"] == "Teste de contrato"


def test_production_import_in_fresh_process(pg_store, pg_url):
    # No module/class monkeypatches in the subprocess: imports match deployment.
    program = """
import os
from pathlib import Path
from database.store import Store, ROOT
from database.postgresql import PostgresBackend
from services.ui_store import display_store
store = Store(database_url=os.environ['MPC_TEST_POSTGRES_URL'],
              postgres_schema=os.environ['MPC_TEST_CACHE_SCHEMA'])
assert type(store._postgres) is PostgresBackend
assert callable(PostgresBackend.cache_key)
assert store.read_cache_key(('configuracoes',)) == store._postgres.cache_key(('configuracoes',))
ui = display_store(store)
assert ui.settings()['sequence_confirmed'] == '1'
assert len(ui.catalog('procuradores')) == 7
store.configure(cache_contract='fresh-process')
assert ui.settings()['cache_contract'] == 'fresh-process'
print('cache-contract-ok')
"""
    env = dict(
        os.environ,
        MPC_TEST_POSTGRES_URL=pg_url,
        MPC_TEST_CACHE_SCHEMA=pg_store._postgres.schema,
    )
    env.pop("DATABASE_URL", None)
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "cache-contract-ok" in result.stdout
