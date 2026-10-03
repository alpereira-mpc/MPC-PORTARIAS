import inspect
from pathlib import Path

import pytest

from database.institutional_reports import InstitutionalReportsStore
from database.postgresql import INSTITUTIONAL_REPORTS_MIGRATION_SQL, PostgresBackend
from database.store import Store
from services.institutional_reports import (
    EMPTY_STRUCTURED_CONTENT,
    build_report_snapshot,
    can_finalize,
)
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
    assert annual["cobertura_historica"]["meses_disponiveis"] == list(range(1, 10))
    assert can_finalize(annual)
    assert _create(repository, annual)["periodo_parcial"]


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
