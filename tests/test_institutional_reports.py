import inspect
import json
from pathlib import Path

import pytest

import services.ai_service as ai_service

from database.institutional_reports import InstitutionalReportsStore
from database.audit import AuditStore
from database.postgresql import INSTITUTIONAL_REPORTS_MIGRATION_SQL, PostgresBackend
from database.store import Store
from services.institutional_reports import (
    EMPTY_STRUCTURED_CONTENT,
    build_report_snapshot,
    can_finalize,
)
from services.institutional_report_content import (
    InstitutionalReportContentService,
    build_ai_context,
    validate_numbers,
)
from services.access import Principal
from services.relatorios_ui import (
    INSTITUTIONAL_STATUS_LABELS,
    _institutional_coverage_text,
    _institutional_covered_period,
    _institutional_snapshot_rows,
)
import services.relatorios_ui as relatorios_ui
from services.tramita_reports import import_reference_reports


def _create(repository, snapshot, actor="admin@test"):
    metadata = snapshot["metadados"]
    return repository.create(
        tipo=metadata["tipo"],
        ano=metadata["ano"],
        trimestre=metadata["trimestre"],
        data_inicio=metadata["data_inicio"],
        data_fim=metadata["data_fim"],
        periodo_parcial=metadata["periodo_parcial"],
        descricao_periodo=metadata["descricao_periodo"],
        snapshot_dados=snapshot,
        conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
        actor=actor,
    )


def test_institutional_reports_are_versioned_snapshots_from_canonical_history(tmp_path):
    store = Store(tmp_path / "institutional.db")
    import_reference_reports(
        store, Path(__file__).resolve().parents[1] / "referencias", "admin@test"
    )
    repository = InstitutionalReportsStore(store)

    quarterly = build_report_snapshot(store, tipo="TRIMESTRAL", ano=2026, trimestre=3)
    summary = quarterly["indicadores_gerais"]
    assert {
        key: summary[key]
        for key in (
            "distributed",
            "production",
            "opinions",
            "quotas",
            "nonproductive_returns",
        )
    } == {
        "distributed": 545,
        "production": 594,
        "opinions": 424,
        "quotas": 170,
        "nonproductive_returns": 0,
    }
    assert summary["production_rate"] == pytest.approx(108.9908)
    assert round(summary["median_days"], 1) == 7.8
    assert quarterly["cobertura_historica"]["meses_disponiveis"] == [7, 8, 9]
    assert quarterly["cobertura_historica"]["completo_no_periodo"]
    assert quarterly["protocolos_distintos"] > 0
    assert quarterly["faixas_permanencia"]

    draft = _create(repository, quarterly)
    assert draft["versao"] == 1 and draft["status"] == "RASCUNHO"
    with pytest.raises(ValueError, match="em elaboração"):
        _create(repository, quarterly)

    frozen = repository.finalize(draft["id"], "finalizador@test")
    assert frozen["status"] == "FINALIZADO"
    assert frozen["finalizado_por"] == "finalizador@test"
    assert repository.get(draft["id"])["snapshot_dados"] == quarterly

    new_snapshot = build_report_snapshot(
        store, tipo="TRIMESTRAL", ano=2026, trimestre=3
    )
    metadata = new_snapshot["metadados"]
    version_two = repository.create_new_version(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio=metadata["data_inicio"],
        data_fim=metadata["data_fim"],
        periodo_parcial=metadata["periodo_parcial"],
        descricao_periodo=metadata["descricao_periodo"],
        snapshot_dados=new_snapshot,
        conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
        actor="admin@test",
    )
    assert version_two["versao"] == 2 and version_two["status"] == "RASCUNHO"
    assert repository.get(draft["id"])["status"] == "FINALIZADO"

    annual = build_report_snapshot(store, tipo="ANUAL", ano=2026)
    assert annual["indicadores_gerais"]["distributed"] == 1760
    assert annual["indicadores_gerais"]["production"] == 1849
    assert annual["indicadores_gerais"]["opinions"] == 1320
    assert annual["indicadores_gerais"]["quotas"] == 529
    assert annual["metadados"]["periodo_parcial"]
    assert annual["metadados"]["trimestre"] is None
    assert annual["comparacao_periodo_anterior"] is None
    assert (
        annual["metadados"]["descricao_periodo"]
        == "Acumulado de janeiro a setembro de 2026"
    )
    assert annual["cobertura_historica"]["meses_disponiveis"] == list(range(1, 10))
    assert can_finalize(annual)
    assert _create(repository, annual)["periodo_parcial"]


def test_quarterly_snapshot_freezes_facts_and_uses_the_immediate_prior_period(tmp_path):
    store = Store(tmp_path / "quarterly-facts.db")
    import_reference_reports(
        store, Path(__file__).resolve().parents[1] / "referencias", "admin@test"
    )
    first = build_report_snapshot(store, tipo="TRIMESTRAL", ano=2026, trimestre=1)
    second = build_report_snapshot(store, tipo="TRIMESTRAL", ano=2026, trimestre=2)
    third = build_report_snapshot(store, tipo="TRIMESTRAL", ano=2026, trimestre=3)
    fourth = build_report_snapshot(store, tipo="TRIMESTRAL", ano=2026, trimestre=4)

    assert first["comparacao_periodo_anterior"] is None
    assert second["comparacao_periodo_anterior"]["periodo"] == "1º trimestre de 2026"
    assert third["comparacao_periodo_anterior"]["periodo"] == "2º trimestre de 2026"
    assert fourth["comparacao_periodo_anterior"]["periodo"] == "3º trimestre de 2026"
    facts = third["fatos_trimestrais"]
    assert facts["total_producao"] == third["indicadores_gerais"]["production"]
    assert facts["total_distribuicoes"] == third["indicadores_gerais"]["distributed"]
    assert facts["saldo_fluxo"] == pytest.approx(
        facts["total_producao"] - facts["total_distribuicoes"]
    )
    assert facts["p75_permanencia"] is not None
    assert facts["p90_permanencia"] is not None
    assert facts["quantidade_mais_60"] == (
        facts["quantidade_61_90"] + facts["quantidade_mais_90"]
    )
    assert facts["percentual_mais_90"] <= facts["percentual_mais_60"]
    comparison = facts["comparacao"]
    assert comparison["periodo_anterior_identificado"] == "2º trimestre de 2026"
    assert comparison["relacao_producao_distribuicoes"]["delta_pontos_percentuais"] == pytest.approx(
        third["indicadores_gerais"]["production_rate"]
        - third["comparacao_periodo_anterior"]["production_rate"]
    )


def test_institutional_report_schema_validates_period_type_and_version(tmp_path):
    store = Store(tmp_path / "schema.db")
    repository = InstitutionalReportsStore(store)
    with pytest.raises(ValueError, match="trimestre"):
        repository.latest_for_period("TRIMESTRAL", 2026, 5)
    with store.connection(read_only=True) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(relatorios_institucionais)"
            )
        }
    assert {"snapshot_dados", "conteudo_estruturado", "versao", "data_corte"} <= columns


def test_postgresql_migration_prepares_jsonb_snapshot_and_version_three():
    migration = " ".join(INSTITUTIONAL_REPORTS_MIGRATION_SQL)
    assert "relatorios_institucionais" in migration
    assert "JSONB" in migration
    assert "COALESCE(trimestre,0),versao" in migration
    assert "VALUES(3)" in inspect.getsource(
        PostgresBackend._migrate_institutional_reports
    )


def test_institutional_preview_uses_friendly_covered_period_and_frozen_rows():
    snapshot = {
        "metadados": {
            "tipo": "ANUAL",
            "ano": 2026,
            "trimestre": None,
            "data_inicio": "2026-01-01",
            "data_fim": "2027-01-01",
            "periodo_parcial": True,
        },
        "cobertura_historica": {
            "meses_disponiveis": list(range(1, 10)),
            "meses_ausentes": [],
            "lacunas_no_ano": [],
        },
        "serie_mensal": [
            {
                "month": 9,
                "summary": {
                    "distributed": 169,
                    "production": 155,
                    "opinions": 117,
                    "quotas": 38,
                    "production_rate": 91.7,
                    "median_days": 9.1,
                },
            }
        ],
    }
    assert _institutional_covered_period(snapshot) == (
        "Período coberto: 01/01/2026 a 30/09/2026"
    )
    assert "janeiro a setembro de 2026" in _institutional_coverage_text(snapshot)
    assert "sem lacunas" in _institutional_coverage_text(snapshot)
    assert _institutional_snapshot_rows(snapshot) == [
        {
            "Mês": "Setembro",
            "Distribuídos": 169,
            "Produção": 155,
            "Pareceres": 117,
            "Cotas": 38,
            "Produção/Distribuições": 91.7,
            "Mediana de permanência": 9.1,
        }
    ]
    assert INSTITUTIONAL_STATUS_LABELS["FINALIZADO"] == "Finalizado"


def _content_snapshot():
    return {
        "metadados": {
            "tipo": "TRIMESTRAL",
            "ano": 2026,
            "trimestre": 3,
            "descricao_periodo": "01/07 a 30/09/2026",
            "periodo_parcial": False,
            "data_inicio": "2026-07-01",
            "data_fim": "2026-10-01",
        },
        "indicadores_gerais": {
            "distributed": 545,
            "production": 594,
            "opinions": 424,
            "quotas": 170,
            "production_rate": 109.0,
            "median_days": 7.8,
        },
        "serie_mensal": [
            {"month": 7, "summary": {"distributed": 221, "production": 227}}
        ],
        "composicao_producao": {"pareceres_percentual": 71.4, "cotas_percentual": 28.6},
        "por_procurador": [{"procurador": "A", "distributed": 10, "production": 12}],
        "faixas_permanencia": [{"faixa": "0–7 dias", "quantidade": 100}],
        "comparacao_periodo_anterior": None,
        "cobertura_historica": {
            "meses_disponiveis": [7, 8, 9],
            "meses_ausentes": [],
            "lacunas_no_ano": [],
        },
        "nota_metodologica": {"producao": "Produção considera Parecer e Cota."},
    }


def _admin():
    return Principal(
        1, "Admin", "admin@test", "ADMINISTRADOR", True, False, False, False, True, ()
    )


def test_content_generation_manual_edits_and_finalized_read_only(tmp_path, monkeypatch):
    store = Store(tmp_path / "content.db")
    repository = InstitutionalReportsStore(store)
    snapshot = _content_snapshot()
    report = repository.create(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio="2026-07-01",
        data_fim="2026-10-01",
        periodo_parcial=False,
        descricao_periodo="T3",
        snapshot_dados=snapshot,
        conteudo_estruturado={},
        actor="admin@test",
    )
    service = InstitutionalReportContentService(store)
    generated = {
        key: f"Texto com {545 if key == 'resumo_executivo' else 594}."
        for key in (
            "resumo_executivo",
            "evolucao_periodo",
            "composicao_producao",
            "permanencia",
            "producao_procurador",
            "comparacao_periodo_anterior",
            "sintese_pontos_atencao",
        )
    }
    monkeypatch.setattr(
        "services.institutional_report_content.ai_service.gerar_conteudo_relatorio_institucional",
        lambda context, secao=None: (
            (
                {secao: "Nova seção com 545."}
                if isinstance(secao, str)
                else {
                    key: generated[key]
                    for key in (generated if secao is None else secao)
                }
            ),
            "modelo-teste",
        ),
    )
    updated = service.generate_all(report["id"], _admin())
    assert updated["status"] == "EM_REVISAO"
    assert updated["conteudo_estruturado"]["resumo_executivo"]["gerado_por_ia"]
    assert updated["snapshot_dados"] == snapshot
    assert AuditStore(store).count({"evento": "RELATORIO_INSTITUCIONAL_IA_GERADA"}) == 1
    regenerated = service.regenerate(report["id"], "permanencia", _admin())
    assert (
        regenerated["conteudo_estruturado"]["permanencia"]["texto"]
        == "Nova seção com 545."
    )
    assert (
        regenerated["conteudo_estruturado"]["resumo_executivo"]["texto"]
        == generated["resumo_executivo"]
    )
    saved = service.save_manual(
        report["id"], {"resumo_executivo": "Texto manual com 545."}, _admin()
    )
    assert saved["conteudo_estruturado"]["resumo_executivo"]["editado_manualmente"]
    repository.finalize(report["id"], "admin@test")
    with pytest.raises(ValueError, match="somente leitura"):
        service.save_manual(report["id"], {"resumo_executivo": "não salvar"}, _admin())


def test_ai_context_excludes_raw_events_and_flags_unknown_numbers():
    snapshot = _content_snapshot()
    context = build_ai_context(snapshot)
    assert "eventos" not in context and "tramita_movimentacoes" not in str(context)
    assert validate_numbers(
        "Foram registrados 545 eventos e 999 processos.", snapshot
    ) == ["999"]
    assert (
        validate_numbers("A proporção foi 109,0% e a mediana 7.8 dias.", snapshot) == []
    )


def test_ai_generation_uses_one_structured_call_and_validates_sections(monkeypatch):
    expected = {
        section: "Texto institucional." for section in ai_service.RELATORIO_SECOES_IA
    }
    raw = {"candidates": [{"content": {"parts": [{"text": json.dumps(expected)}]}}]}
    calls = []
    monkeypatch.setattr(
        ai_service,
        "_executar",
        lambda mount, label, operation: (
            calls.append(operation) or json.dumps(raw).encode(),
            "modelo-teste",
        ),
    )
    result, model = ai_service.gerar_conteudo_relatorio_institucional(
        {"indicadores_gerais": {"distributed": 545}}
    )
    assert result == expected and model == "modelo-teste"
    assert calls == ["relatorio_conteudo"]


def test_institutional_render_error_is_localized_and_does_not_escape(monkeypatch):
    messages = []
    monkeypatch.setattr(
        relatorios_ui,
        "_institutional_workspace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("falha simulada")),
    )
    monkeypatch.setattr(relatorios_ui.st, "error", messages.append)
    relatorios_ui.institutional_reports(None, None)
    assert len(messages) == 1
    assert "relatórios institucionais" in messages[0]
    assert "Não foi possível carregar os indicadores" not in messages[0]
