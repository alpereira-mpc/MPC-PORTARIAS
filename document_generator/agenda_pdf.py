"""Compact in-memory PDF export for the filtered institutional agenda."""

from datetime import datetime
from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from services.date_format import format_date_br

WEEKDAYS = ("Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo")
# Gap between cards of the same day. The date heading keeps a larger spaceBefore.
CARD_GAP = 7
_PAGE_MARGIN = 15 * mm


def _safe(value):
    return escape(str(value or "").replace("\n", " ").replace("\r", " ").strip())


def _record_markup(record, names):
    if record.get("afastamento"):
        person = names.get(record.get("procurador_id"), "Procurador não informado")
        motive = record.get("motivo_outro") or record.get("motivo") or "Afastamento"
        lines = [
            f"<b>AFASTAMENTO - {_safe(person)}</b>",
            f"{_safe(motive)} - {format_date_br(record['data_inicio'])} a {format_date_br(record['data_fim'])}",
        ]
        if record.get("substituto_id"):
            lines.append(
                "Substituto(a): " + _safe(names.get(record["substituto_id"], "Não informado"))
            )
        kind = "leave"
    else:
        people = " / ".join(
            names.get(item, "Procurador não informado") for item in record.get("procuradores", ())
        )
        title = record.get("titulo") or record.get("processo") or "Compromisso"
        moment = "Dia inteiro" if record.get("sem_hora") else record["inicio"][11:16].replace(":", "h")
        details = " | ".join(
            _safe(item) for item in (moment, record.get("local"), record.get("situacao")) if item
        )
        lines = [f"<b>COMPROMISSO - {_safe(people)}</b>", _safe(title), details]
        kind = "commitment"
    observation = record.get("observacoes") or record.get("observacao")
    if observation:
        lines.append("<font size='8'>Observações: " + _safe(observation) + "</font>")
    return "<br/>".join(lines), kind


def _record_card(record, names, styles, width):
    """One bordered card. The box stays inside the flowable, so neighbors cannot share an edge."""
    markup, kind = _record_markup(record, names)
    if kind == "leave":
        background = colors.HexColor("#F1F1F1")
        border = colors.HexColor("#777777")
        stroke = 0.7
        style = styles["leave_box"]
    else:
        background = colors.HexColor("#F8F8F8")
        border = colors.HexColor("#9A9A9A")
        stroke = 0.5
        style = styles["commitment_box"]
    card = Table([[Paragraph(markup, style)]], colWidths=[width])
    card.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), background),
                ("BOX", (0, 0), (-1, -1), stroke, border),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    card.hAlign = "LEFT"
    card.splitByRow = 0
    return card


def _day_flowables(day, records, names, styles, width):
    """Date heading with its first card, then each later card kept intact and separated."""
    heading = Paragraph(
        f"{day.strftime('%d/%m/%Y')} - {WEEKDAYS[day.weekday()]}", styles["day"]
    )
    cards = [_record_card(record, names, styles, width) for record in records]
    flowables = [KeepTogether([heading, cards[0]])]
    for card in cards[1:]:
        flowables.append(KeepTogether([Spacer(1, CARD_GAP), card]))
    return flowables


def generate_agenda_pdf(records, names, filters, *, generated_at=None):
    """Return a valid PDF containing only functional, already-filtered fields."""
    generated_at = generated_at or datetime.now().astimezone()
    ordered = sorted(records, key=lambda row: (
        row["inicio"][:10], bool(row.get("afastamento")), row["inicio"],
        "/".join(names.get(item, "") for item in row.get("procuradores", ())),
    ))
    output = BytesIO()
    content_width = A4[0] - (2 * _PAGE_MARGIN)
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=_PAGE_MARGIN, rightMargin=_PAGE_MARGIN,
                                 topMargin=16 * mm, bottomMargin=16 * mm,
                                 title="Agenda e Afastamentos dos Procuradores",
                                 author="Ministério Público de Contas da Paraíba")
    base = getSampleStyleSheet()
    styles = {
        "institution": ParagraphStyle("AgendaInstitution", parent=base["Heading1"], fontName="Helvetica-Bold", fontSize=13, leading=16, alignment=TA_CENTER, textColor=colors.HexColor("#222222"), spaceAfter=3 * mm),
        "title": ParagraphStyle("AgendaTitle", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=12, leading=15, alignment=TA_CENTER, spaceAfter=4 * mm),
        "filter": ParagraphStyle("AgendaFilter", parent=base["Normal"], fontSize=8.5, leading=11),
        "day": ParagraphStyle("AgendaDay", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10.5, leading=13, spaceBefore=4 * mm, spaceAfter=2 * mm),
        "item_title": ParagraphStyle("AgendaItemTitle", parent=base["Normal"], fontSize=9.5, leading=12),
        "item": ParagraphStyle("AgendaItem", parent=base["Normal"], fontSize=9, leading=11),
        "item_small": ParagraphStyle("AgendaItemSmall", parent=base["Normal"], fontSize=8, leading=10),
        "commitment_box": ParagraphStyle("AgendaCommitmentBox", parent=base["Normal"], fontSize=9, leading=11),
        "leave_box": ParagraphStyle("AgendaLeaveBox", parent=base["Normal"], fontSize=9, leading=11),
    }
    story = [Paragraph("MINISTÉRIO PÚBLICO DE CONTAS DA PARAÍBA", styles["institution"]),
             Paragraph("Agenda e Afastamentos dos Procuradores", styles["title"])]
    story.extend(Paragraph(_safe(label), styles["filter"]) for label in filters)
    story.append(Spacer(1, 3 * mm))
    day_groups = []
    for record in ordered:
        day = datetime.fromisoformat(record["inicio"]).date()
        if not day_groups or day_groups[-1][0] != day:
            day_groups.append((day, []))
        day_groups[-1][1].append(record)
    for day, day_records in day_groups:
        story.extend(_day_flowables(day, day_records, names, styles, content_width))
    if not ordered:
        story.append(Paragraph("Nenhum registro para os filtros selecionados.", styles["item"]))
    footer = "Ministério Público de Contas da Paraíba - Gerado em " + generated_at.strftime("%d/%m/%Y às %H:%M")

    def draw_footer(canvas, _document):
        canvas.saveState(); canvas.setFont("Helvetica", 7.5); canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(15 * mm, 8 * mm, footer)
        canvas.drawRightString(195 * mm, 8 * mm, f"Página {canvas.getPageNumber()}")
        canvas.restoreState()

    document.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)
    return output.getvalue()
