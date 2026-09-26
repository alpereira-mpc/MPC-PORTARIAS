from datetime import datetime, timedelta

import pytest
from streamlit.testing.v1 import AppTest

from database.agenda import AgendaStore
from database.store import ROOT
from tests.test_agenda import draft
from tests.test_postgresql import pg_url, pg_store


@pytest.mark.parametrize("backend_fixture", ["store", "pg_store"])
def test_upcoming_dates_filters_and_pagination(request, backend_fixture):
    agenda = AgendaStore(request.getfixturevalue(backend_fixture))
    past = draft("EVENTO", day="2026-09-09")
    # An event starting before today is excluded even if it finishes tomorrow.
    past["fim"] = "2026-09-11T11:00:00"
    agenda.save(past)
    expected = []
    for index in range(65):
        start = datetime(2026, 9, 10) + timedelta(hours=index)
        record = draft("EVENTO", members=[1, 2] if index % 2 == 0 else [3])
        record.update(
            inicio=start.isoformat(),
            fim=(start + timedelta(minutes=30)).isoformat(),
            titulo=f"Registro {index}",
        )
        record["situacao"] = "Confirmado" if index % 3 == 0 else "Agendado"
        expected.append(agenda.save(record, conflict_confirmed=True))
    distant = draft("DESPACHO", members=[3], day="2099-01-01")
    distant_id = agenda.save(distant, institutional_confirmed=True)
    pages = [agenda.upcoming("2026-09-10", offset=offset) for offset in (0, 30, 60)]
    assert [len(page) for page in pages] == [31, 31, 6]
    assert [r["id"] for page in pages for r in page[:30]] == [*expected, distant_id]
    complete = agenda.active("2026-09-10", None)
    assert [r["id"] for r in complete] == [*expected, distant_id]
    assert len({r["id"] for r in complete}) == len(complete)
    assert pages[0][0]["inicio"] == "2026-09-10T00:00:00"
    assert pages[0][0]["procuradores"] == [1, 2]
    assert {p for page in pages for row in page for p in row["procuradores"]} == {
        1,
        2,
        3,
    }
    assert all(2 in r["procuradores"] for r in agenda.upcoming("2026-09-10", member=2))
    assert agenda.upcoming("2026-09-10", kind="DESPACHO")[0]["id"] == distant_id
    assert all(
        r["situacao"] == "Confirmado"
        for r in agenda.upcoming("2026-09-10", status="Confirmado")
    )
    combined = agenda.upcoming(
        "2026-09-10", member=2, kind="EVENTO", status="Confirmado"
    )
    assert [r["id"] for r in combined] == expected[::6]
    assert not agenda.upcoming("2026-09-10", member=2, kind="DESPACHO")


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 9, 10, 18, 0, tzinfo=tz)


def _captions(app):
    return [caption.value for caption in app.caption]


def _visible(app):
    return "\n".join(str(item.value) for item in (*app.markdown, *app.subheader))


def _assert_continuous(app, summary, present, absent=()):
    captions = _captions(app)
    assert summary in captions
    assert not any(caption.startswith("Página ") for caption in captions)
    assert not any(caption.startswith("Exibindo ") for caption in captions)
    keys = {button.key for button in app.button}
    assert "agenda_upcoming_previous" not in keys
    assert "agenda_upcoming_next" not in keys
    shown = _visible(app)
    for title in present:
        assert title in shown
    for title in absent:
        assert title not in shown
    dates = [item.value for item in app.subheader]
    assert dates == sorted(
        dates, key=lambda value: datetime.strptime(value, "%d/%m/%Y")
    )
    assert len(dates) == len(set(dates))


def test_upcoming_ui_lists_every_record_without_pagination(store, monkeypatch):
    import services.agenda_ui as ui

    monkeypatch.setattr(ui, "datetime", FixedDatetime)
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    agenda = AgendaStore(store)
    for index in range(32):
        record = draft("EVENTO", members=[3] if index == 31 else [2])
        start = datetime(2026, 9, 10, 8, 0) + timedelta(minutes=20 * index)
        record.update(
            titulo=f"Futuro {index:02}",
            inicio=start.isoformat(),
            fim=(start + timedelta(minutes=15)).isoformat(),
        )
        agenda.save(record)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_agenda").click().run()
    titles = [f"Futuro {index:02}" for index in range(32)]
    for view in ("Hoje", "Semana", "Mês", "Próximos"):
        app.radio(key="agenda_view").set_value(view).run()
        assert not app.exception and not app.error
        _assert_continuous(app, "32 compromissos · 0 afastamentos", titles)
    assert not app.date_input
    app.radio(key="agenda_view").set_value("Semana").run()
    assert app.date_input
    app.radio(key="agenda_view").set_value("Próximos").run()
    app.selectbox(key="agenda_filter_member").set_value(3).run()
    _assert_continuous(
        app, "1 compromisso · 0 afastamentos", ("Futuro 31",), ("Futuro 00",)
    )
    app.selectbox(key="agenda_filter_type").set_value("DESPACHO").run()
    assert not any(caption.startswith("Página ") for caption in _captions(app))
    assert "agenda_upcoming_next" not in {button.key for button in app.button}
    assert not any("Futuro" in item.value for item in app.markdown)
    assert not app.exception and not app.error


def test_views_keep_mixed_dates_in_order_without_pagination(store, monkeypatch):
    import services.afastamentos as leaves
    import services.agenda_ui as ui

    monkeypatch.setattr(ui, "datetime", FixedDatetime)
    monkeypatch.setattr(leaves, "datetime", FixedDatetime)
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    agenda = AgendaStore(store)
    for day, title, member in (
        ("2026-09-10", "Evento de hoje", 2),
        ("2026-09-11", "Evento da semana", 2),
        ("2026-09-20", "Evento do mês", 2),
        ("2026-10-02", "Evento futuro", 3),
    ):
        record = draft("EVENTO", members=[member], day=day)
        record["titulo"] = title
        agenda.save(record)
    agenda.save_leave(
        {
            "procurador_id": 2,
            "motivo": "Férias",
            "data_inicio": "2026-09-10",
            "data_fim": "2026-09-11",
        }
    )
    agenda.save_leave(
        {
            "procurador_id": 3,
            "motivo": "Outro",
            "motivo_outro": "Compromisso externo",
            "data_inicio": "2026-09-26",
            "data_fim": "2026-10-12",
        }
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_agenda").click().run()
    expectations = {
        "Hoje": (
            "1 compromisso · 1 afastamento",
            ("Evento de hoje",),
            ("Evento da semana", "Evento do mês", "Evento futuro"),
            ["10/09/2026"],
        ),
        "Semana": (
            "2 compromissos · 1 afastamento",
            ("Evento de hoje", "Evento da semana"),
            ("Evento do mês", "Evento futuro"),
            ["10/09/2026", "11/09/2026"],
        ),
        "Mês": (
            "3 compromissos · 2 afastamentos",
            ("Evento de hoje", "Evento da semana", "Evento do mês"),
            ("Evento futuro",),
            ["10/09/2026", "11/09/2026", "20/09/2026", "26/09/2026"],
        ),
        "Próximos": (
            "4 compromissos · 2 afastamentos",
            ("Evento de hoje", "Evento da semana", "Evento do mês", "Evento futuro"),
            (),
            ["10/09/2026", "11/09/2026", "20/09/2026", "26/09/2026", "02/10/2026"],
        ),
    }
    for view, (summary, present, absent, dates) in expectations.items():
        app.radio(key="agenda_view").set_value(view).run()
        assert not app.exception and not app.error
        _assert_continuous(app, summary, present, absent)
        assert [item.value for item in app.subheader] == dates
    app.selectbox(key="agenda_filter_item_scope").set_value(
        "Somente compromissos"
    ).run()
    _assert_continuous(
        app,
        "4 compromissos · 0 afastamentos",
        ("Evento de hoje", "Evento da semana", "Evento do mês", "Evento futuro"),
    )
    assert sum(button.label == "Editar afastamento" for button in app.button) == 0
    app.selectbox(key="agenda_filter_item_scope").set_value(
        "Somente afastamentos"
    ).run()
    _assert_continuous(
        app, "0 compromissos · 2 afastamentos", (), ("Evento futuro", "Evento de hoje")
    )
    assert sum(button.label == "Editar afastamento" for button in app.button) == 2
    assert sum(button.label == "Editar" for button in app.button) == 0
    app.selectbox(key="agenda_filter_item_scope").set_value("Todos").run()
    app.selectbox(key="agenda_filter_member").set_value(3).run()
    _assert_continuous(
        app,
        "1 compromisso · 1 afastamento",
        ("Evento futuro",),
        ("Evento de hoje", "Evento da semana", "Evento do mês"),
    )
    app.radio(key="agenda_section").set_value("Histórico").run()
    assert not app.exception
    assert app.button(key="agenda_history_previous").disabled
    assert "agenda_upcoming_next" not in {button.key for button in app.button}


class PreviousAgendaStore:
    """Session object from the release before upcoming was introduced."""

    def __init__(self, store):
        self.store = store
        self.bindings = AgendaStore(store).bindings


@pytest.mark.parametrize("backend_fixture", ["store", "pg_store"])
def test_upcoming_refreshes_previous_session_contract(
    request, backend_fixture, monkeypatch
):
    import services.agenda_ui as ui

    store = request.getfixturevalue(backend_fixture)
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    monkeypatch.setattr(ui, "datetime", FixedDatetime)
    agenda = AgendaStore(store)
    agenda.save(draft("EVENTO", day="2026-09-10"))
    ui.read_agenda.clear()
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.session_state["agenda_store"] = PreviousAgendaStore(store)
    app.session_state["agenda_view"] = "Próximos"
    app.button(key="open_agenda").click().run()
    assert not app.exception
    assert type(app.session_state["agenda_store"]) is AgendaStore
    assert app.session_state["agenda_store"].store is store
    assert any("Assunto" in m.value for m in app.markdown)
    current = app.session_state["agenda_store"]
    app.run()
    assert app.session_state["agenda_store"] is current


def test_removed_list_selection_is_migrated(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.session_state["agenda_view"] = "Lista"
    app.button(key="open_agenda").click().run()
    assert not app.exception
    assert app.radio(key="agenda_view").options == [
        "Hoje",
        "Semana",
        "Mês",
        "Próximos",
        "Análise com IA",
    ]
    assert app.radio(key="agenda_view").value == "Hoje"
    assert not any(d.label == "Até" for d in app.date_input)
