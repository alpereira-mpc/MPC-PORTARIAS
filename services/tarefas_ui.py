"""Streamlit interface for the private personal task list."""

from datetime import date, datetime, time
import re
import streamlit as st

from database.tarefas import ACTIVE, HISTORY, TarefasStore, effective_deadline
from services.audit import INSTITUTIONAL_TZ, registrar_evento
from services.branding import module_title
from services.ui_theme import (
    actions_mark,
    badge,
    badges,
    card_container,
    empty_state,
    filter_mark,
    kpi_mark,
    priority_tone,
    render_record,
    section_label,
    status_tone,
)

PRIORITIES = ("BAIXA", "NORMAL", "ALTA", "URGENTE")
STATUS_LABELS = {"A_FAZER": "A fazer", "EM_ANDAMENTO": "Em andamento", "AGUARDANDO": "Aguardando", "CONCLUIDA": "Concluída", "CANCELADA": "Cancelada"}
PRIORITY_LABELS = {"BAIXA": "Baixa", "NORMAL": "Normal", "ALTA": "Alta", "URGENTE": "Urgente"}
HOUR_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
REMINDER_TIMES = tuple(
    f"{hour:02d}:{minute:02d}"
    for hour in range(6, 23)
    for minute in (0, 30)
) + ("23:00",)
ACTIVE_PAGE_SIZE = 20


def _done(message, *, clear_edit=True):
    from services.alerts import invalidate_alert_summary
    invalidate_alert_summary()
    if clear_edit: st.session_state.pop("tarefas_edit", None)
    st.session_state["tarefas_message"] = message
    st.rerun()


def _audit(store, principal, event, action, identifier):
    registrar_evento(store, evento=event, modulo="tarefas", acao=action, principal=principal, entidade_tipo="tarefa", entidade_id=identifier)


def _transition_active_status(
    repo, store, principal, identifier, status, action, message
):
    _record, changed = repo.transition_status(identifier, principal.id, status)
    if changed:
        _audit(store, principal, "TAREFA_STATUS_ALTERADO", action, identifier)
    from services.alerts import invalidate_alert_summary

    invalidate_alert_summary()
    st.session_state["tarefas_message"] = message


def _open_editor(row):
    prefix = "tarefas_form_" + str(row["id"])
    for key in list(st.session_state):
        if key.startswith(prefix):
            st.session_state.pop(key, None)
    st.session_state["tarefas_edit"] = row


def _parse_hour(value, label):
    value = (value or "").strip()
    if not HOUR_RE.fullmatch(value):
        raise ValueError(f"{label}: informe um horário válido no formato HH:MM.")
    return datetime.strptime(value, "%H:%M").time()


def _deadline_values(prefix, row, enabled):
    if not enabled: return None, None
    with st.container(border=True):
        st.caption("Prazo")
        initial = date.fromisoformat(row["prazo_data"]) if row.get("prazo_data") else date.today()
        date_column, toggle_column, hour_column, _spacer = st.columns([3, 1.4, 1.8, 2])
        day = date_column.date_input("Data do prazo", initial, format="DD/MM/YYYY", key=prefix+"date")
        has_time = toggle_column.checkbox("Informar horário", value=bool(row.get("prazo_hora")), key=prefix+"has_time")
        hour = hour_column.text_input("Hora do prazo", value=row.get("prazo_hora") or "", placeholder="HH:MM", max_chars=5, key=prefix+"time") if has_time else None
    return day.isoformat(), hour


def _editor(repo, store, principal):
    old = st.session_state.get("tarefas_edit") or {}
    editing = bool(old.get("id"))
    form_identity = old.get("id") or (
        "new_" + old["origem_modulo"] + "_" + str(old["origem_id"])
        if old.get("origem_modulo") and old.get("origem_id")
        else "new"
    )
    prefix = "tarefas_form_" + str(form_identity)
    st.subheader("Editar tarefa" if editing else "Nova tarefa")
    if old.get("origem_modulo"):
        from services.record_engagement_ui import render_task_origin

        render_task_origin(store, principal, old, "editor")
    # Normal widgets, rather than st.form, preserve the current widget state
    # while a conditional checkbox triggers Streamlit's regular rerun.
    existing_reminders = repo.reminders(old["id"], principal.id) if old.get("id") else []
    reminder_state = prefix + "reminder_slots"
    if reminder_state not in st.session_state:
        st.session_state[reminder_state] = list(range(max(1, len(existing_reminders))))
    title = st.text_input("Título *", value=old.get("titulo", ""), key=prefix+"title")
    description = st.text_area("Descrição", value=old.get("descricao", ""), key=prefix+"description")
    section_label("Classificação")
    priority_column, _ = st.columns([2, 5])
    priority = priority_column.selectbox("Prioridade", PRIORITIES, index=PRIORITIES.index(old.get("prioridade", "NORMAL")), format_func=PRIORITY_LABELS.get, key=prefix+"priority")
    if editing:
        status_key = prefix + "status"
        st.session_state[status_key] = (
            old["status"] if old.get("status") in ACTIVE else "A_FAZER"
        )
        st.selectbox(
            "Status",
            ACTIVE,
            format_func=STATUS_LABELS.get,
            key=status_key,
            disabled=True,
        )
    definir_prazo = st.checkbox("Definir prazo", value=bool(old.get("prazo_data")), key=prefix + "deadline")
    due_date, due_time = _deadline_values(prefix, old, definir_prazo)
    definir_lembretes = st.checkbox("Definir lembretes", value=bool(existing_reminders or old.get("lembrete_em")), key=prefix + "reminder")
    reminder_inputs = []
    if definir_lembretes:
        with st.container(border=True):
            st.caption("Lembretes")
            for index, slot in enumerate(st.session_state[reminder_state]):
                initial = datetime.fromisoformat(existing_reminders[index]["lembrar_em"]) if index < len(existing_reminders) else datetime.now(INSTITUTIONAL_TZ)
                date_column, time_column, remove_column = st.columns([4, 4, 1])
                reminder_date = date_column.date_input(f"Lembrete {index + 1} — Data", initial.date(), format="DD/MM/YYYY", key=f"{prefix}reminder_{slot}_date")
                initial_time = initial.strftime("%H:%M")
                options = REMINDER_TIMES if initial_time in REMINDER_TIMES else tuple(sorted((*REMINDER_TIMES, initial_time)))
                reminder_time = time_column.selectbox(f"Lembrete {index + 1} — Hora", options, index=options.index(initial_time), key=f"{prefix}reminder_{slot}_time")
                reminder_inputs.append((reminder_date, reminder_time, index + 1))
                if index and remove_column.button("Remover", key=f"{prefix}reminder_remove_{slot}"):
                    st.session_state[reminder_state].remove(slot); st.rerun()
            if len(st.session_state[reminder_state]) < 3 and st.button("+ Adicionar lembrete", key=prefix+"add_reminder"):
                st.session_state[reminder_state].append(max(st.session_state[reminder_state]) + 1); st.rerun()
    section_label("Observações")
    notes = st.text_area("Observações", value=old.get("observacoes", ""), key=prefix+"notes")
    submitted = st.button("Salvar alterações" if editing else "Criar tarefa", type="primary", key=prefix+"submit")
    if st.button("Cancelar", key=prefix+"cancel"):
        for key in list(st.session_state):
            if key.startswith(prefix):
                st.session_state.pop(key, None)
        st.session_state.pop("tarefas_edit", None)
        st.rerun()
    if submitted:
        try:
            if due_time is not None:
                due_time = _parse_hour(due_time, "Hora do prazo").strftime("%H:%M")
            reminders = [datetime.combine(day, _parse_hour(hour, f"Hora do lembrete {number}"), INSTITUTIONAL_TZ).isoformat() for day, hour, number in reminder_inputs]
            values={"titulo":title,"descricao":description,"prioridade":priority,"prazo_data":due_date,"prazo_hora":due_time,"lembretes":reminders,"observacoes":notes,"origem_modulo":old.get("origem_modulo"),"origem_id":old.get("origem_id")}
            record = repo.update(old["id"],principal.id,values) if editing else repo.create(principal.id,values)
            if not record: st.error("Tarefa não encontrada."); return
            event = "TAREFA_EDITADA" if old.get("id") else "TAREFA_VINCULADA_CRIADA" if record.get("origem_modulo") else "TAREFA_CRIADA"
            _audit(store,principal,event,"EDITAR" if old.get("id") else "CRIAR",record["id"])
            if not editing:
                for key in list(st.session_state):
                    if key.startswith(prefix): st.session_state.pop(key, None)
            _done("Tarefa salva.")
        except ValueError as exc: st.error(str(exc))
    if old.get("id"):
        from services.record_engagement_ui import render_task_reminders

        render_task_reminders(store, principal, old["id"])


def _card(repo, store, principal, row, index=0):
    today=date.today(); due=date.fromisoformat(row["prazo_data"]) if row.get("prazo_data") else None
    deadline = effective_deadline(row, INSTITUTIONAL_TZ)
    current = datetime.now(INSTITUTIONAL_TZ)
    overdue = bool(due and deadline < current)
    due_label = ""
    if due:
        due_label = "Atrasada" if overdue else "Vence hoje" if due == today else "Prazo"
        due_label = due_label + ": " + due.strftime("%d/%m/%Y") + (" às "+row["prazo_hora"] if row.get("prazo_hora") else "")
    accent = "danger" if overdue else "muted" if row["status"] == "CANCELADA" else priority_tone(row["prioridade"])
    marks = badges(
        (STATUS_LABELS[row["status"]], status_tone(STATUS_LABELS[row["status"]])),
        (PRIORITY_LABELS[row["prioridade"]], priority_tone(row["prioridade"])),
    )
    if overdue:
        marks += badge("Atrasada", "danger")
    with card_container(index, f"task_{row['id']}", critical=overdue):
        render_record(row["titulo"], badges_html=marks, meta=due_label, accent=accent)
        if row.get("origem_modulo"):
            from services.record_engagement_ui import render_task_origin

            render_task_origin(store, principal, row, "active")
        with st.container(key=f"task_actions_{row['id']}"):
            actions_mark()
            controls=st.columns(5)
            status_action = {
                "A_FAZER": (
                    "Iniciar", "start", "EM_ANDAMENTO", "INICIAR", "Tarefa iniciada."
                ),
                "EM_ANDAMENTO": (
                    "Aguardando", "wait", "AGUARDANDO", "AGUARDAR", "Tarefa aguardando."
                ),
                "AGUARDANDO": (
                    "Retomar", "resume", "EM_ANDAMENTO", "RETOMAR", "Tarefa retomada."
                ),
            }.get(row["status"])
            if status_action:
                label, key_part, target, audit_action, message = status_action
                controls[0].button(
                    label,
                    key=f"task_{key_part}_{row['id']}",
                    on_click=_transition_active_status,
                    args=(
                        repo,
                        store,
                        principal,
                        row["id"],
                        target,
                        audit_action,
                        message,
                    ),
                )
            if row["status"] in ACTIVE and controls[1].button("Concluir", key=f"task_finish_{row['id']}"):
                _record, changed = repo.transition_status(row["id"],principal.id,"CONCLUIDA")
                if changed: _audit(store,principal,"TAREFA_STATUS_ALTERADO","CONCLUIR",row["id"])
                _done("Tarefa concluída.")
            controls[2].button(
                "Editar",
                key=f"task_edit_{row['id']}",
                on_click=_open_editor,
                args=(row,),
            )
            if row["status"] in ACTIVE and controls[3].button("Cancelar",key=f"task_cancel_{row['id']}"):
                repo.change_status(row["id"],principal.id,"CANCELADA"); _audit(store,principal,"TAREFA_STATUS_ALTERADO","CANCELAR",row["id"]); _done("Tarefa cancelada.")
            with controls[4].popover("Excluir"):
                confirmed = st.checkbox(
                    "Confirmo a exclusão definitiva",
                    key=f"task_confirm_{row['id']}",
                )
                if st.button(
                    "Excluir tarefa",
                    key=f"task_delete_{row['id']}",
                    disabled=not confirmed,
                ):
                    if repo.delete(row["id"],principal.id): _audit(store,principal,"TAREFA_EXCLUIDA","EXCLUIR",row["id"]); _done("Tarefa excluída.")


def _history_card(repo, store, principal, row, index=0):
    with card_container(index, f"thist_{row['id']}"):
        render_record(
            row["titulo"],
            badges_html=badges(
                (STATUS_LABELS[row["status"]], status_tone(STATUS_LABELS[row["status"]])),
                (PRIORITY_LABELS[row["prioridade"]], priority_tone(row["prioridade"])),
            ),
            accent="muted" if row["status"] == "CANCELADA" else "success",
        )
        if row.get("origem_modulo"):
            from services.record_engagement_ui import render_task_origin

            render_task_origin(store, principal, row, "history")
        with st.container(key=f"history_actions_{row['id']}"):
            actions_mark()
            controls = st.columns(2)
            if row["status"] == "CONCLUIDA" and controls[0].button(
                "Reabrir", key=f"task_reopen_{row['id']}"
            ):
                repo.change_status(row["id"],principal.id,"A_FAZER"); _audit(store,principal,"TAREFA_STATUS_ALTERADO","REABRIR",row["id"]); _done("Tarefa reaberta.")
            with controls[1].popover("Excluir"):
                confirmed = st.checkbox(
                    "Confirmo a exclusão definitiva",
                    key=f"history_confirm_{row['id']}",
                )
                if st.button(
                    "Excluir tarefa",
                    key=f"history_delete_{row['id']}",
                    disabled=not confirmed,
                ):
                    if repo.delete(row["id"],principal.id): _audit(store,principal,"TAREFA_EXCLUIDA","EXCLUIR",row["id"]); _done("Tarefa excluída.")


def _move_page(key, delta):
    st.session_state[key] = max(0, st.session_state.get(key, 0) + delta)


def _active_tasks(repo, store, principal):
    section_label("Filtros")
    with st.container(border=True, key="tarefas_filters"):
        filter_mark()
        search_column, priority_column, deadline_column = st.columns([2.4, 1, 1])
        q = search_column.text_input("Pesquisa", key="tarefas_q")
        priority = priority_column.selectbox(
            "Prioridade",
            [None, *PRIORITIES],
            format_func=lambda value: PRIORITY_LABELS.get(value, "Todas"),
            key="tarefas_priority",
        )
        deadline = deadline_column.selectbox(
            "Prazo",
            [None, "atrasadas", "hoje", "sem_prazo"],
            format_func=lambda value: {
                None: "Todos",
                "atrasadas": "Atrasadas",
                "hoje": "Hoje",
                "sem_prazo": "Sem prazo",
            }[value],
            key="tarefas_deadline",
        )

    filters = {"pesquisa": q, "prioridade": priority, "prazo": deadline}
    signature = (q, priority, deadline)
    if st.session_state.get("tarefas_active_filters") != signature:
        st.session_state["tarefas_active_filters"] = signature
        st.session_state["tarefas_active_page"] = 0
    page = int(st.session_state.get("tarefas_active_page", 0))
    rows = repo.list_active(
        principal.id,
        filters,
        limit=ACTIVE_PAGE_SIZE + 1,
        offset=page * ACTIVE_PAGE_SIZE,
    )
    if page and not rows:
        page = 0
        st.session_state["tarefas_active_page"] = 0
        rows = repo.list_active(
            principal.id,
            filters,
            limit=ACTIVE_PAGE_SIZE + 1,
            offset=0,
        )
    has_next = len(rows) > ACTIVE_PAGE_SIZE
    rows = rows[:ACTIVE_PAGE_SIZE]

    section_label("Tarefas ativas")
    for index, row in enumerate(rows):
        _card(repo, store, principal, row, index)
    if not rows:
        empty_state("Nenhuma tarefa ativa.")
    if page or has_next:
        previous, page_label, following = st.columns([1, 2, 1])
        previous.button(
            "Anterior",
            disabled=page == 0,
            key="tarefas_active_previous",
            on_click=_move_page,
            args=("tarefas_active_page", -1),
        )
        page_label.caption(
            f"Página {page + 1} · até {ACTIVE_PAGE_SIZE} tarefas por página"
        )
        following.button(
            "Próxima",
            disabled=not has_next,
            key="tarefas_active_next",
            on_click=_move_page,
            args=("tarefas_active_page", 1),
        )


def _task_history(repo, store, principal):
    history_filters = {
        "pesquisa": st.session_state.get("tarefas_history_q", ""),
        "prioridade": st.session_state.get("tarefas_history_priority"),
        "status": st.session_state.get("tarefas_history_status"),
    }
    history_rows = repo.list_history(principal.id, history_filters, limit=30)
    with st.expander(
        f"Tarefas concluídas e canceladas ({len(history_rows)})",
        expanded=False,
    ):
        with st.container(border=True, key="tarefas_history_filters"):
            filter_mark()
            search_column, priority_column, status_column = st.columns([2.4, 1, 1])
            search_column.text_input("Pesquisa", key="tarefas_history_q")
            priority_column.selectbox(
                "Prioridade",
                [None, *PRIORITIES],
                format_func=lambda value: PRIORITY_LABELS.get(value, "Todas"),
                key="tarefas_history_priority",
            )
            status_column.selectbox(
                "Situação",
                [None, *HISTORY],
                format_func=lambda value: STATUS_LABELS.get(value, "Todas"),
                key="tarefas_history_status",
            )
        for index, row in enumerate(history_rows):
            _history_card(repo, store, principal, row, index)
        if not history_rows:
            empty_state("Nenhuma tarefa concluída ou cancelada.")


def render(store, principal):
    repo=TarefasStore(store)
    st.title(module_title("tarefas", "TAREFAS")); st.caption("Organização pessoal de demandas, prazos e prioridades")
    if st.session_state.pop("tarefas_message",None): st.success("Alteração realizada.")
    if st.button("+ Nova tarefa", type="primary", key="tarefas_new"):
        st.session_state["tarefas_edit"] = {}
        st.rerun()
    linked = st.session_state.pop("tarefas_new_origin", None)
    if linked:
        st.session_state["tarefas_edit"] = linked
    if st.session_state.get("tarefas_open_id"):
        found = repo.get(st.session_state.pop("tarefas_open_id"), principal.id)
        if found:
            st.session_state["tarefas_edit"] = found
        else:
            empty_state("Tarefa não encontrada.")
    if "tarefas_edit" in st.session_state:
        _editor(repo, store, principal)
    counts=repo.situation_counts(principal.id)
    with st.container(key="tarefas_kpis"):
        cols=st.columns(5)
        for col,key,label,tone in zip(cols,counts,("Atrasadas","Hoje","Próximas","Em andamento","Aguardando"),("danger","warning","info","brand","warning")):
            with col:
                kpi_mark(tone)
                st.metric(label,counts[key])
    _active_tasks(repo, store, principal)
    _task_history(repo, store, principal)
