"""Structural performance contracts. No wall-clock assertions."""

from datetime import date, datetime
from inspect import getsource

from services.alerts import collect_alerts, get_alert_summary
from services.pending import collect_pending, list_pending
from tests.access_testing import TEST_IDENTITY
from tests.test_pending import TODAY, _admin, _prepare, _received


def test_alert_summary_does_not_load_far_future_agenda(store):
    oficios, agenda, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    _received(oficios, proge, "2026-09-13")
    agenda.save(
        dict(
            tipo="EVENTO",
            procuradores=[1],
            inicio="2026-11-20T09:00:00",
            fim="2026-11-20T10:00:00",
            situacao="Agendado",
            titulo="Longe",
            categoria="Curso",
            local="TCE-PB",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    now = datetime(2026, 9, 14, 9, 0, 0)
    items, _, _ = collect_alerts(store, _admin(store), now=now)
    assert all("Longe" not in (item.description or "") for item in items)
    pending, _, _ = collect_pending(store, _admin(store), today=TODAY)
    assert any(i.title == "Longe" for i in pending)


def test_collect_pending_reuses_one_read_connection(store):
    _prepare(store)
    sql = []
    original = store.connection

    def wrapped(*, read_only=False, **kwargs):
        sql.append(read_only)
        return original(read_only=read_only, **kwargs)

    store.connection = wrapped
    collect_pending(store, _admin(store), today=TODAY)
    assert False not in sql
    assert sql.count(True) <= 2


def test_representacoes_people_context_uses_two_reads(store, monkeypatch):
    from contextlib import contextmanager

    from services.representacoes import people_context

    calls = 0
    original = store.connection

    @contextmanager
    def counted(**kwargs):
        nonlocal calls
        calls += 1
        with original(**kwargs) as connection:
            yield connection

    monkeypatch.setattr(store, "connection", counted)
    active_people, active_servers, all_people, all_servers = people_context(store)
    assert calls == 2
    assert active_people
    assert set(active_people) <= set(all_people)
    assert set(active_servers) <= set(all_servers)


def test_audit_overview_reuses_users_and_skips_duplicate_dashboard(store, monkeypatch):
    from database.access import AccessStore
    from database.audit import AuditStore
    from services.audit import overview, user_overview

    principal = _admin(store)
    monkeypatch.setattr(
        AuditStore,
        "dashboard",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("dashboard duplicado")
        ),
    )
    data = overview(store, principal, include_dashboard=False)
    assert data["painel"] is None

    monkeypatch.setattr(
        AccessStore,
        "list_users",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("usuários recarregados")
        ),
    )
    rows = user_overview(store, principal, users=data["usuarios"])
    assert isinstance(rows, list)


def test_second_pending_collect_skips_schema_lookup(store):
    _prepare(store)
    collect_pending(store, _admin(store), today=TODAY)
    queries = []
    original = store.connection

    class Probe:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, query, params=()):
            queries.append(" ".join(str(query).split()))
            return self._inner.execute(query, params)

        def __getattr__(self, name):
            return getattr(self._inner, name)

    from contextlib import contextmanager

    @contextmanager
    def wrapped(*, read_only=False, **kwargs):
        with original(read_only=read_only, **kwargs) as inner:
            yield Probe(inner)

    store.connection = wrapped
    collect_pending(store, _admin(store), today=TODAY)
    assert not any("sqlite_master" in q or "information_schema.tables" in q for q in queries)
    assert not any(
        "FROM agenda_compromisso_procuradores" in q and "WHERE" not in q and "JOIN" not in q
        for q in queries
    )


def test_list_pending_pages_without_changing_authorization(store):
    oficios, _, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    for day in range(13, 20):
        _received(oficios, proge, f"2026-09-{day}")
    page, total, errors, _ = list_pending(
        store, _admin(store), today=TODAY, modules=("oficios",), limit=2, offset=0
    )
    assert not any(errors.values())
    assert len(page) == 2
    assert total >= 2


def test_memorandos_overview_counts_without_payload(store):
    from database.memorandos import MemorandosStore

    service = MemorandosStore(store)
    source = getsource(service.situacao_counts)
    assert "payload" not in source
    assert "LIMIT" not in source
    counts = service.situacao_counts(date(2026, 9, 14))
    assert set(counts) == {"AGENDADA", "EM ANDAMENTO", "ENCERRADA"}


def test_get_alert_summary_matches_collect_alerts_top(store):
    _prepare(store)
    now = datetime(2026, 9, 14, 9, 0, 0)
    summary = get_alert_summary(store, _admin(store), now=now)
    items, _, _ = collect_alerts(store, _admin(store), now=now)
    assert summary["total"] == len(items)
    assert [i.source_id for i in summary["top"]] == [i.source_id for i in items[:5]]


def test_pending_sql_has_no_blob_columns():
    from services import pending

    source = (
        getsource(pending.fetch_oficios)
        + getsource(pending.fetch_agenda)
        + getsource(pending.fetch_memorandos)
        + getsource(pending.fetch_afastamentos)
        + getsource(pending.fetch_tarefas)
        + getsource(pending.fetch_representacoes)
        + getsource(pending.fetch_ouvidoria)
        + getsource(pending.fetch_access_requests)
    )
    assert "conteudo" not in source
    assert "docx" not in source.lower()
    assert "alert_window" in getsource(pending.collect_pending)
