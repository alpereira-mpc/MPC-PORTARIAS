from datetime import date, datetime, timedelta, timezone
from inspect import getsource

import pytest

from database.agenda import AgendaStore
from database.memorandos import MemorandosStore
from database.oficios import OficiosStore
from services.access import has_permission, resolve_principal
from services.pending import (
    FUTURA,
    HOJE,
    PROXIMA,
    SEM_PRAZO,
    URGENTE,
    VENCIDA,
    URGENCY_ORDER,
    classify_agenda,
    classify_deadline,
    classify_memorando,
    collect_pending,
    parse_date,
    today_recife,
)
from tests.access_testing import TEST_IDENTITY, enable_login, seed_access
from tests.test_oficios import files, ready, sample as oficio_sample
from tests.test_postgresql import pg_store, pg_url  # noqa: F401

TODAY = date(2026, 9, 14)


def _admin(store):
    return resolve_principal(store, TEST_IDENTITY)


def _user(store, email, **kwargs):
    seed_access(store, email=email, perfil="USUARIO", **kwargs)
    return resolve_principal(store, {"email": email})


def _received(service, member, prazo, status="Recebido", gabinete_member=None):
    member = gabinete_member or member
    return service.save(
        dict(
            direcao="RECEBIDO",
            numero_externo="EXT-" + prazo,
            remetente="Remetente",
            instituicao="Órgão",
            data="2026-09-01",
            data_recebimento="2026-09-10",
            membros=[member],
            assunto="Assunto " + prazo,
            prazo=prazo,
            status=status,
        )
    )


def _prepare(store):
    oficios = ready(store)
    agenda = AgendaStore(store)
    memorandos = MemorandosStore(store)
    return oficios, agenda, memorandos


def test_deadline_classification():
    assert classify_deadline(TODAY - timedelta(days=1), TODAY) == VENCIDA
    assert classify_deadline(TODAY, TODAY) == HOJE
    assert classify_deadline(TODAY + timedelta(days=1), TODAY) == URGENTE
    assert classify_deadline(TODAY + timedelta(days=3), TODAY) == URGENTE
    assert classify_deadline(TODAY + timedelta(days=4), TODAY) == PROXIMA
    assert classify_deadline(TODAY + timedelta(days=7), TODAY) == PROXIMA
    assert classify_deadline(TODAY + timedelta(days=8), TODAY) == FUTURA
    assert classify_deadline(None, TODAY) == SEM_PRAZO


def test_agenda_and_memorando_semantics():
    assert classify_agenda(TODAY - timedelta(days=1), TODAY) is None
    assert classify_agenda(TODAY, TODAY) == HOJE
    assert classify_memorando("EM ANDAMENTO", TODAY - timedelta(days=2), TODAY) == HOJE
    assert classify_memorando("AGENDADA", TODAY, TODAY) == HOJE
    assert classify_memorando("AGENDADA", TODAY + timedelta(days=2), TODAY) == URGENTE
    assert classify_memorando("ENCERRADA", TODAY, TODAY) is None


def test_recife_date_only_is_not_shifted_to_yesterday():
    assert parse_date("2026-09-14") == date(2026, 9, 14)
    utc_evening = datetime(2026, 9, 15, 2, 0, tzinfo=timezone.utc)
    assert parse_date(utc_evening) == date(2026, 9, 14)
    assert today_recife(datetime(2026, 9, 14, 23, 30, tzinfo=timezone.utc)) == date(
        2026, 9, 14
    )


def test_sources_include_and_exclude(store):
    oficios, agenda, memorandos = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    _received(oficios, proge, "2026-09-13")
    _received(oficios, proge, "2026-09-14")
    future = _received(oficios, proge, "2026-09-22")
    done = _received(oficios, proge, "2026-09-10")
    oficios.update_status(done, "Concluído", "ok")
    cancelled = _received(oficios, proge, "2026-09-11")
    oficios.update_status(cancelled, "Arquivado", "arquivo")
    follow = oficios.save(oficio_sample(oficios))
    oficios.finalize(follow, files)
    oficios.update_status(follow, "Enviado", "enviado", sent="2026-09-14")
    oficios.update_status(follow, "Aguardando resposta", "espera")
    agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-14T10:00:00",
            fim="2026-09-14T11:00:00",
            situacao="Agendado",
            titulo="Reunião de hoje",
            reuniao_com="Conselheiro",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    agenda.save(
        dict(
            tipo="EVENTO",
            procuradores=[1],
            inicio="2026-09-20T09:00:00",
            fim="2026-09-20T10:00:00",
            situacao="Agendado",
            titulo="Evento futuro",
            categoria="Curso",
            local="TCE-PB",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    past = agenda.save(
        dict(
            tipo="DESPACHO",
            procuradores=[1],
            inicio="2026-09-10T09:00:00",
            fim="2026-09-10T10:00:00",
            situacao="Agendado",
            processo="TC 1/2026",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    cancelled_ag = agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-16T09:00:00",
            fim="2026-09-16T10:00:00",
            situacao="Agendado",
            titulo="Cancelada",
            reuniao_com="Conselheiro",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    agenda.cancel(cancelled_ag)
    from tests.test_memorandos import record as memo_record

    memorandos.save_draft(
        memo_record(data_inicio="2026-09-18", data_fim="2026-09-20", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    memorandos.save_draft(
        memo_record(data_inicio="2026-09-10", data_fim="2026-09-20", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    memorandos.save_draft(
        memo_record(data_inicio="2026-08-01", data_fim="2026-09-01", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    cancelled_m = memorandos.save_draft(
        memo_record(data_inicio="2026-09-19", data_fim="2026-09-25", gabinete_procurador_id=1),
        actor_email="admin@test.local",
    )
    with store.connection() as c:
        c.execute(
            "UPDATE memorandos SET status='CANCELADO' WHERE id=?", (cancelled_m,)
        )
    items, errors, _ = collect_pending(store, _admin(store), today=TODAY)
    assert not any(errors.values())
    titles = {i.title for i in items}
    assert any(i.due_date == date(2026, 9, 13) and i.urgency == VENCIDA for i in items)
    assert any(i.urgency == HOJE and i.source_module == "oficios" for i in items)
    assert any(i.source_id == future for i in items)
    assert all(i.source_id not in {done, cancelled} for i in items)
    assert any(i.status_original == "Aguardando resposta" for i in items)
    assert "Reunião de hoje" in titles
    assert "Evento futuro" in titles
    assert "Passado" not in titles
    assert "Cancelada" not in titles
    assert any(i.status_original == "AGENDADA" for i in items)
    assert any(i.status_original == "EM ANDAMENTO" for i in items)
    assert cancelled_m not in {i.source_id for i in items}
    assert past not in {i.source_id for i in items}


def test_ordering(store):
    oficios, _, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    _received(oficios, proge, "2026-09-22")
    _received(oficios, proge, "2026-09-14")
    _received(oficios, proge, "2026-09-13")
    _received(oficios, proge, "2026-09-16")
    _received(oficios, proge, "2026-09-19")
    items, _, _ = collect_pending(
        store, _admin(store), today=TODAY, modules=("oficios",)
    )
    order = [i.urgency for i in items]
    ranks = [URGENCY_ORDER.index(u) for u in order]
    assert ranks == sorted(ranks)
    vencidas = [i for i in items if i.urgency == VENCIDA]
    assert vencidas[0].due_date <= vencidas[-1].due_date if len(vencidas) > 1 else True


def test_permissions_modules_and_cabinets(store):
    oficios, agenda, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    sbbq = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "SBBQ")
    _received(oficios, proge, "2026-09-15", gabinete_member=proge)
    _received(oficios, sbbq, "2026-09-15", gabinete_member=sbbq)
    agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[7],
            inicio="2026-09-15T10:00:00",
            fim="2026-09-15T11:00:00",
            situacao="Agendado",
            titulo="SBBQ reunião",
            reuniao_com="Conselheiro",
            local="Gabinete",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    admin_items, _, _ = collect_pending(store, _admin(store), today=TODAY)
    assert {i.gabinete for i in admin_items if i.source_module == "oficios"} >= {
        "PROGE",
        "SBBQ",
    }
    no_oficios = _user(
        store,
        "agenda.only@test.local",
        pode_agenda=True,
        pode_oficios=False,
        pode_memorandos=False,
        pode_portarias=False,
        pode_admin=False,
        gabinetes=[],
    )
    assert has_permission(no_oficios, "pendencias")
    items, _, _ = collect_pending(store, no_oficios, today=TODAY)
    assert all(i.source_module != "oficios" for i in items)
    proge_user = _user(
        store,
        "proge.only@test.local",
        pode_agenda=True,
        pode_oficios=True,
        pode_memorandos=True,
        pode_portarias=False,
        pode_admin=False,
        gabinetes=["PROGE"],
    )
    scoped, _, _ = collect_pending(store, proge_user, today=TODAY)
    assert all(i.gabinete != "SBBQ" for i in scoped)
    leaked, _, _ = collect_pending(
        store, proge_user, today=TODAY, gabinete="SBBQ"
    )
    assert leaked == []
    with pytest.raises(ValueError, match="módulo"):
        collect_pending(
            store,
            _user(
                store,
                "nenhum@test.local",
                pode_agenda=False,
                pode_oficios=False,
                pode_memorandos=False,
                pode_portarias=True,
                pode_admin=False,
                gabinetes=[],
            ),
            today=TODAY,
        )


def test_isolation_when_oficios_fails(store, monkeypatch):
    oficios, agenda, _ = _prepare(store)
    agenda.save(
        dict(
            tipo="EVENTO",
            procuradores=[1],
            inicio="2026-09-14T09:00:00",
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
    items, errors, _ = collect_pending(store, _admin(store), today=TODAY)
    assert errors.get("oficios") == "oficios"
    assert any(i.source_module == "agenda" for i in items)


def test_queries_do_not_touch_blobs_or_audit():
    import services.pending as pending

    source = getsource(pending)
    for token in ("conteudo", "docx", "pdf", "auditoria_eventos"):
        assert token not in source


def test_menu_and_central_ui(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    portal = next(
        r for r in app.sidebar.radio if getattr(r, "key", None) == "portal_module"
    )
    assert portal.options[0] == "Início"
    assert portal.options[1] == "Pendências"
    assert "Alertas" not in portal.options
    assert portal.options[2] == "Portarias"
    app.sidebar.radio(key="portal_module").set_value("Pendências").run()
    assert not app.exception
    headings = [str(h.value) for h in app.subheader]
    assert any("PENDÊNCIAS" in h for h in headings)
    assert any("Nenhuma pendência encontrada." in str(m.value) for m in app.markdown)
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    assert any(getattr(b, "key", None) == "open_portarias" for b in app.button)
    assert not any(b.label == "Ver pendências" for b in app.button)
    assert not any(b.label == "Ver alertas" for b in app.button)


def test_portal_navigation_helper_applies_once(monkeypatch):
    import portal

    state = {}
    monkeypatch.setattr(portal.st, "session_state", state)
    portal.queue_portal_navigation("Pendências")
    assert portal.PORTAL_NAV_REQUEST in state
    applied = portal.apply_portal_navigation(["Início", "Pendências", "Ofícios"])
    assert applied == "Pendências"
    assert state["portal_module"] == "Pendências"
    assert portal.PORTAL_NAV_REQUEST not in state
    portal.apply_portal_navigation(["Início", "Pendências", "Ofícios"])
    assert state["portal_module"] == "Pendências"
    assert portal.PORTAL_NAV_REQUEST not in state
    assert "st.rerun" not in getsource(portal.apply_portal_navigation)
    assert "st.rerun" in getsource(portal.request_portal_navigation)


def test_portal_navigation_helper_ignores_disallowed_and_widget_keys(monkeypatch):
    import portal

    state = {"portal_module": "Início"}
    monkeypatch.setattr(portal.st, "session_state", state)
    portal.queue_portal_navigation(
        "Relatórios",
        oficio_page="Recebidos",
        pending_open_oficio={"id": "x", "page": "Recebidos"},
    )
    portal.apply_portal_navigation(["Início", "Pendências"])
    assert state["portal_module"] == "Início"
    assert "oficio_page" not in state
    assert "pending_open_oficio" not in state
    assert portal.PORTAL_NAV_REQUEST not in state


def test_programmatic_navigation_does_not_write_widget_keys():
    from portal import (
        apply_portal_navigation,
        open_admin,
        open_alertas,
        open_agenda,
        open_memorandos,
        open_oficios,
        open_pendencias,
        open_portarias,
    )
    from services.pending_ui import _open, open_origin

    for fn in (
        open_portarias,
        open_agenda,
        open_oficios,
        open_memorandos,
        open_admin,
    ):
        source = getsource(fn)
        assert 'st.session_state["portal_module"]' not in source
        assert "st.session_state['portal_module']" not in source
        assert "queue_portal_navigation" in source
        assert "request_portal_navigation" not in source
        assert "st.rerun()" not in source
    for fn in (open_pendencias, open_origin):
        source = getsource(fn)
        assert 'st.session_state["portal_module"]' not in source
        assert "st.session_state['portal_module']" not in source
        assert "request_portal_navigation" in source
    assert "request_alerts_view" in getsource(open_alertas)
    assert 'st.session_state["portal_module"]' not in getsource(open_alertas)
    assert "Alertas" not in getsource(open_alertas)
    assert 'st.session_state["oficio_page"]' not in getsource(_open)
    assert 'st.session_state["memorandos_nav"]' not in getsource(_open)
    assert 'st.session_state["oficio_gabinete"]' not in getsource(_open)
    assert 'st.session_state["agenda_edit"]' not in getsource(_open)
    apply_src = getsource(apply_portal_navigation)
    assert 'st.session_state["portal_module"]' in apply_src


def test_pending_open_queues_transient_destination_state(monkeypatch):
    from services.pending import PendingItem
    from services.pending_ui import _open

    captured = []

    def fake(module, **state):
        captured.append((module, state))

    monkeypatch.setattr("portal.request_portal_navigation", fake)
    oficios = PendingItem(
        source_module="oficios",
        source_id="of-1",
        gabinete="PROGE",
        title="Ofício",
        subtitle="",
        due_date=TODAY,
        start_date=None,
        end_date=None,
        status_original="Recebido",
        urgency=HOJE,
        context="",
        navigation="Ofícios",
        metadata={"direcao": "RECEBIDO"},
    )
    _open(oficios)
    agenda = PendingItem(
        source_module="agenda",
        source_id="ag-1",
        gabinete="PROGE",
        title="Agenda",
        subtitle="",
        due_date=TODAY,
        start_date=TODAY,
        end_date=None,
        status_original="Agendado",
        urgency=HOJE,
        context="",
        navigation="Agenda",
    )
    _open(agenda)
    memo = PendingItem(
        source_module="memorandos",
        source_id="me-1",
        gabinete="PROGE",
        title="Memo",
        subtitle="",
        due_date=TODAY,
        start_date=TODAY,
        end_date=None,
        status_original="EM ANDAMENTO",
        urgency=HOJE,
        context="",
        navigation="Memorandos",
    )
    _open(memo)
    assert captured[0][0] == "Ofícios"
    assert captured[0][1]["pending_open_oficio"] == {
        "id": "of-1",
        "gabinete": "PROGE",
        "page": "Recebidos",
    }
    assert captured[1] == ("Agenda", {"pending_open_agenda": "ag-1"})
    assert captured[2][0] == "Memorandos"
    assert captured[2][1]["pending_open_memorando"] == {
        "id": "me-1",
        "page": "Histórico",
    }


def test_destination_consumes_pending_open_before_widgets(monkeypatch):
    from services import agenda_ui, memorandos_ui, oficios_ui

    oficio_state = {
        "pending_open_oficio": {
            "id": "of-1",
            "gabinete": "PROGE",
            "page": "Recebidos",
        }
    }
    monkeypatch.setattr(oficios_ui.st, "session_state", oficio_state)
    oficios_ui.consume_pending_open_oficio({"PROGE": {"membro_id": 1}})
    assert "pending_open_oficio" not in oficio_state
    assert oficio_state["oficio_gabinete"] == "PROGE"
    assert oficio_state["oficio_page"] == "Recebidos"
    assert oficio_state["oficio_detail"] == "of-1"
    oficios_ui.consume_pending_open_oficio({"PROGE": {"membro_id": 1}})
    assert oficio_state["oficio_page"] == "Recebidos"

    class Agenda:
        def get(self, identifier):
            return {"id": identifier, "titulo": "Aberto"}

    agenda_state = {"pending_open_agenda": "ag-1"}
    monkeypatch.setattr(agenda_ui.st, "session_state", agenda_state)
    agenda_ui.consume_pending_open_agenda(Agenda())
    assert "pending_open_agenda" not in agenda_state
    assert agenda_state["agenda_edit"]["id"] == "ag-1"
    agenda_ui.consume_pending_open_agenda(Agenda())
    assert agenda_state["agenda_edit"]["id"] == "ag-1"

    from services.memorandos_ui import NAV_HISTORY, NAV_NEW, NAV_OVERVIEW

    memo_state = {
        "pending_open_memorando": {"id": "me-1", "page": "Em andamento"}
    }
    monkeypatch.setattr(memorandos_ui.st, "session_state", memo_state)
    pages = [NAV_OVERVIEW, NAV_NEW, NAV_HISTORY]
    memorandos_ui.consume_pending_open_memorando(pages)
    assert "pending_open_memorando" not in memo_state
    assert memo_state[memorandos_ui.NAV_KEY] == NAV_HISTORY
    memorandos_ui.consume_pending_open_memorando(pages)
    assert memo_state[memorandos_ui.NAV_KEY] == NAV_HISTORY


def test_home_cards_and_manual_pending_menu(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from portal import PORTAL_NAV_REQUEST

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    assert any(getattr(b, "key", None) == "open_portarias" for b in app.button)
    assert not any(b.label == "Ver pendências" for b in app.button)
    app.sidebar.radio(key="portal_module").set_value("Pendências").run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Pendências"
    assert PORTAL_NAV_REQUEST not in app.session_state
    headings = [str(h.value) for h in app.subheader]
    assert any("PENDÊNCIAS" in h for h in headings)
    app.run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Pendências"
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Início"
    app.sidebar.radio(key="portal_module").set_value("Pendências").run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Pendências"


def test_pending_deep_links_open_origin_modules(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from portal import PORTAL_NAV_REQUEST
    from tests.test_memorandos import record as memo_record

    oficios, agenda, memorandos = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    oficio_id = _received(oficios, proge, "2026-09-14")
    agenda_id = agenda.save(
        dict(
            tipo="REUNIAO",
            procuradores=[1],
            inicio="2026-09-14T10:00:00",
            fim="2026-09-14T11:00:00",
            situacao="Agendado",
            titulo="Reunião pendente",
            reuniao_com="Conselheiro",
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
    monkeypatch.setattr("services.pending.today_recife", lambda now=None: TODAY)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio(key="portal_module").set_value("Pendências").run()
    assert not app.exception
    app.selectbox(key="pending_module").set_value("oficios").run()
    app.selectbox(key="pending_open_pick").set_value(0).run()
    app.button(key="pending_open_go").click().run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Ofícios"
    assert PORTAL_NAV_REQUEST not in app.session_state
    assert "pending_open_oficio" not in app.session_state
    assert app.session_state["oficio_gabinete"] == "PROGE"
    assert app.session_state["oficio_page"] == "Recebidos"
    assert app.session_state["oficio_detail"] == oficio_id

    app.sidebar.radio(key="portal_module").set_value("Pendências").run()
    app.selectbox(key="pending_module").set_value("agenda").run()
    app.selectbox(key="pending_open_pick").set_value(0).run()
    app.button(key="pending_open_go").click().run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Agenda"
    assert "pending_open_agenda" not in app.session_state
    assert app.session_state["agenda_edit"]["id"] == agenda_id

    app.sidebar.radio(key="portal_module").set_value("Pendências").run()
    app.selectbox(key="pending_module").set_value("memorandos").run()
    app.selectbox(key="pending_open_pick").set_value(0).run()
    app.button(key="pending_open_go").click().run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Memorandos"
    assert "pending_open_memorando" not in app.session_state
    assert app.session_state["memorandos_nav"] == "Histórico"
    assert "pending_focus_memorando" not in app.session_state or app.session_state[
        "pending_focus_memorando"
    ] == memo_id


def test_postgres_pending_contract(pg_store):
    _prepare(pg_store)
    oficios = OficiosStore(pg_store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    _received(oficios, proge, "2026-09-14")
    items, errors, _ = collect_pending(pg_store, _admin(pg_store), today=TODAY)
    assert errors.get("oficios") is None
    assert any(i.source_module == "oficios" for i in items)
