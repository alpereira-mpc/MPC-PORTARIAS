"""Motor denuncias-v1, isolado das regras de PCA e de atos de pessoal."""

from copy import deepcopy
import re
import unicodedata

from services.minutas_specialties import (
    DENUNCIAS,
    DENUNCIAS_MOTOR,
    INSTRUCTION_READY_STATES,
    PROSECUTOR_KEY,
)

MOTOR = DENUNCIAS_MOTOR
CONFIG_VERSION = "1"
SPECIALTY_ID = DENUNCIAS
ORIENTATION = "DENUNCIA_ADMISSIBILIDADE_MERITO_CONDICIONAL"
ADMISSIBILIDADE = ("CONHECIMENTO", "NAO_CONHECIMENTO", "PENDENTE", "NAO_IDENTIFICADA")
MERITO = ("PROCEDENTE", "IMPROCEDENTE", "PROCEDENTE_PARCIAL", "PENDENTE", "NAO_APLICAVEL")
SANEAMENTO = ("NAO_ALEGADO", "ALEGADO_NAO_COMPROVADO", "COMPROVADO", "INSUFICIENTE", "NAO_APLICAVEL")


def _plain(value):
    return "".join(
        char for char in unicodedata.normalize("NFKD", str(value or "").casefold())
        if not unicodedata.combining(char)
    )


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _error(message):
    from services.minutas_generation import MinutasGenerationError
    raise MinutasGenerationError(message)


def is_denuncia_profile(profile):
    return (
        isinstance(profile, dict)
        and str(profile.get("id") or "") == SPECIALTY_ID
        and str(profile.get("motor") or "") == MOTOR
        and str(profile.get("config_versao") or "") == CONFIG_VERSION
        and str(profile.get("procuradora_chave") or "") == PROSECUTOR_KEY
        and str(profile.get("estado") or "") in INSTRUCTION_READY_STATES
    )


def _source_text(context):
    return "\n".join(
        str(unit.get("texto") or "")
        for document in (context or {}).get("documentos_processo_alvo") or ()
        if isinstance(document, dict)
        for unit in document.get("conteudo") or ()
        if isinstance(unit, dict)
    )


def _source_ids(context):
    return {
        str(document.get("fonte_id") or "")
        for document in (context or {}).get("documentos_processo_alvo") or ()
        if isinstance(document, dict) and document.get("fonte_id")
    }


def build_target(profile, processo, origem, periodo_fatos=""):
    from services.minutas_generation import _canonical_process_key
    from services.minutas_library import PROCURADORA
    if not is_denuncia_profile(profile):
        _error("O motor de denúncias não está habilitado para esta especialidade.")
    key = _canonical_process_key(processo)
    if len(key) != 7:
        _error("Informe o processo TC no formato 00000/00 antes de analisar a instrução.")
    return {
        "id": f"instrucao-{key}", "numero_processo": f"{key[:5]}/{key[5:]}",
        "processo_chave": key, "origem": _clean(origem), "periodo_fatos": _clean(periodo_fatos)[:180],
        "procuradora": PROCURADORA, "procuradora_chave": PROCURADORA.casefold(),
        "especialidade_id": SPECIALTY_ID, "classe": "DENUNCIA", "perfil": "",
        "classificacao_estado": "CLASSIFICADO", "subtipo": "", "parecer_definitivo": False,
        "instrucao_local": True, "motor": MOTOR, "config_versao": CONFIG_VERSION,
    }


def header_lines(target):
    lines = [
        f"Processo TC nº {target['numero_processo']}",
        "Natureza: Denúncia",
    ]
    if _clean(target.get("origem")):
        lines.append(f"Origem: {target['origem']}")
    if _clean(target.get("periodo_fatos")):
        lines.append(f"Período dos fatos: {target['periodo_fatos']}")
    return lines


def _bind(context, target):
    bound = deepcopy(context)
    process = dict(bound.get("processo_alvo") or {})
    process.update({
        "especialidade_id": SPECIALTY_ID, "classe": "DENUNCIA", "natureza": "Denúncia",
        "motor": MOTOR, "config_versao": CONFIG_VERSION, "subtipo": "",
    })
    process.pop("exercicio", None)
    if target.get("origem"):
        process["origem"] = target["origem"]
    bound["processo_alvo"] = process
    bound["motor"] = MOTOR
    bound["orientacao_dispositivo"] = ORIENTATION
    return bound


def validate_analysis(analysis, context):
    if not isinstance(analysis, dict) or not _clean(analysis.get("resumo_processual")):
        _error("A análise de denúncia não contém resumo processual válido.")
    if analysis.get("admissibilidade") not in ADMISSIBILIDADE:
        _error("A análise de denúncia não informou admissibilidade válida.")
    if analysis.get("merito") not in MERITO or analysis.get("saneamento") not in SANEAMENTO:
        _error("A análise de denúncia não informou mérito ou saneamento válido.")
    questions = analysis.get("questoes")
    if not isinstance(questions, list):
        _error("A análise de denúncia não contém as questões estruturadas.")
    source_ids = _source_ids(context)
    for question in questions:
        if not isinstance(question, dict) or not _clean(question.get("id")):
            _error("Questão da denúncia sem identificação válida.")
        for field in ("alegacoes", "fatos_comprovados", "lacunas_documentais", "conclusao_auditoria"):
            if not isinstance(question.get(field), list):
                _error("Questão da denúncia sem separação probatória completa.")
        for evidence in question.get("fatos_comprovados") or ():
            if not isinstance(evidence, dict) or str(evidence.get("fonte_id") or "") not in source_ids:
                _error("Fato comprovado da denúncia sem fonte do processo-alvo.")
    return analysis


def _rag_shim(analysis):
    return {"irregularidades": [{
        "id": item.get("id"), "apontamento": item.get("objeto") or item.get("id"),
        "normas_mencionadas": [], "hipoteses_juridicas": [], "argumentos_defesa": [],
    } for item in analysis.get("questoes") or ()]}


def recover_precedents(service, analysis, process_key):
    """O serviço de busca já está delimitado pela especialidade ativa da sessão."""
    return service._instruction_precedents(_rag_shim(analysis), process_key)


def _structure_required(structure):
    fields = ("ementa", "relatorio", "fundamentacao_geral", "conclusao_pedidos", "encerramento")
    if not isinstance(structure, dict) or any(not _clean(structure.get(field)) for field in fields):
        _error("A minuta de denúncia não contém as partes institucionais obrigatórias.")
    if not isinstance(structure.get("topicos_irregularidades"), list):
        _error("A minuta de denúncia não contém tópicos de fundamentação.")
    if not isinstance(structure.get("pedidos"), list) or not structure["pedidos"]:
        _error("A minuta de denúncia não contém providências estruturadas.")
    return structure


def _outcome_category(request):
    text = _plain(request)
    if "nao conhecimento" in text:
        return "NAO_CONHECIMENTO"
    if "conhecimento" in text:
        return "CONHECIMENTO"
    if "procedencia parcial" in text:
        return "PROCEDENTE_PARCIAL"
    if "improced" in text:
        return "IMPROCEDENTE"
    if "proced" in text:
        return "PROCEDENTE"
    if "arquiv" in text:
        return "ARQUIVAMENTO"
    if "diligenc" in text or "prazo" in text or "intim" in text:
        return "DILIGENCIA"
    return "OUTRO"


def normalize_structure(structure, target, context, analysis):
    structure = _structure_required(deepcopy(structure))
    warnings = []
    normalized = dict(structure)
    normalized["identificacao_processual"] = "\n".join(header_lines(target))
    normalized["encerramento"] = "É o Parecer."
    normalized["relatorio_paragrafos"] = [
        _clean(item) for item in normalized.get("relatorio_paragrafos") or () if _clean(item)
    ] or [_clean(normalized["relatorio"])]
    normalized["fundamentacao_geral_paragrafos"] = [
        _clean(item) for item in normalized.get("fundamentacao_geral_paragrafos") or () if _clean(item)
    ] or [_clean(normalized["fundamentacao_geral"])]
    topics = []
    for item in normalized["topicos_irregularidades"]:
        if isinstance(item, dict) and _clean(item.get("titulo")) and _clean(item.get("texto")):
            topics.append({
                "titulo": _clean(item["titulo"]), "texto": _clean(item["texto"]),
                "paragrafos": [_clean(part) for part in item.get("paragrafos") or () if _clean(part)]
                or [_clean(item["texto"])],
            })
    if not topics:
        _error("A minuta de denúncia não fundamentou as questões relevantes.")
    normalized["topicos_irregularidades"] = topics
    forbidden = ("parecer previo", "julgamento das contas", "legalidade do ato de aposentadoria", "registro de aposentadoria")
    kept = []
    for request in normalized["pedidos"]:
        request = _clean(request)
        if not request:
            continue
        if any(token in _plain(request) for token in forbidden):
            warnings.append("Pedido próprio de outro motor foi excluído da minuta de denúncia.")
            continue
        if request not in kept:
            kept.append(request)
    if not kept:
        _error("O dispositivo de denúncia ficou sem providência para revisão humana.")
    normalized["pedidos"] = kept
    normalized["responsabilizacao_administrativa"] = (
        normalized.get("responsabilizacao_administrativa")
        if isinstance(normalized.get("responsabilizacao_administrativa"), dict)
        else {"titulo": "Responsabilização", "texto": ""}
    )
    return normalized, warnings


def render_markdown(structure):
    return "\n\n".join((
        structure["identificacao_processual"], "## Ementa\n\n" + structure["ementa"],
        "## Relatório\n\n" + structure["relatorio"],
        "## Fundamentação\n\n" + structure["fundamentacao_geral"],
        "## Conclusão e propostas\n\n" + structure["conclusao_pedidos"] + "\n\n"
        + "\n".join(f"{index}. {item}" for index, item in enumerate(structure["pedidos"], 1)),
        structure["encerramento"],
    ))


def analyze(service, documentos, *, processo, origem="", periodo_fatos=""):
    from services.minutas_generation import _serialized_size
    from services.minutas_instrucao import montar_contexto
    target = build_target(service._instruction_profile(), processo, origem, periodo_fatos)
    prepared = montar_contexto(documentos, processo=target["numero_processo"], exercicio=None, origem=origem)
    context = _bind(prepared["contexto"], target)
    generated = service._analyze(context)
    analysis = validate_analysis((generated or {}).get("analise"), context)
    precedents = recover_precedents(service, analysis, target["processo_chave"])
    metrics = {
        key: generated[key]
        for key in ("modelo", "duracao_ms", "tokens_entrada", "tokens_saida", "tokens_total")
        if isinstance(generated, dict) and generated.get(key) is not None
    }
    return {
        "analise": analysis, "fontes_versao": prepared["versao"],
        "processo_alvo_id": target["id"], "processo_alvo_chave": target["processo_chave"],
        "procuradora_chave": target["procuradora_chave"], "fontes_alvo": prepared["fontes_alvo"],
        "referencias": precedents, "referencias_historicas": len(precedents.get("referencias") or ()),
        "advertencias_instrucao": prepared["advertencias"], "consolidacao_contexto": prepared["omissoes"],
        "contexto_bytes": _serialized_size(prepared["contexto"]),
        "metadados_cabecalho": {"periodo_fatos": target["periodo_fatos"]},
        "motor": MOTOR, "config_versao": CONFIG_VERSION,
        **metrics,
    }


def generate(service, documentos, analyzed, *, processo, origem="", periodo_fatos="", use_rag=True):
    from services.minutas_generation import _serialized_size
    from services.minutas_instrucao import montar_contexto
    target = build_target(service._instruction_profile(), processo, origem, periodo_fatos)
    prepared = montar_contexto(documentos, processo=target["numero_processo"], exercicio=None, origem=origem)
    if not isinstance(analyzed, dict) or analyzed.get("fontes_versao") != prepared["versao"]:
        _error("A instrução foi alterada. Analise novamente antes de gerar a minuta.")
    if analyzed.get("motor") not in (None, MOTOR):
        _error("A análise não pertence ao motor de denúncias.")
    context = _bind(prepared["contexto"], target)
    analysis = validate_analysis(analyzed.get("analise"), context)
    context["analise_estruturada"] = analysis
    if use_rag:
        opinions, passages = service._instruction_reference_lists(analyzed.get("referencias"))
        context["referencias_historicas"] = opinions
        context["referencias_juridicas_recuperadas"] = passages
        context["orientacao_referencias_juridicas"] = (
            "Precedentes não vinculantes: use somente critérios jurídicos; nunca transfira fatos, "
            "pessoas, períodos, valores ou conclusões de outro processo. A ausência de precedente não impede a minuta."
        )
    if _serialized_size(context) > 3_500_000:
        _error("A instrução excede o limite seguro de contexto.")
    generated = service._generate(context)
    structure, warnings = normalize_structure((generated or {}).get("estrutura"), target, context, analysis)
    return {
        "estrutura": structure, "texto": render_markdown(structure), "fontes_versao": prepared["versao"],
        "processo_alvo_id": target["id"], "processo_alvo_chave": target["processo_chave"],
        "procuradora_chave": target["procuradora_chave"],
        "metadados_cabecalho": {"periodo_fatos": target["periodo_fatos"]},
        "advertencias_qualidade": warnings, "advertencias_documentais": [], "motor": MOTOR,
        "config_versao": CONFIG_VERSION, "fontes_alvo": prepared["fontes_alvo"],
        "referencias_historicas": len(context.get("referencias_historicas") or ()),
        "referencias_juridicas_enviadas": len(context.get("referencias_juridicas_recuperadas") or ()),
    }


def analysis_contract():
    evidence = {"type": "OBJECT", "properties": {"fonte_id": {"type": "STRING"}, "pagina": {"type": "INTEGER"}, "trecho": {"type": "STRING"}}, "required": ["fonte_id", "pagina", "trecho"]}
    question = {"type": "OBJECT", "properties": {"id": {"type": "STRING"}, "objeto": {"type": "STRING"}, "alegacoes": {"type": "ARRAY", "items": {"type": "STRING"}}, "fatos_comprovados": {"type": "ARRAY", "items": evidence}, "lacunas_documentais": {"type": "ARRAY", "items": {"type": "STRING"}}, "conclusao_auditoria": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["id", "objeto", "alegacoes", "fatos_comprovados", "lacunas_documentais", "conclusao_auditoria"]}
    schema = {"type": "OBJECT", "properties": {"resumo_processual": {"type": "STRING"}, "admissibilidade": {"type": "STRING", "enum": list(ADMISSIBILIDADE)}, "objeto_denunciado": {"type": "STRING"}, "questoes": {"type": "ARRAY", "items": question}, "manifestacoes_interessados": {"type": "ARRAY", "items": {"type": "STRING"}}, "saneamento": {"type": "STRING", "enum": list(SANEAMENTO)}, "merito": {"type": "STRING", "enum": list(MERITO)}, "providencias_cabiveis": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["resumo_processual", "admissibilidade", "objeto_denunciado", "questoes", "manifestacoes_interessados", "saneamento", "merito", "providencias_cabiveis"]}
    return PROMPT_ANALISE, schema


def generation_contract():
    topic = {"type": "OBJECT", "properties": {"titulo": {"type": "STRING"}, "texto": {"type": "STRING"}, "paragrafos": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["titulo", "texto", "paragrafos"]}
    schema = {"type": "OBJECT", "properties": {"identificacao_processual": {"type": "STRING"}, "ementa": {"type": "STRING"}, "relatorio": {"type": "STRING"}, "relatorio_paragrafos": {"type": "ARRAY", "items": {"type": "STRING"}}, "fundamentacao_geral": {"type": "STRING"}, "fundamentacao_geral_paragrafos": {"type": "ARRAY", "items": {"type": "STRING"}}, "topicos_irregularidades": {"type": "ARRAY", "items": topic}, "responsabilizacao_administrativa": {"type": "OBJECT", "properties": {"titulo": {"type": "STRING"}, "texto": {"type": "STRING"}}, "required": ["titulo", "texto"]}, "conclusao_pedidos": {"type": "STRING"}, "pedidos": {"type": "ARRAY", "items": {"type": "STRING"}}, "encerramento": {"type": "STRING"}}, "required": ["identificacao_processual", "ementa", "relatorio", "relatorio_paragrafos", "fundamentacao_geral", "fundamentacao_geral_paragrafos", "topicos_irregularidades", "responsabilizacao_administrativa", "conclusao_pedidos", "pedidos", "encerramento"]}
    return PROMPT_GERACAO, schema


PROMPT_ANALISE = """Analise exclusivamente a instrução do processo-alvo de DENÚNCIA. Separe admissibilidade, objeto denunciado, alegações, fatos comprovados com fonte e página, lacunas documentais, manifestações dos interessados, conclusão da Auditoria, saneamento, mérito e providências. Não presuma conhecimento, procedência, improcedência, arquivamento, diligência, cautelar ou responsabilização. Observe a LC-PB nº 192/2024, o Regimento Interno do TCE-PB e a RN-TC nº 07/2024, especialmente arts. 241 a 247, apenas quando verificáveis no contexto e com alterações aplicáveis identificadas. Não invente fatos, valores, sanções, normas, páginas ou providências. Retorne somente JSON do schema."""
PROMPT_GERACAO = """Elabore minuta ministerial de DENÚNCIA apenas a partir do JSON recebido. Fatos vêm exclusivamente da instrução; precedentes históricos só orientam critério jurídico e nunca transferem fatos, pessoas, valores, períodos ou conclusões. Estruture relatório, fundamentação, conclusão, pedidos e encerramento exatamente \"É o Parecer.\". Distinga alegação, fato comprovado e lacuna documental. Observe LC-PB nº 192/2024, Regimento Interno e RN-TC nº 07/2024, arts. 241 a 247, somente quando houver suporte. Conhecimento, não conhecimento, procedência, improcedência, procedência parcial, diligência, arquivamento, cautelar e responsabilização só podem constar quando juridicamente cabíveis e sustentados. Não use regras de PCA, parecer prévio, julgamento de contas, registro de aposentadoria, nem sanção para completar o dispositivo. Retorne somente JSON do schema."""
