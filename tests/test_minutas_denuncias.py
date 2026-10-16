"""Contrato isolado do primeiro motor experimental de denúncias."""

from io import BytesIO
from pathlib import Path
import re

import pytest

from services.minutas_denuncias import MOTOR, SPECIALTY_ID, is_denuncia_profile
from services.minutas_docx import ELVIRA_TEMPLATE, MinutasDocxService
from services.minutas_generation import MinutasGenerationError, MinutasGenerationService
from services.minutas_search import MinutasSearchService
from services.minutas_specialties import DENUNCIAS, instruction_generation_allowed, scope
from tests.test_minutas_instrucao import _doc, _profiled_library


def _analysis(document):
    return {
        "resumo_processual": "Denúncia submetida a exame de admissibilidade e mérito.",
        "admissibilidade": "CONHECIMENTO",
        "objeto_denunciado": "Possível irregularidade em contratação pública.",
        "questoes": [{
            "id": "D-1", "objeto": "Contratação denunciada", "alegacoes": ["A denúncia inicial aponta falha."],
            "fatos_comprovados": [{"fonte_id": document["fonte_id"], "pagina": 1, "trecho": "relatório técnico"}],
            "lacunas_documentais": [], "conclusao_auditoria": ["Auditoria concluiu pelo exame do mérito."],
        }],
        "manifestacoes_interessados": ["Defesa apresentada e examinada."],
        "saneamento": "NAO_ALEGADO", "merito": "PROCEDENTE",
        "providencias_cabiveis": ["Providência específica condicionada aos elementos dos autos."],
    }


def _draft(*requests):
    return {
        "identificacao_processual": "Minuta", "ementa": "Denúncia. Exame de admissibilidade e mérito.",
        "relatorio": "A instrução contém denúncia, auditoria, defesa e decisão.",
        "relatorio_paragrafos": ["A instrução contém denúncia, auditoria, defesa e decisão."],
        "fundamentacao_geral": "A conclusão observa exclusivamente as fontes documentais.",
        "fundamentacao_geral_paragrafos": ["A conclusão observa exclusivamente as fontes documentais."],
        "topicos_irregularidades": [{"titulo": "Objeto denunciado", "texto": "O fato foi confrontado com a defesa e a auditoria.", "paragrafos": ["O fato foi confrontado com a defesa e a auditoria."]}],
        "responsabilizacao_administrativa": {"titulo": "Responsabilização", "texto": ""},
        "conclusao_pedidos": "Ante o exposto, o Ministério Público de Contas opina:",
        "pedidos": list(requests), "encerramento": "É o Parecer.",
    }


def _library(**changes):
    profile = dict(scope(DENUNCIAS), id=DENUNCIAS, motor=MOTOR, estado="Em testes", config_versao="1")
    profile.update(changes)
    library = _profiled_library(DENUNCIAS, motor=MOTOR, estado="Em testes")
    library.repository.profile = lambda: profile
    return library


def test_denuncias_exige_motor_proprio_configuracao_e_estado():
    enabled = dict(scope(DENUNCIAS), id=DENUNCIAS, motor=MOTOR, estado="Em testes", config_versao="1")
    assert instruction_generation_allowed(enabled)
    assert is_denuncia_profile(enabled)
    assert not instruction_generation_allowed(dict(enabled, motor="pca-rpps-v1"))
    assert not instruction_generation_allowed(dict(enabled, config_versao="2"))
    assert not instruction_generation_allowed(dict(enabled, procuradora_chave="outra"))
    assert not instruction_generation_allowed(dict(enabled, estado="A iniciar"))


@pytest.mark.parametrize("request_text", (
    "Pelo conhecimento da denúncia e, no mérito, pela procedência.",
    "Pelo não conhecimento da denúncia e arquivamento, se confirmada a hipótese processual.",
    "Pela realização de diligência, sem conclusão de mérito.",
))
def test_denuncias_pdf_fluxo_rag_isolado_sem_exercicio_e_docx(request_text):
    documents = [
        _doc("denuncia.pdf", "Denúncia inicial sobre contratação.", "denuncia_inicial"),
        _doc("auditoria.pdf", "Relatório da Auditoria sobre contratação.", "relatorio_inicial"),
        _doc("defesa.pdf", "Defesa do interessado.", "manifestacao"),
        _doc("decisao.pdf", "Decisão processual anterior.", "decisao"),
    ]
    library, captured = _library(), {}

    def search(_query, **kwargs):
        captured["search"] = kwargs
        return {"resultados": [
            {"papel": "parecer", "referencia_id": "denuncia-1", "processo_chave": "0999900", "numero_processo": "09999/00", "trecho": "critério jurídico de denúncia"},
            {"papel": "parecer", "referencia_id": "alvo", "processo_chave": "0210826", "trecho": "próprio processo"},
        ]}

    def generate(context):
        captured["context"] = context
        return {"estrutura": _draft(request_text)}

    service = MinutasGenerationService(
        library, analyze=lambda _context: {"analise": _analysis(documents[0])}, generate=generate, search=search,
    )
    analyzed = service.analyze_instruction(
        documents, processo="02108/26", origem="Município de Exemplo", periodo_fatos="2023 a 2025",
    )
    generated = service.generate_instruction(
        documents, analyzed, processo="02108/26", origem="Município de Exemplo", periodo_fatos="2023 a 2025",
    )
    assert analyzed["referencias"]["estado"] == "RECUPERADO"
    assert [item["referencia_id"] for item in analyzed["referencias"]["referencias"]] == ["denuncia-1"]
    assert captured["search"]["exclude_process_key"] == "0210826"
    assert captured["context"]["motor"] == MOTOR
    assert "exercicio" not in captured["context"]["processo_alvo"]
    assert generated["metadados_cabecalho"]["periodo_fatos"] == "2023 a 2025"
    assert generated["estrutura"]["pedidos"] == [request_text]
    MinutasSearchService(library).search("contratação")
    assert library.searches == [DENUNCIAS]
    if not ELVIRA_TEMPLATE.is_file():
        pytest.skip("modelo Word indisponível")
    word = MinutasDocxService(library, template_path=ELVIRA_TEMPLATE).export_instruction(
        analyzed, generated, processo="02108/26", origem="Município de Exemplo",
    )
    from docx import Document
    body = "\n".join(p.text for p in Document(BytesIO(word.content)).paragraphs)
    assert "Natureza: Denúncia" in body and "Período dos fatos: 2023 a 2025" in body


def test_denuncias_sem_precedente_e_alteracao_de_pdf_exigem_comportamento_seguro():
    document = _doc("denuncia.pdf", "Denúncia inicial sobre contratação.", "denuncia_inicial")
    library = _library()
    service = MinutasGenerationService(
        library, analyze=lambda _context: {"analise": _analysis(document)},
        generate=lambda _context: {"estrutura": _draft("Pela improcedência, se os fatos não forem comprovados.")},
        search=lambda *_args, **_kwargs: {"resultados": []},
    )
    analyzed = service.analyze_instruction([document], processo="02108/26", origem="Município")
    assert analyzed["referencias"]["estado"] == "SEM_RESULTADO"
    assert service.generate_instruction([document], analyzed, processo="02108/26", origem="Município")["estrutura"]
    changed = _doc("denuncia.pdf", "Denúncia alterada sobre contratação.", "denuncia_inicial")
    with pytest.raises(MinutasGenerationError, match="alterada"):
        service.generate_instruction([changed], analyzed, processo="02108/26", origem="Município")


def test_ui_denuncias_so_libera_geracao_com_perfil_correto(tmp_path, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from services import minutas_ia_ui
    from tests.test_minutas_specialties import CATALOG, Storage, _choose_specialty, build_store, library, seed

    store, storage = build_store(tmp_path / "minutas.db"), Storage()
    with store.connection() as connection:
        script = (Path(__file__).resolve().parents[1] / "database/minutas_especialidades.sql").read_text(encoding="utf-8")
        for statement in re.sub(r"--[^\n]*", "", script).replace("mpc_portarias.", "").replace("::text", "").split(";"):
            statement = statement.strip()
            if statement.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE OR REPLACE VIEW")):
                connection.execute(statement.replace("CREATE OR REPLACE VIEW", "CREATE VIEW"))
        for profile in CATALOG:
            connection.execute(
                "INSERT INTO minutas_especialidades(id,procuradora_chave,grupo,nome,perfil,estado,motor) VALUES(?,?,?,?,?,?,?)",
                tuple(profile[key] for key in ("id", "procuradora_chave", "grupo", "nome", "perfil", "estado", "motor")),
            )
    seed(store)
    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *args, administrative=False:
        library(store, storage, None if administrative else st.session_state.get("minutas_specialty")))

    def page():
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY
        render(None, TEST_IDENTITY)

    app = AppTest.from_function(page, default_timeout=30).run()
    _choose_specialty(app, DENUNCIAS)
    app.radio(key="minutas_active_view").set_value("Gerar Minuta de Parecer").run()
    assert any("ainda não habilitada" in item.value for item in app.info)
    with store.connection() as connection:
        connection.execute(
            "UPDATE minutas_especialidades SET motor=?, estado=?, config_versao='1' WHERE id=?",
            (MOTOR, "Em testes", DENUNCIAS),
        )
    app.run()
    assert any(item.key == "minutas_instrucao_incluir" for item in app.button)
