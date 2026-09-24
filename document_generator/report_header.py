"""Institutional header shared by Ferramentas MPC-PB PDF reports.

The first page of a report carries the official mark, the institution name,
the report title and the active filters. Later pages keep only the footer
drawn by each report, so this helper belongs in the story, not in a
repeating page template.
"""

from html import escape

from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle

from services.branding import SIDEBAR_LOGO

INSTITUTION_NAME = "MINISTÉRIO PÚBLICO DE CONTAS DA PARAÍBA"
# Points. Height is derived from the file so the mark is never stretched.
LOGO_WIDTH = 52


def institutional_logo_path():
    """Same file the sidebar already renders. Resolved from the project root."""
    return SIDEBAR_LOGO


def _logo():
    path = str(institutional_logo_path())
    pixel_width, pixel_height = ImageReader(path).getSize()
    height = LOGO_WIDTH * pixel_height / pixel_width
    return Image(path, width=LOGO_WIDTH, height=height, hAlign="LEFT", mask="auto")


def build_report_header(title, *, filters=None, subtitle=None, width):
    """Flowables for the first-page institutional header of a PDF report."""
    logo = _logo()
    base = getSampleStyleSheet()
    institution = ParagraphStyle(
        "ReportInstitution",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=13,
        textColor=colors.HexColor("#222222"),
        spaceAfter=1,
    )
    heading = ParagraphStyle(
        "ReportTitle",
        parent=base["Normal"],
        fontName="Helvetica-Bold",
        fontSize=10.5,
        leading=13,
        textColor=colors.HexColor("#333333"),
        spaceBefore=1,
        spaceAfter=0,
    )
    detail = ParagraphStyle(
        "ReportFilter",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor("#333333"),
    )
    text = [
        Paragraph(escape(INSTITUTION_NAME), institution),
        Paragraph(escape(title), heading),
    ]
    if subtitle:
        text.append(Paragraph(escape(subtitle), detail))
    logo_column = LOGO_WIDTH + (4 * mm)
    heading_row = Table(
        [[logo, text]],
        colWidths=[logo_column, width - logo_column],
    )
    heading_row.splitByRow = 0
    heading_row.hAlign = "LEFT"
    heading_row.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (0, 0), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 3 * mm),
                ("LEFTPADDING", (1, 0), (1, 0), 1 * mm),
                ("RIGHTPADDING", (1, 0), (1, 0), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1 * mm),
            ]
        )
    )
    flowables = [heading_row, Spacer(1, 2 * mm)]
    flowables.extend(Paragraph(escape(str(label)), detail) for label in filters or ())
    flowables.append(Spacer(1, 3 * mm))
    return flowables
