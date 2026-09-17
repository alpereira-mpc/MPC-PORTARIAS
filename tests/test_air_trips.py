from datetime import date, timedelta

import pytest

from database.agenda import AgendaStore
from services.agenda_ui import driver_message
from services.alerts import ALTO, ATENCAO, trip_alerts


def commitment(agenda, members=(1,), day=None):
    day = day or date.today() + timedelta(days=10)
    return agenda.save({"tipo": "EVENTO", "procuradores": list(members), "inicio": day.isoformat() + "T09:00:00", "fim": None, "situacao": "Agendado", "titulo": "Encontro Nacional", "categoria": "Institucional", "local": "João Pessoa", "sem_hora": False}, institutional_confirmed=True, conflict_confirmed=True)


def trip(**changes):
    value = {"aeroporto_ida": "João Pessoa", "aeroporto_ida_outro": None, "ida_data": None, "ida_hora": None, "ida_motorista_hora": None, "aeroporto_volta": None, "aeroporto_volta_outro": None, "volta_data": None, "volta_chegada_hora": None, "volta_motorista_hora": None, "motorista_informado": False, "observacao": "restrita"}
    value.update(changes)
    return value


def test_commitment_trip_crud_per_member_and_driver_metadata(store):
    agenda = AgendaStore(store); identifier = commitment(agenda, (1, 2))
    agenda.upsert_commitment_trip(identifier, 1, trip(ida_data="2030-02-01", ida_hora="08:10"))
    agenda.upsert_commitment_trip(identifier, 2, trip(aeroporto_ida="Recife", ida_data="2030-02-02", motorista_informado=True), informed_by="user@example.org")
    saved = agenda.trips_for_commitments([identifier])[identifier]
    assert saved[1]["aeroporto_ida"] == "João Pessoa" and saved[2]["aeroporto_ida"] == "Recife"
    assert saved[2]["informado_em"] and saved[2]["informado_por"] == "user@example.org"
    agenda.delete_commitment_trip(identifier, 1)
    assert set(agenda.trips_for_commitments([identifier])[identifier]) == {2}


@pytest.mark.parametrize("hour", ["05:47", "11:15", "13:31", "23:58", "00:00"])
def test_trip_accepts_free_hhmm_hours_and_return_driver_is_independent(store, hour):
    agenda = AgendaStore(store); identifier = commitment(agenda)
    agenda.upsert_commitment_trip(identifier, 1, trip(ida_data="2030-02-01", ida_hora=hour, ida_motorista_hora="09:00", volta_data="2030-02-02", volta_chegada_hora="13:30", volta_motorista_hora="14:00", aeroporto_volta="Recife"))
    saved = agenda.get_commitment_trip(identifier, 1)
    assert saved["ida_hora"] == hour and saved["aeroporto_ida"] == "João Pessoa"
    assert saved["aeroporto_volta"] == "Recife" and saved["volta_motorista_hora"] == "14:00"


@pytest.mark.parametrize("hour", ["25:00", "13:72", "9:5", "abc"])
def test_trip_rejects_invalid_hour(store, hour):
    agenda = AgendaStore(store); identifier = commitment(agenda)
    with pytest.raises(ValueError, match="HH:MM"):
        agenda.upsert_commitment_trip(identifier, 1, trip(ida_data="2030-02-01", ida_hora=hour))


def test_member_with_trip_cannot_be_removed_without_explicit_treatment(store):
    agenda = AgendaStore(store); identifier = commitment(agenda, (1, 2)); agenda.upsert_commitment_trip(identifier, 2, trip(ida_data="2030-02-01"))
    record = agenda.get(identifier); record["procuradores"] = [1]
    with pytest.raises(ValueError, match="logística de viagem"):
        agenda.save(record, institutional_confirmed=True, conflict_confirmed=True)
    agenda.delete_commitment_trip(identifier, 2); agenda.save(record, institutional_confirmed=True, conflict_confirmed=True)
    assert agenda.get(identifier)["procuradores"] == [1]


def test_trip_alerts_target_commitment_and_skip_closed(store):
    agenda = AgendaStore(store); tomorrow = date.today() + timedelta(days=1); identifier = commitment(agenda, day=date.today())
    agenda.upsert_commitment_trip(identifier, 1, trip(ida_data=tomorrow.isoformat(), volta_data=tomorrow.isoformat()))
    alerts = trip_alerts(store, tomorrow)
    assert {item.category for item in alerts} == {"viagem_ida", "viagem_volta"}
    assert all(item.severity == ALTO and item.metadata["compromisso_id"] == identifier for item in alerts)
    agenda.upsert_commitment_trip(identifier, 1, trip(ida_data=tomorrow.isoformat(), motorista_informado=True), informed_by="tester")
    assert trip_alerts(store, tomorrow)[0].severity == ATENCAO
    agenda.cancel(identifier); assert trip_alerts(store, tomorrow) == []


def test_driver_message_uses_commitment_and_omits_empty_values():
    text = driver_message("Fulano", trip(aeroporto_ida="Recife", ida_data="2030-02-01", ida_hora="11:15"), "Encontro Nacional")
    assert "Encontro Nacional" in text and "01/02" in text and "None" not in text and "--:--" not in text


def test_old_leave_trip_table_still_initializes(store):
    agenda = AgendaStore(store); agenda.initialize()
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agenda_afastamentos_viagens'").fetchone()
        assert c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agenda_compromissos_viagens'").fetchone()
