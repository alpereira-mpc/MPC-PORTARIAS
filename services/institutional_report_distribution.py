"""Distribute one official institutional PDF to the active Procuradores.

The Representações notice sends a single collective message and has no file.
This flow keeps that same sender, Gmail transport, error classes and audit, and
sends one message per Procurador so a partial failure can be retried without
delivering the PDF twice to someone who already received it.
"""

import hashlib
import logging
import time

from database.institutional_reports import InstitutionalReportsStore
from database.notifications import NotificationsStore
from document_generator.institutional_report_pdf import period_label
from services.email_transport import (
    NOT_CONFIGURED,
    SENDER_ADDRESS,
    SENDER_NAME,
    DeliveryRejected,
    DeliveryUncertain,
    NotConfigured,
    institutional_transport,
)
from services.access import has_permission
from services.notifications import (
    SUBSCRIPTION_PROTOCOLO,
    email_valid,
    normalize_email,
    sanitize_error,
)


LOGGER = logging.getLogger(__name__)

# Gmail accepts about 25 MB for the encoded message. Stay under that.
MAX_PDF_BYTES = 20 * 1024 * 1024
SENDABLE_STATUSES = frozenset({"FINALIZADO", "ENVIADO"})

DRAFT_MESSAGE = "Finalize esta versão antes de distribuí-la por e-mail."
MISSING_PDF_MESSAGE = "O PDF oficial desta versão ainda não foi gerado."
HASH_MESSAGE = (
    "O PDF oficial desta versão não confere com o hash armazenado. "
    "O envio foi bloqueado."
)
SIGNATURE_MESSAGE = (
    "O PDF oficial desta versão não possui assinatura válida. O envio foi bloqueado."
)
EMPTY_PDF_MESSAGE = "O PDF oficial desta versão está vazio. O envio foi bloqueado."
SIZE_MESSAGE = (
    "O PDF oficial excede o limite de anexo do envio institucional. "
    "O envio foi bloqueado."
)
CHANGED_PDF_MESSAGE = (
    "O PDF oficial não corresponde ao documento já distribuído desta versão. "
    "O envio foi bloqueado."
)
SEND_ERROR = "Não foi possível concluir o envio deste relatório."
UNCERTAIN_MESSAGE = (
    "O provedor não confirmou o envio. A operação não será repetida automaticamente."
)
MISSING_EMAIL_PREFIX = (
    "Não é possível concluir a distribuição. Os seguintes Procuradores ativos "
    "não possuem e-mail institucional válido:"
)
DUPLICATE_EMAIL_PREFIX = (
    "Não é possível concluir a distribuição. Há e-mail institucional repetido "
    "entre Procuradores ativos:"
)
PERMISSION_MESSAGE = "Você não tem permissão para distribuir este relatório."


def default_subject(report):
    """Institutional subject from the frozen period. The month is never fixed."""
    snapshot = _snapshot(report)
    metadata = snapshot.get("metadados") or {}
    annual = metadata.get("tipo") == "ANUAL"
    if annual and not metadata.get("periodo_parcial"):
        period = str(metadata.get("ano"))
    elif annual:
        period = period_label(snapshot)
    else:
        period = period_label(snapshot).replace(" trimestre ", " Trimestre ")
    title = (
        "Relatório Anual de Produção do MPC-PB"
        if annual
        else "Relatório Trimestral de Produção do MPC-PB"
    )
    return f"{title} — {period}"


def default_body(report):
    """Deterministic message. No model is asked to write it."""
    snapshot = _snapshot(report)
    metadata = snapshot.get("metadados") or {}
    annual = metadata.get("tipo") == "ANUAL"
    title = (
        "Relatório Anual de Produção" if annual else "Relatório Trimestral de Produção"
    )
    if annual and not metadata.get("periodo_parcial"):
        scope = f"referente ao ano de {metadata.get('ano')}"
    else:
        label = period_label(snapshot)
        scope = "referente ao " + (label[:1].lower() + label[1:] if label else "")
    return (
        "Prezadas(os) Procuradoras(es),\n\n"
        "Encaminha-se, para conhecimento, o "
        f"{title} do {SENDER_NAME}, {scope}.\n\n"
        "O documento segue anexo.\n\n"
        "Atenciosamente,\n\n"
        f"{SENDER_NAME}"
    )


def assess_recipients(store):
    """Active Procuradores and the institutional address already used for notices."""
    registered = {
        int(item["membro_id"]): item
        for item in NotificationsStore(store).recipients(
            SUBSCRIPTION_PROTOCOLO, active_only=False
        )
        if item.get("membro_tipo") == "PROCURADOR"
    }
    people = [person for person in store.catalog("procuradores") if person.get("ativo")]
    people.sort(
        key=lambda person: (
            (person.get("nome") or "").casefold(),
            int(person["id"]),
        )
    )
    rows = []
    for person in people:
        name = (person.get("nome") or "").strip() or "Sem nome"
        email = normalize_email((registered.get(int(person["id"])) or {}).get("email"))
        rows.append(
            {
                "procurador_id": int(person["id"]),
                "procurador": name,
                "email": email,
                "valido": bool(email) and email_valid(email),
            }
        )
    counts = {}
    for item in rows:
        if item["valido"]:
            counts[item["email"]] = counts.get(item["email"], 0) + 1
    missing = []
    duplicated = []
    ready = []
    listing = []
    for item in rows:
        entry = dict(item)
        repeated = item["valido"] and counts[item["email"]] > 1
        if not item["valido"]:
            entry["motivo"] = "ausente"
            missing.append(item["procurador"])
        elif repeated:
            entry["valido"] = False
            entry["motivo"] = "repetido"
            duplicated.append(item["procurador"])
        else:
            entry["motivo"] = ""
            ready.append(
                {
                    "procurador_id": item["procurador_id"],
                    "procurador": item["procurador"],
                    "email": item["email"],
                }
            )
        listing.append(entry)
    blockers = []
    if not people:
        blockers.append(
            "Não é possível concluir a distribuição. Não há Procuradores ativos."
        )
    if missing:
        blockers.append(f"{MISSING_EMAIL_PREFIX} {', '.join(missing)}.")
    if duplicated:
        blockers.append(f"{DUPLICATE_EMAIL_PREFIX} {', '.join(duplicated)}.")
    return {
        "destinatarios": [] if blockers else ready,
        "lista": listing,
        "bloqueios": blockers,
    }


def confirm_distribution(
    store, report, principal, *, subject, body, idempotency_key, transport=None
):
    """Send the persisted PDF after an explicit confirmation key."""
    return _distribute(
        store,
        report,
        principal,
        subject=subject,
        body=body,
        idempotency_key=idempotency_key,
        transport=transport,
        recipients=None,
        origem_id=None,
    )


def retry_distribution(
    store,
    report,
    principal,
    source_id,
    *,
    subject,
    body,
    idempotency_key,
    transport=None,
):
    """Send again only to recipients still pending on that operation."""
    _require_sender(principal)
    repository = InstitutionalReportsStore(store)
    source = repository.distribution(source_id)
    if not source or int(source["relatorio_id"]) != int(report["id"]):
        raise ValueError("Distribuição não encontrada para esta versão.")
    pending = [
        {
            "procurador_id": person.get("procurador_id"),
            "procurador": person["procurador"],
            "email": person["email"],
        }
        for person in source["destinatarios"]
        if person["status"] in ("FALHA", "PENDENTE")
    ]
    if not pending:
        raise ValueError("Não há destinatários pendentes nesta distribuição.")
    return _distribute(
        store,
        report,
        principal,
        subject=subject,
        body=body,
        idempotency_key=idempotency_key,
        transport=transport,
        recipients=pending,
        origem_id=source["id"],
    )


def list_report_distributions(store, relatorio_id):
    return InstitutionalReportsStore(store).list_distributions(relatorio_id)


def official_pdf_metadata(store, relatorio_id):
    return InstitutionalReportsStore(store).pdf_metadata(relatorio_id)


def _distribute(
    store,
    report,
    principal,
    *,
    subject,
    body,
    idempotency_key,
    transport,
    recipients,
    origem_id,
):
    _require_sender(principal)
    key = str(idempotency_key or "").strip()
    if not key:
        raise ValueError("Confirmação de envio inválida.")
    repository = InstitutionalReportsStore(store)
    existing = repository.distribution_by_key(key)
    if existing and int(existing["relatorio_id"]) != int(report["id"]):
        raise ValueError("Confirmação de envio inválida.")
    if existing and existing["status_envio"] != "PREPARADO":
        return existing
    current = _current_report(repository, report)
    subject = str(subject or "").strip()
    body = str(body or "").strip()
    if not subject or not body:
        raise ValueError("Informe o assunto e o corpo da mensagem.")
    if recipients is None:
        assessed = assess_recipients(store)
        if assessed["bloqueios"]:
            LOGGER.warning(
                "Distribuição bloqueada do relatório %s: %s",
                current.get("id"),
                " ".join(assessed["bloqueios"]),
            )
            _audit(
                store,
                principal,
                "RELATORIO_DISTRIBUICAO_BLOQUEADA",
                "Bloqueou a distribuição do relatório institucional",
                current,
                {"status_envio": "BLOQUEADO"},
                resultado="ERRO",
                extra={"motivo": "destinatarios"},
            )
            raise ValueError(" ".join(assessed["bloqueios"]))
        recipients = assessed["destinatarios"]
    if not recipients:
        raise ValueError("Não há destinatários para esta distribuição.")
    artifact = _validated_artifact(store, repository, current, principal)
    previous = repository.distribution_hashes(current["id"])
    if any(item != artifact["sha256"] for item in previous):
        _block(store, principal, current, artifact, "pdf_alterado")
        raise ValueError(CHANGED_PDF_MESSAGE)
    if existing is None:
        existing = repository.begin_distribution(
            {
                "relatorio_id": current["id"],
                "origem_envio_id": origem_id,
                "tipo": current["tipo"],
                "ano": current["ano"],
                "trimestre": current.get("trimestre"),
                "versao": current["versao"],
                "remetente": SENDER_ADDRESS,
                "assunto": subject,
                "corpo": body,
                "pdf_nome": artifact["nome_arquivo"],
                "pdf_sha256": artifact["sha256"],
                "pdf_tamanho": artifact["tamanho"],
                "criado_por": _actor(principal),
                "chave_idempotencia": key,
            },
            recipients,
        )
    if int(existing["relatorio_id"]) != int(current["id"]):
        raise ValueError("Confirmação de envio inválida.")
    if existing["status_envio"] != "PREPARADO":
        return existing
    if existing["pdf_sha256"] != artifact["sha256"]:
        raise ValueError(CHANGED_PDF_MESSAGE)
    claimed = repository.claim_distribution(existing["id"])
    if claimed is None:
        return repository.distribution(existing["id"])
    return _send_claimed(
        store,
        repository,
        current,
        principal,
        claimed,
        artifact,
        transport,
        reenvio=current["status"] == "ENVIADO" and origem_id is None,
    )


def _send_claimed(
    store, repository, report, principal, claimed, artifact, transport, *, reenvio
):
    _audit(
        store,
        principal,
        "RELATORIO_DISTRIBUICAO_INICIADA",
        "Iniciou a distribuição do relatório institucional",
        report,
        claimed,
        extra={"reenvio": reenvio},
    )
    started = time.perf_counter()
    try:
        sender = transport if transport is not None else institutional_transport()
    except NotConfigured:
        _fail_pending(repository, claimed, NOT_CONFIGURED)
        closed = repository.close_distribution(
            claimed["id"],
            status="FALHA",
            actor=_actor(principal),
            error=NOT_CONFIGURED,
        )
        _audit(
            store,
            principal,
            "RELATORIO_DISTRIBUICAO_FALHOU",
            "Falhou a distribuição do relatório institucional",
            report,
            closed,
            resultado="ERRO",
            extra={"duracao_ms": _elapsed(started), "reenvio": reenvio},
        )
        raise ValueError(NOT_CONFIGURED) from None
    uncertain = False
    for person in claimed["destinatarios"]:
        if person["status"] == "ENVIADO":
            continue
        message = {
            "to": [person["email"]],
            "subject": claimed["assunto"],
            "text": claimed["corpo"],
            "attachment": {
                "filename": claimed["pdf_nome"],
                "content": artifact["conteudo"],
            },
        }
        try:
            provider_id = sender.send(message)
        except DeliveryRejected as exc:
            repository.record_recipient(
                person["id"], status="FALHA", error=sanitize_error(exc)
            )
            continue
        except DeliveryUncertain as exc:
            repository.record_recipient(
                person["id"], status="PENDENTE", error=sanitize_error(exc)
            )
            uncertain = True
            LOGGER.exception(
                "Envio incerto do relatório institucional %s versão %s.",
                report.get("id"),
                report.get("versao"),
            )
            break
        except Exception as exc:
            repository.record_recipient(
                person["id"], status="PENDENTE", error=sanitize_error(exc)
            )
            uncertain = True
            LOGGER.exception(
                "Envio incerto do relatório institucional %s versão %s.",
                report.get("id"),
                report.get("versao"),
            )
            break
        else:
            if not provider_id:
                repository.record_recipient(
                    person["id"], status="PENDENTE", error=UNCERTAIN_MESSAGE
                )
                uncertain = True
                break
            repository.record_recipient(
                person["id"], status="ENVIADO", provider_id=str(provider_id)
            )
    current = repository.distribution(claimed["id"])
    statuses = [item["status"] for item in current["destinatarios"]]
    if uncertain:
        status = "ENVIANDO"
        error = UNCERTAIN_MESSAGE
        event = "RELATORIO_DISTRIBUICAO_INCERTA"
        action = "Interrompeu a distribuição sem confirmação do provedor"
        resultado = "INCERTO"
    elif statuses and all(item == "ENVIADO" for item in statuses):
        status = "ENVIADO"
        error = ""
        event = "RELATORIO_DISTRIBUICAO_CONCLUIDA"
        action = "Concluiu a distribuição do relatório institucional"
        resultado = "OK"
    elif any(item == "ENVIADO" for item in statuses):
        status = "PARCIAL"
        failed = [
            item["procurador"]
            for item in current["destinatarios"]
            if item["status"] != "ENVIADO"
        ]
        error = "Envio parcial. Ainda não receberam: " + ", ".join(failed) + "."
        event = "RELATORIO_DISTRIBUICAO_PARCIAL"
        action = "Concluiu parcialmente a distribuição do relatório institucional"
        resultado = "PARCIAL"
    else:
        status = "FALHA"
        error = "Nenhum destinatário recebeu a mensagem."
        event = "RELATORIO_DISTRIBUICAO_FALHOU"
        action = "Falhou a distribuição do relatório institucional"
        resultado = "ERRO"
    closed = repository.close_distribution(
        claimed["id"], status=status, actor=_actor(principal), error=error
    )
    if status == "ENVIADO" and report["status"] == "FINALIZADO":
        repository.mark_distributed(report["id"], _actor(principal))
    _audit(
        store,
        principal,
        event,
        action,
        report,
        closed,
        resultado=resultado,
        extra={
            "duracao_ms": _elapsed(started),
            "reenvio": reenvio,
            "provedor_ids": [
                item["provedor_mensagem_id"]
                for item in closed["destinatarios"]
                if item.get("provedor_mensagem_id")
            ],
        },
    )
    return closed


def _fail_pending(repository, claimed, error):
    for person in claimed["destinatarios"]:
        if person["status"] != "ENVIADO":
            repository.record_recipient(person["id"], status="FALHA", error=error)


def _current_report(repository, report):
    current = repository.get(report["id"])
    if not current or not current.get("ativo", True):
        raise ValueError("Relatório institucional não encontrado.")
    if report.get("versao") is not None and int(current["versao"]) != int(
        report["versao"]
    ):
        raise ValueError("A versão selecionada mudou. Prepare o envio novamente.")
    if current["status"] not in SENDABLE_STATUSES:
        raise ValueError(DRAFT_MESSAGE)
    return current


def _validated_artifact(store, repository, report, principal):
    artifact = repository.pdf_artifact(report["id"])
    if not artifact:
        raise ValueError(MISSING_PDF_MESSAGE)
    if int(artifact["relatorio_id"]) != int(report["id"]):
        _block(store, principal, report, artifact, "versao")
        raise ValueError(
            "O PDF oficial não pertence à versão selecionada. O envio foi bloqueado."
        )
    content = bytes(artifact.get("conteudo") or b"")
    if not content or int(artifact.get("tamanho") or 0) <= 0:
        _block(store, principal, report, artifact, "vazio")
        raise ValueError(EMPTY_PDF_MESSAGE)
    if int(artifact.get("tamanho") or 0) != len(content):
        _block(store, principal, report, artifact, "tamanho")
        raise ValueError(
            "O PDF oficial desta versão está inconsistente. O envio foi bloqueado."
        )
    if len(content) > MAX_PDF_BYTES:
        _block(store, principal, report, artifact, "tamanho")
        raise ValueError(SIZE_MESSAGE)
    stored = str(artifact.get("sha256") or "").strip().lower()
    actual = hashlib.sha256(content).hexdigest()
    if not stored or stored != actual:
        LOGGER.error(
            "Distribuição bloqueada do relatório %s versão %s: hash divergente.",
            report.get("id"),
            report.get("versao"),
        )
        _block(store, principal, report, artifact, "hash")
        raise ValueError(HASH_MESSAGE)
    if not content.startswith(b"%PDF"):
        _block(store, principal, report, artifact, "assinatura")
        raise ValueError(SIGNATURE_MESSAGE)
    artifact["conteudo"] = content
    artifact["sha256"] = actual
    return artifact


def _require_sender(principal):
    if not has_permission(principal, "admin"):
        raise ValueError(PERMISSION_MESSAGE)


def _block(store, principal, report, artifact, reason):
    _audit(
        store,
        principal,
        "RELATORIO_DISTRIBUICAO_BLOQUEADA",
        "Bloqueou a distribuição do relatório institucional",
        report,
        {
            "pdf_sha256": (artifact or {}).get("sha256"),
            "pdf_nome": (artifact or {}).get("nome_arquivo"),
            "status_envio": "BLOQUEADO",
        },
        resultado="ERRO",
        extra={"motivo": reason},
    )


def _audit(
    store, principal, event, action, report, envio, *, resultado="OK", extra=None
):
    from services.audit import registrar_evento

    details = {
        "relatorio_id": report.get("id"),
        "versao": report.get("versao"),
        "status_relatorio": report.get("status"),
        "envio_id": (envio or {}).get("id"),
        "status_envio": (envio or {}).get("status_envio"),
        "pdf_nome": (envio or {}).get("pdf_nome"),
        "pdf_sha256": (envio or {}).get("pdf_sha256"),
        "remetente": (envio or {}).get("remetente") or SENDER_ADDRESS,
        "assunto": (envio or {}).get("assunto"),
        "quantidade_destinatarios": len((envio or {}).get("destinatarios") or []),
    }
    if extra:
        details.update(extra)
    registrar_evento(
        store,
        evento=event,
        modulo="relatorios",
        acao=action,
        resultado=resultado,
        principal=principal,
        entidade_tipo="relatorio_institucional",
        entidade_id=str(report.get("id") or ""),
        detalhes=details,
    )


def _snapshot(report):
    return report.get("snapshot_dados") or {}


def _actor(principal):
    return getattr(principal, "email", None) or ""


def _elapsed(started):
    return int((time.perf_counter() - started) * 1000)
