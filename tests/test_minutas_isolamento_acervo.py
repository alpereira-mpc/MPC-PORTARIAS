"""Isolamento do acervo pelo mesmo caminho da interface."""

import pytest

from services.minutas_acervo import MinutasAcervoService
from services.minutas_docx import MinutasDocxError, MinutasDocxService
from services.minutas_library import MinutasLibraryService, MinutasValidationError
from services.minutas_procuradores import (
    PROCURADORES,
    MinutasContextoError,
    exigir_procurador,
)
from services.minutas_search import MinutasSearchService
from services.minutas_specialties import APOSENTADORIAS, PREFEITURAS, RPPS
from tests.access_testing import TEST_IDENTITY
from tests.test_minutas_library import _pdf
from tests.test_minutas_specialties import Storage, environment, library, seed


def _biblioteca(store, storage, sigla, specialty):
    from database.minutas_referencias import MinutasReferenciasStore

    repositorio = object.__new__(MinutasReferenciasStore)
    repositorio.store = store
    repositorio.specialty_id = specialty
    repositorio.include_acervo = True
    class _Gestor:
        ativo = True
        pode_admin = True

    return MinutasLibraryService(
        repositorio, storage, TEST_IDENTITY, {}, procurador=sigla, principal=_Gestor(),
    )


def _acervo(store, storage, sigla, specialty):
    return MinutasAcervoService(_biblioteca(store, storage, sigla, specialty))


def _classificar(store, storage, registro, specialty, classe):
    _acervo(store, storage, "ESPO", specialty).classify(
        registro["id"], specialty, classe, "Órgão sintético",
        "Classificação sintética do piloto.",
    )


@pytest.fixture
def acervo_misto(environment):
    store, storage = environment
    prefeitura = seed(
        store, process="TC 01653/26", subtype="Prefeitura",
        entity="Prefeitura Municipal de Exemplo",
    )
    aposentadoria = seed(
        store, process="TC 02108/26", subtype="Instituto/Autarquia",
        entity="Instituto de Previdência de Exemplo", nature="Aposentadoria",
    )
    _classificar(store, storage, prefeitura, PREFEITURAS, "PCA")
    _classificar(store, storage, aposentadoria, APOSENTADORIAS, "ATOS_PESSOAL")
    return store, storage, prefeitura, aposentadoria


def test_ausencia_de_procurador_no_acervo_nao_abre_espo(environment):
    store, storage = environment

    class Biblioteca:
        class repository:
            store = None

    Biblioteca.repository.store = store
    with pytest.raises(MinutasContextoError, match="Selecione o Procurador"):
        MinutasAcervoService(Biblioteca())
    legado = library(store, storage)
    assert legado.procurador.sigla == "ESPO"
    assert MinutasAcervoService(legado).repository._escopo() == "elvira samara"


@pytest.mark.parametrize("pessoa", PROCURADORES, ids=lambda pessoa: pessoa.sigla)
def test_listagem_da_interface_respeita_o_procurador(acervo_misto, pessoa):
    store, storage, prefeitura, aposentadoria = acervo_misto
    prefeituras = _acervo(store, storage, pessoa.sigla, PREFEITURAS)
    aposentadorias = _acervo(store, storage, pessoa.sigla, APOSENTADORIAS)
    ids_prefeitura = [item["id"] for item in prefeituras.repository.list_records(PREFEITURAS)]
    ids_aposentadoria = [item["id"] for item in aposentadorias.repository.list_records(APOSENTADORIAS)]
    if pessoa.sigla == "ESPO":
        assert ids_prefeitura == [prefeitura["id"]]
        assert ids_aposentadoria == [aposentadoria["id"]]
        return
    assert ids_prefeitura == []
    assert ids_aposentadoria == []
    with pytest.raises(MinutasValidationError, match="não encontrada"):
        _biblioteca(store, storage, pessoa.sigla, PREFEITURAS).get(prefeitura["id"])
    with pytest.raises(MinutasValidationError, match="não encontrada"):
        _biblioteca(store, storage, pessoa.sigla, APOSENTADORIAS).download(
            aposentadoria["id"], "parecer",
        )
    with pytest.raises(ValueError, match="fora do escopo"):
        _acervo(store, storage, pessoa.sigla, PREFEITURAS).classify(
            prefeitura["id"], PREFEITURAS, "PCA", "Outra origem",
            "Tentativa de reclassificar o piloto.",
        )


def test_cadastro_novo_fica_no_gabinete_e_nao_altera_o_piloto(acervo_misto):
    store, storage, prefeitura, _aposentadoria = acervo_misto
    with store.connection() as conexao:
        conexao.execute("ALTER TABLE minutas_referencias ADD COLUMN procurador_sigla TEXT")
    bltc = _acervo(store, storage, "BLTC", PREFEITURAS)
    criado = bltc.add_file(
        PREFEITURAS,
        {
            "classe": "PCA",
            "numero_processo": "TC 01653/26",
            "exercicio": 2025,
            "fundamento": "Cadastro sintético exclusivo deste gabinete.",
            "origem": "Prefeitura Municipal de Exemplo",
        },
        ("parecer.pdf", _pdf()),
        role="parecer",
    )
    assert criado["estado"] == "CADASTRADO"
    assert criado["referencia_id"] != prefeitura["id"]
    assert [item["id"] for item in bltc.repository.list_records(PREFEITURAS)] == [
        criado["referencia_id"]
    ]
    espo = _acervo(store, storage, "ESPO", PREFEITURAS)
    assert [item["id"] for item in espo.repository.list_records(PREFEITURAS)] == [prefeitura["id"]]
    with store.connection() as conexao:
        linha = conexao.execute(
            "SELECT procuradora_chave, procurador_sigla FROM minutas_referencias WHERE id=?",
            (criado["referencia_id"],),
        ).fetchone()
        piloto = conexao.execute(
            "SELECT procuradora_chave, procurador_sigla FROM minutas_referencias WHERE id=?",
            (prefeitura["id"],),
        ).fetchone()
    assert linha["procuradora_chave"] == "bltc"
    assert linha["procurador_sigla"] == "BLTC"
    assert piloto["procuradora_chave"] == "elvira samara"
    assert piloto["procurador_sigla"] is None
    ibmf = _acervo(store, storage, "IBMF", PREFEITURAS)
    assert ibmf.repository.list_records(PREFEITURAS) == []
    assert [item["id"] for item in bltc.repository.list_records(PREFEITURAS)] == [
        criado["referencia_id"]
    ]


def test_pesquisa_e_docx_nao_herdam_outro_gabinete():
    class Repositorio:
        def search_opinions(self, *_args, **_kwargs):
            return [
                {
                    "referencia_id": "espo",
                    "documento_id": "doc-espo",
                    "procuradora": "Elvira Samara",
                    "procuradora_chave": "elvira samara",
                    "numero_processo": "01653/26",
                    "exercicio": 2025,
                    "subtipo": "Prefeitura",
                    "arquivo_nome": "parecer.pdf",
                    "arquivo_path": "referencias/espo/parecer/a.pdf",
                    "arquivo_sha256": "ab" * 32,
                    "extrator_versao": "teste",
                    "atualizado_em": "2026-10-16T00:00:00+00:00",
                    "trecho": "precedente do piloto",
                }
            ]

    class Biblioteca:
        procurador = exigir_procurador("BLTC")
        specialty_id = RPPS
        repository = Repositorio()

        @staticmethod
        def _authorize():
            return "admin@test.local"

    assert MinutasSearchService(Biblioteca()).search("prefeitura")["resultados"] == []

    class Alvo:
        def __init__(self, sigla):
            self.procurador = exigir_procurador(sigla)

    documento = MinutasDocxService.__new__(MinutasDocxService)
    documento.library = Alvo("IBMF")
    perfil, visual = documento._presentation(
        {
            "procuradora": "Isabella Barbosa Marinho Falcão",
            "procuradora_chave": "ibmf",
        }
    )
    assert visual["top_cm"] == 2.5
    assert "Procuradora-Geral" not in perfil["cargo"]
    with pytest.raises(MinutasDocxError, match="não pertence"):
        documento._presentation(
            {"procuradora": "Elvira Samara", "procuradora_chave": "elvira samara"}
        )


def test_troca_de_procurador_descarta_listagem_anterior():
    from streamlit.testing.v1 import AppTest

    def page():
        import streamlit as st
        from services.minutas_specialties_ui import clear_procurador_session

        st.session_state["minutas_procurador"] = "ESPO"
        st.session_state["minutas_query_ESPO_elvira.pca.prefeituras"] = "01653"
        st.session_state["minutas_acervo_mode"] = "Consultar"
        st.session_state["minutas_record_pagina"] = "processo-da-elvira"
        st.session_state["minutas_acervo_role_ESPO_elvira.pca.prefeituras"] = "parecer"
        clear_procurador_session()
        st.write("limpo")

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception

    def ausente(chave):
        try:
            app.session_state[chave]
        except KeyError:
            return True
        return False

    assert ausente("minutas_procurador")
    assert ausente("minutas_query_ESPO_elvira.pca.prefeituras")
    assert ausente("minutas_acervo_mode")
    assert ausente("minutas_record_pagina")
    assert ausente("minutas_acervo_role_ESPO_elvira.pca.prefeituras")
