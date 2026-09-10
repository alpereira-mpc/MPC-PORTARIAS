from datetime import date
from io import BytesIO
from pypdf import PdfWriter
from services.oficios_extraction import (
    extract_received_metadata,
    parse_received_text,
)
from tests.test_oficios import ready


def _pdf(text):
    commands = ["BT /F1 10 Tf 72 720 Td 14 TL"]
    for line in text.splitlines():
        safe = "".join(ch if ord(ch) < 128 else "?" for ch in line)
        safe = safe.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        commands.append(f"({safe}) Tj T*")
    commands.append("ET")
    stream = "\n".join(commands).encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{index} 0 obj\n".encode("ascii"))
        out.extend(body)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for start in offsets[1:]:
        out.extend(f"{start:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(out)


def _blank_pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


SAMPLE = """
Ministerio Publico de Contas do Estado da Paraiba

Oficio n. 123/2026
Joao Pessoa, 10 de setembro de 2026.

Assunto: Solicitacao de informacoes sobre contratos
Referencia: Processo 0001/2026

Excelentissimo Senhor Procurador-Geral,

Segue o pedido formal.

Atenciosamente,

Joao da Silva Santos
Procurador de Justica
"""


def test_parse_number_variations():
    for source in (
        "Ofício nº 123/2026",
        "Ofício n. 123/2026",
        "Ofício n° 123/2026",
        "OFÍCIO Nº 123/2026",
        "Ofício 123/2026",
    ):
        parsed = parse_received_text(
            source + "\nAssunto: Teste de extracao suficiente para letras.\n"
        )
        assert "123/2026" in parsed["numero_externo"]


def test_parse_dates_subject_and_process():
    parsed = parse_received_text(
        "Ofício nº 10/2026\nJoão Pessoa, 10 de setembro de 2026.\n"
        "Assunto: Pedido de vista\ncomplemento do assunto\n"
        "Processo nº 555/2024\n" + ("texto " * 20)
    )
    assert parsed["data"] == "2026-09-10"
    assert parsed["assunto"].startswith("Pedido de vista")
    assert "complemento" in parsed["assunto"]
    assert "555/2024" in parsed["processo"]
    numeric = parse_received_text(
        "Ofício nº 10/2026\nData 10/09/2026\nAssunto: X\n" + ("texto " * 20)
    )
    assert numeric["data"] == "2026-09-10"


def test_process_is_not_oficio_number():
    parsed = parse_received_text(
        "Ofício nº 77/2026\nReferência: Ofício nº 12/2025\nAssunto: Teste\n"
        + ("texto " * 20)
    )
    assert "77/2026" in parsed["numero_externo"]
    assert parsed["processo"] == ""


def test_missing_fields_stay_empty():
    parsed = parse_received_text(
        "Ofício n. 8/2026\nSomente o numero aparece neste documento oficial.\n"
        + ("texto " * 15)
    )
    assert "8/2026" in parsed["numero_externo"]
    assert parsed["assunto"] == ""
    assert parsed["processo"] == ""
    assert parsed["remetente"] == ""
    assert parsed["instituicao"] == ""
    assert parsed["data"] is None


def test_conservative_signatory_and_institution():
    parsed = parse_received_text(SAMPLE)
    assert parsed["instituicao"].lower().startswith("ministerio publico")
    assert parsed["remetente"] == "Joao da Silva Santos"
    assert "Procurador" in parsed["cargo_remetente"]
    noisy = parse_received_text(
        "Oficio 1/2026\nMaria apareceu no meio do texto sem cargo associado.\n"
        + ("texto " * 20)
    )
    assert noisy["remetente"] == ""
    assert noisy["cargo_remetente"] == ""


def test_insufficient_text():
    parsed = parse_received_text("abc")
    assert parsed["texto_suficiente"] is False
    assert parsed["numero_externo"] == ""
    blank = extract_received_metadata(_blank_pdf(), "vazio.pdf")
    assert blank["texto_suficiente"] is False
    assert blank["assunto"] == ""


def test_extract_from_searchable_pdf():
    meta = extract_received_metadata(_pdf(SAMPLE), "oficio.pdf")
    assert meta["texto_suficiente"]
    assert "123/2026" in meta["numero_externo"]
    assert meta["data"] == "2026-09-10"
    assert "Solicitacao" in meta["assunto"]
    assert "0001/2026" in meta["processo"]
    assert meta["remetente"] == "Joao da Silva Santos"


def test_received_save_still_stores_pdf(store):
    service = ready(store)
    member = service.series()[0]["membro_id"]
    content = _pdf(SAMPLE)
    identifier = service.save(
        dict(
            direcao="RECEBIDO",
            numero_externo="123/2026",
            remetente="Joao da Silva Santos",
            instituicao="Ministerio Publico de Contas",
            data="2026-09-10",
            data_recebimento=date.today().isoformat(),
            membros=[member],
            assunto="Solicitacao de informacoes sobre contratos",
        ),
        uploads=[("oficio.pdf", content)],
    )
    files = service.files(identifier)
    assert files[0]["nome"] == "oficio.pdf"
    assert service.download(files[0]["id"]) == content
    assert service.get(identifier)["numero_externo"] == "123/2026"
