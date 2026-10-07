"""Institutional PDF of the current internship placement composition."""

from datetime import datetime
from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from database.estagiarios import GABINETES, LOTACOES
from document_generator.report_header import build_report_header

_MARGIN = 15 * mm


def _safe(value):
    return escape(str(value or "").replace("\n", " ").strip())


def generate_estagiarios_pdf(composition, *, generated_at=None):
    """Return a PDF with the seven cabinets and their active placements only."""
    generated_at = generated_at or datetime.now().astimezone()
    output = BytesIO()
    width = A4[0] - (2 * _MARGIN)
    document = SimpleDocTemplate(
        output, pagesize=A4, leftMargin=_MARGIN, rightMargin=_MARGIN,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title="Estagiários do Ministério Público de Contas da Paraíba",
        author="Ministério Público de Contas da Paraíba",
    )
    base = getSampleStyleSheet()
    heading = ParagraphStyle("EstagiariosCabinet", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10, leading=12, textColor=colors.HexColor("#4B1020"), spaceAfter=2 * mm)
    item = ParagraphStyle("EstagiariosItem", parent=base["Normal"], fontSize=9, leading=11)
    vacancy = ParagraphStyle("EstagiariosVacancy", parent=item, textColor=colors.HexColor("#666666"))
    story = build_report_header(
        "Estagiários do Ministério Público de Contas da Paraíba",
        subtitle="Composição atual por gabinete",
        width=width,
    )
    for code in LOTACOES:
        records = composition.get(code, ())
        cells = [Paragraph(f"<b>{_safe(code)} - {_safe(GABINETES[code])}</b>", heading)]
        for number in range(2):
            if number < len(records):
                row = records[number]
                cells.append(Paragraph(
                    f"{number + 1}. <b>{_safe(row['nome'])}</b><br/>"
                    f"Início: {_safe(row['data_inicio'][8:10] + '/' + row['data_inicio'][5:7] + '/' + row['data_inicio'][:4])}"
                    f" &nbsp; | &nbsp; Limite: {_safe(row['data_limite'][8:10] + '/' + row['data_limite'][5:7] + '/' + row['data_limite'][:4])}",
                    item,
                ))
            else:
                cells.append(Paragraph(f"{number + 1}. Vaga disponível", vacancy))
        card = Table([[cells]], colWidths=[width])
        card.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#FAF8F8")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#9A9A9A")),
            ("LEFTPADDING", (0, 0), (-1, -1), 7), ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.extend([KeepTogether(card), Spacer(1, 3 * mm)])
    footer = "Ministério Público de Contas da Paraíba - Gerado em " + generated_at.strftime("%d/%m/%Y às %H:%M")

    def draw_footer(canvas, _document):
        canvas.saveState(); canvas.setFont("Helvetica", 7.5); canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(_MARGIN, 8 * mm, footer)
        canvas.drawRightString(A4[0] - _MARGIN, 8 * mm, f"Página {canvas.getPageNumber()}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return output.getvalue()
