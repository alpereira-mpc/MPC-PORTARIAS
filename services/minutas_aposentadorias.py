"""Motor aposentadorias-v1: política, validação e dispositivo de atos de aposentadoria.

As regras vêm do padrão institucional observado nos pareceres definitivos da
Procuradora. Fatos, nomes, períodos, valores e conclusões da amostra não entram
na lógica de produção.
"""

from copy import deepcopy
import re
import unicodedata

from services.minutas_specialties import (
    APOSENTADORIAS,
    APOSENTADORIAS_MOTOR,
    INSTRUCTION_READY_STATES,
)

MOTOR = APOSENTADORIAS_MOTOR
CONFIG_VERSION = "1"
SPECIALTY_ID = APOSENTADORIAS
ORIENTATION = "APOSENTADORIA_REGISTRO"

LEGALIDADE = ("LEGAL", "ILEGAL", "DILIGENCIA", "REVISAO_LEGAL")
CUMPRIMENTO = (
    "INTEGRAL", "PARCIAL", "NAO_CUMPRIDO", "NAO_DEMONSTRADO", "NAO_APLICAVEL",
)
RESPONSABILIZACAO = ("NAO_APLICAVEL", "SEM_SANCAO", "SANCAO_CABIVEL", "REVISAO_HUMANA")
SANEAMENTO = ("NAO_ALEGADO", "ALEGADO_NAO_COMPROVADO", "COMPROVADO", "INSUFICIENTE")
FASES = ("APRECIACAO_INICIAL", "CUMPRIMENTO_DECISAO", "REVISAO", "NAO_IDENTIFICADA")
TEMAS = (
    "MAGISTERIO", "PROGRESSAO", "PROVENTOS", "CTC", "DUPLICIDADE_TEMPO",
    "REGRA_CONSTITUCIONAL", "ACUMULACAO", "REENQUADRAMENTO", "RETIFICACAO",
    "REVISAO", "OUTRO",
)
MANIFESTACAO = ("PRESENTE", "AUSENTE", "NAO_EXIGIDA", "NAO_IDENTIFICADA")
ALTERACAO = ("NAO_APLICAVEL", "EXIGE_EXAME", "EXAMINADA")
PROVA_TRINARIA = ("SIM", "NAO", "NAO_APLICAVEL")

_COMPETENCE = (
    "Compete ao Tribunal de Contas apreciar, para fins de registro, a legalidade "
    "das concessões de aposentadoria, nos termos do art. 71, inciso III, da "
    "Constituição Federal, observadas as normas aplicáveis ao Tribunal de Contas "
    "do Estado da Paraíba."
)
_ORDER = {
    "INTEGRAL": 0, "PARCIAL": 0, "NAO_CUMPRIMENTO": 0, "NAO_DEMONSTRADO": 0,
    "MULTA": 1,
    "REVISAO": 2, "LEGAL": 2, "ILEGAL": 2,
    "DILIGENCIA": 3,
    "OUTRO": 4,
}
_FOOTER = re.compile(
    r"impresso por|valida[cç][aã]o\s*:|codigo verificador|c[oó]digo verificador|"
    r"assinado|procuradora-geral|respons[aá]vel\s*:\s*elvira|"
    r"parecer\.\s*proc\.|tramita",
    re.IGNORECASE,
)
_STATUTE = re.compile(
    r"\b(?:lei(?:\s+complementar(?:\s+municipal|\s+estadual)?)?|"
    r"emenda(?:\s+constitucional(?:\s+estadual)?)?|"
    r"portaria|resolu[cç][aã]o|decreto)\s*"
    r"(?:n[ºo°.]*)?\s*(\d{1,6})(?:\s*/\s*(\d{2,4}))?",
    re.IGNORECASE,
)
_REJECTED_ACTOR = (
    "auditor", "procurador", "relator", "advogado", "defensor", "analista",
)


def _plain(value):
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(character for character in decomposed if not unicodedata.combining(character))


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def is_aposentadoria_profile(profile):
    """Perfil operacional deste motor, já com estado de teste ou superior."""
    if not isinstance(profile, dict):
        return False
    return (
        str(profile.get("id") or "") == SPECIALTY_ID
        and str(profile.get("motor") or "") == MOTOR
        and str(profile.get("config_versao") or "") == CONFIG_VERSION
        and str(profile.get("estado") or "") in INSTRUCTION_READY_STATES
    )


def _error(message):
    from services.minutas_generation import MinutasGenerationError
    raise MinutasGenerationError(message)


def _source_units(context):
    units = []
    for document in (context or {}).get("documentos_processo_alvo") or ():
        if not isinstance(document, dict):
            continue
        for unit in document.get("conteudo") or ():
            if isinstance(unit, dict):
                units.append((str(document.get("fonte_id") or ""), unit))
    return units


def _source_text(context):
    return "\n".join(str(unit.get("texto") or "") for _, unit in _source_units(context))


def _source_ids(context):
    return {fonte_id for fonte_id, _unit in _source_units(context) if fonte_id}


def _visible_lines(text):
    kept = []
    for line in str(text or "").splitlines():
        if _FOOTER.search(line):
            continue
        kept.append(line)
    return "\n".join(kept)


def _person_value(value):
    candidate = _clean(value).strip(" .;:-")
    normalized = _plain(candidate)
    if (
        not candidate
        or len(candidate.split()) < 2
        or len(candidate) > 160
        or any(character.isdigit() for character in candidate)
        or any(term in normalized for term in _REJECTED_ACTOR)
    ):
        return ""
    return candidate


def _unique(mapping):
    if len(mapping) == 1:
        return next(iter(mapping.values()))
    return ""


def beneficiary_candidates(text):
    """Nomes de beneficiário fora de rodapé, assinatura e metadado do TRAMITA."""
    visible = _visible_lines(text)
    found = {}
    label = re.compile(
        r"(?im)^\s*(?:interessad[oa](?:\(a\))?|benefici[aá]ri[oa](?:\(a\))?)\s*[:\-–—]\s*(?P<value>.+)$"
    )
    narrative = re.compile(
        r"\baposentadoria\s+d[eao]s?\s+"
        r"(?:(?:[Ss]r|[Ss]ra|[Ss]enhor|[Ss]enhora)\.?\s+)?"
        r"(?P<value>[A-ZÀ-Ý][\wÀ-ÿ'’]+(?:\s+[A-ZÀ-Ý][\wÀ-ÿ'’]+){1,6})"
    )
    for pattern in (label, narrative):
        for match in pattern.finditer(visible):
            person = _person_value(match.group("value"))
            if person:
                found.setdefault(_plain(person), person)
    return found


def act_candidates(text):
    visible = _visible_lines(text)
    found = {}
    pattern = re.compile(
        r"\bPortaria(?:\s*(?:AP|A))?\s*(?:n[ºo°.]*)?\s*(\d{1,6}\s*/\s*\d{2,4})",
        re.IGNORECASE,
    )
    for match in pattern.finditer(visible):
        value = "Portaria " + re.sub(r"\s+", "", match.group(1))
        found.setdefault(_plain(value), value)
    return found


def resolve_header(target, context, analysis=None):
    """Cabeçalho de aposentadoria. Conflito documental fica para conferência manual."""
    warnings = []
    source = _source_text(context)
    manual = _person_value((target or {}).get("beneficiario"))
    extracted = beneficiary_candidates(source)
    if manual:
        beneficiary = manual
    else:
        beneficiary = _unique(extracted)
        if len(extracted) > 1:
            warnings.append(
                "Há mais de um possível beneficiário na instrução. "
                "Confira manualmente; nenhum nome foi escolhido."
            )
    analysis_name = _person_value(((analysis or {}).get("dimensoes") or {}).get("beneficiario"))
    if analysis_name and beneficiary and _plain(analysis_name) != _plain(beneficiary):
        warnings.append(
            "O nome indicado na análise não coincide com a instrução. "
            "Prevaleceu a identificação documental ou a conferência manual."
        )
    elif analysis_name and not beneficiary and _plain(analysis_name) in {
        _plain(item) for item in extracted.values()
    }:
        beneficiary = analysis_name
    manual_act = _clean((target or {}).get("ato"))
    acts = act_candidates(source)
    if manual_act:
        act = manual_act
    else:
        act = _unique(acts)
        if len(acts) > 1:
            warnings.append(
                "Há mais de um ato mencionado na instrução. "
                "O cabeçalho não escolheu a portaria."
            )
    metadata = {}
    if _clean((target or {}).get("origem")):
        metadata["origem"] = _clean(target.get("origem"))
    if beneficiary:
        metadata["beneficiario"] = beneficiary
    if act:
        metadata["ato"] = act
    if not beneficiary:
        metadata["conferir_beneficiario"] = True
    return metadata, warnings


def header_lines(target):
    from services.minutas_generation import _draft_process_display
    process = _draft_process_display(target)
    origin = _clean(target.get("origem")) or "Não informada"
    lines = [
        "Parecer Nº",
        f"Processo TC Nº {process}",
        f"Origem: {origin}",
        "Natureza: Aposentadoria",
    ]
    beneficiary = _person_value(target.get("beneficiario"))
    lines.append(
        f"Beneficiário(a): {beneficiary}" if beneficiary else "Beneficiário(a): conferência manual"
    )
    act = _clean(target.get("ato"))
    if act:
        lines.append(f"Ato: {act}")
    return lines


def _dimensoes(raw):
    if not isinstance(raw, dict):
        _error("A análise de aposentadoria não discriminou as três dimensões jurídicas.")
    phase = str(raw.get("fase") or "")
    legal = str(raw.get("legalidade") or "")
    compliance = str(raw.get("cumprimento") or "")
    sanction = str(raw.get("responsabilizacao") or "")
    if phase not in FASES or legal not in LEGALIDADE or compliance not in CUMPRIMENTO or sanction not in RESPONSABILIZACAO:
        _error("A análise de aposentadoria trouxe encaminhamento fora das hipóteses admitidas.")
    return {
        "fase": phase,
        "legalidade": legal,
        "cumprimento": compliance,
        "responsabilizacao": sanction,
        "beneficiario": _person_value(raw.get("beneficiario")),
        "decisao": _clean(raw.get("decisao"))[:180],
        "responsavel_cumprimento": _person_value(raw.get("responsavel_cumprimento")),
        "responsavel_sancao": _person_value(raw.get("responsavel_sancao")),
        "gestor_sucessor": _person_value(raw.get("gestor_sucessor")),
        "destinatario_diligencia": _clean(raw.get("destinatario_diligencia"))[:180],
        "providencia": _clean(raw.get("providencia"))[:300],
        "harmonia_auditoria": bool(raw.get("harmonia_auditoria")),
    }


def _fontes_validas(fontes, ids):
    if not isinstance(fontes, list) or not fontes:
        return False
    for source in fontes:
        if (
            not isinstance(source, dict)
            or str(source.get("fonte_id") or "") not in ids
            or not isinstance(source.get("pagina"), int)
            or isinstance(source.get("pagina"), bool)
        ):
            return False
    return True


def _questao(raw, ids):
    if not isinstance(raw, dict) or not _clean(raw.get("id")) or not _clean(raw.get("apontamento")):
        _error("A análise de aposentadoria trouxe questão sem identificação.")
    saneamento = str(raw.get("saneamento") or "NAO_ALEGADO")
    tema = str(raw.get("tema") or "OUTRO")
    manifestacao = str(raw.get("manifestacao_beneficiario") or "NAO_EXIGIDA")
    alteracao = str(raw.get("alteracao_constitucional") or "NAO_APLICAVEL")
    retificacao = str(raw.get("retificacao_publicada") or "NAO_APLICAVEL")
    cancelamento = str(raw.get("cancelamento_comprovado") or "NAO_APLICAVEL")
    if (
        saneamento not in SANEAMENTO or tema not in TEMAS or manifestacao not in MANIFESTACAO
        or alteracao not in ALTERACAO or retificacao not in PROVA_TRINARIA
        or cancelamento not in PROVA_TRINARIA
    ):
        _error("A análise de aposentadoria classificou questão em hipótese não admitida.")
    suporte = raw.get("duplicidade_com_suporte")
    if suporte not in (None, True, False):
        _error("A análise de tempo contributivo não indicou o suporte documental da duplicidade.")
    fontes = raw.get("fontes_saneamento") or []
    if saneamento == "COMPROVADO" and not _fontes_validas(fontes, ids):
        saneamento = "INSUFICIENTE"
    return {
        "id": _clean(raw.get("id")),
        "apontamento": _clean(raw.get("apontamento")),
        "tema": tema,
        "saneamento": saneamento,
        "impeditiva": bool(raw.get("impeditiva")),
        "ilicitude_material": bool(raw.get("ilicitude_material")),
        "duplicidade_com_suporte": suporte,
        "manifestacao_beneficiario": manifestacao,
        "alteracao_constitucional": alteracao,
        "retificacao_publicada": retificacao,
        "cancelamento_comprovado": cancelamento,
        "normas": raw.get("normas_mencionadas") if isinstance(raw.get("normas_mencionadas"), list) else [],
        "hipoteses": raw.get("hipoteses_juridicas") if isinstance(raw.get("hipoteses_juridicas"), list) else [],
        "fontes_saneamento": fontes if isinstance(fontes, list) else [],
    }


def validate_analysis(analysis, context):
    if not isinstance(analysis, dict):
        _error("A IA não retornou a análise de aposentadoria.")
    if not _clean(analysis.get("resumo_processual")):
        _error("A análise de aposentadoria não contém o relatório da instrução.")
    ids = _source_ids(context)
    questoes = analysis.get("questoes")
    if not isinstance(questoes, list):
        _error("A análise de aposentadoria não organizou as questões jurídicas.")
    if analysis.get("sem_irregularidades_identificadas") not in (True, False):
        _error("A análise de aposentadoria não indicou se há apontamento.")
    normalized = {
        "resumo_processual": _clean(analysis.get("resumo_processual")),
        "questoes": [_questao(item, ids) for item in questoes],
        "dimensoes": _dimensoes(analysis.get("dimensoes")),
        "sem_irregularidades_identificadas": bool(analysis.get("sem_irregularidades_identificadas")),
        "pontos_atencao_gerais": [
            _clean(item) for item in analysis.get("pontos_atencao_gerais") or [] if _clean(item)
        ],
        "beneficiario": _person_value(analysis.get("beneficiario")),
        "ato": _clean(analysis.get("ato"))[:180],
        "fase": "",
    }
    normalized["fase"] = normalized["dimensoes"]["fase"]
    if not normalized["beneficiario"]:
        normalized["beneficiario"] = normalized["dimensoes"]["beneficiario"]
    return normalized


def reconcile_analysis(analysis, context):
    """Ajusta o encaminhamento sem converter lacuna em ilegalidade nem sanar sem prova."""
    current = deepcopy(analysis)
    warnings = []
    dims = current["dimensoes"]
    source = _plain(_source_text(context))
    questions = current["questoes"]
    legal = dims["legalidade"]

    def to_diligence(reason):
        nonlocal legal
        legal = "DILIGENCIA"
        if reason not in warnings:
            warnings.append(reason)

    unresolved = [
        item for item in questions
        if item["impeditiva"] and item["saneamento"] != "COMPROVADO" and not item["ilicitude_material"]
    ]
    if unresolved and legal in {"LEGAL", "ILEGAL", "REVISAO_LEGAL"}:
        to_diligence(
            "Questão impeditiva sem saneamento comprovado permanece em diligência. "
            "A ausência de documento não foi convertida em ilegalidade nem em registro."
        )
    material = [item for item in questions if item["ilicitude_material"] and item["saneamento"] != "COMPROVADO"]
    if material and legal in {"LEGAL", "REVISAO_LEGAL"}:
        to_diligence(
            "Há ilícito material indicado na análise e o registro não foi mantido "
            "sem que a própria análise o tenha excluído."
        )
    for item in questions:
        if item["tema"] == "DUPLICIDADE_TEMPO" and item["duplicidade_com_suporte"] is False and legal == "ILEGAL":
            to_diligence(
                "Contagem duplicada de tempo não foi declarada sem suporte documental suficiente."
            )
        if item["tema"] == "REENQUADRAMENTO" and item["manifestacao_beneficiario"] in {"AUSENTE", "NAO_IDENTIFICADA"}:
            to_diligence(
                "A alteração do fundamento do benefício depende de manifestação do beneficiário."
            )
        if item["alteracao_constitucional"] == "EXIGE_EXAME" and legal in {"LEGAL", "ILEGAL", "REVISAO_LEGAL"}:
            to_diligence(
                "Alteração constitucional superveniente ainda exige exame de vigência e alcance. "
                "Não houve presunção de retroatividade nem de irretroatividade."
            )
        if item["tema"] == "ACUMULACAO" and item["cancelamento_comprovado"] == "NAO":
            to_diligence(
                "Termo de opção ou notícia de acumulação, sem cancelamento comprovado, "
                "não encerra o registro nem a negativa."
            )
        if item["tema"] == "RETIFICACAO" and item["retificacao_publicada"] == "NAO":
            to_diligence(
                "A retificação do ato ainda não tem publicação comprovada."
            )
        if item["tema"] in {"MAGISTERIO", "PROGRESSAO", "PROVENTOS", "CTC"} and item["saneamento"] == "INSUFICIENTE":
            if legal in {"LEGAL", "REVISAO_LEGAL"}:
                to_diligence(
                    "Documento ou cálculo relacionado ao requisito questionado não foi tratado como saneamento."
                )
    if dims["fase"] == "APRECIACAO_INICIAL" and dims["cumprimento"] != "NAO_APLICAVEL":
        decision = _plain(dims["decisao"])
        if not decision or decision not in source:
            dims["cumprimento"] = "NAO_APLICAVEL"
            warnings.append(
                "Apreciação inicial sem decisão anterior identificada na instrução. "
                "Não foi declarado cumprimento."
            )
    if dims["cumprimento"] != "NAO_APLICAVEL":
        decision = _plain(dims["decisao"])
        if decision and decision not in source:
            warnings.append(
                "A decisão indicada na análise não foi localizada na instrução. "
                "O dispositivo não inventará a identificação."
            )
            dims["decisao"] = ""
    if dims["responsabilizacao"] == "SANCAO_CABIVEL":
        responsible = _plain(dims["responsavel_sancao"])
        if not responsible or responsible not in source:
            dims["responsabilizacao"] = "REVISAO_HUMANA"
            warnings.append(
                "Sanção não foi proposta sem responsável individualizado na instrução."
            )
    if dims["fase"] == "REVISAO" and legal == "LEGAL":
        legal = "REVISAO_LEGAL"
        warnings.append("A revisão não foi tratada como concessão inicial.")
    dims["legalidade"] = legal
    current["fase"] = dims["fase"]
    return current, warnings


def _category(request):
    text = _plain(request)
    if "parecer previo" in text or "julgamento das contas" in text or "irregularidade das contas" in text:
        return "PCA"
    if "afastamento de multa" in text or "afastamento da multa" in text:
        return "AFASTAMENTO"
    if "nao cumprimento" in text:
        return "NAO_CUMPRIMENTO"
    if "cumprimento parcial" in text:
        return "PARCIAL"
    if "ainda nao" in text and "cumpr" in text:
        return "NAO_DEMONSTRADO"
    if "cumprimento" in text and "decis" in text:
        return "INTEGRAL"
    if re.search(r"\bmulta\b", text) and "sem aplicacao de multa" not in text and "sem multa" not in text:
        return "MULTA"
    if re.search(r"\bilegalidade\b", text) or "negativa do respectivo registro" in text or "negativa de registro" in text:
        return "ILEGAL"
    if "revis" in text and ("legalidade" in text or "registro" in text):
        return "REVISAO"
    if re.search(r"\blegalidade\b", text) and "registro" in text:
        return "LEGAL"
    if any(token in text for token in ("diligenc", "assinacao de prazo", "prazo para", "complementa", "intima", "notifica", "providenc")):
        return "DILIGENCIA"
    return "OUTRO"


def _contains(value, text):
    return bool(value) and _plain(value) in _plain(text)


def _canonical(category, request, dims):
    name = dims.get("beneficiario") or ""
    decision = dims.get("decisao") or ""
    responsible = dims.get("responsavel_cumprimento") or ""
    if category == "LEGAL" and _contains(name, request):
        return f"Pela legalidade do ato de aposentadoria de {name} e concessão do respectivo registro."
    if category == "ILEGAL" and _contains(name, request):
        return f"Pela ilegalidade do ato de aposentadoria de {name} e negativa do respectivo registro."
    if category == "REVISAO" and _contains(name, request):
        return (
            f"Pela legalidade do ato revisional de aposentadoria de {name} "
            "e concessão do registro, quando juridicamente cabível."
        )
    if category == "INTEGRAL" and _contains(decision, request):
        return f"Pela declaração de cumprimento da decisão {decision}, nos termos comprovados nos autos."
    if category == "PARCIAL" and _contains(decision, request):
        return (
            f"Pela declaração de cumprimento parcial da decisão {decision}, "
            "nos termos comprovados nos autos."
        )
    if category == "NAO_DEMONSTRADO" and _contains(decision, request):
        return f"Pela declaração de que o cumprimento da decisão {decision} ainda não foi demonstrado."
    if category == "NAO_CUMPRIMENTO" and _contains(decision, request) and _contains(responsible, request):
        sentence = (
            f"Pela declaração de não cumprimento da decisão {decision}, "
            f"por {responsible}, conforme os fundamentos expostos."
        )
        if "sem aplicacao de multa" in _plain(request) or "sem multa" in _plain(request):
            sentence = sentence[:-1] + ", sem aplicação de multa."
        return sentence
    return _clean(request)


def _keep_category(category, dims):
    legal = dims["legalidade"]
    compliance = dims["cumprimento"]
    sanction = dims["responsabilizacao"]
    if category == "PCA":
        return False, "Pedido próprio de julgamento de contas foi excluído do dispositivo de aposentadoria."
    if category == "AFASTAMENTO":
        return False, "Não foi criado pedido autônomo de afastamento de sanção."
    if category in {"INTEGRAL", "PARCIAL", "NAO_CUMPRIMENTO", "NAO_DEMONSTRADO"}:
        expected = {
            "INTEGRAL": "INTEGRAL",
            "PARCIAL": "PARCIAL",
            "NAO_CUMPRIMENTO": "NAO_CUMPRIDO",
            "NAO_DEMONSTRADO": "NAO_DEMONSTRADO",
        }[category]
        if compliance == "NAO_APLICAVEL":
            return False, "Não havia decisão anterior a declarar."
        if compliance != expected and not (compliance == "PARCIAL" and category == "INTEGRAL"):
            return False, "O pedido de cumprimento não corresponde à dimensão apurada."
        return True, ""
    if category == "MULTA":
        if sanction != "SANCAO_CABIVEL":
            return False, "A multa não foi aplicada automaticamente."
        return True, ""
    if category == "LEGAL":
        if legal == "DILIGENCIA":
            return False, "O registro não foi proposto enquanto a instrução ainda exige providência."
        if legal == "ILEGAL":
            return False, "Pedido de registro foi afastado porque a análise concluiu pela ilegalidade."
        if legal == "REVISAO_LEGAL":
            return True, ""
        return legal == "LEGAL", ""
    if category == "ILEGAL":
        if legal == "DILIGENCIA":
            return False, "A negativa de registro não substituiu a complementação instrutória."
        if legal != "ILEGAL":
            return False, "A negativa de registro não tinha suporte na dimensão de legalidade."
        return True, ""
    if category == "REVISAO":
        if legal == "DILIGENCIA":
            return False, "O registro da revisão não foi proposto antes da providência pendente."
        return legal == "REVISAO_LEGAL", "A revisão não foi convertida em registro sem essa conclusão."
    if category == "DILIGENCIA":
        return True, ""
    return True, ""


def _sanction_request_ok(request, dims, source):
    responsible = dims.get("responsavel_sancao") or ""
    successor = dims.get("gestor_sucessor") or ""
    if not _contains(responsible, request) or not _contains(responsible, source):
        return False
    if successor and _contains(successor, request) and not _contains(responsible, request):
        return False
    if not re.search(r"\bart\.?\s*\d+", request, re.IGNORECASE):
        return False
    return True


def normalize_structure(structure, target, context=None, analysis=None):
    from services.minutas_generation import (
        DEVICE_INTRO_HARMONY,
        DEVICE_INTRO_NEUTRAL,
        _normalized_report_sections,
        _sanitize_draft_structure,
    )
    if not isinstance(structure, dict):
        _error("A IA não retornou a minuta de aposentadoria.")
    normalized = _sanitize_draft_structure(structure)
    warnings = []
    raw_dims = (analysis or {}).get("dimensoes") or normalized.get("dimensoes")
    if not isinstance(raw_dims, dict):
        _error("A minuta de aposentadoria não está vinculada às dimensões jurídicas.")
    dims = _dimensoes(raw_dims)
    metadata, header_warnings = resolve_header(target, context or {}, analysis)
    warnings.extend(header_warnings)
    resolved = dict(target)
    resolved.update(metadata)
    if dims.get("beneficiario") and not resolved.get("beneficiario"):
        if _contains(dims["beneficiario"], _source_text(context or {})):
            resolved["beneficiario"] = dims["beneficiario"]
    normalized["identificacao_processual"] = "\n".join(header_lines(resolved))
    normalized["dimensoes"] = dims
    report, foundation = _normalized_report_sections(normalized)
    if not any("71" in block and "iii" in _plain(block) for block in foundation):
        foundation.append(_COMPETENCE)
    normalized["relatorio_paragrafos"] = report
    normalized["relatorio"] = "\n\n".join(report)
    normalized["fundamentacao_geral_paragrafos"] = foundation
    normalized["fundamentacao_geral"] = "\n\n".join(foundation)
    normalized["encerramento"] = "É o Parecer."

    source = _source_text(context or {})
    kept = []
    for request in normalized.get("pedidos") or ():
        text = _clean(request)
        if not text:
            continue
        category = _category(text)
        if category == "INTEGRAL" and dims["cumprimento"] == "PARCIAL":
            category = "PARCIAL"
        if category == "LEGAL" and dims["legalidade"] == "REVISAO_LEGAL":
            category = "REVISAO"
        allowed, reason = _keep_category(category, dims)
        if not allowed:
            if reason:
                warnings.append(reason)
            continue
        if category == "MULTA" and not _sanction_request_ok(text, dims, source):
            warnings.append(
                "Pedido de multa sem responsável individualizado na instrução, "
                "sem fundamento normativo no próprio pedido ou atribuído ao gestor sucessor foi excluído."
            )
            continue
        rewritten = _canonical(category, text, dims)
        if rewritten not in kept:
            kept.append(rewritten)
    kept.sort(key=lambda item: _ORDER.get(_category(item), 4))
    if not kept:
        _error(
            "O dispositivo de aposentadoria ficou sem providência sustentada pela instrução. "
            "Revise a análise antes de concluir."
        )
    normalized["pedidos"] = kept
    normalized["conclusao_pedidos"] = (
        DEVICE_INTRO_HARMONY if dims.get("harmonia_auditoria") else DEVICE_INTRO_NEUTRAL
    )
    responsibility = normalized.get("responsabilizacao_administrativa")
    if not isinstance(responsibility, dict):
        responsibility = {"texto": ""}
    if dims["responsabilizacao"] == "NAO_APLICAVEL":
        responsibility = {"texto": ""}
    elif dims["responsabilizacao"] != "SANCAO_CABIVEL" and not _clean(responsibility.get("texto")):
        responsibility = {"texto": ""}
    normalized["responsabilizacao_administrativa"] = {
        "titulo": "Responsabilização administrativa",
        "texto": _clean(responsibility.get("texto")),
    }
    normalized.pop("analise_sancionatoria", None)
    warnings.extend(_invention_warnings(normalized, context or {}, analysis or {}))
    return normalized, list(dict.fromkeys(warnings))


def _invention_warnings(structure, context, analysis):
    corpus = _plain("\n".join((
        _source_text(context),
        str(analysis),
        " ".join(str(item.get("trecho") or "") for item in (
            (context.get("referencias_juridicas_recuperadas") or [])
            if isinstance(context, dict) else []
        )),
    )))
    visible = "\n".join((
        str(structure.get("ementa") or ""),
        str(structure.get("fundamentacao_geral") or ""),
        str(structure.get("relatorio") or ""),
        "\n".join(str(item) for item in structure.get("pedidos") or ()),
    ))
    warnings = []
    for match in _STATUTE.finditer(visible):
        number = match.group(1)
        year = match.group(2) or ""
        token = number + (year[-2:] if year else "")
        if number == "71":
            continue
        if number not in corpus and token not in corpus and f"{number}/{year}" not in corpus:
            warnings.append(
                f"Norma numerada não localizada na instrução nem nos precedentes recuperados "
                f"({match.group(0).strip()}). Não a trate como comprovada."
            )
    return warnings


def validate_structure(structure):
    required = (
        "identificacao_processual", "ementa", "relatorio", "fundamentacao_geral",
        "conclusao_pedidos", "encerramento",
    )
    if not isinstance(structure, dict) or any(not _clean(structure.get(field)) for field in required):
        _error("A minuta de aposentadoria não contém as partes institucionais obrigatórias.")
    if _plain(structure.get("encerramento")) not in {"e o parecer.", "e o parecer"}:
        _error("O encerramento da minuta deve ser «É o Parecer.».")
    topics = structure.get("topicos_irregularidades")
    requests = structure.get("pedidos")
    if not isinstance(topics, list) or not topics or any(
        not isinstance(topic, dict) or not _clean(topic.get("titulo")) or not _clean(topic.get("texto"))
        for topic in topics
    ):
        _error("A minuta de aposentadoria não fundamentou as questões relevantes.")
    if not isinstance(requests, list) or not requests or any(not _clean(item) for item in requests):
        _error("A minuta de aposentadoria não contém pedidos sustentados.")
    header = _plain(structure.get("identificacao_processual"))
    if "exercicio das contas" in header or "prestacao de contas" in header or "responsavel:" in header:
        _error("O cabeçalho de aposentadoria não pode usar o modelo de PCA.")
    if "natureza: aposentadoria" not in header:
        _error("O cabeçalho deve identificar a natureza Aposentadoria.")
    dims = structure.get("dimensoes")
    if isinstance(dims, dict) and dims.get("legalidade") == "DILIGENCIA":
        if any(_category(item) in {"LEGAL", "ILEGAL", "REVISAO"} for item in requests):
            _error("Diligência não pode coexistir com conclusão definitiva de registro.")
    return structure


def render_markdown(structure):
    sections = [
        structure["identificacao_processual"].strip(),
        "## Ementa\n\n" + structure["ementa"].strip(),
        "## Relatório\n\n" + structure["relatorio"].strip(),
        "## Fundamentação\n\n" + structure["fundamentacao_geral"].strip(),
    ]
    for topic in structure.get("topicos_irregularidades") or ():
        sections.append("### " + topic["titulo"].strip() + "\n\n" + topic["texto"].strip())
    responsibility = structure.get("responsabilizacao_administrativa") or {}
    if _clean(responsibility.get("texto")):
        sections.append("### Responsabilização administrativa\n\n" + responsibility["texto"].strip())
    numbered = "\n".join(
        f"{index}. {request.strip()}" for index, request in enumerate(structure["pedidos"], start=1)
    )
    sections.extend((
        "## Conclusão e propostas de encaminhamento\n\n"
        + structure["conclusao_pedidos"].strip() + "\n\n" + numbered,
        structure["encerramento"].strip(),
    ))
    return "\n\n".join(sections)


def rag_shim(analysis):
    irregularities = []
    for question in analysis.get("questoes") or ():
        irregularities.append({
            "id": question.get("id"),
            "apontamento": question.get("apontamento"),
            "normas_mencionadas": question.get("normas") or [],
            "hipoteses_juridicas": question.get("hipoteses") or [],
            "argumentos_defesa": [],
        })
    if not irregularities and _clean(analysis.get("resumo_processual")):
        irregularities.append({
            "id": "materia",
            "apontamento": analysis.get("resumo_processual"),
            "normas_mencionadas": [],
            "hipoteses_juridicas": [],
            "argumentos_defesa": [],
        })
    return {"irregularidades": irregularities}


def recover_precedents(service, analysis, process_key):
    """Pareceres definitivos de outros processos, sem minuta não aprovada."""
    original = service.search

    def filtered(query, *args, **kwargs):
        method = original if callable(original) else original.search
        response = method(query, *args, **kwargs) or {}
        rows = []
        for row in response.get("resultados") or []:
            if not isinstance(row, dict):
                continue
            if row.get("parecer_definitivo") is False:
                continue
            if str(row.get("papel") or "parecer") != "parecer":
                continue
            if str(row.get("processo_chave") or "") == process_key:
                continue
            rows.append(row)
        return {**response, "resultados": rows}

    service.search = filtered
    try:
        return service._instruction_precedents(rag_shim(analysis), process_key)
    finally:
        service.search = original


def _bind(context, target):
    bound = deepcopy(context)
    process = dict(bound.get("processo_alvo") or {})
    process.update({
        "especialidade_id": SPECIALTY_ID,
        "classe": "ATOS_PESSOAL",
        "natureza": "Aposentadoria",
        "motor": MOTOR,
        "config_versao": CONFIG_VERSION,
        "subtipo": "",
    })
    process.pop("exercicio", None)
    for field in ("origem", "beneficiario", "ato"):
        if _clean(target.get(field)):
            process[field] = target[field]
    bound["processo_alvo"] = process
    bound["motor"] = MOTOR
    bound["orientacao_dispositivo"] = ORIENTATION
    return bound


def build_target(profile, processo, origem, beneficiario, ato):
    from services.minutas_generation import _canonical_process_key
    from services.minutas_procuradores import pessoa_do_perfil
    if not is_aposentadoria_profile(profile):
        _error("O motor de aposentadorias não está habilitado para esta especialidade.")
    key = _canonical_process_key(processo)
    if len(key) != 7:
        _error("Informe o processo TC no formato 00000/00 antes de analisar a instrução.")
    return {
        "id": f"instrucao-{key}",
        "numero_processo": f"{key[:5]}/{key[5:]}",
        "processo_chave": key,
        "origem": _clean(origem),
        "beneficiario": _person_value(beneficiario),
        "ato": _clean(ato)[:180],
        "procuradora": pessoa_do_perfil(profile).nome_exibicao,
        "procuradora_chave": pessoa_do_perfil(profile).chave,
        "especialidade_id": SPECIALTY_ID,
        "classe": "ATOS_PESSOAL",
        "perfil": "",
        "classificacao_estado": "CLASSIFICADO",
        "subtipo": "",
        "parecer_definitivo": False,
        "instrucao_local": True,
        "motor": MOTOR,
        "config_versao": CONFIG_VERSION,
    }


def analyze(service, documentos, *, processo, origem="", beneficiario="", ato=""):
    from services.minutas_generation import _serialized_size
    from services.minutas_instrucao import montar_contexto
    profile = service._instruction_profile()
    target = build_target(profile, processo, origem, beneficiario, ato)
    prepared = montar_contexto(
        documentos, processo=target["numero_processo"], exercicio=None, origem=origem,
    )
    context = _bind(prepared["contexto"], target)
    generated = service._analyze(context)
    if not isinstance(generated, dict):
        _error("A IA não retornou uma análise válida.")
    analysis = validate_analysis(generated.get("analise"), context)
    analysis, warnings = reconcile_analysis(analysis, context)
    metadata, header_warnings = resolve_header(target, context, analysis)
    target.update(metadata)
    precedents = recover_precedents(service, analysis, target["processo_chave"])
    metrics = {
        key: generated[key]
        for key in ("modelo", "duracao_ms", "tokens_entrada", "tokens_saida", "tokens_total")
        if generated.get(key) is not None
    }
    return {
        "analise": analysis,
        "pendencias_conferencia": [],
        "fontes_versao": prepared["versao"],
        "processo_alvo_id": target["id"],
        "processo_alvo_chave": target["processo_chave"],
        "procuradora_chave": target["procuradora_chave"],
        "fontes_alvo": prepared["fontes_alvo"],
        "referencias_historicas": len(precedents.get("referencias") or []),
        "referencias": precedents,
        "advertencias_instrucao": [*prepared["advertencias"], *warnings, *header_warnings],
        "consolidacao_contexto": prepared["omissoes"],
        "contexto_bytes": _serialized_size(prepared["contexto"]),
        "metadados_cabecalho": metadata,
        "motor": MOTOR,
        "config_versao": CONFIG_VERSION,
        **metrics,
    }


def generate(service, documentos, analyzed, *, processo, origem="", beneficiario="", ato="", use_rag=True, orientacao_juridica=None, contexto_orientacao=None, usuario=""):
    from services.minutas_instrucao import montar_contexto
    profile = service._instruction_profile()
    target = build_target(profile, processo, origem, beneficiario, ato)
    prepared = montar_contexto(
        documentos, processo=target["numero_processo"], exercicio=None, origem=origem,
    )
    if not isinstance(analyzed, dict) or analyzed.get("fontes_versao") != prepared["versao"]:
        _error("A instrução foi alterada. Analise novamente antes de gerar a minuta.")
    if analyzed.get("motor") not in (None, MOTOR):
        _error("A análise não pertence ao motor de aposentadorias.")
    orientacao = service._validar_orientacao_operacao(
        target, analyzed, orientacao_juridica, contexto_orientacao, usuario
    )
    analysis = validate_analysis(analyzed.get("analise"), prepared["contexto"])
    analysis, reconcile_warnings = reconcile_analysis(analysis, prepared["contexto"])
    context = _bind(prepared["contexto"], target)
    context["analise_estruturada"] = analysis
    from services.minutas_orientacao import aplicar_bloco, metadados_orientacao
    referencias, consulta = (
        service._enriquecer_referencias_instrucao(analyzed.get("referencias"), orientacao, target)
        if use_rag else (analyzed.get("referencias"), "")
    )
    aplicar_bloco(context, orientacao, consulta if use_rag else "")
    if use_rag:
        opinions, passages = service._instruction_reference_lists(referencias)
        passages = [
            item for item in passages
            if item.get("parecer_definitivo") is not False
        ]
        context["referencias_historicas"] = opinions
        context["referencias_juridicas_recuperadas"] = passages
        context["orientacao_referencias_juridicas"] = (
            "Manifestações históricas não vinculantes. Use somente o critério jurídico, "
            "nunca fatos, nomes, valores, períodos ou conclusões de outro processo. "
            "A ausência de precedente não impede a análise da instrução."
        )
    generated = service._generate(context)
    if not isinstance(generated, dict):
        _error("A IA não retornou uma minuta válida.")
    structure = validate_structure_draft(generated.get("estrutura"))
    structure, warnings = normalize_structure(structure, target, context, analysis)
    structure = validate_structure(structure)
    metadata, _header_warnings = resolve_header(target, context, analysis)
    target.update(metadata)
    structure["identificacao_processual"] = "\n".join(header_lines({**target, **metadata}))
    metrics = {
        key: generated[key]
        for key in ("modelo", "duracao_ms", "tokens_entrada", "tokens_saida", "tokens_total")
        if generated.get(key) is not None
    }
    return {
        "estrutura": structure,
        "texto": render_markdown(structure),
        "fontes_versao": prepared["versao"],
        "processo_alvo_id": target["id"],
        "processo_alvo_chave": target["processo_chave"],
        "procuradora_chave": target["procuradora_chave"],
        "metadados_cabecalho": metadata,
        "advertencias_qualidade": [*reconcile_warnings, *warnings],
        "advertencias_documentais": [],
        "motor": MOTOR,
        "config_versao": CONFIG_VERSION,
        "fontes_alvo": prepared["fontes_alvo"],
        "referencias_historicas": len(context.get("referencias_historicas") or []),
        "referencias_juridicas_enviadas": len(context.get("referencias_juridicas_recuperadas") or []),
        "orientacao_assessor": metadados_orientacao(orientacao, contexto_orientacao),
        **metrics,
    }


def validate_structure_draft(structure):
    """Aceita a redação bruta da IA antes da normalização determinística."""
    if not isinstance(structure, dict):
        _error("A IA não retornou a minuta de aposentadoria.")
    for field in ("ementa", "relatorio", "fundamentacao_geral"):
        if not _clean(structure.get(field)):
            _error("A IA não retornou todas as partes obrigatórias da minuta.")
    topics = structure.get("topicos_irregularidades")
    requests = structure.get("pedidos")
    if not isinstance(topics, list) or not isinstance(requests, list) or not requests:
        _error("A IA não retornou pedidos estruturados válidos.")
    return structure


def analysis_contract():
    source = {
        "type": "OBJECT",
        "properties": {
            "fonte_id": {"type": "STRING"},
            "pagina": {"type": "INTEGER"},
            "trecho_suporte": {"type": "STRING"},
        },
        "required": ["fonte_id", "pagina", "trecho_suporte"],
    }
    schema = {
        "type": "OBJECT",
        "properties": {
            "resumo_processual": {"type": "STRING"},
            "fase": {"type": "STRING", "enum": list(FASES)},
            "beneficiario": {"type": "STRING"},
            "ato": {"type": "STRING"},
            "sem_irregularidades_identificadas": {"type": "BOOLEAN"},
            "pontos_atencao_gerais": {"type": "ARRAY", "items": {"type": "STRING"}},
            "questoes": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "id": {"type": "STRING"},
                        "apontamento": {"type": "STRING"},
                        "tema": {"type": "STRING", "enum": list(TEMAS)},
                        "saneamento": {"type": "STRING", "enum": list(SANEAMENTO)},
                        "impeditiva": {"type": "BOOLEAN"},
                        "ilicitude_material": {"type": "BOOLEAN"},
                        "duplicidade_com_suporte": {"type": "BOOLEAN"},
                        "manifestacao_beneficiario": {"type": "STRING", "enum": list(MANIFESTACAO)},
                        "alteracao_constitucional": {"type": "STRING", "enum": list(ALTERACAO)},
                        "retificacao_publicada": {"type": "STRING", "enum": list(PROVA_TRINARIA)},
                        "cancelamento_comprovado": {"type": "STRING", "enum": list(PROVA_TRINARIA)},
                        "normas_mencionadas": {
                            "type": "ARRAY",
                            "items": {
                                "type": "OBJECT",
                                "properties": {
                                    "descricao": {"type": "STRING"},
                                    "origem": {"type": "STRING"},
                                },
                                "required": ["descricao", "origem"],
                            },
                        },
                        "hipoteses_juridicas": {"type": "ARRAY", "items": {"type": "STRING"}},
                        "fontes_saneamento": {"type": "ARRAY", "items": source},
                    },
                    "required": [
                        "id", "apontamento", "tema", "saneamento", "impeditiva",
                        "ilicitude_material", "manifestacao_beneficiario",
                        "alteracao_constitucional", "retificacao_publicada",
                        "cancelamento_comprovado", "normas_mencionadas",
                        "hipoteses_juridicas", "fontes_saneamento",
                    ],
                },
            },
            "dimensoes": {
                "type": "OBJECT",
                "properties": {
                    "fase": {"type": "STRING", "enum": list(FASES)},
                    "legalidade": {"type": "STRING", "enum": list(LEGALIDADE)},
                    "cumprimento": {"type": "STRING", "enum": list(CUMPRIMENTO)},
                    "responsabilizacao": {"type": "STRING", "enum": list(RESPONSABILIZACAO)},
                    "beneficiario": {"type": "STRING"},
                    "decisao": {"type": "STRING"},
                    "responsavel_cumprimento": {"type": "STRING"},
                    "responsavel_sancao": {"type": "STRING"},
                    "gestor_sucessor": {"type": "STRING"},
                    "destinatario_diligencia": {"type": "STRING"},
                    "providencia": {"type": "STRING"},
                    "harmonia_auditoria": {"type": "BOOLEAN"},
                },
                "required": [
                    "fase", "legalidade", "cumprimento", "responsabilizacao",
                    "beneficiario", "decisao", "responsavel_cumprimento",
                    "responsavel_sancao", "gestor_sucessor", "destinatario_diligencia",
                    "providencia", "harmonia_auditoria",
                ],
            },
        },
        "required": [
            "resumo_processual", "fase", "beneficiario", "ato", "questoes",
            "dimensoes", "sem_irregularidades_identificadas", "pontos_atencao_gerais",
        ],
    }
    return PROMPT_ANALISE, schema


def generation_contract():
    schema = {
        "type": "OBJECT",
        "properties": {
            "identificacao_processual": {"type": "STRING"},
            "ementa": {"type": "STRING"},
            "relatorio": {"type": "STRING"},
            "relatorio_paragrafos": {"type": "ARRAY", "items": {"type": "STRING"}},
            "fundamentacao_geral": {"type": "STRING"},
            "fundamentacao_geral_paragrafos": {"type": "ARRAY", "items": {"type": "STRING"}},
            "topicos_irregularidades": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "titulo": {"type": "STRING"},
                        "texto": {"type": "STRING"},
                        "paragrafos": {"type": "ARRAY", "items": {"type": "STRING"}},
                    },
                    "required": ["titulo", "texto", "paragrafos"],
                },
            },
            "responsabilizacao_administrativa": {
                "type": "OBJECT",
                "properties": {
                    "titulo": {"type": "STRING"},
                    "texto": {"type": "STRING"},
                },
                "required": ["titulo", "texto"],
            },
            "conclusao_pedidos": {"type": "STRING"},
            "pedidos": {"type": "ARRAY", "items": {"type": "STRING"}},
            "encerramento": {"type": "STRING"},
        },
        "required": [
            "identificacao_processual", "ementa", "relatorio", "relatorio_paragrafos",
            "fundamentacao_geral", "fundamentacao_geral_paragrafos",
            "topicos_irregularidades", "responsabilizacao_administrativa",
            "conclusao_pedidos", "pedidos", "encerramento",
        ],
    }
    return PROMPT_GERACAO, schema


PROMPT_ANALISE = """Analise exclusivamente a instrução do processo-alvo de APOSENTADORIA.
Separe fato documentado, alegação da defesa, conclusão da Auditoria e hipótese jurídica.
Reconstrua, para cada questão relevante, a cadeia apontamento original, defesa, documentação apresentada, exame posterior da Auditoria, avaliação e consequência. Documento novo não sana a falha se não corresponder ao requisito questionado.
Não trate pedido de revisão de certidão como certidão retificada. Não declare contagem duplicada de tempo sem suporte documental; nesse caso use duplicidade_com_suporte=false.
Não presuma que diploma, certificado ou portaria resolve qualquer irregularidade de magistério, progressão, enquadramento ou proventos. Exija pertinência ao requisito e, na retificação, a publicação do ato.
Não considere categoria profissional automaticamente abrangida por aposentadoria especial. Não valide fundamentos incompatíveis. Não aplique a EC nº 103/2019 a todo ente sem examinar a regra efetivamente usada.
Na acumulação, distinga cargo, exceção constitucional, termo de opção e cancelamento efetivo. Opção não prova cancelamento. A EC nº 138/2025 exige exame de vigência, alcance e tempo; não presuma retroatividade nem afirme irretroatividade sem esse exame. Se o exame ainda faltar, use alteracao_constitucional=EXIGE_EXAME.
Não altere o fundamento do benefício só porque outra regra parece mais vantajosa. Se a mudança depender de ciência ou concordância do beneficiário e ela não estiver provada, use manifestacao_beneficiario=AUSENTE.
Revisão de aposentadoria não é concessão inicial. Identifique ato originário, registro anterior quando comprovado, objeto da revisão, ato revisional, mudança de fundamento e proventos, inclusive integralidade e paridade quando a instrução tratar disso.
As dimensões são independentes. Legalidade: LEGAL, ILEGAL, DILIGENCIA ou REVISAO_LEGAL. Cumprimento de decisão anterior: INTEGRAL, PARCIAL, NAO_CUMPRIDO, NAO_DEMONSTRADO ou NAO_APLICAVEL. Responsabilização: NAO_APLICAVEL, SEM_SANCAO, SANCAO_CABIVEL ou REVISAO_HUMANA.
Descumprimento não nega o registro por si. Registro pode coexistir com responsabilidade do gestor anterior. Regularização pelo sucessor não transfere a omissão anterior nem a afasta automaticamente. Não marque SANCAO_CABIVEL sem responsável, conduta, período e norma individualizados na instrução. Não aplique multa por presunção.
Falta de elemento relevante leva a DILIGENCIA, não a ILEGAL nem a LEGAL. ILEGAL somente com ilícito material demonstrado. Use ilicitude_material=true apenas nesse caso.
Registre a competência de registro do art. 71, inciso III, da Constituição Federal quando a instrução tratar de aposentadoria sujeita a registro. Não invente fatos, nomes, períodos, valores, normas, páginas ou conclusões. Ignore rodapé, assinatura e validação do TRAMITA como fonte do beneficiário. Campos sem suporte ficam vazios. Retorne somente JSON do schema."""

PROMPT_GERACAO = """Elabore minuta de parecer ministerial de APOSENTADORIA, em português do Brasil, somente com o JSON recebido.
A fonte factual exclusiva é documentos_processo_alvo. Precedentes históricos orientam critério jurídico e não fornecem fatos, nomes, valores, períodos ou conclusões do caso.
Estrutura: identificação sem número oficial de parecer; ementa; relatório da evolução processual; fundamentação; análise das questões; responsabilização administrativa somente quando houver questão sancionatória real; conclusão; pedidos; encerramento exatamente "É o Parecer.".
Não escreva a transição "É o Relatório. Passo a opinar.": o sistema a insere uma vez. Não copie transcrições longas. Não reproduza carimbo, assinatura ou metadado do TRAMITA.
Examine a competência de registro do art. 71, inciso III, da Constituição Federal e as normas do TCE-PB quando pertinentes. Cite Constituição, emendas, Constituição Estadual, Lei Orgânica e Regimento do TCE-PB, legislação previdenciária ou funcional e jurisprudência apenas quando constarem do contexto ou forem o próprio art. 71, III, como competência de registro. Não invente dispositivo, interpretação, precedente, requisito, período ou cálculo. Verifique vigência e tempo. Não aplique a EC nº 103/2019 automaticamente a todos os entes. Não presuma o efeito temporal da EC nº 138/2025.
O dispositivo não julga contas nem emite parecer prévio. Não use "Responsável" para o beneficiário. Não crie pedido de sanção, de afastamento de sanção ou de diligência para completar lista.
Ordene, quando cada um tiver suporte: cumprimento ou não cumprimento; sanção pessoal individualizada; legalidade e registro, ilegalidade e negativa, ou revisão; diligências. Se a dimensão estiver em DILIGENCIA, não conclua registro nem negativa. Se o cumprimento for NAO_APLICAVEL, não declare cumprimento. Se a responsabilização não for SANCAO_CABIVEL, não peça multa.
Preserve divergência fundamentada em relação à Auditoria. Use harmonia na introdução apenas quando a análise marcar harmonia_auditoria=true.
Fórmulas, só com os dados já presentes no contexto: legalidade com registro; ilegalidade com negativa; revisão com registro quando cabível; cumprimento integral, parcial ou não demonstrado; não cumprimento pelo responsável individualizado; prazo para providência concreta a cargo do destinatário identificado.
Retorne somente JSON do schema."""
