"""Controlled institutional DOCX template for final substitution memoranda."""
from io import BytesIO
from pathlib import Path
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt
from services.memorandos import participle, period_text, role_article

ROOT = Path(__file__).resolve().parents[1]


def _paragraph(doc, text="", *, alignment=WD_ALIGN_PARAGRAPH.LEFT, before=0, after=0, first=0, bold=False):
    paragraph = doc.add_paragraph()
    paragraph.alignment = alignment
    paragraph.paragraph_format.space_before = Pt(before)
    paragraph.paragraph_format.space_after = Pt(after)
    paragraph.paragraph_format.first_line_indent = Cm(first)
    paragraph.paragraph_format.line_spacing = 1.15
    run = paragraph.add_run(text); run.font.name = "Times New Roman"; run.font.size = Pt(12); run.bold = bold
    return paragraph


def _subject(record):
    return "Substituição de servidor ocupante de " + record["natureza_funcao"].lower()


def _motive(record):
    return record["motivo_texto"].strip() if record["motivo"] == "Outro" else record["motivo"].lower()


def _first_body(record, step):
    left, right = step["substituido"], step["substituto"]
    return (
        f"Ao cumprimentá-lo, e considerando que {role_article(left['genero'])} "
        f"{left['nome']}, matrícula nº {left.get('matricula','')}, ocupante de "
        f"{record['natureza_funcao'].lower()} de {left.get('cargo','')}, "
        f"{record['gabinete_snapshot']}, encontra-se afastad{'a' if left['genero']=='Feminino' else 'o'} "
        f"de suas atividades laborais {period_text(record['data_inicio'], record['data_fim'])}, "
        f"em decorrência de {_motive(record)}, indico {role_article(right['genero'])} "
        f"{right['nome']}, matrícula nº {right.get('matricula','')}, {right.get('cargo','')}, "
        f"{participle(right['genero'])} em {right.get('lotacao','')}, para substituir "
        f"{role_article(left['genero'])} antes mencionad{'a' if left['genero']=='Feminino' else 'o'} durante o respectivo período."
    )


def _cascade_body(step):
    left, right = step["substituido"], step["substituto"]
    return (
        f"Em decorrência da substituição acima, indico {role_article(right['genero'])} "
        f"{right['nome']}, matrícula nº {right.get('matricula','')}, {right.get('cargo','')}, "
        f"{participle(right['genero'])} em {right.get('lotacao','')}, para substituir, "
        f"no mesmo período, {role_article(left['genero'])} {left['nome']}, matrícula nº "
        f"{left.get('matricula','')}, {left.get('cargo','')}."
    )


def generate(record):
    doc = Document(); section = doc.sections[0]
    section.top_margin, section.bottom_margin = Cm(2.3), Cm(2.3)
    section.left_margin, section.right_margin = Cm(3.0), Cm(2.2)
    logo = ROOT / "assets" / "mpcpb_header_horizontal.png"
    if logo.is_file():
        header = section.header.paragraphs[0]; header.alignment = WD_ALIGN_PARAGRAPH.CENTER
        header.add_run().add_picture(str(logo), width=Cm(12.5))
    _paragraph(doc, "MEMORANDO", alignment=WD_ALIGN_PARAGRAPH.CENTER, before=26, after=24, bold=True)
    _paragraph(doc, "Ao Excelentíssimo Senhor Presidente do Tribunal de Contas do Estado da Paraíba", after=16)
    subject = _paragraph(doc, "", after=18); subject.add_run("Assunto: ").bold = True; subject.add_run(_subject(record)).bold = True
    _paragraph(doc, "Senhor Presidente,", after=14)
    for index, step in enumerate(record["etapas"]):
        _paragraph(doc, _first_body(record, step) if index == 0 else _cascade_body(step), alignment=WD_ALIGN_PARAGRAPH.JUSTIFY, after=12, first=1.25)
    _paragraph(doc, "Com os meus melhores cumprimentos,", after=28)
    _paragraph(doc, record["signatario_nome"].upper(), alignment=WD_ALIGN_PARAGRAPH.CENTER, bold=True)
    _paragraph(doc, record["signatario_cargo"] + " do Ministério Público de Contas da Paraíba", alignment=WD_ALIGN_PARAGRAPH.CENTER)
    out = BytesIO(); doc.save(out); return out.getvalue()


def pdf(record):
    from document_generator.pdf import convert
    content, _ = convert(generate(record)); return content
