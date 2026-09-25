"""Supervised institutional e-mail notices. Protocol never sends by itself."""

from html import escape
import logging
import re

from database.notifications import NotificationsStore
from services.access import require_permission
from services.date_format import format_date_br
from services.email_transport import (
    NOT_CONFIGURED,
    SENDER_ADDRESS,
    SENDER_NAME,
    DeliveryRejected,
    DeliveryUncertain,
    NotConfigured,
    institutional_transport,
)

LOGGER = logging.getLogger("mpc.notifications")

EVENT_REPRESENTACAO_PROTOCOLADA = "REPRESENTACAO_PROTOCOLADA"
SUBSCRIPTION_PROTOCOLO = "PROTOCOLO_DE_REPRESENTACAO"
ENTITY_REPRESENTACAO = "representacao"
STATUS_LABELS = {
    "DRAFT": "Pendente de envio",
    "SENDING": "Envio iniciado, sem confirmação",
    "SENT": "Enviada",
    "FAILED": "Falha no envio",
}
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SECRET = re.compile(
    r"(?i)(bearer\s+\S+|private_key[\"'\s:=]+\S+|token[\"'\s:=]+\S+|secret[\"'\s:=]+\S+|password[\"'\s:=]+\S+|authorization[\"'\s:=]+\S+)"
)


def idempotency_key(representation_id):
    return f"representation:{representation_id}:protocolled"


def normalize_email(value):
    return str(value or "").strip().lower()


def email_valid(value):
    return bool(_EMAIL.match(normalize_email(value)))


def sanitize_error(message):
    text = _SECRET.sub("[omitido]", str(message or "Falha no envio."))
    text = " ".join(text.split())
    return (text or "Falha no envio.")[:300]


def _store(store):
    return NotificationsStore(store)


def _actor(principal):
    return getattr(principal, "email", None) or ""


def _audit(store, principal, evento, acao, record, extra=None, resultado="OK"):
    from services.audit import registrar_evento

    detalhes = {"evento_negocio": EVENT_REPRESENTACAO_PROTOCOLADA}
    if record and record.get("id") is not None:
        detalhes["representacao_id"] = record["id"]
    if record and record.get("numero_processo"):
        detalhes["numero_processo"] = record["numero_processo"]
    if extra:
        detalhes.update(extra)
    registrar_evento(
        store,
        evento=evento,
        modulo="representacoes",
        acao=acao,
        resultado=resultado,
        principal=principal,
        entidade_tipo=ENTITY_REPRESENTACAO,
        entidade_id=None if not record else record.get("id"),
        detalhes=detalhes,
    )


def notice_summary(store, record):
    """Status of one protocolled representation. No draft is created here."""
    if not record or not record.get("numero_processo"):
        return None
    saved = _store(store).get_by_key(idempotency_key(record["id"]))
    count = _store(store).count_active(SUBSCRIPTION_PROTOCOLO)
    subject = (saved or {}).get("assunto_snapshot") or ""
    changed = bool(saved and saved.get("status") == "SENT" and record["numero_processo"] not in subject)
    return {
        "status": None if saved is None else saved["status"],
        "label": "Pendente de envio" if saved is None else STATUS_LABELS.get(saved["status"], saved["status"]),
        "recipient_count": count if saved is None or saved["status"] != "SENT" else len(saved.get("destinatarios_snapshot") or []),
        "sent_at": None if saved is None else saved.get("enviado_em"),
        "error": None if saved is None else (saved.get("erro") or ""),
        "process_changed": changed,
        "notification_id": None if saved is None else saved["id"],
    }


def _require_protocol(record):
    if not record or not record.get("numero_processo"):
        raise ValueError("A comunicação só fica disponível depois do protocolo.")


def ensure_draft(store, record, principal):
    require_permission(principal, "representacoes")
    _require_protocol(record)
    before = _store(store).get_by_key(idempotency_key(record["id"]))
    saved = _store(store).ensure_draft(
        {
            "evento": EVENT_REPRESENTACAO_PROTOCOLADA,
            "entidade_tipo": ENTITY_REPRESENTACAO,
            "entidade_id": record["id"],
            "chave_idempotencia": idempotency_key(record["id"]),
            "criado_por": _actor(principal),
        }
    )
    if before is None:
        _audit(store, principal, "NOTIFICACAO_PREPARADA", "CRIAR", record, {"status": "DRAFT"})
    return saved


def list_recipients(store, *, active_only=False):
    return _store(store).recipients(SUBSCRIPTION_PROTOCOLO, active_only=active_only)


def assess_recipients(recipients):
    active = [item for item in recipients if item.get("ativo")]
    blockers = []
    seen = []
    missing = invalid = False
    for item in active:
        email = normalize_email(item.get("email"))
        if not email:
            missing = True
            continue
        if not email_valid(email):
            invalid = True
            continue
        seen.append(email)
    if not active:
        blockers.append("Não há destinatários ativos para esta comunicação.")
    if missing:
        blockers.append("Há destinatário ativo sem e-mail institucional.")
    if invalid:
        blockers.append("Há e-mail institucional inválido.")
    if len(seen) != len(set(seen)):
        blockers.append("Há e-mail institucional repetido.")
    return blockers


def compose(record):
    number = str(record.get("numero_processo") or "").strip()
    title = str(record.get("titulo") or "").strip()
    day = format_date_br(record.get("data_protocolo"), empty="")
    subject = "MPC-PB — Protocolo de Representação — Processo TC nº " + number
    lines = [
        "Prezados(as),",
        "",
        "Informamos que foi protocolada no Tribunal de Contas do Estado da Paraíba a representação abaixo identificada:",
        "",
        "Processo TC nº: " + number,
    ]
    if title:
        lines.append("Assunto: " + title)
    if day:
        lines.append("Data do protocolo: " + day)
    lines.extend(["", "Atenciosamente,", "", SENDER_NAME])
    text = "\n".join(lines)
    html_lines = ["<p>Prezados(as),</p>", "<p>Informamos que foi protocolada no Tribunal de Contas do Estado da Paraíba a representação abaixo identificada:</p>", "<p>Processo TC nº: " + escape(number) + "<br>"]
    if title:
        html_lines.append("Assunto: " + escape(title) + "<br>")
    if day:
        html_lines.append("Data do protocolo: " + escape(day))
    html_lines.append("</p><p>Atenciosamente,<br>" + escape(SENDER_NAME) + "</p>")
    return {"assunto": subject, "texto": text, "html": "".join(html_lines)}


def build_preview(store, record, principal):
    require_permission(principal, "representacoes")
    _require_protocol(record)
    saved = _store(store).get_by_key(idempotency_key(record["id"]))
    if saved and saved["status"] == "SENT":
        body = saved.get("corpo_snapshot") or {}
        return {
            "status": "SENT",
            "remetente_nome": SENDER_NAME,
            "remetente": SENDER_ADDRESS,
            "destinatarios": saved.get("destinatarios_snapshot") or [],
            "assunto": saved.get("assunto_snapshot") or "",
            "texto": body.get("texto") or "",
            "html": body.get("html") or "",
            "blockers": [],
            "sent_at": saved.get("enviado_em"),
            "error": "",
            "configured": True,
            "process_changed": record["numero_processo"] not in (saved.get("assunto_snapshot") or ""),
        }
    recipients = list_recipients(store, active_only=True)
    blockers = assess_recipients(recipients)
    message = compose(record)
    from services.email_transport import transport_available

    return {
        "status": "DRAFT" if saved is None else saved["status"],
        "remetente_nome": SENDER_NAME,
        "remetente": SENDER_ADDRESS,
        "destinatarios": [
            {
                "nome": item.get("nome") or "Sem nome",
                "email": normalize_email(item.get("email")),
                "cargo": item.get("cargo") or "",
            }
            for item in recipients
        ],
        "assunto": message["assunto"],
        "texto": message["texto"],
        "html": message["html"],
        "blockers": blockers,
        "sent_at": None,
        "error": "" if saved is None else (saved.get("erro") or ""),
        "configured": transport_available(),
        "process_changed": False,
    }


def _snapshot_message(preview):
    return {
        "remetente": f"{SENDER_NAME} <{SENDER_ADDRESS}>",
        "destinatarios": preview["destinatarios"],
        "assunto": preview["assunto"],
        "corpo": {"texto": preview["texto"], "html": preview["html"]},
        "to": [item["email"] for item in preview["destinatarios"]],
        "text": preview["texto"],
        "html": preview["html"],
        "subject": preview["assunto"],
    }


def confirm_send(store, record, principal, transport=None):
    """Send one collective message. A second call cannot claim the same notice."""
    require_permission(principal, "representacoes")
    require_permission(principal, "representacoes_enviar_comunicacao")
    _require_protocol(record)
    saved = ensure_draft(store, record, principal)
    if saved["status"] == "SENT":
        raise ValueError("Comunicação referente ao protocolo já enviada.")
    if saved["status"] == "SENDING":
        raise ValueError(
            "O envio foi iniciado e não houve confirmação. Não será repetido automaticamente."
        )
    preview = build_preview(store, record, principal)
    if preview["blockers"]:
        raise ValueError(" ".join(preview["blockers"]))
    if not preview["destinatarios"]:
        raise ValueError("Não há destinatários ativos para esta comunicação.")
    if transport is None:
        try:
            transport = institutional_transport()
        except NotConfigured:
            raise NotConfigured(NOT_CONFIGURED) from None
    message = _snapshot_message(preview)
    claimed = _store(store).claim(saved["id"], message)
    if claimed is None:
        raise ValueError("Outra execução já assumiu esta comunicação.")
    _audit(
        store,
        principal,
        "NOTIFICACAO_ENVIO_TENTATIVA",
        "ENVIAR",
        record,
        {"status": "SENDING", "destinatarios": len(message["destinatarios"])},
    )
    try:
        provider_id = transport.send(message)
    except DeliveryUncertain:
        _audit(
            store,
            principal,
            "NOTIFICACAO_ENVIO_INCERTO",
            "ENVIAR",
            record,
            {"status": "SENDING"},
            resultado="ATENCAO",
        )
        raise ValueError(
            "Não foi possível confirmar se o provedor aceitou a mensagem. O envio não será repetido automaticamente."
        ) from None
    except DeliveryRejected as exc:
        safe = sanitize_error(exc)
        _store(store).mark_failed(saved["id"], safe)
        _audit(
            store,
            principal,
            "NOTIFICACAO_FALHOU",
            "ENVIAR",
            record,
            {"status": "FAILED"},
            resultado="ERRO",
        )
        raise ValueError("O envio não foi concluído. " + safe) from None
    except Exception:
        LOGGER.exception("Falha incerta ao enviar comunicação institucional")
        _audit(
            store,
            principal,
            "NOTIFICACAO_ENVIO_INCERTO",
            "ENVIAR",
            record,
            {"status": "SENDING"},
            resultado="ATENCAO",
        )
        raise ValueError(
            "Não foi possível confirmar se o provedor aceitou a mensagem. O envio não será repetido automaticamente."
        ) from None
    sent = _store(store).mark_sent(saved["id"], provider_id)
    if sent is None:
        raise ValueError(
            "Não foi possível confirmar se o provedor aceitou a mensagem. O envio não será repetido automaticamente."
        )
    _audit(
        store,
        principal,
        "NOTIFICACAO_ENVIADA",
        "ENVIAR",
        record,
        {
            "status": "SENT",
            "destinatarios": len(claimed.get("destinatarios_snapshot") or []),
            "provedor_mensagem_id": provider_id or "",
        },
    )
    return sent


def save_recipient(store, principal, *, membro_tipo, membro_id, email, ativo):
    require_permission(principal, "admin")
    require_permission(principal, "comunicacoes_configurar_destinatarios")
    if membro_tipo not in ("PROCURADOR", "SERVIDOR"):
        raise ValueError("Tipo de destinatário inválido.")
    normalized = normalize_email(email)
    if ativo and normalized and not email_valid(normalized):
        raise ValueError("Informe um e-mail institucional válido.")
    if ativo and normalized:
        current = list_recipients(store, active_only=True)
        for item in current:
            same = item["membro_tipo"] == membro_tipo and int(item["membro_id"]) == int(membro_id)
            if not same and item.get("email") == normalized:
                raise ValueError("Este e-mail já está atribuído a outro destinatário ativo.")
    saved = _store(store).save_recipient(
        SUBSCRIPTION_PROTOCOLO, membro_tipo, membro_id, normalized, bool(ativo)
    )
    from services.audit import registrar_evento

    registrar_evento(
        store,
        evento="NOTIFICACAO_DESTINATARIO",
        modulo="admin",
        acao="ALTERAR",
        principal=principal,
        entidade_tipo="notificacao_destinatario",
        entidade_id=saved.get("id"),
        detalhes={
            "evento_negocio": SUBSCRIPTION_PROTOCOLO,
            "membro_tipo": membro_tipo,
            "membro_id": int(membro_id),
            "ativo": bool(ativo),
        },
    )
    return saved
