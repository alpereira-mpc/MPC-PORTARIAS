from datetime import datetime
from io import BytesIO
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.platypus import KeepTogether, Spacer, Table

from document_generator.agenda_pdf import (
    CARD_GAP,
    _PAGE_MARGIN,
    _day_flowables,
    generate_agenda_pdf,
)
from document_generator.report_header import (
    INSTITUTION_NAME,
    LOGO_WIDTH,
    build_report_header,
    institutional_logo_path,
)
from services.agenda_ui import agenda_pdf_filename, _listing_summary
from services.branding import SIDEBAR_LOGO


def _text(content):
    return "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)


def test_pdf_contains_commitments_and_leaves_without_technical_ids():
    records = [
        {
            "id": "internal-commitment-id",
            "inicio": "2026-09-24T14:30:00",
            "fim": None,
            "tipo": "EVENTO",
            "titulo": "Reuniao institucional",
            "sem_hora": False,
            "situacao": "Agendado",
            "procuradores": [1],
            "local": "Sede",
        },
        {
            "id": "internal-leave-id",
            "inicio": "2026-09-25T00:00:00",
            "data_inicio": "2026-09-25",
            "data_fim": "2026-10-02",
            "afastamento": True,
            "procurador_id": 2,
            "motivo": "Ferias",
            "status": "AGENDADO",
        },
    ]

    content = generate_agenda_pdf(
        records,
        {1: "Dra. Elvira", 2: "Dr. Bradson"},
        ["Periodo: 24/09/2026 a 02/10/2026"],
        generated_at=datetime(2026, 9, 24, 16, 5),
    )
    text = _text(content)

    assert content.startswith(b"%PDF-")
    assert "COMPROMISSO" in text and "AFASTAMENTO" in text
    assert "Dra. Elvira" in text and "Dr. Bradson" in text
    assert "24/09/2026" in text
    assert "25/09/2026 a 02/10/2026" in text
    assert "14h30" in text
    assert "internal-commitment-id" not in text
    assert "internal-leave-id" not in text


def _commitment(day, hour, person, title):
    return {
        "inicio": f"{day}T{hour}:00",
        "titulo": title,
        "sem_hora": False,
        "situacao": "Agendado",
        "procuradores": [person],
        "local": "Sede",
    }


def _leave(day, person, name):
    return {
        "inicio": f"{day}T00:00:00",
        "data_inicio": day,
        "data_fim": day,
        "afastamento": True,
        "procurador_id": person,
        "motivo": name,
    }


def _card_styles():
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    base = getSampleStyleSheet()
    return {
        "day": ParagraphStyle(
            "AgendaDayTest",
            parent=base["Heading3"],
            fontName="Helvetica-Bold",
            fontSize=10.5,
            leading=13,
            spaceBefore=4,
            spaceAfter=2,
        ),
        "commitment_box": ParagraphStyle(
            "AgendaCommitmentBoxTest", parent=base["Normal"], fontSize=9, leading=11
        ),
        "leave_box": ParagraphStyle(
            "AgendaLeaveBoxTest", parent=base["Normal"], fontSize=9, leading=11
        ),
    }


def test_same_day_records_are_separate_cards():
    names = {1: "Elvira", 2: "Marcílio", 3: "Bradson", 4: "Sheyla"}
    day = datetime(2026, 10, 13).date()
    records = [
        _commitment("2026-10-13", "09:00", 1, "Sessão"),
        _leave("2026-10-13", 4, "Licença"),
        _leave("2026-10-13", 2, "Férias"),
        _leave("2026-10-13", 3, "Missão"),
    ]
    styles = _card_styles()
    width = A4[0] - (2 * _PAGE_MARGIN)
    blocks = _day_flowables(day, records, names, styles, width)

    assert len(blocks) == 4
    assert isinstance(blocks[0], KeepTogether)
    assert isinstance(blocks[0]._content[-1], Table)
    assert not any(isinstance(item, Spacer) for item in blocks[0]._content)
    for block in blocks[1:]:
        assert isinstance(block, KeepTogether)
        spacer, card = block._content
        assert isinstance(spacer, Spacer) and spacer.height == CARD_GAP
        assert isinstance(card, Table)
        assert card.splitByRow == 0

    alone = _day_flowables(day, records[:1], names, styles, width)
    assert len(alone) == 1
    assert isinstance(alone[0]._content[-1], Table)

    content = generate_agenda_pdf(
        records,
        names,
        ["Período: 13/10/2026"],
        generated_at=datetime(2026, 10, 13, 9, 0),
    )
    text = _text(content)
    assert text.count("13/10/2026") >= 1
    assert text.count("COMPROMISSO") == 1
    assert text.count("AFASTAMENTO") == 3


def test_empty_pdf_is_valid_and_filename_uses_brazilian_period():
    content = generate_agenda_pdf([], {}, ["Nenhum registro"])

    assert len(PdfReader(BytesIO(content)).pages) == 1
    assert agenda_pdf_filename(
        datetime(2026, 10, 1).date(), datetime(2026, 10, 31).date()
    ) == "Agenda_MPC-PB_01-10-2026_a_31-10-2026.pdf"


def test_listing_summary_has_no_page_label():
    assert _listing_summary(4, 6) == "4 compromissos · 6 afastamentos"
    assert _listing_summary(1, 0) == "1 compromisso · 0 afastamentos"
    assert _listing_summary(0, 1) == "0 compromissos · 1 afastamento"


def test_report_header_reuses_sidebar_logo_without_distortion():
    from PIL import Image as PilImage
    from reportlab.platypus import Image

    path = institutional_logo_path()
    assert path == SIDEBAR_LOGO
    assert path.name == "mpcpb_logo_sidebar_transparent.png"
    assert path.is_file()
    assert not path.is_absolute() or "assets" in path.parts
    pixels = PilImage.open(path)
    header = build_report_header(
        "Agenda e Afastamentos dos Procuradores",
        filters=["Período: 13/10/2026", "Procurador: Todos"],
        width=A4[0] - (2 * _PAGE_MARGIN),
    )
    logo = header[0]._cellvalues[0][0]
    assert isinstance(logo, Image)
    assert Path(logo.filename).resolve() == path.resolve()
    assert logo.drawWidth == LOGO_WIDTH
    assert abs((logo.drawHeight / logo.drawWidth) - (pixels.height / pixels.width)) < 0.01


def test_institutional_header_is_first_page_only_and_footer_remains():
    records = [
        _commitment(f"2026-11-{day:02d}", "09:00", 1, f"Sessão {day}")
        for day in range(1, 28)
    ]
    content = generate_agenda_pdf(
        records,
        {1: "Dra. Elvira"},
        ["Período: 01/11/2026 a 27/11/2026", "Procurador: Dra. Elvira"],
        generated_at=datetime(2026, 11, 1, 8, 30),
    )
    reader = PdfReader(BytesIO(content))
    assert len(reader.pages) >= 2
    first = reader.pages[0].extract_text() or ""
    later = "\n".join(page.extract_text() or "" for page in reader.pages[1:])
    assert INSTITUTION_NAME in first
    assert "Agenda e Afastamentos dos Procuradores" in first
    assert "Período: 01/11/2026 a 27/11/2026" in first
    assert "Dra. Elvira" in first
    assert INSTITUTION_NAME not in later
    assert "Gerado em 01/11/2026 às 08:30" in first
    assert "Gerado em 01/11/2026 às 08:30" in later
    assert "Página 1" in first
    assert "Página 2" in later
    assert later.count("COMPROMISSO") >= 1
