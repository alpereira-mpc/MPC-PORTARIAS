import logging

import pytest


@pytest.fixture
def performance(monkeypatch):
    from services import performance

    performance.enabled.cache_clear()
    yield performance
    performance.enabled.cache_clear()


def test_performance_is_opt_in(monkeypatch, performance):
    monkeypatch.setenv("MPC_PERF_LOG", "0")
    performance.enabled.cache_clear()
    assert not performance.enabled()


def test_rerun_log_aggregates_only_safe_timing_fields(monkeypatch, caplog, performance):
    monkeypatch.setenv("MPC_PERF_LOG", "1")
    performance.enabled.cache_clear()

    @performance.profile_rerun
    def measured():
        with performance.phase("module_render"):
            with performance.database_operation("AgendaStore.list"):
                performance.record_database_call("postgres.execute", 12.5)
            performance.record_pool_wait(1.25)

    with caplog.at_level(logging.WARNING, logger="mpc.performance"):
        measured()

    message = next(
        record.message for record in caplog.records if record.message.startswith("PERF")
    )
    assert "db_queries=1" in message
    assert "round_trips=1" in message
    assert "module_render_ms=" in message
    assert "AgendaStore.list:1/12.5ms" in message
    assert "SELECT" not in message
    assert "DATABASE_URL" not in message


def test_agenda_operations_keep_behavior_without_active_telemetry(
    store, monkeypatch, performance
):
    from database.agenda import AgendaStore

    monkeypatch.setenv("MPC_PERF_LOG", "0")
    performance.enabled.cache_clear()
    agenda = AgendaStore(store)
    assert agenda.list("2026-01-01", "2026-01-02") == []
