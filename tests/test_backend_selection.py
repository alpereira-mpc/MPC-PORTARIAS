import pytest
import streamlit as st
import psycopg

from database import backend_config
from database.postgresql import DatabaseUnavailable, PostgresBackend, Row, parameters
from database.store import Store

FAKE_URL = "postgresql://test:never-a-real-password@invalid.example/test"


def test_sqlite_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr("database.store.DB_PATH", tmp_path / "local.db")
    store = Store()
    assert store.backend == "sqlite"
    assert store.next_number(2026) == 1
    assert "sequence_confirmed" not in store.settings()


@pytest.mark.parametrize("source", ["environment", "secrets"])
def test_select_postgres_without_connecting(tmp_path, monkeypatch, source):
    if source == "environment":
        monkeypatch.setenv("DATABASE_URL", FAKE_URL)
    else:
        monkeypatch.setattr(st, "secrets", {"DATABASE_URL": FAKE_URL})
    initialized = []
    monkeypatch.setattr(
        PostgresBackend, "initialize", lambda self, root: initialized.append(True)
    )
    monkeypatch.setattr("database.store.ROOT", tmp_path)
    store = Store()
    assert store.backend == "postgresql" and initialized == [True]
    assert "password" not in store.location
    assert not list(tmp_path.rglob("*.db"))


def test_environment_precedes_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", FAKE_URL)
    monkeypatch.setattr(st, "secrets", {"DATABASE_URL": "different"})
    assert backend_config.database_url() == FAKE_URL


def test_explicit_sqlite_isolation_even_with_url(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", FAKE_URL)
    store = Store(tmp_path / "isolated.db")
    assert store.backend == "sqlite"


@pytest.mark.parametrize(
    "value", ["", " ", "sqlite:///local.db", "postgresql://bad%zz"]
)
def test_invalid_url_never_falls_back(tmp_path, monkeypatch, value):
    monkeypatch.setenv("DATABASE_URL", value)
    monkeypatch.setattr("database.store.DB_PATH", tmp_path / "must-not-exist.db")
    with pytest.raises(ValueError):
        Store()
    assert not (tmp_path / "must-not-exist.db").exists()


def test_ssl_and_pooler_and_no_credentials_in_errors(monkeypatch, caplog):
    captured = {}

    def fail(**kwargs):
        captured.update(kwargs)
        raise psycopg.OperationalError(FAKE_URL)

    from database.pool import close_pools
    from psycopg_pool import ConnectionPool

    close_pools()
    monkeypatch.setattr(
        psycopg.Connection,
        "connect",
        classmethod(lambda cls, *args, **kwargs: fail(**kwargs)),
    )

    def fast_pool(**kwargs):
        return ConnectionPool(**dict(kwargs, timeout=0.2, reconnect_timeout=0.1))

    monkeypatch.setattr("database.pool.ConnectionPool", fast_pool)
    backend = PostgresBackend(FAKE_URL + "?sslmode=disable")
    with pytest.raises(DatabaseUnavailable) as error:
        with backend.connection():
            pass
    assert captured["sslmode"] == "require"
    assert captured["prepare_threshold"] is None
    assert captured["connect_timeout"] == 10
    assert "never-a-real-password" not in str(error.value) + caplog.text
    assert error.value.__suppress_context__
    close_pools()


def test_parameter_and_row_compatibility():
    assert (
        parameters("SELECT '?', \"?\" FROM tab WHERE v=?")
        == "SELECT '?', \"?\" FROM tab WHERE v=%s"
    )
    row = Row(["a", "b"], (1, "two"))
    assert row[0] == row["a"] == 1
    assert dict(row) == {"a": 1, "b": "two"}
    assert tuple(row) == (1, "two")


@pytest.mark.parametrize("malformed", [False, True])
def test_missing_or_malformed_secret_file(monkeypatch, malformed):
    class Secrets:
        def get(self, key):
            if malformed:
                try:
                    raise ValueError("fake-secret-must-not-be-exposed")
                except ValueError as cause:
                    raise FileNotFoundError("invalid secrets") from cause
            raise FileNotFoundError("no secrets file")

    monkeypatch.setattr(st, "secrets", Secrets())
    if malformed:
        with pytest.raises(ValueError, match="Não foi possível ler") as error:
            backend_config.database_url()
        assert error.value.__suppress_context__
        assert "fake-secret" not in str(error.value)
    else:
        assert backend_config.database_url() is None
