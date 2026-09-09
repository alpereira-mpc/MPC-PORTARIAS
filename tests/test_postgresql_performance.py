"""Pool/read-cache checks against the guarded disposable PostgreSQL only."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import psycopg
import pytest

from database.pool import close_pools, resource
from database.postgresql import DatabaseUnavailable, PostgresBackend
from database.store import Store
from tests.test_postgresql import pg_url, pg_store


@pytest.fixture(autouse=True)
def isolated_pool():
    close_pools()
    yield
    close_pools()


def test_reruns_reuse_pool_and_skip_schema(pg_store, pg_url, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Repeated schema check or physical connection")

    pg_store.settings()
    monkeypatch.setattr(PostgresBackend, "_initialize", unexpected)
    monkeypatch.setattr(psycopg.Connection, "connect", unexpected)
    for _ in range(3):
        again = Store(database_url=pg_url, postgres_schema=pg_store._postgres.schema)
        assert again.settings()["sequence_confirmed"] == "1"
        assert again.next_number(2026) == 9


def test_pool_replaces_dead_idle_socket(pg_store, pg_url):
    with pg_store.connection(read_only=True) as c:
        pid = c.raw.info.backend_pid
    with psycopg.connect(pg_url, autocommit=True) as admin:
        assert admin.execute("SELECT pg_terminate_backend(%s)", (pid,)).fetchone()[0]
    with pg_store.connection(read_only=True) as c:
        assert c.raw.info.backend_pid != pid
        assert c.execute("SELECT 1").fetchone()[0] == 1


def test_transaction_failure_rolls_back_before_reuse(pg_store):
    with pytest.raises(DatabaseUnavailable):
        with pg_store.connection() as c:
            c.execute(
                "UPDATE configuracoes SET valor='bad' WHERE chave='sequence_confirmed'"
            )
            c.execute("SELECT 1/0")
    assert pg_store.settings()["sequence_confirmed"] == "1"
    with pytest.raises(RuntimeError):
        with pg_store.connection() as c:
            c.execute(
                "UPDATE configuracoes SET valor='bad' WHERE chave='sequence_confirmed'"
            )
            raise RuntimeError("rollback")
    assert pg_store.settings()["sequence_confirmed"] == "1"


def test_transaction_state_does_not_leak(pg_store):
    with pg_store.connection() as c:
        pg_store.authorize_delete(c, "fake-id")
    with pg_store.connection(read_only=True) as c:
        assert not c.execute(
            "SELECT current_setting('mpc.delete_id', true)"
        ).fetchone()[0]
        assert c.execute("SHOW transaction_read_only").fetchone()[0] == "on"
    with pg_store.connection() as c:
        assert c.execute("SHOW transaction_read_only").fetchone()[0] == "off"


def test_read_does_not_wait_for_writer_lock(pg_store):
    entered, release = Event(), Event()

    def writer():
        with pg_store.connection():
            entered.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=2) as workers:
        future = workers.submit(writer)
        assert entered.wait(5)
        try:
            assert workers.submit(pg_store.next_number, 2026).result(timeout=3) == 9
        finally:
            release.set()
        future.result()


def test_read_transaction_rejects_writes(pg_store):
    with pytest.raises(DatabaseUnavailable, match="25006"):
        with pg_store.connection(read_only=True) as c:
            c.execute("UPDATE sequencias SET ultimo=999")
    assert pg_store.next_number(2026) == 9


def test_catalog_cache_is_rerun_scoped_and_invalidated(pg_store, monkeypatch):
    calls = []
    original = psycopg.Connection.execute

    def execute(self, query, *args, **kwargs):
        if isinstance(query, str) and query.startswith("SELECT * FROM procuradores"):
            calls.append(query)
        return original(self, query, *args, **kwargs)

    monkeypatch.setattr(psycopg.Connection, "execute", execute)
    pg_store.begin_rerun()
    member = pg_store.catalog("procuradores")[0]
    member["nome"] = "Somente teste"
    assert pg_store.catalog("procuradores")[0]["nome"] != member["nome"]
    assert len(calls) == 1
    pg_store.save_member(member)
    assert pg_store.catalog("procuradores")[0]["nome"] == member["nome"]
    assert len(calls) == 2
    pg_store.begin_rerun()
    pg_store.catalog("procuradores")
    assert len(calls) == 3


def test_sequence_and_settings_are_never_cached(pg_store):
    pg_store.begin_rerun()
    assert pg_store.next_number(2026) == 9
    pg_store.set_sequence(2026, 15, True)
    assert pg_store.next_number(2026) == 16
    pg_store.configure(pdf_engine="test")
    assert pg_store.settings()["pdf_engine"] == "test"


def test_sqlite_does_not_enable_cache(store):
    store.begin_rerun()
    assert store._read_cache is None


def test_explicit_migration_still_checks_schema(pg_store, monkeypatch):
    checks = []
    original = pg_store._postgres._initialize

    def initialize(root):
        checks.append(True)
        return original(root)

    monkeypatch.setattr(pg_store._postgres, "_initialize", initialize)
    pg_store.migrate()
    assert checks == [True]


def test_pool_bounds(pg_store):
    pool = resource(pg_store._postgres._options).pool
    assert pool.max_size == 4 and pool.min_size == 0
    assert pool.max_idle == 60 and pool.max_lifetime == 600
    assert pool.timeout == 15 and pool.max_waiting == 32
