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
from services.minutas_library import MinutasValidationError
from services.minutas_orientacao import MAX_ORIENTACAO_JURIDICA_CHARS
from services.minutas_search import MinutasSearchService


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
VALIDATION_POLICY_VERSION = "5-rpps-device-sanctions-v1"
LOGGER = logging.getLogger(__name__)
REPORT_TRANSITION = "É o Relatório. Passo a opinar."
DEVICE_INTRO_HARMONY = (
    "Ante o exposto, em harmonia com o entendimento do Órgão Técnico, "
    "o Ministério Público de Contas opina pelo (a):"
)
DEVICE_INTRO_NEUTRAL = "Ante o exposto, o Ministério Público de Contas opina:"
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
_DEVICE_INTRO_RE = re.compile(
    r"^\s*ante\s+o\s+exposto\b.*$"
    r"|^\s*(?:diante|face)\s+(?:do|da)\s+(?:exposto|fundamenta[cç][aã]o)\b.*$",
    re.IGNORECASE,
)
_REQUEST_LIKE_RE = re.compile(
    r"\b(?:regularidade|irregularidade|ressalvas|multa|d[eé]bito|"
    r"recomenda|determina|julg(?:ue|ue-se|amento)|aplique|afaste|"
    r"expedi[cç][aã]o|envio de recomenda)\w*\b",
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
_FORBIDDEN_RPPS_PRELIMINARY_REQUEST_RE = re.compile(
    r"\bemiss[aã]o\s+de\s+parecer\s+pr[eé]vio\b"
    r"|\bparecer\s+pr[eé]vio\s+ou\s+(?:proposta\s+de\s+)?julgamento\b",
    re.IGNORECASE,
)
_MULTA_REQUEST_RE = re.compile(r"\bmulta\b", re.IGNORECASE)
_ACCOUNT_JUDGMENT_REQUEST_RE = re.compile(
    r"\b(?:julgamento|julg(?:ue|uem)-se)\b"
    r"|\bcontas?\s+(?:como\s+)?(?:regulares?(?:\s+com\s+ressalvas)?|irregulares?)\b"
    r"|\b(?:regularidade(?:\s+com\s+ressalvas)?|irregularidade)\s+das?\s+contas\b",
    re.IGNORECASE,
)
_POSITIVE_DEBIT_REQUEST_RE = re.compile(
    r"\b(?:imputa[cç][aã]o|impute-se|condena[cç][aã]o)\b.*\bd[eé]bito\b"
    r"|\breposi[cç][aã]o\s+(?:do\s+dano|ao\s+er[aá]rio)\b",
    re.IGNORECASE,
)
_NEGATIVE_REQUEST_RE = re.compile(
    r"\b(?:afast(?:ar|amento|e-se)|n[aã]o\s+(?:aplicar|imputar|propor))\b",
    re.IGNORECASE,
)
_CORRECTIVE_REQUEST_RE = re.compile(
    r"\b(?:determina|recomenda)\w*\b", re.IGNORECASE
)
_COMMUNICATION_REQUEST_RE = re.compile(
    r"\b(?:comunica|remessa|remeta|juntada|junte-se)\w*\b", re.IGNORECASE
)
_SANCTION_CONCLUSIONS = {
    "MULTA_CABIVEL",
    "MULTA_NAO_CABIVEL",
    "REVISAO_HUMANA_NECESSARIA",
}
_DAMAGE_STATES = {
    "NAO_COMPROVADO_OU_NAO_QUANTIFICADO",
    "COMPROVADO_E_QUANTIFICADO",
    "REVISAO_HUMANA_NECESSARIA",
}
_RECOMMENDATION_REQUEST_RE = re.compile(r"\brecomend\w*\b", re.IGNORECASE)
_BUREAUCRATIC_INSTRUCTION_SENTENCE_RE = re.compile(
    r"^(?:"
    r"(?:integram|compoem|constituem)\s+"
    r"(?:(?:a\s+)?instrucao\s+processual|os\s+autos|o\s+processo)\b"
    r"|a\s+instrucao(?:\s+processual)?\s+(?:e\s+)?"
    r"(?:composta|formada|integrada)\s+por\b"
    r"|(?:o\s+processo|os\s+autos)\s+(?:esta|estao)\s+"
    r"instruid[oa]s?\s+(?:com|por)\b"
    r"|(?:constam|foram\s+juntad[oa]s)\s+(?:dos|aos)\s+autos\s+"
    r"(?:os\s+seguintes\s+)?(?:documentos|elementos|pecas)\b"
    r").*$"
)
_ACCOUNT_OUTCOME_PATTERNS = (
    (
        "REGULARIDADE_COM_RESSALVAS",
        re.compile(
            r"\bregularidade\s+com\s+ressalvas\b"
            r"|\bcontas?\s+(?:como\s+)?regulares?\s+com\s+ressalvas\b",
            re.IGNORECASE,
        ),
    ),
    (
        "IRREGULARIDADE",
        re.compile(
            r"\birregularidade\b|\bcontas?\s+(?:como\s+)?irregulares?\b",
            re.IGNORECASE,
        ),
    ),
    (
        "REGULARIDADE",
        re.compile(
            r"\bregularidade\b(?!\s+com\s+ressalvas)"
            r"|\bcontas?\s+(?:como\s+)?regulares?\b(?!\s+com\s+ressalvas)",
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


def _without_bureaucratic_instruction(value):
    paragraphs = []
    for block in re.split(r"\n\s*\n", str(value or "").strip()):
        kept_lines = []
        for line in block.splitlines():
            sentences = re.split(r"(?<=[.!?])\s+", line.strip())
            kept = [
                sentence.strip()
                for sentence in sentences
                if sentence.strip()
                and not _BUREAUCRATIC_INSTRUCTION_SENTENCE_RE.fullmatch(
                    _plain(sentence).strip()
                )
            ]
            if kept:
                kept_lines.append(" ".join(kept))
        if kept_lines:
            paragraphs.append("\n".join(kept_lines))
    return "\n\n".join(paragraphs).strip()


def _normalized_report_sections(structure, process_sequence=None):
    report = [
        cleaned
        for block in _paragraph_blocks(
            structure.get("relatorio_paragrafos"), structure.get("relatorio")
        )
        if (
            cleaned := _without_bureaucratic_instruction(
                _without_report_transition(block)
            )
        )
    ]
    foundation = [
        cleaned
        for block in _paragraph_blocks(
            structure.get("fundamentacao_geral_paragrafos"),
            structure.get("fundamentacao_geral"),
        )
        if (
            cleaned := _without_bureaucratic_instruction(
                _without_report_transition(block)
            )
        )
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


def _account_outcomes(value):
    text = str(value or "")
    return {
        name for name, pattern in _ACCOUNT_OUTCOME_PATTERNS if pattern.search(text)
    }


def _is_rpps_target(target):
    specialty = str((target or {}).get("especialidade_id") or "").casefold()
    subtype = _plain((target or {}).get("subtipo"))
    return (
        specialty.endswith(".pca.rpps") or specialty.endswith(".rpps")
    ) and subtype != "prefeitura"


def _direct_account_request(value):
    outcomes = _account_outcomes(value)
    if len(outcomes) != 1:
        return ""
    wording = {
        "REGULARIDADE": "regulares",
        "REGULARIDADE_COM_RESSALVAS": "regulares com ressalva",
        "IRREGULARIDADE": "irregulares",
    }
    return f"Julgamento das contas como {wording[next(iter(outcomes))]}."


def _is_prefeituras_target(target):
    return str((target or {}).get("especialidade_id") or "") == "elvira.pca.prefeituras"


def _government_opinion_outcome(request):
    """Return an outcome only for an explicit government-accounts opinion."""
    text = str(request or "")
    if not (
        _PRELIMINARY_OPINION_RE.search(text)
        and re.search(r"\bcontas?\s+de\s+governo\b", text, re.IGNORECASE)
    ):
        return ""
    normalized = _plain(text)
    if "contrario" in normalized:
        return "IRREGULARIDADE"
    if "favoravel" not in normalized:
        return ""
    return (
        "REGULARIDADE_COM_RESSALVAS"
        if "ressalv" in normalized
        else "REGULARIDADE"
    )


def _government_opinion_request(outcome, target):
    origin = str((target or {}).get("origem") or "").strip()
    exercise = str((target or {}).get("exercicio") or "").strip()
    if not origin or not exercise:
        return ""
    base = "das contas de governo de " + origin + ", relativas ao exercício de " + exercise
    if outcome == "REGULARIDADE":
        return "Pela emissão de parecer prévio favorável à aprovação " + base + "."
    if outcome == "REGULARIDADE_COM_RESSALVAS":
        return "Pela emissão de parecer prévio favorável à aprovação, com ressalvas, " + base + ", pelos fundamentos expostos."
    if outcome == "IRREGULARIDADE":
        return "Pela emissão de parecer prévio contrário à aprovação " + base + ", pelos fundamentos expostos."
    return ""


def _request_category(request):
    if (
        _account_outcomes(request)
        and _ACCOUNT_JUDGMENT_REQUEST_RE.search(request)
        and not _PRELIMINARY_OPINION_RE.search(request)
        and not _MULTA_REQUEST_RE.search(request)
        and not _POSITIVE_DEBIT_REQUEST_RE.search(request)
        and not _CORRECTIVE_REQUEST_RE.search(request)
    ):
        return 0
    if _MULTA_REQUEST_RE.search(request):
        return 1
    if _POSITIVE_DEBIT_REQUEST_RE.search(request):
        return 2
    if _CORRECTIVE_REQUEST_RE.search(request):
        return 3
    if _COMMUNICATION_REQUEST_RE.search(request):
        return 4
    return 5


def _order_rpps_requests(requests):
    indexed = list(enumerate(requests))
    indexed.sort(key=lambda item: (_request_category(item[1]), item[0]))
    return [request for _index, request in indexed]


def _canonical_process_key(value):
    normalized = re.sub(r"\s+", " ", _plain(value)).strip()
    normalized = re.sub(r"^processo\s*", "", normalized)
    normalized = re.sub(r"^tc\s*[-:]?\s*", "", normalized)
    if not re.fullmatch(r"\d{5}\s*/?\s*\d{2}", normalized):
        return ""
    return re.sub(r"\D", "", normalized)


def _draft_process_display(target):
    key = _canonical_process_key(target.get("numero_processo")) or (
        _canonical_process_key(target.get("processo_chave"))
    )
    return (
        f"{key[:5]}/{key[5:]}"
        if len(key) == 7
        else str(target.get("numero_processo") or "NÃO INFORMADO").strip()
    )


def _draft_identifier(target):
    return f"MINUTA-TC-{_draft_process_display(target)}"


_HEADER_METADATA_LABELS = {
    "origem": (
        "origem",
        "entidade",
        "unidade gestora",
        "órgão",
        "orgão",
        "jurisdicionado",
    ),
    "responsavel": (
        "responsável",
        "responsavel",
        "gestor responsável",
        "gestor responsavel",
        "dirigente responsável",
        "dirigente responsavel",
        "ordenador de despesa",
    ),
}
_HEADER_METADATA_REJECTED = {
    "origem": (
        "auditoria",
        "tribunal de contas",
        "tce-pb",
        "ministerio publico de contas",
        "relatorio",
        "documento",
    ),
    "responsavel": ("auditor", "procurador", "relator", "analista", "advogado", "defensor"),
}
_RESPONSIBLE_NARRATIVE_PATTERNS = (
    re.compile(
        r"\bsob\s+a\s+responsabilidade\s+d[ao]\s+"
        r"(?:(?:[Pp]refeit[oa]|[Gg]estor(?:a)?)\s+(?:[Ss]r(?:a)?\.?\s*)?"
        r"|[Ss]r(?:a)?\.?\s+|(?![Pp]refeit[oa]\b|[Gg]estor(?:a)?\b|[Ss]r(?:a)?\b))"
        r"(?P<value>[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+){1,5})"
        r"(?=[,.;\n]|$)"
    ),
    re.compile(
        r"\b(?:[Pp]refeit[oa]|[Gg]estor(?:a)?\s+respons[aá]vel)\s+"
        r"(?:[Ss]r(?:a)?\.?\s*)?(?P<value>[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+"
        r"(?:\s+[A-ZÀ-Ý][A-Za-zÀ-ÿ'’-]+){1,5})(?=[,.;\n]|$)"
    ),
)


def _header_metadata_value(field, value):
    candidate = re.sub(r"\s+", " ", str(value or "")).strip(" .;:-")
    normalized = _plain(candidate)
    if (
        not candidate
        or len(candidate) > 220
        or any(term in normalized for term in _HEADER_METADATA_REJECTED[field])
    ):
        return ""
    if field == "responsavel" and (
        len(candidate.split()) < 2 or any(character.isdigit() for character in candidate)
    ):
        return ""
    return candidate


def _header_metadata_from_text(field, text):
    labels = "|".join(re.escape(label) for label in _HEADER_METADATA_LABELS[field])
    pattern = re.compile(
        rf"(?im)^\s*(?:{labels})\s*[:\-–—]\s*(?P<value>[^\n]+)$"
    )
    candidates = {}
    for match in pattern.finditer(str(text or "")):
        value = _header_metadata_value(field, match.group("value"))
        if value:
            candidates.setdefault(_normalized_support(value), value)
    if field == "responsavel":
        for pattern in _RESPONSIBLE_NARRATIVE_PATTERNS:
            for match in pattern.finditer(str(text or "")):
                value = _header_metadata_value(field, match.group("value"))
                if value:
                    candidates.setdefault(_normalized_support(value), value)
    return candidates


def _resolved_header_metadata(target, sources=(), analysis=None):
    metadata = {}
    source_text = "\n".join(
        unit.get("texto", "")
        for source in sources
        for unit in source.get("conteudo", ())
        if isinstance(unit, dict)
    )
    for field in ("origem", "responsavel"):
        structured = _header_metadata_value(field, target.get(field))
        if structured:
            metadata[field] = structured
            continue
        candidates = _header_metadata_from_text(field, source_text)
        if len(candidates) == 1:
            metadata[field] = next(iter(candidates.values()))
            continue
        # A análise consolidada só desempata candidatos que já constem das
        # fontes do processo-alvo; nunca cria metadado a partir da IA.
        analysis_candidates = _header_metadata_from_text(
            field, json.dumps(analysis or {}, ensure_ascii=False)
        )
        confirmed = {
            key: candidates[key]
            for key in candidates.keys() & analysis_candidates.keys()
        }
        if len(confirmed) == 1:
            metadata[field] = next(iter(confirmed.values()))
    return metadata


def _target_with_header_metadata(target, metadata):
    resolved = dict(target)
    for field in ("origem", "responsavel"):
        value = _header_metadata_value(field, (metadata or {}).get(field))
        if value:
            resolved[field] = value
    return resolved


def _draft_header_lines(target):
    process = _draft_process_display(target)
    origin = str(target.get("origem") or "Não informada").strip()
    responsible = str(target.get("responsavel") or "Não informado").strip()
    exercise = str(target.get("exercicio") or "não informado").strip()
    return [
        "Parecer Nº",
        f"Processo TC Nº {process}",
        f"Origem: {origin}",
        f"Natureza: Prestação de Contas Anuais - exercício {exercise}",
        f"Responsável: {responsible}",
    ]


def _recommendation_clauses(request):
    clauses = []
    for line in str(request or "").splitlines():
        if re.search(
            r"\bno\s+sentido\s+de(?:\s+que)?\s*:?\s*$",
            line,
            re.IGNORECASE,
        ):
            continue
        clause = re.sub(r"^\s*[a-z]\s*[).:-]\s*", "", line, flags=re.IGNORECASE)
        clause = re.sub(
            r"^\s*(?:expe[cç]am-se\s+)?recomenda[cç][oõ]es?\s*:?\s*",
            "",
            clause,
            flags=re.IGNORECASE,
        )
        clause = re.sub(
            r"^\s*(?:recomende-se|recomenda-se|seja\s+recomendad[oa])\s*:?\s*",
            "",
            clause,
            flags=re.IGNORECASE,
        ).strip(" ;.")
        no_sense = re.search(
            r"\bno\s+sentido\s+de(?:\s+que)?\s*:?\s*(.+)$",
            clause,
            re.IGNORECASE,
        )
        if no_sense:
            clause = no_sense.group(1).strip(" ;.")
        else:
            clause = re.sub(
                r"^\s*(?:pela\s+)?expedi[çc][aã]o\s+de\s+"
                r"recomenda(?:[çc][aã]o|[çc][oõ]es)\s*(?:à|ao|a)?\s*"
                r"gest[aã]o(?:\s+que)?\s*",
                "",
                clause,
                flags=re.IGNORECASE,
            )
            clause = re.sub(
                r"^\s*(?:à|ao|a)?\s*gest[aã]o\s+(?:que\s+)?",
                "",
                clause,
                flags=re.IGNORECASE,
            )
        clause = re.sub(r"^\s*recomendar\s+", "", clause, flags=re.IGNORECASE)
        clause = clause.strip(" ;.")
        if clause:
            clauses.append(clause)
    return clauses


def _format_recommendation_item(clauses, origin=""):
    cleaned = []
    seen = set()
    for clause in clauses:
        text = re.sub(r"^\s*recomendar\s+", "", str(clause or ""), flags=re.IGNORECASE)
        text = text.strip(" ;.")
        key = _normalized_support(text)
        if not key or key in seen:
            continue
        seen.add(key)
        cleaned.append(text)
    if not cleaned:
        return ""
    recipient = _recommendation_recipient(origin)
    if len(cleaned) == 1:
        body = cleaned[0]
        if body[:1].isupper() and (len(body) == 1 or not body[1].isupper()):
            body = body[0].lower() + body[1:]
        return (
            "Pela expedição de recomendação "
            + recipient
            + ", no sentido de que "
            + body
            + "."
        )
    lines = [
        "Pela expedição de recomendações "
        + recipient
        + ", no sentido de que:"
    ]
    for index, clause in enumerate(cleaned):
        ending = "." if index == len(cleaned) - 1 else ";"
        lines.append(f"{chr(ord('a') + index)}) {clause}{ending}")
    return "\n".join(lines)


def _recommendation_recipient(origin):
    entity = str(origin or "").strip()
    if not entity:
        return "à gestão"
    if _plain(entity).startswith(("instituto", "orgao", "fundo", "conselho")):
        return f"à gestão do {entity}"
    if _plain(entity).startswith(("prefeitura", "secretaria", "camara")):
        return f"à gestão da {entity}"
    return f"à gestão de {entity}"


def _consolidate_recommendations(requests, origin=""):
    positions = [
        index
        for index, request in enumerate(requests)
        if _RECOMMENDATION_REQUEST_RE.search(str(request or ""))
    ]
    if not positions:
        return list(requests)
    clauses = []
    seen = set()
    for index in positions:
        for clause in _recommendation_clauses(requests[index]):
            key = _normalized_support(clause)
            if key and key not in seen:
                seen.add(key)
                clauses.append(clause)
    if not clauses:
        return [request for index, request in enumerate(requests) if index not in positions]
    consolidated = _format_recommendation_item(clauses, origin)
    if not consolidated:
        return [request for index, request in enumerate(requests) if index not in positions]
    first = positions[0]
    result = []
    for index, request in enumerate(requests):
        if index == first:
            result.append(consolidated)
        if index not in positions:
            result.append(request)
    return result


def _ministry_diverges_from_audit(structure):
    for topic in structure.get("topicos_irregularidades") or ():
        if not isinstance(topic, dict):
            continue
        for assessment in topic.get("avaliacoes_irregularidades") or ():
            if not isinstance(assessment, dict):
                continue
            if assessment.get("tratamento_ministerial_defesa") in {
                "ACOLHIDA",
                "PARCIALMENTE_ACOLHIDA",
            }:
                return True
    return False


def _normalized_device_introduction(structure):
    intro = (
        DEVICE_INTRO_NEUTRAL
        if _ministry_diverges_from_audit(structure)
        else DEVICE_INTRO_HARMONY
    )
    request_keys = {
        _normalized_support(request) for request in structure.get("pedidos", ())
    }
    extras = []
    for block in re.split(
        r"\n\s*\n", str(structure.get("conclusao_pedidos") or "").strip()
    ):
        for line in block.splitlines():
            unnumbered = re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", line).strip()
            if not unnumbered or _DEVICE_INTRO_RE.match(unnumbered):
                continue
            key = _normalized_support(unnumbered)
            if key in request_keys:
                continue
            if _REQUEST_LIKE_RE.search(unnumbered):
                extras.append(unnumbered)
                request_keys.add(key)
    return intro, extras


def _sanitize_draft_structure(structure):
    sanitized = deepcopy(structure)
    for field in (
        "ementa",
        "relatorio",
        "fundamentacao_geral",
        "conclusao_pedidos",
    ):
        sanitized[field] = _without_bureaucratic_instruction(
            sanitized.get(field)
        )
    for field in ("relatorio_paragrafos", "fundamentacao_geral_paragrafos"):
        if isinstance(sanitized.get(field), list):
            sanitized[field] = [
                cleaned
                for item in sanitized[field]
                if (cleaned := _without_bureaucratic_instruction(item))
            ]
    for topic in sanitized.get("topicos_irregularidades", ()):
        if not isinstance(topic, dict):
            continue
        topic["texto"] = _without_bureaucratic_instruction(topic.get("texto"))
        if isinstance(topic.get("paragrafos"), list):
            topic["paragrafos"] = [
                cleaned
                for item in topic["paragrafos"]
                if (cleaned := _without_bureaucratic_instruction(item))
            ]
    sanction = sanitized.get("analise_sancionatoria")
    if isinstance(sanction, dict):
        sanction["texto"] = _without_bureaucratic_instruction(
            sanction.get("texto")
        )
    sanitized["pedidos"] = [
        cleaned
        for item in sanitized.get("pedidos", ())
        if (cleaned := _without_bureaucratic_instruction(item))
    ]
    return sanitized


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
_LEGAL_OPINION_SECTIONS = {
    "ementa",
    "fundamentacao",
    "analise_irregularidades",
    "conclusao_ministerial",
}
_OPINION_REMISSION_RE = re.compile(
    r"\b(?:conforme|nos termos|segundo|de acordo com|remiss[aã]o|"
    r"remete-se|remeto-me|acompanho)\b[\s\S]{0,160}?"
    r"\b(?:relat[oó]rio|auditoria|an[aá]lise t[eé]cnica|documento)\b",
    re.IGNORECASE,
)
_THIN_OPINION_RE = re.compile(
    r"\b(?:acompanho(?:\s+integralmente)?\s+o\s+relat[oó]rio|"
    r"na forma do relat[oó]rio|"
    r"sem fundamenta[cç][aã]o|"
    r"fundamenta[cç][aã]o insuficiente|"
    r"n[aã]o desenvolve fundamenta)\b",
    re.IGNORECASE,
)
_LEGAL_FOUNDATION_MARKER_RE = re.compile(
    r"\b(?:art\.?|lei\b|constitui[cç][aã]o|fundamenta|ante o exposto|"
    r"opino|s[uú]mula)\b",
    re.IGNORECASE,
)
_MISSING_TECHNICAL_ANALYSIS = (
    "O parecer definitivo remete a documento ausente. "
    "A análise técnica correspondente não está disponível e não deve ser inventada."
)
_THIN_OPINION_LIMITATION = (
    "O parecer definitivo não desenvolve fundamentação jurídica suficiente. "
    "Indique a limitação e não invente o conteúdo da análise técnica."
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


def _stored_sections(row):
    raw = row.get("secoes_json")
    if isinstance(raw, list):
        sections = raw
    else:
        try:
            sections = json.loads(raw or "[]")
        except (TypeError, json.JSONDecodeError):
            return []
    if not isinstance(sections, list):
        return []
    return [item for item in sections if isinstance(item, dict)]


def _opinion_legal_content(row):
    """Fundamentos do parecer definitivo, sem o relatório de auditoria."""
    sections = _stored_sections(row)
    legal = [
        text
        for section in sections
        if section.get("secao") in _LEGAL_OPINION_SECTIONS
        and (text := _clean_text(section.get("texto")))
    ]
    full = _source_text(row)
    full_text = "\n".join(unit["texto"] for unit in full)
    if legal:
        return "\n".join(legal), [{"texto": text} for text in legal], False
    return full_text, None, bool(sections)


def _opinion_precedent_limitation(text, *, has_audit, sections_without_foundation=False):
    body = str(text or "").strip()
    remits = _OPINION_REMISSION_RE.search(body) is not None
    thin = bool(_THIN_OPINION_RE.search(body)) or sections_without_foundation
    founded = _LEGAL_FOUNDATION_MARKER_RE.search(body) is not None
    if remits and not has_audit:
        return _MISSING_TECHNICAL_ANALYSIS
    if thin or (remits and not founded):
        return _THIN_OPINION_LIMITATION
    return ""


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
        self.extractor = MinutasExtractionService(library, cache_documents=True)
        self.search = search or MinutasSearchService(library)
        base_analyze = analyze or ai_service.analisar_fundamentos_minuta
        base_generate = generate or ai_service.gerar_minuta_parecer
        self._analyze = lambda contexto, _base=base_analyze: _base(self._com_camadas(contexto))

        def _gerar_com_modelo(contexto, _base=base_generate):
            self._exigir_modelo_visual()
            return _base(self._com_camadas(self._exigir_bloco_orientacao(contexto)))

        self._generate = _gerar_com_modelo
        # A instância vive apenas durante um rerun do Streamlit. Este cache evita
        # repetir o mesmo inventário dentro da execução sem reutilizar dados entre
        # sessões ou impedir que o rerun seguinte enxergue fontes alteradas.
        self._records_cache = None
        self._source_state_cache = {}

    def _exigir_modelo_visual(self):
        """Antes da chamada onerosa, quando o cadastro de modelos pode ser consultado."""
        from services.minutas_docx import MinutasDocxError
        from services.minutas_modelo_docx import garantir_modelo_exportavel

        try:
            garantir_modelo_exportavel(self.library)
        except MinutasDocxError as exc:
            raise MinutasGenerationError(str(exc)) from exc

    def _pessoa(self):
        from services.minutas_procuradores import legado_espo

        return getattr(self.library, "procurador", None) or legado_espo()

    def _com_camadas(self, contexto):
        if not isinstance(contexto, dict) or contexto.get("camadas_prompt"):
            return contexto
        from services.minutas_procuradores import camadas_prompt
        from services.minutas_specialties import scope

        nome = ""
        specialty = getattr(self.library, "specialty_id", None)
        if specialty:
            try:
                nome = scope(specialty).get("nome") or ""
            except Exception:
                nome = ""
        enriquecido = dict(contexto)
        enriquecido["camadas_prompt"] = camadas_prompt(
            self._pessoa(),
            nome,
            orientacao_juridica=bool(
                enriquecido.get("orientacao_juridica_usuario")
            ),
        )
        return enriquecido

    @staticmethod
    def _exigir_bloco_orientacao(contexto):
        """Última barreira antes do modelo: não há geração sem o bloco válido."""
        from services.minutas_orientacao import normalizar_orientacao

        if not isinstance(contexto, dict):
            raise MinutasGenerationError(
                "Informe a orientação jurídica antes de gerar a minuta."
            )
        bloco = contexto.get("orientacao_juridica_usuario")
        if not isinstance(bloco, dict):
            raise MinutasGenerationError(
                "Informe a orientação jurídica antes de gerar a minuta."
            )
        normalizar_orientacao(bloco.get("texto"))
        return contexto

    def campos_orientacao(self, target, analyzed, usuario=""):
        campos = {
            "usuario": str(usuario or "").strip(),
            "procurador_chave": str((target or {}).get("procuradora_chave") or ""),
            "especialidade_id": str((target or {}).get("especialidade_id") or ""),
            "fontes_versao": (
                str(analyzed.get("fontes_versao") or "")
                if isinstance(analyzed, dict)
                else ""
            ),
            "analise": analyzed.get("analise") if isinstance(analyzed, dict) else None,
            "processo_chave": str((target or {}).get("processo_chave") or ""),
        }
        registrado = (
            str(analyzed.get("processo_alvo_chave") or "")
            if isinstance(analyzed, dict)
            else ""
        )
        if registrado:
            campos["processo_analise"] = registrado
        return campos

    def pacote_orientacao(self, texto, target, analyzed, usuario=""):
        from services.minutas_orientacao import contexto_orientacao, normalizar_orientacao

        campos = self.campos_orientacao(target, analyzed, usuario)
        normalizado = normalizar_orientacao(texto)
        return {
            "orientacao_juridica": normalizado,
            "contexto_orientacao": contexto_orientacao(
                usuario=campos["usuario"],
                procurador_chave=campos["procurador_chave"],
                especialidade_id=campos["especialidade_id"],
                fontes_versao=campos["fontes_versao"],
                analise=campos["analise"],
                processo_chave=campos["processo_chave"],
            ),
            "usuario": campos["usuario"],
        }

    def _validar_orientacao_operacao(
        self, target, analyzed, orientacao_juridica, contexto_orientacao, usuario
    ):
        from services.minutas_orientacao import exigir_orientacao_confirmada

        campos = self.campos_orientacao(target, analyzed, usuario)
        return exigir_orientacao_confirmada(
            orientacao_juridica,
            contexto_orientacao,
            usuario=campos["usuario"],
            procurador_chave=campos["procurador_chave"],
            especialidade_id=campos["especialidade_id"],
            fontes_versao=campos["fontes_versao"],
            analise=campos["analise"],
            processo_chave=campos["processo_chave"],
            processo_analise=campos.get("processo_analise", ""),
        )

    def _referencia_autorizada(self, row, target):
        if not isinstance(row, dict):
            return False
        if row.get("parecer_definitivo") is False:
            return False
        if str(row.get("papel") or "parecer") != "parecer":
            return False
        processo = str(row.get("processo_chave") or "")
        if processo and processo == str(target.get("processo_chave") or ""):
            return False
        chave = str(row.get("procuradora_chave") or "")
        if chave and chave != str(target.get("procuradora_chave") or ""):
            return False
        nome = str(row.get("procuradora") or "")
        if nome and nome != str(target.get("procuradora") or ""):
            return False
        return True

    def _buscar_orientacao(self, consulta, target):
        if not consulta:
            return []
        search_method = self.search if callable(self.search) else self.search.search
        try:
            response = search_method(
                consulta,
                page_size=MAX_RAG_RESULTS_PER_QUERY,
                exclude_process_key=target.get("processo_chave"),
                exclude_reference_id=str(target.get("id") or ""),
            )
        except Exception:
            return []
        return list((response or {}).get("resultados") or [])

    def _enriquecer_referencias_instrucao(self, precedentes, texto, target):
        from services.minutas_orientacao import (
            consulta_da_orientacao,
            mesclar_referencias,
            trecho_aderente,
        )

        base = deepcopy(precedentes) if isinstance(precedentes, dict) else {
            "estado": "SEM_RESULTADO",
            "referencias": [],
            "consultas": [],
        }
        consulta = consulta_da_orientacao(texto)
        if not consulta:
            return base, ""
        novas = []
        for row in self._buscar_orientacao(consulta, target):
            if not self._referencia_autorizada(row, target):
                continue
            trecho = _clean_text(row.get("trecho") or row.get("texto") or "")[
                :MAX_RAG_PASSAGE_CHARS
            ]
            if not trecho or not trecho_aderente(consulta, trecho):
                continue
            novas.append({
                "referencia_id": str(row.get("referencia_id") or row.get("id") or ""),
                "processo": row.get("numero_processo") or row.get("processo") or "",
                "trecho": trecho,
                "pagina": row.get("pagina") if isinstance(row.get("pagina"), int) else None,
                "documento": row.get("documento") or "Parecer definitivo",
                "criterio_recuperacao": "orientacao_do_assessor",
            })
        referencias = mesclar_referencias(base.get("referencias") or [], novas)
        enriquecido = dict(base)
        enriquecido["referencias"] = referencias
        consultas = list(base.get("consultas") or [])
        consultas.append({"consulta": consulta, "origem": "orientacao_do_assessor"})
        enriquecido["consultas"] = consultas
        if referencias and enriquecido.get("estado") in {None, "", "SEM_RESULTADO", "SEM_CONSULTAS"}:
            enriquecido["estado"] = "RECUPERADO"
        return enriquecido, consulta

    def _mesclar_trechos_orientacao(self, target, trechos, texto):
        from services.minutas_orientacao import (
            consulta_da_orientacao,
            mesclar_referencias,
            trecho_aderente,
        )

        consulta = consulta_da_orientacao(texto)
        if not consulta:
            return list(trechos or []), ""
        novas = []
        for row in self._buscar_orientacao(consulta, target):
            if not self._referencia_autorizada(row, target):
                continue
            passage = str(row.get("contexto") or row.get("trecho") or "").replace(
                "【", ""
            ).replace("】", "").strip()
            if not passage or not trecho_aderente(consulta, passage):
                continue
            novas.append({
                "referencia_id": str(row.get("referencia_id") or ""),
                "processo": row.get("processo") or "",
                "documento": row.get("documento") or "",
                "arquivo_path": row.get("arquivo_path"),
                "sha256_registrado": row.get("sha256_registrado"),
                "fonte_versao": row.get("fonte_versao"),
                "procuradora": row.get("procuradora"),
                "pagina": row.get("pagina"),
                "trecho_literal": passage[:MAX_RAG_PASSAGE_CHARS],
                "materias": ["orientação do assessor"],
                "irregularidades_ids": [],
                "relevancia": float(row.get("relevancia") or 0),
                "natureza": "manifestação histórica não vinculante",
                "criterio_recuperacao": "orientacao_do_assessor",
            })
        return mesclar_referencias(list(trechos or []), novas), consulta

    def preparar_orientacao(
        self,
        context,
        *,
        target,
        analyzed,
        orientacao_juridica,
        contexto_orientacao,
        usuario="",
        precedentes=None,
        use_rag=True,
    ):
        from services.minutas_orientacao import aplicar_bloco

        texto = self._validar_orientacao_operacao(
            target, analyzed, orientacao_juridica, contexto_orientacao, usuario
        )
        consulta = ""
        referencias = precedentes
        if use_rag:
            referencias, consulta = self._enriquecer_referencias_instrucao(
                precedentes, texto, target
            )
        aplicar_bloco(context, texto, consulta if use_rag else "")
        return texto, referencias

    def _records(self):
        from services.minutas_specialties import scope, RPPS
        if scope(getattr(self.library, "specialty_id", None))["id"] != RPPS:
            raise MinutasGenerationError("Esta especialidade ainda não possui motor habilitado.")
        if self._records_cache is None:
            if hasattr(self.library.repository, "profile"):
                profile = self.library.repository.profile()
                if profile["motor"] != "pca-rpps-v1" or profile["config_versao"] != "1":
                    raise MinutasGenerationError("Configuração jurídica ainda não habilitada neste motor.")
            self._records_cache = tuple(self.library.list())
        return self._records_cache

    def _refresh_source_cache(self, target):
        """Força revisão atual das fontes antes de uma ação explícita."""
        self._records_cache = None
        self._source_state_cache.pop(str(target["id"]), None)
        self.extractor.document_cache.clear()

    def targets(self):
        return [
            record
            for record in self._records()
            if record.get("procuradora") == self._pessoa().nome_exibicao
            and record.get("especialidade_id") == self.library.specialty_id
            and record.get("classificacao_estado") == "CLASSIFICADO"
        ]

    @staticmethod
    def _validate_target(target):
        from services.minutas_specialties import RPPS
        keys = {_canonical_process_key(target.get(field)) for field in ("processo_chave", "numero_processo")}
        if "" in keys or len(keys) != 1:
            raise MinutasGenerationError("Identificação do processo inválida ou conflitante.")
        if target.get("especialidade_id") != RPPS or target.get("classificacao_estado") != "CLASSIFICADO":
            raise MinutasGenerationError(
                "O processo não está classificado na especialidade RPPS habilitada."
            )
        from services.minutas_procuradores import procurador_da_chave
        pessoa = procurador_da_chave(target.get("procuradora_chave"))
        if pessoa is None or target.get("procuradora") != pessoa.nome_exibicao:
            raise MinutasGenerationError("Procurador não autorizado para este acervo.")

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
            and record.get("especialidade_id") == target.get("especialidade_id")
            and record.get("classificacao_estado") == "CLASSIFICADO"
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
            "especialidade": target.get("especialidade_id"),
            "configuracao": (self.library.repository.profile()
                             if hasattr(self.library.repository, "profile") else {}),
            "records": [
                (str(record["id"]), str(record.get("atualizado_em") or ""),
                 record.get("especialidade_id"), record.get("classificacao_revisao"))
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

        metadata = _resolved_header_metadata(target, target_sources)
        return (
            _target_with_header_metadata(target, metadata),
            state,
            rows,
            grouped,
            target_sources,
        )

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
            legal_text, legal_units, sections_without_foundation = (
                _opinion_legal_content(opinions[0])
            )
            if legal_units:
                opinion = {**opinion, "conteudo": legal_units}
            legal_terms = _terms(legal_text)
            has_audit = any(row["papel"] == "auditoria" for row in source_rows)
            candidates.append(
                {
                    "record": record,
                    "parecer": opinion,
                    "limitacao": _opinion_precedent_limitation(
                        legal_text,
                        has_audit=has_audit,
                        sections_without_foundation=sections_without_foundation,
                    ),
                    "pertinencia_tematica": len(theme_terms & legal_terms),
                    "similaridade_documental": len(target_terms & legal_terms),
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
                "Não há parecer histórico processado de outro processo nesta especialidade. "
                "Alimente ou confira o acervo, sem utilizar o parecer do processo-alvo."
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
                "Manifestações históricas não vinculantes; são fundamentos jurídicos "
                "e não são fatos do processo-alvo. Se o parecer for pouco fundamentado "
                "ou remeter a documento ausente, indique a limitação e não invente a "
                "análise técnica."
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
                "uso": "fundamentos jurídicos pertinentes do parecer definitivo",
                "parecer": candidate["parecer"],
            }
            if candidate.get("limitacao"):
                reference["limitacao"] = candidate["limitacao"]
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
        sanction = structure.get("analise_sancionatoria")
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
        if (
            not isinstance(sanction, dict)
            or sanction.get("conclusao") not in _SANCTION_CONCLUSIONS
            or sanction.get("dano_ao_erario") not in _DAMAGE_STATES
            or not str(sanction.get("texto") or "").strip()
            or any(
                not isinstance(sanction.get(field), list)
                or any(not str(item or "").strip() for item in sanction[field])
                for field in (
                    "irregularidades_ids",
                    "fundamentos_legais",
                    "responsaveis",
                )
            )
        ):
            raise MinutasGenerationError(
                "A IA não retornou análise sancionatória estruturada válida."
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
        normalized = _sanitize_draft_structure(structure)
        warnings = []
        normalized["identificacao_processual"] = "\n".join(
            _draft_header_lines(target)
        )
        report, foundation = _normalized_report_sections(normalized)
        normalized["relatorio_paragrafos"] = report
        normalized["relatorio"] = "\n\n".join(report)
        normalized["fundamentacao_geral_paragrafos"] = foundation
        normalized["fundamentacao_geral"] = "\n\n".join(foundation)
        normalized["encerramento"] = "É o Parecer."

        requests = [str(item).strip() for item in normalized.get("pedidos", ())]
        opinion_requests = [
            item for item in requests if _PRELIMINARY_OPINION_RE.search(item)
        ]
        account_requests = [
            item
            for item in requests
            if _account_outcomes(item)
            and not _PRELIMINARY_OPINION_RE.search(item)
            and _request_category(item) == 0
        ]
        if _is_prefeituras_target(target):
            government_requests = [
                (item, _government_opinion_outcome(item))
                for item in opinion_requests
                if _government_opinion_outcome(item)
            ]
            primary_requests = []
            if government_requests:
                government = _government_opinion_request(
                    government_requests[0][1], target
                )
                if government:
                    primary_requests.append(government)
                else:
                    warnings.append(
                        "A conclusão de governo está expressa, mas faltam entidade ou "
                        "exercício confirmados para normalizar o parecer prévio; revise "
                        "humanamente antes da assinatura."
                    )
                    primary_requests.append(government_requests[0][0])
            if account_requests:
                primary_requests.append(account_requests[0])
            if primary_requests:
                # Governo vem antes. A gestão só permanece quando há pedido próprio;
                # sanções, recomendações e comunicações não são conclusões principais.
                normalized["pedidos"] = primary_requests + [
                    item for item in requests
                    if item not in opinion_requests and item not in account_requests
                ]
            if len(government_requests) > 1 or len(account_requests) > 1:
                warnings.append(
                    "Foram recebidas conclusões principais repetidas para PCA de "
                    "Prefeitura; foi preservada uma conclusão por competência "
                    "para revisão jurídica."
                )
            if account_requests and not government_requests:
                warnings.append(
                    "PCA de Prefeitura com conclusão apenas de gestão, sem suporte "
                    "documental explícito para parecer prévio sobre contas de governo; "
                    "revise humanamente a competência e o objeto."
                )
            elif not government_requests and not account_requests:
                warnings.append(
                    "PCA de Prefeitura sem conclusão dispositiva identificável; revise "
                    "humanamente a competência e o objeto antes da assinatura."
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
        normalized["pedidos"] = _consolidate_recommendations(
            normalized.get("pedidos", ()), target.get("origem")
        )
        normalized["conclusao_pedidos"], extra_requests = _normalized_device_introduction(
            normalized
        )
        if extra_requests:
            normalized["pedidos"] = _consolidate_recommendations(
                [*normalized["pedidos"], *extra_requests],
                target.get("origem"),
            )
        if _is_rpps_target(target):
            safe_requests = []
            for request in normalized["pedidos"]:
                if _FORBIDDEN_RPPS_PRELIMINARY_REQUEST_RE.search(request):
                    direct_request = _direct_account_request(request)
                    if direct_request:
                        safe_requests.append(direct_request)
                    warnings.append(
                        "Foi removido pedido de emissão de parecer prévio em PCA de "
                        "RPPS; as contas do Instituto estão sujeitas a julgamento."
                    )
                    continue
                safe_requests.append(request)
            damage_state = normalized["analise_sancionatoria"]["dano_ao_erario"]
            if damage_state != "COMPROVADO_E_QUANTIFICADO":
                filtered = []
                for request in safe_requests:
                    positive_debit = (
                        _POSITIVE_DEBIT_REQUEST_RE.search(request)
                        and not _NEGATIVE_REQUEST_RE.search(request)
                    )
                    if positive_debit:
                        warnings.append(
                            "Foi removido pedido de imputação de débito sem dano ao "
                            "erário comprovado e quantificado na análise sancionatória."
                        )
                        continue
                    filtered.append(request)
                safe_requests = filtered
            if not safe_requests:
                safe_requests = [
                    "Submeta-se o dispositivo à revisão jurídica humana quanto à "
                    "classificação das contas."
                ]
            normalized["pedidos"] = _order_rpps_requests(safe_requests)
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
        sanction = structure["analise_sancionatoria"]
        sections.append(
            "### Análise sancionatória\n\n" + str(sanction["texto"]).strip()
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
        sanction = structure["analise_sancionatoria"]
        known_ids = {
            str(item.get("id") or "")
            for item in analysis.get("irregularidades", [])
            if isinstance(item, dict)
        }
        unknown_sanction_ids = sorted(
            set(sanction["irregularidades_ids"]) - known_ids
        )
        if unknown_sanction_ids:
            warnings.append(
                "A análise sancionatória referencia irregularidades inexistentes: "
                + ", ".join(unknown_sanction_ids)
                + "."
            )
        requests = structure.get("pedidos", [])
        positive_multa_requests = [
            request
            for request in requests
            if _MULTA_REQUEST_RE.search(request)
            and not _NEGATIVE_REQUEST_RE.search(request)
        ]
        if sanction["conclusao"] == "MULTA_CABIVEL" and not positive_multa_requests:
            warnings.append(
                "A análise concluiu pelo cabimento de multa, mas o dispositivo não "
                "contém pedido sancionatório autônomo."
            )
        if (
            sanction["conclusao"] != "MULTA_CABIVEL"
            and positive_multa_requests
        ):
            warnings.append(
                "O dispositivo propõe multa sem conclusão sancionatória compatível."
            )
        sanction_text = "\n".join(
            [
                str(sanction["texto"]),
                *map(str, sanction["fundamentos_legais"]),
                *map(str, requests),
            ]
        )
        if (
            sanction["dano_ao_erario"] != "COMPROVADO_E_QUANTIFICADO"
            and re.search(r"\bart(?:igo)?\.?\s*(?:101|294)\b", sanction_text, re.I)
        ):
            warnings.append(
                "Os arts. 101 da LC nº 192/2024 ou 294 do Regimento foram invocados "
                "sem dano ao erário comprovado e quantificado."
            )
        if re.search(r"R\$|\bUFR(?:-PB)?\b|\b\d+(?:[,.]\d+)?\s*%", sanction_text, re.I):
            warnings.append(
                "A análise ou o pedido sancionatório contém valor, percentual ou "
                "UFR-PB; a minuta não deve calcular a multa."
            )
        if re.search(
            r"Minist[eé]rio\s+P[uú]blico\s+de\s+Contas.{0,80}\baplica\w*\b",
            sanction_text,
            re.I | re.S,
        ):
            warnings.append(
                "A redação pode atribuir ao MPC a aplicação da sanção; o MPC opina e "
                "o Tribunal decide."
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

    def generate(
        self,
        target_reference_id,
        analyzed,
        use_rag=True,
        orientacao_juridica=None,
        contexto_orientacao=None,
        usuario="",
    ):
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
        orientacao = self._validar_orientacao_operacao(
            target, analyzed, orientacao_juridica, contexto_orientacao, usuario
        )
        trechos, consulta = self._mesclar_trechos_orientacao(
            target, recovery.get("trechos", ()), orientacao
        ) if use_rag else (list(recovery.get("trechos") or []), "")
        if use_rag and trechos != list(recovery.get("trechos") or []):
            prepared = self.prepare_context(
                target_reference_id,
                analyzed["analise"],
                trechos,
                use_rag=True,
            )
        from services.minutas_orientacao import aplicar_bloco, metadados_orientacao

        analysis = self._validate_analysis(
            analyzed["analise"], prepared["contexto"]
        )
        conference = analysis["conferencia_documental"]
        request_context = deepcopy(prepared["contexto"])
        request_context["analise_estruturada"] = self._analysis_for_generation(
            analysis
        )
        aplicar_bloco(request_context, orientacao, consulta if use_rag else "")
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
        header_metadata = _resolved_header_metadata(
            target,
            prepared["contexto"].get("documentos_processo_alvo", ()),
            analysis,
        )
        header_target = _target_with_header_metadata(target, header_metadata)
        structure, normalization_warnings = self._normalize_structure(
            structure, header_target, prepared["contexto"]
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
            "identificador_minuta": _draft_identifier(target),
            "metadados_cabecalho": header_metadata,
            "advertencias_qualidade": warnings,
            "advertencias_documentais": deepcopy(conference["pendencias"]),
            "validation_policy_version": VALIDATION_POLICY_VERSION,
            "processo_alvo_id": str(target["id"]),
            "processo_alvo_chave": _canonical_process_key(
                target.get("processo_chave") or target.get("numero_processo")
            ),
            "procuradora_chave": str(target.get("procuradora_chave") or ""),
            "orientacao_assessor": metadados_orientacao(orientacao, contexto_orientacao),
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

    def _instruction_profile(self):
        from services.minutas_specialties import (
            instruction_generation_allowed,
            instruction_profile,
        )

        profile = instruction_profile(self.library)
        if not instruction_generation_allowed(profile):
            raise MinutasGenerationError(
                "Esta especialidade ainda não possui motor habilitado."
            )
        return profile

    def _resolver_alvo_instrucao(
        self, *, processo, exercicio=None, origem="", beneficiario="", ato="",
        periodo_fatos="", modalidade_recurso="", materia_originaria="", data_decisao="",
    ):
        from services.minutas_recursos import is_recurso_profile
        from services.minutas_denuncias import is_denuncia_profile
        from services.minutas_aposentadorias import is_aposentadoria_profile

        profile = self._instruction_profile()
        if is_recurso_profile(profile):
            from services.minutas_recursos import build_target
            return build_target(
                profile, processo, origem, modalidade_recurso,
                materia_originaria, data_decisao,
            )
        if is_aposentadoria_profile(profile):
            from services.minutas_aposentadorias import build_target
            return build_target(profile, processo, origem, beneficiario, ato)
        if is_denuncia_profile(profile):
            from services.minutas_denuncias import build_target
            return build_target(profile, processo, origem, periodo_fatos)
        return self._instruction_target(processo, exercicio, origem)

    def campos_orientacao_instrucao(self, analyzed, usuario="", **alvo):
        return self.campos_orientacao(
            self._resolver_alvo_instrucao(**alvo), analyzed, usuario
        )

    def _instruction_target(self, processo, exercicio, origem):
        from services.minutas_specialties import instruction_pca_subtype

        profile = self._instruction_profile()
        key = _canonical_process_key(processo)
        if len(key) != 7:
            raise MinutasGenerationError(
                "Informe o processo TC no formato 00000/00 antes de analisar a instrução."
            )
        return {
            "id": f"instrucao-{key}",
            "numero_processo": f"{key[:5]}/{key[5:]}",
            "processo_chave": key,
            "exercicio": int(exercicio or 0) or None,
            "origem": origem or "",
            "procuradora": self._pessoa().nome_exibicao,
            "procuradora_chave": self._pessoa().chave,
            "especialidade_id": profile["id"],
            "especialidade_id_contextual": profile["id_contextual"],
            "classe": profile.get("grupo") or "",
            "perfil": profile.get("perfil") or "",
            "classificacao_estado": "CLASSIFICADO",
            "subtipo": instruction_pca_subtype(profile),
            "parecer_definitivo": False,
            "instrucao_local": True,
        }

    @staticmethod
    def _bind_instruction_context(context, target):
        bound = deepcopy(context)
        process = dict(bound.get("processo_alvo") or {})
        process.update({
            "especialidade_id": target["especialidade_id"],
            "especialidade_id_contextual": target.get("especialidade_id_contextual") or "",
            "classe": target.get("classe") or "",
            "perfil": target.get("perfil") or "",
            "subtipo": target.get("subtipo") or "",
        })
        for field in ("origem", "responsavel"):
            if str(target.get(field) or "").strip():
                process[field] = target[field]
        bound["processo_alvo"] = process
        return bound

    def _instruction_precedents(self, analysis, process_key):
        """Recupera somente pareceres definitivos de outros processos."""
        plan = _rag_query_plan(analysis)
        queries = plan["consultas"]
        if not queries:
            return {"estado": "SEM_CONSULTAS", "referencias": [], "consultas": []}
        references = []
        seen = set()
        search_method = self.search if callable(self.search) else self.search.search
        try:
            for query in queries:
                response = search_method(
                    query["consulta_principal"],
                    page_size=MAX_RAG_RESULTS_PER_QUERY,
                    exclude_process_key=process_key,
                )
                for row in response.get("resultados") or []:
                    if str(row.get("papel") or "parecer") != "parecer":
                        continue
                    if str(row.get("processo_chave") or "") == process_key:
                        continue
                    identifier = str(row.get("referencia_id") or row.get("id") or "")
                    if identifier in seen:
                        continue
                    seen.add(identifier)
                    references.append({
                        "referencia_id": identifier,
                        "processo": row.get("numero_processo") or row.get("processo") or "",
                        "trecho": _clean_text(
                            row.get("trecho") or row.get("texto") or ""
                        )[:MAX_RAG_PASSAGE_CHARS],
                    })
        except Exception as exc:
            return {
                "estado": "INDISPONIVEL",
                "motivo": (_clean_text(str(exc)) or "Recuperação indisponível.")[:180],
                "referencias": [],
                "consultas": queries,
            }
        return {
            "estado": "RECUPERADO" if references else "SEM_RESULTADO",
            "referencias": references[:MAX_RAG_PASSAGES],
            "consultas": queries,
        }

    def analyze_instruction(
        self, documentos, *, processo, exercicio=None, origem="", beneficiario="", ato="", periodo_fatos="",
        modalidade_recurso="", materia_originaria="", data_decisao="",
    ):
        from services.minutas_recursos import is_recurso_profile
        from services.minutas_denuncias import is_denuncia_profile
        from services.minutas_aposentadorias import is_aposentadoria_profile
        from services.minutas_instrucao import montar_contexto
        self.library._authorize()
        if is_recurso_profile(self._instruction_profile()):
            from services import minutas_recursos as recursos
            return recursos.analyze(
                self, documentos, processo=processo, origem=origem,
                modalidade=modalidade_recurso, materia_originaria=materia_originaria,
                data_decisao=data_decisao,
            )
        if is_aposentadoria_profile(self._instruction_profile()):
            from services import minutas_aposentadorias as retirement
            return retirement.analyze(
                self, documentos, processo=processo, origem=origem,
                beneficiario=beneficiario, ato=ato,
            )
        if is_denuncia_profile(self._instruction_profile()):
            from services import minutas_denuncias as denuncias
            return denuncias.analyze(
                self, documentos, processo=processo, origem=origem, periodo_fatos=periodo_fatos,
            )
        target = self._instruction_target(processo, exercicio, origem)
        prepared = montar_contexto(
            documentos, processo=target["numero_processo"], exercicio=exercicio,
            origem=origem,
        )
        header_metadata = _resolved_header_metadata(
            target, prepared["contexto"].get("documentos_processo_alvo", ())
        )
        target = _target_with_header_metadata(target, header_metadata)
        context = self._bind_instruction_context(prepared["contexto"], target)
        generated = self._analyze(context)
        analysis = self._validate_analysis(generated.get("analise"), context)
        precedents = self._instruction_precedents(analysis, target["processo_chave"])
        conference = analysis.get("conferencia_documental") or {}
        metrics = {
            key: generated[key]
            for key in (
                "modelo", "duracao_ms", "tokens_entrada", "tokens_saida", "tokens_total",
            )
            if isinstance(generated, dict) and generated.get(key) is not None
        }
        return {
            "analise": analysis,
            "pendencias_conferencia": conference.get("pendencias") or [],
            "fontes_versao": prepared["versao"],
            "processo_alvo_id": target["id"],
            "processo_alvo_chave": target["processo_chave"],
            "procuradora_chave": target["procuradora_chave"],
            "fontes_alvo": prepared["fontes_alvo"],
            "referencias_historicas": len(precedents.get("referencias") or []),
            "referencias": precedents,
            "advertencias_instrucao": prepared["advertencias"],
            "consolidacao_contexto": prepared["omissoes"],
            "contexto_bytes": _serialized_size(prepared["contexto"]),
            "metadados_cabecalho": header_metadata,
            **metrics,
        }

    def _instruction_reference_lists(self, precedents):
        """Converte a recuperação no contrato do gerador histórico: lista, não o envelope."""
        opinions = []
        passages = []
        rows = precedents.get("referencias") if isinstance(precedents, dict) else ()
        for item in rows or ():
            if not isinstance(item, dict):
                continue
            passage = str(item.get("trecho") or item.get("trecho_literal") or "").strip()
            process = str(item.get("processo") or "").strip()
            if not passage or not process:
                continue
            page = item.get("pagina")
            page = page if isinstance(page, int) and not isinstance(page, bool) else None
            document = str(item.get("documento") or "Parecer definitivo")
            content = {"texto": passage}
            if page is not None:
                content["pagina"] = page
            opinions.append({
                "processo": process,
                "exercicio": item.get("exercicio") if isinstance(item.get("exercicio"), int) else None,
                "subtipo": str(item.get("subtipo") or ""),
                "uso": "fundamentos jurídicos pertinentes do parecer definitivo",
                "parecer": {
                    "papel": "parecer",
                    "tipo": "Parecer definitivo",
                    "arquivo": document,
                    "conteudo": [content],
                },
            })
            passage_item = {
                "processo": process,
                "documento": document,
                "pagina": page,
                "trecho_literal": passage,
                "natureza": "manifestação histórica não vinculante",
            }
            if item.get("criterio_recuperacao"):
                passage_item["criterio_recuperacao"] = item["criterio_recuperacao"]
            passages.append(passage_item)
        return opinions, passages

    def generate_instruction(
        self, documentos, analyzed, *, processo, exercicio=None, origem="",
        beneficiario="", ato="", periodo_fatos="", modalidade_recurso="",
        materia_originaria="", data_decisao="", use_rag=True,
        orientacao_juridica=None, contexto_orientacao=None, usuario="",
    ):
        from services.minutas_recursos import is_recurso_profile
        from services.minutas_denuncias import is_denuncia_profile
        from services.minutas_aposentadorias import is_aposentadoria_profile
        from services.minutas_instrucao import montar_contexto
        self.library._authorize()
        orientacao_kwargs = {
            "orientacao_juridica": orientacao_juridica,
            "contexto_orientacao": contexto_orientacao,
            "usuario": usuario,
        }
        if is_recurso_profile(self._instruction_profile()):
            from services import minutas_recursos as recursos
            return recursos.generate(
                self, documentos, analyzed, processo=processo, origem=origem,
                modalidade=modalidade_recurso, materia_originaria=materia_originaria,
                data_decisao=data_decisao, use_rag=use_rag, **orientacao_kwargs,
            )
        if is_aposentadoria_profile(self._instruction_profile()):
            from services import minutas_aposentadorias as retirement
            return retirement.generate(
                self, documentos, analyzed, processo=processo, origem=origem,
                beneficiario=beneficiario, ato=ato, use_rag=use_rag, **orientacao_kwargs,
            )
        if is_denuncia_profile(self._instruction_profile()):
            from services import minutas_denuncias as denuncias
            return denuncias.generate(
                self, documentos, analyzed, processo=processo, origem=origem,
                periodo_fatos=periodo_fatos, use_rag=use_rag, **orientacao_kwargs,
            )
        target = self._instruction_target(processo, exercicio, origem)
        prepared = montar_contexto(
            documentos, processo=target["numero_processo"], exercicio=exercicio,
            origem=origem,
        )
        if not isinstance(analyzed, dict) or analyzed.get("fontes_versao") != prepared["versao"]:
            raise MinutasGenerationError(
                "A instrução foi alterada. Analise novamente antes de gerar a minuta."
            )
        orientacao = self._validar_orientacao_operacao(
            target, analyzed, orientacao_juridica, contexto_orientacao, usuario
        )
        analysis = self._validate_analysis(analyzed.get("analise"), prepared["contexto"])
        conference = analysis.get("conferencia_documental") or {}
        header_metadata = _resolved_header_metadata(
            target,
            prepared["contexto"].get("documentos_processo_alvo", ()),
            analysis,
        )
        target = _target_with_header_metadata(target, header_metadata)
        context = self._bind_instruction_context(prepared["contexto"], target)
        context["orientacao_dispositivo"] = (
            "PREFEITURA_CONDICIONAL_DUAS_COMPETENCIAS"
            if target.get("subtipo") == "Prefeitura"
            else "DEMAIS_PCA_JULGAMENTO_DIRETO"
        )
        context["analise_estruturada"] = self._analysis_for_generation(analysis)
        if conference.get("pendencias"):
            context["orientacao_conferencia_documental"] = (
                "Existem pontos não verificados. Eles não impedem a minuta, mas "
                "não podem ser tratados como prova confirmada. Preserve cada "
                "irregularidade e use formulação cautelosa para revisão humana."
            )
        from services.minutas_orientacao import aplicar_bloco, metadados_orientacao

        referencias, consulta = (
            self._enriquecer_referencias_instrucao(
                analyzed.get("referencias"), orientacao, target
            )
            if use_rag else (analyzed.get("referencias"), "")
        )
        aplicar_bloco(context, orientacao, consulta if use_rag else "")
        opinions, passages = ([], [])
        if use_rag:
            opinions, passages = self._instruction_reference_lists(referencias)
            context["referencias_historicas"] = opinions
            context["referencias_juridicas_recuperadas"] = passages
            context["orientacao_referencias_juridicas"] = (
                "Manifestações históricas não vinculantes; são fundamentos jurídicos "
                "e não são fatos do processo-alvo. Se o parecer for pouco fundamentado "
                "ou remeter a documento ausente, indique a limitação e não invente a "
                "análise técnica. A ausência de precedente pertinente não autoriza "
                "fundamento inadequado. Precedentes recuperados pela orientação do "
                "Assessor podem convergir ou divergir; não trate divergência como "
                "entendimento favorável."
            )
        while _serialized_size(context) > MAX_CONTEXT_BYTES and context.get(
            "referencias_juridicas_recuperadas"
        ):
            context["referencias_juridicas_recuperadas"].pop()
        while _serialized_size(context) > MAX_CONTEXT_BYTES and context.get(
            "referencias_historicas"
        ):
            context["referencias_historicas"].pop()
        if _serialized_size(context) > MAX_CONTEXT_BYTES:
            raise MinutasGenerationError(
                "A solicitação integral excede o limite seguro de contexto."
            )
        generated = self._generate(context)
        if not isinstance(generated, dict):
            raise MinutasGenerationError("A IA não retornou uma minuta válida.")
        structure, _warnings = self._normalize_structure(
            self._validate_structure(generated.get("estrutura")), target, context,
        )
        structure = self._validate_structure(structure)
        metrics = {
            key: generated[key]
            for key in (
                "modelo", "duracao_ms", "tokens_entrada", "tokens_saida", "tokens_total",
            )
            if generated.get(key) is not None
        }
        return {
            "estrutura": structure,
            "texto": self._structure_markdown(structure),
            "fontes_versao": prepared["versao"],
            "processo_alvo_id": target["id"],
            "processo_alvo_chave": target["processo_chave"],
            "procuradora_chave": target["procuradora_chave"],
            "metadados_cabecalho": header_metadata,
            "fontes_alvo": prepared["fontes_alvo"],
            "advertencias_documentais": deepcopy(conference.get("pendencias") or []),
            "referencias_historicas": len(context.get("referencias_historicas") or []),
            "referencias_juridicas_enviadas": len(
                context.get("referencias_juridicas_recuperadas") or []
            ),
            "orientacao_assessor": metadados_orientacao(orientacao, contexto_orientacao),
            **metrics,
        }
