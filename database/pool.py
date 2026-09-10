"""Process-local, bounded PostgreSQL pools. No application data is cached here."""

import atexit
import hashlib
import uuid
from threading import RLock

import psycopg
import streamlit as st
from psycopg_pool import ConnectionPool


class SafeConnection(psycopg.Connection):
    @classmethod
    def connect(cls, *args, **kwargs):
        try:
            return super().connect(*args, **kwargs)
        except psycopg.Error:
            # Pool workers log connection exceptions: never pass through a DSN.
            raise psycopg.OperationalError("Conexão PostgreSQL indisponível") from None

    def __repr__(self):
        return "<MPC PostgreSQL connection>"


def check_connection(connection):
    try:
        ConnectionPool.check_connection(connection)
    except psycopg.Error:
        raise psycopg.OperationalError("Conexão PostgreSQL interrompida") from None


class Resource:
    def __init__(self, options):
        self.lock = RLock()
        self.initialized = set()
        self.cache_id = uuid.uuid4().hex
        self.revisions = {}
        self.revision_lock = RLock()
        self.pool = ConnectionPool(
            kwargs=dict(options, prepare_threshold=None),
            connection_class=SafeConnection,
            min_size=0,
            max_size=4,
            timeout=15,
            max_waiting=32,
            max_idle=60,
            max_lifetime=600,
            reconnect_timeout=15,
            check=check_connection,
            name="mpc-postgres",
            open=True,
        )

    def cache_key(self, schema, tables):
        with self.revision_lock:
            return (
                self.cache_id,
                schema,
                tuple(self.revisions.get((schema, table), 0) for table in tables),
            )

    def invalidate(self, schema, tables):
        with self.revision_lock:
            for table in tables:
                key = (schema, table)
                self.revisions[key] = self.revisions.get(key, 0) + 1


_lock = RLock()
_resources = {}


@st.cache_resource(show_spinner=False)
def _cached_resource(key, _options):
    return Resource(_options)


def resource(options):
    key = tuple(sorted(options.items()))
    with _lock:
        if key not in _resources:
            # Bound resources even when credentials change during a process lifetime.
            if len(_resources) >= 4:
                _resources.pop(next(iter(_resources))).pool.close()
                _cached_resource.clear()
            digest = hashlib.sha256(repr(key).encode()).hexdigest()
            _resources[key] = _cached_resource(digest, options)
        return _resources[key]


def close_pools():
    with _lock:
        for item in _resources.values():
            item.pool.close()
        _resources.clear()
        _cached_resource.clear()


atexit.register(close_pools)
