"""Deterministic governance checks. They never send mail or rebuild Tramita."""

import hashlib
import inspect
import math
from pathlib import Path

from database.institutional_reports import InstitutionalReportsStore
from database.notifications import NotificationsStore
from database.store import Store
from document_generator.institutional_report_pdf import institutional_pdf_filename
from services.institutional_report_content import SECTIONS
from services.institutional_report_distribution import assess_recipients
from services.institutional_report_validation import (
    ATENCAO,
    ERRO,
    OK,
    compare_versions,
    content_hash,
    distribution_readiness,
    finalization_readiness,
    overview_row,
    snapshot_hash,
    suggest_periods,
    validate_institutional_report_version,
    verify_pdf_bytes,
)
from services.institutional_reports import (
    EMPTY_STRUCTURED_CONTENT,
    build_report_snapshot,
)
from services.notifications import SUBSCRIPTION_PROTOCOLO
from services.tramita_reports import import_reference_reports
import services.institutional_report_validation as validation_module
import services.institutional_governance_ui as governance_module


def _summary(**overrides):
    summary = {
        "distributed": 100,
        "production": 80,
        "opinions": 50,
        "quotas": 30,
        "production_rate": 80.0,
        "median_days": 7.8,
    }
    summary.update(overrides)
    return summary


def _snapshot(
    tipo="TRIMESTRAL",
    ano=2026,
    trimestre=3,
    months=None,
    summary=None,
    comparison=True,
):
    if months is None:
        months = [7, 8, 9] if tipo == "TRIMESTRAL" else list(range(1, 10))
    summary = _summary() if summary is None else summary
    return {
        "metadados": {
            "tipo": tipo,
            "ano": ano,
            "trimestre": trimestre if tipo == "TRIMESTRAL" else None,
            "periodo_parcial": tipo == "ANUAL" and months != list(range(1, 13)),
            "descricao_periodo": "período congelado",
        },
        "indicadores_gerais": summary,
        "serie_mensal": [{"month": month, "summary": summary} for month in months],
        "composicao_producao": {
            "pareceres": summary["opinions"],
            "cotas": summary["quotas"],
        },
        "por_procurador": [{"procurador": "Elvira", **summary}],
        "cobertura_historica": {
            "meses_disponiveis": list(months),
            "meses_ausentes": [],
        },
        "nota_metodologica": {"origem": "Eventos da referência histórica."},
        "comparacao_periodo_anterior": summary if comparison else None,
    }


def _content(text="Texto institucional suficientemente descritivo para esta seção."):
    return {key: {"texto": text} for key, _label in SECTIONS}


def _report(snapshot=None, **overrides):
    snapshot = _snapshot() if snapshot is None else snapshot
    meta = snapshot["metadados"]
    report = {
        "id": 1,
        "tipo": meta["tipo"],
        "ano": meta["ano"],
        "trimestre": meta["trimestre"],
        "versao": 1,
        "status": "RASCUNHO",
        "data_corte": "2026-10-02T12:00:00",
        "snapshot_dados": snapshot,
        "conteudo_estruturado": _content(),
    }
    report.update(overrides)
    return report


def _codes(result, status=None):
    return {
        item["codigo"]
        for item in result["checks"]
        if status is None or item["status"] == status
    }


def _status(result, code):
    found = [item["status"] for item in result["checks"] if item["codigo"] == code]
    assert found, code
    return found[0]


def test_validation_does_not_call_tramita_mail_or_models():
    source = inspect.getsource(validation_module) + inspect.getsource(governance_module)
    assert "build_report_snapshot" not in source
    assert "months_for_year" not in source
    assert "registrar_evento" not in source
    assert "confirm_distribution" not in source
    assert "institutional_transport" not in source
    assert "gemini" not in source.lower()
    assert "gerar_conteudo" not in source


def test_valid_snapshot_is_ok():
    result = validate_institutional_report_version(_report())
    assert result["status"] == OK
    assert _status(result, "SNAPSHOT_PRESENTE") == OK
    assert _status(result, "PRODUCAO_COMPOSICAO") == OK
    assert "ERRO" not in {item["status"] for item in result["checks"]}


def test_missing_snapshot_is_a_structural_error():
    result = validate_institutional_report_version(_report(snapshot_dados={}))
    assert result["status"] == ERRO
    assert _status(result, "SNAPSHOT_PRESENTE") == ERRO
    readiness = finalization_readiness(result, status="RASCUNHO")
    assert readiness["bloqueado"]
    assert readiness["rotulo"] == "Existem pendências que impedem a finalização"


def test_complete_quarter_coverage_is_ok():
    result = validate_institutional_report_version(_report())
    assert _status(result, "COBERTURA_TRIMESTRAL") == OK


def test_incomplete_quarter_coverage_blocks():
    snapshot = _snapshot(months=[7, 8])
    result = validate_institutional_report_version(_report(snapshot=snapshot))
    assert _status(result, "COBERTURA_TRIMESTRAL") == ERRO
    assert finalization_readiness(result, status="RASCUNHO")["bloqueado"]


def test_continuous_partial_annual_is_attention_not_error():
    snapshot = _snapshot(
        tipo="ANUAL", trimestre=None, months=list(range(1, 10)), comparison=False
    )
    result = validate_institutional_report_version(_report(snapshot=snapshot))
    assert _status(result, "ANUAL_PARCIAL") == ATENCAO
    assert result["status"] == ATENCAO
    readiness = finalization_readiness(result, status="RASCUNHO")
    assert not readiness["bloqueado"]
    assert readiness["requer_confirmacao"]


def test_annual_gap_is_an_error():
    snapshot = _snapshot(
        tipo="ANUAL", trimestre=None, months=[1, 2, 3, 4, 6], comparison=False
    )
    result = validate_institutional_report_version(_report(snapshot=snapshot))
    assert _status(result, "COBERTURA_LACUNA") == ERRO
    assert finalization_readiness(result, status="RASCUNHO")["bloqueado"]


def test_complete_annual_coverage_is_ok():
    snapshot = _snapshot(
        tipo="ANUAL", trimestre=None, months=list(range(1, 13)), comparison=False
    )
    result = validate_institutional_report_version(_report(snapshot=snapshot))
    assert _status(result, "COBERTURA_ANUAL") == OK
    assert "ANUAL_PARCIAL" not in _codes(result)


def test_production_matches_opinions_and_quotas():
    result = validate_institutional_report_version(_report())
    assert _status(result, "PRODUCAO_COMPOSICAO") == OK


def test_structural_production_divergence_blocks():
    summary = _summary(production=90, opinions=50, quotas=30, production_rate=90.0)
    result = validate_institutional_report_version(
        _report(snapshot=_snapshot(summary=summary))
    )
    assert _status(result, "PRODUCAO_COMPOSICAO") == ERRO
    assert finalization_readiness(result, status="EM_REVISAO")["bloqueado"]


def test_production_rate_matches_frozen_values():
    result = validate_institutional_report_version(_report())
    assert _status(result, "PRODUCAO_DISTRIBUICOES") == OK


def test_production_rate_accepts_one_decimal_rounding():
    summary = _summary(distributed=545, production=594, opinions=424, quotas=170)
    summary["production_rate"] = round(594 / 545 * 100, 1)
    result = validate_institutional_report_version(
        _report(snapshot=_snapshot(summary=summary))
    )
    assert _status(result, "PRODUCAO_DISTRIBUICOES") == OK


def test_nan_and_infinity_are_blocked():
    for value in (math.nan, math.inf):
        summary = _summary(median_days=value)
        result = validate_institutional_report_version(
            _report(snapshot=_snapshot(summary=summary))
        )
        assert _status(result, "INDICADOR_NAO_FINITO") == ERRO
        assert finalization_readiness(result, status="RASCUNHO")["bloqueado"]


def test_recognized_integer_and_brazilian_grouping():
    summary = _summary(distributed=1760, production=1849, opinions=1320, quotas=529)
    summary["production_rate"] = round(1849 / 1760 * 100, 1)
    summary["median_days"] = 11.0
    snapshot = _snapshot(
        tipo="ANUAL",
        trimestre=None,
        months=list(range(1, 10)),
        summary=summary,
        comparison=False,
    )
    text = "A produção foi 1849, também escrita 1.849 e 1.849,0, com mediana 11,0."
    result = validate_institutional_report_version(
        _report(snapshot=snapshot, conteudo_estruturado=_content(text))
    )
    assert "NUMERO_NAO_RECONHECIDO" not in _codes(result)


def test_english_decimal_percent_and_median_are_recognized():
    summary = _summary(
        distributed=200,
        production=161,
        opinions=100,
        quotas=61,
        production_rate=80.5,
        median_days=7.8,
    )
    text = "O percentual foi 80.5% e a mediana 7.8 dias, ou 80,5% e 7,8."
    result = validate_institutional_report_version(
        _report(
            snapshot=_snapshot(summary=summary), conteudo_estruturado=_content(text)
        )
    )
    assert "NUMERO_NAO_RECONHECIDO" not in _codes(result)


def test_year_version_and_quarter_are_ignored():
    text = "Ano 2026, versão 1, 3º trimestre, referência de 3 meses."
    result = validate_institutional_report_version(
        _report(conteudo_estruturado=_content(text))
    )
    assert "NUMERO_NAO_RECONHECIDO" not in _codes(result)


def test_unrecognized_number_is_attention_and_names_the_value():
    result = validate_institutional_report_version(
        _report(conteudo_estruturado=_content("Foram citados 999 processos."))
    )
    found = [
        item
        for item in result["checks"]
        if item["codigo"] == "NUMERO_NAO_RECONHECIDO" and item["valor"] == "999"
    ]
    assert found
    assert found[0]["status"] == ATENCAO
    assert found[0]["secao"]
    assert "não pôde ser relacionado" in found[0]["mensagem"]
    readiness = finalization_readiness(result, status="RASCUNHO")
    assert not readiness["bloqueado"]
    assert readiness["requer_confirmacao"]
    assert "2" not in readiness["rotulo"] or readiness["atencoes"]


def test_empty_section_is_attention():
    content = _content()
    content["resumo_executivo"] = {"texto": ""}
    result = validate_institutional_report_version(
        _report(conteudo_estruturado=content)
    )
    assert any(
        item["codigo"] == "SECAO_VAZIA" and item["secao"] == "resumo_executivo"
        for item in result["checks"]
    )
    assert finalization_readiness(result, status="RASCUNHO")["bloqueado"] is False


def test_partial_content_keeps_the_filled_section():
    content = _content("")
    content["resumo_executivo"] = {
        "texto": "Texto institucional suficientemente descritivo e manual."
    }
    result = validate_institutional_report_version(
        _report(conteudo_estruturado=content)
    )
    empty = [item for item in result["checks"] if item["codigo"] == "SECAO_VAZIA"]
    assert empty
    assert all(item["secao"] != "resumo_executivo" for item in empty)
    assert "NUMERO_NAO_RECONHECIDO" not in _codes(result)


def test_manual_text_with_known_numbers_has_no_numeric_attention():
    result = validate_institutional_report_version(
        _report(
            conteudo_estruturado=_content(
                "Produção de 80 pareceres e cotas no período."
            )
        )
    )
    assert "NUMERO_NAO_RECONHECIDO" not in _codes(result)


def test_sound_report_is_ready_to_finalize():
    result = validate_institutional_report_version(_report(), versions=[_report()])
    readiness = finalization_readiness(result, status="RASCUNHO", unsaved=False)
    assert readiness["pronto"]
    assert readiness["rotulo"] == "Pronto para finalizar"


def test_structural_error_blocks_finalization():
    result = validate_institutional_report_version(
        _report(snapshot=_snapshot(months=[7]))
    )
    readiness = finalization_readiness(result, status="RASCUNHO")
    assert readiness["bloqueado"]
    assert not readiness["pronto"]


def test_attention_requires_confirmation_and_does_not_block():
    result = validate_institutional_report_version(
        _report(conteudo_estruturado=_content("Foram citados 999 processos."))
    )
    readiness = finalization_readiness(result, status="RASCUNHO")
    assert readiness["requer_confirmacao"]
    assert not readiness["bloqueado"]
    assert readiness["rotulo"] == "Pode ser finalizado, mas existem pontos de atenção"


def test_unsaved_edit_blocks_finalization():
    result = validate_institutional_report_version(_report())
    readiness = finalization_readiness(result, status="RASCUNHO", unsaved=True)
    assert readiness["bloqueado"]
    assert any(item["codigo"] == "EDICAO_NAO_SALVA" for item in readiness["erros"])


def test_old_report_without_optional_methodology_is_not_corrupt():
    snapshot = _snapshot()
    snapshot.pop("nota_metodologica")
    result = validate_institutional_report_version(_report(snapshot=snapshot))
    assert _status(result, "METODOLOGIA_PRESENTE") == ATENCAO
    assert result["status"] != ERRO
    readiness = finalization_readiness(result, status="RASCUNHO")
    assert not readiness["bloqueado"]
    assert readiness["requer_confirmacao"]


def _pdf_meta(report, *, digest=None, size=12, relatorio_id=None, name=None):
    content = b"%PDF-1.4 ok"
    return {
        "relatorio_id": report["id"] if relatorio_id is None else relatorio_id,
        "nome_arquivo": name or institutional_pdf_filename(report),
        "sha256": digest or hashlib.sha256(content).hexdigest(),
        "tamanho": size,
    }


def test_official_pdf_metadata_is_coherent_without_bytes():
    report = _report(status="FINALIZADO")
    meta = _pdf_meta(report)
    assert "conteudo" not in meta
    result = validate_institutional_report_version(report, pdf_metadata=meta)
    assert _status(result, "PDF_METADADOS") == OK


def test_correct_sha256_of_loaded_bytes():
    report = _report(status="FINALIZADO")
    content = b"%PDF-1.4 oficial"
    artifact = _pdf_meta(
        report, digest=hashlib.sha256(content).hexdigest(), size=len(content)
    )
    artifact["conteudo"] = content
    outcome = verify_pdf_bytes(report, artifact)
    assert outcome["status"] == OK
    assert any(item["mensagem"] == "PDF oficial íntegro." for item in outcome["checks"])


def test_wrong_sha256_is_an_error():
    report = _report(status="FINALIZADO")
    content = b"%PDF-1.4 oficial"
    artifact = _pdf_meta(report, digest="ab" * 32, size=len(content))
    artifact["conteudo"] = content
    outcome = verify_pdf_bytes(report, artifact)
    assert _status(outcome, "PDF_SHA256") == ERRO


def test_pdf_of_another_version_is_an_error():
    report = _report(status="FINALIZADO")
    result = validate_institutional_report_version(
        report, pdf_metadata=_pdf_meta(report, relatorio_id=99)
    )
    assert _status(result, "PDF_VERSAO") == ERRO


def test_missing_pdf_on_sent_report_is_an_error():
    report = _report(status="ENVIADO")
    result = validate_institutional_report_version(
        report, pdf_metadata=None, distributions=[]
    )
    assert _status(result, "PDF_AUSENTE") == ERRO


def test_metadata_validation_does_not_require_the_blob():
    report = _report(status="FINALIZADO")
    meta = _pdf_meta(report)
    result = validate_institutional_report_version(report, pdf_metadata=meta)
    assert "conteudo" not in meta
    assert not any(item["codigo"] == "PDF_SHA256" for item in result["checks"])


def _ready_recipients():
    return {
        "destinatarios": [{"procurador": "Elvira", "email": "elvira@tce.pb.gov.br"}],
        "bloqueios": [],
    }


def test_finalized_report_can_be_ready_to_distribute():
    report = _report(status="FINALIZADO")
    meta = _pdf_meta(report)
    result = validate_institutional_report_version(
        report,
        pdf_metadata=meta,
        recipients=_ready_recipients(),
        gmail_configured=True,
    )
    delivery = distribution_readiness(result, status="FINALIZADO")
    assert delivery["pronta"]
    assert delivery["rotulo"] == "Pronta para distribuição"


def test_draft_is_not_ready_to_distribute():
    result = validate_institutional_report_version(
        _report(), recipients=_ready_recipients()
    )
    delivery = distribution_readiness(result, status="RASCUNHO")
    assert not delivery["pronta"]
    assert "finalização" in delivery["rotulo"]


def test_distribution_without_pdf_is_not_ready():
    report = _report(status="FINALIZADO")
    result = validate_institutional_report_version(
        report, pdf_metadata=None, recipients=_ready_recipients(), gmail_configured=True
    )
    delivery = distribution_readiness(result, status="FINALIZADO")
    assert not delivery["pronta"]
    assert _status(result, "DISTRIBUICAO_PDF") == ERRO


def test_missing_recipient_blocks_distribution_only():
    report = _report(status="FINALIZADO")
    result = validate_institutional_report_version(
        report,
        pdf_metadata=_pdf_meta(report),
        recipients={
            "destinatarios": [],
            "bloqueios": ["Sem e-mail institucional para Elvira."],
        },
        gmail_configured=True,
    )
    assert _status(result, "DISTRIBUICAO_DESTINATARIOS") == ERRO
    assert not distribution_readiness(result, status="FINALIZADO")["pronta"]
    draft = validate_institutional_report_version(
        _report(),
        recipients={
            "destinatarios": [],
            "bloqueios": ["Sem e-mail institucional para Elvira."],
        },
    )
    assert not finalization_readiness(draft, status="RASCUNHO")["bloqueado"]


def test_invalid_and_duplicate_emails_from_the_real_catalog(tmp_path):
    store = Store(tmp_path / "recipients.db")
    notes = NotificationsStore(store)
    people = [person for person in store.catalog("procuradores") if person.get("ativo")]
    for index, person in enumerate(people):
        address = (
            "repetido@tce.pb.gov.br" if index < 2 else f"p{person['id']}@tce.pb.gov.br"
        )
        if index == 3:
            address = "sem-arroba"
        notes.save_recipient(
            SUBSCRIPTION_PROTOCOLO, "PROCURADOR", person["id"], address, True
        )
    assessed = assess_recipients(store)
    assert assessed["bloqueios"]
    report = _report(status="FINALIZADO")
    result = validate_institutional_report_version(
        report,
        pdf_metadata=_pdf_meta(report),
        recipients=assessed,
        gmail_configured=True,
    )
    assert _status(result, "DISTRIBUICAO_DESTINATARIOS") == ERRO
    assert not distribution_readiness(result, status="FINALIZADO")["pronta"]


def test_sent_report_with_valid_history_is_concluded():
    report = _report(status="ENVIADO")
    meta = _pdf_meta(report)
    summaries = [
        {
            "id": 2,
            "status_envio": "ENVIADO",
            "pdf_sha256": meta["sha256"],
            "destinatarios": 7,
            "origem_envio_id": 1,
            "enviado_em": "2026-10-04T10:00:00",
        }
    ]
    result = validate_institutional_report_version(
        report,
        pdf_metadata=meta,
        distributions=summaries,
        recipients=_ready_recipients(),
        gmail_configured=True,
    )
    delivery = distribution_readiness(result, status="ENVIADO", summaries=summaries)
    assert delivery["pronta"]
    assert delivery["rotulo"] == "Distribuição concluída"


def test_draft_with_a_distribution_record_is_incoherent():
    result = validate_institutional_report_version(
        _report(), distributions=[{"status_envio": "ENVIADO"}]
    )
    assert _status(result, "STATUS_DISTRIBUICAO") == ERRO
    assert finalization_readiness(result, status="RASCUNHO")["bloqueado"]


def test_sent_without_pdf_is_incoherent():
    result = validate_institutional_report_version(
        _report(status="ENVIADO"), pdf_metadata=None, distributions=[]
    )
    assert result["status"] == ERRO


def _pair(left_summary=None, right_summary=None, left_text=None, right_text=None):
    left = _report(
        snapshot=_snapshot(summary=left_summary) if left_summary else _snapshot()
    )
    right_snapshot = _snapshot(summary=right_summary) if right_summary else _snapshot()
    right = _report(
        snapshot=right_snapshot,
        id=2,
        versao=2,
        status="EM_REVISAO",
        conteudo_estruturado=(
            _content(right_text) if right_text else _content(left_text)
        ),
    )
    if left_text:
        left["conteudo_estruturado"] = _content(left_text)
    return left, right


def test_compare_same_period_versions():
    left, right = _pair()
    compared = compare_versions(left, right)
    assert compared["compativel"]
    assert compared["snapshots_iguais"]
    production = next(
        item for item in compared["indicadores"] if item["indicador"] == "Produção"
    )
    assert production["anterior"] == production["atual"]


def test_equal_indicators_have_no_material_delta():
    left, right = _pair()
    compared = compare_versions(left, right)
    assert all(not item["alterado"] for item in compared["indicadores"])


def test_different_indicators_show_an_objective_delta():
    right_summary = _summary(
        production=89, opinions=59, quotas=30, production_rate=89.0
    )
    left, right = _pair(right_summary=right_summary)
    compared = compare_versions(left, right)
    production = next(
        item for item in compared["indicadores"] if item["indicador"] == "Produção"
    )
    assert production["diferenca"] == "+9"
    assert "melhor" not in production["diferenca"].lower()
    assert compared["snapshots_iguais"] is False


def test_different_texts_are_marked_changed():
    left, right = _pair(
        left_text="Texto institucional da primeira versão completa.",
        right_text="Texto institucional da segunda versão completa.",
    )
    compared = compare_versions(left, right)
    changed = [item for item in compared["secoes"] if item["alterada"]]
    assert changed
    assert changed[0]["texto_a"]
    assert changed[0]["texto_b"]
    assert "<" not in changed[0]["texto_a"]


def test_different_snapshots_are_detected_by_hash():
    left, right = _pair(right_summary=_summary(distributed=110, production_rate=72.7))
    assert snapshot_hash(left["snapshot_dados"]) != snapshot_hash(
        right["snapshot_dados"]
    )
    assert compare_versions(left, right)["snapshots_iguais"] is False


def test_pdf_hash_difference_is_reported():
    left, right = _pair()
    compared = compare_versions(
        left,
        right,
        left_pdf=_pdf_meta(left, digest="aa" * 32),
        right_pdf=_pdf_meta(right, digest="bb" * 32),
    )
    assert compared["pdf"]["hash_a"] != compared["pdf"]["hash_b"]


def test_different_periods_cannot_be_compared():
    left = _report()
    right = _report(snapshot=_snapshot(trimestre=2, months=[4, 5, 6]), id=2, versao=1)
    compared = compare_versions(left, right)
    assert compared["compativel"] is False


def test_snapshot_and_content_hashes_differ_from_each_other():
    report = _report()
    assert snapshot_hash(report["snapshot_dados"]) != content_hash(
        report["conteudo_estruturado"]
    )
    assert snapshot_hash(report["snapshot_dados"]) == snapshot_hash(
        report["snapshot_dados"]
    )


def test_complete_quarter_without_a_report_is_suggested():
    plan = suggest_periods({2026: [7, 8, 9]}, set())
    assert any(
        item["trimestre"] == 3 and item["tipo"] == "TRIMESTRAL"
        for item in plan["sugestoes"]
    )


def test_incomplete_quarter_is_not_suggested():
    plan = suggest_periods({2026: [7, 8]}, set())
    assert not any(item["tipo"] == "TRIMESTRAL" for item in plan["sugestoes"])


def test_existing_quarter_is_not_suggested_again():
    plan = suggest_periods({2026: [7, 8, 9]}, {("TRIMESTRAL", 2026, 3)})
    assert not any(item["trimestre"] == 3 for item in plan["sugestoes"])


def test_continuous_partial_annual_is_suggested_as_partial():
    plan = suggest_periods({2026: list(range(1, 10))}, set())
    annual = next(item for item in plan["sugestoes"] if item["tipo"] == "ANUAL")
    assert annual["parcial"]
    assert "setembro" in annual["titulo"]
    assert not plan["lacunas"]


def test_annual_with_a_gap_is_not_suggested():
    plan = suggest_periods({2026: [1, 2, 3, 4, 6]}, set())
    assert not any(item["tipo"] == "ANUAL" for item in plan["sugestoes"])
    assert plan["lacunas"]
    assert "maio/2026" in plan["lacunas"][0]["mensagem"]


def test_complete_year_is_recognized_as_complete():
    plan = suggest_periods({2026: list(range(1, 13))}, set())
    annual = next(item for item in plan["sugestoes"] if item["tipo"] == "ANUAL")
    assert annual["parcial"] is False
    assert "dezembro" in annual["cobertura"]


def test_future_months_are_not_invented():
    plan = suggest_periods({2026: list(range(1, 10))}, set())
    assert not any(item.get("trimestre") == 4 for item in plan["sugestoes"])


def test_canonical_reference_numbers_pass_structural_validation(tmp_path):
    store = Store(tmp_path / "canonical.db")
    store.configure(export_dir=str(tmp_path / "exports"))
    import_reference_reports(
        store, Path(__file__).resolve().parents[1] / "referencias", "admin@test"
    )
    quarterly = build_report_snapshot(store, tipo="TRIMESTRAL", ano=2026, trimestre=3)
    annual = build_report_snapshot(store, tipo="ANUAL", ano=2026)
    assert quarterly["indicadores_gerais"]["distributed"] == 545
    assert quarterly["indicadores_gerais"]["production"] == 594
    assert quarterly["indicadores_gerais"]["opinions"] == 424
    assert quarterly["indicadores_gerais"]["quotas"] == 170
    assert round(quarterly["indicadores_gerais"]["median_days"], 1) == 7.8
    assert annual["indicadores_gerais"]["distributed"] == 1760
    assert annual["indicadores_gerais"]["production"] == 1849
    assert annual["indicadores_gerais"]["opinions"] == 1320
    assert annual["indicadores_gerais"]["quotas"] == 529
    assert round(annual["indicadores_gerais"]["median_days"], 1) == 11.0
    for snapshot in (quarterly, annual):
        report = {
            "id": 1,
            "tipo": snapshot["metadados"]["tipo"],
            "ano": snapshot["metadados"]["ano"],
            "trimestre": snapshot["metadados"]["trimestre"],
            "versao": 1,
            "status": "RASCUNHO",
            "snapshot_dados": snapshot,
            "conteudo_estruturado": EMPTY_STRUCTURED_CONTENT,
        }
        result = validate_institutional_report_version(report)
        structural = [
            item
            for item in result["checks"]
            if item["status"] == ERRO
            and item["escopo"] in {"snapshot", "cobertura", "indicadores"}
        ]
        assert not structural, structural


def test_latest_listing_does_not_load_pdf_bytes(tmp_path):
    store = Store(tmp_path / "latest.db")
    repository = InstitutionalReportsStore(store)
    snapshot = _snapshot()
    meta = snapshot["metadados"]
    report = repository.create(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio="2026-07-01",
        data_fim="2026-10-01",
        periodo_parcial=False,
        descricao_periodo="teste",
        snapshot_dados=snapshot,
        conteudo_estruturado=_content(),
        actor="admin@test",
    )
    repository.finalize(report["id"], "admin@test")
    content = b"%PDF-1.4 bytes"
    repository.save_pdf_artifact(
        report["id"],
        nome_arquivo=institutional_pdf_filename(report),
        conteudo=content,
        sha256=hashlib.sha256(content).hexdigest(),
        tamanho=len(content),
        actor="admin@test",
    )
    rows = repository.list_latest_versions()
    assert rows and rows[0]["pdf_sha256"]
    assert "conteudo" not in rows[0]
    summaries = repository.distribution_summaries(report["id"])
    assert summaries == []


def test_overview_omits_version_and_uses_human_periods():
    quarterly = overview_row(_report())
    assert list(quarterly) == [
        "Período",
        "Status",
        "Validação",
        "PDF",
        "Distribuição",
    ]
    assert quarterly["Período"] == "3º trimestre de 2026"
    assert quarterly["Status"] == "Rascunho"
    assert quarterly["PDF"] == "—"
    assert quarterly["Distribuição"] == "—"
    annual = overview_row(
        _report(
            _snapshot(tipo="ANUAL", comparison=False),
            status="EM_REVISAO",
            pdf_sha256="abc",
        )
    )
    assert annual["Período"] == "Anual — janeiro a setembro de 2026"
    assert annual["Status"] == "Em revisão"
    assert annual["PDF"] == "OK"
    frozen = overview_row(_report(status="FINALIZADO"))
    assert frozen["Distribuição"] == "Pendente"
    sent = overview_row(
        _report(status="ENVIADO", ultimo_envio="ENVIADO", pdf_sha256="abc")
    )
    assert sent["Status"] == "Enviado"
    assert sent["Distribuição"] == "Enviado"
    assert sent["PDF"] == "OK"
