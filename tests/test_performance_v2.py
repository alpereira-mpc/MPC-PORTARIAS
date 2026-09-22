"""Behavior and query-count regressions; no timing-dependent assertions."""

from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from database.agenda import AgendaStore
from database.tramita_reports import TramitaReportsStore
from tests.test_agenda import draft
from tests.test_postgresql import pg_store, pg_url
from tests.test_tarefas import _principal


@pytest.fixture(params=["store", "pg_store"])
def reports_store(request):
    return request.getfixturevalue(request.param)


def movement(**changes):
    return {
        "protocolo": "00001/26", "tipo": "Processo", "subcategoria": "Denúncia",
        "origem": "Origem", "data_realizacao": "2026-08-03 09:00",
        "procurador": "Pessoa A", "motivo_distribuicao": "Ao Procurador",
        "data_devolucao": "2026-08-05 21:00", "motivo_devolucao": " Análisado  COM parecer ",
        **changes,
    }


def test_production_aggregation_and_pagination(reports_store):
    repo = TramitaReportsStore(reports_store)
    rows = [movement(protocolo=f"{i:05}/26") for i in range(105)]
    repo.import_rows(kind="ENTRADAS", file_name="a.xls", file_hash="a", actor="test", competence="2026-08", rows=rows)
    repo.import_rows(kind="SAIDAS", file_name="b.xls", file_hash="b", actor="test", competence="2026-08", rows=[movement(), movement(procurador="Pessoa B", motivo_devolucao="Analisado Com Cota", data_devolucao=None)])
    summary = repo.production_summary("2026-08")
    assert summary["Pessoa A"] == dict(entries=105, exits=1, opinions=1, quotas=0, days=2.5, timed=1)
    assert summary["Pessoa B"]["quotas"] == 1
    assert summary["Pessoa B"]["timed"] == 0
    filters = {"procurador": "Pessoa A", "tipo_movimentacao": "ENTRADA"}
    first = repo.movement_page("2026-08", filters)
    second = repo.movement_page("2026-08", filters, 100)
    assert len(first) == 101 and len(second) == 5
    assert not {r["protocolo"] for r in first[:100]} & {r["protocolo"] for r in second}
    assert repo.movement_page("2026-09", filters) == []
    with pytest.raises(ValueError, match="Filtro"):
        repo.movement_page("2026-08", {"unknown": "x"})


def test_stock_aggregation_matches_aging_and_cascading_filters(reports_store):
    repo = TramitaReportsStore(reports_store)
    values = [None, 0, 7, 8, 15, 16, 30, 31, 60, 61, 90, 91]
    rows = [dict(protocolo=f"{i:05}/26", tipo="Processo", digital="Sim", subcategoria="Denúncia",
                 jurisdicionado="Origem", fase="Fase A", procurador="Pessoa A", dias_com_procurador=value,
                 assistente="Assistente", dias_com_assistente=None, dias_no_mpc=100, prescricao="")
            for i, value in enumerate(values)]
    rows.append({**rows[0], "protocolo": "99999/26", "procurador": "Pessoa B", "subcategoria": "Recurso", "fase": "Fase B"})
    repo.import_rows(kind="ESTOQUE", file_name="e.xls", file_hash="e", actor="test", snapshot_date="2026-08-31", rows=rows)
    summary = {r["procurador"]: r for r in repo.stock_summary("2026-08-31")}
    assert summary["Pessoa A"]["n"] == 12
    assert summary["Pessoa A"]["over30"] == 5
    assert summary["Pessoa A"]["timed"] == 11
    assert summary["Pessoa A"]["days"] == sum(v for v in values if v is not None)
    options = repo.stock_options("2026-08-31", {"procurador": "Pessoa B"})
    assert {r["value"] for r in options if r["field"] == "fase"} == {"Fase B"}
    bands, details = repo.stock_details("2026-08-31", {"procurador": "Pessoa A"}, "31–60 dias")
    assert bands == [{"faixa": "31–60 dias", "n": 2}]
    assert [r["dias_com_procurador"] for r in details] == [60, 31]


def test_report_schema_ready_only_after_successful_commit(store, monkeypatch):
    store.__dict__.pop("_tramita_schema_ready", None)
    original = store.connection

    @contextmanager
    def failed(**kwargs):
        with original(**kwargs) as connection:
            yield connection
            raise RuntimeError("rollback")

    monkeypatch.setattr(store, "connection", failed)
    with pytest.raises(RuntimeError, match="rollback"):
        TramitaReportsStore(store)
    assert not getattr(store, "_tramita_schema_ready", False)
    monkeypatch.setattr(store, "connection", original)
    TramitaReportsStore(store)
    monkeypatch.setattr(store, "connection", lambda **kwargs: pytest.fail("DDL repetido"))
    TramitaReportsStore(store)


def test_history_participants_are_batched_and_page_limited(store, monkeypatch):
    repo = AgendaStore(store)
    for index in range(35):
        record = draft(members=[1, 2], day=(date(2026, 8, 1) + timedelta(days=index)).isoformat())
        record["situacao"] = "Realizado"
        repo.save(record, institutional_confirmed=True, conflict_confirmed=True)
    original = store.connection
    statements = []

    @contextmanager
    def counting(**kwargs):
        with original(**kwargs) as connection:
            connection.set_trace_callback(statements.append)
            yield connection

    monkeypatch.setattr(store, "connection", counting)
    rows = repo.history(limit=30)
    assert len(rows) == 31
    assert all(row["procuradores"] == [1, 2] for row in rows)
    assert len([sql for sql in statements if sql.startswith("SELECT")]) == 2


def test_trip_collector_not_called_without_agenda_permission(store, monkeypatch):
    from services import alerts

    principal = replace(_principal(1), pode_oficios=True, pode_agenda=False)
    monkeypatch.setattr(alerts, "trip_alerts", lambda *args: pytest.fail("Coleta não autorizada"))
    items, _, _ = alerts.collect_alerts(store, principal, modules=("agenda",))
    assert items == []


def test_upcoming_leaves_have_bounded_pages_without_losing_records(store):
    repo = AgendaStore(store)
    today = date.today()
    identifiers = []
    for index in range(35):
        day = (today + timedelta(days=index + 1)).isoformat()
        identifiers.append(repo.save_leave({"procurador_id": 1, "motivo": "Férias", "data_inicio": day, "data_fim": day}))
    first = repo.active_leaves(today.isoformat(), None, upcoming=True, limit=30)
    second = repo.active_leaves(today.isoformat(), None, upcoming=True, limit=30, offset=30)
    assert len(first) == 31 and len(second) == 5
    assert [row["id"] for row in first[:30] + second] == identifiers


def test_report_cache_is_session_scoped_and_expires(store, monkeypatch):
    from services import relatorios_ui as ui
    import time

    state, calls = {}, []
    monkeypatch.setattr(ui.st, "session_state", state)
    clock = [100.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    repo = SimpleNamespace(store=store, summary=lambda: calls.append(1) or {"n": 1})
    first, second = _principal(1), _principal(2)
    ui._cached_report(repo.store, first, ("summary",), repo.summary)
    ui._cached_report(repo.store, first, ("summary",), repo.summary)
    assert len(calls) == 1
    clock[0] += 31
    ui._cached_report(repo.store, first, ("summary",), repo.summary)
    ui._cached_report(repo.store, second, ("summary",), repo.summary)
    assert len(calls) == 3


def test_report_renderer_logs_failure_and_shows_friendly_message(monkeypatch, caplog):
    from services import relatorios_ui as ui

    errors = []
    monkeypatch.setattr(ui.st, "error", errors.append)

    def unavailable(*_):
        raise RuntimeError("consulta indisponível")

    ui._render_indicators("produção mensal", unavailable, object(), object())

    assert errors == ["Não foi possível carregar os indicadores neste momento. Tente novamente mais tarde."]
    assert "Falha ao carregar indicadores de produção mensal" in caplog.text


def test_upload_preview_parses_once_per_file_and_account(monkeypatch):
    from services import relatorios_ui as ui

    calls = []
    monkeypatch.setattr(ui.st, "session_state", {})
    monkeypatch.setattr(ui, "parse_stock", lambda content: calls.append(content) or ([], []))
    uploaded = SimpleNamespace(getvalue=lambda: b"xls-one")
    ui._uploaded_preview("ESTOQUE", uploaded, _principal(1))
    ui._uploaded_preview("ESTOQUE", uploaded, _principal(1))
    assert calls == [b"xls-one"]
    ui._uploaded_preview("ESTOQUE", uploaded, _principal(2))
    assert len(calls) == 2


def test_bell_cache_tracks_account_permissions_and_revision(store, monkeypatch):
    from services import alerts_ui as ui
    from services.alerts import invalidate_alert_summary

    state, calls = {}, []
    monkeypatch.setattr(ui.st, "session_state", state)
    monkeypatch.setattr(ui, "get_alert_summary", lambda *a, **k: calls.append(1) or {"total": 0})
    user = _principal(1)
    ui.load_bell_summary(store, user)
    ui.load_bell_summary(store, user)
    assert len(calls) == 1
    ui.load_bell_summary(store, replace(user, pode_agenda=True))
    assert len(calls) == 2
    invalidate_alert_summary(state)
    ui.load_bell_summary(store, replace(user, pode_agenda=True))
    ui.load_bell_summary(store, _principal(2))
    assert len(calls) == 4


def test_alert_order_accepts_mixed_modules_with_same_severity():
    from services.alerts import AlertItem, ALTO, _sort_key

    common = dict(gabinete="—", severity=ALTO, description="Teste", date=date(2026, 9, 17),
                  datetime=None, source_status="Agendado", navigation_target="Agenda")
    items = [
        AlertItem(source_module="agenda", source_id="a", category="agenda_hoje", title="Compromisso", **common),
        AlertItem(source_module="agenda", source_id="v", category="viagem_ida", title="Viagem", metadata={"trip_rank": 0}, **common),
        AlertItem(source_module="tarefas", source_id="t", category="tarefa_atrasada", title="Tarefa", metadata={"task_alert_rank": 0, "task_priority_rank": 1}, **common),
    ]
    ordered = sorted(items, key=_sort_key)
    assert {item.source_id for item in ordered} == {"a", "v", "t"}
    assert ordered[0].source_id == "t"


def test_task_sections_do_not_query_hidden_lists(monkeypatch):
    from services import tarefas_ui as ui
    from streamlit.testing.v1 import AppTest

    calls = []
    repo = SimpleNamespace(
        situation_counts=lambda owner: dict(atrasadas=0, hoje=0, proximas=0, andamento=0, aguardando=0),
        list_active=lambda *a, **k: calls.append("active") or [],
        list_history=lambda *a, **k: calls.append("history") or [],
    )
    monkeypatch.setattr(ui, "TarefasStore", lambda store: repo)
    app = AppTest.from_string(
        "from services.tarefas_ui import render\n"
        "from tests.test_tarefas import _principal\n"
        "render(None, _principal(1))\n"
    ).run()
    assert not app.exception and calls == ["active"]
    calls.clear()
    app.radio(key="tarefas_section").set_value("Histórico").run()
    assert not app.exception and calls == ["history"]


def test_postgres_batch_commits_revisions_and_rolls_back(pg_store):
    from database.postgresql import DatabaseUnavailable

    before = pg_store.read_cache_key(("configuracoes",))
    with pg_store.connection() as connection:
        connection.executemany(
            "INSERT INTO configuracoes(chave,valor) VALUES(?,?)",
            [("perf_v2_a", "1"), ("perf_v2_b", "2")],
        )
        assert pg_store.read_cache_key(("configuracoes",)) == before
    committed = pg_store.read_cache_key(("configuracoes",))
    assert committed != before
    with pytest.raises(DatabaseUnavailable):
        with pg_store.connection() as connection:
            connection.executemany(
                "INSERT INTO configuracoes(chave,valor) VALUES(?,?)",
                [("perf_v2_rollback", "1"), ("perf_v2_a", "duplicate")],
            )
    assert pg_store.read_cache_key(("configuracoes",)) == committed
    with pg_store.connection(read_only=True) as connection:
        assert not connection.execute("SELECT 1 FROM configuracoes WHERE chave=?", ("perf_v2_rollback",)).fetchone()
