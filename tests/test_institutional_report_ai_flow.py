"""Conteúdo e revisão: geração, regeneração, falhas e bloqueio por status."""

import pytest
from streamlit.testing.v1 import AppTest

from database.institutional_reports import InstitutionalReportsStore
from database.store import Store
from services.access import Principal
from services.institutional_report_content import (
    AI_SECTIONS,
    InstitutionalReportContentService,
)
from services.institutional_reports import EMPTY_STRUCTURED_CONTENT
import services.relatorios_ui as relatorios_ui


SNAPSHOT = {
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
    "serie_mensal": [{"month": 7, "summary": {"distributed": 221, "production": 227}}],
    "composicao_producao": {"pareceres_percentual": 71.4, "cotas_percentual": 28.6},
    "por_procurador": [{"procurador": "A", "distributed": 10, "production": 12}],
    "faixas_permanencia": [{"faixa": "0-7 dias", "quantidade": 100}],
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
        1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
    )


def _fake_ai(_context, secao=None):
    if isinstance(secao, str):
        sections = (secao,)
        prefix = "REGEN"
    elif secao:
        sections = tuple(secao)
        prefix = "GERADO"
    else:
        sections = AI_SECTIONS
        prefix = "GERADO"
    return {key: f"{prefix} {key}." for key in sections}, "modelo-teste"


def _unconfigured(_context, secao=None):
    from services.ai_service import GeminiNaoConfigurada

    raise GeminiNaoConfigurada()


def _unexpected(_context, secao=None):
    raise RuntimeError("detalhe interno que nao pode vazar")


def _patch(monkeypatch, function):
    monkeypatch.setattr(
        "services.institutional_report_content.ai_service.gerar_conteudo_relatorio_institucional",
        function,
    )


def _create(tmp_path):
    database = tmp_path / "ai-flow.db"
    store = Store(database)
    repository = InstitutionalReportsStore(store)
    report = repository.create(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio="2026-07-01",
        data_fim="2026-10-01",
        periodo_parcial=False,
        descricao_periodo="T3",
        snapshot_dados=SNAPSHOT,
        conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
        actor="admin@test",
    )
    return database, store, repository, report


def _bind(monkeypatch, database, report):
    monkeypatch.setenv("MPC_AI_FLOW_DB", str(database))
    monkeypatch.setenv("MPC_AI_FLOW_ID", str(report["id"]))


def _editor_page():
    import os

    from database.institutional_reports import InstitutionalReportsStore
    from database.store import Store
    from services.access import Principal
    import services.relatorios_ui as ui

    local = Store(os.environ["MPC_AI_FLOW_DB"])
    repository = InstitutionalReportsStore(local)
    report = repository.get(int(os.environ["MPC_AI_FLOW_ID"]))
    admin = Principal(
        1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
    )
    ui._consume_flash()
    ui._render_institutional_editor(
        local,
        admin,
        report,
        is_latest=True,
        tipo=report["tipo"],
        year=report["ano"],
        quarter=report["trimestre"],
        versions=repository.list_for_period(
            report["tipo"], report["ano"], report["trimestre"]
        ),
    )


def _open(monkeypatch, database, report):
    _bind(monkeypatch, database, report)
    app = AppTest.from_function(_editor_page, default_timeout=30).run()
    assert not app.exception, app.exception
    return app


def _values(app):
    return {area.key: area.value for area in app.text_area}


def _shown(elements):
    return "\n".join(element.value for element in elements)


def test_draft_generation_persists_and_reruns(tmp_path, monkeypatch):
    _patch(monkeypatch, _fake_ai)
    database, _store, repository, report = _create(tmp_path)
    app = _open(monkeypatch, database, report)
    assert relatorios_ui.AI_GENERATE_LABEL in [button.label for button in app.button]
    assert relatorios_ui.AI_REGENERATE_LABEL not in [
        button.label for button in app.button
    ]
    assert any(button.label == "Marcar para revisão" for button in app.button)
    generate = app.button(key=f"inst_ai_all_{report['id']}")
    assert generate.proto.icon == ":material/auto_awesome:"
    assert all(
        button.proto.icon == ":material/auto_awesome:"
        for button in app.button
        if button.label == relatorios_ui.AI_REGENERATE_LABEL
    )
    app = generate.click().run()
    assert not app.exception, app.exception
    assert relatorios_ui.AI_GENERATE_SUCCESS in _shown(app.success)
    saved = repository.get(report["id"])
    assert saved["status"] == "EM_REVISAO"
    values = _values(app)
    assert values[f"inst_text_{report['id']}_resumo_executivo"] == (
        "GERADO resumo_executivo."
    )
    assert (
        saved["conteudo_estruturado"]["resumo_executivo"]["texto"]
        == "GERADO resumo_executivo."
    )
    assert saved["conteudo_estruturado"]["resumo_executivo"]["gerado_por_ia"]
    assert (
        saved["conteudo_estruturado"]["resumo_executivo"]["prompt_version"]
        == "editorial-2026-10"
    )
    assert (
        values[f"inst_text_{report['id']}_comparacao_periodo_anterior"]
        == "Não há período anterior disponível para comparação."
    )
    assert not any(button.label == "Marcar para revisão" for button in app.button)
    assert "Em revisão" in _shown(app.caption)
    assert not any(
        button.key == f"inst_regen_{report['id']}_comparacao_periodo_anterior"
        for button in app.button
    )
    assert "Parecer" in values[f"inst_text_{report['id']}_nota_metodologica"]
    assert "inst_refresh_text" not in app.session_state


def test_review_with_empty_content_can_generate(tmp_path, monkeypatch):
    _patch(monkeypatch, _fake_ai)
    database, _store, repository, report = _create(tmp_path)
    repository.save_content(
        report["id"], EMPTY_STRUCTURED_CONTENT, "admin@test", status="EM_REVISAO"
    )
    app = _open(monkeypatch, database, report)
    assert repository.get(report["id"])["status"] == "EM_REVISAO"
    assert not any(button.label == "Marcar para revisão" for button in app.button)
    assert "Em revisão" in _shown(app.caption)
    app.button(key=f"inst_ai_all_{report['id']}").click().run()
    assert not app.exception, app.exception
    assert relatorios_ui.AI_GENERATE_SUCCESS in _shown(app.success)
    saved = repository.get(report["id"])
    assert saved["status"] == "EM_REVISAO"
    assert saved["conteudo_estruturado"]["evolucao_periodo"]["texto"] == (
        "GERADO evolucao_periodo."
    )
    assert _values(app)[f"inst_text_{report['id']}_evolucao_periodo"] == (
        "GERADO evolucao_periodo."
    )


def test_section_regeneration_persists_only_that_section(tmp_path, monkeypatch):
    _patch(monkeypatch, _fake_ai)
    database, store, repository, report = _create(tmp_path)
    InstitutionalReportContentService(store).generate_all(report["id"], _admin())
    app = _open(monkeypatch, database, report)
    app.button(key=f"inst_regen_{report['id']}_permanencia").click().run()
    assert not app.exception, app.exception
    assert relatorios_ui.AI_REGENERATE_SUCCESS in _shown(app.success)
    saved = repository.get(report["id"])
    content = saved["conteudo_estruturado"]
    assert content["permanencia"]["texto"] == "REGEN permanencia."
    assert content["resumo_executivo"]["texto"] == "GERADO resumo_executivo."
    assert (
        content["sintese_pontos_atencao"]["texto"] == "GERADO sintese_pontos_atencao."
    )
    values = _values(app)
    assert values[f"inst_text_{report['id']}_permanencia"] == "REGEN permanencia."
    assert values[f"inst_text_{report['id']}_resumo_executivo"] == (
        "GERADO resumo_executivo."
    )


def test_typed_text_does_not_stay_after_generation(tmp_path, monkeypatch):
    _patch(monkeypatch, _fake_ai)
    database, _store, _repository, report = _create(tmp_path)
    app = _open(monkeypatch, database, report)
    widget = f"inst_text_{report['id']}_resumo_executivo"
    app.text_area(key=widget).set_value("TEXTO PRESO NA SESSAO").run()
    assert app.text_area(key=widget).value == "TEXTO PRESO NA SESSAO"
    app.button(key=f"inst_ai_all_{report['id']}").click().run()
    assert not app.exception, app.exception
    assert app.text_area(key=widget).value == "GERADO resumo_executivo."
    assert "TEXTO PRESO NA SESSAO" not in _values(app).values()
    assert "inst_refresh_text" not in app.session_state
    keys = [button.key for button in app.button] + list(_values(app))
    assert len(keys) == len(set(keys))
    assert all(not str(key).startswith("inst_refresh_text") for key in keys)


def test_provider_failure_keeps_the_page_and_previous_text(tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr(
        relatorios_ui.LOGGER,
        "exception",
        lambda message, *args, **kwargs: logged.append(message),
    )
    _patch(monkeypatch, _fake_ai)
    database, store, repository, report = _create(tmp_path)
    InstitutionalReportContentService(store).generate_all(report["id"], _admin())
    before = repository.get(report["id"])["conteudo_estruturado"]
    _patch(monkeypatch, _unconfigured)
    app = _open(monkeypatch, database, report)
    app.button(key=f"inst_regen_{report['id']}_permanencia").click().run()
    assert not app.exception, app.exception
    assert "serviço de IA não configurado" in _shown(app.error)
    assert app.text_area
    assert _values(app)[f"inst_text_{report['id']}_permanencia"] == (
        "GERADO permanencia."
    )
    assert _values(app)[f"inst_text_{report['id']}_resumo_executivo"] == (
        "GERADO resumo_executivo."
    )
    assert repository.get(report["id"])["conteudo_estruturado"] == before
    assert relatorios_ui.AI_REGENERATE_FAILURE in logged


def test_unexpected_failure_does_not_crash_or_erase_content(tmp_path, monkeypatch):
    logged = []
    monkeypatch.setattr(
        relatorios_ui.LOGGER,
        "exception",
        lambda message, *args, **kwargs: logged.append(message),
    )
    _patch(monkeypatch, _fake_ai)
    database, store, repository, report = _create(tmp_path)
    InstitutionalReportContentService(store).generate_all(report["id"], _admin())
    before = repository.get(report["id"])["conteudo_estruturado"]
    _patch(monkeypatch, _unexpected)
    app = _open(monkeypatch, database, report)
    app.button(key=f"inst_ai_all_{report['id']}").click().run()
    app.button(key=f"inst_ai_replace_confirm_{report['id']}").click().run()
    assert not app.exception, app.exception
    visible = _shown(app.error)
    assert relatorios_ui.AI_GENERATE_FAILURE in visible
    assert "detalhe interno que nao pode vazar" not in visible
    assert _values(app)[f"inst_text_{report['id']}_resumo_executivo"] == (
        "GERADO resumo_executivo."
    )
    assert repository.get(report["id"])["conteudo_estruturado"] == before
    assert relatorios_ui.AI_GENERATE_FAILURE in logged


def test_finalized_report_blocks_ai_actions(tmp_path, monkeypatch):
    _patch(monkeypatch, _fake_ai)
    database, store, repository, report = _create(tmp_path)
    InstitutionalReportContentService(store).generate_all(report["id"], _admin())
    repository.finalize(report["id"], "admin@test")
    app = _open(monkeypatch, database, report)
    labels = [button.label for button in app.button]
    assert relatorios_ui.AI_GENERATE_LABEL not in labels
    assert relatorios_ui.AI_REGENERATE_LABEL not in labels
    assert "Criar nova versão para editar" in labels
    assert not app.text_area
    with pytest.raises(ValueError, match="somente leitura"):
        InstitutionalReportContentService(store).generate_all(report["id"], _admin())
    with pytest.raises(ValueError, match="somente leitura"):
        InstitutionalReportContentService(store).regenerate(
            report["id"], "permanencia", _admin()
        )


def test_sent_report_blocks_ai_actions(tmp_path, monkeypatch):
    _patch(monkeypatch, _fake_ai)
    database, store, repository, report = _create(tmp_path)
    InstitutionalReportContentService(store).generate_all(report["id"], _admin())
    repository.finalize(report["id"], "admin@test")
    repository.mark_distributed(report["id"], "admin@test")
    app = _open(monkeypatch, database, report)
    assert repository.get(report["id"])["status"] == "ENVIADO"
    labels = [button.label for button in app.button]
    assert relatorios_ui.AI_GENERATE_LABEL not in labels
    assert relatorios_ui.AI_REGENERATE_LABEL not in labels
    assert "Criar nova versão para editar" in labels
    assert not app.text_area
    assert "Relatório enviado" in _shown(app.caption)
    with pytest.raises(ValueError, match="somente leitura"):
        InstitutionalReportContentService(store).generate_all(report["id"], _admin())
