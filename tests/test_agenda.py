from datetime import date
import pytest
from streamlit.testing.v1 import AppTest
from database.agenda import AgendaStore
from database.store import ROOT, Store
from services.agenda import institutional, conflicts
from tests.test_postgresql import pg_url, pg_store


def draft(kind="REUNIAO", members=None, day="2026-09-07"):
    return dict(
        tipo=kind,
        procuradores=members or [2],
        inicio=day + "T10:00:00",
        fim=day + "T11:00:00",
        situacao="Agendado",
        sem_hora=False,
        titulo="Assunto",
        categoria="Curso",
        reuniao_com="Conselheiro",
        processo=" TC nº 001/26 ",
        local="Gabinete",
        observacoes="Teste",
    )


@pytest.mark.parametrize(
    "member,session_day,activity",
    [
        (3, 8, "Sessão da 2ª Câmara"),
        (1, 9, "Sessão do Tribunal Pleno"),
        (2, 10, "Sessão da 1ª Câmara"),
    ],
)
@pytest.mark.parametrize("day", range(7, 14))
@pytest.mark.parametrize("kind", ["REUNIAO", "DESPACHO", "EVENTO"])
def test_institutional_rules(member, session_day, activity, day, kind):
    alerts = institutional(
        [member], kind, date(2026, 9, day), {"bradson": 3, "isabella": 2, "elvira": 1}
    )
    expected = day == session_day and kind != "EVENTO"
    assert bool(alerts) == expected
    if expected:
        assert alerts[0]["activity"] == activity
        assert alerts[0]["confirmation"] is True


def exercise_crud(store):
    agenda = AgendaStore(store)
    for kind in ("EVENTO", "REUNIAO", "DESPACHO"):
        record = draft(kind)
        identifier = agenda.save(record)
        found = agenda.list("2026-09-07", "2026-09-08")
        assert found[0]["id"] == identifier
        if kind == "DESPACHO":
            assert found[0]["processo"] == record["processo"]
        with pytest.raises(ValueError, match="conflito"):
            agenda.save(record)
        second = agenda.save(record, conflict_confirmed=True)
        agenda.cancel(second)
        assert not conflicts(found[0], agenda.list("2026-09-07", "2026-09-08"))
        updated = {**found[0], "titulo": "Alterado"}
        agenda.save(updated)
        with pytest.raises(ValueError):
            agenda.delete(second)
        agenda.delete(second, confirmed=True)
        agenda.delete(identifier, confirmed=True)
    identifier = agenda.save(draft(members=[1, 2]))
    assert agenda.list(
        "2026-09-07", "2026-09-08", member=1, kind="REUNIAO", status="Agendado"
    )[0]["procuradores"] == [1, 2]
    assert not agenda.list("2026-09-07", "2026-09-08", member=3)
    assert not agenda.list("2026-09-07", "2026-09-08", kind="EVENTO")
    assert not agenda.list("2026-09-08", "2026-09-09")
    assert AgendaStore(store).list("2026-09-07", "2026-09-08")[0]["id"] == identifier


def test_sqlite_crud(store):
    exercise_crud(store)
    assert AgendaStore(Store(store.path)).list("2026-09-07", "2026-09-08")


def test_postgres_crud(pg_store):
    exercise_crud(pg_store)


@pytest.mark.parametrize(
    "change",
    [
        dict(procuradores=[3], inicio="2026-09-08T10:00:00", fim="2026-09-08T11:00:00"),
        dict(procuradores=[1], inicio="2026-09-09T10:00:00", fim="2026-09-09T11:00:00"),
        dict(tipo="DESPACHO", inicio="2026-09-10T10:00:00", fim="2026-09-10T11:00:00"),
    ],
)
def test_edit_requires_fresh_confirmation(store, change):
    agenda = AgendaStore(store)
    record = draft()
    record["id"] = agenda.save(record)
    record.update(change)
    with pytest.raises(ValueError, match="institucional"):
        agenda.save(record)
    agenda.save(record, institutional_confirmed=True)
    with pytest.raises(ValueError, match="institucional"):
        agenda.save(record)


def test_two_independent_confirmations(store):
    agenda = AgendaStore(store)
    record = draft(members=[3], day="2026-09-08")
    agenda.save(record, institutional_confirmed=True)
    with pytest.raises(ValueError, match="institucional"):
        agenda.save(record, conflict_confirmed=True)
    with pytest.raises(ValueError, match="conflito"):
        agenda.save(record, institutional_confirmed=True)
    agenda.save(record, institutional_confirmed=True, conflict_confirmed=True)


def test_all_day_and_boundaries(store):
    agenda = AgendaStore(store)
    event = draft("EVENTO")
    event.update(inicio="2026-09-07T00:00:00", fim="2026-09-09T00:00:00", sem_hora=True)
    agenda.save(event)
    assert agenda.list("2026-09-08", "2026-09-09")
    with pytest.raises(ValueError, match="conflito"):
        agenda.save(draft(members=[2], day="2026-09-09"))
    agenda.save(draft(members=[3]))
    timed = draft(members=[3])
    timed.update(inicio="2026-09-07T11:00:00", fim=None)
    agenda.save(timed)


def test_additive_initialization_and_stable_bindings(store):
    before = store.settings(), store.next_number(2026), store.catalog("procuradores")
    agenda = AgendaStore(store)
    with store.connection() as c:
        c.execute("UPDATE procuradores SET nome='Nome atualizado' WHERE id=2")
    assert AgendaStore(store).bindings["isabella"] == 2
    assert store.next_number(2026) == before[1]
    assert all(store.settings()[k] == v for k, v in before[0].items())
    assert len(store.catalog("procuradores")) == len(before[2])


def test_agenda_ui_navigation_and_save(store, monkeypatch):
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_agenda").click().run()
    assert not app.exception
    next(b for b in app.button if b.label == "+ Novo compromisso").click().run()
    next(s for s in app.multiselect if s.label == "Membros do MPC-PB").set_value(
        [2]
    ).run()
    next(t for t in app.text_input if t.label == "Nome do evento *").set_value(
        "Evento teste"
    ).run()
    next(b for b in app.button if b.label == "Salvar compromisso").click().run()
    assert not app.exception and not app.error
    assert any("sucesso" in s.value for s in app.success)
    app.sidebar.radio(key="portal_module").set_value("Portarias").run()
    assert not app.exception


@pytest.mark.parametrize(
    "scenario",
    [
        test_two_independent_confirmations,
        test_all_day_and_boundaries,
        test_additive_initialization_and_stable_bindings,
    ],
)
def test_postgres_availability(pg_store, scenario):
    scenario(pg_store)


@pytest.mark.parametrize(
    "changes",
    [
        dict(procuradores=[]),
        dict(tipo="DESPACHO", procuradores=[1, 2]),
        dict(fim="2026-09-07T09:00:00"),
        dict(titulo=" "),
        dict(local=""),
        dict(sem_hora=True),
    ],
)
def test_invalid_records(store, changes):
    agenda = AgendaStore(store)
    with pytest.raises(ValueError):
        agenda.save({**draft(), **changes})
    assert not agenda.list("2026-09-01", "2026-10-01")


def test_ui_resets_availability_confirmation(store, monkeypatch):
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_agenda").click().run()
    next(b for b in app.button if b.label == "+ Novo compromisso").click().run()
    next(s for s in app.selectbox if s.label == "Tipo de compromisso").set_value(
        "REUNIAO"
    ).run()
    app.multiselect[0].set_value([2]).run()
    next(d for d in app.date_input if d.label == "Data").set_value(
        date(2026, 9, 17)
    ).run()
    save = lambda: next(b for b in app.button if b.label == "Salvar compromisso")
    assert save().disabled
    checkbox = lambda: next(
        c for c in app.checkbox if c.label.startswith("Confirmo que verifiquei")
    )
    checkbox().check().run()
    assert not save().disabled
    next(d for d in app.date_input if d.label == "Data").set_value(
        date(2026, 9, 8)
    ).run()
    assert not save().disabled
    assert not any(c.label.startswith("Confirmo que verifiquei") for c in app.checkbox)
    next(d for d in app.date_input if d.label == "Data").set_value(
        date(2026, 9, 17)
    ).run()
    assert save().disabled and not checkbox().value
    next(s for s in app.selectbox if s.label == "Tipo de compromisso").set_value(
        "EVENTO"
    ).run()
    assert not any("ATENÇÃO À DISPONIBILIDADE" in w.value for w in app.warning)
    assert not app.exception


@pytest.mark.parametrize("backend_fixture", ["store", "pg_store"])
@pytest.mark.parametrize(
    "member,session_day", [(3, "2026-09-08"), (1, "2026-09-09"), (2, "2026-09-10")]
)
@pytest.mark.parametrize("kind", ["REUNIAO", "DESPACHO", "EVENTO"])
def test_save_and_edit_session_confirmation(
    request, backend_fixture, member, session_day, kind
):
    agenda = AgendaStore(request.getfixturevalue(backend_fixture))
    record = draft(kind, members=[member], day=session_day)
    if kind != "EVENTO":
        with pytest.raises(ValueError, match="institucional"):
            agenda.save(record)
    record["id"] = agenda.save(record, institutional_confirmed=kind != "EVENTO")
    # Leave the session day: no institutional acknowledgement is required.
    record.update(inicio="2026-09-07T10:00:00", fim="2026-09-07T11:00:00")
    agenda.save(record)
    # Editing back onto the session day must require new acknowledgement.
    record.update(inicio=session_day + "T10:00:00", fim=session_day + "T11:00:00")
    if kind != "EVENTO":
        with pytest.raises(ValueError, match="institucional"):
            agenda.save(record)
    agenda.save(record, institutional_confirmed=kind != "EVENTO")


def test_existing_bindings_gain_bradson_without_changing_members(store):
    with store.connection() as c:
        c.execute("INSERT INTO configuracoes VALUES('agenda_member_isabella','2')")
        c.execute("INSERT INTO configuracoes VALUES('agenda_member_elvira','1')")
    before = store.catalog("procuradores")
    agenda = AgendaStore(store)
    assert agenda.bindings == {"isabella": 2, "elvira": 1, "bradson": 3}
    assert store.catalog("procuradores") == before
    assert not institutional([2], "REUNIAO", date(2026, 9, 8), agenda.bindings)


@pytest.mark.parametrize("view", ["Hoje", "Semana", "Mês", "Lista", "Próximos"])
@pytest.mark.parametrize("with_record", [False, True])
def test_views_only_show_registered_appointments(store, monkeypatch, view, with_record):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    monkeypatch.setattr("database.store.Store", lambda: store)
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    if with_record:
        record = draft("EVENTO", members=[1, 2, 3], day=today.isoformat())
        record["titulo"] = "Compromisso cadastrado pelo usuário"
        AgendaStore(store).save(record)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_agenda").click().run()
    app.radio(key="agenda_view").set_value(view).run()
    assert not app.exception and not app.error
    displayed = "\n".join(
        str(element.value)
        for category in (
            app.caption,
            app.markdown,
            app.info,
            app.warning,
            app.subheader,
        )
        for element in category
    )
    for text in (
        "Sessão da 2ª Câmara",
        "Sessão do Tribunal Pleno",
        "Sessão da 1ª Câmara",
        "disponibilidade condicionada",
        "sem horário fixo",
    ):
        assert text not in displayed
    if with_record:
        assert "Compromisso cadastrado pelo usuário" in displayed
        assert len(app.expander) == 2  # Sidebar tools and the actual appointment.
    else:
        assert "Nenhum compromisso no período selecionado." in displayed
        assert not any(b.label == "Editar" for b in app.button)
