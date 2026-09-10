"""Local visual QA against PROGE 007/2026 and BTLC 01/2026 originals.

Run from repository root with a functioning Word or LibreOffice converter.
Outputs are isolated under ignored tmp/oficios-visual; originals remain read-only.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from docx import Document
from document_generator.oficios import generate
from document_generator.pdf import convert
from services.oficios import SERIES


def main():
    import pymupdf

    root = Path(__file__).resolve().parents[1]
    output = root / "tmp/oficios-visual"
    output.mkdir(parents=True, exist_ok=True)
    for code, pattern, number, day in [
        ("PROGE", "*PROGE 007-*.docx", 7, "2026-07-29"),
        ("BTLC", "*BLTC.docx", 1, "2026-05-08"),
    ]:
        source = next((root / "referencias").glob(pattern))
        doc = Document(source)
        p = [x.text for x in doc.paragraphs]
        name, rule = next(
            (name, rule) for name, rule in SERIES.items() if rule[0] == code
        )
        config = dict(sigla=code, modelo=rule[1], cabecalho=rule[2], digitos=rule[3])
        record = dict(
            data=day,
            assunto=p[8 if code == "PROGE" else 7].removeprefix("Assunto: "),
            tratamento=p[2],
            destinatario=p[3],
            cargo=p[4],
            unidade=p[5] if code == "PROGE" else "",
            instituicao=p[6 if code == "PROGE" else 5],
            vocativo=p[10],
            corpo="\n\n".join(p[11:14]) if code == "PROGE" else p[12],
            fechamento="Atenciosamente,",
            signatario=name,
            cargo_base="Procuradora" if code == "PROGE" else "Procurador",
        )
        data = generate(record, config, number)
        (output / f"{code}.docx").write_bytes(data)
        pdf, engine = convert(data)
        (output / f"{code}.pdf").write_bytes(pdf)
        for label, document in [
            (code, pymupdf.open(stream=pdf, filetype="pdf")),
            ("ref-" + code, pymupdf.open(source.with_suffix(".pdf"))),
        ]:
            for i, page in enumerate(document):
                page.get_pixmap(matrix=pymupdf.Matrix(1.4, 1.4)).save(
                    output / f"{label}-{i+1}.png"
                )
            print(label, len(document), "pages", engine)


if __name__ == "__main__":
    main()
