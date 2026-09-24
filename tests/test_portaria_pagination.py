from io import BytesIO

from pypdf import PdfReader
import pytest

from document_generator.docx import generate
from document_generator.pdf import PdfUnavailable, convert, libreoffice_path
from services.wording import compose
from tests.cases import sample


def _one_page_pdf(payload, number):
    try:
        pdf, engine = convert(generate(payload, number), "libreoffice")
    except PdfUnavailable as exc:
        pytest.skip(str(exc))
    assert engine == "LibreOffice"
    reader = PdfReader(BytesIO(pdf))
    assert len(reader.pages) == 1
    return reader.pages[0].extract_text() or ""


@pytest.mark.skipif(libreoffice_path() is None, reason="LibreOffice indisponível")
def test_regular_and_slightly_longer_portarias_remain_on_one_page(store):
    regular = sample(store, 5)
    regular_text = _one_page_pdf(regular, 5)

    larger = sample(store, 5)
    body = compose(larger)["body"][0]
    larger["manual"] = {
        "body": [
            body[:-1]
            + ", observadas ainda as providências administrativas necessárias ao "
            "regular exercício das atribuições institucionais durante todo o "
            "período indicado."
        ]
    }
    larger_text = _one_page_pdf(larger, 99)

    note = " ".join(regular["substituicoes"][0]["nota"].split())
    for rendered in (regular_text, larger_text):
        assert "ELVIRA SAMARA PEREIRA DE OLIVEIRA" in rendered
        assert note in " ".join(rendered.split())
