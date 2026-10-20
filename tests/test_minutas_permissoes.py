"""Usuário comum pesquisa e gera; só o administrador mantém o acervo."""

import pytest
from tests.test_minutas_orientacao import gerar_instrucao

from services.minutas_acervo import MinutasAcervoService
from services.minutas_docx import ELVIRA_TEMPLATE, MinutasDocxService
from services.minutas_extraction import MinutasExtractionService
from services.minutas_generation import MinutasGenerationService
from services.minutas_library import MinutasAccessDenied, MinutasLibraryService
from services.minutas_modelos import MinutasModeloVisualService
from services.minutas_procuradores import pode_administrar
from services.minutas_search import MinutasSearchService
from services.minutas_specialties import PREFEITURAS, RPPS
from tests.access_testing import TEST_IDENTITY
from tests.test_minutas_library import _pdf
from tests.test_minutas_specialties import (
    GestorAcervo,
    Storage,
    environment,
    library,
)


class Comum:
    ativo = True
    pode_admin = False
    administrator = False
    perfil = "USUARIO"


class Inativo:
    ativo = False
    pode_admin = True
    perfil = "ADMINISTRADOR"


def _opcoes(app):
    return list(app.radio(key="minutas_active_view").options)


def _ligar_servico(monkeypatch, store, storage):
    import streamlit as st
    from services import minutas_ia_ui

    monkeypatch.setattr(
        minutas_ia_ui,
        "_service",
        lambda *args, administrative=False: library(
            store,
            storage,
            None if administrative else st.session_state.get("minutas_specialty"),
            principal=st.session_state.get("_audit_actor"),
        ),
    )


def test_usuario_comum_ve_pesquisa_e_geracao_sem_acervo_nem_administracao(environment, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from tests.test_minutas_specialties import _choose_specialty

    store, storage = environment
    _ligar_servico(monkeypatch, store, storage)

    def page():
        import streamlit as st
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY

        class Usuario:
            ativo = True
            pode_admin = False

        try:
            st.session_state["minutas_procurador"]
        except KeyError:
            st.session_state["minutas_procurador"] = "BLTC"
        try:
            st.session_state["_audit_actor"]
        except KeyError:
            st.session_state["_audit_actor"] = Usuario()
        render(None, TEST_IDENTITY)

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception
    assert any(item.value == "MINUTAS IA" for item in app.title) or any(
        "Bradson" in item.value for item in app.title
    )
    assert not any(item.key == "minutas_initial_administration_open" for item in app.button)
    _choose_specialty(app)
    assert _opcoes(app) == ["Pesquisa Jurídica", "Gerar Minuta de Parecer"]
    assert app.radio(key="minutas_active_view").value == "Pesquisa Jurídica"
    assert not any(item.key == "minutas_acervo_mode" for item in app.radio)
    app.radio(key="minutas_active_view").set_value("Gerar Minuta de Parecer").run()
    assert any(item.key == "minutas_instrucao_incluir" for item in app.button)


def test_administrador_mantem_acervo_pesquisa_geracao_e_administracao(environment, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from tests.test_minutas_specialties import _choose_specialty

    store, storage = environment
    _ligar_servico(monkeypatch, store, storage)

    def page():
        import streamlit as st
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY

        class Gestor:
            ativo = True
            pode_admin = True

        try:
            st.session_state["minutas_procurador"]
        except KeyError:
            st.session_state["minutas_procurador"] = "ESPO"
        try:
            st.session_state["_audit_actor"]
        except KeyError:
            st.session_state["_audit_actor"] = Gestor()
        render(None, TEST_IDENTITY)

    app = AppTest.from_function(page, default_timeout=30).run()
    assert any(item.key == "minutas_initial_administration_open" for item in app.button)
    _choose_specialty(app)
    assert _opcoes(app) == ["Acervo", "Pesquisa Jurídica", "Gerar Minuta de Parecer"]
    assert app.radio(key="minutas_active_view").value == "Acervo"
    assert "Consultar" in app.radio(key="minutas_acervo_mode").options
    app.radio(key="minutas_active_view").set_value("Pesquisa Jurídica").run()
    assert any(item.label == "Pesquisar pareceres" for item in app.text_input)
    app.radio(key="minutas_active_view").set_value("Gerar Minuta de Parecer").run()
    assert any(item.key == "minutas_instrucao_incluir" for item in app.button)


def test_estado_antigo_de_acervo_e_administracao_nao_abre_para_quem_perdeu_privilegio(
    environment, monkeypatch,
):
    from streamlit.testing.v1 import AppTest

    store, storage = environment
    _ligar_servico(monkeypatch, store, storage)

    def page():
        import streamlit as st
        from services.minutas_ia_ui import render
        from services.minutas_specialties import RPPS
        from tests.access_testing import TEST_IDENTITY

        class Usuario:
            ativo = True
            pode_admin = False

        st.session_state["minutas_procurador"] = "MTTF"
        st.session_state["minutas_nav_category"] = "PCA"
        st.session_state["minutas_pca_choice"] = RPPS
        st.session_state["minutas_specialty"] = RPPS
        st.session_state["minutas_active_view"] = "Acervo"
        st.session_state["minutas_initial_administration"] = True
        st.session_state["_audit_actor"] = Usuario()
        render(None, TEST_IDENTITY)

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception
    assert "Acervo" not in _opcoes(app)
    assert app.radio(key="minutas_active_view").value == "Pesquisa Jurídica"
    assert not any(item.label == "Administração" for item in app.selectbox)
    assert not any(item.key == "minutas_acervo_mode" for item in app.radio)


def test_revogacao_durante_a_sessao_fecha_o_acervo(environment, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from tests.test_minutas_specialties import _choose_specialty

    store, storage = environment
    _ligar_servico(monkeypatch, store, storage)

    def page():
        import streamlit as st
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY

        class Gestor:
            ativo = True
            pode_admin = True

        try:
            st.session_state["minutas_procurador"]
        except KeyError:
            st.session_state["minutas_procurador"] = "ESPO"
        try:
            st.session_state["_audit_actor"]
        except KeyError:
            st.session_state["_audit_actor"] = Gestor()
        render(None, TEST_IDENTITY)

    app = AppTest.from_function(page, default_timeout=30).run()
    _choose_specialty(app)
    assert "Acervo" in _opcoes(app)
    app.session_state["_audit_actor"] = Comum()
    app.run()
    assert _opcoes(app) == ["Pesquisa Jurídica", "Gerar Minuta de Parecer"]
    assert app.radio(key="minutas_active_view").value == "Pesquisa Jurídica"


def test_principal_ausente_ou_inativo_nao_administra(environment, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from tests.test_minutas_specialties import _choose_specialty

    store, storage = environment
    _ligar_servico(monkeypatch, store, storage)

    def sem_ator():
        import streamlit as st
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY

        try:
            st.session_state["minutas_procurador"]
        except KeyError:
            st.session_state["minutas_procurador"] = "SBBQ"
        render(None, TEST_IDENTITY)

    def com_inativo():
        import streamlit as st
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY

        class Bloqueado:
            ativo = False
            pode_admin = True

        try:
            st.session_state["minutas_procurador"]
        except KeyError:
            st.session_state["minutas_procurador"] = "IBMF"
        try:
            st.session_state["_audit_actor"]
        except KeyError:
            st.session_state["_audit_actor"] = Bloqueado()
        render(None, TEST_IDENTITY)

    ausente = AppTest.from_function(sem_ator, default_timeout=30).run()
    _choose_specialty(ausente)
    assert "Acervo" not in _opcoes(ausente)
    assert not pode_administrar(None)
    inativo = AppTest.from_function(com_inativo, default_timeout=30).run()
    _choose_specialty(inativo)
    assert "Acervo" not in _opcoes(inativo)
    assert not any(item.key == "minutas_initial_administration_open" for item in inativo.button)


def test_troca_de_usuario_e_de_procurador_nao_reabre_acervo_alheio(environment, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from tests.test_minutas_specialties import _choose_specialty

    store, storage = environment
    _ligar_servico(monkeypatch, store, storage)

    def page():
        import streamlit as st
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY

        class Gestor:
            ativo = True
            pode_admin = True

        try:
            st.session_state["minutas_procurador"]
        except KeyError:
            st.session_state["minutas_procurador"] = "ESPO"
        try:
            st.session_state["_audit_actor"]
        except KeyError:
            st.session_state["_audit_actor"] = Gestor()
        render(None, TEST_IDENTITY)

    app = AppTest.from_function(page, default_timeout=30).run()
    _choose_specialty(app)
    app.session_state["_audit_actor"] = Comum()
    app.session_state["minutas_procurador"] = "LAF"
    app.session_state["minutas_active_view"] = "Acervo"
    app.run()
    assert "Acervo" not in _opcoes(app)
    assert any("Luciano" in item.value for item in app.title)


def test_usuario_comum_nao_executa_manutencao_por_chamada_direta(environment):
    store, storage = environment
    comum = library(store, storage, RPPS, principal=Comum())
    acervo = MinutasAcervoService(comum)
    valores = {
        "numero_processo": "01653/26",
        "exercicio": 2026,
        "classe": "PCA",
        "origem": "Instituto de Previdência",
        "fundamento": "Tentativa de usuário comum",
    }
    arquivo = ("parecer.pdf", _pdf(b"parecer"))
    operacoes = (
        lambda: acervo.add_file(RPPS, valores, arquivo, role="parecer"),
        lambda: acervo.add_file(RPPS, valores, ("auditoria.pdf", _pdf(b"relatorio")), role="auditoria"),
        lambda: acervo.preview_upload("parecer.pdf", _pdf(b"parecer"), "parecer", RPPS),
        lambda: acervo.classify("id", RPPS, "PCA", "Instituto", "Fundamento"),
        lambda: acervo.link("id", RPPS, "Vínculo"),
        lambda: acervo.restore("evento"),
        lambda: acervo.update_state(RPPS, {"estado": "Validada", "revisao_humana": True, "resultados_testes": "x", "casos_testados": 1}),
        lambda: acervo.dry_run(),
        lambda: acervo.apply({}, {"identificador": "x", "restauracao_verificada": True}),
        lambda: comum.create(valores, arquivo, arquivo),
        lambda: comum.replace("id", "parecer", arquivo),
        lambda: comum.delete("id", confirmed=True),
        lambda: comum.delete_document("id", "doc", confirmed=True),
        lambda: comum.add_document("id", {"tipo": "Cota Ministerial", "descricao": "", "posicao": 1}, arquivo),
        lambda: comum.update_documents("id", []),
        lambda: comum.promote_document_to_primary(
            "id", "doc",
            {"tipo": "Relatório de Análise de Defesa", "descricao": "", "posicao": 1},
            True,
        ),
        lambda: MinutasModeloVisualService(comum).save(RPPS, ("modelo.docx", _pdf())),
        lambda: MinutasExtractionService(comum).process_reference("id"),
        lambda: MinutasExtractionService(comum).reprocess_document("id", "caminho"),
    )
    for operacao in operacoes:
        with pytest.raises(MinutasAccessDenied, match="administrativa"):
            operacao()
    assert comum.list() == []


def test_principal_ausente_inativo_ou_comum_falha_fechado(environment):
    store, storage = environment
    for ator in (None, Inativo(), Comum()):
        servico = library(store, storage, principal=ator)
        with pytest.raises(MinutasAccessDenied, match="administrativa"):
            MinutasAcervoService(servico).add_file(
                RPPS,
                {
                    "numero_processo": "01234/26",
                    "exercicio": 2026,
                    "classe": "PCA",
                    "origem": "Instituto",
                    "fundamento": "Sem privilégio",
                },
                ("parecer.pdf", _pdf(b"x")),
                role="parecer",
            )


def test_administrador_cadastra_classifica_e_permanece_no_gabinete(environment):
    store, storage = environment
    gestor = library(store, storage, PREFEITURAS, principal=GestorAcervo())
    gestor.procurador = __import__(
        "services.minutas_procuradores", fromlist=["exigir_procurador"]
    ).exigir_procurador("BLTC")
    gestor.repository.procuradora_chave = gestor.procurador.chave
    acervo = MinutasAcervoService(gestor)
    criado = acervo.add_file(
        PREFEITURAS,
        {
            "numero_processo": "02720/25",
            "exercicio": 2025,
            "classe": "PCA",
            "origem": "Prefeitura Municipal de Exemplo",
            "fundamento": "Cadastro do administrador.",
        },
        ("parecer.pdf", _pdf(b"parecer-bltc")),
        role="parecer",
    )
    assert criado["estado"] == "CADASTRADO"
    acervo.classify(
        criado["referencia_id"], PREFEITURAS, "PCA",
        "Prefeitura Municipal de Exemplo", "Classificação do administrador.",
    )
    assert [item["id"] for item in acervo.repository.list_records(PREFEITURAS)] == [
        criado["referencia_id"]
    ]
    outro = library(store, storage, PREFEITURAS, principal=GestorAcervo())
    assert MinutasAcervoService(outro).repository.list_records(PREFEITURAS) == []
    with store.connection() as conexao:
        linha = conexao.execute(
            "SELECT procuradora_chave FROM minutas_referencias WHERE id=?",
            (criado["referencia_id"],),
        ).fetchone()
    assert linha["procuradora_chave"] == "bltc"


def test_pesquisa_preserva_filtro_juridico_do_procurador():
    from services.minutas_procuradores import exigir_procurador

    capturado = {}

    class Repositorio:
        specialty_id = RPPS

        def search_opinions(self, consulta, chave, _versao, **filtros):
            capturado["consulta"] = consulta
            capturado["chave"] = chave
            capturado.update(filtros)
            return [
                {
                    "referencia_id": "espo",
                    "documento_id": "d1",
                    "procuradora": "Elvira Samara",
                    "procuradora_chave": "elvira samara",
                    "numero_processo": "01653/26",
                    "exercicio": 2026,
                    "subtipo": "Instituto/Autarquia",
                    "arquivo_nome": "parecer.pdf",
                    "arquivo_path": "referencias/espo/parecer/a.pdf",
                    "arquivo_sha256": "ab" * 32,
                    "extrator_versao": "teste",
                    "atualizado_em": "2026-10-16",
                    "trecho": "Precedente da Dra. Elvira.",
                },
                {
                    "referencia_id": "mttf",
                    "documento_id": "d2",
                    "procuradora": "Marcílio Toscano Franca Filho",
                    "procuradora_chave": "mttf",
                    "numero_processo": "02119/26",
                    "exercicio": 2026,
                    "subtipo": "Instituto/Autarquia",
                    "arquivo_nome": "parecer.pdf",
                    "arquivo_path": "referencias/mttf/parecer/a.pdf",
                    "arquivo_sha256": "cd" * 32,
                    "extrator_versao": "teste",
                    "atualizado_em": "2026-10-16",
                    "trecho": "Precedente deste gabinete.",
                },
            ]

    pessoa = exigir_procurador("MTTF")
    servico = MinutasLibraryService(
        Repositorio(), Storage(), TEST_IDENTITY, {}, procurador=pessoa, principal=Comum(),
    )
    resultado = MinutasSearchService(servico).search(
        "precedente", year=2026, subtype="Instituto/Autarquia",
    )
    assert capturado["chave"] == "mttf"
    assert capturado["year"] == 2026
    assert capturado["subtype"] == "Instituto/Autarquia"
    assert [item["referencia_id"] for item in resultado["resultados"]] == ["mttf"]


def test_usuario_comum_analisa_pdf_transitorio_e_baixa_docx():
    from tests.test_minutas_instrucao import _doc
    from tests.test_minutas_recursos import MODALIDADES, _analysis, _draft, _library

    biblioteca = _library()
    biblioteca.principal = Comum()
    documento = _doc("decisao.pdf", "Decisão recorrida com fundamento documentado.", "decisao")
    assert documento["conteudo"]
    servico = MinutasGenerationService(
        biblioteca,
        analyze=lambda _contexto: {"analise": _analysis(documento, "APELACAO")},
        generate=lambda _contexto: {"estrutura": _draft("APELACAO")},
    )
    analisado = servico.analyze_instruction(
        [documento], processo="02108/26", origem="Município de Exemplo",
        modalidade_recurso="APELACAO", materia_originaria="Licitação",
        data_decisao="2025-03-10",
    )
    gerado = gerar_instrucao(servico,
        [documento], analisado, processo="02108/26", origem="Município de Exemplo",
        modalidade_recurso="APELACAO", materia_originaria="Licitação",
        data_decisao="2025-03-10",
    )
    assert gerado["estrutura"]["pedidos"]
    if not ELVIRA_TEMPLATE.is_file():
        pytest.skip("modelo Word indisponível")
    word = MinutasDocxService(biblioteca, template_path=ELVIRA_TEMPLATE).export_instruction(
        analisado, gerado, processo="02108/26", origem="Município de Exemplo",
        modalidade_recurso="APELACAO", materia_originaria="Licitação",
        data_decisao="2025-03-10",
    )
    assert word.content[:2] == b"PK"
    assert MODALIDADES["APELACAO"]
