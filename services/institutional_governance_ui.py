"""Institutional governance screens. They display checks and never change status."""

import streamlit as st

from database.institutional_reports import InstitutionalReportsStore
from services.date_format import format_datetime_br
from services.institutional_report_validation import (
    ENVIO_LABELS,
    compare_versions,
    content_hash,
    describe_audit_events,
    distribution_readiness,
    finalization_readiness,
    overview_row,
    report_cycle,
    snapshot_hash,
    suggest_periods,
    validate_institutional_report_version,
    verify_pdf_bytes,
)


CYCLE_LABELS = {
    "concluido": "Concluído",
    "pendente": "Pendente",
    "atencao": "Atenção",
    "erro": "Erro",
}
STATUS_LABELS = {
    "RASCUNHO": "Rascunho",
    "EM_REVISAO": "Em revisão",
    "FINALIZADO": "Finalizado",
    "ENVIADO": "Enviado",
}
CHECKLIST = (
    ("snapshot", "Snapshot íntegro"),
    ("cobertura", "Cobertura válida"),
    ("indicadores", "Indicadores coerentes"),
    ("conteudo", "Conteúdo salvo"),
    ("status", "Status compatível"),
)


def render_overview(rows):
    """Compact index. Each row is already loaded and contains no PDF bytes."""
    table = []
    for row in rows or []:
        try:
            table.append(overview_row(row))
        except (TypeError, ValueError, KeyError):
            continue
    if not table:
        return
    st.caption("Versões mais recentes de cada período")
    st.table(table)


def render_ready_periods(plan, *, administrator, selected_token, tipo=None):
    """Suggest periods of the selected type. The click uses the existing flow."""
    clicked = None
    gaps = list(plan.get("lacunas") or [])
    suggestions = list(plan.get("sugestoes") or [])
    if tipo in ("TRIMESTRAL", "ANUAL"):
        suggestions = [item for item in suggestions if item.get("tipo") == tipo]
    if tipo == "TRIMESTRAL":
        gaps = []
    for gap in gaps:
        st.info(gap["mensagem"])
    for item in suggestions:
        if item["tipo"] == "ANUAL" and item["parcial"]:
            st.markdown("**Relatório anual parcial disponível**")
        elif item["tipo"] == "ANUAL":
            st.markdown("**Relatório anual disponível**")
        else:
            st.markdown("**Relatório disponível para criação**")
        st.caption(item["titulo"])
        st.caption(f"Cobertura: {item['cobertura']}.")
        token = f"{item['tipo']}_{item['ano']}_{item['trimestre'] or 0}"
        if (
            administrator
            and token != selected_token
            and st.button(
                "Criar relatório",
                key=f"institutional_governance_create_{token}",
            )
        ):
            clicked = item
    return clicked


def period_plan(coverage_by_year, rows):
    existing = {
        (
            row.get("tipo"),
            int(row["ano"]),
            int(row["trimestre"]) if row.get("trimestre") is not None else None,
        )
        for row in rows or []
        if row.get("ano") is not None
    }
    return suggest_periods(coverage_by_year, existing)


def _audit_rows(store, report_id):
    with store.connection(read_only=True) as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT evento, criado_em FROM auditoria_eventos "
                "WHERE entidade_tipo=? AND entidade_id=? ORDER BY criado_em, id",
                ("relatorio_institucional", str(report_id)),
            ).fetchall()
        ]


def _checklist_table(result, readiness):
    rows = []
    for scope, title in CHECKLIST:
        scoped = [
            item for item in result.get("checks") or [] if item.get("escopo") == scope
        ]
        if any(item["status"] == "ERRO" for item in scoped):
            state = "Pendência"
        elif any(item["status"] == "ATENCAO" for item in scoped):
            state = "Atenção"
        elif scoped:
            state = "OK"
        else:
            state = "OK"
        rows.append({"Item": title, "Situação": state})
    rows.append(
        {
            "Item": "Nenhuma edição pendente",
            "Situação": (
                "Pendência"
                if any(
                    item["codigo"] == "EDICAO_NAO_SALVA" for item in readiness["erros"]
                )
                else "OK"
            ),
        }
    )
    numeric = [
        item
        for item in result.get("checks") or []
        if item["codigo"] == "NUMERO_NAO_RECONHECIDO"
    ]
    rows.append(
        {
            "Item": "Validação numérica executada",
            "Situação": "Atenção" if numeric else "OK",
        }
    )
    return rows


def _render_technical_diagnosis(repository, report):
    """Administrative detail. Raw JSON stays inside a second collapsed panel."""
    snapshot = report.get("snapshot_dados") or {}
    pdf_meta = repository.pdf_metadata(report["id"])
    with st.expander("Diagnóstico técnico", expanded=False):
        st.caption(f"ID: {report.get('id')}")
        st.caption(f"Versão: {report.get('versao')}")
        st.caption(f"Status: {report.get('status')}")
        st.caption(f"Snapshot hash: {snapshot_hash(snapshot)}")
        st.caption(f"Conteúdo hash: {content_hash(report.get('conteudo_estruturado'))}")
        st.caption(f"PDF hash: {(pdf_meta or {}).get('sha256') or '—'}")
        with st.expander("Dados brutos", expanded=False):
            st.json(
                {
                    "snapshot": snapshot,
                    "conteudo_estruturado": report.get("conteudo_estruturado"),
                }
            )


def render_governance(store, report, versions, *, unsaved=False, administrator=False):
    """Validation tab. Failures stay inside the tab."""
    try:
        _render_governance(
            store, report, versions, unsaved=unsaved, administrator=administrator
        )
    except Exception:
        st.error("Não foi possível concluir a validação desta versão.")


def _render_governance(store, report, versions, *, unsaved=False, administrator=False):
    repository = InstitutionalReportsStore(store)
    pdf_metadata = repository.pdf_metadata(report["id"])
    summaries = repository.distribution_summaries(report["id"])
    recipients = None
    gmail_configured = None
    if report.get("status") in {"FINALIZADO", "ENVIADO"}:
        from services.email_transport import gmail_configuration
        from services.institutional_report_distribution import assess_recipients

        recipients = assess_recipients(store)
        try:
            gmail_configured = bool(gmail_configuration().get("configured"))
        except Exception:
            gmail_configured = False
    stored = st.session_state.get(f"institutional_governance_pdf_result_{report['id']}")
    pdf_bytes_result = None
    if stored and pdf_metadata and stored.get("sha256") == pdf_metadata.get("sha256"):
        pdf_bytes_result = stored.get("result")
    result = validate_institutional_report_version(
        report,
        pdf_metadata=pdf_metadata,
        distributions=summaries,
        recipients=recipients,
        gmail_configured=gmail_configured,
        versions=versions,
        pdf_bytes_result=pdf_bytes_result,
    )
    readiness = finalization_readiness(
        result, status=report.get("status"), unsaved=unsaved
    )
    delivery = distribution_readiness(
        result, status=report.get("status"), summaries=summaries
    )
    st.markdown(f"**Integridade da versão: {result['rotulo']}**")
    cycle = report_cycle(report, result, pdf_metadata=pdf_metadata, summaries=summaries)
    st.caption(
        " · ".join(f"{step['etapa']}: {CYCLE_LABELS[step['estado']]}" for step in cycle)
    )
    st.markdown("**Prontidão para finalização**")
    st.caption(readiness["rotulo"])
    st.table(_checklist_table(result, readiness))
    errors = [item for item in result["checks"] if item["status"] == "ERRO"]
    attentions = [item for item in result["checks"] if item["status"] == "ATENCAO"]
    if errors:
        st.markdown("**Pendências**")
        for item in errors:
            detail = item["mensagem"]
            if item.get("valor"):
                detail = f"{detail} Valor encontrado: {item['valor']}."
            st.caption(f"{item['titulo']}: {detail}")
    if attentions:
        st.markdown("**Pontos de atenção**")
        for item in attentions:
            detail = item["mensagem"]
            if item.get("valor"):
                detail = f"{detail} Seção: {item['titulo']}. Valor encontrado: {item['valor']}."
            st.caption(detail)
    if report.get("status") in {"FINALIZADO", "ENVIADO"}:
        st.markdown("**Prontidão para distribuição**")
        st.caption(delivery["rotulo"])
        _render_pdf_check(repository, report, pdf_metadata)
        _render_delivery_history(repository, report, summaries)
    try:
        events = describe_audit_events(_audit_rows(store, report["id"]))
    except Exception:
        events = []
    st.markdown("**Histórico**")
    if not events:
        st.caption("Nenhum evento de auditoria registrado para esta versão.")
    else:
        for event in events:
            st.caption(f"{format_datetime_br(event['quando'])} — {event['titulo']}")
    if administrator:
        _render_technical_diagnosis(repository, report)


def _render_pdf_check(repository, report, pdf_metadata):
    if not pdf_metadata:
        st.caption("PDF oficial ainda não gerado.")
        return
    st.caption(
        f"PDF oficial registrado: {pdf_metadata.get('nome_arquivo') or 'sem nome'}."
    )
    if st.button(
        "Verificar integridade do PDF",
        key=f"institutional_governance_pdf_{report['id']}",
    ):
        artifact = repository.pdf_artifact(report["id"])
        outcome = verify_pdf_bytes(report, artifact)
        st.session_state[f"institutional_governance_pdf_result_{report['id']}"] = {
            "sha256": pdf_metadata.get("sha256"),
            "result": outcome,
        }
        st.rerun()
    stored = st.session_state.get(f"institutional_governance_pdf_result_{report['id']}")
    if not stored or stored.get("sha256") != pdf_metadata.get("sha256"):
        st.caption("A conferência do SHA-256 é feita somente neste botão.")
        return
    outcome = stored["result"]
    if outcome.get("status") == "OK":
        st.caption("PDF oficial íntegro.")
    else:
        for item in outcome.get("checks") or []:
            if item["status"] != "OK":
                st.caption(item["mensagem"])


def _render_delivery_history(repository, report, summaries):
    if report.get("status") != "ENVIADO" or not summaries:
        return
    latest = summaries[0]
    reenvios = max(0, len(summaries) - 1)
    st.caption(
        f"Último envio: {format_datetime_br(latest.get('enviado_em') or latest.get('criado_em'))} · "
        f"{int(latest.get('destinatarios') or 0)} destinatários · "
        f"{ENVIO_LABELS.get(latest.get('status_envio'), latest.get('status_envio'))} · "
        f"reenvios: {reenvios}."
    )
    if st.button(
        "Ver detalhes do envio",
        key=f"institutional_governance_send_{report['id']}",
    ):
        st.session_state[f"institutional_governance_send_open_{report['id']}"] = latest[
            "id"
        ]
    opened = st.session_state.get(f"institutional_governance_send_open_{report['id']}")
    if not opened:
        return
    full = repository.distribution(opened)
    if not full:
        return
    st.caption(f"PDF utilizado: {full.get('pdf_nome') or '—'}")
    st.caption(f"SHA-256: {full.get('pdf_sha256') or '—'}")
    for person in full.get("destinatarios") or []:
        st.text(
            f"{person.get('procurador') or '—'} — {person.get('email') or '—'} — "
            f"{person.get('status') or '—'}"
        )


def _drop_widget(key, valid):
    if key in st.session_state and st.session_state[key] not in valid:
        del st.session_state[key]


def render_version_comparison(store, versions):
    """Compare two versions of the period already selected."""
    if len(versions or []) < 2:
        st.caption(
            "A comparação fica disponível quando houver pelo menos duas versões deste período."
        )
        return
    labels = [
        f"v{item['versao']} — {STATUS_LABELS.get(item['status'], item['status'])}"
        for item in versions
    ]
    key_a = "institutional_governance_compare_a"
    key_b = "institutional_governance_compare_b"
    _drop_widget(key_a, labels)
    _drop_widget(key_b, labels)
    st.markdown("**Comparar versões**")
    left_label = st.selectbox("Versão A", labels, index=len(labels) - 1, key=key_a)
    right_label = st.selectbox("Versão B", labels, index=0, key=key_b)
    if left_label == right_label:
        st.caption("Selecione duas versões diferentes.")
        return
    left = versions[labels.index(left_label)]
    right = versions[labels.index(right_label)]
    repository = InstitutionalReportsStore(store)
    compared = compare_versions(
        left,
        right,
        left_pdf=repository.pdf_metadata(left["id"]),
        right_pdf=repository.pdf_metadata(right["id"]),
    )
    if not compared.get("compativel"):
        st.warning(compared.get("mensagem"))
        return
    meta = compared["metadados"]
    st.caption(
        f"Status: {STATUS_LABELS.get(meta['status_a'], meta['status_a'])} → "
        f"{STATUS_LABELS.get(meta['status_b'], meta['status_b'])}"
    )
    st.caption(
        f"Data de corte: {format_datetime_br(meta['corte_a'])} → "
        f"{format_datetime_br(meta['corte_b'])}"
    )
    st.caption(
        "Cobertura: "
        + (", ".join(str(month) for month in meta["cobertura_a"]) or "—")
        + " → "
        + (", ".join(str(month) for month in meta["cobertura_b"]) or "—")
    )
    if compared.get("snapshots_iguais"):
        st.caption("Os snapshots desta comparação são idênticos.")
    else:
        st.caption("Os snapshots desta comparação são diferentes.")
    st.table(
        [
            {
                "Indicador": item["indicador"],
                "Anterior": item["anterior"],
                "Atual": item["atual"],
                "Diferença": item["diferenca"],
            }
            for item in compared["indicadores"]
        ]
    )
    changed = [item for item in compared["secoes"] if item["alterada"]]
    if not changed:
        st.caption("Nenhuma seção textual foi alterada.")
    for item in changed:
        with st.expander(f"{item['titulo']} — alterada"):
            st.caption("Versão A")
            st.text(item["texto_a"] or "—")
            st.caption("Versão B")
            st.text(item["texto_b"] or "—")
    pdf = compared.get("pdf")
    if pdf and (pdf.get("hash_a") or pdf.get("hash_b")):
        st.caption(
            f"PDF: {pdf.get('nome_a') or '—'} ({pdf.get('tamanho_a') or '—'} bytes) → "
            f"{pdf.get('nome_b') or '—'} ({pdf.get('tamanho_b') or '—'} bytes)"
        )
