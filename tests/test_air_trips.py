from datetime import date, timedelta

import pytest

from database.agenda import AgendaStore
from services.agenda_ui import driver_message
from services.alerts import ALTO, ATENCAO, trip_alerts


def leave(agenda, start, end=None):
    return agenda.save_leave(
        {
            "procurador_id": 1,
            "motivo": "Férias",
            "data_inicio": start.isoformat(),
            "data_fim": (end or start).isoformat(),
        },
        created_by="tester@example.org",
    )


def trip(**changes):
    value = {
        "aeroporto": "João Pessoa",
        "aeroporto_outro": None,
        "ida_data": None,
        "ida_hora": None,
        "ida_companhia": None,
        "ida_voo": None,
        "ida_motorista_hora": None,
        "volta_data": None,
        "volta_chegada_hora": None,
        "volta_companhia": None,
        "volta_voo": None,
        "volta_motorista_hora": None,
        "motorista_informado": False,
        "observacao": "restrita",
    }
    value.update(changes)
    return value


def test_trip_crud_and_driver_metadata(store):
    agenda = AgendaStore(store)
    identifier = leave(agenda, date.today() + timedelta(days=10))
    assert agenda.get_trip(identifier) is None
    agenda.upsert_trip(identifier, trip(ida_data="2030-02-01", ida_hora="08:10"))
    saved = agenda.get_trip(identifier)
    assert saved["ida_data"] == "2030-02-01"
    assert saved["informado_em"] is None
    agenda.upsert_trip(identifier, trip(aeroporto="Recife", motorista_informado=True), informed_by="user@example.org")
    saved = agenda.get_trip(identifier)
    assert saved["aeroporto"] == "Recife"
    assert saved["informado_em"] and saved["informado_por"] == "user@example.org"
    first_stamp = saved["informado_em"]
    agenda.upsert_trip(identifier, trip(aeroporto="Outro", aeroporto_outro="Natal", motorista_informado=True), informed_by="other@example.org")
    assert agenda.get_trip(identifier)["informado_em"] == first_stamp
    assert agenda.get_trip(identifier)["informado_por"] == "user@example.org"
    agenda.delete_trip(identifier)
    assert agenda.get_trip(identifier) is None


def test_trip_allows_one_way_and_rejects_invalid_return(store):
    agenda = AgendaStore(store)
    identifier = leave(agenda, date.today() + timedelta(days=10))
    agenda.upsert_trip(identifier, trip(ida_data="2030-02-01"))
    assert agenda.get_trip(identifier)["volta_data"] is None
    with pytest.raises(ValueError, match="volta"):
        agenda.upsert_trip(identifier, trip(ida_data="2030-02-02", volta_data="2030-02-01"))
    with pytest.raises(ValueError, match="outro aeroporto"):
        agenda.upsert_trip(identifier, trip(aeroporto="Outro"))


def test_trip_alerts_are_selective_prioritized_and_skip_closed_leaves(store):
    agenda = AgendaStore(store)
    tomorrow = date.today() + timedelta(days=1)
    identifier = leave(agenda, date.today(), tomorrow + timedelta(days=2))
    agenda.upsert_trip(identifier, trip(ida_data=tomorrow.isoformat(), volta_data=tomorrow.isoformat()))
    alerts = trip_alerts(store, tomorrow)
    assert {item.category for item in alerts} == {"viagem_ida", "viagem_volta"}
    assert all(item.severity == ALTO for item in alerts)
    assert all(item.metadata["afastamento_id"] == identifier for item in alerts)
    agenda.upsert_trip(identifier, trip(ida_data=tomorrow.isoformat(), motorista_informado=True), informed_by="tester")
    assert trip_alerts(store, tomorrow)[0].severity == ATENCAO
    agenda.cancel_leave(identifier)
    assert trip_alerts(store, tomorrow) == []

    ended = leave(agenda, tomorrow - timedelta(days=3), tomorrow - timedelta(days=2))
    agenda.upsert_trip(ended, trip(volta_data=tomorrow.isoformat()))
    assert trip_alerts(store, tomorrow) == []


def test_trip_message_omits_unfilled_values():
    text = driver_message("Fulano", trip(aeroporto="Recife", ida_data="2030-02-01"))
    assert "01/02" in text
    assert "None" not in text and "--:--" not in text
    assert "retorno" not in text


def test_trip_schema_is_idempotent_and_batch_lookup(store):
    agenda = AgendaStore(store)
    agenda.initialize()
    identifier = leave(agenda, date.today() + timedelta(days=2))
    agenda.upsert_trip(identifier, trip(aeroporto="Outro", aeroporto_outro="Campina Grande"))
    assert agenda.trips_for_leaves([identifier, "inexistente"])[identifier]["aeroporto_outro"] == "Campina Grande"
