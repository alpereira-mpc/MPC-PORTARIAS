"""Opt-in, process-local performance measurements for deployed Streamlit runs.

Metrics are emitted only to the application log when ``MPC_PERF_LOG`` is true.
They never include SQL text, query parameters, document bytes, identities, or
secrets, and are never persisted.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache, wraps
import logging
import os
import time


LOGGER = logging.getLogger("mpc.performance")
_METRICS = ContextVar("mpc_performance_metrics", default=None)
_DATABASE_OPERATION = ContextVar("mpc_performance_database_operation", default=None)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


@lru_cache(maxsize=1)
def enabled():
    """Read the opt-in flag once per process without exposing its value."""
    raw = os.environ.get("MPC_PERF_LOG")
    if raw is None:
        try:
            import streamlit as st

            raw = st.secrets.get("MPC_PERF_LOG")
        except Exception:
            raw = None
    return str(raw or "").strip().casefold() in _TRUE_VALUES


@dataclass
class Metrics:
    started: float = field(default_factory=time.perf_counter)
    phases: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    queries: int = 0
    round_trips: int = 0
    database_ms: float = 0.0
    database_max_ms: float = 0.0
    pool_wait_ms: float = 0.0
    operations: dict[str, list[float]] = field(
        default_factory=lambda: defaultdict(lambda: [0, 0.0])
    )


def active():
    """True only while an opted-in portal or fragment rerun is being measured."""
    return _METRICS.get() is not None


@contextmanager
def phase(name):
    """Accumulate an application-layer phase in the current rerun."""
    metrics = _METRICS.get()
    if metrics is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        metrics.phases[name] += (time.perf_counter() - started) * 1000


@contextmanager
def database_operation(name):
    """Associate PostgreSQL calls with a safe, static repository operation name."""
    if not active():
        yield
        return
    token = _DATABASE_OPERATION.set(name)
    try:
        yield
    finally:
        _DATABASE_OPERATION.reset(token)


def record_database_call(name, elapsed_ms, *, query=True):
    """Record application-observed PostgreSQL time; never retain statement text."""
    metrics = _METRICS.get()
    if metrics is None:
        return
    elapsed = max(0.0, float(elapsed_ms))
    operation = _DATABASE_OPERATION.get() or name
    metrics.round_trips += 1
    metrics.database_ms += elapsed
    metrics.database_max_ms = max(metrics.database_max_ms, elapsed)
    if query:
        metrics.queries += 1
    metrics.operations[operation][0] += 1
    metrics.operations[operation][1] += elapsed


def record_pool_wait(elapsed_ms):
    metrics = _METRICS.get()
    if metrics is not None:
        metrics.pool_wait_ms += max(0.0, float(elapsed_ms))


def _module_name():
    try:
        import streamlit as st

        if st.session_state.get("portal_special_view") == "alerts":
            return "Alertas"
        return st.session_state.get("portal_module") or "Início"
    except Exception:
        return "desconhecido"


def _emit(metrics):
    total_ms = (time.perf_counter() - metrics.started) * 1000
    fields = [
        "PERF",
        f"module={_module_name()}",
        f"total_ms={total_ms:.1f}",
        f"db_queries={metrics.queries}",
        f"db_ms={metrics.database_ms:.1f}",
        f"db_max_ms={metrics.database_max_ms:.1f}",
        f"round_trips={metrics.round_trips}",
        f"pool_wait_ms={metrics.pool_wait_ms:.1f}",
    ]
    fields.extend(
        f"{name}_ms={elapsed:.1f}" for name, elapsed in sorted(metrics.phases.items())
    )
    operations = sorted(metrics.operations.items(), key=lambda item: -item[1][1])[:4]
    if operations:
        fields.append(
            "db_ops="
            + ",".join(
                f"{name}:{int(values[0])}/{values[1]:.1f}ms"
                for name, values in operations
            )
        )
    # warning is intentional: Streamlit Cloud reliably exposes it, and this is
    # emitted only when the explicit performance flag is enabled.
    LOGGER.warning(" | ".join(fields))


def profile_rerun(function):
    """Log one compact aggregate for a complete portal or fragment rerun."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        if not enabled() or active():
            return function(*args, **kwargs)
        metrics = Metrics()
        token = _METRICS.set(metrics)
        try:
            return function(*args, **kwargs)
        finally:
            _METRICS.reset(token)
            _emit(metrics)

    return wrapped
