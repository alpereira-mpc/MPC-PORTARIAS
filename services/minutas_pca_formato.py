"""Formatação complementar do DOCX de PCA de Prefeituras.

O modelo visual cadastrado, ou o fallback já homologado, continua dono da página,
do cabeçalho, do rodapé, das imagens e das tabelas. Este módulo ajusta os blocos
variáveis da minuta. Com modelo visual, o perfil do Procurador ainda define
fonte, tamanho, recuo e ênfase desses blocos. Negrito e itálico de parágrafo
inteiro só entram quando a referência do Procurador grifa o bloco todo.
Destaque de palavra ou de rótulo não se estende ao restante. Se a especialidade
não for PCA de Prefeituras, se não houver perfil ou se a aplicação falhar, o
DOCX gerado permanece como está.

As medidas de página já homologadas não são substituídas pelas margens dos
exemplos. Elvira e Bradson conservam a geometria do modelo ou do fallback
calibrado e só recebem a ênfase prevista no perfil. A data de Elvira, no
modelo visual, segue o parágrafo de data e não o recuo de uma citação.
Faixas e títulos sombreados permanecem. O sombreamento não segue para o corpo.
"""

from copy import deepcopy
import re

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


_ALINHAMENTOS = {
    "both": WD_ALIGN_PARAGRAPH.JUSTIFY,
    "left": WD_ALIGN_PARAGRAPH.LEFT,
    "center": WD_ALIGN_PARAGRAPH.CENTER,
    "right": WD_ALIGN_PARAGRAPH.RIGHT,
}
_ESTILOS_FIXOS = {
    "Minuta Identificação",
    "Minuta Procuradora",
    "Minuta Cargo",
    "Minuta Pedido",
    "Minuta Alínea",
    "Minuta Revisão Título",
    "Minuta Revisão Item",
}
_CITACAO = re.compile(
    r"""^(?:["“‘'«]|art\.?\s+\d|artigo\s+\d|§\s*\d|parágrafo único\b)""",
    re.IGNORECASE,
)
_ASPAS = re.compile(r"(“[^”]{12,}”|\"[^\"\n]{12,}\")")
_DATA = re.compile(
    r"^.+,\s*\d{1,2}\s+de\s+(?:janeiro|fevereiro|março|marco|abril|maio|junho|"
    r"julho|agosto|setembro|outubro|novembro|dezembro|"
    r"jan\.|fev\.|mar\.|abr\.|mai\.|jun\.|jul\.|ago\.|set\.|out\.|nov\.|dez\.)"
    r"\s+de\s+\d{4}\.?$",
    re.IGNORECASE,
)
_ROTULO_EMENTA = re.compile(r"^(Ementa:\s*)", re.IGNORECASE)
_IDENTIFICACAO = re.compile(
    r"^(?:processo|parecer|origem|responsável|responsavel|exercício|exercicio|"
    r"interessad[oa]|relator)\b",
    re.IGNORECASE,
)

# Geometria de conteúdo. Fonte e margens homologadas ficam de fora de propósito.
PERFIS = {
    "ESPO": {
        "preservar_geometria": True,
        "titulo": {"bold": True, "italic": True},
        "citacao": {"italic": True},
    },
    "BLTC": {
        "preservar_geometria": True,
        "titulo": {"bold": True, "italic": True},
    },
    "LAF": {
        "preservar_geometria": False,
        "corpo": {
            "size": 13, "first_cm": 2.54, "left_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "ementa": {
            "size": 11, "left_cm": 6.35, "first_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 12, "bold": False, "italic": True,
            "font": "Calibri",
        },
        "titulo": {
            "size": 13, "left_cm": 0, "first_cm": 0, "align": "both",
            "before_pt": 12, "after_pt": 6, "bold": True, "italic": False,
            "font": "Calibri",
        },
        "citacao": {
            "size": 11, "left_cm": 2.54, "first_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 6, "bold": False, "italic": True,
            "font": "Calibri",
        },
        "conclusao": {
            "size": 13, "first_cm": 2.54, "left_cm": 0, "align": "both",
            "before_pt": 6, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "fecho": {
            "size": 13, "first_cm": 2.54, "left_cm": 0, "align": "both",
            "before_pt": 12, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "data": {
            "size": 13, "first_cm": 2.54, "left_cm": 0, "align": "both",
            "before_pt": 12, "after_pt": 0, "bold": False, "italic": False,
            "font": "Calibri",
        },
    },
    "MASN": {
        "preservar_geometria": False,
        "corpo": {
            "size": 12, "first_cm": 1.25, "left_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 12, "bold": False, "italic": False,
            "font": "Arial",
        },
        "ementa": {
            "size": 11, "left_cm": 6.75, "first_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 12, "bold": True, "italic": False,
            "font": "Arial",
        },
        "titulo": {
            "size": 12, "left_cm": 0, "first_cm": 0, "align": "both",
            "before_pt": 12, "after_pt": 6, "bold": True, "italic": False,
            "font": "Arial",
        },
        "citacao": {
            "size": 12, "first_cm": 1.25, "left_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 12, "bold": False, "italic": False,
            "font": "Arial",
        },
        "conclusao": {
            "size": 12, "first_cm": 1.25, "left_cm": 0, "align": "both",
            "before_pt": 6, "after_pt": 6, "bold": False, "italic": False,
            "font": "Arial",
        },
        "fecho": {
            "size": 12, "left_cm": 0, "first_cm": 0, "align": "both",
            "before_pt": 6, "after_pt": 6, "bold": False, "italic": False,
            "font": "Arial",
        },
        "data": {
            "size": 12, "left_cm": 0, "first_cm": 0, "align": "right",
            "before_pt": 12, "after_pt": 6, "bold": False, "italic": False,
            "font": "Arial",
        },
    },
    "IBMF": {
        "preservar_geometria": False,
        "corpo": {
            "size": 13, "first_cm": 2.5, "left_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "ementa": {
            "size": 11.5, "left_cm": 5.0, "first_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "titulo": {
            "size": 13, "first_cm": 2.5, "left_cm": 0, "align": "both",
            "before_pt": 6, "after_pt": 6, "bold": True, "italic": False,
            "font": "Calibri",
        },
        "citacao": {
            "size": 11, "left_cm": 2.54, "first_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "conclusao": {
            "size": 13, "first_cm": 2.5, "left_cm": 0, "align": "both",
            "before_pt": 6, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "fecho": {
            "size": 13, "first_cm": 2.5, "left_cm": 0, "align": "both",
            "before_pt": 6, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "data": {
            "size": 13, "left_cm": 0, "first_cm": 0, "align": "right",
            "before_pt": 12, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
    },
    "SBBQ": {
        "preservar_geometria": False,
        "corpo": {
            "size": 12, "first_cm": 1.5, "left_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 6, "bold": False, "italic": False,
            "font": "Arial",
        },
        "ementa": {
            "size": 12, "left_cm": 0, "first_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 6, "bold": True, "italic": False,
            "font": "Arial Narrow",
            "rotulo": {"bold": False, "italic": False},
        },
        "titulo": {
            "size": 14, "left_cm": 0, "first_cm": 1.5, "align": "both",
            "before_pt": 10, "after_pt": 4, "bold": True, "italic": False,
            "font": "Arial Narrow",
        },
        "citacao": {
            "size": 12, "first_cm": 1.5, "left_cm": 0, "align": "both",
            "before_pt": 0, "after_pt": 6, "bold": False, "italic": False,
            "font": "Arial",
        },
        "conclusao": {
            "size": 12, "first_cm": 1.5, "left_cm": 0, "align": "both",
            "before_pt": 6, "after_pt": 6, "bold": False, "italic": False,
            "font": "Arial",
        },
        "fecho": {
            "size": 12, "first_cm": 1.5, "left_cm": 0, "align": "both",
            "before_pt": 12, "after_pt": 6, "bold": False, "italic": False,
            "font": "Arial",
        },
        "data": {
            "size": 12, "left_cm": 0, "first_cm": 0, "align": "center",
            "before_pt": 12, "after_pt": 0, "bold": False, "italic": False,
            "font": "Arial",
        },
    },
    "MTTF": {
        "preservar_geometria": False,
        "corpo": {
            "size": 12, "first_cm": 1.27, "left_cm": 0, "align": "both",
            "before_pt": 2.9, "after_pt": 2.9, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "ementa": {
            "size": 10, "left_cm": 8.0, "right_cm": 1.25, "first_cm": 0,
            "align": "both", "before_pt": 2.9, "after_pt": 2.9,
            "bold": False, "italic": False, "font": "Calibri",
            "rotulo": {"bold": False, "italic": True},
        },
        "titulo": {
            "size": 12, "left_cm": 0, "first_cm": 0, "align": "both",
            "before_pt": 10, "after_pt": 6, "bold": True, "italic": False,
            "font": "Calibri",
        },
        "citacao": {
            "size": 11, "left_cm": 4.0, "first_cm": 0, "align": "both",
            "before_pt": 2.9, "after_pt": 2.9, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "conclusao": {
            "size": 12, "first_cm": 1.27, "left_cm": 0, "align": "both",
            "before_pt": 2.9, "after_pt": 2.9, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "fecho": {
            "size": 12, "first_cm": 1.27, "left_cm": 0, "align": "both",
            "before_pt": 10, "after_pt": 6, "bold": False, "italic": False,
            "font": "Calibri",
        },
        "data": {
            "size": 12, "left_cm": 0, "first_cm": 0, "align": "right",
            "before_pt": 2.9, "after_pt": 2.9, "bold": False, "italic": False,
            "font": "Calibri",
        },
    },
}


def eh_pca_prefeituras(library):
    """True só para a especialidade técnica de PCA de Prefeituras."""
    from services.minutas_procuradores import id_catalogo_especialidade, legado_espo
    from services.minutas_specialties import PREFEITURAS

    procurador = getattr(library, "procurador", None) or legado_espo()
    try:
        tecnico = id_catalogo_especialidade(
            getattr(library, "specialty_id", None), procurador,
        )
    except Exception:
        return False
    return tecnico == PREFEITURAS


def complementar_minuta_pca(
    content, *, sigla, structure, origem_modelo, data_assinatura="", identificacao=None,
):
    """Devolve o DOCX com o perfil do Procurador, ou o próprio conteúdo se não couber."""
    perfil = PERFIS.get(str(sigla or ""))
    if perfil is None or not isinstance(structure, dict) or not isinstance(content, (bytes, bytearray)):
        return content
    try:
        return _complementar(
            bytes(content),
            perfil,
            structure,
            bool(origem_modelo),
            str(data_assinatura or ""),
            identificacao if isinstance(identificacao, dict) else None,
        )
    except Exception:
        return content


def _complementar(content, perfil, structure, origem_modelo, data_assinatura, identificacao):
    from io import BytesIO

    from docx import Document

    document = Document(BytesIO(content))
    if identificacao:
        _sanear_identificacao(document, identificacao)
    mapa = _mapa(structure)
    substituir = bool(origem_modelo) and not perfil.get("preservar_geometria")
    for paragraph in list(document.paragraphs):
        if _tem_desenho(paragraph):
            continue
        papel = _papel(paragraph, mapa)
        if (
            _tem_numeracao(paragraph)
            and not _na_tabela(paragraph)
            and not perfil.get("preservar_geometria")
        ):
            _aplicar(
                paragraph,
                _spec(perfil, papel or "corpo"),
                origem_modelo=origem_modelo,
                substituir=substituir,
                recuos=False,
                manter_sombra=(papel == "titulo"),
            )
            if papel in {None, "corpo", "conclusao", "fecho"}:
                _italicar_aspas(paragraph)
            continue
        if papel is None:
            continue
        spec = _spec(perfil, papel)
        estrutural = _na_tabela(paragraph)
        if perfil.get("preservar_geometria") or estrutural:
            _enfase(paragraph, spec)
            if papel == "ementa":
                _diferenciar_rotulo(paragraph, spec)
            if papel in {"corpo", "conclusao", "fecho"}:
                _italicar_aspas(paragraph)
            continue
        _aplicar(
            paragraph,
            spec,
            origem_modelo=origem_modelo,
            substituir=substituir,
            manter_sombra=(papel == "titulo"),
        )
        if papel == "ementa":
            _diferenciar_rotulo(paragraph, spec)
        if papel in {"corpo", "conclusao", "fecho"}:
            _italicar_aspas(paragraph)
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                for paragraph in cell.paragraphs:
                    if _tem_desenho(paragraph):
                        continue
                    papel = _papel(paragraph, mapa)
                    if papel is None or papel == "data":
                        continue
                    _enfase(paragraph, _spec(perfil, papel))
                    if papel == "corpo":
                        _italicar_aspas(paragraph)
    if data_assinatura and not perfil.get("preservar_geometria"):
        _inserir_data(document, data_assinatura, perfil.get("data") or {})
    _garantir_ementa(document, perfil, structure, origem_modelo)
    if not perfil.get("preservar_geometria"):
        from services.minutas_modelo_docx import _eh_faixa_paragrafo

        for paragraph in document.paragraphs:
            if _na_tabela(paragraph) or _eh_faixa_paragrafo(paragraph._p):
                continue
            if _papel(paragraph, mapa) == "titulo":
                continue
            _retirar_sombreamento(paragraph)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def _mapa(structure):
    from services.minutas_docx import _blocks, _closing, _conclusion, _is_report_transition

    titulos, corpos, transicoes, conclusoes, fechos = set(), set(), set(), set(), set()

    def absorver(value, paragraphs=None):
        for block in _blocks(value, paragraphs):
            transicao, texto = _is_report_transition(block)
            chave = _norm(texto)
            if not chave:
                continue
            if transicao:
                transicoes.add(chave)
            else:
                corpos.add(chave)

    absorver(structure.get("relatorio"), structure.get("relatorio_paragrafos"))
    if str(structure.get("admissibilidade") or "").strip():
        titulos.add(_norm("Admissibilidade"))
        absorver(structure.get("admissibilidade"))
    absorver(
        structure.get("fundamentacao_geral"),
        structure.get("fundamentacao_geral_paragrafos"),
    )
    for topic in structure.get("topicos_irregularidades") or ():
        if not isinstance(topic, dict):
            continue
        titulo = str(topic.get("titulo") or "").strip()
        if titulo:
            titulos.add(_norm(titulo))
        absorver(topic.get("texto"), topic.get("paragrafos"))
    responsabilidade = structure.get("responsabilizacao_administrativa")
    if isinstance(responsabilidade, dict) and str(responsabilidade.get("texto") or "").strip():
        titulos.add(_norm(
            responsabilidade.get("titulo") or "Responsabilização administrativa"
        ))
        absorver(responsabilidade.get("texto"))
    sancao = structure.get("analise_sancionatoria")
    if isinstance(sancao, dict) and str(sancao.get("texto") or "").strip():
        absorver(sancao.get("texto"))
    for block in _blocks(_conclusion(structure)):
        chave = _norm(block)
        if chave:
            conclusoes.add(chave)
    fecho = _closing(structure.get("encerramento"))
    if fecho:
        fechos.add(_norm(fecho))
    return {
        "ementa": _norm("Ementa: " + str(structure.get("ementa") or "")),
        "titulos": titulos,
        "corpos": corpos,
        "transicoes": transicoes,
        "conclusoes": conclusoes,
        "fechos": fechos,
    }


def _papel(paragraph, mapa):
    texto = str(paragraph.text or "").strip()
    chave = _norm(texto)
    if not chave or _IDENTIFICACAO.match(texto):
        return None
    estilo = paragraph.style.name if paragraph.style is not None else ""
    if estilo in _ESTILOS_FIXOS:
        return None
    if _e_data(texto):
        return "data"
    if chave == mapa["ementa"] or chave.startswith("ementa:"):
        return "ementa"
    if chave in mapa["titulos"]:
        return "titulo"
    if chave in mapa["transicoes"]:
        return "transicao"
    if chave in mapa["fechos"] or chave == _norm("É o Parecer."):
        return "fecho"
    if chave in mapa["conclusoes"]:
        return "conclusao"
    if _CITACAO.match(texto):
        return "citacao"
    if chave in mapa["corpos"] or estilo in {
        "Minuta Corpo", "Minuta Transcrição", "Minuta Encerramento", "Minuta Transição",
        "Minuta Ementa", "Minuta Irregularidade",
    }:
        if estilo == "Minuta Transcrição" or _CITACAO.match(texto):
            return "citacao"
        if estilo == "Minuta Irregularidade":
            return "titulo"
        if estilo == "Minuta Transição":
            return "transicao"
        if estilo == "Minuta Encerramento":
            return "fecho"
        if estilo == "Minuta Ementa":
            return "ementa"
        return "corpo"
    return None


def _spec(perfil, papel):
    if papel == "transicao":
        base = dict(perfil.get("corpo") or {})
        base["bold"] = True
        base["italic"] = False
        return base
    return dict(perfil.get(papel) or {})


def _aplicar(paragraph, spec, *, origem_modelo, substituir=False, recuos=True, manter_sombra=False):
    formato = paragraph.paragraph_format
    if "align" in spec:
        formato.alignment = _ALINHAMENTOS[spec["align"]]
    if recuos and "left_cm" in spec:
        formato.left_indent = Cm(spec["left_cm"])
    if recuos and "right_cm" in spec:
        formato.right_indent = Cm(spec["right_cm"])
    elif recuos and substituir:
        formato.right_indent = Cm(0)
    if recuos and "first_cm" in spec:
        formato.first_line_indent = Cm(spec["first_cm"])
    if "before_pt" in spec:
        formato.space_before = Pt(spec["before_pt"])
    if "after_pt" in spec:
        formato.space_after = Pt(spec["after_pt"])
    if substituir and not manter_sombra:
        _retirar_sombreamento(paragraph)
    for run in paragraph.runs:
        if not _run_editavel(run):
            continue
        if "font" in spec and (substituir or not origem_modelo):
            _definir_fonte(run, spec["font"])
        if "size" in spec and (substituir or not (origem_modelo and _tem_marca(run, "w:sz"))):
            run.font.size = Pt(spec["size"])
        if "bold" in spec and (substituir or not origem_modelo or spec["bold"] is True):
            if substituir or not origem_modelo or not _tem_marca(run, "w:b"):
                run.font.bold = spec["bold"]
        if "italic" in spec and (substituir or not origem_modelo or spec["italic"] is True):
            if substituir or not origem_modelo or not _tem_marca(run, "w:i"):
                run.font.italic = spec["italic"]


def _enfase(paragraph, spec):
    for run in paragraph.runs:
        if not _run_editavel(run):
            continue
        if spec.get("bold") is True and not _tem_marca(run, "w:b"):
            run.font.bold = True
        if spec.get("italic") is True and not _tem_marca(run, "w:i"):
            run.font.italic = True


def _italicar_aspas(paragraph):
    editaveis = [run for run in paragraph.runs if run.text and _run_editavel(run)]
    if len(editaveis) != 1 or not _run_simples(editaveis[0]):
        return
    run = editaveis[0]
    texto = run.text
    if not _ASPAS.search(texto):
        return
    partes = _ASPAS.split(texto)
    if len(partes) < 2:
        return
    propriedades = run._r.find(qn("w:rPr"))
    pai = run._r.getparent()
    cursor = list(pai).index(run._r)
    pai.remove(run._r)
    for parte in partes:
        if parte == "":
            continue
        pai.insert(cursor, _novo_run(parte, propriedades, italico=bool(_ASPAS.fullmatch(parte))))
        cursor += 1


def _retirar_sombreamento(paragraph):
    propriedades = paragraph._p.find(qn("w:pPr"))
    if propriedades is None:
        return
    sombra = propriedades.find(qn("w:shd"))
    if sombra is not None:
        propriedades.remove(sombra)


def _ancora_da_ementa(document):
    """Ponto imediatamente anterior à faixa PARECER ou à primeira tabela."""
    from services.minutas_modelo_docx import _eh_faixa_paragrafo

    for child in document.element.body:
        if child.tag == qn("w:sectPr"):
            continue
        if _eh_faixa_paragrafo(child) or child.tag == qn("w:tbl"):
            return child
    return None


def _sanear_identificacao(document, target):
    """Troca identificação do modelo pela do processo atual, sem mexer no corpo jurídico."""
    from services.minutas_generation import _draft_header_lines
    from services.minutas_modelo_docx import (
        _atualizar_linha_identificacao,
        _eh_faixa,
        _eh_faixa_paragrafo,
        _substituir_paragrafo,
        _tem_midia,
        _texto,
    )

    linhas = _draft_header_lines(target)
    candidatos = []
    for section in document.sections:
        for part in (section.header, section.footer):
            elemento = getattr(part, "_element", None)
            if elemento is None:
                continue
            candidatos.extend(elemento.iter(qn("w:p")))
    for child in document.element.body:
        if child.tag == qn("w:sectPr"):
            continue
        if _eh_faixa_paragrafo(child) or (child.tag == qn("w:tbl") and _eh_faixa(child)):
            break
        bruto = _texto(child).strip()
        if bruto.casefold().startswith("ementa") or len(bruto) > 220:
            break
        candidatos.extend(child.iter(qn("w:p")))
    textos = [_texto(item) for item in candidatos]
    tem_exercicio = any(
        re.search(r"exerc[ií]cio", texto, re.IGNORECASE) and "natureza" not in texto.casefold()
        for texto in textos
    )
    vistos = set()
    for paragrafo, texto in zip(candidatos, textos):
        if id(paragrafo) in vistos:
            continue
        vistos.add(id(paragrafo))
        plain = texto.strip()
        if not plain or len(plain) > 220 or plain.casefold().startswith("ementa"):
            continue
        novo = _atualizar_linha_identificacao(plain, target, linhas, tem_exercicio)
        if not novo or novo == plain:
            continue
        if _tem_midia(paragrafo):
            textos = list(paragrafo.iter(qn("w:t")))
            if not textos:
                continue
            textos[0].text = novo
            for extra in textos[1:]:
                extra.text = ""
            continue
        _substituir_paragrafo(paragrafo, novo)


def _documento_tem_ementa(document):
    for paragraph in document.paragraphs:
        if paragraph.text.strip().casefold().startswith("ementa"):
            return True
    for table in document.tables:
        for row in table.rows:
            for cell in row.cells:
                if "ementa" in cell.text.casefold():
                    return True
    return False


def _garantir_ementa(document, perfil, structure, origem_modelo):
    """Recoloca a ementa quando o modelo estrutural a descartou junto com a identificação."""
    texto = re.sub(r"\s+", " ", str(structure.get("ementa") or "")).strip()
    if not texto or perfil.get("preservar_geometria") or _documento_tem_ementa(document):
        return
    spec = _spec(perfil, "ementa")
    paragrafo = document.add_paragraph("Ementa: " + texto)
    ancora = _ancora_da_ementa(document)
    if ancora is not None:
        ancora.addprevious(paragrafo._p)
    else:
        ancora_paragrafo = next(
            (
                item for item in document.paragraphs
                if item._p is not paragrafo._p and item.text.strip()
            ),
            None,
        )
        if ancora_paragrafo is not None:
            ancora_paragrafo._p.addprevious(paragrafo._p)
    _aplicar(
        paragrafo, spec, origem_modelo=origem_modelo, substituir=bool(origem_modelo),
    )
    _diferenciar_rotulo(paragrafo, spec)


def _inserir_data(document, texto, spec):
    if any(_e_data(paragraph.text) for paragraph in document.paragraphs):
        return
    assinatura = next(
        (
            paragraph for paragraph in document.paragraphs
            if paragraph.style is not None and paragraph.style.name == "Minuta Procuradora"
        ),
        None,
    )
    if assinatura is None:
        preenchidos = [
            paragraph for paragraph in document.paragraphs
            if paragraph.text.strip() and not _na_tabela(paragraph)
        ]
        if len(preenchidos) >= 2:
            assinatura = preenchidos[-2]
        elif preenchidos:
            assinatura = preenchidos[-1]
    if assinatura is None:
        return
    novo = assinatura.insert_paragraph_before(texto)
    _aplicar(novo, spec, origem_modelo=False)


def _diferenciar_rotulo(paragraph, spec):
    rotulo = spec.get("rotulo")
    if not isinstance(rotulo, dict):
        return
    editaveis = [run for run in paragraph.runs if run.text and _run_simples(run)]
    if not editaveis:
        return
    if len(editaveis) > 1 and _ROTULO_EMENTA.fullmatch(editaveis[0].text):
        _marcar_run(editaveis[0], rotulo.get("bold"), rotulo.get("italic"))
        for run in editaveis[1:]:
            _marcar_run(run, spec.get("bold"), spec.get("italic"))
        return
    if len(editaveis) != 1:
        return
    run = editaveis[0]
    encontrado = _ROTULO_EMENTA.match(run.text)
    if encontrado is None or encontrado.end() >= len(run.text):
        return
    prefixo, resto = run.text[:encontrado.end()], run.text[encontrado.end():]
    propriedades = run._r.find(qn("w:rPr"))
    pai = run._r.getparent()
    cursor = list(pai).index(run._r)
    pai.remove(run._r)
    pai.insert(cursor, _novo_run(
        prefixo, propriedades, italico=rotulo.get("italic"), negrito=rotulo.get("bold"),
    ))
    pai.insert(cursor + 1, _novo_run(
        resto, propriedades, italico=spec.get("italic"), negrito=spec.get("bold"),
    ))


def _marcar_run(run, negrito, italico):
    if negrito is not None:
        run.font.bold = negrito
    if italico is not None:
        run.font.italic = italico


def _definir_fonte(run, nome):
    run.font.name = nome
    run.font.color.rgb = RGBColor(0, 0, 0)
    fontes = run._element.get_or_add_rPr().get_or_add_rFonts()
    for atributo in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        fontes.set(qn(atributo), nome)


def _novo_run(texto, propriedades, *, italico=None, negrito=None):
    if propriedades is not None and propriedades.getparent() is not None:
        run = deepcopy(propriedades.getparent())
        for child in list(run):
            if child.tag != qn("w:rPr"):
                run.remove(child)
    else:
        run = OxmlElement("w:r")
        if propriedades is not None:
            run.append(deepcopy(propriedades))
    rpr = run.find(qn("w:rPr"))
    if italico is not None or negrito is not None:
        if rpr is None:
            rpr = OxmlElement("w:rPr")
            run.insert(0, rpr)
        _fixar_marca(rpr, "w:i", italico)
        _fixar_marca(rpr, "w:b", negrito)
    node = OxmlElement("w:t")
    node.set(qn("xml:space"), "preserve")
    node.text = texto
    run.append(node)
    return run


def _fixar_marca(propriedades, tag, valor):
    if valor is None:
        return
    marca = propriedades.find(qn(tag))
    if marca is None:
        marca = OxmlElement(tag)
        propriedades.append(marca)
    marca.set(qn("w:val"), "true" if valor else "false")


def _norm(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _e_data(texto):
    texto = str(texto or "").strip()
    return len(texto) <= 80 and _DATA.fullmatch(texto) is not None


def _na_tabela(paragraph):
    return paragraph._p.getparent().tag == qn("w:tc")


def _tem_numeracao(paragraph):
    propriedades = paragraph._p.find(qn("w:pPr"))
    return propriedades is not None and propriedades.find(qn("w:numPr")) is not None


def _tem_desenho(paragraph):
    return next(paragraph._p.iter(qn("w:drawing")), None) is not None or next(
        paragraph._p.iter(qn("w:pict")), None
    ) is not None


def _run_editavel(run):
    return next(run._r.iter(qn("w:drawing")), None) is None and next(
        run._r.iter(qn("w:pict")), None
    ) is None


def _run_simples(run):
    return all(child.tag in {qn("w:rPr"), qn("w:t")} for child in run._r)


def _tem_marca(run, tag):
    propriedades = run._r.find(qn("w:rPr"))
    return propriedades is not None and propriedades.find(qn(tag)) is not None

