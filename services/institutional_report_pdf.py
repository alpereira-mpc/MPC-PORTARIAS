"""Deliver the PDF of one institutional report version without changing its status."""

import hashlib
import logging

from database.institutional_reports import InstitutionalReportsStore
from document_generator.institutional_report_pdf import (
    generate_institutional_report_pdf,
    institutional_pdf_filename,
)
from services.audit import registrar_evento
from services.institutional_reports import require_institutional_administrator


LOGGER = logging.getLogger(__name__)
OFFICIAL_STATUSES = frozenset({"FINALIZADO", "ENVIADO"})


def pdf_sha256(payload):
    return hashlib.sha256(payload).hexdigest()


def deliver_institutional_pdf(store, report, principal, *, official):
    """Return the PDF of this version.

    A finalized version is stored once and reused, so later downloads keep the
    same bytes. A draft preview is generated in memory and is not an official file.
    """
    status = report.get("status")
    if status in OFFICIAL_STATUSES:
        official = True
    elif official:
        raise ValueError("PDF oficial exige relatório finalizado.")
    _require_frozen_version(report)
    filename = institutional_pdf_filename(report)
    repository = InstitutionalReportsStore(store)
    if official:
        stored = repository.pdf_artifact(report["id"])
        if stored:
            _audit(
                store,
                principal,
                report,
                "RELATORIO_INSTITUCIONAL_PDF_BAIXADO",
                "Baixou o PDF oficial do relatório institucional",
                stored,
            )
            return _payload(stored, official=True)
        saved = _persist(repository, report, principal, filename)
        _audit(
            store,
            principal,
            report,
            "RELATORIO_INSTITUCIONAL_PDF_GERADO",
            "Gerou o PDF oficial do relatório institucional",
            saved,
        )
        return _payload(saved, official=True)
    content = generate_institutional_report_pdf(report)
    preview = {
        "nome_arquivo": filename,
        "conteudo": content,
        "sha256": pdf_sha256(content),
        "tamanho": len(content),
    }
    _audit(
        store,
        principal,
        report,
        "RELATORIO_INSTITUCIONAL_PDF_PREVIA",
        "Gerou prévia em PDF do relatório institucional",
        preview,
    )
    return _payload(preview, official=False)


def regenerate_official_pdf(store, report, principal):
    """Build a new official file and replace the stored one only after success."""
    require_institutional_administrator(principal)
    if report.get("status") not in OFFICIAL_STATUSES:
        raise ValueError("PDF oficial exige relatório finalizado.")
    _require_frozen_version(report)
    repository = InstitutionalReportsStore(store)
    if repository.list_distributions(report["id"]):
        raise ValueError(
            "Este PDF já foi distribuído e não pode ser regenerado. "
            "Para alterar o relatório, crie uma nova versão."
        )
    saved = _persist(repository, report, principal, institutional_pdf_filename(report))
    _audit(
        store,
        principal,
        report,
        "RELATORIO_INSTITUCIONAL_PDF_REGENERADO",
        "Regenerou o PDF oficial do relatório institucional",
        saved,
    )
    return _payload(saved, official=True)


def _persist(repository, report, principal, filename):
    content = generate_institutional_report_pdf(report)
    return repository.save_pdf_artifact(
        report["id"],
        nome_arquivo=filename,
        conteudo=content,
        sha256=pdf_sha256(content),
        tamanho=len(content),
        actor=getattr(principal, "email", "") or "",
    )


def _payload(artifact, *, official):
    return {
        "nome": artifact["nome_arquivo"],
        "conteudo": artifact["conteudo"],
        "sha256": artifact["sha256"],
        "tamanho": artifact["tamanho"],
        "oficial": official,
    }


def _require_frozen_version(report):
    snapshot = report.get("snapshot_dados")
    if not isinstance(snapshot, dict) or not snapshot.get("metadados"):
        raise ValueError("O snapshot desta versão não está disponível.")
    metadata = snapshot["metadados"]
    if metadata.get("tipo") != report.get("tipo"):
        raise ValueError("A versão do PDF não corresponde ao tipo do relatório.")
    if int(metadata.get("ano")) != int(report.get("ano")):
        raise ValueError("A versão do PDF não corresponde ao ano do relatório.")
    if report.get("tipo") == "TRIMESTRAL" and int(
        metadata.get("trimestre") or 0
    ) != int(report.get("trimestre") or 0):
        raise ValueError("A versão do PDF não corresponde ao trimestre do relatório.")
    if report.get("tipo") == "ANUAL" and metadata.get("trimestre") not in (None, ""):
        raise ValueError("Relatório anual não possui trimestre.")
    if not report.get("versao"):
        raise ValueError("A versão do relatório não está identificada.")
    if not isinstance(report.get("conteudo_estruturado"), dict):
        raise ValueError("O conteúdo estruturado desta versão não está disponível.")
    coverage = snapshot.get("cobertura_historica")
    if not isinstance(coverage, dict) or "meses_disponiveis" not in coverage:
        raise ValueError("A cobertura desta versão não está registrada.")
    if not isinstance(snapshot.get("indicadores_gerais"), dict):
        raise ValueError("Os indicadores desta versão não estão disponíveis.")


def _audit(store, principal, report, event, action, artifact):
    registrar_evento(
        store,
        evento=event,
        modulo="relatorios",
        acao=action,
        resultado="OK",
        principal=principal,
        entidade_tipo="relatorio_institucional",
        entidade_id=str(report.get("id")),
        detalhes={
            "relatorio_id": report.get("id"),
            "versao": report.get("versao"),
            "tipo": report.get("tipo"),
            "ano": report.get("ano"),
            "trimestre": report.get("trimestre"),
            "nome_arquivo": artifact.get("nome_arquivo"),
            "sha256": artifact.get("sha256"),
            "tamanho": artifact.get("tamanho"),
        },
    )
