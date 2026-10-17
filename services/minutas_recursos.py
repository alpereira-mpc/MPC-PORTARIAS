"""Motor recursos-v1 para Apelação e Recurso Ordinário da Procuradora Elvira."""

from copy import deepcopy
import re
import unicodedata

from services.minutas_specialties import (
    INSTRUCTION_READY_STATES,
    PROSECUTOR_KEY,
    RECURSOS,
    RECURSOS_MOTOR,
)


MOTOR = RECURSOS_MOTOR
CONFIG_VERSION = "1"
SPECIALTY_ID = RECURSOS
MODALIDADES = {
    "APELACAO": "Recurso de Apelação",
    "ORDINARIO": "Recurso Ordinário",
}
CONCLUSOES = (
    "NAO_CONHECIMENTO", "PROVIMENTO", "PROVIMENTO_PARCIAL",
    "DESPROVIMENTO", "PENDENTE",
)
REQUISITOS = ("cabimento", "tempestividade", "legitimidade", "interesse", "demais_requisitos")


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _plain(value):
    return "".join(
        char for char in unicodedata.normalize("NFKD", str(value or "").casefold())
        if not unicodedata.combining(char)
    )


def _error(message):
    from services.minutas_generation import MinutasGenerationError
    raise MinutasGenerationError(message)


def is_recurso_profile(profile):
    from services.minutas_procuradores import chave_conhecida

    return (
        isinstance(profile, dict)
        and str(profile.get("id") or "") == SPECIALTY_ID
        and str(profile.get("motor") or "") == MOTOR
        and str(profile.get("config_versao") or "") == CONFIG_VERSION
        and chave_conhecida(str(profile.get("procuradora_chave") or ""))
        and str(profile.get("estado") or "") in INSTRUCTION_READY_STATES
    )


def _modalidade(value):
    key = str(value or "").strip().upper()
    if key not in MODALIDADES:
        _error("Selecione Recurso de Apelação ou Recurso Ordinário.")
    return key


def build_target(profile, processo, origem, modalidade, materia_originaria="", data_decisao=""):
    from services.minutas_generation import _canonical_process_key
    from services.minutas_procuradores import pessoa_do_perfil
    if not is_recurso_profile(profile):
        _error("O motor de recursos não está habilitado para esta especialidade.")
    key = _canonical_process_key(processo)
    if len(key) != 7:
        _error("Informe o processo TC no formato 00000/00 antes de analisar a instrução.")
    modalidade = _modalidade(modalidade)
    pessoa = pessoa_do_perfil(profile)
    return {
        "id": f"instrucao-{key}", "numero_processo": f"{key[:5]}/{key[5:]}",
        "processo_chave": key, "origem": _clean(origem),
        "modalidade_recurso": modalidade,
        "modalidade_rotulo": MODALIDADES[modalidade],
        "materia_originaria": _clean(materia_originaria)[:240],
        "data_decisao": _clean(data_decisao)[:20],
        "procuradora": pessoa.nome_exibicao,
        "procuradora_chave": pessoa.chave,
        "especialidade_id": SPECIALTY_ID, "classe": "RECURSO", "perfil": "",
        "classificacao_estado": "CLASSIFICADO", "subtipo": "",
        "parecer_definitivo": False, "instrucao_local": True,
        "motor": MOTOR, "config_versao": CONFIG_VERSION,
    }


def header_lines(target):
    lines = [
        f"Processo TC nº {target['numero_processo']}",
        f"Natureza: {target['modalidade_rotulo']}",
    ]
    if _clean(target.get("materia_originaria")):
        lines.append(f"Matéria originária: {target['materia_originaria']}")
    if _clean(target.get("data_decisao")):
        lines.append(f"Data da decisão recorrida: {target['data_decisao']}")
    if _clean(target.get("origem")):
        lines.append(f"Origem: {target['origem']}")
    return lines


def _bind(context, target):
    bound = deepcopy(context)
    process = dict(bound.get("processo_alvo") or {})
    process.update({
        "especialidade_id": SPECIALTY_ID, "classe": "RECURSO",
        "natureza": target["modalidade_rotulo"], "modalidade_recurso": target["modalidade_recurso"],
        "materia_originaria": target["materia_originaria"], "data_decisao": target["data_decisao"],
        "motor": MOTOR, "config_versao": CONFIG_VERSION, "subtipo": "",
    })
    process.pop("exercicio", None)
    if target.get("origem"):
        process["origem"] = target["origem"]
    bound["processo_alvo"] = process
    bound["motor"] = MOTOR
    bound["orientacao_dispositivo"] = "RECURSO_ADMISSIBILIDADE_MERITO_CONDICIONAL"
    return bound


def _source_ids(context):
    return {
        str(document.get("fonte_id") or "")
        for document in (context or {}).get("documentos_processo_alvo") or ()
        if isinstance(document, dict) and document.get("fonte_id")
    }


def _validate_evidence(items, source_ids):
    if not isinstance(items, list):
        _error("A análise recursal não separou corretamente as fontes documentais.")
    for item in items:
        if not isinstance(item, dict) or str(item.get("fonte_id") or "") not in source_ids:
            _error("A análise recursal contém fonte alheia ao processo-alvo.")
        if not _clean(item.get("trecho")):
            _error("A análise recursal contém fonte sem trecho verificável.")


def validate_analysis(analysis, context, modalidade):
    if not isinstance(analysis, dict) or not _clean(analysis.get("resumo_processual")):
        _error("A análise recursal não contém resumo processual válido.")
    if analysis.get("modalidade") != _modalidade(modalidade):
        _error("A análise não corresponde à modalidade recursal selecionada.")
    admissibilidade = analysis.get("admissibilidade")
    if not isinstance(admissibilidade, dict):
        _error("A análise recursal não contém exame de admissibilidade.")
    for field in REQUISITOS:
        item = admissibilidade.get(field)
        if not isinstance(item, dict) or not _clean(item.get("conclusao")):
            _error(f"A análise recursal não examinou o requisito: {field}.")
    if not _clean(admissibilidade.get("regime_temporal")):
        _error("A análise recursal não examinou o regime temporal aplicável.")
    decision = analysis.get("decisao_recorrida")
    if not isinstance(decision, dict) or not _clean(decision.get("fundamentos")):
        _error("A decisão recorrida e seus fundamentos não foram identificados.")
    source_ids = _source_ids(context)
    _validate_evidence(decision.get("fontes"), source_ids)
    arguments = analysis.get("razoes_recursais")
    if not isinstance(arguments, list) or not arguments:
        _error("A análise recursal não confrontou as razões do recurso.")
    for argument in arguments:
        if not isinstance(argument, dict) or not _clean(argument.get("argumento")):
            _error("Razão recursal sem identificação válida.")
        if not _clean(argument.get("confronto")) or not _clean(argument.get("conclusao")):
            _error("Razão recursal sem confronto individual e conclusão.")
        _validate_evidence(argument.get("fontes"), source_ids)
    if analysis.get("conclusao") not in CONCLUSOES:
        _error("A análise recursal não informou conclusão juridicamente válida.")
    for field in ("manifestacao_auditoria", "questoes_processuais", "nulidades", "lacunas_documentais"):
        if not isinstance(analysis.get(field), list):
            _error(f"A análise recursal não estruturou o campo: {field}.")
    return analysis


def _rag_shim(analysis):
    return {"irregularidades": [{
        "id": item.get("id") or f"ARG-{index}",
        "apontamento": item.get("argumento") or item.get("conclusao"),
        "normas_mencionadas": analysis.get("fontes_normativas") or [],
        "hipoteses_juridicas": [item.get("confronto") or ""],
        "argumentos_defesa": [item.get("argumento") or ""],
    } for index, item in enumerate(analysis.get("razoes_recursais") or (), 1)]}


def recover_precedents(service, analysis, process_key):
    """Prioriza Recursos e permanece no acervo do Procurador da operação."""
    primary = service._instruction_precedents(_rag_shim(analysis), process_key)
    rows = list(primary.get("referencias") or ())
    if len(rows) >= 6:
        return primary
    try:
        from database.minutas_referencias import MinutasReferenciasStore
        from services.minutas_library import MinutasLibraryService
        from services.minutas_search import MinutasSearchService
        from services.minutas_specialties import CATALOG
        library = service.library
        repository = library.repository
        if not isinstance(repository, MinutasReferenciasStore):
            return primary
        queries = primary.get("consultas") or ()
        query = next((_clean(item.get("consulta_principal")) for item in queries if _clean(item.get("consulta_principal"))), "")
        if not query:
            return primary
        seen = {str(item.get("referencia_id") or "") for item in rows}
        for profile in CATALOG:
            specialty_id = profile["id"]
            if specialty_id == SPECIALTY_ID or len(rows) >= 6:
                continue
            scoped_repository = MinutasReferenciasStore(
                repository.store, specialty_id=specialty_id, include_acervo=True,
                procuradora_chave=library.procurador.chave,
            )
            scoped_library = MinutasLibraryService(
                scoped_repository, library.storage, library.identity, library.secrets,
                procurador=library.procurador,
                principal=getattr(library, "principal", None),
            )
            response = MinutasSearchService(scoped_library).search(
                query, page_size=min(3, 6 - len(rows)), exclude_process_key=process_key,
            )
            for item in response.get("resultados") or ():
                identifier = str(item.get("referencia_id") or "")
                if not identifier or identifier in seen:
                    continue
                seen.add(identifier)
                rows.append({
                    "referencia_id": identifier,
                    "processo": item.get("processo") or "",
                    "documento": item.get("documento") or "Parecer definitivo",
                    "pagina": item.get("pagina"),
                    "trecho": _clean(item.get("trecho"))[:1800],
                    "especialidade_id": specialty_id,
                    "contextual": True,
                })
                if len(rows) >= 6:
                    break
    except Exception as exc:
        # A indisponibilidade contextual não elimina resultados seguros de Recursos.
        result = dict(primary)
        result["pesquisa_contextual"] = {
            "estado": "INDISPONIVEL",
            "motivo": (_clean(exc) or "Pesquisa contextual indisponível.")[:180],
        }
        return result
    result = dict(primary)
    result["referencias"] = rows
    result["estado"] = "RECUPERADO" if rows else primary.get("estado", "SEM_RESULTADO")
    contextual = sum(bool(item.get("contextual")) for item in rows)
    result["pesquisa_contextual"] = {
        "estado": "RECUPERADO" if contextual else "SEM_RESULTADO",
        "quantidade": contextual,
    }
    return result


def _structure_required(structure):
    required = ("ementa", "relatorio", "admissibilidade", "fundamentacao_geral", "conclusao_pedidos", "encerramento")
    if not isinstance(structure, dict) or any(not _clean(structure.get(field)) for field in required):
        _error("A minuta recursal não contém relatório, admissibilidade, mérito e conclusão.")
    if not isinstance(structure.get("topicos_irregularidades"), list) or not structure["topicos_irregularidades"]:
        _error("A minuta recursal não contém fundamentação individual dos argumentos.")
    if not isinstance(structure.get("pedidos"), list) or not structure["pedidos"]:
        _error("A minuta recursal não contém conclusão estruturada.")
    return structure


def normalize_structure(structure, target, context, analysis):
    normalized = _structure_required(deepcopy(structure))
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
        _error("A minuta recursal não fundamentou os argumentos relevantes.")
    normalized["topicos_irregularidades"] = topics
    normalized["responsabilizacao_administrativa"] = {"titulo": "", "texto": ""}
    normalized["pedidos"] = list(dict.fromkeys(_clean(item) for item in normalized["pedidos"] if _clean(item)))
    if not normalized["pedidos"]:
        _error("A minuta recursal ficou sem conclusão para revisão humana.")
    return normalized, []


def render_markdown(structure):
    return "\n\n".join((
        structure["identificacao_processual"], "## Ementa\n\n" + structure["ementa"],
        "## Relatório\n\n" + structure["relatorio"],
        "## Admissibilidade\n\n" + structure["admissibilidade"],
        "## Mérito\n\n" + structure["fundamentacao_geral"],
        "## Conclusão\n\n" + structure["conclusao_pedidos"] + "\n\n"
        + "\n".join(f"{index}. {item}" for index, item in enumerate(structure["pedidos"], 1)),
        structure["encerramento"],
    ))


def analyze(service, documentos, *, processo, origem="", modalidade="", materia_originaria="", data_decisao=""):
    from services.minutas_generation import _serialized_size
    from services.minutas_instrucao import montar_contexto
    target = build_target(service._instruction_profile(), processo, origem, modalidade, materia_originaria, data_decisao)
    prepared = montar_contexto(documentos, processo=target["numero_processo"], exercicio=None, origem=origem)
    context = _bind(prepared["contexto"], target)
    generated = service._analyze(context)
    analysis = validate_analysis((generated or {}).get("analise"), context, target["modalidade_recurso"])
    precedents = recover_precedents(service, analysis, target["processo_chave"])
    metrics = {key: generated[key] for key in ("modelo", "duracao_ms", "tokens_entrada", "tokens_saida", "tokens_total") if isinstance(generated, dict) and generated.get(key) is not None}
    return {
        "analise": analysis, "fontes_versao": prepared["versao"],
        "processo_alvo_id": target["id"], "processo_alvo_chave": target["processo_chave"],
        "procuradora_chave": target["procuradora_chave"], "fontes_alvo": prepared["fontes_alvo"],
        "referencias": precedents, "referencias_historicas": len(precedents.get("referencias") or ()),
        "advertencias_instrucao": prepared["advertencias"], "consolidacao_contexto": prepared["omissoes"],
        "contexto_bytes": _serialized_size(prepared["contexto"]),
        "metadados_cabecalho": {
            "modalidade": target["modalidade_recurso"], "materia_originaria": target["materia_originaria"],
            "data_decisao": target["data_decisao"],
        },
        "motor": MOTOR, "config_versao": CONFIG_VERSION, **metrics,
    }


def generate(service, documentos, analyzed, *, processo, origem="", modalidade="", materia_originaria="", data_decisao="", use_rag=True):
    from services.minutas_generation import _serialized_size
    from services.minutas_instrucao import montar_contexto
    target = build_target(service._instruction_profile(), processo, origem, modalidade, materia_originaria, data_decisao)
    prepared = montar_contexto(documentos, processo=target["numero_processo"], exercicio=None, origem=origem)
    if not isinstance(analyzed, dict) or analyzed.get("fontes_versao") != prepared["versao"]:
        _error("A instrução foi alterada. Analise novamente antes de gerar a minuta.")
    if analyzed.get("motor") not in (None, MOTOR):
        _error("A análise não pertence ao motor de recursos.")
    metadata = analyzed.get("metadados_cabecalho") or {}
    if metadata.get("modalidade") != target["modalidade_recurso"]:
        _error("A modalidade recursal foi alterada. Analise novamente.")
    context = _bind(prepared["contexto"], target)
    analysis = validate_analysis(analyzed.get("analise"), context, target["modalidade_recurso"])
    context["analise_estruturada"] = analysis
    if use_rag:
        opinions, passages = service._instruction_reference_lists(analyzed.get("referencias"))
        context["referencias_historicas"] = opinions
        context["referencias_juridicas_recuperadas"] = passages
        context["orientacao_referencias_juridicas"] = (
            "Pareceres definitivos não vinculantes: aproveite apenas critérios jurídicos pertinentes; "
            "não copie texto nem transfira fatos, datas, pessoas, valores ou conclusões de outro processo."
        )
    if _serialized_size(context) > 3_500_000:
        _error("A instrução excede o limite seguro de contexto.")
    generated = service._generate(context)
    structure, warnings = normalize_structure((generated or {}).get("estrutura"), target, context, analysis)
    return {
        "estrutura": structure, "texto": render_markdown(structure), "fontes_versao": prepared["versao"],
        "processo_alvo_id": target["id"], "processo_alvo_chave": target["processo_chave"],
        "procuradora_chave": target["procuradora_chave"],
        "metadados_cabecalho": metadata, "advertencias_qualidade": warnings,
        "advertencias_documentais": [], "motor": MOTOR, "config_versao": CONFIG_VERSION,
        "fontes_alvo": prepared["fontes_alvo"],
        "referencias_historicas": len(context.get("referencias_historicas") or ()),
        "referencias_juridicas_enviadas": len(context.get("referencias_juridicas_recuperadas") or ()),
    }


def analysis_contract():
    evidence = {"type": "OBJECT", "properties": {"fonte_id": {"type": "STRING"}, "pagina": {"type": "INTEGER"}, "trecho": {"type": "STRING"}}, "required": ["fonte_id", "trecho"]}
    requirement = {"type": "OBJECT", "properties": {"conclusao": {"type": "STRING"}, "fundamentacao": {"type": "STRING"}, "fontes": {"type": "ARRAY", "items": evidence}}, "required": ["conclusao", "fundamentacao", "fontes"]}
    argument = {"type": "OBJECT", "properties": {"id": {"type": "STRING"}, "argumento": {"type": "STRING"}, "fundamento_decisao": {"type": "STRING"}, "provas": {"type": "ARRAY", "items": evidence}, "analise_auditoria": {"type": "STRING"}, "confronto": {"type": "STRING"}, "conclusao": {"type": "STRING"}, "fontes": {"type": "ARRAY", "items": evidence}}, "required": ["id", "argumento", "fundamento_decisao", "provas", "analise_auditoria", "confronto", "conclusao", "fontes"]}
    admissibility_properties = {field: requirement for field in REQUISITOS}
    admissibility_properties.update({"regime_temporal": {"type": "STRING"}, "conclusao": {"type": "STRING"}})
    schema = {"type": "OBJECT", "properties": {
        "resumo_processual": {"type": "STRING"}, "modalidade": {"type": "STRING", "enum": list(MODALIDADES)},
        "decisao_recorrida": {"type": "OBJECT", "properties": {"identificacao": {"type": "STRING"}, "data": {"type": "STRING"}, "orgao": {"type": "STRING"}, "fundamentos": {"type": "STRING"}, "fontes": {"type": "ARRAY", "items": evidence}}, "required": ["identificacao", "data", "orgao", "fundamentos", "fontes"]},
        "razoes_recursais": {"type": "ARRAY", "items": argument},
        "manifestacao_auditoria": {"type": "ARRAY", "items": {"type": "STRING"}},
        "admissibilidade": {"type": "OBJECT", "properties": admissibility_properties, "required": [*REQUISITOS, "regime_temporal", "conclusao"]},
        "questoes_processuais": {"type": "ARRAY", "items": {"type": "STRING"}},
        "nulidades": {"type": "ARRAY", "items": {"type": "STRING"}},
        "merito": {"type": "STRING"}, "conclusao": {"type": "STRING", "enum": list(CONCLUSOES)},
        "lacunas_documentais": {"type": "ARRAY", "items": {"type": "STRING"}},
        "fontes_normativas": {"type": "ARRAY", "items": {"type": "STRING"}},
    }, "required": ["resumo_processual", "modalidade", "decisao_recorrida", "razoes_recursais", "manifestacao_auditoria", "admissibilidade", "questoes_processuais", "nulidades", "merito", "conclusao", "lacunas_documentais", "fontes_normativas"]}
    return PROMPT_ANALISE, schema


def generation_contract():
    topic = {"type": "OBJECT", "properties": {"titulo": {"type": "STRING"}, "texto": {"type": "STRING"}, "paragrafos": {"type": "ARRAY", "items": {"type": "STRING"}}}, "required": ["titulo", "texto", "paragrafos"]}
    schema = {"type": "OBJECT", "properties": {
        "identificacao_processual": {"type": "STRING"}, "ementa": {"type": "STRING"},
        "relatorio": {"type": "STRING"}, "relatorio_paragrafos": {"type": "ARRAY", "items": {"type": "STRING"}},
        "admissibilidade": {"type": "STRING"}, "fundamentacao_geral": {"type": "STRING"},
        "fundamentacao_geral_paragrafos": {"type": "ARRAY", "items": {"type": "STRING"}},
        "topicos_irregularidades": {"type": "ARRAY", "items": topic},
        "conclusao_pedidos": {"type": "STRING"}, "pedidos": {"type": "ARRAY", "items": {"type": "STRING"}},
        "encerramento": {"type": "STRING"},
    }, "required": ["identificacao_processual", "ementa", "relatorio", "relatorio_paragrafos", "admissibilidade", "fundamentacao_geral", "fundamentacao_geral_paragrafos", "topicos_irregularidades", "conclusao_pedidos", "pedidos", "encerramento"]}
    return PROMPT_GERACAO, schema


PROMPT_ANALISE = """Analise exclusivamente a instrução do processo-alvo de RECURSO. A modalidade informada é vinculante: APELACAO significa recurso contra decisão definitiva de Câmara (RN-TC 07/2024, arts. 278 a 280); ORDINARIO significa recurso contra decisão definitiva originária do Tribunal Pleno (arts. 281 a 283). Identifique a decisão recorrida, razões recursais, manifestação da Auditoria e documentos relevantes. Examine separadamente cabimento, tempestividade, legitimidade, interesse e demais requisitos. Confronte cada argumento com os fundamentos da decisão, provas e análise técnica. Examine questões processuais e nulidades e conclua, sem automatismo, por não conhecimento, provimento, provimento parcial, desprovimento ou pendência. Considere a LC-PB 192/2024, a RN-TC 07/2024, suas alterações vigentes e regras de transição. Identifique a data da decisão e o regime temporal; nunca aplique automaticamente o regime atual a decisão anterior à vigência pertinente. Não invente fatos, datas, prazos, precedentes, páginas ou fundamentos. Registre lacunas e cite fonte e página quando disponível. Retorne somente JSON do schema."""
PROMPT_GERACAO = """Elabore minuta ministerial de RECURSO apenas a partir do JSON recebido. Estruture relatório, admissibilidade, mérito com confronto individual das razões e conclusão. Preserve a modalidade selecionada e a conclusão juridicamente sustentada. Fatos vêm exclusivamente do processo-alvo. Pareceres definitivos recuperados são não vinculantes: aproveite entendimentos sem copiar automaticamente textos e nunca transfira fatos, datas, pessoas, valores ou conclusões. Aplique LC-PB 192/2024, RN-TC 07/2024, alterações e transição apenas com suporte temporal identificado; sinalize incertezas e informações ausentes. Não invente precedentes, fontes ou páginas. Encerre exatamente com "É o Parecer." e mantenha a minuta editável e sujeita à revisão humana. Retorne somente JSON do schema."""
