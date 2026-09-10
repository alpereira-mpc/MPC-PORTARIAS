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


def test_upcoming_ui_pagination_and_reference_date(store, monkeypatch):
    import services.agenda_ui as ui

    monkeypatch.setattr(ui, "datetime", FixedDatetime)
    monkeypatch.setattr("database.store.Store", lambda: store)
    agenda = AgendaStore(store)
    for index in range(32):
        record = draft("EVENTO", members=[3] if index == 31 else [2])
        start = datetime(2026, 9, 10) + timedelta(hours=index)
        record.update(
            titulo=f"Futuro {index:02}",
            inicio=start.isoformat(),
            fim=(start + timedelta(minutes=30)).isoformat(),
        )
        agenda.save(record)
    agenda.save(draft("EVENTO", day="2026-09-09"))
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_agenda").click().run()
    app.radio(key="agenda_view").set_value("Semana").run()
    app.date_input(key="agenda_anchor").set_value(datetime(2020, 1, 1).date()).run()
    app.radio(key="agenda_view").set_value("Próximos").run()
    assert not app.exception and not app.date_input
    assert any("Futuro 00" in m.value for m in app.markdown)
    assert not any("Futuro 30" in m.value for m in app.markdown)
    assert any(c.value == "Exibindo 1–30" for c in app.caption)
    assert app.button(key="agenda_upcoming_previous").disabled
    app.button(key="agenda_upcoming_next").click().run()
    assert any(c.value == "Exibindo 31–32" for c in app.caption)
    assert app.button(key="agenda_upcoming_next").disabled
    app.button(key="agenda_upcoming_previous").click().run()
    assert any(c.value == "Exibindo 1–30" for c in app.caption)
    app.button(key="agenda_upcoming_next").click().run()
    app.selectbox(key="agenda_filter_member").set_value(3).run()
    assert any(c.value == "Exibindo 1–1" for c in app.caption)
    assert any("Futuro 31" in m.value for m in app.markdown)
    assert not any("Sessão" in c.value for c in app.caption)
    assert not app.exception and not app.error
