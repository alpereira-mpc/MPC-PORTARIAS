"""Instrução processual local para gerar minuta. Não grava o acervo nem alimenta o RAG."""

from hashlib import sha256
import re
import uuid

from services.minutas_extraction import extract_content
from services.minutas_library import MinutasValidationError, _validate_file


TIPOS = (
    ("denuncia_inicial", "Denúncia Inicial", "complementar"),
    ("relatorio_inicial", "Relatório Inicial de Auditoria", "auditoria"),
    ("analise_defesa", "Relatório de Análise de Defesa", "auditoria"),
    ("complementacao", "Relatório de Complementação", "auditoria"),
    ("cota", "Cota Ministerial", "complementar"),
    ("despacho", "Despacho", "complementar"),
    ("manifestacao", "Manifestação ou Defesa", "complementar"),
    ("informacao", "Informação Técnica", "complementar"),
    ("decisao", "Decisão ou Acórdão", "complementar"),
    ("outros", "Outros documentos processuais", "complementar"),
)
TIPO_POR_CHAVE = {item[0]: item for item in TIPOS}
_SANADA = re.compile(r"\b(?:sanad[oa]s?|elidid[oa]s?|superad[oa]s?|regularizad[oa]s?)\b")
_PERMANECE = re.compile(r"\b(?:permanece|subsiste|mantid[oa]|nao\s+sanad)\b")
_NEGA = re.compile(r"\b(?:nao\s+se\s+confirma|nao\s+procede|improcedente|afastad[oa])\b")


class MinutasInstrucaoError(ValueError):
    pass


def tipo_info(key):
    found = TIPO_POR_CHAVE.get(key)
    if not found:
        raise MinutasInstrucaoError("Classificação documental inválida.")
    return {"chave": found[0], "rotulo": found[1], "papel": found[2]}


def _plain(value):
    import unicodedata
    return "".join(
        char for char in unicodedata.normalize("NFD", str(value or "").casefold())
        if not unicodedata.combining(char)
    )


def _finding_label(line):
    from services.minutas_generation import _FINDING_HEADING, _normalized_support
    normalized = _normalized_support(line)
    match = _FINDING_HEADING.match(normalized)
    if not match:
        return ""
    return match.group(1) or match.group(2) or normalized[:80]


def receber_pdf(name, content, tipo, *, data="", origem="", descricao=""):
    """Extrai um PDF da instrução. O arquivo não é enviado ao acervo."""
    info = tipo_info(tipo)
    file = _validate_file("parecer", (name, content))
    if file["mime"] != "application/pdf":
        raise MinutasValidationError("A instrução processual aceita apenas PDF.")
    extracted = extract_content(file["content"], file["mime"], info["papel"])
    units = []
    for unit in extracted.get("unidades") or []:
        text = re.sub(r"\s+", " ", str(unit.get("texto") or "")).strip()
        if not text:
            continue
        item = {"texto": text}
        if isinstance(unit.get("pagina"), int) and not isinstance(unit.get("pagina"), bool):
            item["pagina"] = unit["pagina"]
        units.append(item)
    if not units and extracted.get("texto_integral"):
        units.append({"texto": re.sub(r"\s+", " ", extracted["texto_integral"]).strip(), "pagina": 1})
    if not units:
        raise MinutasInstrucaoError("Não foi possível extrair texto deste PDF.")
    digest = sha256(file["content"]).hexdigest()
    identifier = str(uuid.uuid4())
    return {
        "id": identifier,
        "fonte_id": sha256(identifier.encode()).hexdigest()[:16],
        "nome": file["name"],
        "tipo": info["chave"],
        "rotulo": info["rotulo"] if info["chave"] != "outros" else (descricao.strip() or info["rotulo"]),
        "papel": info["papel"],
        "descricao": descricao.strip(),
        "data": str(data or "").strip(),
        "origem_documento": str(origem or "").strip(),
        "sha256": digest,
        "paginas": [unit["pagina"] for unit in units if "pagina" in unit],
        "conteudo": units,
        "status_extracao": extracted.get("status") or "",
    }


def ordenar(documentos):
    ordered = [dict(item) for item in documentos]
    for index, item in enumerate(ordered, 1):
        item["ordem"] = index
    return ordered


def versao(documentos):
    payload = [
        (item["id"], item["ordem"], item["tipo"], item.get("descricao") or "",
         item.get("data") or "", item.get("origem_documento") or "", item["sha256"])
        for item in ordenar(documentos)
    ]
    return sha256(repr(payload).encode()).hexdigest()


def fontes(documentos):
    """Coleção no formato que o motor de geração já consome."""
    sources = []
    for item in ordenar(documentos):
        sources.append({
            "fonte_id": item["fonte_id"],
            "processo": "",
            "papel": item["papel"],
            "tipo": item["rotulo"],
            "arquivo": item["nome"],
            "ordem_processual": item["ordem"],
            "etapa_processual": item["rotulo"],
            "data": item.get("data") or "",
            "origem_documento": item.get("origem_documento") or "",
            "sha256": item["sha256"],
            "conteudo": list(item["conteudo"]),
        })
    return sources


def _linhas(documento):
    found = []
    for unit in documento["conteudo"]:
        for raw in str(unit.get("texto") or "").split(". "):
            label = re.sub(r"\s+", " ", raw).strip()
            identifier = _finding_label(label)
            if identifier:
                found.append({
                    "identificador": identifier,
                    "texto": label[:400],
                    "pagina": unit.get("pagina"),
                    "fonte_id": documento["fonte_id"],
                    "arquivo": documento["nome"],
                    "ordem": documento.get("ordem") or 0,
                })
    return found


def conferencia(documentos):
    """Cruza a instrução sem tratar o documento mais recente como vencedor."""
    ordered = ordenar(documentos)
    warnings = []
    kinds = {item["tipo"] for item in ordered}
    if len(ordered) == 1:
        warnings.append(
            f"A instrução contém apenas {ordered[0]['rotulo']}. "
            "Não há outras etapas documentais para confrontar; nenhuma etapa ausente foi presumida."
        )
    elif "analise_defesa" not in kinds and "manifestacao" not in kinds:
        warnings.append("Não há manifestação de defesa nesta instrução. O contraditório não foi presumido.")
    dated = [item for item in ordered if item.get("data")]
    if len(dated) >= 2 and [item["id"] for item in sorted(dated, key=lambda item: item["data"])] != [item["id"] for item in dated]:
        warnings.append("A sequência escolhida não segue as datas informadas. A geração usará a ordem definida por você.")
    positions = {}
    for item in ordered:
        for line in _linhas(item):
            positions.setdefault(line["identificador"], []).append(line)
    divergencias = []
    evolucao = []
    for identifier, lines in positions.items():
        texts = [_plain(line["texto"]) for line in lines]
        sanada = any(_SANADA.search(text) for text in texts)
        permanece = any(_PERMANECE.search(text) for text in texts)
        nega = any(_NEGA.search(text) for text in texts)
        if sanada and (permanece or any(not _SANADA.search(text) and not _NEGA.search(text) for text in texts)):
            evolucao.append({
                "identificador": identifier,
                "leitura": "Há registro posterior de saneamento e também o apontamento original. Nenhum dos dois prevalece automaticamente.",
                "posicoes": lines,
            })
        if nega and any(not _NEGA.search(text) for text in texts):
            divergencias.append({
                "identificador": identifier,
                "leitura": "Os documentos divergem sobre o apontamento. A divergência fica registrada, sem escolha automática.",
                "posicoes": lines,
            })
    return {"advertencias": warnings, "evolucao": evolucao, "divergencias": divergencias}


def consolidar(sources, budget):
    """Reduz o contexto sem descartar apontamento identificável."""
    from services.minutas_generation import _serialized_size
    payload = {"documentos_processo_alvo": sources}
    if _serialized_size(payload) <= budget:
        return sources, []
    condensed = []
    omissions = []
    for source in sources:
        kept = []
        omitted_pages = []
        for index, unit in enumerate(source["conteudo"]):
            text = unit.get("texto") or ""
            keep = index == 0 or bool(_finding_label(text)) or bool(
                _SANADA.search(_plain(text)) or _PERMANECE.search(_plain(text)) or _NEGA.search(_plain(text))
            )
            if keep:
                kept.append(unit)
            elif unit.get("pagina") is not None:
                omitted_pages.append(unit["pagina"])
        if omitted_pages:
            omissions.append({
                "arquivo": source["arquivo"],
                "paginas_nao_enviadas_integralmente": omitted_pages,
                "motivo": "Consolidação pelo limite de contexto. Apontamentos identificáveis foram preservados.",
            })
        condensed.append({**source, "conteudo": kept, "consolidado": True})
    if _serialized_size({"documentos_processo_alvo": condensed}) > budget:
        raise MinutasInstrucaoError(
            "O volume documental excede o contexto seguro mesmo após a consolidação dos apontamentos. "
            "Nenhum apontamento foi descartado em silêncio; retire ou separe documentos antes de gerar."
        )
    return condensed, omissions


def montar_contexto(documentos, *, processo="", exercicio=None, origem="", budget=None):
    if not documentos:
        raise MinutasInstrucaoError("Inclua ao menos um PDF de instrução.")
    from services.minutas_generation import MAX_CONTEXT_BYTES
    review = conferencia(documentos)
    sources, omissions = consolidar(fontes(documentos), budget or MAX_CONTEXT_BYTES)
    context = {
        "modo": "Instrução processual do processo em elaboração",
        "processo_alvo": {
            "numero": processo or "não informado na instrução",
            "exercicio": exercicio,
            "origem": origem,
            "parecer_definitivo_excluido": True,
            "instrucao_local": True,
        },
        "documentos_processo_alvo": sources,
        "sequencia_processual_verificada": [
            {"fonte_id": item["fonte_id"], "tipo": item["tipo"], "arquivo": item["arquivo"],
             "ordem": item["ordem_processual"]}
            for item in sources
        ],
        "conferencia_instrucao": review,
        "consolidacao_contexto": omissions,
        "orientacao_instrucao": (
            "A coleção está na ordem definida pelo Assessor. "
            "Não presuma que o documento mais recente corrige os anteriores. "
            "Associe cada fato ao documento e à página. "
            "Estes arquivos não são precedentes e não integram o acervo."
        ),
    }
    return {"contexto": context, "versao": versao(documentos), "fontes_alvo": len(sources),
            "advertencias": review["advertencias"], "omissoes": omissions}


_CADASTRO_BREAK = re.compile(
    r"(?i)\s+(?=(?:processo(?:\s+tc)?|subcategoria|jurisdicionado|origem|"
    r"entidade(?:\s+fiscalizada)?|respons[aá]vel(?:es)?|relator(?:es)?|"
    r"auditor(?:es)?(?:\s+de\s+contas)?|assunto|"
    r"exerc[ií]cio(?:\s+das\s+contas|\s+financeiro)?)\s*:)"
)


def _cadastro_text(document):
    raw = "\n".join(
        str(unit.get("texto") or "") for unit in (document or {}).get("conteudo") or ()
    )
    return _CADASTRO_BREAK.sub("\n", raw)


def sugerir_metadados_instrucao(documentos):
    """Lê só dados cadastrais já extraídos. Não escolhe valor quando há conflito."""
    from services.minutas_acervo import (
        display_process_key,
        identify_process_keys,
        parse_report_header,
        plain,
    )

    processes = {}
    years = set()
    origins = {}
    warnings = []
    for document in documentos or []:
        name = str(document.get("nome") or "")
        text = _cadastro_text(document)
        header = parse_report_header(text)
        name_keys, text_keys, year_set = identify_process_keys(name, text)
        header_key = str(header.get("processo_chave") or "")
        if header_key and name_keys and header_key not in name_keys:
            warnings.append(
                f"{name}: o cabeçalho e o nome do arquivo indicam processos diferentes."
            )
        elif header_key:
            processes[header_key] = display_process_key(header_key)
        elif len(name_keys) == 1:
            key = next(iter(name_keys))
            processes[key] = display_process_key(key)
            warnings.append(
                f"{name}: o processo foi lido do nome do arquivo. Confira o número."
            )
        elif len(name_keys) > 1:
            warnings.append(
                f"{name}: o nome do arquivo contém mais de um número de processo."
            )
        elif len(text_keys) == 1:
            key = next(iter(text_keys))
            processes[key] = display_process_key(key)
        year = header.get("exercicio")
        if year is None and len(year_set) == 1:
            year = next(iter(year_set))
        elif year is not None and year_set and year not in year_set:
            warnings.append(
                f"{name}: o exercício do cabeçalho não coincide com outras menções."
            )
            year = None
        if year is not None:
            years.add(int(year))
        origin = str(header.get("origem") or "").strip()
        if origin:
            origins.setdefault(plain(origin), origin)
    suggestion = {
        "processo": "",
        "exercicio": None,
        "origem": "",
        "advertencias": [],
    }
    if len(processes) == 1:
        suggestion["processo"] = next(iter(processes.values()))
    elif len(processes) > 1:
        warnings.append(
            "Os PDFs indicam processos distintos. Informe o processo manualmente."
        )
    if len(years) == 1:
        suggestion["exercicio"] = next(iter(years))
    elif len(years) > 1:
        warnings.append(
            "Os PDFs indicam exercícios distintos. Informe o exercício manualmente."
        )
    if len(origins) == 1:
        suggestion["origem"] = next(iter(origins.values()))
    elif len(origins) > 1:
        warnings.append(
            "Os PDFs indicam origens distintas. Informe a origem manualmente."
        )
    suggestion["advertencias"] = warnings
    return suggestion
