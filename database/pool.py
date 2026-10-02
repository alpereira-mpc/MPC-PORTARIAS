"""Process-local, bounded PostgreSQL pools. No application data is cached here."""

import atexit
import hashlib
import time
import uuid
from threading import RLock

import psycopg
import streamlit as st
from psycopg_pool import ConnectionPool


HEALTH_CHECK_AFTER_IDLE_SECONDS = 30


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
    """Ping a new or genuinely idle connection, not every warm checkout.

    psycopg_pool invokes this callback on every ``getconn()``.  A hot checkout
    was returned cleanly moments earlier, so a second network round trip adds
    latency without improving its safety.  A newly-created or idle connection
    is still checked before being handed to the caller.
    """
    idle_since = getattr(connection, "_mpc_idle_since", None)
    if (
        idle_since is not None
        and time.monotonic() - idle_since < HEALTH_CHECK_AFTER_IDLE_SECONDS
    ):
        return
    try:
        ConnectionPool.check_connection(connection)
    except psycopg.Error:
        raise psycopg.OperationalError("Conexão PostgreSQL interrompida") from None


def mark_connection_idle(connection):
    """Remember a clean return so the next warm checkout avoids a ping.

    If a future psycopg connection implementation disallows instance
    attributes, falling back to a check on every checkout remains safe.
    """
    try:
        connection._mpc_idle_since = time.monotonic()
    except (AttributeError, TypeError):
        pass


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
