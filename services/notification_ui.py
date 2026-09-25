"""Supervised preview for protocol notices and the administrative recipient list."""

import logging

import streamlit as st

from services.access import has_permission
from services.email_transport import NOT_CONFIGURED, NotConfigured
from services.ui_theme import definition_block, section_label

LOGGER = logging.getLogger("mpc.notifications.ui")
_PREVIEW = "representacoes_mail_preview"


def _when(value):
    from services.audit import format_local_short

    text = format_local_short(value)
    if " " not in text:
        return text
    day, clock = text.split(" ", 1)
    return day + " às " + clock


def render_protocol_notice(store, principal, record):
    from services.notifications import (
        build_preview,
        confirm_send,
        ensure_draft,
        notice_summary,
    )

    summary = notice_summary(store, record)
    if summary is None:
        return
    section_label("Comunicação do protocolo")
    if summary["status"] == "SENT":
        st.success("Enviada em " + _when(summary["sent_at"]) + ".")
        st.caption("Destinatários: " + str(summary["recipient_count"]))
        if summary["process_changed"]:
            st.info("Comunicação referente ao protocolo já enviada.")
    elif summary["status"] == "SENDING":
        st.warning(
            "O envio foi iniciado e não houve confirmação. Não será repetido automaticamente."
        )
    elif summary["status"] == "FAILED":
        st.error("O envio não foi concluído.")
        if summary["error"]:
            st.caption(summary["error"])
        st.caption("Destinatários configurados: " + str(summary["recipient_count"]))
    else:
        st.caption("Status: Pendente de envio")
        st.caption("Destinatários configurados: " + str(summary["recipient_count"]))
    open_preview = st.session_state.get(_PREVIEW) == record["id"]
    if summary["status"] == "SENDING":
        return
    label = "Visualizar comunicação enviada" if summary["status"] == "SENT" else "Visualizar e enviar e-mail"
    can_send_notice = has_permission(principal, "representacoes_enviar_comunicacao")
    if not open_preview and st.button(label, key="rep_mail_open_" + str(record["id"])):
        if summary["status"] != "SENT":
            ensure_draft(store, record, principal)
        st.session_state[_PREVIEW] = record["id"]
        st.rerun()
    if not open_preview:
        return
    preview = build_preview(store, record, principal)
    definition_block(
        "Remetente",
        (("Nome", preview["remetente_nome"]), ("E-mail", preview["remetente"])),
    )
    names = [
        (item.get("nome") or "Sem nome")
        + ((" — " + item["cargo"]) if item.get("cargo") else "")
        + " <" + (item.get("email") or "sem e-mail") + ">"
        for item in preview["destinatarios"]
    ]
    st.markdown("**Destinatários**")
    st.text("\n".join(names) if names else "Nenhum destinatário ativo.")
    st.text_input("Assunto", preview["assunto"], disabled=True, key="rep_mail_subject_" + str(record["id"]))
    st.text_area("Mensagem", preview["texto"], disabled=True, height=220, key="rep_mail_body_" + str(record["id"]))
    for blocker in preview["blockers"]:
        st.error(blocker)
    if preview["status"] != "SENT" and not preview["configured"]:
        st.warning(NOT_CONFIGURED)
    if preview["process_changed"]:
        st.info("Comunicação referente ao protocolo já enviada.")
    if st.button("Fechar prévia", key="rep_mail_close_" + str(record["id"])):
        st.session_state.pop(_PREVIEW, None)
        st.rerun()
    can_send = (
        preview["status"] in (None, "DRAFT", "FAILED")
        and not preview["blockers"]
        and preview["configured"]
        and preview["destinatarios"]
    )
    if can_send and can_send_notice and st.button("Confirmar envio", type="primary", key="rep_mail_send_" + str(record["id"])):
        try:
            confirm_send(store, record, principal)
        except NotConfigured as exc:
            st.warning(str(exc))
            return
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state.pop(_PREVIEW, None)
        st.session_state["representacoes_message"] = "Comunicação enviada."
        st.rerun()


def render_admin_recipients(store, principal):
    from database.memorandos import MemorandosStore
    from services.notifications import list_recipients

    st.subheader("Comunicação de protocolo")
    st.caption(
        "Destinatários do aviso de protocolo de representação. "
        "O remetente institucional permanece mpc@tce.pb.gov.br."
    )
    current = list_recipients(store)
    if current:
        st.dataframe(
            [
                {
                    "Nome": item.get("nome") or "—",
                    "Função": item.get("cargo") or "—",
                    "E-mail": item.get("email") or "—",
                    "Recebe aviso": "Sim" if item.get("ativo") else "Não",
                }
                for item in current
            ],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.caption("Nenhum destinatário configurado.")
    people = []
    for member in store.catalog("procuradores"):
        people.append(("PROCURADOR", member["id"], member["nome"], member.get("funcao") or member.get("cargo_base") or ""))
    for server in MemorandosStore(store).all_servers(include_inactive=False):
        people.append(("SERVIDOR", server["id"], server["nome"], server.get("cargo") or ""))
    if people:
        _recipient_form(store, principal, people, current)
    render_gmail_test(store, principal)


def _recipient_form(store, principal, people, current):
    from services.notifications import save_recipient

    if not has_permission(principal, "comunicacoes_configurar_destinatarios"):
        st.caption("Seu acesso permite consultar os destinatários, mas não alterá-los.")
        return
    labels = {
        index: f"{'Procurador' if kind == 'PROCURADOR' else 'Servidor'} — {name}"
        for index, (kind, _identifier, name, _role) in enumerate(people)
    }
    chosen = st.selectbox("Pessoa", list(labels), format_func=labels.get, key="mail_recipient_person")
    kind, identifier, _name, _role = people[chosen]
    existing = next(
        (item for item in current if item["membro_tipo"] == kind and int(item["membro_id"]) == int(identifier)),
        None,
    )
    email_key = "mail_recipient_email_" + kind + "_" + str(identifier)
    active_key = "mail_recipient_active_" + kind + "_" + str(identifier)
    if email_key not in st.session_state:
        st.session_state[email_key] = "" if existing is None else existing.get("email") or ""
    if active_key not in st.session_state:
        st.session_state[active_key] = True if existing is None else bool(existing.get("ativo"))
    email = st.text_input("E-mail institucional", key=email_key)
    active = st.checkbox("Recebe aviso de protocolo de representação", key=active_key)
    if st.button("Salvar destinatário", type="primary", key="mail_recipient_save"):
        try:
            save_recipient(
                store,
                principal,
                membro_tipo=kind,
                membro_id=identifier,
                email=email,
                ativo=active,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state["sistema_mail_message"] = "Destinatário atualizado."
            st.rerun()
    if message := st.session_state.pop("sistema_mail_message", None):
        st.success(message)


def render_gmail_test(store, principal):
    from services.email_transport import SENDER_ADDRESS, SENDER_NAME, NotConfigured, gmail_configuration
    from services.gmail_test import institutional_recipient, send_institutional_test

    section_label("Teste de envio institucional")
    status = gmail_configuration()
    method = "OAuth 2.0" if status["mode"] == "user_oauth" else "Conta de serviço" if status["mode"] == "service_account" else "OAuth 2.0"
    definition_block(
        "Remetente",
        (("Nome", SENDER_NAME), ("E-mail", SENDER_ADDRESS), ("Método", method)),
    )
    if status["configured"]:
        st.caption("Status: Configurado")
    else:
        st.caption("Status: Não configurado")
    if status["problem"] == "sender":
        st.error("O remetente institucional deve ser mpc@tce.pb.gov.br.")
    allowed = has_permission(principal, "comunicacoes_enviar_teste")
    recipient = st.text_input("Destinatário de teste", key="gmail_test_recipient", disabled=not allowed)
    pending = st.session_state.get("gmail_test_pending")
    if st.button("Enviar e-mail de teste", key="gmail_test_prepare", disabled=not allowed):
        try:
            st.session_state["gmail_test_pending"] = institutional_recipient(recipient)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.rerun()
    if not pending:
        return
    st.warning("Enviar um e-mail de teste de " + SENDER_ADDRESS + " para " + pending + "?")
    if st.button("Cancelar teste", key="gmail_test_cancel"):
        st.session_state.pop("gmail_test_pending", None)
        st.rerun()
    if allowed and st.button("Confirmar envio do teste", type="primary", key="gmail_test_confirm"):
        try:
            send_institutional_test(store, principal, pending)
        except NotConfigured as exc:
            st.warning(str(exc))
            return
        except ValueError as exc:
            st.error(str(exc))
            return
        except Exception as exc:
            st.error(str(exc))
            detail = getattr(exc, "error_code", None)
            http_status = getattr(exc, "http_status", None)
            description = getattr(exc, "detail", "")
            bits = []
            if http_status:
                bits.append("HTTP " + str(http_status))
            if detail:
                bits.append(str(detail))
            if description:
                bits.append(description)
            if bits:
                st.caption(" · ".join(bits))
            return
        st.session_state.pop("gmail_test_pending", None)
        st.success("Teste enviado com sucesso.")
