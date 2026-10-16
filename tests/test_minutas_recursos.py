"""Fluxo isolado de Apelação e Recurso Ordinário."""

from io import BytesIO
from pathlib import Path
import re

import pytest

from services.minutas_acervo import MinutasAcervoService
from services.minutas_docx import ELVIRA_TEMPLATE, MinutasDocxService
from services.minutas_generation import MinutasGenerationError, MinutasGenerationService
from services.minutas_recursos import MODALIDADES, MOTOR, SPECIALTY_ID, is_recurso_profile
from services.minutas_specialties import CATALOG, RECURSOS, instruction_generation_allowed, scope
from tests.test_minutas_instrucao import _doc, _profiled_library
from tests.test_minutas_specialties import Storage, _pdf, build_store, library


def _analysis(document, modalidade):
    evidence = [{"fonte_id": document["fonte_id"], "pagina": 1, "trecho": "fundamento documentado"}]
    requirement = {"conclusao": "ATENDIDO", "fundamentacao": "Requisito conferido nas fontes.", "fontes": evidence}
    return {
        "resumo_processual": "Recurso submetido ao exame de admissibilidade e mérito.",
        "modalidade": modalidade,
        "decisao_recorrida": {
            "identificacao": "Acórdão recorrido", "data": "2025-03-10", "orgao": "Câmara" if modalidade == "APELACAO" else "Tribunal Pleno",
            "fundamentos": "A decisão adotou o fundamento documentado.", "fontes": evidence,
        },
        "razoes_recursais": [{
            "id": "R-1", "argumento": "A recorrente pede a reforma da decisão.",
            "fundamento_decisao": "Fundamento documentado da decisão.", "provas": evidence,
            "analise_auditoria": "A Auditoria manteve o entendimento técnico.",
            "confronto": "O argumento foi confrontado com a decisão, a prova e a Auditoria.",
            "conclusao": "Argumento insuficiente.", "fontes": evidence,
        }],
        "manifestacao_auditoria": ["A Auditoria opinou pela manutenção."],
        "admissibilidade": {
            "cabimento": requirement, "tempestividade": requirement,
            "legitimidade": requirement, "interesse": requirement,
            "demais_requisitos": requirement,
            "regime_temporal": "Decisão posterior à vigência indicada; conferir eventuais alterações.",
            "conclusao": "CONHECIMENTO",
        },
        "questoes_processuais": [], "nulidades": [],
        "merito": "As razões não afastam os fundamentos da decisão.",
        "conclusao": "DESPROVIMENTO", "lacunas_documentais": [],
        "fontes_normativas": ["LC-PB 192/2024", "RN-TC 07/2024"],
    }


def _draft(modalidade):
    return {
        "identificacao_processual": "Minuta",
        "ementa": f"{MODALIDADES[modalidade]}. Admissibilidade. Mérito.",
        "relatorio": "Relatório da decisão, das razões e da manifestação da Auditoria.",
        "relatorio_paragrafos": ["Relatório da decisão, das razões e da manifestação da Auditoria."],
        "admissibilidade": "Presentes os requisitos de admissibilidade no regime temporal aplicável.",
        "fundamentacao_geral": "As razões foram confrontadas individualmente com a decisão e as provas.",
        "fundamentacao_geral_paragrafos": ["As razões foram confrontadas individualmente com a decisão e as provas."],
        "topicos_irregularidades": [{
            "titulo": "Razão recursal", "texto": "O argumento não afasta o fundamento recorrido.",
            "paragrafos": ["O argumento não afasta o fundamento recorrido."],
        }],
        "conclusao_pedidos": "Ante o exposto, opina-se pelo conhecimento e desprovimento.",
        "pedidos": ["Pelo conhecimento e desprovimento do recurso."],
        "encerramento": "É o Parecer.",
    }


def _library():
    profile = dict(scope(RECURSOS), id=RECURSOS, motor=MOTOR, estado="Em testes", config_versao="1")
    result = _profiled_library(RECURSOS, motor=MOTOR, estado="Em testes")
    result.repository.profile = lambda: profile
    return result


def _prepare_store(path):
    store = build_store(path)
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
    return store


def test_recursos_exige_motor_proprio_e_identidade_da_elvira():
    enabled = dict(scope(RECURSOS), motor=MOTOR, estado="Em testes", config_versao="1")
    assert instruction_generation_allowed(enabled)
    assert is_recurso_profile(enabled)
    assert not instruction_generation_allowed(dict(enabled, motor="pca-rpps-v1"))
    assert not instruction_generation_allowed(dict(enabled, procuradora_chave="outra"))
    assert not instruction_generation_allowed(dict(enabled, estado="A iniciar"))
    from services.ai_service import _minuta_contract
    analysis_prompt, analysis_schema = _minuta_contract({"motor": MOTOR}, "analise")
    generation_prompt, generation_schema = _minuta_contract({"motor": MOTOR}, "geracao")
    assert "regime temporal" in analysis_prompt.casefold()
    assert "modalidade" in analysis_schema["properties"]
    assert "admissibilidade" in generation_schema["properties"]
    assert "copiar automaticamente" in generation_prompt.casefold()


@pytest.mark.parametrize("modalidade", tuple(MODALIDADES))
def test_fluxo_completo_das_duas_modalidades_usa_servicos_reais(modalidade):
    documents = [
        _doc("decisao.pdf", "Decisão recorrida e seus fundamentos.", "decisao"),
        _doc("razoes.pdf", "Razões recursais e pedido de reforma.", "manifestacao"),
        _doc("auditoria.pdf", "Manifestação técnica da Auditoria.", "analise_defesa"),
    ]
    captured = {}

    def search(_query, **kwargs):
        captured["search"] = kwargs
        return {"resultados": [{
            "papel": "parecer", "referencia_id": "recurso-anterior",
            "processo_chave": "0999900", "numero_processo": "09999/00",
            "trecho": "entendimento jurídico definitivo aplicável ao recurso",
        }]}

    def generate(context):
        captured["generation_context"] = context
        return {"estrutura": _draft(modalidade)}

    service = MinutasGenerationService(
        _library(),
        analyze=lambda _context: {"analise": _analysis(documents[0], modalidade), "modelo": "mock-recursos"},
        generate=generate,
        search=search,
    )
    analyzed = service.analyze_instruction(
        documents, processo="02108/26", origem="Município de Exemplo",
        modalidade_recurso=modalidade, materia_originaria="Licitação",
        data_decisao="2025-03-10",
    )
    generated = service.generate_instruction(
        documents, analyzed, processo="02108/26", origem="Município de Exemplo",
        modalidade_recurso=modalidade, materia_originaria="Licitação",
        data_decisao="2025-03-10",
    )
    assert analyzed["motor"] == MOTOR
    assert analyzed["referencias"]["referencias"][0]["referencia_id"] == "recurso-anterior"
    assert captured["search"]["exclude_process_key"] == "0210826"
    assert captured["generation_context"]["motor"] == MOTOR
    assert captured["generation_context"]["processo_alvo"]["modalidade_recurso"] == modalidade
    assert generated["estrutura"]["admissibilidade"]
    assert generated["estrutura"]["pedidos"] == ["Pelo conhecimento e desprovimento do recurso."]
    if not ELVIRA_TEMPLATE.is_file():
        pytest.skip("modelo Word indisponível")
    word = MinutasDocxService(service.library, template_path=ELVIRA_TEMPLATE).export_instruction(
        analyzed, generated, processo="02108/26", origem="Município de Exemplo",
        modalidade_recurso=modalidade, materia_originaria="Licitação",
        data_decisao="2025-03-10",
    )
    from docx import Document
    body = "\n".join(paragraph.text for paragraph in Document(BytesIO(word.content)).paragraphs)
    assert f"Natureza: {MODALIDADES[modalidade]}" in body
    assert "Admissibilidade" in body and "Pelo conhecimento e desprovimento" in body


def test_alterar_modalidade_ou_documento_exige_nova_analise():
    document = _doc("decisao.pdf", "Decisão recorrida.", "decisao")
    service = MinutasGenerationService(
        _library(), analyze=lambda _context: {"analise": _analysis(document, "APELACAO")},
        generate=lambda _context: {"estrutura": _draft("APELACAO")},
        search=lambda *_args, **_kwargs: {"resultados": []},
    )
    analyzed = service.analyze_instruction([document], processo="02108/26", modalidade_recurso="APELACAO")
    with pytest.raises(MinutasGenerationError, match="modalidade"):
        service.generate_instruction([document], analyzed, processo="02108/26", modalidade_recurso="ORDINARIO")
    changed = _doc("decisao.pdf", "Decisão recorrida alterada.", "decisao")
    with pytest.raises(MinutasGenerationError, match="alterada"):
        service.generate_instruction([changed], analyzed, processo="02108/26", modalidade_recurso="APELACAO")


def test_upload_persistente_classifica_modalidade_materia_e_teses_sem_schema_novo(tmp_path):
    store, storage = _prepare_store(tmp_path / "recursos.db"), Storage()
    scoped = library(store, storage, RECURSOS)
    values = {
        "numero_processo": "02108/26", "classe": "RECURSO", "origem": "Município de Exemplo",
        "fundamento": "Parecer definitivo conferido", "modalidade_recurso": "Recurso de Apelação",
        "materia_originaria": "Licitação", "teses": ["Cabimento", "Efeito devolutivo"],
        "descricao": "Parecer definitivo revisado",
    }
    content = _pdf(b"parecer definitivo")
    result = MinutasAcervoService(scoped).add_file(
        RECURSOS, values, ("parecer-recurso.pdf", content), role="parecer",
    )
    assert result["estado"] == "CADASTRADO"
    second = MinutasAcervoService(scoped).add_file(
        RECURSOS,
        {**values, "descricao": "Segundo parecer definitivo"},
        ("parecer-recurso-2.pdf", _pdf(b"segundo parecer definitivo")),
        role="parecer",
    )
    assert second["referencia_id"] == result["referencia_id"]
    reloaded = library(store, storage, RECURSOS)
    record = reloaded.get(result["referencia_id"])
    documents = reloaded.repository.list_documents(result["referencia_id"])
    with store.connection(read_only=True) as connection:
        classification = dict(connection.execute(
            "SELECT * FROM minutas_classificacoes WHERE referencia_id=?",
            (result["referencia_id"],),
        ).fetchone())
    assert record["numero_processo"] == "TC 02108/26" and record["exercicio"] is None
    assert len(documents) == 2 and all(item["papel"] == "parecer" for item in documents)
    assert {item["descricao"] for item in documents} == {
        "Parecer definitivo revisado", "Segundo parecer definitivo",
    }
    assert classification["modalidade"] == "Recurso de Apelação"
    assert classification["perfil"] == "Licitação"
    assert "Cabimento" in classification["temas_json"]
    assert content in {storage.download(item["path"]) for item in documents}


def test_card_so_indica_geracao_apos_habilitacao_e_painel_oferece_duas_modalidades(tmp_path, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from services import minutas_ia_ui
    from tests.access_testing import TEST_IDENTITY
    from tests.test_minutas_specialties import _choose_specialty

    store, storage = _prepare_store(tmp_path / "recursos-ui.db"), Storage()
    monkeypatch.setattr(
        minutas_ia_ui,
        "_service",
        lambda *args, administrative=False: library(
            store, storage, None if administrative else st.session_state.get("minutas_specialty"),
        ),
    )

    def page():
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY
        render(None, TEST_IDENTITY)

    inactive = AppTest.from_function(page, default_timeout=30).run()
    inactive.button(key="minutas_category_RECURSOS").click().run()
    assert any("ainda não possui motor" in item.value for item in inactive.info)

    with store.connection() as connection:
        connection.execute(
            "UPDATE minutas_especialidades SET motor=?,estado='Em testes',config_versao='1' WHERE id=?",
            (MOTOR, RECURSOS),
        )
    active = AppTest.from_function(page, default_timeout=30).run()
    _choose_specialty(active, RECURSOS)
    active.radio(key="minutas_active_view").set_value("Gerar Minuta de Parecer").run()
    modality = active.radio(key="minutas_recurso_modalidade")
    assert set(modality.options) == set(MODALIDADES.values())
    assert any(item.key == "minutas_instrucao_incluir" for item in active.button)


def test_sql_de_habilitacao_e_separado_idempotente_e_sem_alteracao_de_esquema():
    sql = (Path(__file__).resolve().parents[1] / "database/minutas_recursos_habilitacao.sql").read_text(encoding="utf-8")
    normalized = re.sub(r"\s+", " ", sql).casefold()
    assert "create table" not in normalized and "alter table" not in normalized and "drop " not in normalized
    assert "where id='elvira.recursos'" in normalized
    assert "motor not in ('', 'recursos-v1')" in normalized
    assert "motor<> 'recursos-v1'" in normalized
