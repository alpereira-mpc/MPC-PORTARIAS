"""Document generation from sanitized institutional packages, using existing PDF conversion."""

from copy import deepcopy
from io import BytesIO
from pathlib import Path
import re
from docx import Document
from docx.text.paragraph import Paragraph
from docx.shared import Pt
from services.oficios import long_date, safe_name

ROOT = Path(__file__).resolve().parents[1]


def generate(record, series, number=None):
    model = series["modelo"]
    if model not in ("PROGE", "BTLC"):
        raise ValueError("Confirme o modelo institucional desta série.")
    doc = Document(ROOT / "templates" / "oficios" / f"{model}.docx")
    number_text = (
        str(number).zfill(series["digitos"]) if number is not None else "RASCUNHO"
    )
    heading = series["cabecalho"].format(numero=number_text, ano=record["data"][:4])
    title = record.get("titulo_assinatura") or (
        "Procuradora-Geral do Ministério Público de Contas da Paraíba"
        if series["sigla"] == "PROGE"
        else record["cargo_base"] + " do Ministério Público de Contas da Paraíba"
    )
    values = {
        "heading": heading
        + "                             "
        + long_date(record["data"]),
        "treatment": record.get("tratamento", ""),
        "name": record.get("destinatario", ""),
        "role": record.get("cargo", ""),
        "unit": record.get("unidade", ""),
        "institution": record.get("instituicao", ""),
        "subject": "Assunto: " + record["assunto"],
        "vocative": record.get("vocativo", ""),
        "body": record.get("corpo", ""),
        "closing": record.get("fechamento", "Atenciosamente,"),
        "signature": record["signatario"].upper(),
        "title": title,
    }
    for p in list(doc.paragraphs):
        if not p.text:
            continue
        key = p.text.strip("{}")
        chunks = re.split(r"\n\s*\n", values[key]) if key == "body" else [values[key]]
        if not chunks[0] and key in (
            "treatment",
            "name",
            "role",
            "unit",
            "institution",
        ):
            p._p.getparent().remove(p._p)
            continue
        template = deepcopy(p._p)
        previous = p
        for index, text in enumerate(chunks):
            target = p if index == 0 else Paragraph(deepcopy(template), p._parent)
            if index:
                previous._p.addnext(target._p)
            props = (
                deepcopy(target.runs[0]._r.rPr)
                if target.runs and target.runs[0]._r.rPr is not None
                else None
            )
            target.clear()
            run = target.add_run(text.strip())
            if props is not None:
                run._r.insert(0, props)
            if key == "heading" and model == "BTLC" and heading.startswith("Ofício "):
                target.clear()
                first = target.add_run("Ofício ")
                first.font.name = "Calibri"
                first.font.size = Pt(12)
                middle = target.add_run(heading.removeprefix("Ofício "))
                middle.font.name = "Calibri"
                middle.font.size = Pt(13)
                last = target.add_run(" " * 38 + long_date(record["data"]))
                last.font.name = "Calibri"
                last.font.size = Pt(12)
            if key in ("signature", "closing"):
                target.paragraph_format.keep_with_next = True
            previous = target
    out = BytesIO()
    doc.save(out)
    return out.getvalue()


def filename(record, series, number, ext):
    return (
        safe_name(
            f"Oficio_{series['sigla']}_{str(number).zfill(series['digitos'])}_{record['data'][:4]}_{record.get('destinatario') or record['assunto']}"
        )
        + "."
        + ext
    )


def official_documents(record, series, number):
    from document_generator.pdf import convert

    docx = generate(record, series, number)
    try:
        pdf, _ = convert(docx)
    except RuntimeError as exc:
        raise RuntimeError(
            "Conversão PDF indisponível. O rascunho foi mantido e nenhum número foi consumido. Verifique Word/LibreOffice e tente novamente."
        ) from exc
    return [
        ("docx", filename(record, series, number, "docx"), docx),
        ("pdf", filename(record, series, number, "pdf"), pdf),
    ]
