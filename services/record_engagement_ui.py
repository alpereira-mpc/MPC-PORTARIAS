"""Small, on-demand controls shared by record detail views."""

from datetime import date, datetime, time

import streamlit as st

from database.record_engagement import RecordEngagementStore
from database.tarefas import TarefasStore
from services.audit import INSTITUTIONAL_TZ, registrar_evento
from services.date_format import format_date_br
from services.ui_theme import empty_state, section_label

ORIGIN_LABELS = {
    "oficio_enviado": "Ofício enviado",
    "oficio_recebido": "Ofício recebido",
    "memorando": "Memorando",
    "representacao": "Representação",
    "ouvidoria": "Notícia de Fato",
    "tarefa": "Tarefa",
}
TASK_STATUS = {
    "A_FAZER": "A fazer",
    "EM_ANDAMENTO": "Em andamento",
    "AGUARDANDO": "Aguardando",
    "CONCLUIDA": "Concluída",
    "CANCELADA": "Cancelada",
}


def _audit(store, principal, event, action, module, identifier, extra=None):
    registrar_evento(
        store,
        evento=event,
        modulo="tarefas" if event.startswith("TAREFA") else "alertas",
        acao=action,
        principal=principal,
        entidade_tipo=module,
        entidade_id=identifier,
        detalhes=extra,
    )


def open_linked_origin(module, identifier, store, principal):
    RecordEngagementStore(store).authorize_origin(module, identifier, principal.id)
    from portal import request_portal_navigation

    if module.startswith("oficio_"):
        page = "Acompanhamento" if module == "oficio_enviado" else "Recebidos"
        request_portal_navigation(
            "Ofícios", pending_open_oficio={"id": identifier, "page": page}
        )
    elif module == "memorando":
        request_portal_navigation(
            "Memorandos", pending_open_memorando={"id": identifier, "page": "Histórico"}
        )
    elif module == "representacao":
        request_portal_navigation("Representações", representacoes_view=int(identifier))
    elif module == "ouvidoria":
        request_portal_navigation("Ouvidoria", ouvidoria_open_id=int(identifier))
    elif module == "tarefa":
        request_portal_navigation("Tarefas", tarefas_open_id=int(identifier))


def _create_task(module, identifier, title):
    from portal import request_portal_navigation

    request_portal_navigation(
        "Tarefas",
        tarefas_new_origin={
            "origem_modulo": module,
            "origem_id": str(identifier),
            "titulo": "Acompanhar " + (title or ORIGIN_LABELS[module]),
        },
    )


def _reminder_editor(repo, store, principal, module, identifier, prefix):
    with st.expander("Criar lembrete", expanded=False):
        has_time = st.checkbox("Informar horário", key=prefix + "_has_time")
        with st.form(prefix + "_reminder_form"):
            reminder_date = st.date_input(
                "Data do lembrete", date.today(), format="DD/MM/YYYY"
            )
            reminder_time = (
                st.time_input("Horário", time(9, 0)) if has_time else time.min
            )
            text = st.text_input("Texto curto (opcional)", max_chars=300)
            submitted = st.form_submit_button("Salvar lembrete")
        if submitted:
            moment = datetime.combine(reminder_date, reminder_time, INSTITUTIONAL_TZ)
            reminder_id = repo.create_reminder(
                principal.id, module, identifier, moment.isoformat(), text
            )
            _audit(
                store,
                principal,
                "LEMBRETE_CRIADO",
                "CRIAR",
                module,
                identifier,
                {"lembrete_id": reminder_id, "data": reminder_date.isoformat()},
            )
            from services.alerts import invalidate_alert_summary

            invalidate_alert_summary()
            st.rerun()


def _reminders(repo, store, principal, module, identifier, prefix):
    rows = repo.list_origin_reminders(principal.id, module, identifier)
    pending = [row for row in rows if row["status"] == "PENDENTE"]
    if not pending:
        return
    section_label("Lembretes")
    for row in pending:
        moment = datetime.fromisoformat(row["lembrar_em"])
        cols = st.columns([3.2, 1.6, 1])
        cols[0].caption(
            (row.get("texto") or "Lembrete")
            + " · "
            + moment.astimezone(INSTITUTIONAL_TZ).strftime("%d/%m/%Y %H:%M")
        )
        if cols[1].button("Lembrar amanhã", key=f"{prefix}_tomorrow_{row['id']}"):
            repo.remind_tomorrow(
                row["id"], principal.id, datetime.now(INSTITUTIONAL_TZ)
            )
            _audit(store, principal, "LEMBRETE_REAGENDADO", "ALTERAR", module, identifier)
            st.rerun()
        if cols[2].button("Concluir", key=f"{prefix}_done_{row['id']}"):
            repo.conclude(row["id"], principal.id)
            _audit(store, principal, "LEMBRETE_CONCLUIDO", "CONCLUIR", module, identifier)
            st.rerun()
        with st.expander("Escolher nova data", expanded=False):
            new_date = st.date_input(
                "Nova data",
                moment.astimezone(INSTITUTIONAL_TZ).date(),
                format="DD/MM/YYYY",
                key=f"{prefix}_new_date_{row['id']}",
            )
            if st.button("Reagendar", key=f"{prefix}_reschedule_{row['id']}"):
                local = moment.astimezone(INSTITUTIONAL_TZ)
                repo.reschedule(
                    row["id"],
                    principal.id,
                    datetime.combine(new_date, local.time(), INSTITUTIONAL_TZ).isoformat(),
                )
                _audit(store, principal, "LEMBRETE_REAGENDADO", "ALTERAR", module, identifier)
                st.rerun()


def render_origin_tools(store, principal, module, identifier, title):
    """Render only from an already-open record detail."""
    prefix = f"eng_{module}_{identifier}"
    repo = RecordEngagementStore(store)
    section_label("Organização pessoal")
    left, middle = st.columns(2)
    if left.button("Criar tarefa", key=prefix + "_task"):
        _create_task(module, identifier, title)
    following = repo.is_following(principal.id, module, identifier)
    follow_label = "Deixar de seguir" if following else "Seguir"
    if middle.button(follow_label, key=prefix + "_follow"):
        if following:
            repo.unfollow(principal.id, module, identifier)
            event, action = "REGISTRO_DEIXOU_DE_SER_SEGUIDO", "DESVINCULAR"
        else:
            repo.follow(principal.id, module, identifier)
            event, action = "REGISTRO_SEGUIDO", "VINCULAR"
        _audit(store, principal, event, action, module, identifier)
        st.rerun()
    _reminder_editor(repo, store, principal, module, identifier, prefix)
    _reminders(repo, store, principal, module, identifier, prefix)

    related = TarefasStore(store).list_related(module, identifier, principal.id)
    section_label("Tarefas relacionadas")
    if not related:
        empty_state("Nenhuma tarefa relacionada.")
    for row in related:
        due = format_date_br(row.get("prazo_data"), empty="Sem prazo")
        cols = st.columns([4, 1])
        cols[0].write(row["titulo"])
        cols[0].caption(
            f"{row['responsavel']} · {due} · {TASK_STATUS.get(row['status'], row['status'])}"
        )
        if cols[1].button("Abrir tarefa", key=f"{prefix}_open_task_{row['id']}"):
            from portal import request_portal_navigation

            request_portal_navigation("Tarefas", tarefas_open_id=row["id"])


def render_task_origin(store, principal, row, context="detail"):
    module = row.get("origem_modulo")
    identifier = row.get("origem_id")
    if not module or not identifier:
        return
    st.caption("Origem: " + ORIGIN_LABELS.get(module, module))
    if st.button(
        "Abrir origem", key=f"task_origin_{context}_{row.get('id', 'new')}"
    ):
        try:
            open_linked_origin(module, identifier, store, principal)
        except ValueError:
            st.error("A origem não existe ou você não possui mais acesso.")


def render_task_reminders(store, principal, task_id):
    repo = RecordEngagementStore(store)
    prefix = f"eng_tarefa_{task_id}"
    _reminder_editor(repo, store, principal, "tarefa", task_id, prefix)
    _reminders(repo, store, principal, "tarefa", task_id, prefix)


def render_notice_actions(store, principal, notice_id, notice_type, key):
    """Conclude / reschedule actions for a due notice (Alertas / Pendências)."""
    if not notice_id:
        return
    from services.alerts import invalidate_alert_summary

    repo = RecordEngagementStore(store)
    is_reminder = notice_type == "LEMBRETE"
    columns = st.columns(2 if is_reminder else 1)
    if columns[0].button(
        "Concluir" if is_reminder else "Marcar como lido", key=key + "_done"
    ):
        repo.conclude(notice_id, principal.id)
        _audit(
            store,
            principal,
            "LEMBRETE_CONCLUIDO" if is_reminder else "AVISO_LIDO",
            "CONCLUIR",
            "aviso",
            notice_id,
        )
        invalidate_alert_summary()
        st.rerun()
    if is_reminder and columns[1].button("Lembrar amanhã", key=key + "_tomorrow"):
        repo.remind_tomorrow(notice_id, principal.id, datetime.now(INSTITUTIONAL_TZ))
        _audit(store, principal, "LEMBRETE_REAGENDADO", "ALTERAR", "lembrete", notice_id)
        invalidate_alert_summary()
        st.rerun()
    if is_reminder:
        with st.expander("Escolher nova data", expanded=False):
            chosen = st.date_input(
                "Nova data", date.today(), format="DD/MM/YYYY", key=key + "_date"
            )
            if st.button("Reagendar", key=key + "_reschedule"):
                repo.reschedule(
                    notice_id,
                    principal.id,
                    datetime.combine(chosen, time.min, INSTITUTIONAL_TZ).isoformat(),
                )
                _audit(
                    store,
                    principal,
                    "LEMBRETE_REAGENDADO",
                    "ALTERAR",
                    "lembrete",
                    notice_id,
                )
                invalidate_alert_summary()
                st.rerun()
