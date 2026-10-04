"""Phase 4 PDF: frozen snapshot, persisted prose, and one official file per version."""

import inspect
import shutil
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest
from pypdf import PdfReader
from streamlit.testing.v1 import AppTest

from database.institutional_reports import InstitutionalReportsStore
from database.postgresql import INSTITUTIONAL_REPORTS_MIGRATION_SQL
from database.store import Store
from document_generator.institutional_report_pdf import (
    ANNUAL_WITHOUT_PRIOR,
    CHART_COLORS,
    LOGO,
    generate_institutional_report_pdf,
    institutional_pdf_filename,
    period_label,
)
from services.access import Principal
from services.branding import SIDEBAR_LOGO
from services.institutional_reports import (
    EMPTY_STRUCTURED_CONTENT,
    build_report_snapshot,
)
from services.institutional_report_pdf import (
    deliver_institutional_pdf,
    pdf_sha256,
    regenerate_official_pdf,
)
from services.relatorios_ui import CHART_COLORS as UI_COLORS
import services.relatorios_ui as relatorios_ui
from services.tramita_reports import import_reference_reports


EMITTED = datetime(2026, 10, 3, 9, 0, 0)


def _admin():
    return Principal(
        1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
    )


def _load_reference(path):
    store = Store(path)
    import_reference_reports(
        store, Path(__file__).resolve().parents[1] / "referencias", "admin@test"
    )
    quarterly = build_report_snapshot(store, tipo="TRIMESTRAL", ano=2026, trimestre=3)
    annual = build_report_snapshot(store, tipo="ANUAL", ano=2026)
    return store, quarterly, annual


@pytest.fixture(scope="module")
def reference(tmp_path_factory):
    return _load_reference(tmp_path_factory.mktemp("pdf-ref") / "ref.db")


def _content(**sections):
    content = {
        key: {"texto": value}
        for key, value in {
            "resumo_executivo": "Resumo congelado da versão.",
            "evolucao_periodo": "Evolução congelada do período.",
            "composicao_producao": "Composição congelada da produção.",
            "permanencia": "Permanência congelada do período.",
            "producao_procurador": "Produção congelada por Procurador.",
            "comparacao_periodo_anterior": "Comparação congelada do período.",
            "sintese_pontos_atencao": (
                "Síntese congelada do período.\n\nPontos de atenção\n"
                "- Ponto de atenção congelado."
            ),
            "nota_metodologica": "",
        }.items()
    }
    for key, value in sections.items():
        content[key] = {"texto": value}
    return content


def _report(snapshot, **extra):
    metadata = snapshot["metadados"]
    report = {
        "id": 1,
        "tipo": metadata["tipo"],
        "ano": metadata["ano"],
        "trimestre": metadata["trimestre"],
        "versao": 1,
        "status": "FINALIZADO",
        "data_corte": "2026-10-03T09:00:00",
        "snapshot_dados": snapshot,
        "conteudo_estruturado": _content(),
    }
    report.update(extra)
    return report


def _pdf_text(payload):
    reader = PdfReader(BytesIO(payload))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    return text, len(reader.pages)


def _persist(repository, snapshot, content=None, **extra):
    metadata = snapshot["metadados"]
    created = repository.create(
        tipo=metadata["tipo"],
        ano=metadata["ano"],
        trimestre=metadata["trimestre"],
        data_inicio=metadata["data_inicio"],
        data_fim=metadata["data_fim"],
        periodo_parcial=metadata["periodo_parcial"],
        descricao_periodo=metadata["descricao_periodo"],
        snapshot_dados=snapshot,
        conteudo_estruturado=content or _content(),
        actor="admin@test",
    )
    if extra.get("status", "FINALIZADO") == "FINALIZADO":
        created = repository.finalize(created["id"], "admin@test")
    if content is None and extra.get("replace_content"):
        created = repository.save_content(
            created["id"], extra["replace_content"], "admin@test"
        )
    return created


def test_palette_logo_and_filename_follow_the_institutional_version():
    import document_generator.institutional_report_pdf as pdf_module

    assert CHART_COLORS == UI_COLORS
    assert LOGO == SIDEBAR_LOGO
    quarterly = institutional_pdf_filename(
        {"tipo": "TRIMESTRAL", "ano": 2026, "trimestre": 3, "versao": 2}
    )
    annual = institutional_pdf_filename(
        {"tipo": "ANUAL", "ano": 2026, "trimestre": None, "versao": 1}
    )
    assert quarterly == "Relatorio_Trimestral_MPCPB_2026_T3_v2.pdf"
    assert annual == "Relatorio_Anual_MPCPB_2026_v1.pdf"
    source = inspect.getsource(pdf_module)
    assert "period_data" not in source
    assert "TramitaReportsStore" not in source
    assert "read_excel" not in source


def test_quarterly_pdf_uses_the_frozen_snapshot_and_persisted_text(reference):
    _store, quarterly, _annual = reference
    report = _report(
        quarterly,
        conteudo_estruturado=_content(
            resumo_executivo="Resumo exclusivo congelado com o valor 545."
        ),
    )
    payload = generate_institutional_report_pdf(report, emitido_em=EMITTED)
    text, pages = _pdf_text(payload)
    first = PdfReader(BytesIO(payload)).pages[0].extract_text() or ""
    assert "TRIMESTRAL" in first
    assert "Data de emissão" in first
    assert payload.startswith(b"%PDF")
    assert len(payload) > 2000
    assert 4 <= pages <= 12
    assert "545" in text
    assert "594" in text
    assert "424" in text
    assert "170" in text
    assert "7,8" in text
    assert "3º trimestre de 2026" in text
    assert "Versão 1" in text
    assert "Resumo exclusivo congelado com o valor 545." in text
    assert "Elvira Samara Pereira de Oliveira" in text
    assert "Nota metodológica" in text
    assert "Parecer ou Cota" in text
    assert "Pareceres" in text
    assert "opiniões" not in text.lower()
    assert "opinions" not in text.lower()
    assert "Fonte: Tramita/TCE-PB" in text
    assert "30/09/2026" in text
    assert "Página 1 /" in text
    assert "RASCUNHO" not in text
    assert "documento em elaboração" not in text
    assert "relatorios-indicadores-2026-01" not in text
    repeated = generate_institutional_report_pdf(report, emitido_em=EMITTED)
    assert pdf_sha256(payload) == pdf_sha256(repeated)
    assert repeated == payload


def test_partial_annual_pdf_states_coverage_and_skips_empty_comparison(reference):
    _store, _quarterly, annual = reference
    assert annual["comparacao_periodo_anterior"] is None
    report = _report(annual, conteudo_estruturado=_content())
    text, pages = _pdf_text(
        generate_institutional_report_pdf(report, emitido_em=EMITTED)
    )
    assert 4 <= pages <= 12
    assert "1.760" in text
    assert "1.849" in text
    assert "1.320" in text
    assert "529" in text
    assert "11,0" in text
    assert period_label(annual) == "Acumulado de janeiro a setembro de 2026"
    assert "Acumulado de janeiro a setembro de 2026" in text
    assert ANNUAL_WITHOUT_PRIOR in text
    assert "Período atual e período anterior" not in text
    assert "RELATÓRIO ANUAL DE PRODUÇÃO" in text
    assert "Pareceres" in text
    assert "opiniões" not in text.lower()
    assert "opinions" not in text.lower()


def test_draft_is_marked_and_review_is_not_a_final_document(reference):
    _store, quarterly, _annual = reference
    draft = _report(quarterly, status="RASCUNHO", versao=2)
    draft_text, _pages = _pdf_text(
        generate_institutional_report_pdf(draft, emitido_em=EMITTED)
    )
    review = _report(quarterly, status="EM_REVISAO", versao=3)
    review_text, _pages = _pdf_text(
        generate_institutional_report_pdf(review, emitido_em=EMITTED)
    )
    assert "RASCUNHO" in draft_text
    assert "documento em elaboração" in draft_text
    assert "EM REVISÃO" in review_text
    assert "RASCUNHO" not in review_text


def test_missing_or_hostile_content_does_not_break_the_pdf(reference):
    _store, quarterly, annual = reference
    empty = dict(quarterly)
    empty["serie_mensal"] = []
    empty["por_procurador"] = []
    empty["faixas_permanencia"] = []
    empty["comparacao_periodo_anterior"] = None
    report = _report(
        empty,
        conteudo_estruturado=_content(
            resumo_executivo="Texto com marca <b>545</b> & gráfico vazio.",
            evolucao_periodo="",
            composicao_producao="",
            permanencia="",
            producao_procurador="",
            comparacao_periodo_anterior="",
            sintese_pontos_atencao="",
        ),
    )
    text, _pages = _pdf_text(
        generate_institutional_report_pdf(report, emitido_em=EMITTED)
    )
    assert "545" in text
    assert "Texto com marca" in text
    annual_empty = dict(annual)
    annual_empty["serie_mensal"] = [{"month": None}]
    annual_empty["por_procurador"] = [{"procurador": None}]
    annual_report = _report(annual_empty, conteudo_estruturado=EMPTY_STRUCTURED_CONTENT)
    payload = generate_institutional_report_pdf(annual_report, emitido_em=EMITTED)
    assert payload.startswith(b"%PDF")
    annual_text, _pages = _pdf_text(payload)
    assert ANNUAL_WITHOUT_PRIOR in annual_text
    assert "Resumo ainda não elaborado" not in annual_text


def test_selected_version_controls_the_pdf_and_history_cannot_rewrite_it(
    reference, tmp_path
):
    source, quarterly, _annual = reference
    with source.connection() as connection:
        connection.execute("PRAGMA wal_checkpoint(FULL)")
    clone = tmp_path / "clone.db"
    shutil.copy(source.path, clone)
    store = Store(clone)
    repository = InstitutionalReportsStore(store)
    first = _persist(
        repository,
        quarterly,
        _content(resumo_executivo="Texto exclusivo da versão 1."),
    )
    second_snapshot = dict(quarterly)
    second_snapshot["indicadores_gerais"] = dict(quarterly["indicadores_gerais"])
    second_snapshot["indicadores_gerais"]["distributed"] = 999
    metadata = second_snapshot["metadados"]
    second = repository.create_new_version(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio=metadata["data_inicio"],
        data_fim=metadata["data_fim"],
        periodo_parcial=metadata["periodo_parcial"],
        descricao_periodo=metadata["descricao_periodo"],
        snapshot_dados=second_snapshot,
        conteudo_estruturado=_content(resumo_executivo="Texto exclusivo da versão 2."),
        actor="admin@test",
    )
    original = generate_institutional_report_pdf(
        repository.get(first["id"]), emitido_em=EMITTED
    )
    original_text, _pages = _pdf_text(original)
    with store.connection() as connection:
        connection.execute("DELETE FROM tramita_movimentacoes")
    again = generate_institutional_report_pdf(
        repository.get(first["id"]), emitido_em=EMITTED
    )
    again_text, _pages = _pdf_text(again)
    other = generate_institutional_report_pdf(
        repository.get(second["id"]), emitido_em=EMITTED
    )
    other_text, _pages = _pdf_text(other)
    assert original == again
    assert "Texto exclusivo da versão 1." in original_text
    assert "Texto exclusivo da versão 2." not in original_text
    assert "545" in again_text
    assert "Texto exclusivo da versão 2." in other_text
    assert "999" in other_text
    assert "Texto exclusivo da versão 1." not in other_text
    assert second["versao"] == 2 and second["status"] == "RASCUNHO"


def test_official_pdf_is_stored_once_and_a_preview_is_not(
    reference, tmp_path, monkeypatch
):
    source, quarterly, _annual = reference
    with source.connection() as connection:
        connection.execute("PRAGMA wal_checkpoint(FULL)")
    clone = tmp_path / "official.db"
    shutil.copy(source.path, clone)
    store = Store(clone)
    repository = InstitutionalReportsStore(store)
    draft = repository.create(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio=quarterly["metadados"]["data_inicio"],
        data_fim=quarterly["metadados"]["data_fim"],
        periodo_parcial=quarterly["metadados"]["periodo_parcial"],
        descricao_periodo=quarterly["metadados"]["descricao_periodo"],
        snapshot_dados=quarterly,
        conteudo_estruturado=_content(),
        actor="admin@test",
    )
    preview = deliver_institutional_pdf(store, draft, _admin(), official=False)
    assert preview["nome"] == "Relatorio_Trimestral_MPCPB_2026_T3_v1.pdf"
    assert preview["conteudo"].startswith(b"%PDF")
    assert preview["sha256"] == pdf_sha256(preview["conteudo"])
    assert repository.pdf_artifact(draft["id"]) is None
    assert repository.get(draft["id"])["status"] == "RASCUNHO"
    with pytest.raises(ValueError, match="finalizada"):
        repository.save_pdf_artifact(
            draft["id"],
            nome_arquivo="x.pdf",
            conteudo=b"%PDF",
            sha256="abc",
            tamanho=4,
            actor="admin@test",
        )

    final = repository.finalize(draft["id"], "admin@test")
    calls = {"count": 0}
    original = generate_institutional_report_pdf

    def counting(report, emitido_em=None):
        calls["count"] += 1
        return original(report, emitido_em=emitido_em)

    monkeypatch.setattr(
        "services.institutional_report_pdf.generate_institutional_report_pdf", counting
    )
    first = deliver_institutional_pdf(store, final, _admin(), official=True)
    stored = repository.pdf_artifact(final["id"])
    assert stored["sha256"] == first["sha256"]
    assert stored["nome_arquivo"] == first["nome"]
    assert stored["gerado_por"] == "admin@test"
    assert stored["conteudo"] == first["conteudo"]
    assert calls["count"] == 1
    second = deliver_institutional_pdf(
        store, repository.get(final["id"]), _admin(), official=True
    )
    assert calls["count"] == 1
    assert second["conteudo"] == first["conteudo"]
    assert repository.get(final["id"])["status"] == "FINALIZADO"
    with pytest.raises(ValueError, match="somente leitura"):
        repository.save_content(final["id"], _content(), "admin@test")

    def broken(report, emitido_em=None):
        raise RuntimeError("falha controlada")

    monkeypatch.setattr(
        "services.institutional_report_pdf.generate_institutional_report_pdf", broken
    )
    with pytest.raises(RuntimeError):
        regenerate_official_pdf(store, repository.get(final["id"]), _admin())
    assert repository.pdf_artifact(final["id"])["sha256"] == first["sha256"]
    rows = repository.list_for_period("TRIMESTRAL", 2026, 3)
    assert all("sha256" not in row for row in rows)


def test_sqlite_and_postgres_migrations_keep_the_pdf_beside_the_version(tmp_path):
    store = Store(tmp_path / "schema.db")
    InstitutionalReportsStore(store)
    with store.connection(read_only=True) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "relatorios_institucionais_pdf" in tables
    migration = " ".join(INSTITUTIONAL_REPORTS_MIGRATION_SQL)
    assert "relatorios_institucionais_pdf" in migration
    assert "BYTEA" in migration


def test_pdf_action_stays_in_institutional_visualization(tmp_path, monkeypatch):
    quarterly_source = inspect.getsource(relatorios_ui.quarterly)
    annual_source = inspect.getsource(relatorios_ui.annual)
    assert "Baixar PDF" not in quarterly_source
    assert "Baixar prévia" not in quarterly_source
    assert "Baixar PDF" not in annual_source
    workspace = inspect.getsource(relatorios_ui._institutional_workspace)
    assert "_render_institutional_pdf_action" in workspace

    database = tmp_path / "ui.db"
    store = Store(database)
    repository = InstitutionalReportsStore(store)
    snapshot = {
        "metadados": {
            "tipo": "TRIMESTRAL",
            "ano": 2026,
            "trimestre": 3,
            "data_inicio": "2026-07-01",
            "data_fim": "2026-10-01",
            "periodo_parcial": False,
            "descricao_periodo": "3º trimestre de 2026",
        },
        "indicadores_gerais": {
            "distributed": 545,
            "production": 594,
            "opinions": 424,
            "quotas": 170,
            "production_rate": 109.0,
            "median_days": 7.8,
        },
        "serie_mensal": [],
        "por_procurador": [
            {
                "procurador": "Elvira Samara Pereira de Oliveira",
                "distributed": 73,
                "production": 87,
                "opinions": 63,
                "quotas": 24,
                "production_rate": 119.2,
                "median_days": 20.1,
            }
        ],
        "faixas_permanencia": [],
        "comparacao_periodo_anterior": None,
        "cobertura_historica": {
            "meses_disponiveis": [7, 8, 9],
            "meses_ausentes": [],
            "lacunas_no_ano": [],
        },
        "nota_metodologica": {
            "producao": "Produção considera somente saídas classificadas como Parecer ou Cota."
        },
    }
    repository.create(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio="2026-07-01",
        data_fim="2026-10-01",
        periodo_parcial=False,
        descricao_periodo="3º trimestre de 2026",
        snapshot_dados=snapshot,
        conteudo_estruturado=_content(),
        actor="admin@test",
    )
    monkeypatch.setenv("MPC_PDF_UI_DB", str(database))
    monkeypatch.setattr(
        "database.tramita_reports.TramitaReportsStore.historical_years",
        lambda self: [2026],
    )
    monkeypatch.setattr(
        "database.tramita_reports.TramitaReportsStore.months_for_year",
        lambda self, year: [7, 8, 9],
    )

    def page():
        import os

        from database.store import Store
        from services.access import Principal
        from services.relatorios_ui import institutional_reports

        institutional_reports(
            Store(os.environ["MPC_PDF_UI_DB"]),
            Principal(
                1,
                "Admin",
                "admin@test",
                "ADMINISTRADOR",
                True,
                True,
                True,
                True,
                True,
                (),
            ),
        )

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception
    labels = [button.label for button in app.button]
    assert "Baixar prévia em PDF" in labels
    assert "Baixar PDF" not in labels

    def fail(store, report, principal, *, official):
        raise RuntimeError("pdf indisponível")

    monkeypatch.setattr(
        "services.institutional_report_pdf.deliver_institutional_pdf", fail
    )
    prepare = next(
        button for button in app.button if button.label == "Baixar prévia em PDF"
    )
    app = prepare.click().run()
    assert not app.exception
    errors = " ".join(item.value for item in app.error)
    assert "Não foi possível gerar o PDF desta versão no momento." in errors
    assert any("Relatórios Institucionais" in item.value for item in app.subheader)
    assert not any(
        "Não foi possível carregar os indicadores" in item.value for item in app.error
    )
