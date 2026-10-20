"""O DOCX gerado usa o modelo vigente do Procurador e da especialidade."""

import base64
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import pytest
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Cm, Emu, Pt

from services.minutas_docx import MinutasDocxError, MinutasDocxService
from services.minutas_generation import MinutasGenerationError, MinutasGenerationService
from services.minutas_library import MinutasLibraryService
from services.minutas_modelo_docx import (
    MENSAGEM_MODELO_AUSENTE,
    MENSAGEM_MODELO_INVALIDO,
    impressao_modelo,
    validar_pacote,
)
from services.minutas_modelos import MinutasModeloVisualService
from services.minutas_orientacao import aplicar_bloco
from services.minutas_procuradores import exigir_procurador
from services.minutas_specialties import CAMARAS, PREFEITURAS, SHARED_INSTRUCTION_MOTOR
from tests.test_minutas_instrucao import _structure
from tests.test_minutas_modelos import _install
from tests.test_minutas_specialties import GestorAcervo, Storage, TEST_IDENTITY, build_store


PNG_A = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_B = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)
VENENO = "Processo secreto do modelo visual que não pode reaparecer na minuta gerada."


def _fonte(run, nome, tamanho):
    run.font.name = nome
    run.font.size = Pt(tamanho)
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fonts.set(qn(attr), nome)


def _modelo(
    nome_arquivo,
    *,
    fonte,
    esquerda,
    superior,
    logo,
    largura,
    ementa=WD_ALIGN_PARAGRAPH.LEFT,
    assinatura=WD_ALIGN_PARAGRAPH.CENTER,
):
    document = Document()
    normal = document.styles["Normal"]
    normal.font.name = fonte
    normal.font.size = Pt(12)
    section = document.sections[0]
    section.page_width = Cm(21)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(superior)
    section.bottom_margin = Cm(2)
    section.left_margin = Cm(esquerda)
    section.right_margin = Cm(2)
    section.header_distance = Cm(0.6)
    section.footer_distance = Cm(0.6)
    header = section.header.paragraphs[0]
    header.alignment = WD_ALIGN_PARAGRAPH.CENTER
    image = header.add_run()
    image.add_picture(BytesIO(logo), width=Emu(largura))
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run(f"Rodapé exclusivo {fonte}")
    body = document.add_paragraph(VENENO)
    body.paragraph_format.first_line_indent = Cm(1.2)
    _fonte(body.runs[0], fonte, 12)
    ementa_item = document.add_paragraph("Ementa de exemplo que não pode sair.")
    ementa_item.alignment = ementa
    ementa_item.paragraph_format.left_indent = Cm(6.5)
    _fonte(ementa_item.runs[0], fonte, 10)
    title = document.add_paragraph("Irregularidade de exemplo do modelo")
    title.runs[0].bold = True
    title.runs[0].italic = True
    _fonte(title.runs[0], fonte, 12)
    name = document.add_paragraph("NOME DE EXEMPLO DO MODELO")
    name.alignment = assinatura
    name.runs[0].bold = True
    _fonte(name.runs[0], fonte, 14)
    role = document.add_paragraph("Cargo de exemplo do modelo")
    role.alignment = assinatura
    _fonte(role.runs[0], fonte, 11)
    output = BytesIO()
    document.save(output)
    return nome_arquivo, output.getvalue()


def _gabinete(store, storage, specialty, sigla):
    from database.minutas_referencias import MinutasReferenciasStore

    repo = object.__new__(MinutasReferenciasStore)
    repo.store, repo.specialty_id, repo.include_acervo = store, specialty, True
    return MinutasLibraryService(
        repo,
        storage,
        TEST_IDENTITY,
        {"MINUTAS_PILOTO_ENABLED": True, "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]]},
        procurador=exigir_procurador(sigla),
        principal=GestorAcervo(),
    )


def _habilitar(store, specialty):
    with store.connection() as connection:
        connection.execute(
            "UPDATE minutas_especialidades SET motor=?, estado=? WHERE id=?",
            (SHARED_INSTRUCTION_MOTOR, "Em testes", specialty),
        )


def _exportar(library, processo="02659/25"):
    key = processo.replace("/", "")
    analyzed = {"fontes_versao": "v", "processo_alvo_id": f"instrucao-{key}", "analise": {}}
    generated = {
        "fontes_versao": "v",
        "processo_alvo_id": f"instrucao-{key}",
        "estrutura": _structure(),
        "metadados_cabecalho": {},
    }
    return MinutasDocxService(library).export_instruction(
        analyzed, generated, processo=processo, exercicio=2025, origem="Prefeitura Municipal de Exemplo",
    )


def _pacote(content, name):
    with ZipFile(BytesIO(content)) as package:
        return package.read(name).decode("utf-8")


def _logotipos(content):
    document = Document(BytesIO(content))
    return [
        relationship.target_part.blob
        for section in document.sections
        for relationship in section.header.part.rels.values()
        if relationship.reltype.endswith("/image")
    ]


@pytest.fixture
def ambiente(tmp_path):
    store = build_store(tmp_path / "minutas.db")
    _install(store)
    storage = Storage()
    _habilitar(store, PREFEITURAS)
    _habilitar(store, CAMARAS)
    return store, storage


def test_pacote_com_macro_ou_caminho_externo_e_recusado():
    buffer = BytesIO()
    with ZipFile(buffer, "w") as package:
        package.writestr("[Content_Types].xml", "<Types></Types>")
        package.writestr("word/document.xml", "<w:document/>")
        package.writestr("word/vbaProject.bin", b"macro")
    with pytest.raises(MinutasDocxError, match="inválido ou incompatível"):
        validar_pacote(buffer.getvalue())
    slipped = BytesIO()
    with ZipFile(slipped, "w") as package:
        package.writestr("[Content_Types].xml", "<Types></Types>")
        package.writestr("word/../fora.txt", b"x")
    with pytest.raises(MinutasDocxError, match="inválido ou incompatível"):
        validar_pacote(slipped.getvalue())


def test_bradson_prefeitura_usa_o_modelo_cadastrado_e_nao_o_de_elvira(ambiente):
    store, storage = ambiente
    bradson = _gabinete(store, storage, PREFEITURAS, "BLTC")
    elvira = _gabinete(store, storage, PREFEITURAS, "ESPO")
    modelo_bradson = _modelo(
        "bradson-prefeitura.docx", fonte="Times New Roman", esquerda=3.2, superior=2.4,
        logo=PNG_B, largura=900000, ementa=WD_ALIGN_PARAGRAPH.RIGHT,
        assinatura=WD_ALIGN_PARAGRAPH.CENTER,
    )
    modelo_elvira = _modelo(
        "elvira-prefeitura.docx", fonte="Arial", esquerda=1.8, superior=5.2,
        logo=PNG_A, largura=1400000, ementa=WD_ALIGN_PARAGRAPH.LEFT,
        assinatura=WD_ALIGN_PARAGRAPH.LEFT,
    )
    MinutasModeloVisualService(bradson).save(PREFEITURAS, modelo_bradson)
    MinutasModeloVisualService(elvira).save(PREFEITURAS, modelo_elvira)

    gerado = _exportar(bradson, "02659/25")
    documento = Document(BytesIO(gerado.content))
    texto = "\n".join(item.text for item in documento.paragraphs)
    xml = _pacote(gerado.content, "word/document.xml")
    estilos = _pacote(gerado.content, "word/styles.xml")
    ementa = next(item for item in documento.paragraphs if item.text.startswith("Ementa:"))
    assinatura = next(item for item in documento.paragraphs if "BRADSON" in item.text)

    assert gerado.modelo["procuradora_chave"] == "bltc"
    assert gerado.modelo["especialidade_id"] == PREFEITURAS
    assert gerado.modelo["sha256"] == sha256(modelo_bradson[1]).hexdigest()
    assert documento.sections[0].left_margin.cm == pytest.approx(3.2, abs=0.05)
    assert documento.sections[0].top_margin.cm == pytest.approx(2.4, abs=0.05)
    assert ementa.alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert assinatura.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert "Times New Roman" in estilos
    assert "Times New Roman" in xml
    assert "Minuta Procuradora" not in estilos
    assert "Minuta Procuradora" not in xml
    assert _logotipos(gerado.content) == [PNG_B]
    assert "BRADSON TIBÉRIO LUNA CAMELO" in texto
    assert "ELVIRA SAMARA" not in texto
    assert VENENO not in texto
    assert "NOME DE EXEMPLO" not in texto
    assert "02659/25" in texto
    assert "Rodapé exclusivo Times New Roman" in "\n".join(
        item.text for item in documento.sections[0].footer.paragraphs
    )
    assert gerado.content[:2] == b"PK"
    Document(BytesIO(gerado.content))

    outra = _exportar(elvira, "02659/25")
    outra_doc = Document(BytesIO(outra.content))
    assert outra.modelo["procuradora_chave"] == "elvira samara"
    assert outra_doc.sections[0].left_margin.cm == pytest.approx(1.8, abs=0.05)
    assert _logotipos(outra.content) == [PNG_A]
    assert "Arial" in _pacote(outra.content, "word/styles.xml")
    assert "BRADSON" not in "\n".join(item.text for item in outra_doc.paragraphs)
    assert "ELVIRA SAMARA PEREIRA DE OLIVEIRA" in "\n".join(
        item.text for item in outra_doc.paragraphs
    )


def test_especialidade_e_versao_nao_contaminam_outro_modelo(ambiente):
    store, storage = ambiente
    prefeitura = _gabinete(store, storage, PREFEITURAS, "BLTC")
    camara = _gabinete(store, storage, CAMARAS, "BLTC")
    primeiro = _modelo(
        "prefeitura-v1.docx", fonte="Times New Roman", esquerda=3.2, superior=2.2,
        logo=PNG_B, largura=900000,
    )
    segundo = _modelo(
        "prefeitura-v2.docx", fonte="Times New Roman", esquerda=3.2, superior=4.4,
        logo=PNG_B, largura=900000,
    )
    camaras = _modelo(
        "camara.docx", fonte="Calibri", esquerda=1.5, superior=2.2,
        logo=PNG_A, largura=700000, assinatura=WD_ALIGN_PARAGRAPH.LEFT,
    )
    servico = MinutasModeloVisualService(prefeitura)
    assert impressao_modelo(prefeitura) == "ausente"
    servico.save(PREFEITURAS, primeiro)
    antes = impressao_modelo(prefeitura)
    gerado = _exportar(prefeitura)
    assert Document(BytesIO(gerado.content)).sections[0].top_margin.cm == pytest.approx(2.2, abs=0.05)
    servico.save(PREFEITURAS, segundo)
    depois = impressao_modelo(prefeitura)
    assert antes != depois
    assert Document(BytesIO(_exportar(prefeitura).content)).sections[0].top_margin.cm == pytest.approx(4.4, abs=0.05)
    servico.save(PREFEITURAS, primeiro)
    assert Document(BytesIO(_exportar(prefeitura).content)).sections[0].top_margin.cm == pytest.approx(2.2, abs=0.05)
    MinutasModeloVisualService(camara).save(CAMARAS, camaras)
    camara_doc = Document(BytesIO(_exportar(camara, "02108/26").content))
    assert camara_doc.sections[0].left_margin.cm == pytest.approx(1.5, abs=0.05)
    assert _logotipos(MinutasDocxService(camara).export_instruction(
        {"fontes_versao": "v", "processo_alvo_id": "instrucao-0210826", "analise": {}},
        {
            "fontes_versao": "v",
            "processo_alvo_id": "instrucao-0210826",
            "estrutura": _structure(),
            "metadados_cabecalho": {},
        },
        processo="02108/26",
        exercicio=2025,
        origem="Câmara Municipal de Exemplo",
    ).content) == [PNG_A]
    assert _logotipos(_exportar(prefeitura).content) == [PNG_B]


def test_ausencia_ou_modelo_invalido_nao_cai_no_padrao_de_elvira(ambiente):
    store, storage = ambiente
    bradson = _gabinete(store, storage, PREFEITURAS, "BLTC")
    chamadas = []
    servico = MinutasGenerationService(bradson, generate=lambda contexto: chamadas.append(contexto))
    with pytest.raises(MinutasDocxError, match="não há um modelo visual válido"):
        _exportar(bradson)
    with pytest.raises(MinutasGenerationError, match="não há um modelo visual válido"):
        servico._generate({})
    assert chamadas == []
    assert MENSAGEM_MODELO_AUSENTE.startswith("Não foi possível gerar o DOCX")

    salvo = MinutasModeloVisualService(bradson).save(
        PREFEITURAS,
        _modelo(
            "valido.docx", fonte="Times New Roman", esquerda=3, superior=2,
            logo=PNG_B, largura=800000,
        ),
    )
    storage.objects[salvo["path"]] = b"PK\x03\x04isto-nao-e-docx"
    with pytest.raises(MinutasDocxError, match="inválido ou incompatível"):
        _exportar(bradson)
    with pytest.raises(MinutasGenerationError, match="inválido ou incompatível"):
        servico._generate({})
    assert chamadas == []
    assert MENSAGEM_MODELO_INVALIDO.startswith("Não foi possível gerar o DOCX")


def test_modelo_valido_nao_dispensa_a_orientacao_do_assessor(ambiente):
    store, storage = ambiente
    bradson = _gabinete(store, storage, PREFEITURAS, "BLTC")
    MinutasModeloVisualService(bradson).save(
        PREFEITURAS,
        _modelo(
            "valido.docx", fonte="Times New Roman", esquerda=3, superior=2,
            logo=PNG_B, largura=800000,
        ),
    )
    chamadas = []
    servico = MinutasGenerationService(
        bradson, generate=lambda contexto: chamadas.append(contexto) or {"ok": True},
    )
    with pytest.raises(MinutasGenerationError, match="Informe a orientação"):
        servico._generate({})
    contexto = {}
    aplicar_bloco(
        contexto,
        "Sustentar a linha argumentativa indicada pelo assessor nesta minuta experimental.",
    )
    assert servico._generate(contexto)["ok"] is True
    assert chamadas[0]["orientacao_juridica_usuario"]["texto"].startswith("Sustentar")
