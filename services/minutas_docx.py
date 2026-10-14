"""Exportação DOCX institucional das minutas experimentais já geradas."""

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import re
import unicodedata

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_ORIENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt, RGBColor

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
ELVIRA_TEMPLATE = (
    PROJECT_ROOT / "modelos" / "modelo_visual_elvira_bananeiras_convertido.docx"
)


@dataclass(frozen=True)
class MinutaDocx:
    name: str
    mime: str
    content: bytes


class MinutasDocxError(ValueError):
    pass


_PROFILES = {
    "Elvira Samara": {
        "nome": "ELVIRA SAMARA PEREIRA DE OLIVEIRA",
        "cargo": (
            "Procuradora-Geral do Ministério Público de Contas do Estado da Paraíba"
        ),
        "template": ELVIRA_TEMPLATE,
    }
}
_ALINEA = re.compile(r"^\s*[a-z]\s*[).:-]\s*(.+)$", re.IGNORECASE)


def _plain(value):
    decomposed = unicodedata.normalize("NFKD", str(value or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _canonical_process_key(value):
    normalized = re.sub(r"\s+", " ", _plain(value)).strip()
    normalized = re.sub(r"^processo\s*", "", normalized)
    normalized = re.sub(r"^tc\s*[-:]?\s*", "", normalized)
    if not re.fullmatch(r"\d{5}\s*/?\s*\d{2}", normalized):
        return ""
    return re.sub(r"\D", "", normalized)


def _normalized_support(value):
    return re.sub(r"\s+", " ", _plain(value)).strip()


def _set_run_font(run, size, *, bold=None, italic=None):
    run.font.name = "Arial"
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = RGBColor(0, 0, 0)
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:ascii"), "Arial")
    fonts.set(qn("w:hAnsi"), "Arial")
    fonts.set(qn("w:eastAsia"), "Arial")
    fonts.set(qn("w:cs"), "Arial")


def _configure_style(
    document,
    name,
    *,
    size=12,
    bold=False,
    italic=False,
    alignment=WD_ALIGN_PARAGRAPH.JUSTIFY,
    left_cm=0,
    right_cm=0,
    first_cm=0,
    before_pt=0,
    after_pt=6,
    keep_next=False,
):
    styles = document.styles
    style = styles[name] if name in styles else styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
    style.font.name = "Arial"
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.italic = italic
    style.font.color.rgb = RGBColor(0, 0, 0)
    fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:ascii"), "Arial")
    fonts.set(qn("w:hAnsi"), "Arial")
    fonts.set(qn("w:eastAsia"), "Arial")
    fonts.set(qn("w:cs"), "Arial")
    fmt = style.paragraph_format
    fmt.alignment = alignment
    fmt.left_indent = Cm(left_cm)
    fmt.right_indent = Cm(right_cm)
    fmt.first_line_indent = Cm(first_cm)
    fmt.space_before = Pt(before_pt)
    fmt.space_after = Pt(after_pt)
    fmt.line_spacing = 1
    fmt.keep_with_next = keep_next
    fmt.keep_together = keep_next
    return style


def _configure_styles(document):
    _configure_style(
        document,
        "Normal",
        size=12,
        first_cm=3,
        after_pt=6,
    )
    _configure_style(
        document,
        "Minuta Identificação",
        size=12,
        bold=True,
        alignment=WD_ALIGN_PARAGRAPH.LEFT,
        after_pt=0,
    )
    _configure_style(
        document,
        "Minuta Ementa",
        size=11,
        left_cm=6.752,
        after_pt=36,
    )
    _configure_style(document, "Minuta Corpo", size=12, first_cm=3, after_pt=12)
    _configure_style(
        document,
        "Minuta Transição",
        size=12,
        first_cm=3,
        after_pt=12,
        keep_next=True,
    )
    _configure_style(
        document,
        "Minuta Irregularidade",
        size=12,
        bold=True,
        italic=True,
        left_cm=3,
        after_pt=6,
        keep_next=True,
    )
    _configure_style(
        document,
        "Minuta Transcrição",
        size=11,
        left_cm=3,
        right_cm=0.13,
        after_pt=6,
    )
    _configure_style(
        document,
        "Minuta Pedido",
        size=12,
        left_cm=3.741,
        first_cm=-0.635,
        after_pt=6,
    )
    _configure_style(
        document,
        "Minuta Alínea",
        size=12,
        left_cm=4.251,
        right_cm=0.13,
        first_cm=-0.499,
        after_pt=6,
    )
    _configure_style(document, "Minuta Encerramento", size=12, first_cm=3, after_pt=6)
    _configure_style(
        document,
        "Minuta Procuradora",
        size=14,
        bold=True,
        italic=True,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        after_pt=0,
    )
    _configure_style(
        document,
        "Minuta Cargo",
        size=11,
        alignment=WD_ALIGN_PARAGRAPH.CENTER,
        after_pt=0,
    )
    _configure_style(
        document,
        "Minuta Revisão Título",
        size=12,
        bold=True,
        alignment=WD_ALIGN_PARAGRAPH.LEFT,
        before_pt=0,
        after_pt=12,
        keep_next=True,
    )
    _configure_style(
        document,
        "Minuta Revisão Item",
        size=10,
        alignment=WD_ALIGN_PARAGRAPH.LEFT,
        left_cm=0.75,
        first_cm=-0.5,
        after_pt=6,
    )


def _template_logo(template_path):
    if not template_path.is_file():
        raise MinutasDocxError(
            "O modelo visual institucional da Procuradora não está disponível."
        )
    try:
        template = Document(template_path)
    except Exception as exc:
        raise MinutasDocxError("O modelo visual institucional é inválido.") from exc
    for section in template.sections:
        for relationship in section.header.part.rels.values():
            if relationship.reltype.endswith("/image"):
                return relationship.target_part.blob
    raise MinutasDocxError("A logomarca institucional não foi localizada no modelo.")


def _next_numbering_id(numbering, tag):
    values = []
    for element in numbering.findall(qn(f"w:{tag}")):
        value = element.get(qn(f"w:{tag}Id"))
        if value is not None:
            values.append(int(value))
    return max(values, default=-1) + 1


def _numbering_definition(document, *, kind, marker, left_twips, hanging_twips):
    numbering = document.part.numbering_part.element
    abstract_id = _next_numbering_id(numbering, "abstractNum")
    number_id = _next_numbering_id(numbering, "num")

    abstract = OxmlElement("w:abstractNum")
    abstract.set(qn("w:abstractNumId"), str(abstract_id))
    multi = OxmlElement("w:multiLevelType")
    multi.set(qn("w:val"), "singleLevel")
    abstract.append(multi)
    level = OxmlElement("w:lvl")
    level.set(qn("w:ilvl"), "0")
    start = OxmlElement("w:start")
    start.set(qn("w:val"), "1")
    number_format = OxmlElement("w:numFmt")
    number_format.set(qn("w:val"), kind)
    level_text = OxmlElement("w:lvlText")
    level_text.set(qn("w:val"), marker)
    suffix = OxmlElement("w:suff")
    suffix.set(qn("w:val"), "space")
    paragraph_properties = OxmlElement("w:pPr")
    indentation = OxmlElement("w:ind")
    indentation.set(qn("w:left"), str(left_twips))
    indentation.set(qn("w:hanging"), str(hanging_twips))
    paragraph_properties.append(indentation)
    level.extend((start, number_format, level_text, suffix, paragraph_properties))
    abstract.append(level)
    first_number = numbering.find(qn("w:num"))
    if first_number is None:
        numbering.append(abstract)
    else:
        numbering.insert(numbering.index(first_number), abstract)

    number = OxmlElement("w:num")
    number.set(qn("w:numId"), str(number_id))
    abstract_reference = OxmlElement("w:abstractNumId")
    abstract_reference.set(qn("w:val"), str(abstract_id))
    number.append(abstract_reference)
    numbering.append(number)
    return number_id


def _apply_numbering(paragraph, number_id):
    properties = paragraph._p.get_or_add_pPr()
    number_properties = properties.find(qn("w:numPr"))
    if number_properties is None:
        number_properties = OxmlElement("w:numPr")
        properties.append(number_properties)
    level = OxmlElement("w:ilvl")
    level.set(qn("w:val"), "0")
    identifier = OxmlElement("w:numId")
    identifier.set(qn("w:val"), str(number_id))
    number_properties.extend((level, identifier))


def _add_text(paragraph, text, *, size=12, bold=None, italic=None):
    lines = str(text).splitlines() or [""]
    for index, line in enumerate(lines):
        if index:
            paragraph.add_run().add_break()
        run = paragraph.add_run(line)
        _set_run_font(run, size, bold=bold, italic=italic)


def _blocks(value, structured=None):
    if isinstance(structured, list):
        return [
            block.strip()
            for item in structured
            for block in re.split(r"\n\s*\n", str(item or "").strip())
            if block.strip()
        ]
    return [
        block.strip()
        for block in re.split(r"\n\s*\n", str(value or "").strip())
        if block.strip()
    ]


def _add_body(document, value, style="Minuta Corpo", *, paragraphs=None):
    for block in _blocks(value, paragraphs):
        if block == "É o relatório. Passa-se a opinar.":
            selected_style = "Minuta Transição"
        elif block.startswith(('"', "“", "‘", "'")):
            selected_style = "Minuta Transcrição"
        else:
            selected_style = style
        paragraph = document.add_paragraph(style=selected_style)
        _add_text(paragraph, block, size=11 if selected_style == "Minuta Transcrição" else 12)


def _conclusion(structure):
    request_keys = {
        _normalized_support(request) for request in structure.get("pedidos", ())
    }
    kept = []
    for line in str(structure.get("conclusao_pedidos") or "").splitlines():
        unnumbered = re.sub(r"^\s*(?:\d+[.)]|[-*])\s*", "", line)
        if _normalized_support(unnumbered) not in request_keys:
            kept.append(line)
    return "\n".join(kept).strip()


def _closing(value):
    if re.search(r"\bé\s+o\s+parecer\.", str(value or ""), re.IGNORECASE):
        return "É o Parecer."
    return str(value or "").strip()


def _add_requests(document, requests):
    decimal_id = _numbering_definition(
        document,
        kind="decimal",
        marker="%1.",
        left_twips=2121,
        hanging_twips=360,
    )
    for request in requests:
        lines = [line.strip() for line in str(request).splitlines() if line.strip()]
        if not lines:
            continue
        paragraph = document.add_paragraph(style="Minuta Pedido")
        _apply_numbering(paragraph, decimal_id)
        _add_text(paragraph, lines[0], bold=True)
        alineas = []
        for line in lines[1:]:
            match = _ALINEA.match(line)
            if not match:
                paragraph.add_run().add_break()
                _add_text(paragraph, line, bold=True)
                continue
            alineas.append(match.group(1).strip())
        if alineas:
            alpha_id = _numbering_definition(
                document,
                kind="lowerLetter",
                marker="%1)",
                left_twips=2410,
                hanging_twips=283,
            )
            for item in alineas:
                subparagraph = document.add_paragraph(style="Minuta Alínea")
                _apply_numbering(subparagraph, alpha_id)
                _add_text(subparagraph, item)


def _filename(target):
    key = _canonical_process_key(target.get("numero_processo")) or _canonical_process_key(
        target.get("processo_chave")
    )
    if len(key) == 7:
        return f"MINUTA_PARECER_TC_{key[:5]}_{key[5:]}.docx"
    return "MINUTA_PARECER.docx"


def _build_docx(target, structure, profile, header_lines):
    document = Document()
    section = document.sections[0]
    section.orientation = WD_ORIENT.PORTRAIT
    section.page_width = Cm(21.001)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(6.35)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(2.75)
    section.right_margin = Cm(1.75)
    section.header_distance = Cm(0.5)
    section.footer_distance = Cm(0)
    _configure_styles(document)

    header = section.header
    paragraph = header.paragraphs[0]
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.left_indent = Cm(0)
    paragraph.paragraph_format.right_indent = Cm(0)
    paragraph.paragraph_format.first_line_indent = Cm(0)
    paragraph.paragraph_format.space_before = Pt(0)
    paragraph.paragraph_format.space_after = Pt(0)
    run = paragraph.add_run()
    logo = run.add_picture(
        BytesIO(_template_logo(Path(profile["template"]))), width=Inches(2.62)
    )
    logo._inline.docPr.set("title", "MPC-PB")
    logo._inline.docPr.set(
        "descr", "Logomarca do Ministério Público de Contas da Paraíba"
    )

    footer = section.footer
    footer_paragraph = footer.paragraphs[0]
    footer_paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer_paragraph.paragraph_format.space_before = Pt(0)
    footer_run = footer_paragraph.add_run(
        "MINUTA EXPERIMENTAL — SUJEITA À REVISÃO HUMANA"
    )
    _set_run_font(footer_run, 9, italic=True)

    for index, line in enumerate(header_lines):
        item = document.add_paragraph(style="Minuta Identificação")
        _add_text(item, line, bold=True)
        if index == len(header_lines) - 1:
            item.paragraph_format.space_after = Pt(24)

    ementa = document.add_paragraph(style="Minuta Ementa")
    _add_text(ementa, f"Ementa: {structure['ementa']}", size=11)
    _add_body(
        document,
        structure["relatorio"],
        paragraphs=structure.get("relatorio_paragrafos"),
    )
    _add_body(
        document,
        structure["fundamentacao_geral"],
        paragraphs=structure.get("fundamentacao_geral_paragrafos"),
    )
    for topic in structure["topicos_irregularidades"]:
        heading = document.add_paragraph(style="Minuta Irregularidade")
        _add_text(heading, topic["titulo"], bold=True, italic=True)
        _add_body(
            document,
            topic["texto"],
            paragraphs=topic.get("paragrafos"),
        )

    conclusion = _conclusion(structure)
    if conclusion:
        _add_body(document, conclusion)
    _add_requests(document, structure["pedidos"])
    _add_body(
        document,
        _closing(structure["encerramento"]),
        style="Minuta Encerramento",
    )

    name = document.add_paragraph(style="Minuta Procuradora")
    name.paragraph_format.space_before = Pt(24)
    _add_text(name, profile["nome"], size=14, bold=True, italic=True)
    title = document.add_paragraph(style="Minuta Cargo")
    _add_text(title, profile["cargo"], size=11)

    output = BytesIO()
    document.save(output)
    return output.getvalue()


class MinutasDocxService:
    """Revalida a sessão e exporta em memória, sem nova operação de IA."""

    def __init__(self, library, *, source_summary=None, template_path=None):
        self.library = library
        if source_summary is None:
            from services.minutas_generation import MinutasGenerationService

            source_summary = MinutasGenerationService(library).source_summary
        self._source_summary = source_summary
        self.template_path = Path(template_path) if template_path else None

    def export(self, target_reference_id, analyzed, generated):
        from services.minutas_generation import (
            MinutasGenerationService,
            _canonical_process_key as canonical_process_key,
            _draft_header_lines,
            _target_with_header_metadata,
        )

        self.library._authorize()
        from services.minutas_specialties import scope, RPPS
        if scope(getattr(self.library, "specialty_id", None))["id"] != RPPS:
            raise MinutasDocxError("Exportação indisponível nesta especialidade.")
        target = self.library.get(target_reference_id)
        MinutasGenerationService._validate_target(target)
        if not isinstance(analyzed, dict) or not isinstance(generated, dict):
            raise MinutasDocxError("Gere novamente a minuta antes de exportar.")
        summary = self._source_summary(target_reference_id)
        current_version = summary.get("fontes_versao")
        if summary.get("issues"):
            raise MinutasDocxError(
                "Confira ou reprocesse os documentos instrutórios antes de exportar."
            )
        if any(
            item.get("fontes_versao") != current_version
            for item in (analyzed, generated)
        ):
            raise MinutasDocxError(
                "As fontes foram alteradas. Analise e gere novamente a minuta."
            )

        expected = {
            "processo_alvo_id": str(target["id"]),
            "processo_alvo_chave": canonical_process_key(
                target.get("processo_chave") or target.get("numero_processo")
            ),
            "procuradora_chave": str(target.get("procuradora_chave") or ""),
        }
        for name, value in expected.items():
            if str(analyzed.get(name) or "") != value or str(
                generated.get(name) or ""
            ) != value:
                raise MinutasDocxError(
                    "A minuta não pertence ao processo e à Procuradora selecionados."
                )

        profile = dict(_PROFILES.get(target.get("procuradora")) or {})
        if not profile:
            raise MinutasDocxError("Não há modelo Word autorizado para esta Procuradora.")
        if self.template_path:
            profile["template"] = self.template_path
        structure = MinutasGenerationService._validate_structure(
            generated.get("estrutura")
        )
        header_target = _target_with_header_metadata(
            target, generated.get("metadados_cabecalho")
        )
        structure, _normalization_warnings = (
            MinutasGenerationService._normalize_structure(structure, header_target)
        )
        structure = MinutasGenerationService._validate_structure(structure)
        return MinutaDocx(
            name=_filename(target),
            mime=DOCX_MIME,
            content=_build_docx(
                target,
                structure,
                profile,
                _draft_header_lines(header_target),
            ),
        )
