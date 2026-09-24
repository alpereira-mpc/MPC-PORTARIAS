from datetime import date, datetime, time, timedelta
from services.audit import INSTITUTIONAL_TZ
import pytest

from database.tarefas import TarefasStore, effective_deadline
from services.access import Principal, has_permission
from services.alerts import can_view_alertas, collect_alerts, task_alerts
from services.tarefas_ui import REMINDER_TIMES, _parse_hour


def _principal(
    identifier,
    admin=False,
    *,
    pode_portarias=False,
    pode_agenda=False,
    pode_oficios=False,
    pode_memorandos=False,
):
    return Principal(
        identifier,
        f"Usuário {identifier}",
        f"u{identifier}@test.local",
        "ADMINISTRADOR" if admin else "USUARIO",
        True,
        pode_portarias,
        pode_agenda,
        pode_oficios,
        admin,
        (),
        pode_memorandos,
    )


def _alerts_principal(identifier):
    """May open Alertas (Ofícios); task rows stay scoped by owner_user_id."""
    return _principal(identifier, pode_oficios=True)


def test_tarefas_are_private_through_every_repository_operation(store):
    repo = TarefasStore(store)
    task = repo.create(1, {"titulo": "Privada", "prioridade": "URGENTE"})
    assert [row["id"] for row in repo.list_active(1)] == [task["id"]]
    assert repo.list_active(2) == []
    assert repo.get(task["id"], 2) is None
    assert repo.update(task["id"], 2, {"titulo": "vazamento"}) is None
    assert repo.change_status(task["id"], 2, "CONCLUIDA") is None
    assert not repo.delete(task["id"], 2)
    assert repo.get(task["id"], 1)["titulo"] == "Privada"
    assert repo.get(task["id"], 99) is None  # administrador não recebe bypass


def test_tarefas_only_does_not_grant_alertas():
    only_tasks = _principal(1)
    assert has_permission(only_tasks, "tarefas")
    assert not has_permission(only_tasks, "alertas")
    assert not can_view_alertas(only_tasks)
    with pytest.raises(ValueError, match="módulo"):
        collect_alerts(None, only_tasks)


def test_status_and_alerts_are_owner_scoped(store):
    repo = TarefasStore(store)
    yesterday = (date.today().fromordinal(date.today().toordinal() - 1)).isoformat()
    task = repo.create(1, {"titulo": "Vencida", "prazo_data": yesterday, "prioridade": "URGENTE"})
    assert repo.change_status(task["id"], 1, "CONCLUIDA")["concluido_em"]
    assert repo.list_active(1) == []
    assert repo.list_history(1)[0]["status"] == "CONCLUIDA"
    assert repo.change_status(task["id"], 1, "A_FAZER")["concluido_em"] is None
    own, _, _ = collect_alerts(store, _alerts_principal(1), now=datetime.now().astimezone())
    other, _, _ = collect_alerts(store, _alerts_principal(2), now=datetime.now().astimezone())
    assert any(alert.source_module == "tarefas" and alert.source_id == str(task["id"]) for alert in own)
    assert not any(alert.source_module == "tarefas" for alert in other)


def test_concluding_a_task_is_idempotent(store):
    repo = TarefasStore(store)
    task = repo.create(1, {"titulo": "Concluir uma vez"})
    with store.connection(read_only=True) as connection:
        before_count = connection.execute("SELECT COUNT(*) FROM tarefas").fetchone()[0]

    first, first_changed = repo.transition_status(task["id"], 1, "CONCLUIDA")
    second, second_changed = repo.transition_status(task["id"], 1, "CONCLUIDA")

    assert first_changed is True
    assert second_changed is False
    assert second == first
    assert first["id"] == task["id"]
    with store.connection(read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM tarefas").fetchone()[0] == before_count
    active_ids = [row["id"] for row in repo.list_active(1)]
    history_ids = [row["id"] for row in repo.list_history(1)]
    assert active_ids == []
    assert history_ids == [task["id"]]
    assert len(active_ids) == len(set(active_ids))
    assert len(history_ids) == len(set(history_ids))


def test_start_and_wait_transitions_are_idempotent(store):
    repo = TarefasStore(store)
    task = repo.create(1, {"titulo": "Aguardar uma vez"})

    started, started_changed = repo.transition_status(
        task["id"], 1, "EM_ANDAMENTO"
    )
    started_again, started_again_changed = repo.transition_status(
        task["id"], 1, "EM_ANDAMENTO"
    )
    waiting, waiting_changed = repo.transition_status(task["id"], 1, "AGUARDANDO")
    waiting_again, waiting_again_changed = repo.transition_status(
        task["id"], 1, "AGUARDANDO"
    )

    assert started_changed is True
    assert started_again_changed is False
    assert started_again == started
    assert waiting_changed is True
    assert waiting_again_changed is False
    assert waiting_again == waiting
    assert [row["id"] for row in repo.list_active(1)] == [task["id"]]
    with store.connection(read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM tarefas WHERE id=?", (task["id"],)
            ).fetchone()[0]
            == 1
        )


def test_generic_update_cannot_change_task_status(store):
    repo = TarefasStore(store)
    task = repo.create(1, {"titulo": "Status protegido"})
    repo.transition_status(task["id"], 1, "AGUARDANDO")

    updated = repo.update(
        task["id"],
        1,
        {"observacoes": "Somente observação", "status": "CANCELADA"},
    )

    assert updated["observacoes"] == "Somente observação"
    assert updated["status"] == "AGUARDANDO"
    assert updated["cancelado_em"] is None
    assert [row["id"] for row in repo.list_active(1)] == [task["id"]]


def test_creation_with_three_reminders(store):
    repo = TarefasStore(store)
    due = date.today().fromordinal(date.today().toordinal() + 2)
    reminders = [
        datetime.combine(due, datetime.min.time(), INSTITUTIONAL_TZ).replace(hour=hour).isoformat()
        for hour in (9, 10, 11)
    ]
    task = repo.create(1, {"titulo": "Completa", "prazo_data": due.isoformat(), "lembretes": reminders})
    assert task["prazo_hora"] is None
    assert [item["lembrar_em"] for item in repo.reminders(task["id"], 1)] == reminders
    assert repo.reminders(task["id"], 2) == []


def test_reminder_validation_rejects_duplicates_fourth_and_after_due(store):
    repo = TarefasStore(store)
    due = date.today().fromordinal(date.today().toordinal() + 1)
    first = datetime.combine(due, datetime.min.time(), INSTITUTIONAL_TZ).replace(hour=9).isoformat()
    later = datetime.combine(due, datetime.min.time(), INSTITUTIONAL_TZ).replace(hour=17).isoformat()
    with pytest.raises(ValueError, match="duplicados"):
        repo.create(1, {"titulo": "Duplicada", "lembretes": [first, first]})
    with pytest.raises(ValueError, match="máximo"):
        repo.create(1, {"titulo": "Quatro", "lembretes": [first, later, first.replace("09:00", "10:00"), first.replace("09:00", "11:00")]})
    with pytest.raises(ValueError, match="posterior"):
        repo.create(1, {"titulo": "Após prazo", "prazo_data": due.isoformat(), "prazo_hora": "09:00", "lembretes": [later]})


def test_due_time_and_multiple_expired_reminders_produce_one_alert(store):
    repo = TarefasStore(store)
    today = date.today()
    reminders = [
        datetime.combine(today, datetime.min.time(), INSTITUTIONAL_TZ).replace(hour=hour).isoformat()
        for hour in (7, 8, 9)
    ]
    task = repo.create(1, {"titulo": "Um alerta", "prazo_data": today.isoformat(), "prazo_hora": "12:00", "lembretes": reminders})
    assert repo.get(task["id"], 1)["prazo_hora"] == "12:00"
    alerts, _, _ = collect_alerts(
        store,
        _alerts_principal(1),
        now=datetime.combine(today, datetime.min.time(), INSTITUTIONAL_TZ).replace(hour=10),
    )
    assert len([alert for alert in alerts if alert.source_module == "tarefas" and alert.source_id == str(task["id"])]) == 1
    repo.change_status(task["id"], 1, "CONCLUIDA")
    alerts, _, _ = collect_alerts(
        store,
        _alerts_principal(1),
        now=datetime.combine(today, datetime.min.time(), INSTITUTIONAL_TZ).replace(hour=10),
    )
    assert not any(alert.source_module == "tarefas" for alert in alerts)


def test_manual_hour_accepts_hhmm_and_rejects_invalid_values():
    assert _parse_hour("06:40", "Hora") == time(6, 40)
    assert _parse_hour("00:00", "Hora") == time(0, 0)
    assert _parse_hour("23:59", "Hora") == time(23, 59)
    for value in ("25:00", "9:7", "ab:cd", ""):
        with pytest.raises(ValueError, match="HH:MM"):
            _parse_hour(value, "Hora")


def test_create_and_edit_keep_deadline_hour_as_hhmm(store):
    repo = TarefasStore(store)
    today = date.today().isoformat()
    task = repo.create(1, {"titulo": "Com hora", "prazo_data": today, "prazo_hora": _parse_hour("06:40", "Hora").strftime("%H:%M")})
    assert repo.get(task["id"], 1)["prazo_hora"] == "06:40"
    updated = repo.update(task["id"], 1, {"prazo_data": today, "prazo_hora": _parse_hour("23:59", "Hora").strftime("%H:%M")})
    assert updated["prazo_hora"] == "23:59"


def test_reminder_time_choices_are_centralized_half_hour_intervals():
    assert REMINDER_TIMES[0] == "06:00"
    assert REMINDER_TIMES[-1] == "23:00"
    assert "06:30" in REMINDER_TIMES and "14:30" in REMINDER_TIMES
    assert "23:30" not in REMINDER_TIMES


def test_deadline_uses_configured_local_hour_in_alert(store):
    repo = TarefasStore(store)
    today = date.today()
    task = repo.create(1, {"titulo": "06:10", "prazo_data": today.isoformat(), "prazo_hora": "06:10"})
    row = repo.get(task["id"], 1)
    deadline = effective_deadline(row, INSTITUTIONAL_TZ)
    assert deadline.strftime("%H:%M") == "06:10"
    alerts = task_alerts(store, _principal(1), datetime.combine(today, time(6, 0), INSTITUTIONAL_TZ))
    alert = next(item for item in alerts if item.source_id == str(task["id"]))
    assert alert.datetime.strftime("%H:%M") == "06:10"
    assert alert.datetime.tzinfo == INSTITUTIONAL_TZ


def test_date_only_deadline_is_not_midnight_or_overdue_at_start_of_day(store):
    repo = TarefasStore(store)
    today = date.today()
    task = repo.create(1, {"titulo": "Dia inteiro", "prazo_data": today.isoformat()})
    now = datetime.combine(today, time(0, 1), INSTITUTIONAL_TZ)
    alert = next(item for item in task_alerts(store, _principal(1), now) if item.source_id == str(task["id"]))
    assert alert.title == "VENCE HOJE"
    assert alert.datetime is None
    assert alert.date == today
    assert effective_deadline(repo.get(task["id"], 1), INSTITUTIONAL_TZ).time() == time.max


def test_deadline_becomes_overdue_only_after_its_configured_hour(store):
    repo = TarefasStore(store)
    today = date.today()
    task = repo.create(1, {"titulo": "Horário", "prazo_data": today.isoformat(), "prazo_hora": "06:10"})
    before = task_alerts(store, _principal(1), datetime.combine(today, time(6, 9), INSTITUTIONAL_TZ))
    after = task_alerts(store, _principal(1), datetime.combine(today, time(6, 11), INSTITUTIONAL_TZ))
    assert next(item for item in before if item.source_id == str(task["id"])).title == "VENCE HOJE"
    assert next(item for item in after if item.source_id == str(task["id"])).title == "ATRASADA"


def test_task_alert_window_covers_seven_days_and_prioritizes_relevant_reason(store):
    repo = TarefasStore(store)
    today = date.today()
    now = datetime.combine(today, time(12), INSTITUTIONAL_TZ)
    urgent_late = repo.create(1, {"titulo": "Urgente atrasada", "prioridade": "URGENTE", "prazo_data": (today - timedelta(days=1)).isoformat()})
    high_late = repo.create(1, {"titulo": "Alta atrasada", "prioridade": "ALTA", "prazo_data": (today - timedelta(days=1)).isoformat()})
    today_task = repo.create(1, {"titulo": "Hoje", "prioridade": "NORMAL", "prazo_data": today.isoformat(), "prazo_hora": "20:00"})
    tomorrow_task = repo.create(1, {"titulo": "Amanhã", "prioridade": "ALTA", "prazo_data": (today + timedelta(days=1)).isoformat()})
    two_days = repo.create(1, {"titulo": "Dois dias", "prioridade": "NORMAL", "prazo_data": (today + timedelta(days=2)).isoformat()})
    seven_days = repo.create(1, {"titulo": "Sete dias", "prioridade": "BAIXA", "prazo_data": (today + timedelta(days=7)).isoformat()})
    hidden = repo.create(1, {"titulo": "Oito dias", "prazo_data": (today + timedelta(days=8)).isoformat()})
    far_reminder = repo.create(1, {"titulo": "Lembrete distante", "prazo_data": (today + timedelta(days=9)).isoformat(), "lembretes": [(now - timedelta(minutes=1)).isoformat()]})
    items, _, _ = collect_alerts(store, _alerts_principal(1), now=now)
    tasks = [item for item in items if item.source_module == "tarefas"]
    identifiers = [item.source_id for item in tasks]
    assert str(hidden["id"]) not in identifiers
    assert {str(item["id"]) for item in (urgent_late, high_late, today_task, tomorrow_task, two_days, seven_days, far_reminder)} <= set(identifiers)
    assert identifiers.index(str(urgent_late["id"])) < identifiers.index(str(high_late["id"]))
    assert next(item for item in tasks if item.source_id == str(today_task["id"])).title == "VENCE HOJE"
    assert next(item for item in tasks if item.source_id == str(tomorrow_task["id"])).title == "VENCE AMANHÃ"
    assert next(item for item in tasks if item.source_id == str(two_days["id"])).title == "PRÓXIMO PRAZO"
    assert next(item for item in tasks if item.source_id == str(far_reminder["id"])).title == "LEMBRETE"


def test_same_task_has_only_one_alert_and_priority_then_deadline_breaks_ties(store):
    repo = TarefasStore(store)
    today = date.today(); now = datetime.combine(today, time(12), INSTITUTIONAL_TZ)
    first = repo.create(1, {"titulo": "Mais próximo", "prioridade": "ALTA", "prazo_data": (today + timedelta(days=2)).isoformat()})
    second = repo.create(1, {"titulo": "Depois", "prioridade": "ALTA", "prazo_data": (today + timedelta(days=3)).isoformat()})
    urgent = repo.create(1, {"titulo": "Prioridade", "prioridade": "URGENTE", "prazo_data": (today + timedelta(days=5)).isoformat()})
    duplicate = repo.create(1, {"titulo": "Uma só", "prioridade": "URGENTE", "prazo_data": today.isoformat(), "prazo_hora": "20:00", "lembretes": [(now - timedelta(minutes=1)).isoformat()]})
    items, _, _ = collect_alerts(store, _alerts_principal(1), now=now)
    tasks = [item for item in items if item.source_module == "tarefas"]
    ids = [item.source_id for item in tasks]
    assert ids.count(str(duplicate["id"])) == 1
    assert ids.index(str(urgent["id"])) < ids.index(str(first["id"]))
    assert ids.index(str(first["id"])) < ids.index(str(second["id"]))


def test_legacy_reminder_migration_is_empty_safe_and_idempotent(store):
    import database.tarefas as tarefas_module

    repo = TarefasStore(store)
    # A clean bootstrap has no legacy reminder and must remain valid.
    tarefas_module._READY.clear()
    TarefasStore(store)
    stamp = datetime.now(INSTITUTIONAL_TZ).isoformat()
    with store.connection() as connection:
        identifier = connection.execute(
            "INSERT INTO tarefas(owner_user_id,titulo,descricao,categoria,prioridade,status,prazo_data,prazo_hora,lembrete_em,observacoes,criado_em,atualizado_em) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, "Legada", "", "", "NORMAL", "A_FAZER", None, None, stamp, "", stamp, stamp),
        ).lastrowid
    tarefas_module._READY.clear()
    migrated = TarefasStore(store)
    assert [row["lembrar_em"] for row in migrated.reminders(identifier, 1)] == [stamp]
    tarefas_module._READY.clear()
    repeated = TarefasStore(store)
    assert len(repeated.reminders(identifier, 1)) == 1


def test_new_task_form_keeps_existing_listing(store, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from database.access import AccessStore
    from database.store import ROOT
    from tests.access_testing import TEST_IDENTITY, enable_login

    enable_login(monkeypatch, store)
    owner = AccessStore(store).get_by_email(TEST_IDENTITY["email"])
    TarefasStore(store).create(owner["id"], {"titulo": "Tarefa visível no módulo"})
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    app.sidebar.radio(key="portal_module").set_value("Tarefas").run()
    assert not app.exception
    titles = " ".join(str(item.value) for item in app.markdown)
    assert "Tarefa visível no módulo" in titles
    app.button(key="tarefas_new").click().run()
    assert not app.exception
    assert any("Nova tarefa" in str(item.value) for item in app.subheader)
    titles = " ".join(str(item.value) for item in app.markdown)
    assert "Tarefa visível no módulo" in titles
    app.button(key="tarefas_form_newcancel").click().run()
    assert not app.exception
    assert not any("Nova tarefa" in str(item.value) for item in app.subheader)
    titles = " ".join(str(item.value) for item in app.markdown)
    assert "Tarefa visível no módulo" in titles


@pytest.mark.parametrize("status", ("A_FAZER", "EM_ANDAMENTO", "AGUARDANDO"))
def test_editing_only_notes_preserves_overdue_active_status(
    store, monkeypatch, status
):
    from streamlit.testing.v1 import AppTest

    from database.access import AccessStore
    from database.store import ROOT
    from tests.access_testing import TEST_IDENTITY, enable_login

    enable_login(monkeypatch, store)
    owner = AccessStore(store).get_by_email(TEST_IDENTITY["email"])
    repo = TarefasStore(store)
    overdue = date.today() - timedelta(days=1)
    task = repo.create(
        owner["id"],
        {
            "titulo": f"Atrasada {status}",
            "prazo_data": overdue.isoformat(),
            "prioridade": "ALTA",
        },
    )
    if status != "A_FAZER":
        task, changed = repo.transition_status(task["id"], owner["id"], status)
        assert changed is True
    monkeypatch.setattr("database.store.Store", lambda: store)

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio(key="portal_module").set_value("Tarefas").run()
    app.button(key=f"task_edit_{task['id']}").click().run()
    assert not app.exception
    assert app.selectbox(key=f"tarefas_form_{task['id']}status").disabled is True

    app.text_area(key=f"tarefas_form_{task['id']}notes").set_value(
        "Observação atualizada"
    ).run()
    app.button(key=f"tarefas_form_{task['id']}submit").click().run()

    assert not app.exception
    updated = repo.get(task["id"], owner["id"])
    assert updated["status"] == status
    assert updated["observacoes"] == "Observação atualizada"
    assert updated["prazo_data"] == overdue.isoformat()
    assert updated["concluido_em"] is None
    assert updated["cancelado_em"] is None
    assert [row["id"] for row in repo.list_active(owner["id"])].count(task["id"]) == 1
    app.run()
    assert sum(
        button.key == f"task_finish_{task['id']}" for button in app.button
    ) == 1
    assert any("Atrasada" in str(item.value) for item in app.markdown)


def test_task_page_prioritizes_active_tasks_and_updates_collapsed_history(
    store, monkeypatch
):
    from streamlit.testing.v1 import AppTest

    from database.access import AccessStore
    from database.store import ROOT
    from tests.access_testing import TEST_IDENTITY, enable_login

    enable_login(monkeypatch, store)
    owner = AccessStore(store).get_by_email(TEST_IDENTITY["email"])
    repo = TarefasStore(store)
    first = repo.create(owner["id"], {"titulo": "Ativa alfa"})
    second = repo.create(owner["id"], {"titulo": "Ativa beta"})
    to_cancel = repo.create(owner["id"], {"titulo": "Ativa cancelar"})
    edit_only = repo.create(owner["id"], {"titulo": "Ativa editar"})
    completed = repo.create(owner["id"], {"titulo": "Concluída recente"})
    cancelled = repo.create(owner["id"], {"titulo": "Cancelada recente"})
    repo.change_status(completed["id"], owner["id"], "CONCLUIDA")
    repo.change_status(cancelled["id"], owner["id"], "CANCELADA")
    monkeypatch.setattr("database.store.Store", lambda: store)

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio(key="portal_module").set_value("Tarefas").run()

    assert not app.exception
    assert [metric.label for metric in app.metric] == [
        "Atrasadas",
        "Hoje",
        "Próximas",
        "Em andamento",
        "Aguardando",
    ]
    assert app.expander[0].label == "Tarefas concluídas e canceladas (2)"
    assert app.expander[0].proto.expanded is False
    assert app.button(key=f"task_finish_{first['id']}")
    assert app.button(key=f"task_finish_{second['id']}")

    app.button(key=f"task_start_{first['id']}").click().run()
    assert not app.exception
    assert repo.get(first["id"], owner["id"])["status"] == "EM_ANDAMENTO"
    assert sum("Ativa alfa" in str(item.value) for item in app.markdown) == 1
    app.button(key=f"task_wait_{first['id']}").click().run()
    assert not app.exception
    assert repo.get(first["id"], owner["id"])["status"] == "AGUARDANDO"
    assert sum("Ativa alfa" in str(item.value) for item in app.markdown) == 1
    assert not any(
        button.key == f"task_wait_{first['id']}" for button in app.button
    )
    app.run().run()
    assert sum("Ativa alfa" in str(item.value) for item in app.markdown) == 1
    with store.connection(read_only=True) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM tarefas WHERE id=?", (first["id"],)
            ).fetchone()[0]
            == 1
        )
    app.button(key=f"task_resume_{first['id']}").click().run()
    assert not app.exception
    assert repo.get(first["id"], owner["id"])["status"] == "EM_ANDAMENTO"

    app.button(key=f"task_cancel_{to_cancel['id']}").click().run()
    assert not app.exception
    assert repo.get(to_cancel["id"], owner["id"])["status"] == "CANCELADA"
    assert app.expander[0].label == "Tarefas concluídas e canceladas (3)"

    app.button(key=f"task_finish_{first['id']}").click().run()

    assert not app.exception
    assert repo.get(first["id"], owner["id"])["status"] == "CONCLUIDA"
    assert app.expander[0].label == "Tarefas concluídas e canceladas (4)"
    assert not any(
        button.key == f"task_finish_{first['id']}" for button in app.button
    )
    assert app.button(key=f"task_reopen_{first['id']}")

    app.text_input(key="tarefas_q").set_value("beta").run()

    assert not app.exception
    assert app.button(key=f"task_finish_{second['id']}")
    assert not any(
        button.key == f"task_finish_{first['id']}" for button in app.button
    )

    app.checkbox(key=f"task_confirm_{second['id']}").check().run()
    app.button(key=f"task_delete_{second['id']}").click().run()

    assert not app.exception
    assert repo.get(second["id"], owner["id"]) is None
    assert not any(
        button.key == f"task_finish_{second['id']}" for button in app.button
    )

    app.text_input(key="tarefas_q").set_value("").run()
    app.button(key=f"task_edit_{edit_only['id']}").click().run()
    assert not app.exception
    assert app.text_input(key=f"tarefas_form_{edit_only['id']}title").value == "Ativa editar"


def test_active_task_cards_are_paginated(store, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from database.access import AccessStore
    from database.store import ROOT
    from tests.access_testing import TEST_IDENTITY, enable_login

    enable_login(monkeypatch, store)
    owner = AccessStore(store).get_by_email(TEST_IDENTITY["email"])
    repo = TarefasStore(store)
    for index in range(25):
        repo.create(owner["id"], {"titulo": f"Tarefa {index:02d}"})
    monkeypatch.setattr("database.store.Store", lambda: store)

    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio(key="portal_module").set_value("Tarefas").run()

    assert not app.exception
    assert len(
        [button for button in app.button if str(button.key).startswith("task_finish_")]
    ) == 20
    assert app.button(key="tarefas_active_next")

    app.button(key="tarefas_active_next").click().run()

    assert not app.exception
    assert len(
        [button for button in app.button if str(button.key).startswith("task_finish_")]
    ) == 5
    assert app.button(key="tarefas_active_previous")
