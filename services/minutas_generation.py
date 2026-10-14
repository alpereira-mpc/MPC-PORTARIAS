"""In-memory context selection and experimental Minutas IA generation."""

from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import logging
import re
from time import perf_counter
import unicodedata

from services import ai_service
from services.minutas_extraction import EXTRACTOR_VERSION, MinutasExtractionService
from services.minutas_library import MinutasValidationError, PROCURADORA
from services.minutas_search import MinutasSearchService


HISTORICAL_TEST_PROCESS_KEY = "0165326"
HISTORICAL_TEST_PROCESS_KEYS = frozenset(
    {HISTORICAL_TEST_PROCESS_KEY, "0272025", "0149926", "0265025"}
)
MAX_CONTEXT_BYTES = ai_service.MINUTA_MAX_CONTEXT_BYTES
MAX_HISTORICAL_REFERENCES = 3
MAX_RAG_QUERIES = 4
MAX_RAG_SEARCH_CALLS = 8
MAX_RAG_RESULTS_PER_QUERY = 4
MAX_RAG_PASSAGES = 6
MAX_RAG_PASSAGES_PER_OPINION = 2
MAX_RAG_PASSAGE_CHARS = 1600
RAG_RECOVERY_VERSION = "3b5b1-relevance-v2"
EVIDENCE_CORRECTION_VERSION = "3c3-assisted-evidence-v1"
VALIDATION_POLICY_VERSION = "4-documentary-warnings-v1"
LOGGER = logging.getLogger(__name__)
REPORT_TRANSITION = "É o relatório. Passa-se a opinar."
_VALIDATION_LINE_PATTERNS = (
    re.compile(r"^verifique\s+a\s+autenticidade\b.*$"),
    re.compile(r"^(?:codigo|código)\s+verificador\s*[:#-]\s*\S+.*$"),
    re.compile(r"^documento\s+assinado\s+(?:eletronicamente|digitalmente)\s+por\b.*$"),
    re.compile(r"^assinatura\s+(?:eletronica|eletrônica|digital)\s*[:#-]\s*\S+.*$"),
    re.compile(r"^(?:autenticacao|autenticação)\s+do\s+documento\s*[:#-]\s*\S+.*$"),
    re.compile(r"^documento\s+gerado\s+(?:pelo|no)\s+tramita\b.*$"),
    re.compile(r"^tramita\s*[-–—|:]\s*(?:sistema|validacao|validação|https?://)\b.*$"),
)
_STOPWORDS = {
    "a",
    "ao",
    "aos",
    "as",
    "com",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "e",
    "em",
    "na",
    "nas",
    "no",
    "nos",
    "o",
    "os",
    "para",
    "por",
    "que",
    "se",
    "um",
    "uma",
}
_RAG_GENERIC_TERMS = _STOPWORDS | {
    "ausencia",
    "aplicacao",
    "classificacao",
    "circunstancia",
    "circunstancias",
    "incorreta",
    "incorreto",
    "incidencia",
    "desacordo",
    "proveniente",
    "provenientes",
    "recurso",
    "recursos",
    "analise",
    "apontamento",
    "auditoria",
    "citada",
    "comprovacao",
    "conclusao",
    "contas",
    "defesa",
    "documentado",
    "documental",
    "fundamento",
    "hipotese",
    "irregularidade",
    "juridica",
    "ministerial",
    "obrigacao",
    "norma",
    "original",
    "outro",
    "pagina",
    "relatorio",
    "revisao",
    "sujeita",
    "processo",
    "principio",
    "pratica",
    "praticas",
    "distincao",
    "entre",
}
_RAG_CONCEPTS = (
    {
        "stems": ("receita", "intraorcament"),
        "primary": "receita intraorçamentária",
        "alternative": "contribuição intraorçamentária",
        "anchors": (("intraorcament",),),
    },
    {
        "stems": ("certific", "conselh"),
        "primary": "certificação conselho previdenciário",
        "alternative": "regularidade conselho previdenciário",
        "anchors": (
            ("certific", "conselh"),
            ("regularidade", "conselh"),
            ("credenci", "conselh"),
        ),
    },
    {
        "stems": ("compos", "conselh"),
        "primary": "composição conselho previdenciário",
        "alternative": "membros conselho previdenciário",
        "anchors": (
            ("compos", "conselh"),
            ("membro", "conselh"),
            ("represent", "conselh"),
        ),
    },
    {
        "stems": ("parcelament", "previdenci"),
        "primary": "parcelamento previdenciário",
        "alternative": "acordo dívida previdenciária",
        "anchors": (("parcelament",), ("acordo", "divid", "previdenci")),
    },
    {
        "stems": ("parcelament",),
        "primary": "parcelamento",
        "alternative": "acordo parcelamento",
        "anchors": (("parcelament",), ("acordo", "divid")),
    },
    {
        "stems": ("contribu", "previdenci"),
        "primary": "contribuição previdenciária",
        "alternative": "recolhimento previdenciário",
        "anchors": (("contribu", "previdenci"), ("recolh", "previdenci")),
    },
    {
        "stems": ("recolh", "previdenci"),
        "primary": "recolhimento previdenciário",
        "alternative": "contribuição previdenciária",
        "anchors": (("recolh", "previdenci"), ("contribu", "previdenci")),
    },
    {
        "stems": ("fundeb",),
        "primary": "FUNDEB",
        "alternative": "fundo educação básica",
        "anchors": (("fundeb",), ("fundo", "educacao", "basica")),
    },
    {
        "stems": ("licit",),
        "primary": "licitação",
        "alternative": "procedimento licitatório",
        "anchors": (("licit",),),
    },
    {
        "stems": ("contrat",),
        "primary": "contratação pública",
        "alternative": "contrato administrativo",
        "anchors": (("contrat",),),
    },
    {
        "stems": ("pessoal",),
        "primary": "despesa pessoal",
        "alternative": "limite pessoal",
        "anchors": (("pessoal",),),
    },
    {
        "stems": ("restos", "pagar"),
        "primary": "restos a pagar",
        "alternative": "obrigações financeiras",
        "anchors": (("restos", "pagar"),),
    },
    {
        "stems": ("controle", "intern"),
        "primary": "controle interno",
        "alternative": "sistema controle interno",
        "anchors": (("controle", "intern"),),
    },
    {
        "stems": ("transpar",),
        "primary": "transparência pública",
        "alternative": "publicidade administrativa",
        "anchors": (("transpar",),),
    },
)
_MINISTERIAL_TREATMENTS = {
    "MANTIDA_REJEICAO",
    "ACOLHIDA",
    "PARCIALMENTE_ACOLHIDA",
    "NAO_APLICAVEL",
}
_CURE_STATES = {"NAO_ALEGADO", "ALEGADO_NAO_COMPROVADO", "COMPROVADO"}
_SPECIFIC_LEGAL_DIVERGENCE = re.compile(
    r"\b(?:(?:art(?:igo)?\.?|lei|decreto|resolucao|sumula)\s*(?:n[.º°o]\s*)?\d+|"
    r"prescricao\s+(?:quinquenal|trienal|bienal)|competencia\s+(?:material|temporal))\b"
)
_SPECIFIC_PROBATIVE_DIVERGENCE = re.compile(
    r"\b(?:comprova|comprovou|demonstra|demonstrou|atesta|atestou|registra|registrou)\b"
    r".*\b(?:recolhimento|pagamento|calculo|prazo|data|valor)\b"
)
_CATEGORICAL_CURE = re.compile(
    r"\b(?:irregularidade|falha|vicio|pendencia)\w*\s+"
    r"(?:(?:foi|foram|esta|estao|restou|restaram|considera-se|consideram-se)\s+)?"
    r"(?:sanad[ao]s?|regularizad[ao]s?|corrigid[ao]s?|superad[ao]s?)\b"
)
_REPORT_TRANSITION_RE = re.compile(
    r"\b[eé]\s+o\s+relat[oó]rio\s*\.\s*"
    r"(?:passo\s+a|passa-se\s+a)\s+opinar\s*\.?",
    re.IGNORECASE,
)
_STANDALONE_FOLIO_RE = re.compile(
    r"^\s*(?:fls?|folhas?)\.?\s*(\d{1,7})"
    r"(?:\s*(?:/|a|at[eé]|[-–—])\s*(\d{1,7}))?\s*\.?\s*$",
    re.IGNORECASE,
)
_PRELIMINARY_OPINION_RE = re.compile(
    r"\b(?:emiss[aã]o\s+de\s+)?parecer(?:\s+pr[eé]vio)?\s+"
    r"(?:favor[aá]vel|contr[aá]rio)\b",
    re.IGNORECASE,
)
_ACCOUNT_OUTCOME_PATTERNS = (
    (
        "REGULARIDADE_COM_RESSALVAS",
        re.compile(
            r"\bregularidade\s+com\s+ressalvas\b"
            r"|\bcontas?\s+regulares?\s+com\s+ressalvas\b",
            re.IGNORECASE,
        ),
    ),
    (
        "IRREGULARIDADE",
        re.compile(
            r"\birregularidade\b|\bcontas?\s+irregulares?\b", re.IGNORECASE
        ),
    ),
    (
        "REGULARIDADE",
        re.compile(
            r"\bregularidade\b(?!\s+com\s+ressalvas)"
            r"|\bcontas?\s+regulares?\b(?!\s+com\s+ressalvas)",
            re.IGNORECASE,
        ),
    ),
)
_INVALID_BRL_RE = re.compile(
    r"R\$\s*\d{1,3}(?:\.\d{3})*\.\d{2}(?!\d)", re.IGNORECASE
)
_NO_HARM_CLAIM_RE = re.compile(
    r"\b(?:aus[eê]ncia|inexist[eê]ncia)\s+(?:de\s+)?(?:dano|preju[ií]zo)\b"
    r"|\bn[aã]o\s+(?:houve|h[aá])\s+(?:dano|preju[ií]zo)\b",
    re.IGNORECASE,
)


class MinutasGenerationError(MinutasValidationError):
    pass


def _plain(value):
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _clean_text(value):
    lines = []
    for raw_line in str(value or "").splitlines():
        normalized = re.sub(r"\s+", " ", _plain(raw_line)).strip()
        if any(pattern.fullmatch(normalized) for pattern in _VALIDATION_LINE_PATTERNS):
            continue
        lines.append(raw_line.rstrip())
    return "\n".join(lines).strip()


def _paragraph_blocks(structured, legacy):
    if isinstance(structured, list):
        return [
            block.strip()
            for item in structured
            for block in re.split(r"\n\s*\n", str(item or "").strip())
            if block.strip()
        ]
    return [
        block.strip()
        for block in re.split(r"\n\s*\n", str(legacy or "").strip())
        if block.strip()
    ]


def _without_report_transition(value):
    return re.sub(r"\s+", " ", _REPORT_TRANSITION_RE.sub(" ", str(value or ""))).strip()


def _normalized_report_sections(structure, process_sequence=None):
    report = [
        cleaned
        for block in _paragraph_blocks(
            structure.get("relatorio_paragrafos"), structure.get("relatorio")
        )
        if (cleaned := _without_report_transition(block))
    ]
    foundation = [
        cleaned
        for block in _paragraph_blocks(
            structure.get("fundamentacao_geral_paragrafos"),
            structure.get("fundamentacao_geral"),
        )
        if (cleaned := _without_report_transition(block))
    ]
    sequence = str(process_sequence or "").strip()
    if sequence and _normalized_support(sequence) not in {
        _normalized_support(block) for block in report
    }:
        report.append(sequence)
    report.append(REPORT_TRANSITION)
    return report, foundation


def _verified_source_folios(content):
    ranges = set()
    singles = set()
    paginated_units = []
    for unit in content or ():
        if not isinstance(unit, dict):
            continue
        if isinstance(unit.get("pagina"), int):
            paginated_units.append(unit)
        for raw_line in str(unit.get("texto") or "").splitlines():
            match = _STANDALONE_FOLIO_RE.fullmatch(raw_line.strip())
            if not match:
                continue
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else None
            if end is not None:
                if end < start:
                    return None
                ranges.add((start, end))
            else:
                singles.add(start)
    if len(ranges) == 1:
        start, end = next(iter(ranges))
        if any(not start <= item <= end for item in singles):
            return None
        return {
            "inicio": start,
            "fim": end,
            "citacao": f"fls. {start}/{end}",
            "criterio": "intervalo expresso no próprio documento",
        }
    if ranges:
        return None
    ordered = sorted(singles)
    if len(ordered) == 1:
        return {
            "inicio": ordered[0],
            "fim": None,
            "citacao": f"fl. {ordered[0]}",
            "criterio": "folha única expressa no próprio documento",
        }
    if (
        ordered
        and len(ordered) == len(paginated_units)
        and ordered == list(range(ordered[0], ordered[-1] + 1))
    ):
        return {
            "inicio": ordered[0],
            "fim": ordered[-1],
            "citacao": f"fls. {ordered[0]}/{ordered[-1]}",
            "criterio": "folhas expressas e contínuas em todas as páginas",
        }
    return None


def _process_sequence(context):
    sequence = []
    for source in context.get("documentos_processo_alvo", ()):
        item = {
            "fonte_id": source.get("fonte_id"),
            "tipo": source.get("tipo") or source.get("papel"),
            "arquivo": source.get("arquivo"),
        }
        if source.get("folhas_processuais"):
            item["folhas_processuais"] = dict(source["folhas_processuais"])
        sequence.append(item)
    return sequence


def _process_sequence_paragraph(context):
    entries = []
    for item in _process_sequence(context):
        label = str(item.get("tipo") or item.get("arquivo") or "Documento").strip()
        folios = item.get("folhas_processuais") or {}
        citation = str(folios.get("citacao") or "").strip()
        entries.append(f"{label} ({citation})" if citation else label)
    if not entries:
        return ""
    return "Integram a instrução processual: " + "; ".join(entries) + "."


def _account_outcomes(value):
    text = str(value or "")
    return {
        name for name, pattern in _ACCOUNT_OUTCOME_PATTERNS if pattern.search(text)
    }


def _canonical_process_key(value):
    normalized = re.sub(r"\s+", " ", _plain(value)).strip()
    normalized = re.sub(r"^processo\s*", "", normalized)
    normalized = re.sub(r"^tc\s*[-:]?\s*", "", normalized)
    if not re.fullmatch(r"\d{5}\s*/?\s*\d{2}", normalized):
        return ""
    return re.sub(r"\D", "", normalized)


def _is_historical_test_process(record):
    keys = {
        key
        for field in ("processo_chave", "numero_processo")
        if (key := _canonical_process_key(record.get(field)))
    }
    return len(keys) == 1 and keys.issubset(HISTORICAL_TEST_PROCESS_KEYS)


def _terms(value):
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", _plain(value))
        if token not in _STOPWORDS
    }


def _rag_query_plan(analysis):
    queries = []
    omitted = []
    seen = set()
    irregularities = analysis.get("irregularidades", []) if isinstance(analysis, dict) else []
    for irregularity in irregularities:
        if not isinstance(irregularity, dict):
            continue
        matter = str(irregularity.get("apontamento") or "").strip()
        identifier = str(irregularity.get("id") or "")
        norm_parts = [
            str(item.get("descricao") or "")
            for item in irregularity.get("normas_mencionadas", [])
            if isinstance(item, dict)
        ]
        hypothesis_parts = [
            str(item or "") for item in irregularity.get("hipoteses_juridicas", [])
        ]
        defense_parts = [
            str(item.get(field) or "")
            for item in irregularity.get("argumentos_defesa", [])
            if isinstance(item, dict)
            for field in ("alegacao", "comprovacao_documental")
        ]
        material_text = " ".join([matter] + norm_parts)
        normalized_text = _plain(material_text)
        material_tokens = {
            token
            for token in re.findall(r"[a-z0-9]+", normalized_text)
            if len(token) >= 4
            and token not in _RAG_GENERIC_TERMS
            and not token.isdigit()
        }
        concept_text = normalized_text
        if not material_tokens:
            concept_text += " " + _plain(" ".join(hypothesis_parts + defense_parts))
        primary = alternative = ""
        anchor_patterns = ()
        for concept in _RAG_CONCEPTS:
            if all(stem in concept_text for stem in concept["stems"]):
                primary = concept["primary"]
                alternative = concept["alternative"]
                anchor_patterns = concept["anchors"]
                break
        if not primary:
            tokens = []
            normalized_tokens = set()
            # O objeto material e as normas específicas têm precedência. Hipóteses
            # e defesa apenas completam uma consulta ainda sem assunto concreto.
            for part in [matter] + norm_parts + hypothesis_parts + defense_parts:
                for token in re.findall(r"[0-9A-Za-zÀ-ÿº°./-]+", part):
                    cleaned = token.strip("-./")
                    normalized = _plain(cleaned)
                    if (
                        len(normalized) < 4
                        or normalized in _RAG_GENERIC_TERMS
                        or normalized in normalized_tokens
                        or normalized.isdigit()
                    ):
                        continue
                    normalized_tokens.add(normalized)
                    tokens.append(cleaned)
                    if len(tokens) >= 3:
                        break
                if len(tokens) >= 3:
                    break
            primary = " ".join(tokens)
            alternative = " ".join(tokens[:2]) if len(tokens) > 2 else ""
            anchor_patterns = tuple((token,) for token in normalized_tokens)
        key = _normalized_support(primary)
        if not key:
            omitted.append(
                {"irregularidade_id": identifier, "materia": matter, "motivo": "sem termos técnicos"}
            )
            continue
        if key in seen:
            omitted.append(
                {"irregularidade_id": identifier, "materia": matter, "motivo": "consulta equivalente já selecionada"}
            )
            continue
        if len(queries) >= MAX_RAG_QUERIES:
            omitted.append(
                {"irregularidade_id": identifier, "materia": matter, "motivo": "limite de matérias pesquisadas"}
            )
            continue
        seen.add(key)
        if _normalized_support(alternative) == key:
            alternative = ""
        queries.append(
            {
                "irregularidade_id": identifier,
                "materia": matter or primary,
                "consulta": primary,
                "consulta_principal": primary,
                "consulta_alternativa": alternative,
                "ancoras_tematicas": anchor_patterns,
            }
        )
    return {"consultas": queries, "materias_nao_pesquisadas": omitted}


def _rag_passage_is_pertinent(query, passage):
    """Confere âncoras explícitas somente no trecho que será enviado ao Gemini."""
    normalized = _plain(passage)
    return any(
        pattern and all(stem in normalized for stem in pattern)
        for pattern in query.get("ancoras_tematicas", ())
    )


def _rag_queries(analysis):
    return _rag_query_plan(analysis)["consultas"]


_FINDING_HEADING = re.compile(
    r"^(?:(\d+(?:\.\d+)*)\s*[-–—.:)]?\s*)?"
    r"(?:irregularidade|apontamento|achado|constatacao)\b"
    r"(?:\s*(?:n[.oº°]*\s*)?(\d+(?:\.\d+)*))?"
)
_FINDING_GENERIC_TERMS = {
    "achado",
    "apontamento",
    "auditoria",
    "constatacao",
    "irregularidade",
    "original",
    "pagina",
}
_DEFENSE_ALLEGATION = re.compile(
    r"\b(?:a defesa|o gestor|o interessado|o responsavel)\s+"
    r"(?:aduziu|afirmou|alegou|argumentou|informou|sustentou)\b"
)
_DOCUMENTED_CONFIRMATION = re.compile(
    r"\b(?:a auditoria confirmou|documentos? comprovam|restou comprovad[oa])\b"
)
_PDF_LIGATURES = str.maketrans(
    {
        "ﬀ": "ff",
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
        "ﬅ": "st",
        "ﬆ": "st",
    }
)


def _normalized_support(value):
    return re.sub(r"\s+", " ", _plain(value)).strip()


def _evidence_words(value):
    text = unicodedata.normalize("NFKC", str(value or "")).translate(_PDF_LIGATURES)
    text = text.replace("\u00ad", "").replace("\u200b", "")
    text = re.sub(
        r"(?<=\w)[\-‐‑][^\S\r\n]*(?:\r\n|\r|\n)\s*(?=\w)",
        "",
        text,
    )
    return re.findall(r"[a-z0-9]+", _plain(text))


def _continuous_word_sequence(excerpt, page_text):
    excerpt_words = _evidence_words(excerpt)
    if (
        len(excerpt_words) < 2
        or sum(len(word) for word in excerpt_words) < 8
        or len(excerpt_words) > 100
    ):
        return False, "trecho ausente ou curto demais"
    page_words = _evidence_words(page_text)
    width = len(excerpt_words)
    verified = any(
        page_words[index : index + width] == excerpt_words
        for index in range(len(page_words) - width + 1)
    )
    return (
        (True, "")
        if verified
        else (False, "trecho não localizado na página indicada")
    )


def _literal_page_matches(excerpt, pages):
    matches = []
    for page, page_text in pages.items():
        verified, _reason = _continuous_word_sequence(excerpt, page_text)
        if verified:
            matches.append(page)
    return sorted(matches)


def _analysis_reference_records(analysis):
    records = []
    category_fields = (
        ("fatos_documentados", "Fato documentado", ("descricao",)),
        ("normas_mencionadas", "Norma mencionada", ("descricao", "origem")),
        (
            "argumentos_defesa",
            "Alegação defensiva",
            ("alegacao", "comprovacao_documental"),
        ),
    )
    for irregularity_index, irregularity in enumerate(
        analysis.get("irregularidades", ())
    ):
        identifier = str(irregularity.get("id") or "")
        for field, category, assertion_fields in category_fields:
            for item_index, item in enumerate(irregularity.get(field, ())):
                assertion = " ".join(
                    str(item.get(name) or "").strip() for name in assertion_fields
                ).strip()
                for source_index, reference in enumerate(item.get("fontes") or ()):
                    location = (
                        irregularity_index,
                        field,
                        item_index,
                        source_index,
                    )
                    records.append(
                        {
                            "irregularidade": identifier,
                            "categoria": category,
                            "afirmacao": assertion,
                            "referencia": reference,
                            "localizacao": location,
                            "chave": _reference_key(location, reference),
                        }
                    )
        audit = irregularity.get("avaliacao_auditoria") or {}
        for source_index, reference in enumerate(audit.get("fontes") or ()):
            location = (
                irregularity_index,
                "avaliacao_auditoria",
                0,
                source_index,
            )
            records.append(
                {
                    "irregularidade": identifier,
                    "categoria": "Conclusão da Auditoria",
                    "afirmacao": str(audit.get("descricao") or "").strip(),
                    "referencia": reference,
                    "localizacao": location,
                    "chave": _reference_key(location, reference),
                }
            )
    return records


def _reference_key(location, reference):
    payload = json.dumps(
        {
            "localizacao": location,
            "fonte_id": reference.get("fonte_id"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:24]


def _candidate_passages(page_text):
    pieces = [
        part.strip()
        for part in re.split(
            r"(?:\r?\n)+|(?<=[.!?;])\s+(?=[0-9A-ZÀ-Ý])",
            str(page_text or ""),
        )
        if part.strip()
    ]
    passages = []
    seen = set()
    for index, piece in enumerate(pieces):
        options = [piece]
        if index + 1 < len(pieces):
            options.append(f"{piece} {pieces[index + 1]}")
        for option in options:
            words = _evidence_words(option)
            normalized = " ".join(words)
            if (
                len(words) < 2
                or len(words) > 100
                or sum(len(word) for word in words) < 8
                or normalized in seen
            ):
                continue
            seen.add(normalized)
            passages.append(option)
    return passages


def _protected_assertion_tokens(value):
    normalized = _plain(value)
    tokens = set(re.findall(r"\br\s*\$\s*[\d.,]+|\b\d[\d./-]*\b", normalized))
    for pattern in (
        r"\bart(?:igo)?\.?\s*(?:n[.oº°]*\s*)?\d+[a-z-]*",
        r"\blei\s*(?:n[.oº°]*\s*)?\d+[\d./-]*",
        r"\b(?:decreto|resolucao|sumula)\s*(?:n[.oº°]*\s*)?\d+[\d./-]*",
    ):
        tokens.update(re.findall(pattern, normalized))
    return tokens


def _protected_assertion_names(value):
    ignored = {
        "A",
        "As",
        "Auditoria",
        "Conclusão",
        "Defesa",
        "O",
        "Os",
        "Relatório",
    }
    names = set()
    capitalized = (
        r"(?:[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ][a-záàâãéêíóôõúç]+|"
        r"[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ]{2,})"
    )
    for match in re.finditer(
        rf"\b{capitalized}(?:(?:\s+(?:d[aeo]s?|e))?\s+{capitalized})+\b",
        str(value or ""),
    ):
        words = match.group(0).strip().split()
        while words and words[0] in ignored:
            words.pop(0)
        substantive_words = [
            word
            for word in words
            if word.casefold() not in {"da", "de", "do", "das", "dos", "e"}
        ]
        if len(substantive_words) >= 2:
            names.add(_normalized_support(" ".join(words)))
    names.update(
        _normalized_support(match.group(0))
        for match in re.finditer(r"\b[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ]{3,}\b", str(value or ""))
    )
    return names


def _candidate_support_check(assertion, passage):
    assertion_normalized = _normalized_support(assertion)
    passage_normalized = _normalized_support(passage)
    protected = _protected_assertion_tokens(assertion)
    missing_protected = sorted(
        token for token in protected if token not in passage_normalized
    )
    if missing_protected:
        return False, "não preserva valores, datas ou referências normativas da afirmação"
    missing_names = sorted(
        name
        for name in _protected_assertion_names(assertion)
        if name not in passage_normalized
    )
    if missing_names:
        return False, "não preserva nomes ou entidades da afirmação"

    decisive_stems = {
        stem
        for stem in (
            "acolhid",
            "afastad",
            "ausencia",
            "comprovad",
            "inexist",
            "irregular",
            "mantid",
            "nao",
            "regulariz",
            "rejeitad",
            "sanad",
            "subsist",
            "superad",
            "procedent",
            "improcedent",
        )
        if stem in assertion_normalized
    }
    if any(stem not in passage_normalized for stem in decisive_stems):
        return False, "não preserva qualificadores decisivos da afirmação"

    assertion_terms = _terms(assertion) - _RAG_GENERIC_TERMS
    if not assertion_terms:
        assertion_terms = _terms(assertion) - _STOPWORDS
    passage_terms = _terms(passage)
    matched = assertion_terms & passage_terms
    required = 1 if len(assertion_terms) <= 2 else max(2, (len(assertion_terms) + 1) // 2)
    if len(matched) < required:
        return False, "não contém elementos objetivos suficientes da afirmação"
    return True, "elementos objetivos e qualificadores preservados"


def _evidence_candidates(assertion, excerpt, pages, *, limit=6):
    assertion_terms = _terms(assertion)
    excerpt_terms = _terms(excerpt)
    search_terms = (assertion_terms | excerpt_terms) - _STOPWORDS
    candidates = []
    for page, page_text in pages.items():
        for passage in _candidate_passages(page_text):
            passage_terms = _terms(passage)
            matched_assertion = assertion_terms & passage_terms
            matched_excerpt = excerpt_terms & passage_terms
            matched_search = search_terms & passage_terms
            if not matched_search:
                continue
            safe, reason = _candidate_support_check(assertion, passage)
            candidate_id = sha256(
                json.dumps(
                    {
                        "pagina": page,
                        "trecho": _normalized_support(passage),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()[:24]
            candidates.append(
                {
                    "id": candidate_id,
                    "pagina": page,
                    "trecho_literal": passage,
                    "termos_afirmacao": sorted(matched_assertion),
                    "substituicao_segura": safe,
                    "motivo_seguranca": reason,
                    "pontuacao": (
                        len(matched_assertion) * 4
                        + len(matched_excerpt) * 2
                        + len(matched_search)
                    ),
                }
            )
    candidates.sort(
        key=lambda item: (
            not item["substituicao_segura"],
            -item["pontuacao"],
            item["pagina"],
            item["id"],
        )
    )
    return candidates[:limit]


def _specific_divergence_ground(value):
    normalized = _normalized_support(value)
    return bool(
        normalized
        and (
            _SPECIFIC_LEGAL_DIVERGENCE.search(normalized)
            or _SPECIFIC_PROBATIVE_DIVERGENCE.search(normalized)
        )
    )


def _explicit_audit_findings(context):
    findings = []
    for source in context["documentos_processo_alvo"]:
        if source.get("papel") != "auditoria":
            continue
        for unit in source.get("conteudo", ()):
            for raw_line in str(unit.get("texto") or "").splitlines():
                label = re.sub(r"\s+", " ", raw_line).strip()
                normalized = _normalized_support(label)
                if not normalized or re.search(
                    r"\b(?:sem irregularidades|nao (?:foram )?"
                    r"(?:identificad|constatad|encontrad)\w* irregularidades?)\b",
                    normalized,
                ):
                    continue
                match = _FINDING_HEADING.match(normalized)
                if not match:
                    continue
                terms = _terms(normalized) - _FINDING_GENERIC_TERMS
                findings.append(
                    {
                        "identificador": match.group(1) or match.group(2),
                        "rotulo": label[:240],
                        "termos": terms,
                    }
                )
    return findings


def _source_text(row):
    try:
        units = json.loads(row.get("unidades_json") or "[]")
    except (TypeError, json.JSONDecodeError):
        units = []
    content = []
    for unit in units if isinstance(units, list) else []:
        if not isinstance(unit, dict):
            continue
        text = _clean_text(unit.get("texto"))
        if not text:
            continue
        entry = {"texto": text}
        if isinstance(unit.get("pagina"), int):
            entry["pagina"] = unit["pagina"]
        elif isinstance(unit.get("ordem"), int):
            entry["ordem"] = unit["ordem"]
        if unit.get("tipo") in {"paragrafo", "tabela"}:
            entry["tipo"] = unit["tipo"]
        content.append(entry)
    if not content:
        text = _clean_text(row.get("texto_integral"))
        if text:
            content.append({"texto": text})
    return content


def _serialized_size(value):
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


class MinutasGenerationService:
    """Build bounded contexts for the two explicit Minutas AI stages."""

    def __init__(self, library, analyze=None, generate=None, search=None):
        self.library = library
        self.extractor = MinutasExtractionService(library)
        self.search = search or MinutasSearchService(library)
        self._analyze = analyze or ai_service.analisar_fundamentos_minuta
        self._generate = generate or ai_service.gerar_minuta_parecer
        # A instância vive apenas durante um rerun do Streamlit. Este cache evita
        # repetir o mesmo inventário dentro da execução sem reutilizar dados entre
        # sessões ou impedir que o rerun seguinte enxergue fontes alteradas.
        self._records_cache = None
        self._source_state_cache = {}

    def _records(self):
        if self._records_cache is None:
            self._records_cache = tuple(self.library.list())
        return self._records_cache

    def _refresh_source_cache(self, target):
        """Força revisão atual das fontes antes de uma ação explícita."""
        self._records_cache = None
        self._source_state_cache.pop(str(target["id"]), None)

    def targets(self):
        return [
            record
            for record in self._records()
            if record.get("procuradora") == PROCURADORA
            and _is_historical_test_process(record)
        ]

    @staticmethod
    def _validate_target(target):
        if not _is_historical_test_process(target):
            raise MinutasGenerationError(
                "O processo selecionado não está habilitado para a validação cruzada."
            )
        if target.get("procuradora") != PROCURADORA:
            raise MinutasGenerationError("Procuradora não autorizada para este teste.")

    def _source_state(self, target):
        self._validate_target(target)
        cache_key = str(target["id"])
        if cache_key in self._source_state_cache:
            return self._source_state_cache[cache_key]
        started = perf_counter()
        records_list = [
            record
            for record in self._records()
            if record.get("procuradora_chave") == target["procuradora_chave"]
            and _is_historical_test_process(record)
        ]
        records = {str(record["id"]): record for record in records_list}
        inventory = self.library.repository.list_extraction_inventory_by_procuradora(
            target["procuradora_chave"]
        )
        by_path = {row["arquivo_path"]: row for row in inventory}
        target_opinion_hashes = {
            str(row.get("arquivo_sha256"))
            for row in inventory
            if str(row.get("referencia_id")) == str(target["id"])
            and row.get("papel") == "parecer"
            and row.get("arquivo_sha256")
        }
        required = [
            document
            for document in self.extractor._documents(target)
            if document["papel"] in {"auditoria", "complementar"}
        ]
        issues = []
        for document in required:
            extraction = by_path.get(document["path"])
            reason = ""
            if extraction is None:
                reason = "extração pendente"
            elif extraction.get("status") != "PROCESSADO":
                reason = "necessita atenção"
            elif extraction.get("extrator_versao") != EXTRACTOR_VERSION:
                reason = "extração desatualizada"
            elif not extraction.get("arquivo_sha256") or extraction.get(
                "arquivo_sha256"
            ) == "0" * 64:
                reason = "extração inválida"
            elif int(extraction.get("arquivo_tamanho") or -1) != int(
                document.get("tamanho") or 0
            ):
                reason = "documento alterado após a extração"
            if reason:
                issues.append(f"{document['nome']} ({reason})")

        historical_ids = {
            str(row["referencia_id"])
            for row in inventory
            if str(row["referencia_id"]) != str(target["id"])
            and row.get("papel") == "parecer"
            and row.get("status") == "PROCESSADO"
            and row.get("extrator_versao") == EXTRACTOR_VERSION
            and str(row.get("arquivo_sha256") or "") not in target_opinion_hashes
            and str(row["referencia_id"]) in records
            and self._valid_extraction_row(
                row, records[str(row["referencia_id"])]
            )
        }
        revision_payload = {
            "records": [
                (str(record["id"]), str(record.get("atualizado_em") or ""))
                for record in records_list
            ],
            "inventory": [
                (
                    str(row.get("referencia_id") or ""),
                    str(row.get("arquivo_path") or ""),
                    str(row.get("arquivo_sha256") or ""),
                    str(row.get("extrator_versao") or ""),
                    str(row.get("status") or ""),
                    str(row.get("atualizado_em") or ""),
                )
                for row in inventory
            ],
        }
        revision = sha256(
            json.dumps(revision_payload, sort_keys=True).encode("utf-8")
        ).hexdigest()
        state = {
            "records": records,
            "issues": issues,
            "fontes_alvo_previstas": len(required),
            "referencias_historicas_disponiveis": len(historical_ids),
            "fontes_versao": revision,
            "_parecer_alvo_hashes": target_opinion_hashes,
        }
        self._source_state_cache[cache_key] = state
        LOGGER.info(
            "MINUTAS_PERF etapa=inventario duracao_ms=%d registros=%d extracoes=%d",
            round((perf_counter() - started) * 1000),
            len(records_list),
            len(inventory),
        )
        return state

    def source_summary(self, target_reference_id):
        self.library._authorize()
        target = self.library.get(target_reference_id)
        state = self._source_state(target)
        return {
            key: value
            for key, value in state.items()
            if key != "records" and not key.startswith("_")
        }

    def _source(self, row, record):
        content = _source_text(row)
        if not content:
            return None
        source = {
            "fonte_id": sha256(
                (str(row.get("arquivo_sha256") or row["arquivo_path"])).encode()
            ).hexdigest()[:16],
            "processo": record["numero_processo"],
            "exercicio": int(record["exercicio"]),
            "subtipo": record["subtipo"],
            "papel": row["papel"],
            "tipo": row["tipo"],
            "arquivo": row["arquivo_nome"],
            "ordem_processual": int(row.get("posicao") or 0),
            "etapa_processual": row["tipo"],
            "conteudo": content,
        }
        folios = _verified_source_folios(content)
        if folios:
            source["folhas_processuais"] = folios
        return source

    def _valid_extraction_row(self, row, record):
        if str(row.get("referencia_id")) != str(record.get("id")):
            return False
        documents = {
            document["path"]: document for document in self.extractor._documents(record)
        }
        document = documents.get(row.get("arquivo_path"))
        if document is None:
            return False
        if (
            row.get("status") != "PROCESSADO"
            or row.get("extrator_versao") != EXTRACTOR_VERSION
            or not row.get("arquivo_sha256")
            or row.get("arquivo_sha256") == "0" * 64
            or row.get("papel") != document["papel"]
            or str(row.get("arquivo_nome") or "") != str(document["nome"])
            or int(row.get("arquivo_tamanho") or -1)
            != int(document.get("tamanho") or 0)
        ):
            return False
        stored_document_id = row.get("documento_id")
        registered_document_id = document.get("documento_id")
        return not (
            stored_document_id
            and registered_document_id
            and str(stored_document_id) != str(registered_document_id)
        )

    def _target_bundle(self, target_reference_id):
        self.library._authorize()
        target = self.library.get(target_reference_id)
        state = self._source_state(target)
        if state["issues"]:
            raise MinutasGenerationError(
                "Confira ou reprocesse os documentos instrutórios antes da geração: "
                + "; ".join(state["issues"])
                + "."
            )
        records = state["records"]
        rows = self.extractor.context_sources(target_reference_id)
        grouped = defaultdict(list)
        for row in rows:
            record = records.get(str(row["referencia_id"]))
            if record is not None:
                grouped[str(row["referencia_id"])].append(row)

        target_rows = sorted(
            (
                row
                for row in grouped.get(str(target_reference_id), [])
                if row["papel"] in {"auditoria", "complementar"}
                and str(row.get("arquivo_sha256") or "")
                not in state["_parecer_alvo_hashes"]
                and self._valid_extraction_row(row, target)
            ),
            key=lambda row: (
                0 if row["papel"] == "auditoria" else 1,
                row.get("posicao") or 0,
                row["arquivo_nome"],
            ),
        )
        target_sources = [
            source
            for row in target_rows
            if (source := self._source(row, target)) is not None
        ]
        if not any(source["papel"] == "auditoria" for source in target_sources):
            raise MinutasGenerationError(
                "O relatório da Auditoria do processo selecionado não possui "
                "extração processada."
            )

        return target, state, rows, grouped, target_sources

    def prepare_analysis_context(self, target_reference_id):
        target, state, _rows, _grouped, target_sources = self._target_bundle(
            target_reference_id
        )
        context = {
            "modo": "Análise probatória e jurídica do processo-alvo",
            "processo_alvo": {
                "procuradora": target["procuradora"],
                "numero": target["numero_processo"],
                "exercicio": int(target["exercicio"]),
                "subtipo": target["subtipo"],
                "parecer_definitivo_excluido": True,
            },
            "documentos_processo_alvo": target_sources,
            "sequencia_processual_verificada": _process_sequence(
                {"documentos_processo_alvo": target_sources}
            ),
        }
        for field in ("origem", "responsavel"):
            value = str(target.get(field) or "").strip()
            if value:
                context["processo_alvo"][field] = value
        if _serialized_size(context) > MAX_CONTEXT_BYTES:
            raise MinutasGenerationError(
                "Os documentos essenciais do processo-alvo excedem o limite de contexto."
            )
        return {
            "contexto": context,
            "fontes_versao": state["fontes_versao"],
            "fontes_alvo": len(target_sources),
        }

    def _recover_legal_references(self, target_reference_id, analysis):
        target = self.library.get(target_reference_id)
        plan = _rag_query_plan(analysis)
        queries = plan["consultas"]
        omitted_matters = plan["materias_nao_pesquisadas"]
        if not queries:
            return {
                "versao": RAG_RECOVERY_VERSION,
                "estado": "SEM_CONSULTAS",
                "consultas": [],
                "trechos": [],
                "pareceres": 0,
                "resultados_retornados": 0,
                "descartes": {},
                "materias_nao_pesquisadas": omitted_matters,
                "consultas_executadas": 0,
                "motivo_ausencia": (
                    "A análise não produziu termos discriminativos para pesquisa."
                ),
            }
        diagnostic_queries = [
            {
                **query,
                "resultados_retornados": 0,
                "tentativas": [],
                "descartados": {
                    "barreira_documental": 0,
                    "duplicacao": 0,
                    "sem_trecho": 0,
                    "sem_pertinencia": 0,
                },
            }
            for query in queries
        ]
        allowed_rows = self.extractor.context_sources(target_reference_id)
        allowed_by_path = {
            row["arquivo_path"]: row
            for row in allowed_rows
            if row.get("papel") == "parecer"
            and row.get("procuradora_chave") == target["procuradora_chave"]
        }
        target_opinion = self.library.repository.get_extraction(
            target["parecer_path"]
        )
        excluded_hashes = {
            target_opinion.get("arquivo_sha256")
        } if target_opinion and target_opinion.get("arquivo_sha256") else set()
        candidates = {}
        search_calls = 0
        try:
            for query in diagnostic_queries:
                attempts = [("principal", query["consulta_principal"])]
                if query.get("consulta_alternativa"):
                    attempts.append(("alternativa", query["consulta_alternativa"]))
                for attempt_type, expression in attempts:
                    if search_calls >= MAX_RAG_SEARCH_CALLS:
                        break
                    attempt = {
                        "tipo": attempt_type,
                        "consulta": expression,
                        "resultados_retornados": 0,
                        "descartados": {
                            "barreira_documental": 0,
                            "duplicacao": 0,
                            "sem_trecho": 0,
                            "sem_pertinencia": 0,
                        },
                    }
                    query["tentativas"].append(attempt)
                    search_method = (
                        self.search if callable(self.search) else self.search.search
                    )
                    response = search_method(
                        expression,
                        page_size=MAX_RAG_RESULTS_PER_QUERY,
                        exclude_reference_id=str(target_reference_id),
                        exclude_process_key=target.get("processo_chave"),
                        exclude_paths=(target["parecer_path"],),
                        exclude_hashes=excluded_hashes,
                    )
                    search_calls += 1
                    results = list(response.get("resultados", []))
                    attempt["resultados_retornados"] = len(results)
                    eligible = False
                    for item in results:
                        source = allowed_by_path.get(item.get("arquivo_path"))
                        if (
                            source is None
                            or str(source.get("referencia_id"))
                            == str(target_reference_id)
                            or source.get("arquivo_sha256")
                            != item.get("sha256_registrado")
                        ):
                            attempt["descartados"]["barreira_documental"] += 1
                            continue
                        passage = str(
                            item.get("contexto") or item.get("trecho") or ""
                        ).replace("【", "").replace("】", "").strip()
                        if not passage:
                            attempt["descartados"]["sem_trecho"] += 1
                            continue
                        passage = passage[:MAX_RAG_PASSAGE_CHARS]
                        if not _rag_passage_is_pertinent(query, passage):
                            attempt["descartados"]["sem_pertinencia"] += 1
                            continue
                        eligible = True
                        fingerprint = sha256(
                            _normalized_support(passage).encode("utf-8")
                        ).hexdigest()
                        existing = candidates.get(fingerprint)
                        if existing:
                            attempt["descartados"]["duplicacao"] += 1
                            if query["materia"] not in existing["materias"]:
                                existing["materias"].append(query["materia"])
                            if (
                                query["irregularidade_id"]
                                and query["irregularidade_id"]
                                not in existing["irregularidades_ids"]
                            ):
                                existing["irregularidades_ids"].append(
                                    query["irregularidade_id"]
                                )
                            existing["relevancia"] = max(
                                existing["relevancia"], item["relevancia"]
                            )
                            continue
                        candidates[fingerprint] = {
                            "referencia_id": str(item["referencia_id"]),
                            "processo": item["processo"],
                            "documento": item["documento"],
                            "arquivo_path": item["arquivo_path"],
                            "sha256_registrado": item["sha256_registrado"],
                            "fonte_versao": item["fonte_versao"],
                            "procuradora": item["procuradora"],
                            "pagina": item.get("pagina"),
                            "trecho_literal": passage,
                            "materias": [query["materia"]],
                            "irregularidades_ids": [query["irregularidade_id"]]
                            if query["irregularidade_id"]
                            else [],
                            "relevancia": float(item.get("relevancia") or 0),
                            "natureza": "manifestação histórica não vinculante",
                        }
                    query["resultados_retornados"] += len(results)
                    for key, value in attempt["descartados"].items():
                        query["descartados"][key] += value
                    if eligible:
                        break
        except Exception:
            return {
                "versao": RAG_RECOVERY_VERSION,
                "estado": "INDISPONIVEL",
                "consultas": diagnostic_queries,
                "trechos": [],
                "pareceres": 0,
                "resultados_retornados": sum(
                    item["resultados_retornados"] for item in diagnostic_queries
                ),
                "descartes": {},
                "materias_nao_pesquisadas": omitted_matters,
                "consultas_executadas": search_calls,
                "motivo_ausencia": "A pesquisa jurídica ficou indisponível.",
                "aviso": "Pesquisa jurídica indisponível nesta execução.",
            }

        selected = []
        per_opinion = defaultdict(int)
        for candidate in sorted(
            candidates.values(), key=lambda item: -item["relevancia"]
        ):
            if per_opinion[candidate["referencia_id"]] >= MAX_RAG_PASSAGES_PER_OPINION:
                continue
            selected.append(candidate)
            per_opinion[candidate["referencia_id"]] += 1
            if len(selected) >= MAX_RAG_PASSAGES:
                break
        returned = sum(
            item["resultados_retornados"] for item in diagnostic_queries
        )
        discards = {
            key: sum(item["descartados"][key] for item in diagnostic_queries)
            for key in (
                "barreira_documental",
                "duplicacao",
                "sem_trecho",
                "sem_pertinencia",
            )
        }
        if selected:
            absence_reason = ""
        elif returned == 0:
            absence_reason = (
                "A pesquisa lexical não retornou ocorrências para as consultas construídas."
            )
        elif discards["barreira_documental"] == returned:
            absence_reason = (
                "As ocorrências retornadas foram excluídas pelas barreiras documentais."
            )
        elif discards["sem_pertinencia"]:
            absence_reason = (
                "As ocorrências retornadas não continham âncora temática verificável "
                "no trecho recuperado."
            )
        else:
            absence_reason = (
                "Nenhuma ocorrência retornada permaneceu elegível após deduplicação "
                "e validação documental."
            )
        return {
            "versao": RAG_RECOVERY_VERSION,
            "estado": "CONCLUIDA" if selected else "SEM_RESULTADOS",
            "consultas": diagnostic_queries,
            "trechos": selected,
            "pareceres": len({item["referencia_id"] for item in selected}),
            "resultados_retornados": returned,
            "descartes": discards,
            "materias_nao_pesquisadas": omitted_matters,
            "consultas_executadas": search_calls,
            "motivo_ausencia": absence_reason,
        }

    def _validated_legal_references(self, target_reference_id, references):
        target = self.library.get(target_reference_id)
        allowed = {
            row["arquivo_path"]: row
            for row in self.extractor.context_sources(target_reference_id)
            if row.get("papel") == "parecer"
        }
        validated = []
        seen = set()
        per_opinion = defaultdict(int)
        for item in references or ():
            if not isinstance(item, dict):
                continue
            source = allowed.get(item.get("arquivo_path"))
            passage = str(item.get("trecho_literal") or "").strip()
            fingerprint = _normalized_support(passage)
            if (
                source is None
                or not passage
                or fingerprint in seen
                or str(source.get("referencia_id")) == str(target_reference_id)
                or _canonical_process_key(item.get("processo"))
                == _canonical_process_key(target.get("numero_processo"))
                or source.get("procuradora_chave") != target["procuradora_chave"]
                or source.get("arquivo_sha256") != item.get("sha256_registrado")
                or per_opinion[str(source["referencia_id"])]
                >= MAX_RAG_PASSAGES_PER_OPINION
            ):
                continue
            seen.add(fingerprint)
            per_opinion[str(source["referencia_id"])] += 1
            validated.append(item)
            if len(validated) >= MAX_RAG_PASSAGES:
                break
        return validated

    def prepare_context(
        self,
        target_reference_id,
        analysis=None,
        legal_references=(),
        use_rag=False,
    ):
        target, state, _rows, grouped, target_sources = self._target_bundle(
            target_reference_id
        )
        records = state["records"]
        recovered_count = len(legal_references or ())
        validated_legal_references = (
            self._validated_legal_references(
                target_reference_id, legal_references
            )
            if use_rag
            else []
        )

        target_text = "\n".join(
            unit["texto"] for source in target_sources for unit in source["conteudo"]
        )
        analysis_text = json.dumps(analysis or {}, ensure_ascii=False)
        target_terms = _terms(target_text)
        theme_terms = _terms(analysis_text) if analysis else target_terms
        candidates = []
        for reference_id, source_rows in grouped.items():
            if (
                reference_id == str(target_reference_id)
                or reference_id not in records
            ):
                continue
            record = records[reference_id]
            source_rows = [
                row
                for row in source_rows
                if self._valid_extraction_row(row, record)
                and str(row.get("arquivo_sha256") or "")
                not in state["_parecer_alvo_hashes"]
            ]
            opinions = [row for row in source_rows if row["papel"] == "parecer"]
            if not opinions:
                continue
            opinion = self._source(opinions[0], record)
            if opinion is None:
                continue
            audits = [row for row in source_rows if row["papel"] == "auditoria"]
            audit = self._source(audits[0], record) if audits else None
            comparison = audit or opinion
            comparison_text = "\n".join(
                unit["texto"] for unit in comparison["conteudo"]
            )
            comparison_terms = _terms(comparison_text)
            candidates.append(
                {
                    "record": record,
                    "parecer": opinion,
                    "auditoria": audit,
                    "pertinencia_tematica": len(theme_terms & comparison_terms),
                    "similaridade_documental": len(target_terms & comparison_terms),
                }
            )
        candidates.sort(
            key=lambda item: (
                item["record"]["subtipo"] != target["subtipo"],
                -item["pertinencia_tematica"],
                -item["similaridade_documental"],
                -int(item["record"]["exercicio"]),
                item["record"]["numero_processo"],
            )
        )
        relevant_candidates = []
        for candidate in candidates:
            same_subtype = candidate["record"]["subtipo"] == target["subtipo"]
            has_contextual_signal = bool(
                candidate["pertinencia_tematica"]
                or candidate["similaridade_documental"]
            )
            if not relevant_candidates or same_subtype or has_contextual_signal:
                relevant_candidates.append(candidate)
            if len(relevant_candidates) >= MAX_HISTORICAL_REFERENCES:
                break
        candidates = relevant_candidates
        if not candidates:
            raise MinutasGenerationError(
                "Não há parecer histórico processado da Procuradora selecionada."
            )

        context = {
            "modo": "Teste de Geração — Processo Histórico",
            "natureza": "documento experimental sujeito à revisão humana",
            "processo_alvo": {
                "procuradora": target["procuradora"],
                "numero": target["numero_processo"],
                "exercicio": int(target["exercicio"]),
                "subtipo": target["subtipo"],
                "parecer_definitivo_excluido": True,
            },
            "documentos_processo_alvo": target_sources,
            "sequencia_processual_verificada": _process_sequence(
                {"documentos_processo_alvo": target_sources}
            ),
            "orientacao_dispositivo": (
                "PREFEITURA_CONDICIONAL_DUAS_COMPETENCIAS"
                if target["subtipo"] == "Prefeitura"
                else "DEMAIS_PCA_JULGAMENTO_DIRETO"
            ),
            "referencias_historicas": [],
        }
        for field in ("origem", "responsavel"):
            value = str(target.get(field) or "").strip()
            if value:
                context["processo_alvo"][field] = value
        if analysis is not None:
            context["analise_estruturada"] = analysis
        if use_rag:
            context["referencias_juridicas_recuperadas"] = list(
                validated_legal_references
            )
            context["orientacao_referencias_juridicas"] = (
                "Manifestações históricas não vinculantes; não são fatos do processo-alvo."
            )
        omitted = []
        while (
            _serialized_size(context) > MAX_CONTEXT_BYTES
            and context.get("referencias_juridicas_recuperadas")
        ):
            removed = context["referencias_juridicas_recuperadas"].pop()
            omitted.append(
                f"Trecho jurídico {removed['processo']}, p. "
                f"{removed.get('pagina') or 'não identificada'}"
            )
        if _serialized_size(context) > MAX_CONTEXT_BYTES:
            raise MinutasGenerationError(
                "Os documentos essenciais e a análise estruturada excedem o limite de contexto."
            )

        for candidate in candidates:
            reference = {
                "processo": candidate["record"]["numero_processo"],
                "exercicio": int(candidate["record"]["exercicio"]),
                "subtipo": candidate["record"]["subtipo"],
                "uso": "estilo, estrutura e raciocínio jurídico",
                "parecer": candidate["parecer"],
            }
            proposed = {
                **context,
                "referencias_historicas": [
                    *context["referencias_historicas"],
                    reference,
                ],
            }
            if _serialized_size(proposed) <= MAX_CONTEXT_BYTES:
                context = proposed
            else:
                omitted.append(
                    f"Parecer histórico {candidate['record']['numero_processo']}"
                )
        context_bytes = _serialized_size(context)
        used_sources = [
            f"{source['processo']} — {source['tipo']} ({source['arquivo']})"
            for source in context["documentos_processo_alvo"]
        ]
        for reference in context["referencias_historicas"]:
            opinion = reference["parecer"]
            used_sources.append(
                f"{reference['processo']} — {opinion['tipo']} ({opinion['arquivo']})"
            )
        legal_sources = [
            f"{item['processo']} — {item['documento']}, p. "
            f"{item.get('pagina') or 'não identificada'}"
            for item in context.get("referencias_juridicas_recuperadas", [])
        ]
        return {
            "contexto": context,
            "contexto_bytes": context_bytes,
            "fontes_alvo": len(target_sources),
            "referencias_historicas": len(context["referencias_historicas"]),
            "relatorios_historicos": 0,
            "rag_ativo": bool(use_rag),
            "referencias_juridicas_recuperadas": recovered_count,
            "referencias_juridicas_enviadas": len(legal_sources),
            "fontes_juridicas_enviadas": legal_sources,
            "omitidas_por_limite": omitted,
            "fontes_versao": state["fontes_versao"],
            "fontes_utilizadas": used_sources,
        }

    @staticmethod
    def _source_page_texts(context):
        sources = {}
        for source in context["documentos_processo_alvo"]:
            pages = defaultdict(list)
            for unit in source["conteudo"]:
                page = unit.get("pagina")
                if not isinstance(page, int) or isinstance(page, bool):
                    page = 0
                pages[page].append(str(unit.get("texto") or ""))
            sources[source["fonte_id"]] = {
                page: "\n".join(parts) for page, parts in pages.items()
            }
        return sources

    @staticmethod
    def _source_pages(context):
        return {
            source_id: set(pages)
            for source_id, pages in MinutasGenerationService._source_page_texts(
                context
            ).items()
        }

    @classmethod
    def _validate_analysis(cls, analysis, context):
        if not isinstance(analysis, dict):
            raise MinutasGenerationError("A análise jurídica retornou estrutura inválida.")
        irregularities = analysis.get("irregularidades")
        no_findings = analysis.get("sem_irregularidades_identificadas")
        if not isinstance(irregularities, list) or not isinstance(no_findings, bool):
            raise MinutasGenerationError("A análise jurídica retornou estrutura inválida.")
        if no_findings == bool(irregularities):
            raise MinutasGenerationError(
                "A análise jurídica retornou declaração contraditória de cobertura."
            )
        if not str(analysis.get("resumo_processual") or "").strip() or not isinstance(
            analysis.get("pontos_atencao_gerais"), list
        ):
            raise MinutasGenerationError("A análise jurídica retornou estrutura incompleta.")
        if any(
            not isinstance(item, str) or not item.strip()
            for item in analysis["pontos_atencao_gerais"]
        ):
            raise MinutasGenerationError("A análise jurídica retornou pontos de atenção inválidos.")
        page_texts = cls._source_page_texts(context)
        source_names = {
            source["fonte_id"]: source.get("arquivo") or source["fonte_id"]
            for source in context["documentos_processo_alvo"]
        }
        pending = []

        def verify_reference(reference):
            if not isinstance(reference, dict):
                raise MinutasGenerationError(
                    "A análise jurídica retornou fonte inválida."
                )
            source_id = reference.get("fonte_id")
            page = reference.get("pagina")
            excerpt = reference.get("trecho_suporte")
            reason = ""
            if source_id not in page_texts:
                reason = "fonte não localizada"
            elif not isinstance(page, int) or isinstance(page, bool):
                reason = "página inválida"
            elif not isinstance(excerpt, str) or not excerpt.strip():
                reason = "trecho de suporte ausente"
            elif page not in page_texts[source_id]:
                reason = "página não localizada na fonte indicada"
            else:
                verified, reason = _continuous_word_sequence(
                    excerpt, page_texts[source_id][page]
                )
                if verified:
                    reason = ""
            if reason in {
                "página não localizada na fonte indicada",
                "trecho não localizado na página indicada",
            }:
                matches = _literal_page_matches(excerpt, page_texts[source_id])
                if len(matches) == 1:
                    corrected_page = matches[0]
                    reference["pagina_original"] = page
                    reference["pagina"] = corrected_page
                    reference["pagina_corrigida_automaticamente"] = True
                    reference["criterio_correcao_pagina"] = (
                        "correspondência literal normalizada única no mesmo documento"
                    )
                    page = corrected_page
                    reason = ""
            reference["verificado"] = not reason
            if reason:
                reference["falha_verificacao"] = reason
            else:
                reference.pop("falha_verificacao", None)
            return {
                "verificado": not reason,
                "motivo": reason,
                "documento": source_names.get(source_id, str(source_id or "não informado")),
                "pagina": page if isinstance(page, int) and not isinstance(page, bool) else None,
                "pagina_original": reference.get("pagina_original"),
                "pagina_corrigida_automaticamente": bool(
                    reference.get("pagina_corrigida_automaticamente")
                ),
                "trecho": _normalized_support(excerpt) if isinstance(excerpt, str) else "",
            }

        def check_sources(sources, irregularity_id, category):
            results = [verify_reference(reference) for reference in sources]
            has_verified = any(result["verificado"] for result in results)
            if not results:
                results = [
                    {
                        "verificado": False,
                        "motivo": "nenhum trecho de suporte informado",
                        "documento": "não informado",
                        "pagina": None,
                        "trecho": "",
                    }
                ]
            for result in results:
                if result["verificado"]:
                    continue
                pending.append(
                    {
                        "irregularidade": irregularity_id,
                        "categoria": category,
                        "documento": result["documento"],
                        "pagina": result["pagina"],
                        "natureza": result["motivo"],
                        "bloqueante": False,
                    }
                )
            state = (
                "VERIFICADA"
                if results and all(result["verificado"] for result in results)
                else "PARCIALMENTE_VERIFICADA"
                if has_verified
                else "NAO_VERIFICADA"
            )
            verified_supports = [
                result["trecho"] for result in results if result["verificado"]
            ]
            return state, verified_supports

        identifiers = set()
        for index, irregularity in enumerate(irregularities, 1):
            if not isinstance(irregularity, dict):
                raise MinutasGenerationError(
                    "A análise jurídica retornou irregularidade inválida."
                )
            identifier = str(irregularity.get("id") or "").strip()
            pointing = str(irregularity.get("apontamento") or "").strip()
            if not identifier or identifier in identifiers or not pointing:
                raise MinutasGenerationError(
                    "A análise jurídica não identificou corretamente as irregularidades."
                )
            identifiers.add(identifier)
            for field in (
                "fatos_documentados",
                "normas_mencionadas",
                "argumentos_defesa",
                "pontos_controvertidos",
                "hipoteses_juridicas",
            ):
                if not isinstance(irregularity.get(field), list):
                    raise MinutasGenerationError(
                        f"A irregularidade {index} retornou o campo {field} inválido."
                    )
            sourced_fields = (
                ("fatos_documentados", ("descricao",), "Fato documentado"),
                ("normas_mencionadas", ("descricao", "origem"), "Norma mencionada"),
                (
                    "argumentos_defesa",
                    ("alegacao", "comprovacao_documental"),
                    "Alegação defensiva",
                ),
            )
            for field, text_fields, category in sourced_fields:
                for item in irregularity[field]:
                    if not isinstance(item, dict) or not isinstance(
                        item.get("fontes"), list
                    ):
                        raise MinutasGenerationError(
                            f"A irregularidade {index} retornou evidência inválida."
                        )
                    if any(key not in item for key in text_fields) or not str(
                        item.get(text_fields[0]) or ""
                    ).strip():
                        raise MinutasGenerationError(
                            f"A irregularidade {index} retornou evidência incompleta."
                        )
                    state, supports = check_sources(
                        item["fontes"], identifier, category
                    )
                    item["estado_conferencia"] = state
                    if field == "fatos_documentados" and supports and all(
                        _DEFENSE_ALLEGATION.search(support)
                        and not _DOCUMENTED_CONFIRMATION.search(support)
                        for support in supports
                    ):
                        item["estado_conferencia"] = "NAO_VERIFICADA"
                        pending.append(
                            {
                                "irregularidade": identifier,
                                "categoria": category,
                                "documento": source_names.get(
                                    item["fontes"][0].get("fonte_id"), "não informado"
                                ),
                                "pagina": item["fontes"][0].get("pagina"),
                                "natureza": "alegação defensiva classificada como fato",
                                "bloqueante": False,
                            }
                        )
            for field in ("pontos_controvertidos", "hipoteses_juridicas"):
                if any(
                    not isinstance(item, str) or not item.strip()
                    for item in irregularity[field]
                ):
                    raise MinutasGenerationError(
                        f"A irregularidade {index} retornou análise jurídica inválida."
                    )
            audit = irregularity.get("avaliacao_auditoria")
            if (
                not isinstance(audit, dict)
                or not str(audit.get("descricao") or "").strip()
                or not isinstance(audit.get("fontes"), list)
            ):
                raise MinutasGenerationError(
                    f"A irregularidade {index} não distingue a conclusão da Auditoria."
                )
            treatment = str(audit.get("tratamento_defesa") or "").upper()
            if treatment not in {
                "ACOLHIDA",
                "REJEITADA",
                "PARCIALMENTE_ACOLHIDA",
                "NAO_IDENTIFICADO",
            }:
                raise MinutasGenerationError(
                    f"A irregularidade {index} não classificou o tratamento da defesa."
                )
            if treatment == "NAO_IDENTIFICADO" and not audit["fontes"]:
                audit["estado_conferencia"] = "NAO_APLICAVEL"
            else:
                audit["estado_conferencia"], _supports = check_sources(
                    audit["fontes"], identifier, "Conclusão da Auditoria"
                )

        findings = _explicit_audit_findings(context)
        missing_findings = []
        for finding in findings:
            finding_id = finding["identificador"]
            finding_terms = finding["termos"]
            if not finding_id and not finding_terms:
                continue
            covered = False
            for irregularity in irregularities:
                searchable = _normalized_support(
                    f"{irregularity['id']} {irregularity['apontamento']}"
                )
                if finding_id and re.search(
                    rf"(?<!\d){re.escape(finding_id)}(?!\d)", searchable
                ):
                    covered = True
                    break
                if finding_terms & _terms(irregularity["apontamento"]):
                    covered = True
                    break
            if not covered:
                missing_findings.append(finding["rotulo"])
        if missing_findings or (findings and no_findings):
            labels = missing_findings or [item["rotulo"] for item in findings]
            raise MinutasGenerationError(
                "A análise pode ter omitido apontamentos identificáveis da Auditoria: "
                + "; ".join(labels[:5])
                + ". Confira a instrução."
            )
        analysis["conferencia_documental"] = {
            "estado": "PENDENTE" if pending else "VERIFICADA",
            "suficiente_para_geracao": True,
            "pendencias": pending,
            "politica_versao": VALIDATION_POLICY_VERSION,
        }
        return analysis

    @classmethod
    def _conference_diagnostics(cls, analysis, context):
        page_texts = cls._source_page_texts(context)
        source_names = {
            source["fonte_id"]: source.get("arquivo") or source["fonte_id"]
            for source in context["documentos_processo_alvo"]
        }
        records = _analysis_reference_records(analysis)
        references = defaultdict(list)
        for record in records:
            references[(record["irregularidade"], record["categoria"])].append(record)

        diagnostics = []
        used_records = set()
        conference = analysis.get("conferencia_documental") or {}
        for issue in conference.get("pendencias", ()):
            candidates = references.get(
                (str(issue.get("irregularidade") or ""), issue.get("categoria")),
                (),
            )
            record = next(
                (
                    item
                    for item in candidates
                    if item["chave"] not in used_records
                    if item["referencia"].get("falha_verificacao")
                    == issue.get("natureza")
                ),
                next(
                    (
                        item
                        for item in candidates
                        if item["chave"] not in used_records
                    ),
                    {},
                ),
            )
            if record:
                used_records.add(record["chave"])
            reference = record.get("referencia") or {}
            source_id = reference.get("fonte_id")
            page = reference.get("pagina")
            excerpt = str(reference.get("trecho_suporte") or "")
            indicated_text = page_texts.get(source_id, {}).get(page, "")
            other_pages = []
            if excerpt:
                for candidate_page, text in page_texts.get(source_id, {}).items():
                    if candidate_page == page:
                        continue
                    verified, _reason = _continuous_word_sequence(excerpt, text)
                    if verified:
                        other_pages.append(candidate_page)
            evidence_candidates = []
            if (
                issue.get("natureza")
                in {
                    "página não localizada na fonte indicada",
                    "trecho não localizado na página indicada",
                }
                and source_id in page_texts
            ):
                evidence_candidates = _evidence_candidates(
                    record.get("afirmacao"), excerpt, page_texts[source_id]
                )
            diagnostics.append(
                {
                    "irregularidade": issue.get("irregularidade"),
                    "campo": issue.get("categoria"),
                    "documento": source_names.get(
                        source_id, issue.get("documento") or "não informado"
                    ),
                    "pagina": page if page is not None else issue.get("pagina"),
                    "trecho_suporte": excerpt,
                    "texto_pagina": indicated_text,
                    "correspondencias_em_outras_paginas": sorted(other_pages),
                    "afirmacao_associada": record.get("afirmacao") or "",
                    "referencia_chave": record.get("chave"),
                    "candidatos": evidence_candidates,
                    "motivo": issue.get("natureza"),
                    "bloqueante": bool(issue.get("bloqueante")),
                }
            )
        recorded_corrections = set()
        for (identifier, category), candidates in references.items():
            for record in candidates:
                reference = record["referencia"]
                if not reference.get("pagina_corrigida_automaticamente"):
                    continue
                source_id = reference.get("fonte_id")
                original_page = reference.get("pagina_original")
                corrected_page = reference.get("pagina")
                correction_key = (
                    identifier,
                    category,
                    source_id,
                    original_page,
                    corrected_page,
                    str(reference.get("trecho_suporte") or ""),
                )
                if correction_key in recorded_corrections:
                    continue
                recorded_corrections.add(correction_key)
                diagnostics.append(
                    {
                        "irregularidade": identifier,
                        "campo": category,
                        "documento": source_names.get(
                            source_id, str(source_id or "não informado")
                        ),
                        "pagina": corrected_page,
                        "pagina_original": original_page,
                        "pagina_corrigida": corrected_page,
                        "pagina_corrigida_automaticamente": True,
                        "trecho_suporte": str(
                            reference.get("trecho_suporte") or ""
                        ),
                        "texto_pagina": page_texts.get(source_id, {}).get(
                            corrected_page, ""
                        ),
                        "texto_pagina_original": page_texts.get(source_id, {}).get(
                            original_page, ""
                        ),
                        "correspondencias_em_outras_paginas": [],
                        "motivo": reference.get("criterio_correcao_pagina"),
                        "bloqueante": False,
                    }
                )
        return diagnostics

    def correct_evidence_reference(
        self,
        target_reference_id,
        analyzed,
        reference_key,
        candidate_id,
    ):
        """Apply one literal candidate and revalidate the complete analysis."""
        self.library._authorize()
        target = self.library.get(target_reference_id)
        self._validate_target(target)
        if not isinstance(analyzed, dict) or not isinstance(
            analyzed.get("analise"), dict
        ):
            raise MinutasGenerationError(
                "Execute novamente Analisar fundamentos antes da conferência."
            )
        if analyzed.get("evidence_correction_version") != EVIDENCE_CORRECTION_VERSION:
            raise MinutasGenerationError(
                "A versão da conferência mudou. Analise novamente os fundamentos."
            )
        expected_identity = {
            "processo_alvo_id": str(target["id"]),
            "processo_alvo_chave": _canonical_process_key(
                target.get("processo_chave") or target.get("numero_processo")
            ),
            "procuradora_chave": str(target.get("procuradora_chave") or ""),
        }
        if any(analyzed.get(key) != value for key, value in expected_identity.items()):
            raise MinutasGenerationError(
                "A análise não pertence ao processo e à Procuradora selecionados."
            )

        self._refresh_source_cache(target)
        prepared = self.prepare_analysis_context(target_reference_id)
        if analyzed.get("fontes_versao") != prepared["fontes_versao"]:
            raise MinutasGenerationError(
                "As fontes foram alteradas. Analise novamente os fundamentos."
            )

        corrected = deepcopy(analyzed)
        analysis = self._validate_analysis(
            corrected["analise"], prepared["contexto"]
        )
        records = {
            record["chave"]: record for record in _analysis_reference_records(analysis)
        }
        record = records.get(str(reference_key or ""))
        if record is None:
            raise MinutasGenerationError(
                "A referência pendente não pertence à análise atual."
            )
        reference = record["referencia"]
        if reference.get("verificado"):
            raise MinutasGenerationError("A referência selecionada já está verificada.")
        source_id = reference.get("fonte_id")
        page_texts = self._source_page_texts(prepared["contexto"])
        if source_id not in page_texts:
            raise MinutasGenerationError(
                "A fonte da referência não está autorizada para este processo."
            )
        candidates = _evidence_candidates(
            record["afirmacao"],
            reference.get("trecho_suporte"),
            page_texts[source_id],
        )
        candidate = next(
            (item for item in candidates if item["id"] == candidate_id), None
        )
        if candidate is None:
            raise MinutasGenerationError(
                "O trecho candidato não pertence às fontes atuais."
            )
        if not candidate["substituicao_segura"]:
            raise MinutasGenerationError(
                "O trecho selecionado não preserva a afirmação original."
            )
        verified, _reason = _continuous_word_sequence(
            candidate["trecho_literal"],
            page_texts[source_id].get(candidate["pagina"], ""),
        )
        if not verified:
            raise MinutasGenerationError(
                "O trecho selecionado não corresponde literalmente à fonte atual."
            )

        original_page = reference.get("pagina")
        original_excerpt = reference.get("trecho_suporte")
        reference["pagina"] = candidate["pagina"]
        reference["trecho_suporte"] = candidate["trecho_literal"]
        reference["correcao_assistida"] = {
            "pagina_original": original_page,
            "trecho_original": original_excerpt,
            "pagina_corrigida": candidate["pagina"],
            "trecho_corrigido": candidate["trecho_literal"],
            "candidato_id": candidate["id"],
            "criterio": (
                "trecho literal do mesmo documento autorizado, com elementos "
                "objetivos e qualificadores preservados"
            ),
            "revisao_humana_obrigatoria": True,
        }
        for key in (
            "falha_verificacao",
            "pagina_corrigida_automaticamente",
            "pagina_original",
            "criterio_correcao_pagina",
        ):
            reference.pop(key, None)

        analysis = self._validate_analysis(analysis, prepared["contexto"])
        if not reference.get("verificado"):
            raise MinutasGenerationError(
                "A correção não superou a validação literal da referência."
            )
        conference = analysis["conferencia_documental"]
        corrected["analise"] = analysis
        corrected["suficiente_para_geracao"] = conference[
            "suficiente_para_geracao"
        ]
        corrected["pendencias_conferencia"] = conference["pendencias"]
        corrected["diagnostico_conferencia"] = self._conference_diagnostics(
            analysis, prepared["contexto"]
        )
        interventions = list(corrected.get("intervencoes_evidencias") or ())
        interventions.append(
            {
                "referencia_chave": reference_key,
                "irregularidade": record["irregularidade"],
                "categoria": record["categoria"],
                "fonte_id": source_id,
                **reference["correcao_assistida"],
            }
        )
        corrected["intervencoes_evidencias"] = interventions
        corrected["validation_policy_version"] = VALIDATION_POLICY_VERSION
        return corrected

    def migrate_validation_policy(self, target_reference_id, analyzed):
        """Reclassify a stored analysis without making another Gemini call."""
        self.library._authorize()
        target = self.library.get(target_reference_id)
        self._validate_target(target)
        if not isinstance(analyzed, dict) or not isinstance(
            analyzed.get("analise"), dict
        ):
            raise MinutasGenerationError(
                "Execute novamente Analisar fundamentos antes de gerar a minuta."
            )
        expected_identity = {
            "processo_alvo_id": str(target["id"]),
            "processo_alvo_chave": _canonical_process_key(
                target.get("processo_chave") or target.get("numero_processo")
            ),
            "procuradora_chave": str(target.get("procuradora_chave") or ""),
        }
        if any(
            str(analyzed.get(key) or "") != value
            for key, value in expected_identity.items()
        ):
            raise MinutasGenerationError(
                "A análise não pertence ao processo e à Procuradora selecionados."
            )
        self._refresh_source_cache(target)
        prepared = self.prepare_analysis_context(target_reference_id)
        if analyzed.get("fontes_versao") != prepared["fontes_versao"]:
            raise MinutasGenerationError(
                "As fontes foram alteradas. Analise novamente os fundamentos."
            )
        migrated = deepcopy(analyzed)
        analysis = self._validate_analysis(
            migrated["analise"], prepared["contexto"]
        )
        conference = analysis["conferencia_documental"]
        migrated.update(
            {
                "analise": analysis,
                "suficiente_para_geracao": True,
                "pendencias_conferencia": conference["pendencias"],
                "diagnostico_conferencia": self._conference_diagnostics(
                    analysis, prepared["contexto"]
                ),
                "evidence_correction_version": EVIDENCE_CORRECTION_VERSION,
                "validation_policy_version": VALIDATION_POLICY_VERSION,
            }
        )
        return migrated

    def pilot_diagnostics(self, target_reference_id, analyzed):
        self.library._authorize()
        target = self.library.get(target_reference_id)
        self._validate_target(target)
        if not isinstance(analyzed, dict):
            return {"disponivel": False}
        expected_identity = {
            "processo_alvo_id": str(target["id"]),
            "processo_alvo_chave": _canonical_process_key(
                target.get("processo_chave") or target.get("numero_processo")
            ),
            "procuradora_chave": str(target.get("procuradora_chave") or ""),
        }
        if any(analyzed.get(key) != value for key, value in expected_identity.items()):
            return {"disponivel": False}
        self._refresh_source_cache(target)
        current_revision = self._source_state(target)["fontes_versao"]
        if (
            analyzed.get("fontes_versao") != current_revision
            or analyzed.get("evidence_correction_version")
            != EVIDENCE_CORRECTION_VERSION
        ):
            return {"disponivel": False}
        conference = analyzed.get("diagnostico_conferencia")
        recovery = analyzed.get("recuperacao_juridica")
        if (
            conference is None
            or not isinstance(recovery, dict)
            or analyzed.get("rag_recovery_version") != RAG_RECOVERY_VERSION
            or recovery.get("versao") != RAG_RECOVERY_VERSION
        ):
            return {"disponivel": False}
        return {
            "disponivel": True,
            "conferencia_documental": conference,
            "recuperacao_juridica": recovery,
        }

    def refresh_legal_recovery(self, target_reference_id, analyzed):
        started = perf_counter()
        self.library._authorize()
        target = self.library.get(target_reference_id)
        self._validate_target(target)
        if not isinstance(analyzed, dict) or not isinstance(
            analyzed.get("analise"), dict
        ):
            raise MinutasGenerationError(
                "Execute novamente Analisar fundamentos para atualizar a pesquisa jurídica."
            )
        self._refresh_source_cache(target)
        current = self.source_summary(target_reference_id)
        if analyzed.get("fontes_versao") != current["fontes_versao"]:
            raise MinutasGenerationError(
                "As fontes foram alteradas. Analise novamente os fundamentos."
            )
        refreshed = dict(analyzed)
        refreshed["recuperacao_juridica"] = self._recover_legal_references(
            target_reference_id, analyzed["analise"]
        )
        refreshed["rag_recovery_version"] = RAG_RECOVERY_VERSION
        LOGGER.info(
            "MINUTAS_PERF etapa=rag_atualizacao duracao_ms=%d consultas=%d trechos=%d",
            round((perf_counter() - started) * 1000),
            int(refreshed["recuperacao_juridica"].get("consultas_executadas") or 0),
            len(refreshed["recuperacao_juridica"].get("trechos") or ()),
        )
        return refreshed

    @staticmethod
    def _validate_structure(structure):
        required = (
            "identificacao_processual",
            "ementa",
            "relatorio",
            "fundamentacao_geral",
            "conclusao_pedidos",
            "encerramento",
        )
        if not isinstance(structure, dict) or any(
            not str(structure.get(field) or "").strip() for field in required
        ):
            raise MinutasGenerationError(
                "A IA não retornou todas as partes obrigatórias da minuta."
            )
        topics = structure.get("topicos_irregularidades")
        requests = structure.get("pedidos")
        if not isinstance(requests, list) or not requests or any(
            not str(request or "").strip() for request in requests
        ):
            raise MinutasGenerationError(
                "A IA não retornou pedidos estruturados válidos."
            )
        if not isinstance(topics, list) or any(
            not isinstance(topic, dict)
            or not str(topic.get("titulo") or "").strip()
            or not isinstance(topic.get("irregularidades_ids"), list)
            or not isinstance(topic.get("avaliacoes_irregularidades"), list)
            or not str(topic.get("texto") or "").strip()
            for topic in topics
        ):
            raise MinutasGenerationError(
                "A IA não retornou fundamentação estruturada válida."
            )
        for field in ("relatorio_paragrafos", "fundamentacao_geral_paragrafos"):
            paragraphs = structure.get(field)
            if paragraphs is not None and (
                not isinstance(paragraphs, list)
                or not paragraphs
                or any(not str(item or "").strip() for item in paragraphs)
            ):
                raise MinutasGenerationError(
                    "A IA não retornou a estrutura de parágrafos válida."
                )
        for topic in topics:
            paragraphs = topic.get("paragrafos")
            if paragraphs is not None and (
                not isinstance(paragraphs, list)
                or not paragraphs
                or any(not str(item or "").strip() for item in paragraphs)
            ):
                raise MinutasGenerationError(
                    "A IA não retornou os parágrafos da fundamentação válidos."
                )
            identifiers = topic["irregularidades_ids"]
            assessments = topic["avaliacoes_irregularidades"]
            assessment_ids = [
                assessment.get("irregularidade_id")
                for assessment in assessments
                if isinstance(assessment, dict)
            ]
            if (
                any(
                    not isinstance(identifier, str) or not identifier.strip()
                    for identifier in identifiers
                )
                or any(
                    not isinstance(identifier, str) or not identifier.strip()
                    for identifier in assessment_ids
                )
                or len(set(identifiers)) != len(identifiers)
                or len(assessment_ids) != len(identifiers)
                or set(assessment_ids) != set(identifiers)
            ):
                raise MinutasGenerationError(
                    "A IA não vinculou corretamente as avaliações às irregularidades."
                )
            for assessment in assessments:
                if (
                    not isinstance(assessment, dict)
                    or not str(assessment.get("irregularidade_id") or "").strip()
                    or assessment.get("tratamento_ministerial_defesa")
                    not in _MINISTERIAL_TREATMENTS
                    or not isinstance(assessment.get("fundamentos_divergencia"), list)
                    or any(
                        not str(ground or "").strip()
                        for ground in assessment.get("fundamentos_divergencia", [])
                    )
                    or assessment.get("estado_saneamento") not in _CURE_STATES
                    or not isinstance(assessment.get("fontes_saneamento"), list)
                    or any(
                        not isinstance(source, dict)
                        or not str(source.get("fonte_id") or "").strip()
                        or not isinstance(source.get("pagina"), int)
                        or isinstance(source.get("pagina"), bool)
                        or not isinstance(source.get("trecho_suporte"), str)
                        for source in assessment.get("fontes_saneamento", [])
                    )
                ):
                    raise MinutasGenerationError(
                        "A IA não retornou avaliações ministeriais estruturadas válidas."
                    )
        return structure

    @staticmethod
    def _normalize_structure(structure, target, context=None):
        normalized = deepcopy(structure)
        warnings = []
        report, foundation = _normalized_report_sections(
            normalized,
            _process_sequence_paragraph(context or {}),
        )
        normalized["relatorio_paragrafos"] = report
        normalized["relatorio"] = "\n\n".join(report)
        normalized["fundamentacao_geral_paragrafos"] = foundation
        normalized["fundamentacao_geral"] = "\n\n".join(foundation)
        normalized["encerramento"] = "É o Parecer."

        requests = [str(item).strip() for item in normalized.get("pedidos", ())]
        opinion_requests = [
            item for item in requests if _PRELIMINARY_OPINION_RE.search(item)
        ]
        account_requests = [item for item in requests if _account_outcomes(item)]
        account_requests = [
            item
            for item in account_requests
            if not _PRELIMINARY_OPINION_RE.search(item)
        ]
        subtype = str(target.get("subtipo") or "")
        if subtype == "Prefeitura":
            if opinion_requests and account_requests:
                ordered = [opinion_requests[0], account_requests[0]]
                normalized["pedidos"] = ordered + [
                    item for item in requests if item not in ordered
                ]
            else:
                warnings.append(
                    "PCA de Prefeitura sem elementos suficientes para confirmar "
                    "se as duas manifestações dispositivas são cabíveis; revise a "
                    "competência e o objeto antes da assinatura."
                )
        else:
            if opinion_requests:
                removable = [
                    item
                    for item in opinion_requests
                    if not re.search(
                        r"\b(?:multa|d[eé]bito|recomenda|determina|ressalva)\w*\b",
                        item,
                        re.IGNORECASE,
                    )
                ]
                remaining = [item for item in requests if item not in removable]
                if remaining:
                    requests = remaining
                warnings.append(
                    "Foi identificada proposta de parecer prévio em PCA que não é "
                    "de Prefeitura; o dispositivo exige revisão jurídica."
                )
            account_requests = [
                item
                for item in requests
                if _account_outcomes(item)
                and not _PRELIMINARY_OPINION_RE.search(item)
            ]
            if account_requests:
                first = account_requests[0]
                normalized["pedidos"] = [first] + [
                    item for item in requests if item != first
                ]
            else:
                normalized["pedidos"] = requests
                warnings.append(
                    "A PCA exige proposta direta de regularidade, regularidade com "
                    "ressalvas ou irregularidade no primeiro item do dispositivo."
                )
        return normalized, warnings

    @staticmethod
    def _structure_markdown(structure):
        structure = deepcopy(structure)
        report_blocks, foundation_blocks = _normalized_report_sections(structure)
        structure["relatorio_paragrafos"] = report_blocks
        structure["fundamentacao_geral_paragrafos"] = foundation_blocks
        structure["encerramento"] = "É o Parecer."
        report = "\n\n".join(
            _paragraph_blocks(
                structure.get("relatorio_paragrafos"), structure["relatorio"]
            )
        )
        general_foundation = "\n\n".join(
            _paragraph_blocks(
                structure.get("fundamentacao_geral_paragrafos"),
                structure["fundamentacao_geral"],
            )
        )
        sections = [
            structure["identificacao_processual"].strip(),
            "## Ementa\n\n" + structure["ementa"].strip(),
            "## Relatório\n\n" + report,
            "## Fundamentação\n\n" + general_foundation,
        ]
        for topic in structure["topicos_irregularidades"]:
            if isinstance(topic, dict) and str(topic.get("texto") or "").strip():
                topic_text = "\n\n".join(
                    _paragraph_blocks(topic.get("paragrafos"), topic["texto"])
                )
                sections.append(
                    "### "
                    + str(topic.get("titulo") or "Irregularidade").strip()
                    + "\n\n"
                    + topic_text
                )
        request_texts = [request.strip() for request in structure["pedidos"]]
        request_keys = {_normalized_support(request) for request in request_texts}
        conclusion_lines = []
        for line in structure["conclusao_pedidos"].strip().splitlines():
            unnumbered = re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", line)
            if _normalized_support(unnumbered) not in request_keys:
                conclusion_lines.append(line)
        conclusion = "\n".join(conclusion_lines).strip()
        if structure["pedidos"]:
            numbered_requests = "\n".join(
                f"{index}. {request.strip()}"
                for index, request in enumerate(structure["pedidos"], start=1)
            )
            conclusion = "\n\n".join(
                part for part in (conclusion, numbered_requests) if part
            )
        sections.extend(
            (
                "## Conclusão e propostas de encaminhamento\n\n" + conclusion,
                structure["encerramento"].strip(),
            )
        )
        return "\n\n".join(sections)

    @staticmethod
    def _analysis_for_generation(analysis):
        """Keep unverified findings visible without presenting their quotes as proof."""
        safe = deepcopy(analysis)
        pending = []
        for irregularity in safe.get("irregularidades", []):
            identifier = str(irregularity.get("id") or "")
            groups = (
                ("fatos_documentados", "Fato documentado"),
                ("normas_mencionadas", "Norma mencionada"),
                ("argumentos_defesa", "Alegação defensiva"),
            )
            for field, category in groups:
                for item in irregularity.get(field, []):
                    if item.get("estado_conferencia") == "NAO_VERIFICADA":
                        item["tratamento_redacao"] = (
                            "PENDENTE_DE_CONFERENCIA; não apresentar como fato "
                            "documentalmente confirmado"
                        )
                    for reference in item.get("fontes", []):
                        if reference.get("verificado") is True:
                            continue
                        reason = str(
                            reference.get("falha_verificacao")
                            or "referência não verificada"
                        )
                        reference["trecho_suporte"] = ""
                        reference["uso_na_redacao"] = "NAO_COMPROVADO"
                        reference["aviso_conferencia"] = reason
                        pending.append(
                            {
                                "irregularidade": identifier,
                                "categoria": category,
                                "motivo": reason,
                            }
                        )
            audit = irregularity.get("avaliacao_auditoria") or {}
            if audit.get("estado_conferencia") == "NAO_VERIFICADA":
                audit["tratamento_redacao"] = (
                    "PENDENTE_DE_CONFERENCIA; atribuir como conclusão indicada, "
                    "não como conclusão documentalmente confirmada"
                )
            for reference in audit.get("fontes", []):
                if reference.get("verificado") is True:
                    continue
                reason = str(
                    reference.get("falha_verificacao")
                    or "referência não verificada"
                )
                reference["trecho_suporte"] = ""
                reference["uso_na_redacao"] = "NAO_COMPROVADO"
                reference["aviso_conferencia"] = reason
                pending.append(
                    {
                        "irregularidade": identifier,
                        "categoria": "Conclusão da Auditoria",
                        "motivo": reason,
                    }
                )
        if pending:
            safe["orientacao_evidencias_pendentes"] = (
                "As referências marcadas NAO_COMPROVADO não são prova literal. "
                "Preserve as irregularidades, diferencie constatação, alegação e "
                "hipótese, use redação cautelosa e indique necessidade de conferência."
            )
        return safe

    @staticmethod
    def _quality_warnings(structure, analysis, target_context):
        warnings = []
        assessments = {
            str(assessment.get("irregularidade_id") or ""): assessment
            for topic in structure.get("topicos_irregularidades", [])
            if isinstance(topic, dict)
            for assessment in topic.get("avaliacoes_irregularidades", [])
            if isinstance(assessment, dict)
        }
        covered = {
            str(identifier)
            for topic in structure.get("topicos_irregularidades", [])
            if isinstance(topic, dict)
            for identifier in topic.get("irregularidades_ids", [])
        }
        missing = [
            item["id"]
            for item in analysis.get("irregularidades", [])
            if item["id"] not in covered
        ]
        if missing:
            warnings.append(
                "Possível omissão: irregularidades não vinculadas à fundamentação final: "
                + ", ".join(missing)
                + "."
            )
        missing_assessments = [
            item["id"]
            for item in analysis.get("irregularidades", [])
            if item["id"] not in assessments
        ]
        if missing_assessments:
            warnings.append(
                "Possível omissão: irregularidades sem avaliação ministerial estruturada: "
                + ", ".join(missing_assessments)
                + "."
            )
        page_texts = MinutasGenerationService._source_page_texts(target_context)
        source_pages = {
            source_id: set(pages) for source_id, pages in page_texts.items()
        }
        for irregularity in analysis.get("irregularidades", []):
            assessment = assessments.get(irregularity["id"])
            if not assessment:
                continue
            audit_treatment = irregularity.get("avaliacao_auditoria", {}).get(
                "tratamento_defesa"
            )
            ministry_treatment = assessment.get("tratamento_ministerial_defesa")
            if audit_treatment == "REJEITADA" and ministry_treatment in {
                "ACOLHIDA", "PARCIALMENTE_ACOLHIDA"
            }:
                grounds = assessment.get("fundamentos_divergencia", [])
                has_specific_ground = any(
                    _specific_divergence_ground(ground) for ground in grounds
                )
                if not has_specific_ground:
                    warnings.append(
                        f"Divergência da Auditoria sem fundamento jurídico ou "
                        f"probatório específico: {irregularity['id']}."
                    )
            cure_sources = assessment.get("fontes_saneamento", [])
            verified_cure_source = False
            for source in cure_sources:
                if not isinstance(source, dict):
                    continue
                source_id = source.get("fonte_id")
                page = source.get("pagina")
                excerpt = source.get("trecho_suporte")
                if page not in page_texts.get(source_id, {}):
                    continue
                verified, _reason = _continuous_word_sequence(
                    excerpt, page_texts[source_id][page]
                )
                if verified:
                    verified_cure_source = True
                    break
            topic_text = "\n".join(
                str(topic.get("texto") or "")
                for topic in structure.get("topicos_irregularidades", [])
                if isinstance(topic, dict)
                and irregularity["id"] in topic.get("irregularidades_ids", [])
            )
            categorical_cure = bool(_CATEGORICAL_CURE.search(_plain(topic_text)))
            cure_claim = (
                assessment.get("estado_saneamento") == "COMPROVADO"
                or categorical_cure
            )
            specific_probative_ground = any(
                _specific_divergence_ground(ground)
                for ground in assessment.get("fundamentos_divergencia", [])
            )
            unsupported_cure = cure_claim and (
                not verified_cure_source
                or (
                    audit_treatment == "REJEITADA"
                    and not specific_probative_ground
                )
            )
            if unsupported_cure:
                if audit_treatment == "REJEITADA":
                    warnings.append(
                        "Saneamento afirmado apesar da manutenção da irregularidade "
                        "pela Auditoria, sem justificativa probatória textual verificável: "
                        f"{irregularity['id']}."
                    )
                else:
                    warnings.append(
                        "Afirmação categórica de saneamento sem suporte textual "
                        f"verificável: {irregularity['id']}."
                    )
        text = MinutasGenerationService._structure_markdown(structure)
        source_text = "\n".join(
            unit["texto"]
            for source in target_context["documentos_processo_alvo"]
            for unit in source["conteudo"]
        )
        source_normalized = _normalized_support(source_text)
        for amount in sorted(set(_INVALID_BRL_RE.findall(text))):
            warnings.append(
                "Possível valor monetário com separadores incompatíveis com o "
                f"padrão brasileiro: {amount}. Confira a fonte; o valor não foi "
                "reescrito automaticamente."
            )
        for amount in set(re.findall(r"R\$\s*[\d.]+(?:,\d{2})?", text)):
            if re.sub(r"\s+", "", amount) not in re.sub(r"\s+", "", source_text):
                warnings.append(
                    f"Possível valor sem correspondência literal nas fontes: {amount}."
                )
        target_process = _canonical_process_key(
            target_context.get("processo_alvo", {}).get("numero")
        )
        for process in set(
            re.findall(r"\bTC\s*(?:n[.º°o]\s*)?(\d{5}\s*/\s*\d{2})\b", text, re.IGNORECASE)
        ):
            if _canonical_process_key(process) != target_process:
                warnings.append(
                    "Possível número de processo alheio ao processo-alvo: "
                    f"TC {process}."
                )
        normative_patterns = (
            r"\bPortaria\s+(?:[A-Z]{2,10}\s+)?(?:n[.º°o]\s*)?\d[\d./-]*",
            r"\b(?:Lei|Decreto|Resolução)\s+(?:n[.º°o]\s*)?\d[\d./-]*",
            r"\bart(?:igo)?\.?(?:\s+n[.º°o])?\s*\d+[A-Za-z-]*",
        )
        cited_norms = {
            match.group(0)
            for pattern in normative_patterns
            for match in re.finditer(pattern, text, re.IGNORECASE)
        }
        for norm in sorted(cited_norms):
            if _normalized_support(norm) not in source_normalized:
                warnings.append(
                    "Possível referência normativa sem correspondência nas fontes "
                    f"do processo-alvo: {norm}."
                )
        unsupported_harm_claims = {
            match.group(0)
            for match in _NO_HARM_CLAIM_RE.finditer(text)
            if _normalized_support(match.group(0)) not in source_normalized
        }
        if unsupported_harm_claims:
            warnings.append(
                "Possível afirmação de ausência de dano ou prejuízo ao controle "
                "sem suporte textual nas fontes do processo-alvo."
            )
        valid_pages = set().union(*source_pages.values())
        for page in {
            int(value)
            for value in re.findall(r"(?:página|p\.)\s*(\d+)", text, re.IGNORECASE)
        }:
            if page not in valid_pages:
                warnings.append(
                    f"Possível citação de página sem correspondência nas fontes: {page}."
                )
        foundation = "\n".join(
            [structure["fundamentacao_geral"]]
            + [
                str(topic.get("texto") or "")
                for topic in structure.get("topicos_irregularidades", [])
                if isinstance(topic, dict)
            ]
        )
        foundation_outcomes = _account_outcomes(
            "\n".join((structure["ementa"], foundation))
        )
        dispositive_outcomes = _account_outcomes(
            "\n".join(
                (structure["conclusao_pedidos"], *structure.get("pedidos", ()))
            )
        )
        if len(dispositive_outcomes) > 1:
            warnings.append(
                "O dispositivo contém propostas incompatíveis sobre a regularidade "
                "do mesmo objeto."
            )
        if (
            foundation_outcomes
            and dispositive_outcomes
            and foundation_outcomes.isdisjoint(dispositive_outcomes)
        ):
            warnings.append(
                "Possível inconsistência entre a ementa, a fundamentação e a conclusão "
                "quanto ao resultado."
            )
        return warnings

    def analyze(self, target_reference_id):
        total_started = perf_counter()
        self.library._authorize()
        target = self.library.get(target_reference_id)
        self._validate_target(target)
        self._refresh_source_cache(target)
        prepared = self.prepare_analysis_context(target_reference_id)
        ai_started = perf_counter()
        generated = self._analyze(prepared["contexto"])
        ai_duration_ms = round((perf_counter() - ai_started) * 1000)
        if not isinstance(generated, dict):
            raise MinutasGenerationError("A IA não retornou análise jurídica válida.")
        analysis = self._validate_analysis(generated.get("analise"), prepared["contexto"])
        conference = analysis["conferencia_documental"]
        conference_diagnostics = self._conference_diagnostics(
            analysis, prepared["contexto"]
        )
        rag_started = perf_counter()
        legal_recovery = self._recover_legal_references(
            target_reference_id, analysis
        )
        rag_duration_ms = round((perf_counter() - rag_started) * 1000)
        response = {
            **generated,
            "analise": analysis,
            "suficiente_para_geracao": conference["suficiente_para_geracao"],
            "pendencias_conferencia": conference["pendencias"],
            "fontes_versao": prepared["fontes_versao"],
            "fontes_alvo": prepared["fontes_alvo"],
            "diagnostico_conferencia": conference_diagnostics,
            "recuperacao_juridica": legal_recovery,
            "rag_recovery_version": RAG_RECOVERY_VERSION,
            "evidence_correction_version": EVIDENCE_CORRECTION_VERSION,
            "validation_policy_version": VALIDATION_POLICY_VERSION,
            "processo_alvo_id": str(target["id"]),
            "processo_alvo_chave": _canonical_process_key(
                target.get("processo_chave") or target.get("numero_processo")
            ),
            "procuradora_chave": str(target.get("procuradora_chave") or ""),
        }
        LOGGER.info(
            "MINUTAS_PERF etapa=analise duracao_ms=%d gemini_ms=%d rag_ms=%d "
            "fontes=%d consultas=%d trechos=%d",
            round((perf_counter() - total_started) * 1000),
            ai_duration_ms,
            rag_duration_ms,
            int(prepared["fontes_alvo"]),
            int(legal_recovery.get("consultas_executadas") or 0),
            len(legal_recovery.get("trechos") or ()),
        )
        return response

    def generate(self, target_reference_id, analyzed, use_rag=True):
        total_started = perf_counter()
        self.library._authorize()
        target = self.library.get(target_reference_id)
        self._validate_target(target)
        if not isinstance(analyzed, dict) or not isinstance(analyzed.get("analise"), dict):
            raise MinutasGenerationError("Analise os fundamentos antes de gerar a minuta.")
        expected_identity = {
            "processo_alvo_id": str(target["id"]),
            "processo_alvo_chave": _canonical_process_key(
                target.get("processo_chave") or target.get("numero_processo")
            ),
            "procuradora_chave": str(target.get("procuradora_chave") or ""),
        }
        if any(
            str(analyzed.get(key) or "") != value
            for key, value in expected_identity.items()
        ):
            raise MinutasGenerationError(
                "A análise não pertence ao processo e à Procuradora selecionados."
            )
        recovery = analyzed.get("recuperacao_juridica") or {}
        if use_rag and (
            analyzed.get("rag_recovery_version") != RAG_RECOVERY_VERSION
            or recovery.get("versao") != RAG_RECOVERY_VERSION
        ):
            raise MinutasGenerationError(
                "Atualize a recuperação jurídica antes de gerar a minuta."
            )
        self._refresh_source_cache(target)
        prepared = self.prepare_context(
            target_reference_id,
            analyzed["analise"],
            recovery.get("trechos", ()),
            use_rag=bool(use_rag),
        )
        if analyzed.get("fontes_versao") != prepared["fontes_versao"]:
            raise MinutasGenerationError(
                "As fontes foram alteradas. Analise novamente os fundamentos."
            )
        analysis = self._validate_analysis(
            analyzed["analise"], prepared["contexto"]
        )
        conference = analysis["conferencia_documental"]
        request_context = deepcopy(prepared["contexto"])
        request_context["analise_estruturada"] = self._analysis_for_generation(
            analysis
        )
        if conference["pendencias"]:
            request_context["orientacao_conferencia_documental"] = (
                "Existem pontos não verificados. Eles não impedem a minuta, mas "
                "não podem ser tratados como prova confirmada. Preserve cada "
                "irregularidade e use formulação cautelosa para revisão humana."
            )
        if _serialized_size(request_context) > MAX_CONTEXT_BYTES:
            raise MinutasGenerationError(
                "A solicitação integral excede o limite seguro de contexto."
            )
        ai_started = perf_counter()
        generated = self._generate(request_context)
        ai_duration_ms = round((perf_counter() - ai_started) * 1000)
        if not isinstance(generated, dict):
            raise MinutasGenerationError("A IA não retornou uma minuta válida.")
        structure = self._validate_structure(generated.get("estrutura"))
        structure, normalization_warnings = self._normalize_structure(
            structure, target, prepared["contexto"]
        )
        structure = self._validate_structure(structure)
        warnings = [
            *normalization_warnings,
            *self._quality_warnings(
                structure, analysis, prepared["contexto"]
            ),
        ]
        response = {
            **generated,
            "estrutura": structure,
            "texto": self._structure_markdown(structure),
            "advertencias_qualidade": warnings,
            "advertencias_documentais": deepcopy(conference["pendencias"]),
            "validation_policy_version": VALIDATION_POLICY_VERSION,
            "processo_alvo_id": str(target["id"]),
            "processo_alvo_chave": _canonical_process_key(
                target.get("processo_chave") or target.get("numero_processo")
            ),
            "procuradora_chave": str(target.get("procuradora_chave") or ""),
            **{
                key: (
                    _serialized_size(request_context)
                    if key == "contexto_bytes"
                    else value
                )
                for key, value in prepared.items()
                if key != "contexto"
            },
        }
        LOGGER.info(
            "MINUTAS_PERF etapa=geracao duracao_ms=%d gemini_ms=%d "
            "contexto_bytes=%d rag_ativo=%s fontes_alvo=%d referencias=%d",
            round((perf_counter() - total_started) * 1000),
            ai_duration_ms,
            int(response.get("contexto_bytes") or 0),
            bool(use_rag),
            int(response.get("fontes_alvo") or 0),
            int(response.get("referencias_historicas") or 0),
        )
        return response
