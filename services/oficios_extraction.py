"""Local extraction of received-ofício metadata from searchable PDFs."""

from datetime import date
from io import BytesIO
import re
from pypdf import PdfReader
from services.oficios import MONTHS, MAX_FILE, normalized

MIN_LETTERS = 40
MAX_PAGES = 30
MAX_TEXT = 80000
EMPTY = {
    "numero_externo": "",
    "remetente": "",
    "cargo_remetente": "",
    "instituicao": "",
    "assunto": "",
    "processo": "",
    "data": None,
    "texto_extraido": "",
    "texto_suficiente": False,
}
FIELD_START = re.compile(
    r"(?i)^\s*(assunto|refer[eê]ncia|ref\.?|processo|of[ií]cio|data|destinat[aá]rio|"
    r"vocativo|anexo|observa[cç][aã]o)\b"
)
NUMBER_RE = re.compile(
    r"(?i)of[ií]cio\s+(?:n[º°o]\.?\s*|n\.\s*)?(?:[A-Za-z]{2,12}[-/])?\d{1,6}\s*/\s*\d{4}"
)
MONTH_INDEX = {normalized(name): index for index, name in enumerate(MONTHS, start=1)}
CARGO_HINTS = (
    "procurador",
    "promotor",
    "secretário",
    "secretario",
    "diretor",
    "presidente",
    "juiz",
    "desembargador",
    "auditor",
    "conselheiro",
    "chefe",
    "coordenador",
    "defensor",
    "advogado",
    "ministro",
    "governador",
    "prefeito",
    "controlador",
    "corregedor",
    "ouvidor",
    "subprocurador",
)
ORG_HINTS = (
    "ministério público",
    "ministerio publico",
    "tribunal de contas",
    "procuradoria",
    "secretaria",
    "prefeitura",
    "universidade",
    "instituto",
    "conselho",
    "câmara",
    "camara",
    "assembleia",
    "defensoria",
    "controladoria",
)
SKIP_LINES = (
    "atenciosamente",
    "respeitosamente",
    "cordialmente",
    "excelentíssim",
    "excelentissim",
    "a sua excelência",
    "a sua excelencia",
    "documento assinado",
    "assinatura eletrônica",
    "assinatura eletronica",
)


def extract_pdf_text(file_bytes):
    if (
        not file_bytes
        or len(file_bytes) > MAX_FILE
        or not file_bytes.startswith(b"%PDF-")
    ):
        return ""
    try:
        reader = PdfReader(BytesIO(file_bytes))
        if reader.is_encrypted or not reader.pages:
            return ""
        chunks = []
        size = 0
        for page in reader.pages[:MAX_PAGES]:
            chunk = page.extract_text() or ""
            chunks.append(chunk)
            size += len(chunk)
            if size >= MAX_TEXT:
                break
        return "\n".join(chunks)[:MAX_TEXT]
    except Exception:
        return ""


def extract_received_metadata(file_bytes, filename=""):
    del filename
    text = extract_pdf_text(file_bytes)
    parsed = parse_received_text(text)
    parsed["texto_extraido"] = text.strip()
    parsed["texto_suficiente"] = _letter_count(text) >= MIN_LETTERS
    if not parsed["texto_suficiente"]:
        parsed.update({k: v for k, v in EMPTY.items() if k != "texto_extraido"})
        parsed["texto_extraido"] = text.strip()
        parsed["texto_suficiente"] = False
    return parsed


def parse_received_text(text):
    result = dict(EMPTY)
    result["texto_extraido"] = text or ""
    result["texto_suficiente"] = _letter_count(text) >= MIN_LETTERS
    if not result["texto_suficiente"]:
        return result
    result["numero_externo"] = _number(text)
    result["data"] = _date(text)
    result["assunto"] = _labeled(text, r"assunto")
    result["processo"] = _process(text)
    result["remetente"], result["cargo_remetente"] = _signatory(text)
    result["instituicao"] = _institution(text)
    return result


def _letter_count(text):
    return len(re.findall(r"[A-Za-zÀ-ÿ]", text or ""))


def _clean(value):
    return re.sub(r"\s+", " ", (value or "").strip())


def _number(text):
    for match in NUMBER_RE.finditer(text):
        snippet = _clean(match.group(0))
        start = max(0, match.start() - 24)
        context = normalized(text[start : match.start()])
        if "resposta ao" in context:
            continue
        return snippet
    return ""


def _date(text):
    header = text[:4000]
    found = []
    for match in re.finditer(
        r"\b(\d{1,2})\s+de\s+(janeiro|fevereiro|mar[cç]o|abril|maio|junho|julho|"
        r"agosto|setembro|outubro|novembro|dezembro)\s+de\s+(\d{4})\b",
        header,
        re.I,
    ):
        parsed = _valid_date(
            match.group(1), MONTH_INDEX.get(normalized(match.group(2))), match.group(3)
        )
        if parsed:
            found.append(parsed)
    if found:
        return found[0].isoformat()
    for match in re.finditer(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", header):
        parsed = _valid_date(match.group(1), int(match.group(2)), match.group(3))
        if parsed:
            found.append(parsed)
    return found[0].isoformat() if found else None


def _valid_date(day, month, year):
    try:
        value = date(int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None
    if value.year < 1000 or value.year > 2100:
        return None
    return value


def _labeled(text, label):
    pattern = re.compile(rf"(?im)^\s*{label}\s*[:\-\u2013]\s*(.*)$")
    match = pattern.search(text)
    if not match:
        return ""
    parts = [_clean(match.group(1))]
    for line in text[match.end() :].splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if (
            FIELD_START.match(stripped)
            or NUMBER_RE.match(stripped)
            or len(stripped) > 160
        ):
            break
        parts.append(_clean(stripped))
        break
    return _clean(" ".join(p for p in parts if p))[:300]


def _process(text):
    pattern = re.compile(
        r"(?im)^\s*(?:refer[eê]ncia|ref\.?|processo(?:\s*n[º°o]\.?|\s*n\.)?)\s*[:\-\u2013]?\s*(.*)$"
    )
    match = pattern.search(text)
    if not match:
        return ""
    value = _clean(match.group(1))
    if (
        not value
        or NUMBER_RE.fullmatch(value)
        or normalized(value).startswith("oficio")
    ):
        return ""
    return value[:180]


def _nonempty_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip()]


def _skipped(line):
    folded = normalized(line)
    return any(token in folded for token in SKIP_LINES)


def _looks_like_name(line):
    if _skipped(line) or re.search(r"\d", line) or len(line) < 8 or len(line) > 80:
        return False
    words = line.split()
    if not (2 <= len(words) <= 6):
        return False
    particles = {"de", "da", "do", "dos", "das", "e", "del"}
    for word in words:
        if normalized(word) in particles:
            continue
        if not re.fullmatch(r"[A-Za-zÀ-ÿ'.-]+", word):
            return False
        if word[0].islower():
            return False
    return True


def _looks_like_cargo(line):
    folded = normalized(line)
    return any(hint in folded for hint in CARGO_HINTS) and 3 < len(line) < 80


def _signatory(text):
    lines = _nonempty_lines(text)[-30:]
    for index in range(len(lines) - 1, 0, -1):
        cargo, name = lines[index], lines[index - 1]
        if _looks_like_cargo(cargo) and _looks_like_name(name) and not _skipped(name):
            return _clean(name), _clean(cargo)
    return "", ""


def _institution(text):
    lines = _nonempty_lines(text)
    candidates = []
    for line in lines[:12] + lines[-8:]:
        folded = normalized(line)
        if _skipped(line) or NUMBER_RE.search(line) or len(line) > 120:
            continue
        if any(hint in folded for hint in ORG_HINTS):
            candidates.append(_clean(line))
    if not candidates:
        return ""
    return max(candidates, key=len)
