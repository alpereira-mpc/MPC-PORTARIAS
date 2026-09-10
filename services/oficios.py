"""Central rules for correspondence; no Streamlit or persistence dependencies."""

from datetime import date, timedelta
from io import BytesIO
from pathlib import PurePath
import re
import unicodedata
from zipfile import ZipFile

SENT = (
    "Rascunho",
    "Gerado",
    "Enviado",
    "Aguardando resposta",
    "Respondido",
    "Concluído",
    "Cancelado",
)
RECEIVED = (
    "Recebido",
    "Em análise",
    "Aguardando providência",
    "Encaminhado",
    "Respondido",
    "Concluído",
    "Arquivado",
)
CLOSED = ("Respondido", "Concluído", "Cancelado", "Arquivado")
SERIES = {
    "Elvira Samara Pereira de Oliveira": (
        "PROGE",
        "PROGE",
        "Ofício MPC/PB - PROGE n. {numero}/{ano}",
        3,
    ),
    "Bradson Tibério Luna Camelo": (
        "BTLC",
        "BTLC",
        "Ofício BTLC-MPC-PB nº {numero}/{ano}",
        2,
    ),
    **{
        name: (code, "PROGE", f"Ofício MPC/PB - {code} n. {{numero}}/{{ano}}", 3)
        for code, name in (
            ("SBBQ", "Sheyla Barreto Braga de Queiroz"),
            ("IBMF", "Isabella Barbosa Marinho Falcão"),
            ("MTFF", "Marcílio Toscano Franca Filho"),
            ("LAF", "Luciano Andrade Farias"),
            ("MASN", "Manoel Antonio dos Santos Neto"),
        )
    },
}
GABINETES = ("PROGE", "SBBQ", "IBMF", "MTFF", "BTLC", "LAF", "MASN")


def fingerprint(record, series, context=None):
    """Bind preview to editable values, institutional configuration and editor context."""
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(
            [record, series, context],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


BASELINES = {("PROGE", 2025): 21, ("PROGE", 2026): 8, ("BTLC", 2026): 2}
MONTHS = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
MAX_FILE = 10 * 1024 * 1024


def normalized(value):
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(c)
    )


def long_date(value):
    d = date.fromisoformat(value)
    return f"João Pessoa (PB), {d.day} de {MONTHS[d.month-1]} de {d.year}."


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")[:150]
    if not value or value.split(".")[0].upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *[f"COM{i}" for i in range(10)],
        *[f"LPT{i}" for i in range(10)],
    }:
        value = "oficio_" + value
    return value


def validate_upload(name, content):
    if not content or len(content) > MAX_FILE:
        raise ValueError("Cada arquivo deve ter entre 1 byte e 10 MB.")
    ext = PurePath(name).suffix.lower()
    if ext == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("PDF inválido.")
        from pypdf import PdfReader

        try:
            reader = PdfReader(BytesIO(content))
            count = len(reader.pages)
            if reader.is_encrypted or not count:
                raise ValueError()
        except Exception as exc:
            raise ValueError("PDF inválido ou protegido.") from exc
        mime = "application/pdf"
    elif ext == ".docx":
        try:
            with ZipFile(BytesIO(content)) as z:
                if (
                    sum(i.file_size for i in z.infolist()) > 50 * 1024 * 1024
                    or len(z.infolist()) > 2000
                ):
                    raise ValueError()
                if not {"[Content_Types].xml", "word/document.xml"} <= set(
                    z.namelist()
                ):
                    raise ValueError()
                if any("vbaproject" in n.lower() for n in z.namelist()):
                    raise ValueError()
                from docx import Document

                Document(BytesIO(content))
        except Exception as exc:
            raise ValueError("DOCX inválido ou não permitido.") from exc
        mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    else:
        raise ValueError("Aceitos apenas PDF e DOCX.")
    return safe_name(name), mime


def validate(record, official=False):
    if record.get("direcao") not in ("ENVIADO", "RECEBIDO"):
        raise ValueError("Direção inválida.")
    if date.fromisoformat(record["data"]).year < 1000:
        raise ValueError("Utilize ano com quatro dígitos, a partir de 1000.")
    for field in ("prazo", "data_envio", "data_recebimento"):
        if record.get(field):
            date.fromisoformat(record[field])
    if not str(record.get("assunto", "")).strip():
        raise ValueError("Preencha o assunto.")
    if record["direcao"] == "RECEBIDO":
        for field in ("numero_externo", "remetente", "instituicao", "data_recebimento"):
            if not record.get(field):
                raise ValueError(
                    "Preencha número, remetente, instituição e recebimento."
                )
        if not record.get("membros"):
            raise ValueError("Selecione o destinatário interno.")
    elif official:
        if (
            not record.get("membro_id")
            or not record.get("corpo", "").strip()
            or not any(
                record.get(k, "").strip()
                for k in ("destinatario", "instituicao", "unidade")
            )
        ):
            raise ValueError("Preencha signatário, destinatário e corpo.")
    for value in record.values():
        if isinstance(value, str) and (
            len(value) > 100000 or re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", value)
        ):
            raise ValueError("Texto inválido ou excessivamente longo.")


def attention(record, today=None):
    today = today or date.today()
    if record["status"] in CLOSED:
        return ""
    due = record.get("prazo")
    if due and date.fromisoformat(due) < today:
        return "⚠ Prazo vencido"
    if due and date.fromisoformat(due) <= today + timedelta(days=7):
        return "◷ Prazo próximo (7 dias)"
    if record["direcao"] == "ENVIADO" and record["status"] != "Rascunho":
        return "• Enviado ainda não concluído"
    if record["status"] in ("Em análise", "Aguardando providência"):
        return "• " + record["status"]
    return ""


def open_service(store):
    """Public module entry point; backend selection remains owned by Store."""
    from database.oficios import OficiosStore

    return OficiosStore(store)


def suggest_vocative(treatment, role=""):
    feminine = "Senhora" in treatment
    prefix = "Excelentíssima Senhora" if feminine else "Excelentíssimo Senhor"
    if "Excelência" not in treatment:
        prefix = "Senhora" if feminine else "Senhor"
    return prefix + (" " + role.strip() if role.strip() else "") + ","
