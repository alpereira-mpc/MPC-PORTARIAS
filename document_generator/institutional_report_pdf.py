"""Native A4 institutional report built only from a frozen version.

The generator reads the report dict (snapshot, structured content and metadata).
It does not query Tramita, reopen workbooks, or recalculate indicators.
"""

import html
import logging
import math
import re
from io import BytesIO
from calendar import monthrange
from datetime import date, datetime
from pathlib import Path

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Drawing
from reportlab.graphics.widgets.markers import makeMarker
from reportlab.lib.colors import Color, HexColor, white
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfdoc import PDFDate
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    BaseDocTemplate,
    CondPageBreak,
    Frame,
    HRFlowable,
    Image,
    KeepTogether,
    NextPageTemplate,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from services.date_format import format_date_br, format_datetime_br
from services.institutional_presentation import (
    ANNUAL_WITHOUT_PRIOR,
    PRIOR_WITHOUT_DATA,
    format_report_period,
)
from services.institutional_report_content import normalize_content


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
LOGO = ROOT / "assets" / "mpcpb_logo_sidebar_transparent.png"
BRAND = HexColor("#8E1B2C")
INK = HexColor("#1C2830")
MUTED = HexColor("#5C6570")
LINE = HexColor("#E4D5D8")
PAPER = HexColor("#F8F4F4")
NOTE_PAPER = HexColor("#F6F4F1")
NOTE_LINE = HexColor("#E3DDD4")
PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT = 16 * mm
RIGHT = 16 * mm
BOTTOM = 16 * mm
COVER_TOP = 16 * mm
BODY_TOP = 20 * mm
CONTENT_WIDTH = PAGE_WIDTH - LEFT - RIGHT
CHART_COLORS = {
    "Distribuídos": "#1F77B4",
    "Produção": "#2CA02C",
    "Pareceres": "#9467BD",
    "Cotas": "#FF7F0E",
    "Mediana de permanência": "#D62728",
    "Produção/Distribuições": "#17A2B8",
}
ANNUAL_CHART_COLORS = {
    "Distribuídos": "#2F6688",
    "Produção": "#8E1B2C",
    "Pareceres": "#8E1B2C",
    "Cotas": "#B27A2B",
    "Faixas de permanência": (
        "#6F8E83",
        "#8797A5",
        "#A58E6B",
        "#C28A2C",
        "#B85C38",
        "#9B1724",
    ),
}
MONTHS = (
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)
SOURCE = (
    "Fonte: Tramita/TCE-PB. Dados consolidados pelo Ministério Público de "
    "Contas do Estado da Paraíba."
)
_BULLET = re.compile(r"^([-*•]|\d+[.)])\s+")
_ATTENTION = re.compile(r"(?im)^(?:#+\s*)?pontos de atenção\s*:?\s*$")
_SYNTHESIS_HEADING = re.compile(r"(?im)^(?:#+\s*)?síntese\s*:?\s*")


def institutional_pdf_filename(report):
    """Stable ASCII file name for one report version."""
    year = int(report["ano"])
    version = int(report["versao"])
    if report.get("tipo") == "TRIMESTRAL":
        quarter = int(report["trimestre"])
        return f"Relatorio_Trimestral_MPCPB_{year}_T{quarter}_v{version}.pdf"
    return f"Relatorio_Anual_MPCPB_{year}_v{version}.pdf"


def period_label(snapshot):
    """Human coverage from the frozen months. Stored wording stays unchanged."""
    return format_report_period(snapshot)


def document_title(snapshot, *, formal=False):
    quarterly = (snapshot.get("metadados") or {}).get("tipo") == "TRIMESTRAL"
    if quarterly:
        return (
            "RELATÓRIO TRIMESTRAL DE PRODUÇÃO"
            if formal
            else "Relatório Trimestral de Produção"
        )
    partial = bool((snapshot.get("metadados") or {}).get("periodo_parcial"))
    suffix = " — PARCIAL" if partial else ""
    return (
        ("RELATÓRIO ANUAL DE PRODUÇÃO" + suffix)
        if formal
        else ("Relatório Anual de Produção" + suffix)
    )


def generate_institutional_report_pdf(report, *, emitido_em=None):
    """Return PDF bytes for this version. `emitido_em` keeps repeated renders stable."""
    emitted = emitido_em or datetime.now().astimezone().replace(microsecond=0)
    snapshot = report.get("snapshot_dados") or {}
    meta = _meta(report, snapshot, emitted)
    styles = _styles()
    buffer_target = BytesIO()
    document = BaseDocTemplate(
        buffer_target,
        invariant=1,
        pagesize=A4,
        title=meta["heading"],
        author="Ministério Público de Contas do Estado da Paraíba",
        leftMargin=LEFT,
        rightMargin=RIGHT,
        topMargin=BODY_TOP,
        bottomMargin=BOTTOM,
    )
    document.addPageTemplates(
        [
            PageTemplate(
                id="cover",
                frames=[
                    Frame(
                        LEFT,
                        BOTTOM,
                        CONTENT_WIDTH,
                        PAGE_HEIGHT - COVER_TOP - BOTTOM,
                        id="cover",
                        showBoundary=0,
                    )
                ],
            ),
            PageTemplate(
                id="body",
                frames=[
                    Frame(
                        LEFT,
                        BOTTOM,
                        CONTENT_WIDTH,
                        PAGE_HEIGHT - BODY_TOP - BOTTOM,
                        id="body",
                        showBoundary=0,
                    )
                ],
            ),
        ]
    )
    story = _cover(meta, styles) + [
        NextPageTemplate("body"),
        PageBreak(),
    ]
    story.extend(_body(report, snapshot, meta, styles))
    document.build(
        story,
        canvasmaker=lambda *args, **kwargs: _NumberedCanvas(*args, meta=meta, **kwargs),
    )
    return buffer_target.getvalue()


class _Stamp:
    def __init__(self, moment):
        self.YMDhms = (
            moment.year,
            moment.month,
            moment.day,
            moment.hour,
            moment.minute,
            moment.second,
        )
        self.dhh = 0
        self.dmm = 0


class _NumberedCanvas(pdf_canvas.Canvas):
    def __init__(self, *args, meta=None, **kwargs):
        pdf_canvas.Canvas.__init__(self, *args, **kwargs)
        self._meta = meta
        self._saved_page_states = []
        self._pin_dates()

    def _pin_dates(self):
        stamped = PDFDate(_Stamp(self._meta["emitido_em"]))
        self._doc.info.created = stamped
        self._doc.info.modified = stamped

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._pin_dates()
            _paint(self, self._meta, page_count)
            pdf_canvas.Canvas.showPage(self)
        self._pin_dates()
        pdf_canvas.Canvas.save(self)


def _paint(canvas, meta, page_count):
    page = canvas.getPageNumber()
    canvas.saveState()
    if meta["watermark"]:
        canvas.saveState()
        canvas.setFillColor(Color(0.56, 0.11, 0.18, alpha=0.12))
        canvas.setFont("Helvetica-Bold", 58)
        canvas.translate(PAGE_WIDTH / 2, PAGE_HEIGHT / 2)
        canvas.rotate(42)
        canvas.drawCentredString(0, 0, meta["watermark"])
        canvas.restoreState()
    if page > 1:
        header = f"MPC-PB | {meta['heading']} | {meta['period']}"
        size = 8
        while (
            size > 6.5
            and pdfmetrics.stringWidth(header, "Helvetica", size) > CONTENT_WIDTH
        ):
            size -= 0.25
        canvas.setFillColor(MUTED)
        canvas.setFont("Helvetica", size)
        canvas.drawString(LEFT, PAGE_HEIGHT - 12 * mm, header)
        canvas.setStrokeColor(LINE)
        canvas.setLineWidth(0.4)
        canvas.line(
            LEFT, PAGE_HEIGHT - 14 * mm, PAGE_WIDTH - RIGHT, PAGE_HEIGHT - 14 * mm
        )
    footer = (
        f"MPC-PB  ·  Dados consolidados até {meta['consolidado_em']}  ·  "
        f"Versão {meta['versao']}  ·  Página {page} / {page_count}"
    )
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica", 8)
    canvas.drawCentredString(PAGE_WIDTH / 2, 8 * mm, footer)
    canvas.restoreState()


def _meta(report, snapshot, emitted):
    status = report.get("status")
    watermark = None
    if status == "RASCUNHO":
        watermark = "RASCUNHO"
    elif status == "EM_REVISAO":
        watermark = "EM REVISÃO"
    return {
        "heading": document_title(snapshot),
        "formal": document_title(snapshot, formal=True),
        "period": period_label(snapshot),
        "versao": report.get("versao"),
        "corte": format_datetime_br(report.get("data_corte")),
        "emitido": format_datetime_br(emitted),
        "emitido_em": emitted,
        "consolidado_em": _consolidated_until(report, snapshot),
        "watermark": watermark,
        "partial": bool((snapshot.get("metadados") or {}).get("periodo_parcial")),
        "annual": (snapshot.get("metadados") or {}).get("tipo") == "ANUAL",
        "referencia": _reference_period(snapshot),
    }


def _reference_period(snapshot):
    metadata = snapshot.get("metadados") or {}
    months = (snapshot.get("cobertura_historica") or {}).get("meses_disponiveis") or []
    if metadata.get("tipo") == "ANUAL" and months:
        year, last = int(metadata["ano"]), max(int(month) for month in months)
        return f"01/01/{year} a {monthrange(year, last)[1]:02d}/{last:02d}/{year}"
    return format_report_period(snapshot)


def _consolidated_until(report, snapshot):
    metadata = snapshot.get("metadados") or {}
    months = (snapshot.get("cobertura_historica") or {}).get("meses_disponiveis") or []
    start = str(metadata.get("data_inicio") or "")[:10]
    if months and start:
        try:
            origin = date.fromisoformat(start)
            last_month = max(int(month) for month in months)
            end = date(origin.year, last_month, monthrange(origin.year, last_month)[1])
            return format_date_br(end)
        except (TypeError, ValueError):
            pass
    return format_date_br(report.get("data_corte"))


def _styles():
    return {
        "draft": ParagraphStyle(
            "pdf_draft",
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            alignment=TA_CENTER,
            textColor=BRAND,
            spaceAfter=8,
        ),
        "org": ParagraphStyle(
            "pdf_org",
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=15,
            alignment=TA_CENTER,
            textColor=INK,
        ),
        "title": ParagraphStyle(
            "pdf_title",
            fontName="Helvetica-Bold",
            fontSize=15,
            leading=19,
            alignment=TA_CENTER,
            textColor=BRAND,
            spaceBefore=8,
        ),
        "period": ParagraphStyle(
            "pdf_period",
            fontName="Helvetica",
            fontSize=12,
            leading=16,
            alignment=TA_CENTER,
            textColor=INK,
            spaceBefore=6,
        ),
        "meta": ParagraphStyle(
            "pdf_meta",
            fontName="Helvetica",
            fontSize=9,
            leading=13,
            alignment=TA_CENTER,
            textColor=MUTED,
        ),
        "h1": ParagraphStyle(
            "pdf_h1",
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            textColor=BRAND,
            spaceBefore=8,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "pdf_h2",
            fontName="Helvetica-Bold",
            fontSize=11,
            leading=14,
            textColor=INK,
            spaceBefore=6,
            spaceAfter=3,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "pdf_body",
            fontName="Helvetica",
            fontSize=10,
            leading=14,
            alignment=TA_JUSTIFY,
            textColor=INK,
            spaceAfter=6,
        ),
        "bullet": ParagraphStyle(
            "pdf_bullet",
            fontName="Helvetica",
            fontSize=10,
            leading=13,
            textColor=INK,
            leftIndent=12,
            bulletIndent=0,
            spaceAfter=2,
        ),
        "chart": ParagraphStyle(
            "pdf_chart",
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=INK,
            spaceBefore=4,
            spaceAfter=2,
            keepWithNext=True,
        ),
        "legend": ParagraphStyle(
            "pdf_legend",
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=INK,
        ),
        "metric_value": ParagraphStyle(
            "pdf_metric_value",
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            alignment=TA_CENTER,
            textColor=BRAND,
        ),
        "metric_label": ParagraphStyle(
            "pdf_metric_label",
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            alignment=TA_CENTER,
            textColor=MUTED,
        ),
        "th": ParagraphStyle(
            "pdf_th",
            fontName="Helvetica-Bold",
            fontSize=7.5,
            leading=9,
            alignment=TA_CENTER,
            textColor=white,
        ),
        "td_name": ParagraphStyle(
            "pdf_td_name",
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=INK,
        ),
        "td_num": ParagraphStyle(
            "pdf_td_num",
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            alignment=TA_RIGHT,
            textColor=INK,
        ),
        "note_title": ParagraphStyle(
            "pdf_note_title",
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=12,
            textColor=INK,
            spaceAfter=4,
        ),
        "note": ParagraphStyle(
            "pdf_note",
            fontName="Helvetica",
            fontSize=8.5,
            leading=11.5,
            textColor=HexColor("#3A4450"),
            spaceAfter=4,
        ),
        "source": ParagraphStyle(
            "pdf_source",
            fontName="Helvetica-Oblique",
            fontSize=8,
            leading=11,
            textColor=MUTED,
            spaceBefore=2,
        ),
    }


def _cover(meta, styles):
    story = []
    if meta["watermark"]:
        story.append(
            Paragraph(
                _escape(
                    f"{meta['watermark']} — documento em elaboração. "
                    "Não constitui a versão final."
                ),
                styles["draft"],
            )
        )
    logo = _logo(32 * mm)
    if logo is not None:
        story.extend([logo, Spacer(1, 6 * mm)])
    story.extend(
        [
            Paragraph("MINISTÉRIO PÚBLICO DE CONTAS", styles["org"]),
            Paragraph("DO ESTADO DA PARAÍBA", styles["org"]),
            Spacer(1, 8 * mm),
            HRFlowable(
                color=BRAND,
                thickness=1.5,
                width="42%",
                spaceBefore=0,
                spaceAfter=2,
                hAlign="CENTER",
            ),
            Paragraph(_escape(meta["formal"]), styles["title"]),
            Paragraph(_escape(meta["period"]), styles["period"]),
        ]
    )
    if not meta["annual"]:
        story.extend(
            [
                Spacer(1, 62 * mm),
                Paragraph(_escape(f"Versão {meta['versao']}"), styles["meta"]),
                Paragraph(_escape(f"Data de corte: {meta['corte']}"), styles["meta"]),
                Paragraph(
                    _escape(f"Data de emissão: {meta['emitido']}"), styles["meta"]
                ),
            ]
        )
        return story
    story.append(Spacer(1, 46 * mm))
    technical = [
        ["Período de referência", meta["referencia"]],
        ["Dados extraídos em", meta["emitido"].replace(" ", " às ", 1)],
        ["Dados consolidados até", meta["consolidado_em"]],
        ["Versão", str(meta["versao"])],
        ["Fonte", "Tramita/TCE-PB"],
    ]
    block = Table(technical, colWidths=[54 * mm, CONTENT_WIDTH - 54 * mm])
    block.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PAPER),
                ("BOX", (0, 0), (-1, -1), 0.5, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, LINE),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (0, -1), BRAND),
                ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("LEADING", (0, 0), (-1, -1), 11),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(block)
    return story


def _logo(width):
    if not LOGO.is_file():
        LOGGER.warning("Brasão institucional ausente; a capa segue sem a imagem.")
        return None
    image_width, image_height = ImageReader(str(LOGO)).getSize()
    height = width * image_height / float(image_width)
    return Image(str(LOGO), width=width, height=height, mask="auto", hAlign="CENTER")


def _body(report, snapshot, meta, styles):
    content = normalize_content(report.get("conteudo_estruturado"), snapshot)
    story = []
    story.extend(
        _section(styles, "Resumo executivo", _text(content, "resumo_executivo"))
    )
    if meta["annual"]:
        story.extend(_annual_highlights(snapshot, styles))
    metrics = _metrics(
        snapshot.get("indicadores_gerais") or {},
        annual=(meta["heading"].startswith("Relatório Anual")),
    )
    if metrics:
        story.extend(
            [
                CondPageBreak(90),
                Paragraph("Indicadores do período", styles["h1"]),
                metrics,
            ]
        )
    monthly = _monthly_rows(snapshot)
    story.extend(
        _section(styles, "Evolução do período", _text(content, "evolucao_periodo"))
    )
    story.extend(
        _chart(
            "Distribuídos e produção por mês",
            lambda: _bars(
                monthly,
                ["Distribuídos", "Produção"],
                compact=len(monthly) > 4,
                colors=ANNUAL_CHART_COLORS if meta["annual"] else None,
                height=172 if meta["annual"] else None,
            ),
            [
                (
                    "Distribuídos",
                    (ANNUAL_CHART_COLORS if meta["annual"] else CHART_COLORS)[
                        "Distribuídos"
                    ],
                ),
                (
                    "Produção",
                    (ANNUAL_CHART_COLORS if meta["annual"] else CHART_COLORS)[
                        "Produção"
                    ],
                ),
            ],
            styles,
            spacing=2 * mm if meta["annual"] else 3 * mm,
        )
    )
    story.extend(
        _section(
            styles, "Composição da produção", _text(content, "composicao_producao")
        )
    )
    story.extend(
        _chart(
            "Pareceres e cotas por mês",
            lambda: _bars(
                monthly,
                ["Pareceres", "Cotas"],
                stacked=True,
                compact=len(monthly) > 4,
                colors=ANNUAL_CHART_COLORS if meta["annual"] else None,
                height=168 if meta["annual"] else None,
            ),
            [
                (
                    "Pareceres",
                    (ANNUAL_CHART_COLORS if meta["annual"] else CHART_COLORS)[
                        "Pareceres"
                    ],
                ),
                (
                    "Cotas",
                    (ANNUAL_CHART_COLORS if meta["annual"] else CHART_COLORS)["Cotas"],
                ),
            ],
            styles,
            spacing=2 * mm if meta["annual"] else 3 * mm,
        )
    )
    story.extend(_section(styles, "Permanência", _text(content, "permanencia")))
    if meta["annual"]:
        story.extend(_annual_permanence_summary(snapshot, styles, compact=True))
    story.extend(
        _chart(
            "Mediana de permanência por mês",
            lambda: _median_line(
                monthly,
                compact=len(monthly) > 4,
                height=140 if meta["annual"] else None,
            ),
            [("Mediana de permanência", CHART_COLORS["Mediana de permanência"])],
            styles,
            spacing=2 * mm if meta["annual"] else 3 * mm,
        )
    )
    story.extend(
        _chart(
            "Faixas de permanência",
            lambda: _band_bars(
                snapshot,
                annual=meta["annual"],
                height=150 if meta["annual"] else None,
            ),
            (
                []
                if meta["annual"]
                else [("Processos", CHART_COLORS["Mediana de permanência"])]
            ),
            styles,
            spacing=2 * mm if meta["annual"] else 3 * mm,
        )
    )
    procuradores = _procurador_rows(snapshot)
    if meta["annual"]:
        story.extend(
            [
                PageBreak(),
                Paragraph("Produção por Procurador", styles["h1"]),
            ]
        )
        story.extend(
            _flow_text(
                _text(content, "producao_procurador"),
                styles["body"],
                styles["bullet"],
            )
        )
    else:
        story.extend(
            _section(
                styles, "Produção por Procurador", _text(content, "producao_procurador")
            )
        )
    table = _procurador_table(procuradores)
    if table is not None:
        story.extend([CondPageBreak(120), table, Spacer(1, 4 * mm)])
    if meta["annual"]:
        story.append(
            Paragraph(
                "Os indicadores individuais refletem o conjunto de processos movimentados no período e não constituem, isoladamente, avaliação de desempenho, devendo ser considerados juntamente com o perfil e a complexidade do acervo.",
                styles["note"],
            )
        )
    story.extend(
        _chart(
            "Distribuídos e produção por Procurador",
            lambda: _procurador_bars(
                procuradores,
                ["Distribuídos", "Produção"],
                colors=ANNUAL_CHART_COLORS if meta["annual"] else None,
            ),
            [
                (
                    "Distribuídos",
                    (ANNUAL_CHART_COLORS if meta["annual"] else CHART_COLORS)[
                        "Distribuídos"
                    ],
                ),
                (
                    "Produção",
                    (ANNUAL_CHART_COLORS if meta["annual"] else CHART_COLORS)[
                        "Produção"
                    ],
                ),
            ],
            styles,
        )
    )
    if meta["annual"]:
        story.extend(
            _chart(
                "Composição de Pareceres e Cotas por Procurador",
                lambda: _procurador_composition(procuradores),
                [
                    ("Pareceres", ANNUAL_CHART_COLORS["Pareceres"]),
                    ("Cotas", ANNUAL_CHART_COLORS["Cotas"]),
                ],
                styles,
            )
        )
    else:
        story.extend(
            _chart(
                "Pareceres e cotas por Procurador",
                lambda: _procurador_bars(procuradores, ["Pareceres", "Cotas"]),
                [
                    ("Pareceres", CHART_COLORS["Pareceres"]),
                    ("Cotas", CHART_COLORS["Cotas"]),
                ],
                styles,
            )
        )
    story.extend(_comparison(snapshot, content, styles))
    story.extend(_synthesis(content, styles, annual=meta["annual"]))
    story.extend(_methodology(content, meta, styles))
    return story


def _annual_highlights(snapshot, styles):
    facts = snapshot.get("fatos_anuais") or {}
    summary = snapshot.get("indicadores_gerais") or {}
    if not facts:
        return []
    balance = facts.get("saldo_fluxo")
    rate = facts.get("indice_fluxo")
    peak_p, peak_d = (
        facts.get("pico_producao") or {},
        facts.get("pico_distribuicoes") or {},
    )
    bullets = []
    if balance is not None and rate is not None:
        bullets.append(
            f"A produção registrada foi de {format_count(summary.get('production'))}, frente a {format_count(summary.get('distributed'))} distribuições, com saldo de {float(balance):+.0f} registros e relação de {format_decimal(rate, '%')}."
        )
    if peak_p and peak_d:
        bullets.append(
            f"O maior volume de produção ocorreu em {MONTHS[int(peak_p['mes']) - 1].lower()}, com {format_count(peak_p['producao'])} registros; o de distribuições, em {MONTHS[int(peak_d['mes']) - 1].lower()}, com {format_count(peak_d['distribuicoes'])}."
        )
    if summary.get("median_days") is not None:
        bullets.append(
            f"A mediana geral de permanência foi de {format_decimal(summary['median_days'], ' dias')}."
        )
    if facts.get("quantidade_mais_60") is not None:
        bullets.append(
            f"{format_count(facts['quantidade_mais_60'])} registros apresentaram permanência superior a 60 dias, dos quais {format_count(facts.get('quantidade_mais_90') or 0)} acima de 90 dias."
        )
    if not bullets:
        return []
    return [
        CondPageBreak(78),
        Paragraph("Destaques do período", styles["h1"]),
        *[Paragraph("• " + _escape(item), styles["bullet"]) for item in bullets],
    ]


def _annual_permanence_summary(snapshot, styles, *, compact=False):
    facts = snapshot.get("fatos_anuais") or {}
    entries = (
        ("P75", format_decimal(facts.get("p75_permanencia"), " dias")),
        ("P90", format_decimal(facts.get("p90_permanencia"), " dias")),
        (
            "Acima de 60 dias",
            f"{format_count(facts.get('quantidade_mais_60'))} · {format_decimal(facts.get('percentual_mais_60'), '%')}",
        ),
        (
            "Acima de 90 dias",
            f"{format_count(facts.get('quantidade_mais_90'))} · {format_decimal(facts.get('percentual_mais_90'), '%')}",
        ),
    )
    if not any(
        facts.get(key) is not None
        for key in (
            "p75_permanencia",
            "p90_permanencia",
            "quantidade_mais_60",
            "quantidade_mais_90",
        )
    ):
        return []
    cards = [
        [
            Paragraph(_escape(value), styles["metric_value"]),
            Paragraph(_escape(label), styles["metric_label"]),
        ]
        for label, value in entries
    ]
    table = Table([cards], colWidths=[CONTENT_WIDTH / 4] * 4)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NOTE_PAPER),
                ("BOX", (0, 0), (-1, -1), 0.35, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 2 if compact else 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2 if compact else 4),
            ]
        )
    )
    return [table, Spacer(1, 1 * mm if compact else 2 * mm)]


def _section(styles, title, text):
    blocks = _flow_text(text, styles["body"], styles["bullet"])
    if not blocks:
        return []
    return [CondPageBreak(80), Paragraph(title, styles["h1"]), *blocks]


def _text(content, key):
    return (content.get(key) or {}).get("texto") or ""


def _metrics(summary, *, annual=False):
    fields = (
        ("distributed", "Distribuídos", "count"),
        ("production", "Produção", "count"),
        ("opinions", "Pareceres", "count"),
        ("quotas", "Cotas", "count"),
        ("production_rate", "Produção/Distribuições", "percent"),
        ("median_days", "Mediana de permanência", "days"),
    )
    if annual:
        fields = (
            ("production", "Produção", "count"),
            ("distributed", "Distribuições", "count"),
            ("flow_balance", "Saldo do fluxo", "signed"),
            ("opinions", "Pareceres", "count"),
            ("quotas", "Cotas", "count"),
            ("median_days", "Mediana de permanência", "days"),
            ("production_rate", "Relação Produção / Distribuições", "percent"),
        )
        summary = {
            **summary,
            "flow_balance": (summary.get("production") or 0)
            - (summary.get("distributed") or 0),
        }
    present = [item for item in fields if item[0] in summary]
    if not present:
        return None
    styles = _styles()
    cells = []
    for key, label, kind in present:
        value = summary[key]
        if kind == "count":
            shown = format_count(value)
        elif kind == "percent":
            shown = format_decimal(value, "%")
        elif kind == "signed":
            shown = f"{float(value):+.0f}" if value else "0"
        else:
            shown = format_decimal(value, " dias")
        cells.append(
            [
                Paragraph(_escape(shown), styles["metric_value"]),
                Paragraph(_escape(label), styles["metric_label"]),
            ]
        )
    complement = cells.pop() if annual and len(cells) == 7 else None
    while len(cells) % 3:
        cells.append("")
    rows = [cells[index : index + 3] for index in range(0, len(cells), 3)]
    column = CONTENT_WIDTH / 3
    if complement is not None:
        rows.append([complement, "", ""])
    table = Table(rows, colWidths=[column, column, column])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), PAPER),
                ("BOX", (0, 0), (-1, -1), 0.4, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.3, LINE),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                *(
                    (
                        [
                            ("SPAN", (0, 2), (2, 2)),
                            ("BACKGROUND", (0, 2), (2, 2), NOTE_PAPER),
                            ("TOPPADDING", (0, 2), (2, 2), 5),
                            ("BOTTOMPADDING", (0, 2), (2, 2), 5),
                        ]
                        if complement is not None
                        else []
                    )
                ),
            ]
        )
    )
    table.keepWithNext = False
    return table


def format_count(value):
    if value is None:
        return "—"
    return f"{int(round(float(value))):,}".replace(",", ".")


def format_decimal(value, suffix=""):
    if value is None:
        return "—"
    return f"{float(value):.1f}".replace(".", ",") + suffix


def _monthly_rows(snapshot):
    required = (
        "distributed",
        "production",
        "opinions",
        "quotas",
        "median_days",
        "production_rate",
    )
    rows = []
    for item in snapshot.get("serie_mensal") or []:
        if not isinstance(item, dict):
            continue
        summary = item.get("summary")
        month = item.get("month")
        if not isinstance(summary, dict) or not month:
            continue
        if any(key not in summary for key in required):
            continue
        rows.append(
            {
                "Mês": MONTHS[int(month) - 1],
                "Distribuídos": summary["distributed"],
                "Produção": summary["production"],
                "Pareceres": summary["opinions"],
                "Cotas": summary["quotas"],
                "Mediana de permanência": summary["median_days"],
            }
        )
    return rows


def _procurador_rows(snapshot):
    rows = []
    for row in snapshot.get("por_procurador") or []:
        if not isinstance(row, dict) or not row.get("procurador"):
            continue
        rows.append(
            {
                "Procurador": row["procurador"],
                "Distribuídos": row.get("distributed"),
                "Produção": row.get("production"),
                "Pareceres": row.get("opinions"),
                "Cotas": row.get("quotas"),
                "Produção/Distribuições": row.get("production_rate"),
                "Mediana de permanência": row.get("median_days"),
            }
        )
    return rows


def _comparison_rows(snapshot):
    prior = snapshot.get("comparacao_periodo_anterior")
    summary = snapshot.get("indicadores_gerais") or {}
    metadata = snapshot.get("metadados") or {}
    if not isinstance(prior, dict):
        return None
    needed = ("distributed", "production", "opinions", "quotas")
    if any(key not in prior or key not in summary for key in needed):
        return None
    if metadata.get("tipo") == "TRIMESTRAL":
        quarter = int(metadata.get("trimestre") or 0)
        prior_label = f"{quarter - 1 if quarter > 1 else 4}º trimestre"
        current_label = f"{quarter}º trimestre"
    else:
        year = int(metadata.get("ano") or 0)
        prior_label = str(year - 1)
        current_label = str(year)

    def pack(label, source):
        return {
            "Período": label,
            "Distribuídos": source["distributed"],
            "Produção": source["production"],
            "Pareceres": source["opinions"],
            "Cotas": source["quotas"],
        }

    return [pack(prior_label, prior), pack(current_label, summary)]


def _bars(
    rows,
    fields,
    *,
    stacked=False,
    compact=False,
    category="Mês",
    colors=None,
    height=None,
):
    if not rows:
        return None
    series = [_numbers(rows, field) for field in fields]
    if any(item is None for item in series):
        return None
    if category == "Mês":
        categories = [_month_axis(row["Mês"], compact) for row in rows]
    else:
        categories = [str(row[category]) for row in rows]
    return _vertical_bars(
        categories,
        series,
        [(colors or CHART_COLORS)[field] for field in fields],
        stacked,
        height=height,
    )


def _median_line(rows, *, compact=False, height=None):
    if not rows:
        return None
    values = _numbers(rows, "Mediana de permanência")
    if not values:
        return None
    categories = [_month_axis(row["Mês"], compact) for row in rows]
    return _line_chart(
        categories, values, CHART_COLORS["Mediana de permanência"], height=height
    )


def _band_bars(snapshot, *, annual=False, height=None):
    order = (
        "0–7 dias",
        "8–15 dias",
        "16–30 dias",
        "31–60 dias",
        "61–90 dias",
        "Mais de 90 dias",
    )
    rows = []
    for item in snapshot.get("faixas_permanencia") or []:
        if (
            isinstance(item, dict)
            and item.get("faixa")
            and item.get("quantidade") is not None
        ):
            rows.append(item)
    if not rows:
        return None
    rows.sort(
        key=lambda item: order.index(item["faixa"]) if item["faixa"] in order else 99
    )
    return _horizontal_bars(
        [str(item["faixa"]) for item in rows],
        [[float(item["quantidade"]) for item in rows]],
        [CHART_COLORS["Mediana de permanência"]],
        bar_colors=ANNUAL_CHART_COLORS["Faixas de permanência"] if annual else None,
        height=height,
    )


def _procurador_bars(rows, fields, *, colors=None):
    if not rows:
        return None
    series = [_numbers(rows, field) for field in fields]
    if any(item is None for item in series):
        return None
    return _horizontal_bars(
        [row["Procurador"] for row in rows],
        series,
        [(colors or CHART_COLORS)[field] for field in fields],
    )


def _procurador_composition(rows):
    """Annual-only 100% bars: profile, not a duplicate of absolute totals."""
    usable = []
    for row in rows:
        opinions, quotas = row.get("Pareceres"), row.get("Cotas")
        total = float(opinions or 0) + float(quotas or 0)
        if total:
            usable.append(
                (
                    row["Procurador"],
                    float(opinions or 0) / total * 100,
                    float(quotas or 0) / total * 100,
                )
            )
    if not usable:
        return None
    return _horizontal_bars(
        [item[0] for item in usable],
        [[item[1] for item in usable], [item[2] for item in usable]],
        [ANNUAL_CHART_COLORS["Pareceres"], ANNUAL_CHART_COLORS["Cotas"]],
        stacked=True,
    )


def _numbers(rows, field):
    values = []
    for row in rows:
        if field not in row or row[field] is None:
            return None
        try:
            values.append(float(row[field]))
        except (TypeError, ValueError):
            return None
    return values


def _month_axis(name, compact):
    return name[:3] if compact else name


def _vertical_bars(categories, series, colors, stacked, *, height=None):
    height = height or (168 if len(categories) <= 4 else 188)
    drawing = Drawing(CONTENT_WIDTH, height)
    chart = VerticalBarChart()
    chart.x = 38
    chart.y = 32
    chart.width = CONTENT_WIDTH - 50
    chart.height = height - 48
    chart.data = series
    chart.categoryAxis.categoryNames = categories
    chart.categoryAxis.style = "stacked" if stacked else "parallel"
    chart.groupSpacing = 8
    chart.barSpacing = 1.5
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = _axis_max(series, stacked)
    _style_axes(chart, decimal=False)
    _paint_bars(chart, colors)
    drawing.add(chart)
    return drawing


def _horizontal_bars(
    categories, series, colors, stacked=False, bar_colors=None, height=None
):
    height = height or min(440, max(130, 24 * len(categories) + 36))
    drawing = Drawing(CONTENT_WIDTH, height)
    chart = HorizontalBarChart()
    label_width = 10 + max(
        pdfmetrics.stringWidth(name, "Helvetica", 7) for name in categories
    )
    label_width = min(max(label_width, 90), CONTENT_WIDTH * 0.48)
    chart.x = label_width
    chart.y = 22
    chart.width = CONTENT_WIDTH - label_width - 8
    chart.height = height - 36
    categories = list(reversed(categories))
    series = [list(reversed(values)) for values in series]
    chart.data = series
    chart.categoryAxis.style = "stacked" if stacked else "parallel"
    chart.categoryAxis.categoryNames = categories
    chart.categoryAxis.labels.boxAnchor = "e"
    chart.categoryAxis.labels.dx = -4
    chart.categoryAxis.labels.fontSize = 7
    chart.groupSpacing = 6
    chart.barSpacing = 1
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100 if stacked else _axis_max(series, False)
    _style_axes(chart, decimal=False)
    _paint_bars(chart, colors)
    if bar_colors:
        for index, color in enumerate(reversed(bar_colors)):
            chart.bars[(0, index)].fillColor = HexColor(color)
            chart.bars[(0, index)].strokeColor = HexColor(color)
    drawing.add(chart)
    return drawing


def _line_chart(categories, values, color, *, height=None):
    height = height or 156
    drawing = Drawing(CONTENT_WIDTH, height)
    chart = HorizontalLineChart()
    chart.x = 38
    chart.y = 32
    chart.width = CONTENT_WIDTH - 50
    chart.height = height - 48
    chart.data = [values]
    chart.categoryAxis.categoryNames = categories
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = _axis_max([values], False)
    _style_axes(chart, decimal=True)
    paint = HexColor(color)
    chart.lines[0].strokeColor = paint
    chart.lines[0].strokeWidth = 1.6
    chart.lines[0].symbol = makeMarker("FilledCircle")
    chart.lines[0].symbol.size = 3.5
    chart.lines[0].symbol.fillColor = paint
    chart.lines[0].symbol.strokeColor = paint
    drawing.add(chart)
    return drawing


def _style_axes(chart, *, decimal):
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labels.fillColor = MUTED
    chart.valueAxis.strokeColor = HexColor("#C5CDD4")
    chart.valueAxis.labelTextFormat = _decimal_axis if decimal else _count_axis
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 7
    chart.categoryAxis.labels.fillColor = INK
    chart.categoryAxis.strokeColor = HexColor("#C5CDD4")


def _paint_bars(chart, colors):
    for index, color in enumerate(colors):
        paint = HexColor(color)
        chart.bars[index].fillColor = paint
        chart.bars[index].strokeColor = paint
        chart.bars[index].strokeWidth = 0.1


def _axis_max(series, stacked):
    if stacked:
        peak = max(sum(point) for point in zip(*series))
    else:
        peak = max(value for seq in series for value in seq)
    if peak <= 0:
        return 1
    padded = peak * 1.12
    magnitude = 10 ** math.floor(math.log10(padded))
    step = magnitude / 2 if padded / magnitude < 5 else magnitude
    return math.ceil(padded / step) * step


def _count_axis(value):
    return format_count(value)


def _decimal_axis(value):
    return format_decimal(value)


def _chart(title, builder, legend, styles, *, spacing=3 * mm):
    try:
        drawing = builder()
    except Exception:
        LOGGER.exception("Gráfico institucional omitido: %s", title)
        return []
    if drawing is None:
        LOGGER.info("Gráfico institucional sem dados e não desenhado: %s", title)
        return []
    block = [
        Paragraph(_escape(title), styles["chart"]),
        drawing,
    ]
    if legend:
        block.append(_legend(legend, styles))
    block.append(Spacer(1, spacing))
    try:
        return [KeepTogether(block)]
    except Exception:
        LOGGER.exception("Gráfico institucional omitido na paginação: %s", title)
        return []


def _legend(items, styles):
    row = []
    widths = []
    for label, color in items:
        swatch = Table([[""]], colWidths=[8], rowHeights=[8])
        swatch.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), HexColor(color)),
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ]
            )
        )
        label_width = pdfmetrics.stringWidth(label, "Helvetica", 8) + 10
        row.extend([swatch, Paragraph(_escape(label), styles["legend"])])
        widths.extend([12, label_width])
    table = Table([row], colWidths=widths)
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return table


def _procurador_table(rows):
    if not rows:
        return None
    styles = _styles()
    headers = [
        "Procurador",
        "Distribuídos",
        "Produção",
        "Pareceres",
        "Cotas",
        "Produção/<br/>Distribuições",
        "Mediana de<br/>permanência",
    ]
    header = [Paragraph(item, styles["th"]) for item in headers]
    data = [header]
    for row in rows:
        data.append(
            [
                Paragraph(_escape(row["Procurador"]), styles["td_name"]),
                Paragraph(_escape(format_count(row["Distribuídos"])), styles["td_num"]),
                Paragraph(_escape(format_count(row["Produção"])), styles["td_num"]),
                Paragraph(_escape(format_count(row["Pareceres"])), styles["td_num"]),
                Paragraph(_escape(format_count(row["Cotas"])), styles["td_num"]),
                Paragraph(
                    _escape(format_decimal(row["Produção/Distribuições"], "%")),
                    styles["td_num"],
                ),
                Paragraph(
                    _escape(format_decimal(row["Mediana de permanência"], " dias")),
                    styles["td_num"],
                ),
            ]
        )
    widths = [176, 52, 48, 48, 40, 72, 68]
    scale = CONTENT_WIDTH / sum(widths)
    widths = [item * scale for item in widths]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.splitByRow = 1
    if hasattr(table, "splitInRow"):
        table.splitInRow = 0
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BRAND),
                ("TEXTCOLOR", (0, 0), (-1, 0), white),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
                ("GRID", (0, 0), (-1, -1), 0.3, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [white, HexColor("#FBF7F6")]),
            ]
        )
    )
    return table


def _comparison(snapshot, content, styles):
    text = _text(content, "comparacao_periodo_anterior")
    rows = _comparison_rows(snapshot)
    annual = (snapshot.get("metadados") or {}).get("tipo") == "ANUAL"
    notice = ""
    if rows is None:
        notice = ANNUAL_WITHOUT_PRIOR if annual else PRIOR_WITHOUT_DATA
        if notice.casefold() in text.casefold():
            notice = ""
    blocks = _flow_text(text, styles["body"], styles["bullet"])
    if not blocks and rows is None and not notice:
        return []
    story = [
        CondPageBreak(90),
        Paragraph("Comparação com período anterior", styles["h1"]),
    ]
    story.extend(blocks)
    if notice:
        story.append(Paragraph(_escape(notice), styles["body"]))
    if rows:
        story.extend(
            _chart(
                "Período atual e período anterior",
                lambda: _bars(
                    rows,
                    ["Distribuídos", "Produção", "Pareceres", "Cotas"],
                    category="Período",
                ),
                [
                    ("Distribuídos", CHART_COLORS["Distribuídos"]),
                    ("Produção", CHART_COLORS["Produção"]),
                    ("Pareceres", CHART_COLORS["Pareceres"]),
                    ("Cotas", CHART_COLORS["Cotas"]),
                ],
                styles,
            )
        )
    return story


def _synthesis(content, styles, *, annual=False):
    raw = _text(content, "sintese_pontos_atencao")
    if not raw.strip():
        return []
    prose, attention = _split_attention(raw)
    story = [
        CondPageBreak(90),
        Paragraph(
            "SÍNTESE GERENCIAL E METODOLOGIA" if annual else "Síntese do período",
            styles["h1"],
        ),
    ]
    if prose and attention:
        story.append(
            Paragraph("Síntese gerencial" if annual else "Síntese", styles["h2"])
        )
    if prose:
        story.extend(_flow_text(prose, styles["body"], styles["bullet"]))
    if attention:
        story.append(Paragraph("Pontos de atenção", styles["h2"]))
        story.extend(_flow_text(attention, styles["body"], styles["bullet"]))
    return story


def _split_attention(text):
    match = _ATTENTION.search(text)
    if not match:
        return text.strip(), ""
    prose = _SYNTHESIS_HEADING.sub("", text[: match.start()]).strip()
    return prose, text[match.end() :].strip()


def _methodology(content, meta, styles):
    note = _readable_note(_text(content, "nota_metodologica"))
    flow = []
    if note:
        flow.append(Paragraph("Nota metodológica", styles["note_title"]))
        flow.extend(_flow_text(note, styles["note"], styles["note"]))
    if meta["annual"]:
        flow.append(
            Paragraph(
                "As distribuições e produções contabilizadas no período representam eventos ocorridos no intervalo de referência e não necessariamente correspondem à mesma coorte de processos.",
                styles["note"],
            )
        )
    flow.append(Paragraph(_escape(SOURCE), styles["source"]))
    flow.append(
        Paragraph(
            _escape(f"Dados consolidados até {meta['consolidado_em']}."),
            styles["source"],
        )
    )
    box = Table([[flow]], colWidths=[CONTENT_WIDTH])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), NOTE_PAPER),
                ("BOX", (0, 0), (-1, -1), 0.4, NOTE_LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return [CondPageBreak(110), box]


def _readable_note(text):
    """Keep the methodological sentences and leave out an internal version token."""
    kept = []
    for block in re.split(r"\n\s*\n", _clean(text)):
        token = block.strip()
        if re.fullmatch(r"[\w.-]+", token) and any(
            character.isdigit() for character in token
        ):
            continue
        kept.append(block)
    return "\n\n".join(kept).strip()


def _flow_text(text, body, bullet):
    cleaned = _clean(text)
    if not cleaned:
        return []
    flow = []
    for block in re.split(r"\n\s*\n", cleaned):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        prose = []

        def flush():
            if prose:
                flow.append(Paragraph(_escape(" ".join(prose)), body))
                prose.clear()

        for line in lines:
            if _BULLET.match(line):
                flush()
                flow.append(Paragraph("• " + _escape(_BULLET.sub("", line)), bullet))
            else:
                prose.append(line)
        flush()
    return flow


def _clean(text):
    if text is None:
        return ""
    value = str(text).replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value)
    value = re.sub(r"</?[^>\n]+>", "", value)
    return value.strip()


def _escape(text):
    return html.escape(str(text), quote=False)
