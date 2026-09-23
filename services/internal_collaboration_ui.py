"""Componentes discretos de notas e encaminhamentos, carregados só no detalhe."""

from datetime import date

import streamlit as st

from database.internal_collaboration import InternalCollaborationStore
from services.audit import format_local_short, registrar_evento
from services.oficios import GABINETES
from services.ui_theme import badges, empty_state, section_label, status_tone

STATUS_LABELS = {
    "PENDENTE": "Pendente",
    "CIENTE": "Ciente",
    "CONCLUIDO": "Concluído",
}


def _audit(store, principal, module, identifier, event, action, forwarding_id=None):
    audit_module = {
        "oficio_enviado": "oficios",
        "oficio_recebido": "oficios",
        "memorando": "memorandos",
        "representacao": "representacoes",
        "ouvidoria": "ouvidoria",
    }[module]
    registrar_evento(
        store,
        evento=event,
        modulo=audit_module,
        acao=action,
        principal=principal,
        entidade_tipo=module,
        entidade_id=identifier,
        detalhes={"encaminhamento_id": forwarding_id} if forwarding_id else None,
    )


def render_internal_collaboration(store, principal, module, identifier):
    """Render only inside an already opened record detail."""
    repository = InternalCollaborationStore(store)
    prefix = f"ic_{module}_{identifier}_"

    with st.expander("Notas internas", expanded=False):
        with st.form(prefix + "note_form", clear_on_submit=True):
            note = st.text_area(
                "Nova nota",
                max_chars=4000,
                placeholder="Registre uma observação interna.",
                key=prefix + "note_text",
            )
            add_note = st.form_submit_button("Adicionar nota")
        if add_note:
            try:
                repository.add_note(module, identifier, principal.id, note)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _audit(
                    store,
                    principal,
                    module,
                    identifier,
                    "NOTA_INTERNA_CRIADA",
                    "REGISTRAR",
                )
                st.success("Nota interna adicionada.")
        notes = repository.list_notes(module, identifier, principal.id)
        if not notes:
            empty_state("Nenhuma nota interna registrada.")
        for item in reversed(notes):
            st.markdown(
                f"**{item['autor_nome']}** · {format_local_short(item['criado_em'])}"
            )
            st.write(item["texto"])

    with st.expander("Encaminhar / Solicitar providência", expanded=False):
        users = repository.eligible_users(module, identifier, principal.id)
        user_map = {item["id"]: item for item in users}
        with st.form(prefix + "forward_form", clear_on_submit=True):
            recipient_id = st.selectbox(
                "Usuário destinatário (opcional)",
                list(user_map),
                index=None,
                placeholder="Selecione um usuário",
                format_func=lambda value: (
                    f"{user_map[value]['nome']} · {user_map[value]['email']}"
                    if value in user_map
                    else ""
                ),
                key=prefix + "recipient",
            )
            cabinet = st.selectbox(
                "Gabinete/unidade (opcional)",
                list(GABINETES),
                index=None,
                placeholder="Selecione um gabinete",
                key=prefix + "cabinet",
            )
            message = st.text_area(
                "Providência / mensagem",
                max_chars=2000,
                key=prefix + "message",
            )
            due = st.date_input(
                "Prazo opcional",
                value=None,
                format="DD/MM/YYYY",
                key=prefix + "due",
            )
            create = st.form_submit_button("Encaminhar")
        if create:
            try:
                forwarding_id = repository.create_forwarding(
                    module,
                    identifier,
                    principal.id,
                    message,
                    recipient_id=recipient_id,
                    cabinet=cabinet,
                    due=due.isoformat() if due else None,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                _audit(
                    store,
                    principal,
                    module,
                    identifier,
                    "ENCAMINHAMENTO_CRIADO",
                    "ENCAMINHAR",
                    forwarding_id,
                )
                st.success("Encaminhamento registrado.")

        section_label("Histórico")
        rows = repository.list_forwardings(module, identifier, principal.id)
        if not rows:
            empty_state("Nenhum encaminhamento registrado.")
        for item in rows:
            label = STATUS_LABELS[item["status"]]
            st.markdown(
                f"**{item['remetente_nome']}** · {format_local_short(item['criado_em'])} "
                + badges((label, status_tone(label))),
                unsafe_allow_html=True,
            )
            destination = " e ".join(
                value
                for value in (
                    item.get("destinatario_nome"),
                    item.get("destinatario_gabinete"),
                )
                if value
            )
            st.caption("Destinatário: " + destination)
            st.write(item["providencia"])
            if item.get("prazo"):
                st.caption(
                    "Prazo: "
                    + date.fromisoformat(item["prazo"]).strftime("%d/%m/%Y")
                )
            if item.get("ciente_em"):
                st.caption(
                    f"Ciência por {item['ciente_por_nome']} em "
                    f"{format_local_short(item['ciente_em'])}."
                )
            if item.get("concluido_em"):
                st.caption(
                    f"Conclusão por {item['concluido_por_nome']} em "
                    f"{format_local_short(item['concluido_em'])}."
                )
            if item["pode_agir"] and item["status"] != "CONCLUIDO":
                left, right = st.columns(2)
                if item["status"] == "PENDENTE" and left.button(
                    "Dar ciência", key=prefix + "ack_" + str(item["id"])
                ):
                    try:
                        repository.mark_acknowledged(item["id"], principal.id)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        _audit(
                            store,
                            principal,
                            module,
                            identifier,
                            "ENCAMINHAMENTO_CIENTE",
                            "CIENCIA",
                            item["id"],
                        )
                        st.rerun(scope="fragment")
                if right.button(
                    "Concluir providência", key=prefix + "done_" + str(item["id"])
                ):
                    try:
                        repository.conclude(item["id"], principal.id)
                    except ValueError as exc:
                        st.error(str(exc))
                    else:
                        _audit(
                            store,
                            principal,
                            module,
                            identifier,
                            "ENCAMINHAMENTO_CONCLUIDO",
                            "CONCLUIR",
                            item["id"],
                        )
                        st.rerun(scope="fragment")
