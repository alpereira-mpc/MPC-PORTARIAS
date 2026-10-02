"""Unit tests for PostgreSQL read scopes; no database server is contacted."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import inspect
from types import SimpleNamespace

import psycopg
import pytest

from database.postgresql import Connection, DatabaseUnavailable, PostgresBackend


class Raw:
    def __init__(self, *, fail_statement=None, error=None):
        self.commands = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False
        self.fail_statement = fail_statement
        self.error = error

    def execute(self, statement, values=None, prepare=None):
        self.commands.append((statement, values, prepare))
        if self.error is not None or (
            self.fail_statement and self.fail_statement in str(statement)
        ):
            raise self.error or psycopg.OperationalError("write interrupted")
        return SimpleNamespace(rowcount=0, fetchone=lambda: None, description=[])

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


class Pool:
    def __init__(self, *connections):
        self.available = list(connections)
        self.acquisitions = 0
        self.returned = []

    def getconn(self):
        self.acquisitions += 1
        return self.available.pop(0)

    def putconn(self, connection):
        self.returned.append(connection)


@pytest.fixture
def backend(monkeypatch):
    import database.pool as pool_module

    raws = [Raw() for _ in range(4)]
    pool = Pool(*raws)
    monkeypatch.setattr(
        pool_module, "resource", lambda _options: SimpleNamespace(pool=pool)
    )
    return PostgresBackend("postgresql://localhost/test", schema="mpc_test"), pool, raws


def test_warm_pool_checkout_skips_ping_until_connection_is_idle(monkeypatch):
    import database.pool as pool_module

    clock = [100.0]
    calls = []
    connection = SimpleNamespace()
    monkeypatch.setattr(pool_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        pool_module.ConnectionPool,
        "check_connection",
        lambda raw: calls.append(raw),
    )

    pool_module.check_connection(connection)
    pool_module.mark_connection_idle(connection)
    clock[0] += pool_module.HEALTH_CHECK_AFTER_IDLE_SECONDS - 1
    pool_module.check_connection(connection)
    clock[0] += 2
    pool_module.check_connection(connection)

    assert calls == [connection, connection]


def test_broken_connection_check_remains_safely_sanitized(monkeypatch):
    import database.pool as pool_module

    connection = SimpleNamespace()
    monkeypatch.setattr(
        pool_module.ConnectionPool,
        "check_connection",
        lambda _raw: (_ for _ in ()).throw(psycopg.OperationalError("internal dsn")),
    )

    with pytest.raises(
        psycopg.OperationalError, match="Conexão PostgreSQL interrompida"
    ) as error:
        pool_module.check_connection(connection)
    assert "internal dsn" not in str(error.value)


def test_nested_read_scope_reuses_one_connection_and_one_commit(backend):
    subject, pool, raws = backend

    with subject.connection(read_only=True) as outer:
        with subject.connection(read_only=True) as nested:
            assert nested is outer

    assert pool.acquisitions == 1
    assert raws[0].commits == 1
    assert all(
        "pg_advisory_xact_lock" not in str(command[0]) for command in raws[0].commands
    )
    assert "search_path" in repr(raws[0].commands[0][0])


def test_portal_module_fragment_opens_one_postgres_read_scope(monkeypatch):
    import portal

    calls = []

    @contextmanager
    def connection(*, read_only):
        calls.append(read_only)
        yield object()

    store = SimpleNamespace(backend="postgresql", connection=connection)
    monkeypatch.setattr(portal, "phase", lambda _name: nullcontext())
    renderer = inspect.unwrap(portal._render_module_fragment)
    renderer(
        lambda received_store, _principal: calls.append(received_store), store, None
    )

    assert calls == [True, store]


def test_writer_keeps_lock_and_commit(backend):
    subject, pool, raws = backend

    with subject.connection() as connection:
        assert connection.read_only is False

    assert pool.acquisitions == 1
    assert raws[0].commits == 1
    assert any(
        "pg_advisory_xact_lock" in str(command[0]) for command in raws[0].commands
    )


def test_writer_is_not_reused_inside_read_only_scope(backend):
    subject, pool, raws = backend

    with subject.connection(read_only=True) as reader:
        with subject.connection() as writer:
            assert writer is not reader
            assert writer.read_only is False

    assert pool.acquisitions == 2
    assert any(
        "pg_advisory_xact_lock" in str(command[0]) for command in raws[1].commands
    )


def test_read_scope_rolls_back_on_exception(backend):
    subject, _pool, raws = backend

    with pytest.raises(RuntimeError, match="interrupted"):
        with subject.connection(read_only=True):
            raise RuntimeError("interrupted")

    assert raws[0].commits == 0
    assert raws[0].rollbacks == 1


def test_postgres_error_never_exposes_connection_string(monkeypatch):
    import database.pool as pool_module

    raw = Raw(error=psycopg.OperationalError("postgresql://secret@example.invalid/db"))
    monkeypatch.setattr(
        pool_module,
        "resource",
        lambda _options: SimpleNamespace(pool=Pool(raw)),
    )
    subject = PostgresBackend("postgresql://localhost/test", schema="mpc_test")

    with pytest.raises(DatabaseUnavailable) as error:
        with subject.connection(read_only=True):
            pass
    assert "secret@example.invalid" not in str(error.value)


def test_broken_connection_is_closed_and_not_marked_as_warm(monkeypatch):
    import database.pool as pool_module

    raw = Raw(error=psycopg.OperationalError("socket lost"))
    raw.rollback = lambda: (_ for _ in ()).throw(psycopg.OperationalError("lost"))
    pool = Pool(raw)
    monkeypatch.setattr(
        pool_module, "resource", lambda _options: SimpleNamespace(pool=pool)
    )
    monkeypatch.setattr(
        pool_module,
        "mark_connection_idle",
        lambda _raw: pytest.fail("socket broken must not be marked warm"),
    )
    subject = PostgresBackend("postgresql://localhost/test", schema="mpc_test")

    with pytest.raises(DatabaseUnavailable):
        with subject.connection(read_only=True):
            pass
    assert raw.closed is True
    assert pool.returned == [raw]


def test_contextvar_does_not_share_read_connection_with_another_thread(backend):
    subject, pool, _raws = backend

    with subject.connection(read_only=True) as parent:
        with ThreadPoolExecutor(max_workers=1) as workers:
            child = workers.submit(_thread_connection, subject).result()
        assert child is not parent

    assert pool.acquisitions == 2


def _thread_connection(subject):
    with subject.connection(read_only=True) as connection:
        return connection


def test_mutation_commit_invalidates_and_is_not_retried(backend):
    subject, _pool, raws = backend
    invalidated = []
    connection = Connection(raws[0], lambda tables: invalidated.append(set(tables)))
    connection.changed.add("tarefas")
    connection.commit()
    assert invalidated == [{"tarefas"}]

    raws[0].fail_statement = "INSERT"
    with pytest.raises(DatabaseUnavailable):
        with subject.connection() as writer:
            writer.execute("INSERT INTO tarefas(id) VALUES(?)", (1,))
    assert sum("INSERT" in str(command[0]) for command in raws[0].commands) == 1
    assert raws[0].rollbacks == 1
