"""In-memory context selection and experimental Minutas IA generation."""

from collections import defaultdict
from hashlib import sha256
import json
import re
import unicodedata

from services import ai_service
from services.minutas_extraction import EXTRACTOR_VERSION, MinutasExtractionService
from services.minutas_library import MinutasValidationError, PROCURADORA


HISTORICAL_TEST_PROCESS_KEY = "0165326"
MAX_CONTEXT_BYTES = ai_service.MINUTA_MAX_CONTEXT_BYTES
MAX_HISTORICAL_REFERENCES = 3
MAX_HISTORICAL_AUDITS = 1
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
    return keys == {HISTORICAL_TEST_PROCESS_KEY}


def _terms(value):
    return {
        token
        for token in re.findall(r"[a-z0-9]{4,}", _plain(value))
        if token not in _STOPWORDS
    }


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

    def __init__(self, library, analyze=None, generate=None):
        self.library = library
        self.extractor = MinutasExtractionService(library)
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
                "Nesta fase, somente o processo histórico de Taperoá está habilitado."
            )
        if target.get("procuradora") != PROCURADORA:
            raise MinutasGenerationError("Procuradora não autorizada para este teste.")

    def _source_state(self, target):
        self._validate_target(target)
        records_list = [
            record
            for record in self.library.list()
            if record.get("procuradora_chave") == target["procuradora_chave"]
        ]
        records = {str(record["id"]): record for record in records_list}
        inventory = self.library.repository.list_extraction_inventory_by_procuradora(
            target["procuradora_chave"]
        )
        by_path = {row["arquivo_path"]: row for row in inventory}
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
        }

    def source_summary(self, target_reference_id):
        self.library._authorize()
        target = self.library.get(target_reference_id)
        state = self._source_state(target)
        return {key: value for key, value in state.items() if key != "records"}

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
                "O relatório da Auditoria de Taperoá não possui extração processada."
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

    def prepare_context(self, target_reference_id, analysis=None):
        target, state, _rows, grouped, target_sources = self._target_bundle(
            target_reference_id
        )
        records = state["records"]

        target_text = "\n".join(
            unit["texto"] for source in target_sources for unit in source["conteudo"]
        )
        analysis_text = json.dumps(analysis or {}, ensure_ascii=False)
        target_terms = _terms(target_text)
        theme_terms = _terms(analysis_text) if analysis else target_terms
        candidates = []
        for reference_id, source_rows in grouped.items():
            if reference_id == str(target_reference_id):
                continue
            record = records[reference_id]
            source_rows = [
                row
                for row in source_rows
                if self._valid_extraction_row(row, record)
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
        if _serialized_size(context) > MAX_CONTEXT_BYTES:
            raise MinutasGenerationError(
                "Os documentos essenciais e a análise estruturada excedem o limite de contexto."
            )

        omitted = []
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
        selected_by_process = {
            item["processo"]: item for item in context["referencias_historicas"]
        }
        historical_audits = 0
        for candidate in candidates:
            audit = candidate["auditoria"]
            reference = selected_by_process.get(candidate["record"]["numero_processo"])
            if (
                audit is None
                or reference is None
                or historical_audits >= MAX_HISTORICAL_AUDITS
                or (
                    candidate["pertinencia_tematica"] == 0
                    and candidate["similaridade_documental"] == 0
                    and candidate["record"]["subtipo"] != target["subtipo"]
                )
            ):
                continue
            proposed_reference = {**reference, "relatorio_auditoria": audit}
            proposed_history = [
                proposed_reference if item is reference else item
                for item in context["referencias_historicas"]
            ]
            proposed = {**context, "referencias_historicas": proposed_history}
            if _serialized_size(proposed) <= MAX_CONTEXT_BYTES:
                context = proposed
                selected_by_process[reference["processo"]] = proposed_reference
                historical_audits += 1
            else:
                omitted.append(
                    f"Relatório histórico {candidate['record']['numero_processo']}"
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
            if audit := reference.get("relatorio_auditoria"):
                used_sources.append(
                    f"{reference['processo']} — {audit['tipo']} ({audit['arquivo']})"
                )
        return {
            "contexto": context,
            "contexto_bytes": context_bytes,
            "fontes_alvo": len(target_sources),
            "referencias_historicas": len(context["referencias_historicas"]),
            "relatorios_historicos": sum(
                "relatorio_auditoria" in item
                for item in context["referencias_historicas"]
            ),
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
        if not isinstance(topics, list) or any(
            not isinstance(topic, dict)
            or not str(topic.get("titulo") or "").strip()
            or not isinstance(topic.get("irregularidades_ids"), list)
            or not str(topic.get("texto") or "").strip()
            for topic in topics
        ):
            raise MinutasGenerationError(
                "A IA não retornou fundamentação estruturada válida."
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
        sections.extend(
            (
                "## Conclusão e propostas de encaminhamento\n\n"
                + structure["conclusao_pedidos"].strip(),
                structure["encerramento"].strip(),
            )
        )
        return "\n\n".join(sections)

    @staticmethod
    def _quality_warnings(structure, analysis, target_context):
        warnings = []
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
        valid_pages = set().union(*MinutasGenerationService._source_pages(target_context).values())
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
        return {
            **generated,
            "analise": analysis,
            "suficiente_para_geracao": conference["suficiente_para_geracao"],
            "pendencias_conferencia": conference["pendencias"],
            "fontes_versao": prepared["fontes_versao"],
            "fontes_alvo": prepared["fontes_alvo"],
        }

    def generate(self, target_reference_id, analyzed):
        self.library._authorize()
        if not isinstance(analyzed, dict) or not isinstance(analyzed.get("analise"), dict):
            raise MinutasGenerationError("Analise os fundamentos antes de gerar a minuta.")
        prepared = self.prepare_context(target_reference_id, analyzed["analise"])
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
