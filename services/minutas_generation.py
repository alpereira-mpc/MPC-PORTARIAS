"""In-memory context selection and experimental Minutas IA generation."""

from collections import defaultdict
from hashlib import sha256
import json
import re
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

    def targets(self):
        return [
            record
            for record in self.library.list()
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
        records_list = [
            record
            for record in self.library.list()
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
        return {
            "records": records,
            "issues": issues,
            "fontes_alvo_previstas": len(required),
            "referencias_historicas_disponiveis": len(historical_ids),
            "fontes_versao": revision,
            "_parecer_alvo_hashes": target_opinion_hashes,
        }

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
        return {
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
        }
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
            "referencias_historicas": [],
        }
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
            elif page not in page_texts[source_id]:
                reason = "página não localizada na fonte indicada"
            elif not isinstance(excerpt, str) or not excerpt.strip():
                reason = "trecho de suporte ausente"
            else:
                verified, reason = _continuous_word_sequence(
                    excerpt, page_texts[source_id][page]
                )
                if verified:
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
                        "bloqueante": not has_verified,
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
                                "bloqueante": True,
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
        blocking = [item for item in pending if item["bloqueante"]]
        analysis["conferencia_documental"] = {
            "estado": "PENDENTE" if pending else "VERIFICADA",
            "suficiente_para_geracao": not blocking,
            "pendencias": pending,
        }
        return analysis

    @classmethod
    def _conference_diagnostics(cls, analysis, context):
        page_texts = cls._source_page_texts(context)
        source_names = {
            source["fonte_id"]: source.get("arquivo") or source["fonte_id"]
            for source in context["documentos_processo_alvo"]
        }
        references = defaultdict(list)
        category_fields = (
            ("fatos_documentados", "Fato documentado"),
            ("normas_mencionadas", "Norma mencionada"),
            ("argumentos_defesa", "Alegação defensiva"),
        )
        for irregularity in analysis.get("irregularidades", ()):
            identifier = str(irregularity.get("id") or "")
            for field, category in category_fields:
                for item in irregularity.get(field, ()):
                    references[(identifier, category)].extend(item.get("fontes") or ())
            audit = irregularity.get("avaliacao_auditoria") or {}
            references[(identifier, "Conclusão da Auditoria")].extend(
                audit.get("fontes") or ()
            )

        diagnostics = []
        conference = analysis.get("conferencia_documental") or {}
        for issue in conference.get("pendencias", ()):
            candidates = references.get(
                (str(issue.get("irregularidade") or ""), issue.get("categoria")),
                (),
            )
            reference = next(
                (
                    item
                    for item in candidates
                    if item.get("falha_verificacao") == issue.get("natureza")
                ),
                candidates[0] if candidates else {},
            )
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
                    "motivo": issue.get("natureza"),
                    "bloqueante": bool(issue.get("bloqueante")),
                }
            )
        return diagnostics

    def pilot_diagnostics(self, target_reference_id, analyzed):
        self.library._authorize()
        target = self.library.get(target_reference_id)
        self._validate_target(target)
        if not isinstance(analyzed, dict):
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
        self.library._authorize()
        target = self.library.get(target_reference_id)
        self._validate_target(target)
        if not isinstance(analyzed, dict) or not isinstance(
            analyzed.get("analise"), dict
        ):
            raise MinutasGenerationError(
                "Execute novamente Analisar fundamentos para atualizar a pesquisa jurídica."
            )
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
        for topic in topics:
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
    def _structure_markdown(structure):
        sections = [
            structure["identificacao_processual"].strip(),
            "## Ementa\n\n" + structure["ementa"].strip(),
            "## Relatório\n\n" + structure["relatorio"].strip(),
            "## Fundamentação\n\n" + structure["fundamentacao_geral"].strip(),
        ]
        for topic in structure["topicos_irregularidades"]:
            if isinstance(topic, dict) and str(topic.get("texto") or "").strip():
                sections.append(
                    "### "
                    + str(topic.get("titulo") or "Irregularidade").strip()
                    + "\n\n"
                    + str(topic["texto"]).strip()
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
        for amount in set(re.findall(r"R\$\s*[\d.]+(?:,\d{2})?", text)):
            if re.sub(r"\s+", "", amount) not in re.sub(r"\s+", "", source_text):
                warnings.append(
                    f"Possível valor sem correspondência literal nas fontes: {amount}."
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
        def declared_outcome(value):
            normalized = _plain(value)
            irregular = bool(re.search(r"\birregular(?:idade|es)?\b", normalized))
            regular = bool(re.search(r"\bregular(?:idade|es)?\b", normalized))
            if irregular and not regular:
                return "irregular"
            if regular and not irregular and "ressalva" not in normalized:
                return "regular"
            return None

        foundation = "\n".join(
            [structure["fundamentacao_geral"]]
            + [
                str(topic.get("texto") or "")
                for topic in structure.get("topicos_irregularidades", [])
                if isinstance(topic, dict)
            ]
        )
        outcomes = [
            declared_outcome(structure["ementa"]),
            declared_outcome(foundation),
            declared_outcome(structure["conclusao_pedidos"]),
        ]
        if "regular" in outcomes and "irregular" in outcomes:
            warnings.append(
                "Possível inconsistência entre a ementa, a fundamentação e a conclusão "
                "quanto ao resultado."
            )
        return warnings

    def analyze(self, target_reference_id):
        self.library._authorize()
        prepared = self.prepare_analysis_context(target_reference_id)
        generated = self._analyze(prepared["contexto"])
        if not isinstance(generated, dict):
            raise MinutasGenerationError("A IA não retornou análise jurídica válida.")
        analysis = self._validate_analysis(generated.get("analise"), prepared["contexto"])
        conference = analysis["conferencia_documental"]
        conference_diagnostics = self._conference_diagnostics(
            analysis, prepared["contexto"]
        )
        legal_recovery = self._recover_legal_references(
            target_reference_id, analysis
        )
        return {
            **generated,
            "analise": analysis,
            "suficiente_para_geracao": conference["suficiente_para_geracao"],
            "pendencias_conferencia": conference["pendencias"],
            "fontes_versao": prepared["fontes_versao"],
            "fontes_alvo": prepared["fontes_alvo"],
            "diagnostico_conferencia": conference_diagnostics,
            "recuperacao_juridica": legal_recovery,
            "rag_recovery_version": RAG_RECOVERY_VERSION,
        }

    def generate(self, target_reference_id, analyzed, use_rag=True):
        self.library._authorize()
        if not isinstance(analyzed, dict) or not isinstance(analyzed.get("analise"), dict):
            raise MinutasGenerationError("Analise os fundamentos antes de gerar a minuta.")
        recovery = analyzed.get("recuperacao_juridica") or {}
        if use_rag and (
            analyzed.get("rag_recovery_version") != RAG_RECOVERY_VERSION
            or recovery.get("versao") != RAG_RECOVERY_VERSION
        ):
            raise MinutasGenerationError(
                "Atualize a recuperação jurídica antes de gerar a minuta."
            )
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
        if not conference["suficiente_para_geracao"]:
            details = []
            for issue in (
                item for item in conference["pendencias"] if item["bloqueante"]
            ):
                location = issue["documento"]
                if issue["pagina"] is not None:
                    location += f", p. {issue['pagina']}"
                details.append(
                    f"{issue['irregularidade']} — {issue['categoria']} — "
                    f"{location}: {issue['natureza']}"
                )
            raise MinutasGenerationError(
                "Confira as evidências antes de gerar a minuta: "
                + "; ".join(details[:3])
                + "."
            )
        request_context = prepared["contexto"]
        if _serialized_size(request_context) > MAX_CONTEXT_BYTES:
            raise MinutasGenerationError(
                "A solicitação integral excede o limite seguro de contexto."
            )
        generated = self._generate(request_context)
        if not isinstance(generated, dict):
            raise MinutasGenerationError("A IA não retornou uma minuta válida.")
        structure = self._validate_structure(generated.get("estrutura"))
        warnings = self._quality_warnings(
            structure, analyzed["analise"], prepared["contexto"]
        )
        return {
            **generated,
            "estrutura": structure,
            "texto": self._structure_markdown(structure),
            "advertencias_qualidade": warnings,
            **{key: value for key, value in prepared.items() if key != "contexto"},
        }
