from datetime import date, datetime, timedelta, timezone
from inspect import getsource

import pytest

from services.access import has_permission, resolve_principal
from services.alerts import (
    ALTO,
    ATENCAO,
    CRITICO,
    INFORMATIVO,
    alert_from_pending,
    can_view_alertas,
    collect_alerts,
    get_alert_summary,
    system_alerts,
)
from services.audit import INSTITUTIONAL_TZ
from services.pending import PendingItem, now_recife, parse_datetime
from services.system_health import ATTENTION, ERROR, OK
from tests.access_testing import TEST_IDENTITY, enable_login, seed_access
from tests.test_pending import (
    TODAY,
    _admin,
    _prepare,
    _received,
    _user,
)
from tests.test_memorandos import record as memo_record
from tests.test_postgresql import pg_store, pg_url  # noqa: F401

NOW = datetime(2026, 9, 14, 12, 0, tzinfo=INSTITUTIONAL_TZ)
HEALTHY = {
    "database": {"status": OK, "summary": "OK — SQLite"},
    "schema": {"status": OK, "summary": "OK — Schema íntegro", "missing_tables": []},
    "documents": {"pdf": {"status": OK, "summary": "OK — conversor PDF disponível"}},
    "audit": {"status": OK, "summary": "OK", "errors_24h": 0},
}


def _item(**kwargs):
    values = dict(
        source_module="oficios",
        source_id="1",
        gabinete="PROGE",
        title="Ofício",
        subtitle="",
        due_date=TODAY,
        start_date=TODAY,
        end_date=TODAY,
        status_original="Recebido",
        urgency="HOJE",
        context="",
        navigation="Ofícios",
        metadata={},
    )
    values.update(kwargs)
    return PendingItem(**values)


def test_oficio_severity_rules():
    assert alert_from_pending(_item(urgency="VENCIDA", due_date=TODAY - timedelta(days=1)), NOW).severity == CRITICO
    assert alert_from_pending(_item(urgency="HOJE"), NOW).title == "Prazo vence hoje"
    assert alert_from_pending(_item(urgency="URGENTE", due_date=TODAY + timedelta(days=1)), NOW).severity == ATENCAO
    assert alert_from_pending(_item(urgency="URGENTE", due_date=TODAY + timedelta(days=3)), NOW).severity == ATENCAO
    assert alert_from_pending(_item(urgency="PRÓXIMA", due_date=TODAY + timedelta(days=4)), NOW) is None
    assert alert_from_pending(_item(urgency="SEM PRAZO", due_date=None), NOW).severity == INFORMATIVO


def test_agenda_severity_and_no_duplicates():
    today_late = _item(
        source_module="agenda",
        title="Reunião tarde",
        urgency="HOJE",
        navigation="Agenda",
        metadata={"inicio": "2026-09-14T15:00:00"},
    )
    soon = _item(
        source_module="agenda",
        title="Reunião breve",
        urgency="HOJE",
        navigation="Agenda",
        metadata={"inicio": "2026-09-14T13:30:00"},
    )
    next_day = _item(
        source_module="agenda",
        title="Amanhã",
        urgency="URGENTE",
        due_date=TODAY + timedelta(days=1),
        start_date=TODAY + timedelta(days=1),
        navigation="Agenda",
        metadata={"inicio": "2026-09-15T08:00:00"},
    )
    distant = _item(
        source_module="agenda",
        title="Longe",
        urgency="URGENTE",
        due_date=TODAY + timedelta(days=3),
        start_date=TODAY + timedelta(days=3),
        navigation="Agenda",
        metadata={"inicio": "2026-09-17T10:00:00"},
    )
    past = _item(
        source_module="agenda",
        title="Passado",
        urgency="HOJE",
        navigation="Agenda",
        metadata={"inicio": "2026-09-14T08:00:00"},
    )
    assert alert_from_pending(today_late, NOW).severity == ALTO
    assert alert_from_pending(today_late, NOW).title == "Compromisso hoje"
    assert alert_from_pending(soon, NOW).severity == CRITICO
    assert alert_from_pending(soon, NOW).title == "Compromisso em breve"
    assert alert_from_pending(next_day, NOW).severity == ATENCAO
    assert alert_from_pending(distant, NOW) is None
    assert alert_from_pending(past, NOW) is None


def test_memorando_severity_rules():
    ongoing = _item(
        source_module="memorandos",
        status_original="EM ANDAMENTO",
        urgency="HOJE",
        navigation="Memorandos",
    )
    starts = _item(
        source_module="memorandos",
        status_original="AGENDADA",
        urgency="HOJE",
        navigation="Memorandos",
    )
    soon = _item(
        source_module="memorandos",
        status_original="AGENDADA",
        urgency="URGENTE",
        due_date=TODAY + timedelta(days=3),
        navigation="Memorandos",
    )
    later = _item(
        source_module="memorandos",
        status_original="AGENDADA",
        urgency="PRÓXIMA",
        due_date=TODAY + timedelta(days=4),
        navigation="Memorandos",
    )
    assert alert_from_pending(ongoing, NOW).severity == ALTO
    assert alert_from_pending(ongoing, NOW).title == "Substituição em andamento"
    assert alert_from_pending(starts, NOW).title == "Substituição inicia hoje"
    assert alert_from_pending(soon, NOW).severity == ATENCAO
    assert alert_from_pending(later, NOW) is None
    assert alert_from_pending(_item(source_module="memorandos", status_original="ENCERRADA", urgency="HOJE", navigation="Memorandos"), NOW) is None


def test_timezone_midnight_and_utc(monkeypatch):
    utc_evening = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)
    local = parse_datetime(utc_evening)
    assert local.date() == date(2026, 9, 14)
    assert now_recife(datetime(2026, 9, 14, 23, 30, tzinfo=timezone.utc)).date() == date(
        2026, 9, 14
    )
    midnight = datetime(2026, 9, 15, 0, 0, tzinfo=INSTITUTIONAL_TZ)
    soon = _item(
        source_module="agenda",
        navigation="Agenda",
        metadata={"inicio": "2026-09-15T00:30:00"},
    )
    assert alert_from_pending(soon, midnight).severity == CRITICO


def test_closed_oficios_are_not_alerts(store):
    oficios, _, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    done = _received(oficios, proge, "2026-09-13")
    oficios.update_status(done, "Concluído", "ok")
    cancelled = _received(oficios, proge, "2026-09-13")
    oficios.update_status(cancelled, "Arquivado", "arquivo")
    overdue = _received(oficios, proge, "2026-09-13")
    today = _received(oficios, proge, "2026-09-14")
    plus1 = _received(oficios, proge, "2026-09-15")
    plus3 = _received(oficios, proge, "2026-09-17")
    plus4 = _received(oficios, proge, "2026-09-18")
    items, _, _ = collect_alerts(
        store, _admin(store), now=NOW, cached_health=HEALTHY, modules=("oficios",)
    )
    ids = {i.source_id for i in items}
    assert overdue in ids and today in ids and plus1 in ids and plus3 in ids
    assert plus4 not in ids and done not in ids and cancelled not in ids
    by_id = {i.source_id: i for i in items}
    assert by_id[overdue].severity == CRITICO
    assert by_id[today].severity == ALTO
    assert by_id[plus1].severity == ATENCAO
    assert by_id[plus3].severity == ATENCAO


def test_agenda_store_rules(store):
    _, agenda, _ = _prepare(store)
    past = agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-10T10:00:00",
            fim="2026-09-10T11:00:00",
            situacao="Agendado",
            titulo="Passado",
            reuniao_com="X",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    today = agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-14T15:00:00",
            fim="2026-09-14T16:00:00",
            situacao="Agendado",
            titulo="Hoje tarde",
            reuniao_com="X",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    brief = agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-14T13:00:00",
            fim="2026-09-14T14:00:00",
            situacao="Agendado",
            titulo="Em breve",
            reuniao_com="X",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    tomorrow = agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-15T08:00:00",
            fim="2026-09-15T09:00:00",
            situacao="Agendado",
            titulo="Amanhã cedo",
            reuniao_com="X",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    far = agenda.save(
        dict(
            tipo="EVENTO",
            procuradores=[1],
            inicio="2026-09-20T09:00:00",
            fim="2026-09-20T10:00:00",
            situacao="Agendado",
            titulo="Longe",
            categoria="Curso",
            local="TCE-PB",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    cancelled = agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-14T16:00:00",
            fim="2026-09-14T17:00:00",
            situacao="Agendado",
            titulo="Cancelada",
            reuniao_com="X",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    agenda.cancel(cancelled)
    items, _, _ = collect_alerts(
        store, _admin(store), now=NOW, cached_health=HEALTHY, modules=("agenda",)
    )
    ids = {i.source_id for i in items}
    assert today in ids and brief in ids and tomorrow in ids
    assert past not in ids and far not in ids and cancelled not in ids
    by_id = {i.source_id: i for i in items}
    assert by_id[brief].severity == CRITICO
    assert by_id[today].severity == ALTO
    assert by_id[tomorrow].severity == ATENCAO


def test_memorando_store_rules(store):
    _, _, memorandos = _prepare(store)
    ongoing = memorandos.save_draft(
        memo_record(data_inicio="2026-09-10", data_fim="2026-09-20", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    starts = memorandos.save_draft(
        memo_record(data_inicio="2026-09-14", data_fim="2026-09-20", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    soon = memorandos.save_draft(
        memo_record(data_inicio="2026-09-17", data_fim="2026-09-20", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    later = memorandos.save_draft(
        memo_record(data_inicio="2026-09-18", data_fim="2026-09-20", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    ended = memorandos.save_draft(
        memo_record(data_inicio="2026-08-01", data_fim="2026-09-01", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    cancelled = memorandos.save_draft(
        memo_record(data_inicio="2026-09-16", data_fim="2026-09-25", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    with store.connection() as c:
        c.execute("UPDATE memorandos SET status='CANCELADO' WHERE id=?", (cancelled,))
    items, _, _ = collect_alerts(
        store, _admin(store), now=NOW, cached_health=HEALTHY, modules=("memorandos",)
    )
    ids = {i.source_id for i in items}
    assert ongoing in ids and starts in ids and soon in ids
    assert later not in ids and ended not in ids and cancelled not in ids


def test_permissions_and_cabinet_filter(store):
    oficios, agenda, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    sbbq = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "SBBQ")
    _received(oficios, proge, "2026-09-13", gabinete_member=proge)
    _received(oficios, sbbq, "2026-09-13", gabinete_member=sbbq)
    agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[7],
            inicio="2026-09-14T15:00:00",
            fim="2026-09-14T16:00:00",
            situacao="Agendado",
            titulo="SBBQ reunião",
            reuniao_com="X",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    admin_items, _, _ = collect_alerts(
        store, _admin(store), now=NOW, cached_health=HEALTHY
    )
    assert {i.gabinete for i in admin_items if i.source_module == "oficios"} >= {
        "PROGE",
        "SBBQ",
    }
    proge_user = _user(
        store,
        "proge.alerts@test.local",
        pode_agenda=True,
        pode_oficios=True,
        pode_memorandos=False,
        pode_portarias=False,
        pode_admin=False,
        gabinetes=["PROGE"],
    )
    assert has_permission(proge_user, "alertas")
    scoped, _, _ = collect_alerts(store, proge_user, now=NOW, gabinete="SBBQ")
    assert scoped == []
    visible, _, _ = collect_alerts(store, proge_user, now=NOW)
    assert all(i.gabinete != "SBBQ" for i in visible if i.source_module == "oficios")
    assert all(i.source_module != "sistema" for i in visible)
    no_oficios = _user(
        store,
        "agenda.alerts@test.local",
        pode_agenda=True,
        pode_oficios=False,
        pode_memorandos=False,
        pode_portarias=False,
        pode_admin=False,
        gabinetes=[],
    )
    only_agenda, _, _ = collect_alerts(store, no_oficios, now=NOW)
    assert all(i.source_module != "oficios" for i in only_agenda)
    assert all(i.source_module != "sistema" for i in only_agenda)
    portarias_only = _user(
        store,
        "port.alerts@test.local",
        pode_agenda=False,
        pode_oficios=False,
        pode_memorandos=False,
        pode_portarias=True,
        pode_admin=False,
        gabinetes=[],
    )
    assert not has_permission(portarias_only, "alertas")
    assert not can_view_alertas(portarias_only)
    with pytest.raises(ValueError, match="módulo"):
        collect_alerts(store, portarias_only, now=NOW)
    assert has_permission(portarias_only, "tarefas")
    assert not has_permission(portarias_only, "pendencias")
    tarefas_only = _user(
        store,
        "tarefas.alerts@test.local",
        pode_agenda=False,
        pode_oficios=False,
        pode_memorandos=False,
        pode_portarias=False,
        pode_admin=False,
        gabinetes=[],
    )
    assert has_permission(tarefas_only, "tarefas")
    assert not has_permission(tarefas_only, "alertas")
    assert not can_view_alertas(tarefas_only)
    with pytest.raises(ValueError, match="módulo"):
        collect_alerts(store, tarefas_only, now=NOW)


def test_system_alerts_remain_visible_on_hoje_period(store):
    broken = {
        **HEALTHY,
        "database": {"status": ERROR, "summary": "ERRO — banco"},
    }
    items, _, _ = collect_alerts(
        store,
        _admin(store),
        now=NOW,
        modules=("sistema",),
        period="hoje",
        cached_health=broken,
    )
    assert any(i.source_module == "sistema" and i.category == "banco" for i in items)


def test_system_alerts_admin_only(store):
    assert system_alerts(store, cached=HEALTHY) == []
    items = system_alerts(
        store,
        cached={
            **HEALTHY,
            "database": {"status": ERROR, "summary": "ERRO — banco"},
        },
    )
    assert items[0].severity == CRITICO
    assert items[0].category == "banco"
    pdf = system_alerts(
        store,
        cached={
            **HEALTHY,
            "documents": {
                "pdf": {"status": ATTENTION, "summary": "ATENÇÃO — conversor PDF não detectado"}
            },
        },
    )
    assert pdf[0].severity == ATENCAO
    assert pdf[0].category == "conversor_pdf"
    historical = system_alerts(
        store,
        cached={**HEALTHY, "audit": {"status": OK, "errors_24h": 2}},
    )
    assert historical == []
    user = _user(
        store,
        "no.sys@test.local",
        pode_agenda=True,
        pode_oficios=False,
        pode_memorandos=False,
        pode_portarias=False,
        pode_admin=False,
        gabinetes=[],
    )
    broken = {
        **HEALTHY,
        "database": {"status": ERROR, "summary": "ERRO — banco"},
    }
    assert system_alerts(store, cached=broken, principal=user) == []
    collected, errors, _ = collect_alerts(
        store, user, now=NOW, cached_health=broken
    )
    assert all(i.source_module != "sistema" for i in collected)
    assert "sistema" not in errors
    forced, _, _ = collect_alerts(
        store, user, now=NOW, modules=("sistema",), cached_health=broken
    )
    assert forced == []


def test_historical_operational_error_is_not_a_live_alert(store):
    from database.audit import AuditStore
    from services.audit import registrar_erro
    from services.system_health import inspect_audit

    admin = _admin(store)
    user = _user(
        store,
        "comum.hist@test.local",
        pode_agenda=True,
        pode_oficios=True,
        pode_memorandos=True,
        pode_portarias=False,
        pode_admin=False,
        gabinetes=["PROGE"],
    )
    registrar_erro(
        store,
        modulo="admin",
        acao="DIAGNOSTICO",
        erro=RuntimeError("falha já corrigida"),
        principal=admin,
    )
    audit = inspect_audit(store)
    assert audit["status"] == OK
    assert audit["errors_24h"] == 1
    events = AuditStore(store).list_events({"evento": "ERRO_OPERACIONAL"})
    assert len(events) == 1
    assert events[0]["modulo"] == "admin"
    assert events[0]["resultado"] == "ERRO"
    cached = {**HEALTHY, "audit": audit}
    assert system_alerts(store, cached=cached, principal=admin) == []
    items, _, _ = collect_alerts(store, admin, now=NOW, cached_health=cached)
    assert all(i.category != "erro_operacional" for i in items)
    assert all(i.source_id != "audit_errors" for i in items)
    summary = get_alert_summary(store, admin, now=NOW, cached_health=cached)
    assert all(i.source_module != "sistema" for i in summary["top"])
    user_summary = get_alert_summary(store, user, now=NOW, cached_health=cached)
    assert all(i.source_module != "sistema" for i in user_summary["top"])
    assert user_summary["counts"]["total"] == user_summary["total"]
    leftover = AuditStore(store).list_events({"evento": "ERRO_OPERACIONAL"})
    assert len(leftover) == 1
    assert leftover[0]["id"] == events[0]["id"]


def test_current_health_failures_alert_admin_and_clear_when_ok(store):
    admin = _admin(store)
    user = _user(
        store,
        "comum.tech@test.local",
        pode_agenda=True,
        pode_oficios=True,
        pode_memorandos=False,
        pode_portarias=False,
        pode_admin=False,
        gabinetes=["PROGE"],
    )
    db_fail = {
        **HEALTHY,
        "database": {"status": ERROR, "summary": "ERRO — banco"},
    }
    admin_items, _, _ = collect_alerts(store, admin, now=NOW, cached_health=db_fail)
    assert any(i.category == "banco" and i.severity == CRITICO for i in admin_items)
    admin_summary = get_alert_summary(store, admin, now=NOW, cached_health=db_fail)
    assert admin_summary["total"] >= 1
    assert any(i.category == "banco" for i in admin_summary["top"])
    user_items, user_errors, _ = collect_alerts(
        store, user, now=NOW, cached_health=db_fail
    )
    assert all(i.source_module != "sistema" for i in user_items)
    assert "sistema" not in user_errors
    user_summary = get_alert_summary(store, user, now=NOW, cached_health=db_fail)
    assert all(i.source_module != "sistema" for i in user_summary["top"])
    recovered, _, _ = collect_alerts(store, admin, now=NOW, cached_health=HEALTHY)
    assert all(i.source_module != "sistema" for i in recovered)

    schema_fail = {
        **HEALTHY,
        "schema": {
            "status": ERROR,
            "summary": "ERRO — Schema indisponível",
            "missing_tables": ["auditoria_eventos"],
        },
    }
    schema_items = system_alerts(store, cached=schema_fail, principal=admin)
    assert any(i.category == "schema" for i in schema_items)
    schema_attention = {
        **HEALTHY,
        "schema": {
            "status": ATTENTION,
            "summary": "ATENÇÃO — Estrutura esperada não encontrada",
            "missing_tables": ["auditoria_eventos"],
            "missing_columns": [],
        },
    }
    assert any(
        i.category == "schema"
        for i in system_alerts(store, cached=schema_attention, principal=admin)
    )
    pdf_fail = {
        **HEALTHY,
        "documents": {
            "pdf": {"status": ATTENTION, "summary": "ATENÇÃO — conversor PDF não detectado"}
        },
    }
    assert any(
        i.category == "conversor_pdf"
        for i in system_alerts(store, cached=pdf_fail, principal=admin)
    )
    audit_down = {
        **HEALTHY,
        "audit": {"status": ERROR, "summary": "ERRO — Auditoria indisponível", "errors_24h": 0},
    }
    assert any(
        i.category == "auditoria"
        for i in system_alerts(store, cached=audit_down, principal=admin)
    )
    assert system_alerts(store, cached=HEALTHY, principal=admin) == []
    assert system_alerts(store, cached=db_fail, principal=user) == []


def test_isolation_when_oficios_fails(store, monkeypatch):
    _, agenda, _ = _prepare(store)
    agenda.save(
        dict(
            tipo="EVENTO",
            procuradores=[1],
            inicio="2026-09-14T15:00:00",
            fim="2026-09-14T16:00:00",
            situacao="Agendado",
            titulo="Agenda isolada",
            categoria="Curso",
            local="TCE-PB",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )

    def boom(*args, **kwargs):
        raise RuntimeError("oficios indisponível")

    monkeypatch.setattr("services.pending.fetch_oficios", boom)
    items, errors, _ = collect_alerts(
        store, _admin(store), now=NOW, cached_health=HEALTHY
    )
    assert errors.get("oficios") == "oficios"
    assert any(i.source_module == "agenda" for i in items)


def test_alert_summary_and_home_stay_light(store):
    from portal import home
    from services.alerts import get_alert_summary

    oficios, _, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    for day in range(13, 19):
        _received(oficios, proge, f"2026-09-{day:02d}")
    summary = get_alert_summary(store, _admin(store), now=NOW, cached_health=HEALTHY)
    assert summary["counts"]["criticos"] >= 1
    assert len(summary["top"]) <= 5
    ranks = ["CRÍTICO", "ALTO", "ATENÇÃO", "INFORMATIVO"]
    order = [ranks.index(i.severity) for i in summary["top"]]
    assert order == sorted(order)
    home_src = getsource(home)
    assert "dashboard_counts" not in home_src
    assert "render_home_summary" not in home_src
    assert "get_alert_summary" not in home_src
    zero_user = _user(
        store,
        "empty.alerts@test.local",
        pode_agenda=True,
        pode_oficios=True,
        pode_memorandos=True,
        pode_portarias=False,
        pode_admin=False,
        gabinetes=["LAF"],
    )
    empty = get_alert_summary(store, zero_user, now=NOW)
    assert empty["total"] == 0
    assert empty["top"] == []


def test_system_alerts_require_cache(store):
    assert system_alerts(store) == []
    assert system_alerts(store, cached=None) == []


def test_navigation_helpers_are_safe():
    from portal import (
        PORTAL_NAV_STATE_KEYS,
        apply_portal_navigation,
        open_alertas,
        request_alerts_view,
    )
    from services.alerts_ui import open_alert_origin, render
    from services.access_ui import consume_pending_open_admin
    from services.pending_ui import open_origin

    assert "pending_open_admin" in PORTAL_NAV_STATE_KEYS
    assert "request_alerts_view" in getsource(open_alertas)
    assert "request_portal_navigation" not in getsource(open_alertas)
    assert 'st.session_state["portal_module"]' not in getsource(open_alertas)
    assert 'st.session_state["portal_module"]' not in getsource(open_origin)
    assert 'st.session_state["portal_module"]' not in getsource(open_alert_origin)
    assert "st.rerun" not in getsource(apply_portal_navigation)
    assert "pending_open_admin" in getsource(open_origin)
    assert "admin_secao" in getsource(consume_pending_open_admin)
    assert 'st.session_state["portal_module"]' not in getsource(render)
    from portal import queue_alerts_view

    assert "st.rerun()" not in getsource(queue_alerts_view)
    assert 'st.rerun(scope="app")' in getsource(request_alerts_view)
    assert "pending_open_admin" in getsource(open_origin)
    assert "admin_secao" in getsource(consume_pending_open_admin)
    assert 'st.session_state["portal_module"]' not in getsource(render)


def test_consume_admin_open_before_widgets(monkeypatch):
    from services import access_ui

    state = {
        "pending_open_admin": {
            "secao": "Sistema",
            "aba": "Saúde",
            "audit_tab": "Auditoria",
        }
    }
    monkeypatch.setattr(access_ui.st, "session_state", state)
    access_ui.consume_pending_open_admin()
    assert "pending_open_admin" not in state
    assert state["admin_secao"] == "Sistema"
    assert state["admin_sistema_aba"] == "Saúde"
    assert state["audit_tab"] == "Auditoria"
    access_ui.consume_pending_open_admin()
    assert state["admin_secao"] == "Sistema"
    assert "pending_open_admin" not in state


def test_consume_admin_ignores_invalid_legacy_state(monkeypatch):
    from services import access_ui

    state = {"pending_open_admin": {"secao": "Inexistente", "aba": "X"}}
    monkeypatch.setattr(access_ui.st, "session_state", state)
    access_ui.consume_pending_open_admin()
    assert "admin_secao" not in state
    assert "admin_sistema_aba" not in state
    state["pending_open_admin"] = True
    assert access_ui.consume_pending_open_admin() is None
    assert "admin_secao" not in state
    state["pending_open_admin"] = "Acessos e Auditoria"
    access_ui.consume_pending_open_admin()
    assert state["admin_secao"] == "Acessos e Auditoria"
    assert "pending_open_admin" not in state


def test_menu_home_and_deep_links(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from portal import PORTAL_ALERTS_REQUEST, PORTAL_NAV_REQUEST, PORTAL_SPECIAL_VIEW

    oficios, agenda, memorandos = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    oficio_id = _received(oficios, proge, "2026-09-13")
    agenda_id = agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-14T15:00:00",
            fim="2026-09-14T16:00:00",
            situacao="Agendado",
            titulo="Reunião alerta",
            reuniao_com="X",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    memo_id = memorandos.save_draft(
        memo_record(data_inicio="2026-09-10", data_fim="2026-09-20", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    monkeypatch.setattr("services.alerts.system_alerts", lambda *a, **k: [])
    monkeypatch.setattr("services.pending.today_recife", lambda now=None: TODAY)
    monkeypatch.setattr("services.alerts.now_recife", lambda now=None: NOW)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    portal = next(
        r for r in app.sidebar.radio if getattr(r, "key", None) == "portal_module"
    )
    assert "Alertas" not in portal.options
    assert portal.options[2] == "Portarias"
    assert any(getattr(b, "key", None) == "open_portarias" for b in app.button)
    assert not any(b.label == "Ver alertas" for b in app.button)
    labels = " ".join(str(getattr(b, "label", "")) for b in app.button)
    assert "🔔 0" not in labels
    app.session_state[PORTAL_ALERTS_REQUEST] = True
    app.run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Início"
    assert PORTAL_NAV_REQUEST not in app.session_state
    headings = [str(h.value) for h in app.subheader]
    assert any("ALERTAS" in h for h in headings)
    app.run()
    assert any("ALERTAS" in str(h.value) for h in app.subheader)
    app.button(key="alert_open_0").click().run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Ofícios"
    assert PORTAL_SPECIAL_VIEW not in app.session_state
    assert "pending_open_oficio" not in app.session_state
    assert app.session_state["oficio_detail"] == oficio_id

    app.session_state[PORTAL_ALERTS_REQUEST] = True
    app.run()
    app.selectbox(key="alerts_module").set_value("agenda").run()
    app.button(key="alert_open_0").click().run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Agenda"
    assert "pending_open_agenda" not in app.session_state
    assert app.session_state["agenda_edit"]["id"] == agenda_id

    app.session_state[PORTAL_ALERTS_REQUEST] = True
    app.run()
    app.selectbox(key="alerts_module").set_value("memorandos").run()
    app.button(key="alert_open_0").click().run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Memorandos"
    assert "pending_open_memorando" not in app.session_state
    assert app.session_state["memorandos_nav"] == "Histórico"
    assert memo_id


def test_alerts_view_back_and_stale_menu_value(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from portal import PORTAL_ALERTS_REQUEST, PORTAL_SPECIAL_VIEW

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    monkeypatch.setattr("services.alerts.system_alerts", lambda *a, **k: [])
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.session_state["portal_module"] = "Alertas"
    app.run()
    assert not app.exception
    portal = next(
        r for r in app.sidebar.radio if getattr(r, "key", None) == "portal_module"
    )
    assert "Alertas" not in portal.options
    assert portal.value == "Início"
    app.session_state[PORTAL_ALERTS_REQUEST] = True
    app.run()
    assert any("ALERTAS" in str(h.value) for h in app.subheader)
    app.button(key="alerts_back").click().run()
    assert not app.exception
    assert PORTAL_SPECIAL_VIEW not in app.session_state
    assert app.sidebar.radio(key="portal_module").value == "Início"
    assert not any("ALERTAS" in str(h.value) for h in app.subheader)


def test_apply_alerts_request_is_consumed_once(monkeypatch):
    import portal

    state = {"portal_module": "Portarias"}
    monkeypatch.setattr(portal.st, "session_state", state)
    portal.queue_portal_navigation = portal.queue_portal_navigation
    state[portal.PORTAL_ALERTS_REQUEST] = True
    assert portal.apply_alerts_view_request() == portal.ALERTS_VIEW
    assert portal.PORTAL_ALERTS_REQUEST not in state
    assert state[portal.PORTAL_SPECIAL_VIEW] == portal.ALERTS_VIEW
    assert state[portal.PORTAL_SPECIAL_ANCHOR] == "Portarias"
    assert portal.apply_alerts_view_request() is None
    assert state[portal.PORTAL_SPECIAL_VIEW] == portal.ALERTS_VIEW
    assert portal.alerts_overlay_active("Agenda") is False
    assert portal.PORTAL_SPECIAL_VIEW not in state


def test_admin_system_alert_opens_health(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from portal import PORTAL_ALERTS_REQUEST
    from services.alerts import AlertItem

    def fake_system(*args, **kwargs):
        return [
            AlertItem(
                source_module="sistema",
                source_id="database",
                gabinete="—",
                severity=CRITICO,
                category="banco",
                title="Banco indisponível",
                description="ERRO — banco",
                date=TODAY,
                datetime=None,
                source_status=ERROR,
                navigation_target="Administração",
                metadata={"secao": "Sistema", "aba": "Saúde"},
            )
        ]

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    monkeypatch.setattr("services.alerts.system_alerts", fake_system)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.session_state[PORTAL_ALERTS_REQUEST] = True
    app.run()
    app.selectbox(key="alerts_module").set_value("sistema").run()
    app.button(key="alert_open_0").click().run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Administração"
    assert "pending_open_admin" not in app.session_state
    assert app.session_state["admin_secao"] == "Sistema"
    assert app.session_state["admin_sistema_aba"] == "Saúde"


def test_alert_summary_cache_uses_revision(store, monkeypatch):
    from services import alerts_ui
    from services.alerts import ALERTS_REVISION_KEY, invalidate_alert_summary

    oficios, agenda, memorandos = _prepare(store)
    state = {}
    monkeypatch.setattr(alerts_ui.st, "session_state", state)
    monkeypatch.setattr("services.alerts.system_alerts", lambda *a, **k: [])
    monkeypatch.setattr("services.alerts.now_recife", lambda now=None: NOW)
    calls = {"n": 0}
    original = alerts_ui.get_alert_summary

    def counted(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(alerts_ui, "get_alert_summary", counted)
    first = alerts_ui.load_bell_summary(store, _admin(store))
    second = alerts_ui.load_bell_summary(store, _admin(store))
    assert first["total"] == second["total"] == 0
    assert calls["n"] == 1
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    _received(oficios, proge, "2026-09-13")
    stale = alerts_ui.load_bell_summary(store, _admin(store))
    assert stale["total"] == 0
    assert calls["n"] == 1
    assert invalidate_alert_summary(state) == 1
    assert state[ALERTS_REVISION_KEY] == 1
    fresh = alerts_ui.load_bell_summary(store, _admin(store))
    assert fresh["total"] >= 1
    assert calls["n"] == 2
    alerts_ui.load_bell_summary(store, _admin(store))
    assert calls["n"] == 2
    agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-14T15:00:00",
            fim="2026-09-14T16:00:00",
            situacao="Agendado",
            titulo="Novo",
            reuniao_com="X",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    invalidate_alert_summary(state)
    after_agenda = alerts_ui.load_bell_summary(store, _admin(store))
    assert after_agenda["total"] > fresh["total"]
    memorandos.save_draft(
        memo_record(data_inicio="2026-09-10", data_fim="2026-09-20", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    invalidate_alert_summary(state)
    after_memo = alerts_ui.load_bell_summary(store, _admin(store))
    assert after_memo["total"] > after_agenda["total"]


def test_failed_write_does_not_invalidate():
    from services.alerts import ALERTS_REVISION_KEY, alerts_revision, invalidate_alert_summary

    state = {ALERTS_REVISION_KEY: 3}
    try:
        raise ValueError("falha")
    except ValueError:
        pass
    assert alerts_revision(state) == 3
    invalidate_alert_summary(state)
    assert alerts_revision(state) == 4


def test_modules_invalidate_after_success_only():
    from services.agenda_ui import done as agenda_done
    from services.oficios_ui import done as oficios_done
    from services import memorandos_ui, system_ui

    assert "invalidate_alert_summary" in getsource(agenda_done)
    assert "invalidate_alert_summary" in getsource(oficios_done)
    assert "invalidate_alert_summary" in getsource(memorandos_ui._finalize_active)
    assert "invalidate_alert_summary" in getsource(memorandos_ui._editor)
    assert "invalidate_alert_summary" in getsource(memorandos_ui._details)
    health = getsource(system_ui.render_health)
    assert "invalidate_alert_summary" in health
    assert "registrado" in health
    assert "Erro operacional recente" not in health
    assert "if refresh:" in health
    assert "cache_data.clear" not in getsource(
        __import__("services.alerts", fromlist=["invalidate_alert_summary"]).invalidate_alert_summary
    )
    except_block = getsource(memorandos_ui._finalize_active)
    assert except_block.find("invalidate_alert_summary") < except_block.find(
        "except ValueError"
    )


def test_inicio_after_change_refreshes_bell_without_f5(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from services.alerts import ALERTS_REVISION_KEY, BELL_CACHE_KEY

    oficios, _, _ = _prepare(store)
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    monkeypatch.setattr("services.alerts.system_alerts", lambda *a, **k: [])
    monkeypatch.setattr("services.alerts.now_recife", lambda now=None: NOW)
    monkeypatch.setattr("services.pending.today_recife", lambda now=None: TODAY)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    cached = app.session_state[BELL_CACHE_KEY]
    assert cached["payload"]["total"] == 0
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    _received(oficios, proge, "2026-09-13")
    app.sidebar.radio(key="portal_module").set_value("Pendências").run()
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    assert app.session_state[BELL_CACHE_KEY]["payload"]["total"] == 0
    app.session_state[ALERTS_REVISION_KEY] = 1
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    assert app.session_state[BELL_CACHE_KEY]["payload"]["total"] >= 1
    assert "cache_data.clear" not in getsource(
        __import__("portal", fromlist=["home"]).home
    )


def test_bell_navigation_closes_popover_without_consuming_alerts(store, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from database.access import AccessStore
    from database.store import ROOT
    from database.tarefas import TarefasStore
    from services.alerts import BELL_CACHE_KEY
    from services.alerts_ui import BELL_INTENT, bell_is_open, bell_widget_key

    enable_login(monkeypatch, store)
    owner = AccessStore(store).get_by_email(TEST_IDENTITY["email"])
    task = TarefasStore(store).create(
        owner["id"],
        {"titulo": "Alerta do sininho", "prazo_data": "2026-09-13"},
    )
    monkeypatch.setattr("database.store.Store", lambda: store)
    monkeypatch.setattr("services.alerts.system_alerts", lambda *a, **k: [])
    monkeypatch.setattr("services.alerts.now_recife", lambda now=None: NOW)
    monkeypatch.setattr("services.pending.today_recife", lambda now=None: TODAY)

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    total = app.session_state[BELL_CACHE_KEY]["payload"]["total"]
    assert total >= 1

    app.session_state[bell_widget_key(app.session_state)] = True
    app.run()
    assert bell_is_open(app.session_state) is True
    app.button(key="bell_open_0").click().run()

    assert not app.exception
    assert bell_is_open(app.session_state) is False
    assert app.sidebar.radio(key="portal_module").value == "Tarefas"
    assert app.session_state[BELL_CACHE_KEY]["payload"]["total"] == total
    assert TarefasStore(store).get(task["id"], owner["id"])["status"] == "A_FAZER"

    app.session_state[bell_widget_key(app.session_state)] = True
    app.run()
    assert bell_is_open(app.session_state) is False
    app.session_state[BELL_INTENT] = True
    app.session_state[bell_widget_key(app.session_state)] = True
    app.run()
    assert bell_is_open(app.session_state) is True
    assert app.session_state[BELL_CACHE_KEY]["payload"]["total"] == total
    assert app.button(key="bell_open_0")


def test_closed_bell_stays_closed_through_task_reruns(store, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from database.access import AccessStore
    from database.store import ROOT
    from database.tarefas import TarefasStore
    from services.alerts import BELL_CACHE_KEY
    from services.alerts_ui import BELL_INTENT, bell_is_open, bell_widget_key

    enable_login(monkeypatch, store)
    owner = AccessStore(store).get_by_email(TEST_IDENTITY["email"])
    repo = TarefasStore(store)
    task = repo.create(
        owner["id"],
        {
            "titulo": "Sininho permanece fechado",
            "descricao": "Texto inicial",
            "observacoes": "Nota inicial",
            "prioridade": "NORMAL",
            "prazo_data": "2026-09-13",
        },
    )
    created = repo.get(task["id"], owner["id"])["criado_em"]
    monkeypatch.setattr("database.store.Store", lambda: store)
    monkeypatch.setattr("services.alerts.system_alerts", lambda *a, **k: [])
    monkeypatch.setattr("services.alerts.now_recife", lambda now=None: NOW)
    monkeypatch.setattr("services.pending.today_recife", lambda now=None: TODAY)

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    total = app.session_state[BELL_CACHE_KEY]["payload"]["total"]
    assert total >= 1
    app.session_state[bell_widget_key(app.session_state)] = True
    app.run()
    assert bell_is_open(app.session_state) is True

    app.button(key="bell_open_0").click().run()
    assert not app.exception
    assert bell_is_open(app.session_state) is False
    assert app.sidebar.radio(key="portal_module").value == "Tarefas"

    def closed():
        assert bell_is_open(app.session_state) is False
        assert BELL_INTENT in app.session_state
        assert app.session_state[BELL_INTENT] is False

    prefix = f"tarefas_form_{task['id']}"
    if f"{prefix}cancel" not in {button.key for button in app.button}:
        app.button(key=f"task_edit_{task['id']}").click().run()
    closed()
    app.text_input(key=prefix + "title").set_value("Sininho título novo").run()
    app.text_area(key=prefix + "description").set_value("Descrição nova").run()
    app.text_area(key=prefix + "notes").set_value("Observação nova").run()
    app.selectbox(key=prefix + "priority").set_value("ALTA").run()
    closed()
    app.button(key=prefix + "submit").click().run()
    assert not app.exception
    closed()
    saved = repo.get(task["id"], owner["id"])
    assert saved["titulo"] == "Sininho título novo"
    assert saved["descricao"] == "Descrição nova"
    assert saved["observacoes"] == "Observação nova"
    assert saved["prioridade"] == "ALTA"
    assert saved["status"] == "A_FAZER"
    assert saved["criado_em"] == created
    assert saved["prazo_data"] == "2026-09-13"
    with store.connection(read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM tarefas WHERE id=?", (task["id"],)
            ).fetchone()[0]
            == 1
        )

    app.button(key=f"task_edit_{task['id']}").click().run()
    closed()
    app.button(key=prefix + "cancel").click().run()
    assert not app.exception
    closed()
    assert "tarefas_edit" not in app.session_state
    assert repo.get(task["id"], owner["id"])["titulo"] == "Sininho título novo"

    app.button(key=f"task_start_{task['id']}").click().run()
    assert not app.exception
    closed()
    assert repo.get(task["id"], owner["id"])["status"] == "EM_ANDAMENTO"
    assert app.session_state[BELL_CACHE_KEY]["payload"]["total"] == total

    app.session_state[BELL_INTENT] = True
    app.session_state[bell_widget_key(app.session_state)] = True
    app.run()
    assert bell_is_open(app.session_state) is True
    assert app.button(key="bell_open_0")


def test_audit_alertas_once_per_entry(store):
    from services.audit import registrar_modulo

    principal = _admin(store)
    state = {}
    registrar_modulo(store, principal, "Alertas", state=state)
    registrar_modulo(store, principal, "Alertas", state=state)
    with store.connection(read_only=True) as c:
        count = c.execute(
            "SELECT COUNT(*) FROM auditoria_eventos WHERE evento=?",
            ("ALERTAS_ACESSADOS",),
        ).fetchone()[0]
    assert count == 1


def test_postgres_alerts_contract(pg_store):
    oficios, _, _ = _prepare(pg_store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    _received(oficios, proge, "2026-09-13")
    items, errors, _ = collect_alerts(
        pg_store, _admin(pg_store), now=NOW, cached_health=HEALTHY, modules=("oficios",)
    )
    assert errors.get("oficios") is None
    assert any(i.source_module == "oficios" and i.severity == CRITICO for i in items)
