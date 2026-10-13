"""Local, traceable text extraction for the private Minutas IA library."""

from hashlib import sha256
from io import BytesIO
import json
import re
import unicodedata
import uuid

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pypdf import PdfReader

from database.store import now
from services.minutas_library import MinutasValidationError
from services.oficios import MAX_FILE


EXTRACTOR_VERSION = "minutas-local-2"
MIN_USABLE_CHARACTERS = 50
PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class MinutasExtractionError(RuntimeError):
    pass


def _pdf_content(content):
    reader = PdfReader(BytesIO(content))
    units = []
    texts = []
    warnings = []
    for number, page in enumerate(reader.pages, 1):
        try:
            text = page.extract_text() or ""
            unit = {"pagina": number, "texto": text}
        except Exception:
            text = ""
            message = f"Falha na extração da página {number}."
            unit = {"pagina": number, "texto": text, "advertencia": message}
            warnings.append({"mensagem": message, "critica": True})
        units.append(unit)
        texts.append(text)
    return "\n".join(texts), units, warnings


def _docx_content(content):
    document = Document(BytesIO(content))
    units = []
    texts = []
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            text = Paragraph(child, document).text
            kind = "paragrafo"
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            text = "\n".join(
                "\t".join(cell.text for cell in row.cells) for row in table.rows
            )
            kind = "tabela"
        else:
            continue
        units.append({"ordem": len(units) + 1, "tipo": kind, "texto": text})
        texts.append(text)
    return "\n".join(texts), units, []


_SECTION_HEADINGS = {
    "auditoria": {
        "informacoes_processuais": ("relatório", "processo", "introdução"),
        "achados": ("achados", "achados de auditoria"),
        "irregularidades": ("irregularidades", "falhas constatadas"),
        "manifestacao_defesa": ("defesa", "manifestação da defesa"),
        "conclusao_tecnica": ("conclusão", "conclusão técnica"),
    },
    "complementar": {
        "questoes": ("questões", "irregularidades", "análise"),
        "manifestacao_defesa": ("defesa", "manifestação da defesa"),
        "solicitacoes": ("solicitações", "diligências", "complementação"),
        "conclusao": ("conclusão",),
    },
    "parecer": {
        "relatorio": ("relatório",),
        "fundamentacao": ("fundamentação", "fundamentos"),
        "analise_irregularidades": ("análise", "irregularidades"),
        "conclusao_ministerial": ("conclusão", "voto", "parecer"),
    },
}


def _normalize_heading(value):
    return re.sub(r"\s+", " ", value.strip().rstrip(":").casefold())


_OPINION_MARKERS = (
    ("ementa", re.compile(r"\bementa\s*:", re.IGNORECASE)),
    (
        "transicao_fundamentacao",
        re.compile(
            r"\b[eé]\s+o\s+relat[oó]rio\s*\.\s*passo\s+a\s+opinar\s*\.?",
            re.IGNORECASE,
        ),
    ),
    (
        "conclusao_ministerial",
        re.compile(r"\bante\s+o\s+exposto\b", re.IGNORECASE),
    ),
    (
        "fecho_parecer",
        re.compile(r"\b[eé]\s+o\s+parecer\s*\.?", re.IGNORECASE),
    ),
)


def _first_marker(text, name, after=0):
    pattern = dict(_OPINION_MARKERS)[name]
    return pattern.search(text, after)


def _opinion_sections(text):
    """Map only boundaries explicitly signalled by the opinion's own wording."""
    ementa = _first_marker(text, "ementa")
    transition = _first_marker(text, "transicao_fundamentacao")
    conclusion = _first_marker(text, "conclusao_ministerial")
    closing = _first_marker(text, "fecho_parecer")
    sections = []

    report_start = 0
    if ementa:
        paragraph_end = re.search(r"(?:\r?\n)\s*(?:\r?\n)", text[ementa.end() :])
        if paragraph_end:
            ementa_end = ementa.end() + paragraph_end.end()
        else:
            line_end = re.search(r"\r?\n", text[ementa.end() :])
            ementa_end = ementa.end() + line_end.start() if line_end else len(text)
        sections.append(
            {
                "secao": "ementa",
                "titulo_original": ementa.group(0),
                "texto": text[ementa.start() : ementa_end],
            }
        )
        report_start = ementa_end

    if transition and report_start < transition.start() and text[
        report_start : transition.start()
    ].strip():
        sections.append(
            {
                "secao": "relatorio",
                "titulo_original": transition.group(0),
                "texto": text[report_start : transition.start()],
            }
        )

    if transition:
        grounds_end = conclusion.start() if conclusion else (
            closing.start() if closing else len(text)
        )
        if transition.start() < grounds_end:
            sections.append(
                {
                    "secao": "fundamentacao",
                    "titulo_original": transition.group(0),
                    "texto": text[transition.start() : grounds_end],
                }
            )

    conclusion_start = conclusion.start() if conclusion else (
        closing.start() if closing else None
    )
    if conclusion_start is not None:
        marker = conclusion or closing
        sections.append(
            {
                "secao": "conclusao_ministerial",
                "titulo_original": marker.group(0),
                "texto": text[conclusion_start:],
            }
        )
    return sections


def _legal_sections(text, role):
    """Recognize only explicit, standalone headings already present in the text."""
    if role == "parecer":
        opinion_sections = _opinion_sections(text)
        if opinion_sections:
            return opinion_sections
    aliases = _SECTION_HEADINGS.get(role, {})
    starts = []
    offset = 0
    for line in text.splitlines(keepends=True):
        normalized = _normalize_heading(line)
        for section, headings in aliases.items():
            if normalized in headings:
                starts.append((offset, section, line.strip()))
                break
        offset += len(line)
    sections = []
    for index, (start, section, heading) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        sections.append(
            {
                "secao": section,
                "titulo_original": heading,
                "texto": text[start:end],
            }
        )
    return sections


def _plain_text(value):
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    )


def _text_fingerprint(value):
    normalized = re.sub(r"\s+", " ", _plain_text(str(value or ""))).strip()
    return sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _is_non_substantive_line(line):
    normalized = re.sub(r"\s+", " ", _plain_text(line)).strip(" -–—|:;,.()[]")
    if not normalized:
        return True
    if re.fullmatch(r"(?:pagina\s*)?\d+(?:\s*(?:de|/|-|–)\s*\d+)?", normalized):
        return True
    markers = (
        "tramita",
        "verifique a autenticidade",
        "validacao",
        "codigo verificador",
        "documento assinado eletronicamente",
        "documento assinado digitalmente",
        "assinatura eletronic",
        "assinatura digital",
        "autenticacao do documento",
    )
    return any(marker in normalized for marker in markers)


def _substantive_characters(text):
    substantive = "\n".join(
        line for line in text.splitlines() if not _is_non_substantive_line(line)
    )
    return len(re.sub(r"\s+", "", substantive))


def _pdf_quality_warnings(units):
    warnings = []
    last_page = len(units)
    for unit in units:
        if unit.get("advertencia"):
            continue
        text = unit["texto"]
        if _substantive_characters(text):
            continue
        page = unit["pagina"]
        if not re.sub(r"\s+", "", text):
            message = f"A página {page} não contém texto extraível."
        else:
            message = (
                f"A página {page} contém apenas elementos de validação, "
                "assinatura ou paginação."
            )
        warnings.append(
            {
                "mensagem": message,
                "critica": page != last_page,
            }
        )
    return warnings


def extract_content(content, mime, role):
    if not isinstance(content, bytes) or not content or len(content) > MAX_FILE:
        raise MinutasExtractionError("Arquivo vazio ou superior a 10 MB.")
    try:
        if mime == PDF_MIME:
            text, units, warnings = _pdf_content(content)
        elif mime == DOCX_MIME:
            text, units, warnings = _docx_content(content)
        else:
            raise MinutasExtractionError("Formato de arquivo não suportado.")
    except MinutasExtractionError:
        raise
    except Exception as exc:
        raise MinutasExtractionError("Não foi possível ler o arquivo.") from exc
    if mime == PDF_MIME:
        warnings.extend(_pdf_quality_warnings(units))
        usable = _substantive_characters(text)
    else:
        usable = len(re.sub(r"\s+", "", text))
    if usable == 0:
        warnings.append(
            {
                "mensagem": "Nenhum texto substantivo extraível foi encontrado; OCR não está disponível.",
                "critica": True,
            }
        )
    elif usable < MIN_USABLE_CHARACTERS:
        warnings.append(
            {
                "mensagem": "O texto substantivo extraído é insuficiente e necessita conferência.",
                "critica": True,
            }
        )
    warning = " ".join(dict.fromkeys(item["mensagem"] for item in warnings))
    return {
        "status": (
            "ATENCAO"
            if any(item["critica"] for item in warnings)
            else "PROCESSADO"
        ),
        "texto_integral": text,
        "unidades": units,
        "secoes": _legal_sections(text, role),
        "erro": warning,
    }


class MinutasExtractionService:
    def __init__(self, library):
        self.library = library
        self.repository = library.repository
        self.storage = library.storage

    def _documents(self, reference):
        documents = [
            {
                "documento_id": None,
                "papel": "auditoria",
                "tipo": "Relatório da Auditoria",
                "posicao": 0,
                "nome": reference["auditoria_nome"],
                "mime": reference["auditoria_mime"],
                "tamanho": reference["auditoria_tamanho"],
                "path": reference["auditoria_path"],
            }
        ]
        for document in self.repository.list_documents(reference["id"]):
            documents.append(
                {
                    "documento_id": document["id"],
                    "papel": "complementar",
                    "tipo": document["tipo"],
                    "posicao": int(document["posicao"]),
                    "nome": document["nome"],
                    "mime": document["mime"],
                    "tamanho": document["tamanho"],
                    "path": document["path"],
                }
            )
        documents.append(
            {
                "documento_id": None,
                "papel": "parecer",
                "tipo": "Parecer definitivo",
                "posicao": None,
                "nome": reference["parecer_nome"],
                "mime": reference["parecer_mime"],
                "tamanho": reference["parecer_tamanho"],
                "path": reference["parecer_path"],
            }
        )
        return documents

    def statuses(self, reference_id):
        self.library.get(reference_id)
        return {
            row["arquivo_path"]: row
            for row in self.repository.list_extractions(reference_id)
        }

    def process_reference(self, reference_id, force_paths=None):
        actor = self.library._authorize()
        reference = self.library.get(reference_id)
        forced = set(force_paths or ())
        return [
            self._process_document(
                reference, document, actor, force=document["path"] in forced
            )
            for document in self._documents(reference)
        ]

    def _process_document(self, reference, document, actor, force=False):
        content = None
        existing = self.repository.get_extraction(document["path"])
        try:
            content = self.storage.download(document["path"])
            digest = sha256(content).hexdigest()
            if (
                not force
                and existing
                and existing["arquivo_sha256"] == digest
                and existing["extrator_versao"] == EXTRACTOR_VERSION
            ):
                return {**existing, "reutilizado": True}
            extracted = extract_content(content, document["mime"], document["papel"])
        except MinutasExtractionError as exc:
            digest = sha256(content).hexdigest() if content else "0" * 64
            extracted = {
                "status": "ATENCAO",
                "texto_integral": "",
                "unidades": [],
                "secoes": [],
                "erro": str(exc),
            }
        except Exception:
            digest = "0" * 64
            extracted = {
                "status": "ATENCAO",
                "texto_integral": "",
                "unidades": [],
                "secoes": [],
                "erro": "Não foi possível obter o arquivo privado.",
            }
        stamp = now()
        record = {
            "id": existing["id"] if existing else str(uuid.uuid4()),
            "referencia_id": reference["id"],
            "documento_id": document["documento_id"],
            "papel": document["papel"],
            "tipo": document["tipo"],
            "posicao": document["posicao"],
            "procuradora": reference["procuradora"],
            "procuradora_chave": reference["procuradora_chave"],
            "arquivo_nome": document["nome"],
            "arquivo_mime": document["mime"],
            "arquivo_tamanho": document["tamanho"],
            "arquivo_path": document["path"],
            "arquivo_sha256": digest,
            "extrator_versao": EXTRACTOR_VERSION,
            "status": extracted["status"],
            "texto_integral": extracted["texto_integral"],
            "unidades_json": json.dumps(extracted["unidades"], ensure_ascii=False),
            "secoes_json": json.dumps(extracted["secoes"], ensure_ascii=False),
            "erro": extracted["erro"],
            "processado_em": stamp,
            "processado_por": actor,
            "atualizado_em": stamp,
        }
        return self.repository.upsert_extraction(record)

    def reprocess_document(self, reference_id, path):
        actor = self.library._authorize()
        reference = self.library.get(reference_id)
        document = next(
            (item for item in self._documents(reference) if item["path"] == path),
            None,
        )
        if not document:
            raise MinutasValidationError("Documento não encontrado nesta referência.")
        return [self._process_document(reference, document, actor, force=True)]

    def view(self, reference_id, path):
        reference = self.library.get(reference_id)
        if path not in {item["path"] for item in self._documents(reference)}:
            raise MinutasValidationError("Documento não encontrado nesta referência.")
        row = self.repository.get_extraction(path)
        if not row:
            raise MinutasValidationError("O documento ainda não foi processado.")
        return {
            **row,
            "unidades": json.loads(row["unidades_json"]),
            "secoes": json.loads(row["secoes_json"]),
        }

    def context_sources(self, target_reference_id):
        target = self.library.get(target_reference_id)
        rows = self.repository.list_extractions_by_procuradora(
            target["procuradora_chave"]
        )
        stored_target_opinion = self.repository.get_extraction(target["parecer_path"])
        target_opinions = [
            row
            for row in rows
            if str(row["referencia_id"]) == str(target_reference_id)
            and (
                row["papel"] == "parecer"
                or row["arquivo_path"] == target["parecer_path"]
            )
        ]
        if stored_target_opinion and not any(
            row["arquivo_path"] == stored_target_opinion["arquivo_path"]
            for row in target_opinions
        ):
            target_opinions.append(stored_target_opinion)
        forbidden_paths = {target["parecer_path"]}
        forbidden_hashes = {
            row.get("arquivo_sha256")
            for row in target_opinions
            if row.get("arquivo_sha256")
        }
        forbidden_texts = {
            fingerprint
            for row in target_opinions
            if (fingerprint := _text_fingerprint(row.get("texto_integral")))
        }
        return [
            row
            for row in rows
            if row["arquivo_path"] not in forbidden_paths
            and row.get("arquivo_sha256") not in forbidden_hashes
            and _text_fingerprint(row.get("texto_integral")) not in forbidden_texts
            and not (
                str(row["referencia_id"]) == str(target_reference_id)
                and row["papel"] == "parecer"
            )
        ]
