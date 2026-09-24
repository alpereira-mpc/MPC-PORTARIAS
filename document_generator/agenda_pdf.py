"""Compact in-memory PDF export for the filtered institutional agenda."""

from datetime import datetime
from html import escape
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer

from services.date_format import format_date_br

WEEKDAYS = ("Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo")


def _safe(value):
    return escape(str(value or "").replace("\n", " ").replace("\r", " ").strip())


def _record_box(record, names, styles):
    if record.get("afastamento"):
        person = names.get(record.get("procurador_id"), "Procurador não informado")
        motive = record.get("motivo_outro") or record.get("motivo") or "Afastamento"
        lines = [f"<b>AFASTAMENTO - {_safe(person)}</b>", f"{_safe(motive)} - {format_date_br(record['data_inicio'])} a {format_date_br(record['data_fim'])}"]
        if record.get("substituto_id"):
            lines.append("Substituto(a): " + _safe(names.get(record["substituto_id"], "Não informado")))
        style = styles["leave_box"]
    else:
        people = " / ".join(names.get(item, "Procurador não informado") for item in record.get("procuradores", ()))
        title = record.get("titulo") or record.get("processo") or "Compromisso"
        moment = "Dia inteiro" if record.get("sem_hora") else record["inicio"][11:16].replace(":", "h")
        details = " | ".join(_safe(item) for item in (moment, record.get("local"), record.get("situacao")) if item)
        lines = [f"<b>COMPROMISSO - {_safe(people)}</b>", _safe(title), details]
        style = styles["commitment_box"]
    observation = record.get("observacoes") or record.get("observacao")
    if observation:
        lines.append("<font size='8'>Observações: " + _safe(observation) + "</font>")
    return Paragraph("<br/>".join(lines), style)


def generate_agenda_pdf(records, names, filters, *, generated_at=None):
    """Return a valid PDF containing only functional, already-filtered fields."""
    generated_at = generated_at or datetime.now().astimezone()
    ordered = sorted(records, key=lambda row: (
        row["inicio"][:10], bool(row.get("afastamento")), row["inicio"],
        "/".join(names.get(item, "") for item in row.get("procuradores", ())),
    ))
    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
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
        "commitment_box": ParagraphStyle("AgendaCommitmentBox", parent=base["Normal"], fontSize=9, leading=11, backColor=colors.HexColor("#F8F8F8"), borderColor=colors.HexColor("#9A9A9A"), borderWidth=0.5, borderPadding=7),
        "leave_box": ParagraphStyle("AgendaLeaveBox", parent=base["Normal"], fontSize=9, leading=11, backColor=colors.HexColor("#F1F1F1"), borderColor=colors.HexColor("#777777"), borderWidth=0.7, borderPadding=7),
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
        heading = Paragraph(
            f"{day.strftime('%d/%m/%Y')} - {WEEKDAYS[day.weekday()]}", styles["day"]
        )
        story.append(KeepTogether([heading, _record_box(day_records[0], names, styles)]))
        story.append(Spacer(1, 2 * mm))
        for record in day_records[1:]:
            story.append(KeepTogether([_record_box(record, names, styles)]))
            story.append(Spacer(1, 2 * mm))
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
