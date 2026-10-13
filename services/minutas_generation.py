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
    """Build a bounded legal context and make one call through the shared AI layer."""

    def __init__(self, library, generate=None):
        self.library = library
        self.extractor = MinutasExtractionService(library)
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
            elif extraction.get("arquivo_sha256") == "0" * 64:
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

    def prepare_context(self, target_reference_id):
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

        target_text = "\n".join(
            unit["texto"] for source in target_sources for unit in source["conteudo"]
        )
        target_terms = _terms(target_text)
        candidates = []
        for reference_id, source_rows in grouped.items():
            if reference_id == str(target_reference_id):
                continue
            record = records[reference_id]
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
            similarity = len(target_terms & _terms(comparison_text))
            candidates.append(
                {
                    "record": record,
                    "parecer": opinion,
                    "auditoria": audit,
                    "similaridade_lexical": similarity,
                }
            )
        candidates.sort(
            key=lambda item: (
                item["record"]["subtipo"] != target["subtipo"],
                -item["similaridade_lexical"],
                -int(item["record"]["exercicio"]),
                item["record"]["numero_processo"],
            )
        )
        candidates = candidates[:MAX_HISTORICAL_REFERENCES]
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
        if _serialized_size(context) > MAX_CONTEXT_BYTES:
            raise MinutasGenerationError(
                "Os documentos essenciais do processo-alvo excedem o limite de contexto."
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
        if not context["referencias_historicas"]:
            raise MinutasGenerationError(
                "Os pareceres históricos excedem o limite seguro de contexto."
            )

        selected_by_process = {
            item["processo"]: item for item in context["referencias_historicas"]
        }
        for candidate in candidates:
            audit = candidate["auditoria"]
            reference = selected_by_process.get(candidate["record"]["numero_processo"])
            if audit is None or reference is None:
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

    def generate(self, target_reference_id):
        self.library._authorize()
        prepared = self.prepare_context(target_reference_id)
        generated = self._generate(prepared["contexto"])
        if (
            not isinstance(generated, dict)
            or not str(generated.get("texto") or "").strip()
        ):
            raise MinutasGenerationError("A IA não retornou uma minuta válida.")
        return {
            **generated,
            **{key: value for key, value in prepared.items() if key != "contexto"},
        }
