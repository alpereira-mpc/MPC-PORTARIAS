"""Contratos do cadastro multiprocurador, sem banco remoto."""

import pytest

from services.minutas_procuradores import (
    PROCURADORES,
    MinutasContextoError,
    aplicar_contexto,
    assinatura_neutra,
    camadas_prompt,
    chave_efetiva,
    config_neutra,
    exigir_procurador,
    legado_espo,
    listar_ativos,
    mesmo_escopo,
    navegacao_legada,
    procurador_do_repositorio,
    resolver_configuracao,
    visual_neutro,
)
from services.minutas_specialties import CATALOG, PROSECUTOR_KEY, scope


SIGLAS = ("ESPO", "MTTF", "BLTC", "SBBQ", "IBMF", "MASN", "LAF")


def test_cadastro_tem_exatamente_sete_perfis_sem_duplicidade():
    assert [item.sigla for item in PROCURADORES] == list(SIGLAS)
    assert len({item.sigla for item in PROCURADORES}) == 7
    assert len({item.chave for item in PROCURADORES}) == 7
    assert len(listar_ativos()) == 7
    elvira = legado_espo()
    assert elvira.nome == "Elvira Samara Pereira de Oliveira"
    assert elvira.nome_exibicao == "Elvira Samara"
    assert elvira.chave == PROSECUTOR_KEY == "elvira samara"
    assert elvira.prefixo == "elvira"
    assert exigir_procurador("mttf").nome == "Marcílio Toscano Franca Filho"
    assert exigir_procurador("MASN").nome == "Manoel Antônio dos Santos Neto"


def test_operacao_nova_sem_procurador_nao_assume_espo():
    for valor in (None, "", "   ", "oitavo", "PROGE"):
        with pytest.raises(MinutasContextoError):
            exigir_procurador(valor)


def test_repositorio_legado_sem_chave_permanece_no_piloto():
    class Repositorio:
        pass

    assert procurador_do_repositorio(Repositorio()).sigla == "ESPO"
    assert chave_efetiva(Repositorio()) == "elvira samara"


def test_repositorio_com_chave_desconhecida_nao_le_o_piloto():
    class Repositorio:
        procuradora_chave = "gabinete-ambiguo"

    with pytest.raises(MinutasContextoError):
        procurador_do_repositorio(Repositorio())


def test_configuracao_dos_novos_perfis_nao_herda_elvira():
    catalogo = scope("elvira.pca.rpps")["config"]
    assert catalogo.get("template") == "elvira"
    for sigla in SIGLAS:
        if sigla == "ESPO":
            resolvida = resolver_configuracao(sigla, catalogo=catalogo)
            assert resolvida.get("template") == "elvira"
            continue
        resolvida = resolver_configuracao(sigla, catalogo=catalogo)
        assert resolvida == config_neutra()
        assert resolvida["template"] != "elvira"
        assert resolvida["teses"] == []
        assert "elvira" not in assinatura_neutra(sigla)["cargo"].casefold()
        assert assinatura_neutra(sigla)["nome"] != "ELVIRA SAMARA PEREIRA DE OLIVEIRA"
        texto = camadas_prompt(sigla, "Denúncias")
        assert "outro gabinete" in texto
        assert "Elvira Samara Pereira de Oliveira" not in texto


def test_contexto_espo_preserva_configuracao_e_rejeita_chave_alheia():
    perfil = dict(scope("elvira.denuncias"))
    aplicado = aplicar_contexto(perfil, "ESPO")
    assert aplicado["procuradora"] == "Elvira Samara"
    assert aplicado["procuradora_chave"] == "elvira samara"
    assert aplicado["config"] == perfil["config"]
    with pytest.raises(MinutasContextoError):
        aplicar_contexto({"id": "elvira.denuncias", "procuradora_chave": "mttf", "config": {}}, "ESPO")


def test_contexto_novo_nao_carrega_configuracao_juridica_da_elvira():
    perfil = dict(scope("elvira.pca.rpps"))
    aplicado = aplicar_contexto(perfil, "BLTC")
    assert aplicado["id"] == "elvira.pca.rpps"
    assert aplicado["procuradora_chave"] == "bltc"
    assert aplicado["procuradora"] == "Bradson Tibério Luna Camelo"
    assert aplicado["config"]["template"] == "institucional"
    assert aplicado["config"]["teses"] == []
    assert mesmo_escopo(
        {"procuradora_chave": "bltc", "procuradora": aplicado["procuradora"]}, "BLTC"
    )
    assert not mesmo_escopo(
        {"procuradora_chave": "elvira samara", "procuradora": "Elvira Samara"}, "BLTC"
    )


def test_visual_neutro_nao_repete_margem_calibrada_do_piloto():
    neutro = visual_neutro()
    assert neutro["top_cm"] != 6.35
    assert neutro["fonte"] == "Arial"


def test_catalogo_tecnico_continua_unico():
    assert len({item["id"] for item in CATALOG}) == len(CATALOG) > 20
    assert {item["procuradora_chave"] for item in CATALOG} == {"elvira samara"}
    assert "elvira.denuncias" in {item["id"] for item in CATALOG}


def test_navegacao_legada_so_liga_com_sinal_explicito():
    assert navegacao_legada({}) is False
    assert navegacao_legada({"MINUTAS_NAV_LEGADA": False}) is False
    assert navegacao_legada({"MINUTAS_NAV_LEGADA": True}) is True
    assert navegacao_legada(None) is False


def test_acervo_e_docx_nao_cruzam_tres_perfis():
    from services.minutas_docx import MinutasDocxError, MinutasDocxService
    from services.minutas_library import MinutasLibraryService, MinutasValidationError
    from tests.access_testing import TEST_IDENTITY
    from tests.test_minutas_library import FakeRepository, FakeStorage

    registros = {
        "ESPO": ("espo", "elvira samara", "Elvira Samara"),
        "MTTF": ("mttf", "mttf", "Marcílio Toscano Franca Filho"),
        "BLTC": ("bltc", "bltc", "Bradson Tibério Luna Camelo"),
    }
    repositorio = FakeRepository()
    for identificador, chave, nome in registros.values():
        repositorio.records[identificador] = {
            "id": identificador,
            "procuradora_chave": chave,
            "procuradora": nome,
        }
    armazenamento = FakeStorage()
    for sigla, (identificador, chave, _nome) in registros.items():
        servico = MinutasLibraryService(
            repositorio, armazenamento, TEST_IDENTITY, {}, procurador=sigla,
        )
        assert [item["id"] for item in servico.list()] == [identificador]
        assert servico.get(identificador)["procuradora_chave"] == chave
        for outro in registros:
            if outro == sigla:
                continue
            with pytest.raises(MinutasValidationError, match="não encontrada"):
                servico.get(registros[outro][0])

    class Biblioteca:
        def __init__(self, sigla):
            self.procurador = exigir_procurador(sigla)

    documento = MinutasDocxService.__new__(MinutasDocxService)
    documento.library = Biblioteca("ESPO")
    perfil, visual = documento._presentation(
        {"procuradora": "Elvira Samara", "procuradora_chave": "elvira samara"}
    )
    assert visual is None
    assert "Procuradora-Geral" in perfil["cargo"]
    documento.library = Biblioteca("BLTC")
    perfil, visual = documento._presentation(
        {
            "procuradora": "Bradson Tibério Luna Camelo",
            "procuradora_chave": "bltc",
        }
    )
    assert visual["top_cm"] == 2.5
    assert visual["fonte"] == "Arial"
    assert "Procuradora-Geral" not in perfil["cargo"]
    assert perfil["nome"] == "BRADSON TIBÉRIO LUNA CAMELO"
    with pytest.raises(MinutasDocxError, match="não pertence"):
        documento._presentation(
            {"procuradora": "Elvira Samara", "procuradora_chave": "elvira samara"}
        )


def test_pesquisa_descarta_precedente_de_outro_procurador():
    from services.minutas_search import MinutasSearchService
    from services.minutas_specialties import RPPS

    class Repositorio:
        def search_opinions(self, *_args, **_kwargs):
            return [
                _linha("1", "Elvira Samara", "elvira samara"),
                _linha("2", "Marcílio Toscano Franca Filho", "mttf"),
            ]

    class Biblioteca:
        procurador = exigir_procurador("ESPO")
        specialty_id = RPPS
        repository = Repositorio()

        @staticmethod
        def _authorize():
            return "admin@test.local"

    resultado = MinutasSearchService(Biblioteca()).search("prescrição")
    assert [item["referencia_id"] for item in resultado["resultados"]] == ["1"]


def _linha(identificador, nome, chave):
    return {
        "referencia_id": identificador,
        "documento_id": "doc-" + identificador,
        "procuradora": nome,
        "procuradora_chave": chave,
        "numero_processo": identificador + "/26",
        "exercicio": 2025,
        "subtipo": "Instituto/Autarquia",
        "arquivo_nome": "parecer.pdf",
        "arquivo_path": "referencias/" + identificador + "/parecer/a.pdf",
        "arquivo_sha256": "ab" * 32,
        "extrator_versao": "teste",
        "atualizado_em": "2026-10-16T00:00:00+00:00",
        "trecho": "trecho documental",
    }


def test_sem_principal_a_exclusao_falha_fora_do_pytest(monkeypatch):
    from services.minutas_library import MinutasAccessDenied, MinutasLibraryService
    from tests.access_testing import TEST_IDENTITY
    from tests.test_minutas_library import FakeRepository, FakeStorage

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    servico = MinutasLibraryService(
        FakeRepository(), FakeStorage(), TEST_IDENTITY, {}, procurador="ESPO",
    )
    with pytest.raises(MinutasAccessDenied, match="administrativa"):
        servico.delete("qualquer", confirmed=True)


def test_exclusao_permanente_exige_administrador_quando_ha_principal():
    from services.minutas_library import MinutasAccessDenied, MinutasLibraryService
    from tests.access_testing import TEST_IDENTITY
    from tests.test_minutas_library import FakeRepository, FakeStorage

    class Principal:
        ativo = True
        pode_admin = False
        administrator = False

    servico = MinutasLibraryService(
        FakeRepository(), FakeStorage(), TEST_IDENTITY, {},
        procurador="ESPO", principal=Principal(),
    )
    with pytest.raises(MinutasAccessDenied, match="administrativa"):
        servico.delete("qualquer", confirmed=True)
    Principal.pode_admin = True
    with pytest.raises(Exception, match="não encontrada|Confirme"):
        servico.delete("ausente", confirmed=True)


def test_migracao_sql_e_aditiva_e_so_associa_o_piloto_comprovado():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "database/minutas_procuradores.sql").read_text(encoding="utf-8")
    normalizado = " ".join(sql.casefold().split())
    assert "drop " not in normalizado
    assert "truncate" not in normalizado
    assert "delete from" not in normalizado
    for sigla in SIGLAS:
        assert f"'{sigla.lower()}'" in normalizado
    assert "where procuradora_chave = 'elvira samara'" in normalizado
    assert "and procurador_sigla is null" in normalizado
    assert "on conflict (sigla) do nothing" in normalizado
    assert "minutas_nav_legada" in normalizado
