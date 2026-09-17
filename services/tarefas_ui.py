"""Streamlit interface for the private personal task list."""

from datetime import date, datetime, time
import re
import streamlit as st

from database.tarefas import ACTIVE, HISTORY, TarefasStore, effective_deadline
from services.audit import INSTITUTIONAL_TZ, registrar_evento
from services.branding import module_title

PRIORITIES = ("BAIXA", "NORMAL", "ALTA", "URGENTE")
STATUS_LABELS = {"A_FAZER": "A fazer", "EM_ANDAMENTO": "Em andamento", "AGUARDANDO": "Aguardando", "CONCLUIDA": "Concluída", "CANCELADA": "Cancelada"}
PRIORITY_LABELS = {"BAIXA": "Baixa", "NORMAL": "Normal", "ALTA": "Alta", "URGENTE": "Urgente"}
HOUR_RE = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
REMINDER_TIMES = tuple(
    f"{hour:02d}:{minute:02d}"
    for hour in range(6, 23)
    for minute in (0, 30)
) + ("23:00",)


def _done(message, *, clear_edit=True):
    from services.alerts import invalidate_alert_summary
    invalidate_alert_summary()
    if clear_edit: st.session_state.pop("tarefas_edit", None)
    st.session_state["tarefas_message"] = message
    st.rerun()


def _audit(store, principal, event, action, identifier):
    registrar_evento(store, evento=event, modulo="tarefas", acao=action, principal=principal, entidade_tipo="tarefa", entidade_id=identifier)


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
    prefix = "tarefas_form_" + str(old.get("id", "new"))
    st.subheader("Editar tarefa" if old else "Nova tarefa")
    # Normal widgets, rather than st.form, preserve the current widget state
    # while a conditional checkbox triggers Streamlit's regular rerun.
    existing_reminders = repo.reminders(old["id"], principal.id) if old.get("id") else []
    reminder_state = prefix + "reminder_slots"
    if reminder_state not in st.session_state:
        st.session_state[reminder_state] = list(range(max(1, len(existing_reminders))))
    title = st.text_input("Título *", value=old.get("titulo", ""), key=prefix+"title")
    description = st.text_area("Descrição", value=old.get("descricao", ""), key=prefix+"description")
    priority_column, _ = st.columns([2, 5])
    priority = priority_column.selectbox("Prioridade", PRIORITIES, index=PRIORITIES.index(old.get("prioridade", "NORMAL")), format_func=PRIORITY_LABELS.get, key=prefix+"priority")
    status = st.selectbox("Status", ACTIVE, index=ACTIVE.index(old.get("status", "A_FAZER")) if old.get("status") in ACTIVE else 0, format_func=STATUS_LABELS.get, key=prefix+"status") if old else "A_FAZER"
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
    notes = st.text_area("Observações", value=old.get("observacoes", ""), key=prefix+"notes")
    submitted = st.button("Salvar alterações" if old else "Criar tarefa", type="primary", key=prefix+"submit")
    if submitted:
        try:
            if due_time is not None:
                due_time = _parse_hour(due_time, "Hora do prazo").strftime("%H:%M")
            reminders = [datetime.combine(day, _parse_hour(hour, f"Hora do lembrete {number}"), INSTITUTIONAL_TZ).isoformat() for day, hour, number in reminder_inputs]
            values={"titulo":title,"descricao":description,"prioridade":priority,"status":status,"prazo_data":due_date,"prazo_hora":due_time,"lembretes":reminders,"observacoes":notes}
            record = repo.update(old["id"],principal.id,values) if old else repo.create(principal.id,values)
            if not record: st.error("Tarefa não encontrada."); return
            _audit(store,principal,"TAREFA_EDITADA" if old else "TAREFA_CRIADA","EDITAR" if old else "CRIAR",record["id"])
            if not old:
                for key in list(st.session_state):
                    if key.startswith(prefix): st.session_state.pop(key, None)
            _done("Tarefa salva.")
        except ValueError as exc: st.error(str(exc))


def _card(repo, store, principal, row):
    today=date.today(); due=date.fromisoformat(row["prazo_data"]) if row.get("prazo_data") else None
    deadline = effective_deadline(row, INSTITUTIONAL_TZ)
    current = datetime.now(INSTITUTIONAL_TZ)
    with st.container(border=True):
        st.markdown(f"**{row['titulo']}** · :{'red' if row['prioridade']=='URGENTE' else 'orange' if row['prioridade']=='ALTA' else 'blue' if row['prioridade']=='NORMAL' else 'gray'}[{PRIORITY_LABELS[row['prioridade']]}]")
        st.caption(STATUS_LABELS[row["status"]])
        if due:
            label = "⚠ Atrasada" if deadline < current else "Vence hoje" if due == today else "Prazo"
            st.write(label + ": " + due.strftime("%d/%m/%Y") + (" às "+row["prazo_hora"] if row.get("prazo_hora") else ""))
        controls=st.columns(5)
        if row["status"] == "A_FAZER" and controls[0].button("Iniciar", key=f"task_start_{row['id']}"):
            repo.change_status(row["id"],principal.id,"EM_ANDAMENTO"); _audit(store,principal,"TAREFA_STATUS_ALTERADO","INICIAR",row["id"]); _done("Tarefa iniciada.")
        elif row["status"] == "EM_ANDAMENTO" and controls[0].button("Aguardando", key=f"task_wait_{row['id']}"):
            repo.change_status(row["id"],principal.id,"AGUARDANDO"); _audit(store,principal,"TAREFA_STATUS_ALTERADO","AGUARDAR",row["id"]); _done("Tarefa aguardando.")
        elif row["status"] == "AGUARDANDO" and controls[0].button("Retomar", key=f"task_resume_{row['id']}"):
            repo.change_status(row["id"],principal.id,"EM_ANDAMENTO"); _audit(store,principal,"TAREFA_STATUS_ALTERADO","RETOMAR",row["id"]); _done("Tarefa retomada.")
        if row["status"] in ACTIVE and controls[1].button("Concluir", key=f"task_finish_{row['id']}"):
            repo.change_status(row["id"],principal.id,"CONCLUIDA"); _audit(store,principal,"TAREFA_STATUS_ALTERADO","CONCLUIR",row["id"]); _done("Tarefa concluída.")
        if controls[2].button("Editar", key=f"task_edit_{row['id']}"):
            st.session_state["tarefas_edit"]=row; st.rerun()
        if row["status"] in ACTIVE and controls[3].button("Cancelar",key=f"task_cancel_{row['id']}"):
            repo.change_status(row["id"],principal.id,"CANCELADA"); _audit(store,principal,"TAREFA_STATUS_ALTERADO","CANCELAR",row["id"]); _done("Tarefa cancelada.")
        if st.checkbox("Confirmo a exclusão definitiva",key=f"task_confirm_{row['id']}") and controls[4].button("Excluir",key=f"task_delete_{row['id']}"):
            if repo.delete(row["id"],principal.id): _audit(store,principal,"TAREFA_EXCLUIDA","EXCLUIR",row["id"]); _done("Tarefa excluída.")


def render(store, principal):
    repo=TarefasStore(store)
    st.title(module_title("tarefas", "TAREFAS")); st.caption("Organização pessoal de demandas, prazos e prioridades")
    if st.session_state.pop("tarefas_message",None): st.success("Alteração realizada.")
    if st.button("+ Nova tarefa",type="primary"):
        st.session_state["tarefas_edit"]={}; st.rerun()
    if "tarefas_edit" in st.session_state: _editor(repo,store,principal); return
    counts=repo.situation_counts(principal.id); cols=st.columns(5)
    for col,key,label in zip(cols,counts,("Atrasadas","Hoje","Próximas","Em andamento","Aguardando")): col.metric(label,counts[key])
    if st.session_state.get("tarefas_open_id"):
        st.session_state["tarefas_section"] = "Tarefas"
    section = st.radio("Seção", ["Tarefas", "Histórico"], horizontal=True, key="tarefas_section")
    if section == "Tarefas":
        with st.expander("Filtros",expanded=False):
            q=st.text_input("Pesquisa",key="tarefas_q"); priority=st.selectbox("Prioridade",[None,*PRIORITIES],format_func=lambda x: PRIORITY_LABELS.get(x,"Todas"),key="tarefas_priority"); deadline=st.selectbox("Prazo",[None,"atrasadas","hoje","sem_prazo"],format_func=lambda x:{None:"Todos","atrasadas":"Atrasadas","hoje":"Hoje","sem_prazo":"Sem prazo"}[x],key="tarefas_deadline")
        rows=repo.list_active(principal.id,{"pesquisa":q,"prioridade":priority,"prazo":deadline})
        opened=st.session_state.pop("tarefas_open_id",None)
        if opened and not repo.get(opened,principal.id): st.info("Tarefa não encontrada.")
        for row in rows: _card(repo,store,principal,row)
        if not rows: st.info("Nenhuma tarefa ativa.")
    else:
        with st.expander("Filtros", expanded=False):
            history_q=st.text_input("Pesquisa",key="tarefas_history_q")
            history_priority=st.selectbox("Prioridade",[None,*PRIORITIES],format_func=lambda x: PRIORITY_LABELS.get(x,"Todas"),key="tarefas_history_priority")
            history_status=st.selectbox("Situação",[None,*HISTORY],format_func=lambda x: STATUS_LABELS.get(x,"Todas"),key="tarefas_history_status")
        rows=repo.list_history(principal.id,{"pesquisa":history_q,"prioridade":history_priority,"status":history_status},limit=30)
        for row in rows:
            st.write(f"**{row['titulo']}** · {STATUS_LABELS[row['status']]}")
            if row["status"]=="CONCLUIDA" and st.button("Reabrir",key=f"task_reopen_{row['id']}"):
                repo.change_status(row["id"],principal.id,"A_FAZER"); _audit(store,principal,"TAREFA_STATUS_ALTERADO","REABRIR",row["id"]); _done("Tarefa reaberta.")
            if st.checkbox("Confirmo a exclusão definitiva",key=f"history_confirm_{row['id']}") and st.button("Excluir",key=f"history_delete_{row['id']}"):
                if repo.delete(row["id"],principal.id): _audit(store,principal,"TAREFA_EXCLUIDA","EXCLUIR",row["id"]); _done("Tarefa excluída.")
