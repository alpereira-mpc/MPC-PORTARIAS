from datetime import date

import pytest
from streamlit.testing.v1 import AppTest

from database.agenda import AgendaStore
from database.store import ROOT
from services.agenda_ui import display_datetime
from tests.test_agenda import draft
from tests.test_agenda_upcoming import FixedDatetime


def test_date_presentation():
    assert display_datetime("2026-09-30T14:30:00") == "30/09/2026 às 14:30"
    assert display_datetime("2026-10-03T00:00:00", True) == "03/10/2026"


@pytest.mark.parametrize("kind", ["EVENTO", "REUNIAO", "DESPACHO"])
def test_date_inputs_and_edit_preserve_iso(store, monkeypatch, kind):
    monkeypatch.setattr("database.store.Store", lambda: store)
    agenda = AgendaStore(store)
    record = draft(kind, members=[3], day="2026-09-30")
    identifier = agenda.save(record)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_agenda").click().run()
    app.session_state["agenda_edit"] = agenda.list("2026-09-30", "2026-10-01")[0]
    app.run()
    assert all(widget.proto.format == "DD/MM/YYYY" for widget in app.date_input)
    app.date_input[0].set_value(date(2026, 10, 3)).run()
    if kind == "EVENTO":
        app.date_input[1].set_value(date(2026, 10, 3)).run()
    next(b for b in app.button if b.label == "Salvar compromisso").click().run()
    assert not app.exception and not app.error
    saved = agenda.list("2026-10-03", "2026-10-04")[0]
    assert saved["id"] == identifier
    assert saved["inicio"] == "2026-10-03T10:00:00"
    assert saved["fim"] == "2026-10-03T11:00:00"
    next(b for b in app.button if b.label == "+ Novo compromisso").click().run()
    next(s for s in app.selectbox if s.label == "Tipo de compromisso").set_value(
        kind
    ).run()
    assert all(widget.proto.format == "DD/MM/YYYY" for widget in app.date_input)


@pytest.mark.parametrize("view", ["Hoje", "Semana", "Mês", "Lista", "Próximos"])
def test_view_dates_and_details(store, monkeypatch, view):
    import services.agenda_ui as ui

    monkeypatch.setattr(ui, "datetime", FixedDatetime)
    monkeypatch.setattr("database.store.Store", lambda: store)
    agenda = AgendaStore(store)
    agenda.save(draft("EVENTO", members=[3], day="2026-09-10"))
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_agenda").click().run()
    app.radio(key="agenda_view").set_value(view).run()
    assert all(widget.proto.format == "DD/MM/YYYY" for widget in app.date_input)
    assert any(h.value == "10/09/2026" for h in app.subheader)
    text = "\n".join(m.value for m in app.markdown)
    assert "10/09/2026 às 10:00" in text
    assert "10/09/2026 às 11:00" in text
    assert "2026-09-10" not in text and "2026/09/10" not in text
    assert not app.exception


def test_conflict_date_display(store, monkeypatch):
    monkeypatch.setattr("database.store.Store", lambda: store)
    agenda = AgendaStore(store)
    record = draft("REUNIAO", members=[3], day="2026-09-30")
    agenda.save(record)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_agenda").click().run()
    # Unsaved copy must conflict with the existing appointment.
    app.session_state["agenda_edit"] = record
    app.run()
    assert any("CONFLITO DE HORÁRIO" in w.value for w in app.warning)
    assert any("30/09/2026 às 10:00" in m.value for m in app.markdown)
    assert next(b for b in app.button if b.label == "Salvar compromisso").disabled
    assert not app.exception
