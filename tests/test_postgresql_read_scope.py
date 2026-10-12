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

    def get_stats(self):
        return {}


@pytest.fixture
def backend(monkeypatch):
    import database.pool as pool_module

    raws = [Raw() for _ in range(8)]
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


def test_idle_health_check_has_separate_telemetry(monkeypatch):
    import database.pool as pool_module

    recorded = []
    connection = SimpleNamespace()
    monkeypatch.setattr(pool_module, "performance_active", lambda: True)
    monkeypatch.setattr(pool_module, "record_pool_check", recorded.append)
    monkeypatch.setattr(
        pool_module.ConnectionPool,
        "check_connection",
        lambda _raw: None,
    )

    pool_module.check_connection(connection)

    assert len(recorded) == 1
    assert recorded[0] >= 0


def test_checkout_reports_pool_stat_deltas_without_queries(backend, monkeypatch):
    import database.postgresql as postgresql

    subject, pool, _raws = backend
    snapshots = iter(
        (
            {
                "requests_queued": 2,
                "requests_wait_ms": 100,
                "connections_num": 1,
                "connections_ms": 400,
            },
            {
                "requests_queued": 3,
                "requests_wait_ms": 588,
                "connections_num": 2,
                "connections_ms": 887,
            },
        )
    )
    pool.get_stats = lambda: next(snapshots)
    waits, activity, database_calls = [], [], []
    monkeypatch.setattr(postgresql, "performance_active", lambda: True)
    monkeypatch.setattr(postgresql, "record_pool_wait", waits.append)
    monkeypatch.setattr(
        postgresql,
        "record_pool_activity",
        lambda **values: activity.append(values),
    )
    monkeypatch.setattr(
        postgresql,
        "record_database_call",
        lambda *args, **kwargs: database_calls.append((args, kwargs)),
    )

    with subject.connection(read_only=True):
        pass

    assert len(waits) == 1
    assert activity == [
        {
            "queued": 1,
            "queue_wait_ms": 488,
            "connections": 1,
            "connection_ms": 487,
        }
    ]
    assert all(call[0][0] != "postgres.execute" for call in database_calls)


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


def test_cold_bell_reuses_one_read_transaction_and_hot_cache_uses_none(
    backend, monkeypatch
):
    from services import alerts_ui
    from services.alerts import invalidate_alert_summary

    subject, pool, raws = backend
    state = {}
    store = SimpleNamespace(
        backend="postgresql",
        connection=subject.connection,
    )
    principal = object()
    monkeypatch.setattr(alerts_ui.st, "session_state", state)

    def five_read_sources(received_store, *_args, **_kwargs):
        for _ in range(5):
            with received_store.connection(read_only=True) as connection:
                connection.execute("SELECT 1")
        return {"total": 0}

    monkeypatch.setattr(alerts_ui, "get_alert_summary", five_read_sources)

    five_read_sources(store)
    assert pool.acquisitions == 5
    assert sum(raw.commits for raw in raws[:5]) == 5
    assert sum(len(raw.commands) + raw.commits for raw in raws[:5]) == 15

    alerts_ui.load_bell_summary(store, principal)
    assert pool.acquisitions == 6
    assert raws[5].commits == 1
    assert sum(
        str(command[0]) == "SELECT 1" for command in raws[5].commands
    ) == 5
    assert len(raws[5].commands) + raws[5].commits == 7

    alerts_ui.load_bell_summary(store, principal)
    assert pool.acquisitions == 6

    invalidate_alert_summary(state)
    alerts_ui.load_bell_summary(store, principal)
    assert pool.acquisitions == 7
    assert raws[6].commits == 1


def test_bell_retries_isolated_reads_after_caught_sql_failure(monkeypatch):
    from services import alerts_ui

    class Status:
        name = "INTRANS"

    class ReadConnection:
        def __init__(self):
            self.raw = SimpleNamespace(
                info=SimpleNamespace(transaction_status=Status())
            )

        def execute(self, statement):
            status = self.raw.info.transaction_status
            if statement == "BROKEN":
                status.name = "INERROR"
                raise RuntimeError("sql failure")
            if status.name == "INERROR":
                raise RuntimeError("current transaction is aborted")
            return 1

    class Store:
        backend = "postgresql"

        def __init__(self):
            self.active = None

        @contextmanager
        def connection(self, *, read_only):
            assert read_only is True
            if self.active is not None:
                yield self.active
                return
            connection = ReadConnection()
            self.active = connection
            try:
                yield connection
            finally:
                self.active = None

    store = Store()
    healthy_reads = []
    monkeypatch.setattr(alerts_ui.st, "session_state", {})

    def summary(received_store, *_args, **_kwargs):
        try:
            with received_store.connection(read_only=True) as connection:
                connection.execute("BROKEN")
        except RuntimeError:
            pass
        try:
            with received_store.connection(read_only=True) as connection:
                connection.execute("HEALTHY")
                healthy_reads.append(True)
        except RuntimeError:
            pass
        return {"total": len(healthy_reads)}

    monkeypatch.setattr(alerts_ui, "get_alert_summary", summary)

    payload = alerts_ui.load_bell_summary(store, object())

    assert payload["total"] == 1
    assert healthy_reads == [True]


def test_portal_module_fragment_opens_one_postgres_read_scope(monkeypatch):
    import portal
    from services import access

    calls = []

    @contextmanager
    def connection(*, read_only):
        calls.append(read_only)
        yield object()

    store = SimpleNamespace(
        backend="postgresql",
        connection=connection,
        begin_rerun=lambda: calls.append("begin_rerun"),
    )
    monkeypatch.setattr(portal, "phase", lambda _name: nullcontext())

    @contextmanager
    def authorization_scope(_store, principal):
        yield principal

    monkeypatch.setattr(access, "authorization_scope", authorization_scope)
    renderer = inspect.unwrap(portal._render_module_fragment)
    renderer(
        lambda received_store, _principal: calls.append(received_store),
        store,
        SimpleNamespace(ativo=True),
        "tarefas",
    )

    assert calls == ["begin_rerun", True, store]


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
