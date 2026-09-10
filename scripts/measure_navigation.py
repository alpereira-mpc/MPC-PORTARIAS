"""Measure navigation exclusively in a guarded, disposable loopback PostgreSQL.

Set MPC_TEST_POSTGRES_URL and pass an output JSON path. Creates a fresh test
schema with seed only. No Portaria is finalized and production URLs are rejected.
Timings use AppTest (three reruns per screen), not a deployed Cloud browser.
SQL counts include setup and health checks but exclude driver BEGIN/COMMIT.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json, time, uuid, statistics, os
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool
from unittest.mock import patch
import psycopg
import streamlit as st
from streamlit.testing.v1 import AppTest
from database.store import Store, ROOT
from database.postgresql import PostgresBackend

URL = os.environ["MPC_TEST_POSTGRES_URL"]
options = conninfo_to_dict(URL)
if (
    options.get("host") not in ("127.0.0.1", "localhost", "::1")
    or options.get("dbname") != "mpc_disposable_tests"
    or "hostaddr" in options
    or "service" in options
):
    raise ValueError("Use somente PostgreSQL descartável em loopback.")
with psycopg.connect(URL) as c:
    if (
        c.execute("SELECT tag FROM public.mpc_test_guard").fetchone()[0]
        != "disposable-mpc-tests-v1"
    ):
        raise ValueError("Marcador de banco descartável inválido.")
schema = "mpc_test_" + uuid.uuid4().hex
metrics = {}
original_connect = psycopg.Connection.connect.__func__
original_execute = psycopg.Connection.execute
original_initialize = PostgresBackend.initialize
original_getconn = ConnectionPool.getconn


def getconn(self, *args, **kwargs):
    start = time.perf_counter()
    try:
        return original_getconn(self, *args, **kwargs)
    finally:
        metrics["acquire_ms"] = (
            metrics.get("acquire_ms", 0) + (time.perf_counter() - start) * 1000
        )


def connect(cls, *a, **kw):
    start = time.perf_counter()
    result = original_connect(cls, *a, **kw)
    metrics["connections"] = metrics.get("connections", 0) + 1
    metrics["connect_ms"] = (
        metrics.get("connect_ms", 0) + (time.perf_counter() - start) * 1000
    )
    return result


def execute(self, query, *a, **kw):
    metrics["execute_calls"] = metrics.get("execute_calls", 0) + 1
    text = query.as_string(self) if hasattr(query, "as_string") else query
    if isinstance(text, bytes):
        text = text.decode()
    metrics["commands"] = metrics.get("commands", 0) + max(
        1, len([x for x in text.split(";") if x.strip()])
    )
    return original_execute(self, query, *a, **kw)


def initialize(self, root):
    start = time.perf_counter()
    try:
        return original_initialize(self, root)
    finally:
        metrics["init_ms"] = (
            metrics.get("init_ms", 0) + (time.perf_counter() - start) * 1000
        )


def factory():
    store = Store(database_url=URL, postgres_schema=schema)
    store.path = ROOT / "tmp/performance/runtime"
    store.path.parent.mkdir(exist_ok=True, parents=True)
    return store


result = {}
with (
    patch.object(ConnectionPool, "getconn", getconn),
    patch.object(psycopg.Connection, "connect", classmethod(connect)),
    patch.object(
        psycopg, "connect", lambda *a, **kw: connect(psycopg.Connection, *a, **kw)
    ),
    patch.object(psycopg.Connection, "execute", execute),
    patch.object(PostgresBackend, "initialize", initialize),
    patch("database.store.Store", factory),
    patch.object(st, "secrets", {}),
):
    start = time.perf_counter()
    factory()
    result["bootstrap"] = dict(metrics, total_ms=(time.perf_counter() - start) * 1000)
    metrics.clear()
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60)
    start = time.perf_counter()
    app.run()
    app.button(key="open_portarias").click().run()
    assert not app.exception
    result["first_app"] = dict(metrics, total_ms=(time.perf_counter() - start) * 1000)
    for screen in ["Nova Portaria", "Histórico", "Procuradores", "Configurações"]:
        runs = []
        for _ in range(3):
            metrics.clear()
            start = time.perf_counter()
            app.radio(key="nav").set_value(screen).run()
            assert not app.exception
            runs.append(dict(metrics, total_ms=(time.perf_counter() - start) * 1000))
        result[screen] = {
            k: round(statistics.median(r.get(k, 0) for r in runs), 2)
            for k in set().union(*runs)
        }
Path(sys.argv[1]).write_text(
    json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(json.dumps(result, indent=2, ensure_ascii=False))
