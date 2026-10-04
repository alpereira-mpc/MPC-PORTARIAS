"""Distribution tab for one institutional report version.

Sending happens only on the explicit confirmation button. A rerun, a refresh
or the first click that opens the preview never calls Gmail.
"""

import logging
import uuid

import streamlit as st

from services.date_format import format_datetime_br
from services.email_transport import SENDER_ADDRESS
from services.institutional_report_distribution import (
    DRAFT_MESSAGE,
    MISSING_PDF_MESSAGE,
    SEND_ERROR,
    UNCERTAIN_MESSAGE,
    assess_recipients,
    confirm_distribution,
    default_body,
    default_subject,
    list_report_distributions,
    official_pdf_metadata,
    retry_distribution,
)
from services.relatorios_ui import (
    _institutional_period_label,
    _institutional_status_label,
    _institutional_title,
)


LOGGER = logging.getLogger(__name__)

DISTRIBUTION_STATUS = {
    "PREPARADO": "Preparado",
    "ENVIANDO": "Em andamento",
    "ENVIADO": "Enviado",
    "PARCIAL": "Parcial",
    "FALHA": "Falha",
}
RECIPIENT_STATUS = {
    "PENDENTE": "Pendente",
    "ENVIADO": "Enviado",
    "FALHA": "Falha",
}


def render_distribution(store, principal, report):
    try:
        _panel(store, principal, report)
    except Exception:
        LOGGER.exception(
            "Falha ao exibir a distribuição do relatório institucional %s",
            report.get("id"),
        )
        st.error(SEND_ERROR)


def _panel(store, principal, report):
    _clear_foreign_state(report)
    _show_result(report)
    snapshot = report.get("snapshot_dados") or {}
    administrator = bool(getattr(principal, "administrator", False))
    st.markdown("##### Distribuição")
    st.caption(
        f"{_institutional_title(snapshot)} · "
        f"{_institutional_period_label(snapshot)} · "
        f"Versão {report.get('versao')} · "
        f"{_institutional_status_label(report.get('status'))} · "
        f"Data de corte: {format_datetime_br(report.get('data_corte'))}"
    )
    history = list_report_distributions(store, report["id"])
    if report.get("status") not in ("FINALIZADO", "ENVIADO"):
        st.info(DRAFT_MESSAGE)
        _render_history(history)
        return
    if report.get("status") == "ENVIADO":
        st.warning("Esta versão já foi distribuída anteriormente.")
        if history:
            latest = history[0]
            st.caption(
                "Último envio: "
                f"{_when(latest.get('enviado_em') or latest.get('criado_em'))} · "
                f"{DISTRIBUTION_STATUS.get(latest.get('status_envio'), latest.get('status_envio'))}"
            )
    metadata = official_pdf_metadata(store, report["id"])
    if not metadata:
        st.warning(MISSING_PDF_MESSAGE)
        if administrator and st.button(
            "Gerar PDF oficial",
            key=f"institutional_distribution_generate_{report['id']}",
        ):
            _generate_official_pdf(store, principal, report)
        _render_history(history)
        return
    st.caption(
        f"PDF: {metadata['nome_arquivo']} · "
        f"{_format_size(metadata['tamanho'])} · "
        f"Versão {report.get('versao')}"
    )
    st.caption(f"SHA-256: {metadata['sha256']}")
    st.caption(f"Remetente: {SENDER_ADDRESS}")
    _render_download(store, report)
    assessed = assess_recipients(store)
    st.markdown("##### Destinatários")
    if not assessed["lista"]:
        st.caption("Nenhum Procurador ativo encontrado.")
    for person in assessed["lista"]:
        mark = "✓" if person["valido"] else "•"
        address = person["email"] or "e-mail ausente"
        st.write(f"{mark} {person['procurador']} — {address}")
    for blocker in assessed["bloqueios"]:
        st.error(blocker)
    if administrator:
        _render_composer(store, principal, report, assessed, history, metadata)
    else:
        st.caption(default_subject(report))
        st.text(default_body(report))
    _render_history(history)


def _render_composer(store, principal, report, assessed, history, metadata):
    subject_key = f"institutional_distribution_subject_{report['id']}"
    body_key = f"institutional_distribution_body_{report['id']}"
    if subject_key not in st.session_state:
        st.session_state[subject_key] = default_subject(report)
    if body_key not in st.session_state:
        st.session_state[body_key] = default_body(report)
    st.text_input("Assunto", key=subject_key)
    st.text_area("Mensagem", key=body_key, height=220)
    pending = _pending_operation(history)
    confirm = st.session_state.get("institutional_distribution_confirm")
    if confirm and int(confirm.get("report_id") or 0) == int(report["id"]):
        _render_confirmation(
            store, principal, report, confirm, subject_key, body_key, metadata
        )
        return
    if pending:
        count = sum(
            1
            for person in pending["destinatarios"]
            if person["status"] in ("FALHA", "PENDENTE")
        )
        if st.button(
            "Tentar novamente apenas os pendentes",
            key=f"institutional_distribution_retry_{report['id']}",
        ):
            _open_confirmation(report, mode="retry", source_id=pending["id"])
        st.caption(f"Destinatários ainda pendentes nesta operação: {count}.")
        return
    if assessed["bloqueios"]:
        return
    label = (
        "Preparar reenvio"
        if report.get("status") == "ENVIADO"
        else "Enviar aos Procuradores"
    )
    if st.button(label, key=f"institutional_distribution_open_{report['id']}"):
        _open_confirmation(report, mode="send", source_id=None)


def _render_confirmation(
    store, principal, report, confirm, subject_key, body_key, metadata
):
    if int(confirm.get("versao") or 0) != int(report.get("versao") or 0):
        st.session_state.pop("institutional_distribution_confirm", None)
        st.warning("A versão selecionada mudou. Prepare o envio novamente.")
        return
    period = _institutional_period_label(report.get("snapshot_dados") or {})
    if confirm.get("mode") == "retry":
        st.warning(
            "Confirmar nova tentativa apenas para os Procuradores que ainda "
            "não receberam esta distribuição?"
        )
    else:
        st.warning("Confirmar envio deste relatório para todos os Procuradores ativos?")
    st.caption(
        f"Anexo: {metadata['nome_arquivo']} · "
        f"Versão {report.get('versao')} · {period}"
    )
    if st.button(
        "Confirmar envio",
        key=f"institutional_distribution_confirm_button_{report['id']}",
    ):
        token = confirm["token"]
        mode = confirm.get("mode")
        source_id = confirm.get("source_id")
        subject = st.session_state.get(subject_key, "")
        body = st.session_state.get(body_key, "")
        st.session_state.pop("institutional_distribution_confirm", None)
        try:
            if mode == "retry":
                outcome = retry_distribution(
                    store,
                    report,
                    principal,
                    source_id,
                    subject=subject,
                    body=body,
                    idempotency_key=token,
                )
            else:
                outcome = confirm_distribution(
                    store,
                    report,
                    principal,
                    subject=subject,
                    body=body,
                    idempotency_key=token,
                )
        except ValueError as exc:
            st.error(str(exc))
        except Exception:
            LOGGER.exception(
                "Falha ao enviar o relatório institucional %s versão %s",
                report.get("id"),
                report.get("versao"),
            )
            st.error(SEND_ERROR)
        else:
            st.session_state["institutional_distribution_result"] = {
                "report_id": report["id"],
                "status": outcome["status_envio"],
                "erro": outcome.get("erro_resumo") or "",
                "recebidos": [
                    person["procurador"]
                    for person in outcome["destinatarios"]
                    if person["status"] == "ENVIADO"
                ],
                "pendentes": [
                    f"{person['procurador']} — {person.get('erro') or RECIPIENT_STATUS.get(person['status'], person['status'])}"
                    for person in outcome["destinatarios"]
                    if person["status"] != "ENVIADO"
                ],
            }
            st.rerun()
    if st.button(
        "Cancelar",
        key=f"institutional_distribution_cancel_{report['id']}",
    ):
        st.session_state.pop("institutional_distribution_confirm", None)
        st.rerun()


def _open_confirmation(report, *, mode, source_id):
    st.session_state["institutional_distribution_confirm"] = {
        "report_id": int(report["id"]),
        "versao": int(report["versao"]),
        "token": str(uuid.uuid4()),
        "mode": mode,
        "source_id": source_id,
    }
    st.rerun()


def _generate_official_pdf(store, principal, report):
    try:
        from services.institutional_report_pdf import deliver_institutional_pdf

        deliver_institutional_pdf(store, report, principal, official=True)
    except Exception:
        LOGGER.exception(
            "Falha ao gerar o PDF oficial do relatório institucional %s",
            report.get("id"),
        )
        st.error("Não foi possível gerar o PDF desta versão no momento.")
    else:
        st.rerun()


def _render_download(store, report):
    signature = f"{report.get('id')}:{report.get('versao')}"
    ready = st.session_state.get("institutional_distribution_pdf")
    if ready and ready.get("signature") != signature:
        st.session_state.pop("institutional_distribution_pdf", None)
        ready = None
    if ready and ready.get("signature") == signature:
        st.download_button(
            "Baixar PDF",
            ready["data"],
            ready["name"],
            mime="application/pdf",
            key=f"institutional_distribution_download_{report['id']}",
        )
        return
    if st.button(
        "Baixar PDF",
        key=f"institutional_distribution_prepare_{report['id']}",
    ):
        try:
            from database.institutional_reports import InstitutionalReportsStore

            artifact = InstitutionalReportsStore(store).pdf_artifact(report["id"])
        except Exception:
            LOGGER.exception(
                "Falha ao preparar o download do relatório institucional %s",
                report.get("id"),
            )
            st.error(SEND_ERROR)
            return
        if not artifact or not artifact.get("conteudo"):
            st.error(MISSING_PDF_MESSAGE)
            return
        st.session_state["institutional_distribution_pdf"] = {
            "signature": signature,
            "data": bytes(artifact["conteudo"]),
            "name": artifact["nome_arquivo"],
        }
        st.rerun()


def _render_history(history):
    st.markdown("##### Histórico de distribuição")
    if not history:
        st.caption("Nenhuma distribuição registrada para esta versão.")
        return
    for item in history:
        moment = _when(item.get("enviado_em") or item.get("criado_em"))
        status = DISTRIBUTION_STATUS.get(
            item.get("status_envio"), item.get("status_envio")
        )
        st.markdown(f"**{moment}**")
        st.caption(
            f"Enviado por: {item.get('enviado_por') or item.get('criado_por') or '—'} · "
            f"Remetente: {item.get('remetente')} · "
            f"Destinatários: {len(item.get('destinatarios') or [])} · "
            f"Resultado: {status}"
        )
        st.text(f"Assunto: {item.get('assunto') or ''}")
        st.caption(f"Anexo: {item.get('pdf_nome')}")
        with st.expander(f"Detalhe do envio {moment}"):
            st.caption(f"SHA-256: {item.get('pdf_sha256')}")
            st.caption(f"Tamanho: {_format_size(item.get('pdf_tamanho'))}")
            if item.get("corpo"):
                st.text(item["corpo"])
            if item.get("erro_resumo"):
                st.caption(item["erro_resumo"])
            for person in item.get("destinatarios") or []:
                bits = [
                    person.get("procurador") or "—",
                    person.get("email") or "—",
                    RECIPIENT_STATUS.get(person.get("status"), person.get("status")),
                ]
                if person.get("provedor_mensagem_id"):
                    bits.append(person["provedor_mensagem_id"])
                if person.get("erro"):
                    bits.append(person["erro"])
                st.text(" — ".join(bits))


def _show_result(report):
    result = st.session_state.get("institutional_distribution_result")
    if not result:
        return
    if int(result.get("report_id") or 0) != int(report["id"]):
        st.session_state.pop("institutional_distribution_result", None)
        return
    st.session_state.pop("institutional_distribution_result", None)
    status = result.get("status")
    if status == "ENVIADO":
        st.success("Relatório distribuído aos Procuradores ativos.")
    elif status == "PARCIAL":
        st.warning(
            "A distribuição foi parcial. Quem já recebeu o PDF não será incluído "
            "na nova tentativa."
        )
    elif status == "ENVIANDO":
        st.warning(UNCERTAIN_MESSAGE)
    else:
        st.error(result.get("erro") or SEND_ERROR)
    if result.get("erro") and status != "FALHA":
        st.caption(result["erro"])
    if result.get("recebidos"):
        st.caption("Receberam: " + ", ".join(result["recebidos"]) + ".")
    if result.get("pendentes"):
        st.caption("Ainda não receberam: " + "; ".join(result["pendentes"]) + ".")


def _clear_foreign_state(report):
    confirm = st.session_state.get("institutional_distribution_confirm")
    if confirm and int(confirm.get("report_id") or 0) != int(report["id"]):
        st.session_state.pop("institutional_distribution_confirm", None)
    result = st.session_state.get("institutional_distribution_result")
    if result and int(result.get("report_id") or 0) != int(report["id"]):
        st.session_state.pop("institutional_distribution_result", None)


def _pending_operation(history):
    if not history:
        return None
    latest = history[0]
    if latest.get("status_envio") not in ("PARCIAL", "FALHA", "ENVIANDO"):
        return None
    if any(
        person["status"] in ("FALHA", "PENDENTE")
        for person in latest.get("destinatarios") or []
    ):
        return latest
    return None


def _when(value):
    text = format_datetime_br(value)
    if " " in text:
        day, hour = text.split(" ", 1)
        return f"{day} às {hour}"
    return text


def _format_size(size):
    size = int(size or 0)
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB".replace(".", ",")
    return f"{size / (1024 * 1024):.1f} MB".replace(".", ",")
