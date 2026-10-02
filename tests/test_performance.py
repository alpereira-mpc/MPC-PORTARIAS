import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def performance(monkeypatch):
    from services import performance

    performance.enabled.cache_clear()
    monkeypatch.setattr(performance, "_telemetry_confirmation_emitted", False)
    yield performance
    performance.enabled.cache_clear()


def test_performance_disabled_emits_no_line(monkeypatch, capsys, performance):
    monkeypatch.setenv("MPC_PERF_LOG", "0")
    performance.enabled.cache_clear()

    @performance.profile_rerun
    def measured():
        pass

    measured()
    assert not performance.enabled()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("value", ("1", "true", "yes"))
def test_environment_opt_in_values_are_enabled(monkeypatch, performance, value):
    monkeypatch.setenv("MPC_PERF_LOG", value)
    performance.enabled.cache_clear()
    assert performance.enabled()


def test_root_secret_boolean_true_is_enabled(monkeypatch, performance):
    monkeypatch.delenv("MPC_PERF_LOG", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "streamlit",
        SimpleNamespace(secrets={"MPC_PERF_LOG": True}),
    )
    performance.enabled.cache_clear()
    assert performance.enabled()


def test_rerun_prints_one_safe_consolidated_line_with_flush(
    monkeypatch, capsys, performance
):
    monkeypatch.setenv("MPC_PERF_LOG", "1")
    performance.enabled.cache_clear()

    @performance.profile_rerun
    def measured():
        with performance.phase("module_render"):
            with performance.database_operation("AgendaStore.list"):
                performance.record_database_call("postgres.execute", 12.5)
            performance.record_pool_wait(1.25)

    measured()

    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "PERF | telemetry=enabled"
    assert len(lines) == 2
    message = lines[1]
    assert message.startswith("PERF |")
    assert "db_queries=1" in message
    assert "round_trips=1" in message
    assert "module_render_ms=" in message
    assert "AgendaStore.list:1/12.5ms" in message
    assert "SELECT" not in message
    assert "DATABASE_URL" not in message


def test_print_emission_requests_flush(monkeypatch, performance):
    calls = []
    monkeypatch.setenv("MPC_PERF_LOG", "1")
    performance.enabled.cache_clear()
    monkeypatch.setattr(
        "builtins.print", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    @performance.profile_rerun
    def measured():
        pass

    measured()
    assert calls[0][0] == ("PERF | telemetry=enabled",)
    assert calls[1][0][0].startswith("PERF | module=")
    assert all(kwargs == {"flush": True} for _, kwargs in calls)


def test_enabled_confirmation_is_emitted_once_per_process(
    monkeypatch, capsys, performance
):
    monkeypatch.setenv("MPC_PERF_LOG", "1")
    performance.enabled.cache_clear()

    @performance.profile_rerun
    def measured():
        pass

    measured()
    measured()
    lines = capsys.readouterr().out.splitlines()
    assert lines.count("PERF | telemetry=enabled") == 1
    assert len([line for line in lines if line.startswith("PERF | module=")]) == 2


def test_rerun_line_is_emitted_when_streamlit_stops_execution(
    monkeypatch, capsys, performance
):
    class StreamlitStop(BaseException):
        pass

    monkeypatch.setenv("MPC_PERF_LOG", "1")
    performance.enabled.cache_clear()

    @performance.profile_rerun
    def measured():
        raise StreamlitStop()

    with pytest.raises(StreamlitStop):
        measured()
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1].startswith("PERF | module=")


def test_agenda_operations_keep_behavior_without_active_telemetry(
    store, monkeypatch, performance
):
    from database.agenda import AgendaStore

    monkeypatch.setenv("MPC_PERF_LOG", "0")
    performance.enabled.cache_clear()
    agenda = AgendaStore(store)
    assert agenda.list("2026-01-01", "2026-01-02") == []
