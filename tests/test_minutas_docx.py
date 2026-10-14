from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

from services.minutas_docx import (
    DOCX_MIME,
    ELVIRA_TEMPLATE,
    MinutasDocxError,
    MinutasDocxService,
)
from services.minutas_generation import RAG_RECOVERY_VERSION
from services.minutas_library import MinutasAccessDenied


VERSION = "fontes-atuais"


def _target(identifier="target-taperoa", process="TC 01653/26", key="0165326"):
    return {
        "id": identifier,
        "procuradora": "Elvira Samara",
        "procuradora_chave": "elvira samara",
        "numero_processo": process,
        "processo_chave": key,
        "exercicio": 2026,
        "subtipo": "Instituto/Autarquia",
    }


class Library:
    def __init__(self, target=None, *, authorized=True):
        self.target = target or _target()
        self.authorized = authorized

    def _authorize(self):
        if not self.authorized:
            raise MinutasAccessDenied("Acesso não autorizado a este módulo.")
        return "admin@test.local"

    def get(self, identifier):
        self._authorize()
        if str(identifier) != str(self.target["id"]):
            raise MinutasDocxError("Referência não encontrada.")
        return self.target


def _summary(_identifier):
    return {"fontes_versao": VERSION, "issues": []}


def _structure(*, long=False):
    body = (
        "A instrução demonstrou os fatos documentados e preservou a distinção "
        "entre defesa, Auditoria e avaliação ministerial."
    )
    if long:
        body = "\n\n".join(f"Parágrafo jurídico sintético {index}. {body}" for index in range(45))
    return {
        "identificacao_processual": "Processo TC 01653/26",
        "ementa": "PRESTAÇÃO DE CONTAS ANUAIS. ANÁLISE DOCUMENTAL.",
        "relatorio": "Relatório processual com acentuação e valor de R$ 1.234,56.",
        "fundamentacao_geral": body,
        "topicos_irregularidades": [
            {
                "titulo": "18.1 Receita intraorçamentária",
                "irregularidades_ids": ["18.1"],
                "avaliacoes_irregularidades": [
                    {
                        "irregularidade_id": "18.1",
                        "tratamento_ministerial_defesa": "MANTIDA_REJEICAO",
                        "fundamentos_divergencia": [],
                        "estado_saneamento": "ALEGADO_NAO_COMPROVADO",
                        "fontes_saneamento": [],
                    }
                ],
                "texto": (
                    "A fundamentação examina o art. 37 da Constituição e os "
                    "documentos efetivamente indicados.\n\n"
                    "“Trecho documental sintético para conferência.”"
                ),
            }
        ],
        "conclusao_pedidos": (
            "Ante o exposto, propõem-se os encaminhamentos.\n"
            "1. Julgue-se o feito conforme a fundamentação."
        ),
        "pedidos": [
            "Julgue-se o feito conforme a fundamentação.",
            (
                "Expeçam-se recomendações:\n"
                "a) mantenha os controles previdenciários;\n"
                "b) documente as providências adotadas."
            ),
        ],
        "encerramento": "É o Parecer.",
    }


def _payload(target=None, *, sufficient=True, version=VERSION):
    target = target or _target()
    identity = {
        "processo_alvo_id": str(target["id"]),
        "processo_alvo_chave": "0165326",
        "procuradora_chave": target["procuradora_chave"],
    }
    analyzed = {
        **identity,
        "suficiente_para_geracao": sufficient,
        "fontes_versao": version,
        "rag_recovery_version": RAG_RECOVERY_VERSION,
    }
    generated = {
        **identity,
        "fontes_versao": version,
        "estrutura": _structure(),
        "advertencias_qualidade": ["Advertência não bloqueante para revisão."],
    }
    return analyzed, generated


def _export(*, target=None, structure=None):
    target = target or _target()
    analyzed, generated = _payload(target)
    if structure is not None:
        generated["estrutura"] = structure
    return MinutasDocxService(
        Library(target), source_summary=_summary
    ).export(target["id"], analyzed, generated)


def _image_blobs(document):
    return [
        relationship.target_part.blob
        for section in document.sections
        for relationship in section.header.part.rels.values()
        if relationship.reltype.endswith("/image")
    ]


def test_docx_is_editable_a4_and_matches_institutional_geometry():
    exported = _export()
    document = Document(BytesIO(exported.content))
    section = document.sections[0]

    assert exported.name == "MINUTA_PARECER_TC_01653_26.docx"
    assert exported.mime == DOCX_MIME
    assert exported.content[:2] == b"PK"
    assert section.page_width.cm == pytest.approx(21.001, abs=0.02)
    assert section.page_height.cm == pytest.approx(29.7, abs=0.02)
    assert section.top_margin.cm == pytest.approx(1.776, abs=0.02)
    assert section.bottom_margin.cm == pytest.approx(2.0, abs=0.02)
    assert section.left_margin.cm == pytest.approx(2.75, abs=0.02)
    assert section.right_margin.cm == pytest.approx(1.75, abs=0.02)
    assert document.styles["Normal"].font.name == "Arial"
    assert document.styles["Normal"].font.size.pt == pytest.approx(12)


def test_docx_uses_original_logo_and_contains_only_target_content():
    exported = _export()
    output = Document(BytesIO(exported.content))
    reference = Document(ELVIRA_TEMPLATE)
    output_images = _image_blobs(output)
    reference_images = _image_blobs(reference)
    body_text = "\n".join(paragraph.text for paragraph in output.paragraphs)
    footer_text = "\n".join(
        paragraph.text
        for section in output.sections
        for paragraph in section.footer.paragraphs
    )

    assert len(output_images) == 1
    assert sha256(output_images[0]).digest() == sha256(reference_images[0]).digest()
    assert "Processo TC Nº 01653/26" in body_text
    assert "ELVIRA SAMARA PEREIRA DE OLIVEIRA" in body_text
    assert "Procuradora-Geral do Ministério Público de Contas" in body_text
    assert "MINUTA EXPERIMENTAL — SUJEITA À REVISÃO HUMANA" in footer_text
    forbidden = ("bananeir", "02209/26", " ycn", "joão pessoa,")
    assert not any(item in body_text.casefold() for item in forbidden)
    with ZipFile(BytesIO(exported.content)) as package:
        assert not any(name.endswith("vbaProject.bin") for name in package.namelist())
        xml_text = "\n".join(
            package.read(name).decode("utf-8", errors="ignore")
            for name in package.namelist()
            if name.endswith(".xml")
        ).casefold()
    assert "bananeir" not in xml_text
    assert ">ycn<" not in xml_text
    assert "w:fldsimple" not in xml_text
    assert "logomarca do ministério público de contas da paraíba" in xml_text


def test_docx_preserves_structure_formatting_numbering_and_alineas():
    exported = _export()
    document = Document(BytesIO(exported.content))
    paragraphs = [paragraph for paragraph in document.paragraphs if paragraph.text]
    all_text = "\n".join(paragraph.text for paragraph in paragraphs)

    ementa = next(paragraph for paragraph in paragraphs if paragraph.text.startswith("Ementa:"))
    heading = next(paragraph for paragraph in paragraphs if paragraph.text.startswith("18.1"))
    transcription = next(
        paragraph for paragraph in paragraphs if paragraph.text.startswith("“Trecho")
    )
    request = next(
        paragraph
        for paragraph in paragraphs
        if paragraph.text == "Julgue-se o feito conforme a fundamentação."
    )
    alineas = [
        paragraph
        for paragraph in paragraphs
        if paragraph.text.startswith(("mantenha", "documente"))
    ]

    assert ementa.style.name == "Minuta Ementa"
    assert ementa.style.paragraph_format.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    assert ementa.style.paragraph_format.left_indent.cm == pytest.approx(
        6.752, abs=0.02
    )
    assert heading.style.name == "Minuta Irregularidade"
    assert heading.runs[0].bold is True and heading.runs[0].italic is True
    assert transcription.style.name == "Minuta Transcrição"
    assert request._p.pPr.numPr is not None
    assert len(alineas) == 2
    assert all(paragraph._p.pPr.numPr is not None for paragraph in alineas)
    assert all_text.count("Julgue-se o feito conforme a fundamentação.") == 1
    assert "R$ 1.234,56" in all_text
    assert "art. 37" in all_text
    with ZipFile(BytesIO(exported.content)) as package:
        numbering = package.read("word/numbering.xml").decode("utf-8")
    assert 'w:val="decimal"' in numbering
    assert 'w:val="lowerLetter"' in numbering
    assert 'w:val="%1)"' in numbering


@pytest.mark.parametrize("long", (False, True))
def test_docx_supports_short_and_long_drafts(long):
    exported = _export(structure=_structure(long=long))
    document = Document(BytesIO(exported.content))

    assert document.paragraphs
    assert document.paragraphs[-1].text.startswith("Procuradora-Geral")
    if long:
        assert len(document.paragraphs) > 55


def test_export_revalidates_authorization_target_sources_and_blocking_state():
    target = _target()
    analyzed, generated = _payload(target)

    with pytest.raises(MinutasAccessDenied):
        MinutasDocxService(
            Library(target, authorized=False), source_summary=_summary
        ).export(target["id"], analyzed, generated)

    wrong = dict(generated, processo_alvo_id="outro-processo")
    with pytest.raises(MinutasDocxError, match="não pertence"):
        MinutasDocxService(Library(target), source_summary=_summary).export(
            target["id"], analyzed, wrong
        )

    stale = dict(generated, fontes_versao="fontes-antigas")
    with pytest.raises(MinutasDocxError, match="fontes foram alteradas"):
        MinutasDocxService(Library(target), source_summary=_summary).export(
            target["id"], analyzed, stale
        )

    blocked = dict(analyzed, suficiente_para_geracao=False)
    with pytest.raises(MinutasDocxError, match="bloqueada"):
        MinutasDocxService(Library(target), source_summary=_summary).export(
            target["id"], blocked, generated
        )


def test_export_requires_the_visual_template(tmp_path):
    target = _target()
    analyzed, generated = _payload(target)

    with pytest.raises(MinutasDocxError, match="modelo visual institucional"):
        MinutasDocxService(
            Library(target),
            source_summary=_summary,
            template_path=tmp_path / "ausente.docx",
        ).export(target["id"], analyzed, generated)
