"""Motor aposentadorias-v1, com IA simulada e normalização determinística."""

from io import BytesIO

from docx import Document
import pytest

from services.ai_service import (
    PROMPT_MINUTA_PARECER,
    _MINUTA_OUTPUT_SCHEMA,
    _minuta_contract,
)
from services.minutas_aposentadorias import (
    MOTOR,
    PROMPT_ANALISE,
    PROMPT_GERACAO,
    SPECIALTY_ID,
    beneficiary_candidates,
    header_lines,
    normalize_structure,
    reconcile_analysis,
    validate_analysis,
)
from services.minutas_docx import ELVIRA_TEMPLATE, MinutasDocxService
from services.minutas_generation import MinutasGenerationError, MinutasGenerationService
from services.minutas_search import MinutasSearchService
from services.minutas_specialties import (
    APOSENTADORIAS,
    CAMARAS,
    PREFEITURAS,
    RPPS,
    SECRETARIAS_MUNICIPAIS,
    SHARED_INSTRUCTION_MOTOR,
    instruction_generation_allowed,
    scope,
)
from tests.test_minutas_instrucao import _analysis, _doc, _profiled_library, _structure


FONTE = "fonte-1"
NOME = "Ana Clara Souza"
GESTOR = "Bruno Lima Santos"
SUCESSOR = "Carla Nunes Dias"
DECISAO = "Acórdão AC1-TC 01000/25"
TEXTO = f"""Natureza: Aposentadoria
Origem: Instituto de Previdência de Exemplo
A aposentadoria da Sra. {NOME} foi concedida pela Portaria 10/2026.
Beneficiária: {NOME}
{DECISAO}
O ex-gestor {GESTOR} não atendeu a decisão no prazo.
A gestora sucessora {SUCESSOR} apresentou a retificação publicada.
"""


def _context(text=TEXTO):
    return {
        "documentos_processo_alvo": [{
            "fonte_id": FONTE,
            "conteudo": [{"pagina": 1, "texto": text}],
        }],
    }


def _dims(**extra):
    base = {
        "fase": "APRECIACAO_INICIAL",
        "legalidade": "LEGAL",
        "cumprimento": "NAO_APLICAVEL",
        "responsabilizacao": "NAO_APLICAVEL",
        "beneficiario": NOME,
        "decisao": "",
        "responsavel_cumprimento": "",
        "responsavel_sancao": "",
        "gestor_sucessor": "",
        "destinatario_diligencia": "",
        "providencia": "",
        "harmonia_auditoria": True,
    }
    base.update(extra)
    return base


def _analysis_case(questoes=(), **extra):
    dimensions = _dims(**extra)
    return {
        "resumo_processual": "Aposentadoria em exame na instrução.",
        "fase": dimensions["fase"],
        "beneficiario": NOME,
        "ato": "",
        "questoes": list(questoes),
        "dimensoes": dimensions,
        "sem_irregularidades_identificadas": dimensions["legalidade"] == "LEGAL",
        "pontos_atencao_gerais": [],
    }


def _questao(**extra):
    item = {
        "id": "Q1",
        "apontamento": "Requisito do benefício ainda em exame",
        "tema": "OUTRO",
        "saneamento": "NAO_ALEGADO",
        "impeditiva": False,
        "ilicitude_material": False,
        "duplicidade_com_suporte": None,
        "manifestacao_beneficiario": "NAO_EXIGIDA",
        "alteracao_constitucional": "NAO_APLICAVEL",
        "retificacao_publicada": "NAO_APLICAVEL",
        "cancelamento_comprovado": "NAO_APLICAVEL",
        "normas_mencionadas": [],
        "hipoteses_juridicas": [],
        "fontes_saneamento": [],
    }
    item.update(extra)
    return item


def _draft(*requests):
    return {
        "identificacao_processual": "Minuta",
        "ementa": "Aposentadoria. Exame de legalidade para registro.",
        "relatorio": "A instrução examina o ato de aposentadoria.",
        "relatorio_paragrafos": ["A instrução examina o ato de aposentadoria."],
        "fundamentacao_geral": (
            "A competência de registro observa o art. 71, III, da Constituição Federal."
        ),
        "fundamentacao_geral_paragrafos": [
            "A competência de registro observa o art. 71, III, da Constituição Federal.",
        ],
        "topicos_irregularidades": [{
            "titulo": "Exame do benefício",
            "texto": "A questão foi examinada a partir da instrução do processo.",
            "paragrafos": ["A questão foi examinada a partir da instrução do processo."],
        }],
        "responsabilizacao_administrativa": {"titulo": "Responsabilização administrativa", "texto": ""},
        "conclusao_pedidos": "Ante o exposto.",
        "pedidos": list(requests),
        "encerramento": "É o Parecer.",
    }


def _finish(analysis, *requests, text=TEXTO):
    validated = validate_analysis(analysis, _context(text))
    reconciled, warnings = reconcile_analysis(validated, _context(text))
    structure, extra = normalize_structure(
        _draft(*requests),
        {"numero_processo": "01000/26", "origem": "Instituto de Previdência de Exemplo"},
        _context(text),
        reconciled,
    )
    again, _repeated = normalize_structure(structure, {
        "numero_processo": "01000/26",
        "origem": "Instituto de Previdência de Exemplo",
        "beneficiario": structure["dimensoes"].get("beneficiario") or NOME,
    }, _context(text), reconciled)
    assert again["pedidos"] == structure["pedidos"]
    assert again["conclusao_pedidos"] == structure["conclusao_pedidos"]
    return structure, warnings + extra


def _library(**profile):
    base = dict(scope(APOSENTADORIAS))
    base.update(motor=MOTOR, estado="Em testes", config_versao="1", id=APOSENTADORIAS)
    base.update(profile)

    class Repository:
        specialty_id = base["id"]

        def profile(self):
            return base

    class Library:
        specialty_id = base["id"]
        repository = Repository()
        storage = object()

        def _authorize(self):
            return None

    return Library()


def test_amostra_nao_entra_na_logica_e_os_contratos_sao_proprios():
    source = open("services/minutas_aposentadorias.py", encoding="utf-8").read().casefold()
    for token in (
        "luzinete", "mônica", "monica maria", "emmanuel", "erilânia", "joselita",
        "madalena", "willams", "terezinha", "francisca bezerra", "josefa leite",
        "01559", "03977", "04597", "06340", "07955", "09373", "16618", "19504",
        "7.676,27",
    ):
        assert token.casefold() not in source
    assert "art. 71" in PROMPT_ANALISE and "ec nº 138/2025" in PROMPT_ANALISE.casefold()
    assert "não julga contas" in PROMPT_GERACAO.casefold()
    assert "art. 58 da lei complementar estadual" not in PROMPT_GERACAO.casefold()
    prompt, schema = _minuta_contract({"motor": MOTOR}, "geracao")
    assert prompt == PROMPT_GERACAO
    assert "analise_sancionatoria" not in schema["required"]
    assert _minuta_contract({}, "geracao") == (PROMPT_MINUTA_PARECER, _MINUTA_OUTPUT_SCHEMA)


def test_legalidade_registro_e_apreciacao_inicial():
    structure, _warnings = _finish(
        _analysis_case(),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    assert structure["pedidos"] == [
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    ]
    assert "cumprimento" not in structure["pedidos"][0].casefold()
    assert "Natureza: Aposentadoria" in structure["identificacao_processual"]
    assert "Exercício" not in structure["identificacao_processual"]
    assert "Responsável:" not in structure["identificacao_processual"]
    assert "É o Relatório. Passo a opinar." in structure["relatorio"]
    assert structure["encerramento"] == "É o Parecer."


def test_ilegalidade_material_nega_registro_sem_inventar_diligencia():
    structure, warnings = _finish(
        _analysis_case(
            [_questao(tema="REGRA_CONSTITUCIONAL", ilicitude_material=True, saneamento="ALEGADO_NAO_COMPROVADO")],
            legalidade="ILEGAL",
            harmonia_auditoria=False,
        ),
        f"Pela ilegalidade do ato de aposentadoria de {NOME} e negativa do respectivo registro.",
    )
    assert "ilegalidade" in structure["pedidos"][0].casefold()
    assert "negativa" in structure["pedidos"][0].casefold()
    assert len(structure["pedidos"]) == 1
    assert not any("dilig" in item.casefold() for item in structure["pedidos"])
    assert "harmonia" not in structure["conclusao_pedidos"].casefold()
    assert warnings is not None


def test_diligencia_nao_conclui_registro_nem_negativa():
    structure, _warnings = _finish(
        _analysis_case(legalidade="DILIGENCIA", harmonia_auditoria=False),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
        "Pela assinação de prazo para apresentar a certidão integral, a cargo do Instituto de Exemplo.",
    )
    assert structure["pedidos"] == [
        "Pela assinação de prazo para apresentar a certidão integral, a cargo do Instituto de Exemplo.",
    ]
    assert structure["dimensoes"]["legalidade"] == "DILIGENCIA"


def test_cumprimento_integral_parcial_nao_cumprido_e_nao_demonstrado():
    integral, _warnings = _finish(
        _analysis_case(fase="CUMPRIMENTO_DECISAO", cumprimento="INTEGRAL", decisao=DECISAO),
        f"Pela declaração de cumprimento da decisão {DECISAO}, nos termos comprovados nos autos.",
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    assert integral["pedidos"][0].startswith("Pela declaração de cumprimento da decisão")
    assert "legalidade" in integral["pedidos"][1].casefold()
    partial, _warnings = _finish(
        _analysis_case(fase="CUMPRIMENTO_DECISAO", cumprimento="PARCIAL", decisao=DECISAO),
        f"Pela declaração de cumprimento integral da decisão {DECISAO}.",
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    assert "parcial" in partial["pedidos"][0].casefold()
    denied, _warnings = _finish(
        _analysis_case(
            fase="CUMPRIMENTO_DECISAO", cumprimento="NAO_CUMPRIDO", decisao=DECISAO,
            responsavel_cumprimento=GESTOR, legalidade="ILEGAL",
        ),
        f"Pela declaração de não cumprimento da decisão {DECISAO}, por {GESTOR}, conforme os fundamentos expostos.",
        f"Pela ilegalidade do ato de aposentadoria de {NOME} e negativa do respectivo registro.",
    )
    assert denied["pedidos"][0].casefold().startswith("pela declaração de não cumprimento")
    assert GESTOR in denied["pedidos"][0]
    pending, _warnings = _finish(
        _analysis_case(
            fase="CUMPRIMENTO_DECISAO", cumprimento="NAO_DEMONSTRADO", decisao=DECISAO,
            legalidade="DILIGENCIA",
        ),
        f"O cumprimento da decisão {DECISAO} ainda não foi demonstrado.",
        "Pela assinação de prazo para comprovar o cumprimento, a cargo do Instituto de Exemplo.",
    )
    assert "ainda não foi demonstrado" in pending["pedidos"][0].casefold()


def test_saneamento_comprovado_exige_fonte_e_documento_insuficiente_nao_registra():
    proved = _questao(
        saneamento="COMPROVADO",
        impeditiva=True,
        fontes_saneamento=[{"fonte_id": FONTE, "pagina": 1, "trecho_suporte": "sanada"}],
    )
    structure, _warnings = _finish(
        _analysis_case([proved]),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    assert "legalidade" in structure["pedidos"][0].casefold()
    insufficient = _questao(saneamento="COMPROVADO", impeditiva=True, fontes_saneamento=[])
    structure, warnings = _finish(
        _analysis_case([insufficient], legalidade="LEGAL"),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
        "Pela assinação de prazo para juntar o documento correspondente à falha, a cargo do Instituto de Exemplo.",
    )
    assert structure["dimensoes"]["legalidade"] == "DILIGENCIA"
    assert "registro" not in structure["pedidos"][0].casefold()
    assert warnings


@pytest.mark.parametrize("tema,publicado,esperado", (
    ("RETIFICACAO", "SIM", "LEGAL"),
    ("RETIFICACAO", "NAO", "DILIGENCIA"),
    ("MAGISTERIO", "NAO_APLICAVEL", "DILIGENCIA"),
    ("PROGRESSAO", "NAO_APLICAVEL", "DILIGENCIA"),
    ("PROVENTOS", "NAO_APLICAVEL", "DILIGENCIA"),
))
def test_retificacao_magisterio_progressao_e_proventos(tema, publicado, esperado):
    question = _questao(
        tema=tema,
        impeditiva=True,
        saneamento="COMPROVADO" if esperado == "LEGAL" else "INSUFICIENTE",
        retificacao_publicada=publicado,
        fontes_saneamento=(
            [{"fonte_id": FONTE, "pagina": 1, "trecho_suporte": "publicada"}]
            if esperado == "LEGAL" else []
        ),
    )
    requests = [f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro."]
    if esperado == "DILIGENCIA":
        requests.append("Pela assinação de prazo para comprovar o requisito específico, a cargo do Instituto de Exemplo.")
    structure, _warnings = _finish(_analysis_case([question]), *requests)
    assert structure["dimensoes"]["legalidade"] == esperado


def test_ctc_duplicidade_e_regra_constitucional():
    incomplete, _warnings = _finish(
        _analysis_case([_questao(tema="CTC", saneamento="INSUFICIENTE", impeditiva=True)]),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
        "Pela assinação de prazo para apresentar a CTC integral, a cargo do Instituto de Exemplo.",
    )
    assert incomplete["dimensoes"]["legalidade"] == "DILIGENCIA"
    corrected, _warnings = _finish(
        _analysis_case([_questao(
            tema="CTC", saneamento="COMPROVADO", impeditiva=True,
            fontes_saneamento=[{"fonte_id": FONTE, "pagina": 1, "trecho_suporte": "CTC retificada"}],
        )]),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    assert corrected["dimensoes"]["legalidade"] == "LEGAL"
    conflicting, _warnings = _finish(
        _analysis_case(
            [_questao(tema="CTC", saneamento="ALEGADO_NAO_COMPROVADO", impeditiva=True)],
            legalidade="ILEGAL",
        ),
        f"Pela ilegalidade do ato de aposentadoria de {NOME} e negativa do respectivo registro.",
        "Pela assinação de prazo para esclarecer a CTC, a cargo do Instituto de Exemplo.",
    )
    assert conflicting["dimensoes"]["legalidade"] == "DILIGENCIA"
    unsupported, _warnings = _finish(
        _analysis_case(
            [_questao(tema="DUPLICIDADE_TEMPO", duplicidade_com_suporte=False, ilicitude_material=True)],
            legalidade="ILEGAL",
        ),
        f"Pela ilegalidade do ato de aposentadoria de {NOME} e negativa do respectivo registro.",
        "Pela assinação de prazo para esclarecer o período, a cargo do Instituto de Exemplo.",
    )
    assert unsupported["dimensoes"]["legalidade"] == "DILIGENCIA"
    supported, _warnings = _finish(
        _analysis_case(
            [_questao(
                tema="DUPLICIDADE_TEMPO", duplicidade_com_suporte=True, ilicitude_material=True,
                saneamento="ALEGADO_NAO_COMPROVADO",
            )],
            legalidade="ILEGAL",
        ),
        f"Pela ilegalidade do ato de aposentadoria de {NOME} e negativa do respectivo registro.",
    )
    assert supported["pedidos"][0].casefold().startswith("pela ilegalidade")
    incompatible, _warnings = _finish(
        _analysis_case(
            [_questao(tema="REGRA_CONSTITUCIONAL", ilicitude_material=True, saneamento="ALEGADO_NAO_COMPROVADO")],
            legalidade="ILEGAL",
        ),
        f"Pela ilegalidade do ato de aposentadoria de {NOME} e negativa do respectivo registro.",
    )
    assert "negativa" in incompatible["pedidos"][0].casefold()


def test_acumulacao_opcao_e_emenda_superveniente():
    allowed, _warnings = _finish(
        _analysis_case([_questao(
            tema="ACUMULACAO", saneamento="COMPROVADO", cancelamento_comprovado="NAO_APLICAVEL",
            fontes_saneamento=[{"fonte_id": FONTE, "pagina": 1, "trecho_suporte": "cargos acumuláveis"}],
        )]),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    assert allowed["dimensoes"]["legalidade"] == "LEGAL"
    forbidden, _warnings = _finish(
        _analysis_case(
            [_questao(
                tema="ACUMULACAO", ilicitude_material=True, saneamento="ALEGADO_NAO_COMPROVADO",
                cancelamento_comprovado="NAO_APLICAVEL",
            )],
            legalidade="ILEGAL",
        ),
        f"Pela ilegalidade do ato de aposentadoria de {NOME} e negativa do respectivo registro.",
    )
    assert forbidden["dimensoes"]["legalidade"] == "ILEGAL"
    option, _warnings = _finish(
        _analysis_case(
            [_questao(tema="ACUMULACAO", cancelamento_comprovado="NAO", ilicitude_material=True)],
            legalidade="ILEGAL",
        ),
        f"Pela ilegalidade do ato de aposentadoria de {NOME} e negativa do respectivo registro.",
        "Pela assinação de prazo para comprovar o cancelamento do benefício incompatível, a cargo do Instituto de Exemplo.",
    )
    assert option["dimensoes"]["legalidade"] == "DILIGENCIA"
    assert "negativa" not in " ".join(option["pedidos"]).casefold()
    amendment, warnings = _finish(
        _analysis_case(
            [_questao(tema="ACUMULACAO", alteracao_constitucional="EXIGE_EXAME")],
            legalidade="LEGAL",
        ),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
        "Pela assinação de prazo para examinar a vigência da emenda, a cargo do Instituto de Exemplo.",
    )
    assert amendment["dimensoes"]["legalidade"] == "DILIGENCIA"
    assert any("retroatividade" in warning.casefold() for warning in warnings)


def test_reenquadramento_exige_manifestacao_do_beneficiario():
    structure, warnings = _finish(
        _analysis_case(
            [_questao(tema="REENQUADRAMENTO", manifestacao_beneficiario="AUSENTE", impeditiva=True)],
            legalidade="REVISAO_LEGAL",
            fase="CUMPRIMENTO_DECISAO",
        ),
        f"Pela legalidade do ato revisional de aposentadoria de {NOME} e concessão do registro.",
        f"Pela assinação de prazo para cientificar {NOME} sobre a alteração do fundamento, a cargo do Instituto de Exemplo.",
    )
    assert structure["dimensoes"]["legalidade"] == "DILIGENCIA"
    assert "revisional" not in " ".join(structure["pedidos"]).casefold()
    assert any("beneficiário" in warning.casefold() for warning in warnings)


def test_revisao_nao_e_concessao_inicial():
    structure, warnings = _finish(
        _analysis_case(fase="REVISAO", legalidade="LEGAL"),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    assert structure["dimensoes"]["legalidade"] == "REVISAO_LEGAL"
    assert "revisional" in structure["pedidos"][0].casefold()
    assert any("concessão inicial" in warning.casefold() for warning in warnings)


def test_gestor_sucessor_multa_e_registro_coexistem_sem_multa_automatica():
    both, _warnings = _finish(
        _analysis_case(
            fase="CUMPRIMENTO_DECISAO",
            cumprimento="NAO_CUMPRIDO",
            decisao=DECISAO,
            responsavel_cumprimento=GESTOR,
            responsabilizacao="SANCAO_CABIVEL",
            responsavel_sancao=GESTOR,
            gestor_sucessor=SUCESSOR,
        ),
        f"Pela declaração de não cumprimento da decisão {DECISAO}, por {GESTOR}.",
        f"Pela aplicação de multa ao Sr. {GESTOR}, com fulcro no art. 100 da lei orgânica do tribunal.",
        f"Pela aplicação de multa à Sra. {SUCESSOR}, com fulcro no art. 100 da lei orgânica do tribunal.",
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    joined = " ".join(both["pedidos"])
    assert GESTOR in joined and "multa" in joined.casefold()
    assert SUCESSOR not in joined
    assert "legalidade" in both["pedidos"][-1].casefold()
    assert [ _category_name(item) for item in both["pedidos"] ] == [
        "NAO_CUMPRIMENTO", "MULTA", "LEGAL",
    ]
    without_fine, _warnings = _finish(
        _analysis_case(
            fase="CUMPRIMENTO_DECISAO",
            cumprimento="NAO_CUMPRIDO",
            decisao=DECISAO,
            responsavel_cumprimento=GESTOR,
            responsabilizacao="SEM_SANCAO",
        ),
        f"Pela declaração de não cumprimento da decisão {DECISAO}, por {GESTOR}, sem aplicação de multa.",
        f"Pela aplicação de multa ao Sr. {GESTOR}, com fulcro no art. 100 da lei orgânica do tribunal.",
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    assert "multa ao" not in " ".join(without_fine["pedidos"]).casefold()
    assert "sem aplicação de multa" in without_fine["pedidos"][0].casefold()
    assert "legalidade" in without_fine["pedidos"][-1].casefold()


def _category_name(request):
    from services.minutas_aposentadorias import _category
    return _category(request)


def test_beneficiario_ignora_rodape_e_conflito_fica_manual():
    footer = f"""Impresso por alpereira em 15/10/2026. Validação: ABC123.
Responsável: Elvira S. P. de Oliveira
Assinado
Elvira Samara Pereira de Oliveira
A aposentadoria da Sra. {NOME} foi concedida pela Portaria 10/2026.
"""
    found = beneficiary_candidates(footer)
    assert list(found.values()) == [NOME]
    lines = header_lines({
        "numero_processo": "01000/26",
        "origem": "Instituto de Previdência de Exemplo",
        "beneficiario": NOME,
        "ato": "Portaria 10/2026",
    })
    assert "Beneficiário(a): " + NOME in lines
    assert "Natureza: Aposentadoria" in lines
    assert not any(line.startswith("Responsável") or "Exercício" in line for line in lines)
    conflict = beneficiary_candidates(
        f"A aposentadoria da Sra. {NOME}.\nA aposentadoria do Sr. {GESTOR}."
    )
    assert len(conflict) == 2
    metadata, warnings = __import__(
        "services.minutas_aposentadorias", fromlist=["resolve_header"]
    ).resolve_header({"numero_processo": "01000/26"}, _context(
        f"A aposentadoria da Sra. {NOME}.\nA aposentadoria do Sr. {GESTOR}."
    ))
    assert "beneficiario" not in metadata
    assert warnings


def test_norma_valor_e_nome_nao_sao_inventados():
    structure, warnings = _finish(
        _analysis_case(),
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro.",
    )
    text = structure["texto"] if "texto" in structure else "\n".join(structure["pedidos"])
    assert "9999" not in text and "R$" not in text
    dirty = _draft(
        f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro."
    )
    dirty["fundamentacao_geral"] += " Aplica-se a Lei nº 9999/2099."
    dirty["fundamentacao_geral_paragrafos"].append("Aplica-se a Lei nº 9999/2099.")
    _validated = validate_analysis(_analysis_case(), _context())
    reconciled, _first = reconcile_analysis(_validated, _context())
    normalized, extra = normalize_structure(
        dirty,
        {"numero_processo": "01000/26", "origem": "Instituto de Exemplo"},
        _context(),
        reconciled,
    )
    assert any("9999" in warning for warning in extra)
    assert "R$" not in "\n".join(normalized["pedidos"])
    assert NOME in normalized["pedidos"][0]


def test_pesquisa_sem_precedente_rag_isolado_e_docx():
    captured = {}

    def analyze(context):
        captured["motor"] = context["motor"]
        captured["orientacao"] = context["orientacao_dispositivo"]
        captured["alvo"] = context["processo_alvo"]
        return {"analise": _analysis_case(), "modelo": "simulado"}

    def generate(context):
        captured["geracao"] = context["motor"]
        captured["historicas"] = context.get("referencias_historicas")
        return {"estrutura": _draft(
            f"Pela legalidade do ato de aposentadoria de {NOME} e concessão do respectivo registro."
        ), "modelo": "simulado"}

    def search(query, **kwargs):
        captured.setdefault("buscas", []).append((query, kwargs.get("exclude_process_key")))
        return {"resultados": [
            {"papel": "parecer", "referencia_id": "outro", "processo_chave": "0200026",
             "numero_processo": "02000/26", "trecho": "Critério de registro do art. 71, III.",
             "parecer_definitivo": True},
            {"papel": "parecer", "referencia_id": "alvo", "processo_chave": "0100026",
             "numero_processo": "01000/26", "trecho": "Fato do próprio processo."},
            {"papel": "parecer", "referencia_id": "minuta", "processo_chave": "0300030",
             "numero_processo": "03000/30", "trecho": "Minuta não aprovada.",
             "parecer_definitivo": False},
            {"papel": "auditoria", "referencia_id": "relatorio", "processo_chave": "0200026",
             "numero_processo": "02000/26", "trecho": "Fato de auditoria."},
        ]}

    document = _doc("inicial.pdf", TEXTO, "relatorio_inicial")
    service = MinutasGenerationService(
        _library(), analyze=analyze, generate=generate, search=search,
    )
    analyzed = service.analyze_instruction(
        [document], processo="01000/26", origem="Instituto de Previdência de Exemplo",
        beneficiario=NOME,
    )
    assert captured["motor"] == MOTOR
    assert captured["orientacao"] == "APOSENTADORIA_REGISTRO"
    assert captured["alvo"]["especialidade_id"] == SPECIALTY_ID
    assert "exercicio" not in captured["alvo"]
    assert [item["referencia_id"] for item in analyzed["referencias"]["referencias"]] == ["outro"]
    assert captured["buscas"][0][1] == "0100026"
    generated = service.generate_instruction(
        [document], analyzed, processo="01000/26",
        origem="Instituto de Previdência de Exemplo", beneficiario=NOME,
    )
    assert captured["geracao"] == MOTOR
    assert generated["estrutura"]["pedidos"][0].casefold().startswith("pela legalidade")
    assert "Prestação de Contas" not in generated["texto"]
    word = MinutasDocxService(_library(), template_path=ELVIRA_TEMPLATE).export_instruction(
        analyzed, generated, processo="01000/26", origem="Instituto de Previdência de Exemplo",
    )
    paragraphs = [item.text for item in Document(BytesIO(word.content)).paragraphs]
    body = "\n".join(paragraphs)
    assert "Natureza: Aposentadoria" in body
    assert f"Beneficiário(a): {NOME}" in body
    assert "Exercício das contas" not in body
    assert "Responsável:" not in body
    assert "Pela legalidade do ato de aposentadoria" in body
    assert word.name == "MINUTA_PARECER_TC_01000_26.docx"

    empty = MinutasGenerationService(
        _library(),
        analyze=lambda _context: {"analise": {
            **_analysis_case(),
            "resumo_processual": "Ato.",
            "questoes": [],
        }},
        generate=generate,
        search=lambda *_args, **_kwargs: {"resultados": []},
    )
    quiet = empty.analyze_instruction([document], processo="01000/26", origem="Instituto de Exemplo")
    assert quiet["referencias"]["estado"] == "SEM_CONSULTAS"
    assert quiet["referencias_historicas"] == 0


def test_rag_do_servico_fica_na_especialidade():
    captured = {}

    class Repository:
        specialty_id = APOSENTADORIAS

        def search_opinions(self, *_args, **kwargs):
            captured.update(kwargs)
            return []

    class Library:
        specialty_id = APOSENTADORIAS
        repository = Repository()

        def _authorize(self):
            return None

    result = MinutasSearchService(Library()).search("registro de aposentadoria")
    assert captured["specialty_id"] == APOSENTADORIAS
    assert result["especialidade_id"] == APOSENTADORIAS
    assert result["resultados"] == []


@pytest.mark.parametrize("profile", (
    {"motor": SHARED_INSTRUCTION_MOTOR},
    {"motor": MOTOR, "config_versao": "2"},
    {"motor": MOTOR, "estado": "A iniciar"},
    {"id": "elvira.pessoal.pensoes", "motor": MOTOR, "estado": "Em testes"},
    {"id": "elvira.pessoal.reformas", "motor": MOTOR, "estado": "Em testes"},
    {"id": "elvira.pessoal.admissoes", "motor": MOTOR, "estado": "Em testes"},
))
def test_motor_configuracao_e_outras_especialidades_bloqueados(profile):
    library = _library(**profile)
    service = MinutasGenerationService(library, analyze=lambda *_a, **_k: {}, generate=lambda *_a, **_k: {})
    with pytest.raises(MinutasGenerationError, match="motor habilitado|aposentadorias"):
        service.analyze_instruction([_doc("a.pdf", TEXTO, "relatorio_inicial")], processo="01000/26")
    assert instruction_generation_allowed(library.repository.profile()) is False


def test_quatro_motores_de_pca_continuam_habilitados():
    assert instruction_generation_allowed(scope(RPPS)) is True
    captured = {}
    text = "Pagamento sem cobertura documental no instituto de previdencia municipal"

    def generate(context):
        captured["orientacao"] = context["orientacao_dispositivo"]
        captured["motor"] = context.get("motor")
        return {"estrutura": _structure(), "modelo": "pca"}

    for specialty, expected in (
        (RPPS, "DEMAIS_PCA_JULGAMENTO_DIRETO"),
        (CAMARAS, "DEMAIS_PCA_JULGAMENTO_DIRETO"),
        (SECRETARIAS_MUNICIPAIS, "DEMAIS_PCA_JULGAMENTO_DIRETO"),
        (PREFEITURAS, "PREFEITURA_CONDICIONAL_DUAS_COMPETENCIAS"),
    ):
        state = "Funcional com ajustes" if specialty == RPPS else "Em testes"
        library = _profiled_library(specialty, motor=SHARED_INSTRUCTION_MOTOR, estado=state)
        document = _doc("inicial.pdf", text, "relatorio_inicial")
        service = MinutasGenerationService(
            library,
            analyze=lambda _context, document=document: {
                "analise": _analysis(document), "modelo": "pca",
            },
            generate=generate,
        )
        analyzed = service.analyze_instruction(
            [document], processo="02108/26", exercicio=2025, origem="Entidade de Exemplo",
        )
        service.generate_instruction(
            [document], analyzed, processo="02108/26", exercicio=2025, origem="Entidade de Exemplo",
        )
        assert captured["orientacao"] == expected
        assert captured.get("motor") != MOTOR


def test_navegacao_preserva_filtro_e_descarta_instrucao_de_aposentadoria():
    from streamlit.testing.v1 import AppTest

    def page():
        import streamlit as st
        from services.minutas_specialties import APOSENTADORIAS as specialty
        from services.minutas_specialties_ui import synchronize_scope

        class Repository:
            specialty_id = specialty

            def scope_revision(self):
                return "rev-aposentadoria"

        class Library:
            specialty_id = specialty
            repository = Repository()

        st.session_state["minutas_acervo_query"] = "magistério"
        st.session_state["minutas_instrucao_documentos"] = [{"id": "tmp"}]
        st.session_state["minutas_instrucao_beneficiario"] = "Nome provisório"
        synchronize_scope(Library(), {"email": "assessor@mpc"})

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception
    assert app.session_state["minutas_acervo_query"] == "magistério"
    assert "minutas_instrucao_documentos" not in app.session_state
    assert "minutas_instrucao_beneficiario" not in app.session_state
