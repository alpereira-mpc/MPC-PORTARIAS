from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from streamlit.testing.v1 import AppTest

from database.agenda import AgendaStore
from database.store import ROOT
from services.afastamentos import status as leave_status
from services.agenda import fase_compromisso, fase_temporal
from services.agenda_ui import apresentacao_temporal


def _event(inicio, fim, *, sem_hora=False, situacao="Confirmado"):
    return {
        "tipo": "EVENTO",
        "inicio": inicio,
        "fim": fim,
        "sem_hora": sem_hora,
        "situacao": situacao,
    }


def test_all_day_and_timed_periods_follow_the_inclusive_end():
    agora = datetime(2026, 9, 28, 15, 0)

    assert (
        fase_compromisso(
            _event("2026-09-29T00:00:00", "2026-09-29T00:00:00", sem_hora=True),
            agora,
        )
        == "futuro"
    )
    assert (
        fase_compromisso(
            _event("2026-09-27T00:00:00", "2026-09-29T00:00:00", sem_hora=True),
            agora,
        )
        == "em_andamento"
    )
    assert (
        fase_compromisso(
            _event("2026-09-28T00:00:00", "2026-09-28T00:00:00", sem_hora=True),
            agora,
        )
        == "em_andamento"
    )
    assert (
        fase_compromisso(
            _event("2026-09-26T00:00:00", "2026-10-12T00:00:00", sem_hora=True),
            datetime(2026, 10, 12, 23, 59),
        )
        == "em_andamento"
    )
    assert (
        fase_compromisso(
            _event("2026-09-26T00:00:00", "2026-10-12T00:00:00", sem_hora=True),
            datetime(2026, 10, 13, 0, 0),
        )
        == "passado"
    )
    assert (
        fase_compromisso(
            _event("2026-09-27T09:00:00", "2026-09-27T10:00:00"),
            agora,
        )
        == "passado"
    )

    inicio = datetime(2026, 9, 28, 9, 0)
    fim = datetime(2026, 9, 28, 17, 0)
    assert fase_temporal(inicio, fim, datetime(2026, 9, 28, 8, 59)) == "futuro"
    assert fase_temporal(inicio, fim, datetime(2026, 9, 28, 9, 0)) == "em_andamento"
    assert fase_temporal(inicio, fim, datetime(2026, 9, 28, 17, 0)) == "em_andamento"
    assert fase_temporal(inicio, fim, datetime(2026, 9, 28, 17, 0, 1)) == "passado"


def test_open_appointment_without_an_end_stays_in_progress():
    row = _event("2026-09-28T10:00:00", None)
    assert fase_compromisso(row, datetime(2026, 9, 28, 9, 30)) == "futuro"
    assert fase_compromisso(row, datetime(2026, 9, 28, 10, 0)) == "em_andamento"
    assert fase_compromisso(row, datetime(2026, 9, 28, 10, 1)) == "em_andamento"
    assert fase_compromisso(row, datetime(2026, 9, 28, 15, 0)) == "em_andamento"
    assert fase_compromisso(row, datetime(2026, 9, 29, 8, 0)) == "em_andamento"

    marcas, passado = apresentacao_temporal(row, datetime(2026, 9, 29, 8, 0))
    assert [item[0] for item in marcas] == ["Evento", "Confirmado", "EM ANDAMENTO"]
    assert passado is False

    row["situacao"] = "Realizado"
    marcas, passado = apresentacao_temporal(row, datetime(2026, 9, 29, 8, 0))
    assert "EM ANDAMENTO" not in [item[0] for item in marcas]
    assert passado is False

    row["situacao"] = "Cancelado"
    marcas, passado = apresentacao_temporal(row, datetime(2026, 9, 29, 8, 0))
    assert "EM ANDAMENTO" not in [item[0] for item in marcas]
    assert passado is False

    dia_inteiro = _event("2026-09-28T00:00:00", None, sem_hora=True)
    assert fase_compromisso(dia_inteiro, datetime(2026, 9, 27, 23, 0)) == "futuro"
    assert fase_compromisso(dia_inteiro, datetime(2026, 9, 28, 0, 1)) == "em_andamento"
    assert fase_compromisso(dia_inteiro, datetime(2026, 9, 30, 18, 0)) == "em_andamento"


def test_cancelled_and_finished_appointments_do_not_show_in_progress():
    row = _event(
        "2026-09-26T00:00:00",
        "2026-10-12T00:00:00",
        sem_hora=True,
        situacao="Cancelado",
    )
    marcas, passado = apresentacao_temporal(row, datetime(2026, 9, 28, 12, 0))
    assert "EM ANDAMENTO" not in [item[0] for item in marcas]
    assert passado is False
    row["situacao"] = "Realizado"
    marcas, passado = apresentacao_temporal(row, datetime(2026, 9, 28, 12, 0))
    assert "EM ANDAMENTO" not in [item[0] for item in marcas]
    assert passado is False


def test_open_appointment_shows_in_progress_badge_or_the_past_alert():
    vigente = _event("2026-09-26T00:00:00", "2026-10-12T00:00:00", sem_hora=True)
    marcas, passado = apresentacao_temporal(vigente, datetime(2026, 9, 28, 12, 0))
    assert [item[0] for item in marcas] == ["Evento", "Confirmado", "EM ANDAMENTO"]
    assert passado is False

    encerrado = _event("2026-09-27T09:00:00", "2026-09-27T10:00:00")
    marcas, passado = apresentacao_temporal(encerrado, datetime(2026, 9, 28, 12, 0))
    assert "EM ANDAMENTO" not in [item[0] for item in marcas]
    assert passado is True


def test_leaves_use_the_same_inclusive_day_rule():
    def leave(start, end):
        return {"data_inicio": start, "data_fim": end, "cancelado": 0}

    assert leave_status(
        leave("2026-09-29", "2026-09-30"), datetime(2026, 9, 28).date()
    ) == ("AGENDADO")
    assert leave_status(
        leave("2026-09-27", "2026-09-29"), datetime(2026, 9, 28).date()
    ) == ("EM ANDAMENTO")
    assert leave_status(
        leave("2026-09-20", "2026-09-28"), datetime(2026, 9, 28).date()
    ) == ("EM ANDAMENTO")
    assert leave_status(
        leave("2026-09-20", "2026-09-27"), datetime(2026, 9, 28).date()
    ) == ("ENCERRADO")
    assert leave_status(
        leave("2026-09-29", "2026-10-02"), datetime(2026, 9, 28).date()
    ) == ("AGENDADO")
    cancelled = leave("2026-09-27", "2026-09-29")
    cancelled["cancelado"] = 1
    assert leave_status(cancelled, datetime(2026, 9, 28).date()) == "CANCELADO"


def test_multi_day_periods_cross_month_and_year_boundaries():
    assert (
        fase_temporal(
            datetime(2026, 12, 28),
            datetime(2027, 1, 3),
            datetime(2026, 12, 31, 18, 0),
            dia_inteiro=True,
        )
        == "em_andamento"
    )
    assert (
        fase_temporal(
            datetime(2026, 12, 28),
            datetime(2027, 1, 3),
            datetime(2027, 1, 1, 0, 1),
            dia_inteiro=True,
        )
        == "em_andamento"
    )
    assert (
        fase_temporal(
            datetime(2026, 12, 28),
            datetime(2027, 1, 3),
            datetime(2027, 1, 3, 23, 0),
            dia_inteiro=True,
        )
        == "em_andamento"
    )
    assert (
        fase_temporal(
            datetime(2026, 12, 28),
            datetime(2027, 1, 3),
            datetime(2027, 1, 4, 0, 0),
            dia_inteiro=True,
        )
        == "passado"
    )
    assert (
        leave_status(
            {"data_inicio": "2026-12-30", "data_fim": "2027-01-02", "cancelado": 0},
            datetime(2027, 1, 2).date(),
        )
        == "EM ANDAMENTO"
    )
    assert (
        leave_status(
            {"data_inicio": "2026-12-30", "data_fim": "2027-01-02", "cancelado": 0},
            datetime(2027, 1, 3).date(),
        )
        == "ENCERRADO"
    )


def _card(app, text):
    for item in app.markdown:
        if text in str(item.value):
            return str(item.value)
    return ""


def test_listing_shows_in_progress_without_the_past_warning(store, monkeypatch):
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    agenda = AgendaStore(store)
    agenda.save(
        {
            "tipo": "EVENTO",
            "procuradores": [1],
            "inicio": (today - timedelta(days=2)).isoformat() + "T00:00:00",
            "fim": (today + timedelta(days=14)).isoformat() + "T00:00:00",
            "sem_hora": True,
            "situacao": "Confirmado",
            "titulo": "Evento vigente",
            "categoria": "Curso",
            "local": "TCE-PB",
        }
    )
    agenda.save(
        {
            "tipo": "EVENTO",
            "procuradores": [2],
            "inicio": (today - timedelta(days=1)).isoformat() + "T09:00:00",
            "fim": (today - timedelta(days=1)).isoformat() + "T10:00:00",
            "sem_hora": False,
            "situacao": "Confirmado",
            "titulo": "Evento encerrado",
            "categoria": "Curso",
            "local": "TCE-PB",
        }
    )
    agenda.save_leave(
        {
            "procurador_id": 4,
            "motivo": "Férias",
            "data_inicio": (today - timedelta(days=1)).isoformat(),
            "data_fim": (today + timedelta(days=1)).isoformat(),
        }
    )
    agenda.save_leave(
        {
            "procurador_id": 4,
            "motivo": "Licença especial",
            "data_inicio": (today + timedelta(days=3)).isoformat(),
            "data_fim": (today + timedelta(days=4)).isoformat(),
        }
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    app.button(key="open_agenda").click().run()
    vigente = _card(app, "Evento vigente")
    assert "EM ANDAMENTO" in vigente
    assert "Confirmado" in vigente
    assert "mpc-badge" in vigente
    assert not any(
        "passado ainda não encerrado" in str(item.value) for item in app.warning
    )
    afastamento = _card(app, "Férias")
    assert "EM ANDAMENTO" in afastamento
    assert "mpc-badge" in afastamento

    anchor = today if today.day > 1 else today - timedelta(days=1)
    app.radio(key="agenda_view").set_value("Mês").run()
    app.date_input(key="agenda_anchor").set_value(anchor).run()
    encerrado = _card(app, "Evento encerrado")
    assert "EM ANDAMENTO" not in encerrado
    assert any("passado ainda não encerrado" in str(item.value) for item in app.warning)

    app.radio(key="agenda_view").set_value("Próximos").run()
    futuro = _card(app, "Licença especial")
    assert "AGENDADO" in futuro
    assert "EM ANDAMENTO" not in futuro
