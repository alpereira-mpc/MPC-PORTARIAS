"""Resolução e aplicação do DOCX visual cadastrado por Procurador e especialidade.

O arquivo vigente é a base do documento exportado. O gerador não recria a
página com estilos de outro gabinete nem reaproveita o texto processual do modelo.
"""

from copy import deepcopy
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

from services.minutas_docx import (
    ELVIRA_TEMPLATE,
    MinutasDocxError,
    _ALINEA,
    _blocks,
    _closing,
    _conclusion,
    _is_report_transition,
    _numbering_definition,
    _apply_numbering,
    _paragraph_metrics,
    _signature_date,
)


MENSAGEM_MODELO_AUSENTE = (
    "Não foi possível gerar o DOCX porque não há um modelo visual válido "
    "cadastrado para este Procurador e esta especialidade. Cadastre ou "
    "regularize o modelo correspondente."
)
MENSAGEM_MODELO_INVALIDO = (
    "Não foi possível gerar o DOCX porque o modelo visual cadastrado para "
    "este Procurador e esta especialidade é inválido ou incompatível. "
    "Regularize o modelo correspondente."
)
_MAX_BYTES = 10 * 1024 * 1024
_MAX_ENTRIES = 200
_MAX_UNCOMPRESSED = 40 * 1024 * 1024
_BLOQUEADOS = (
    "vbaproject",
    "oleobject",
    "activex",
    "embeddings",
    "attachedtemplate",
)


def _legado_institucional(library):
    """Só o piloto que já possui arquivo institucional próprio pode seguir sem cadastro."""
    from services.minutas_procuradores import legado_espo

    procurador = getattr(library, "procurador", None) or legado_espo()
    return procurador.sigla == "ESPO" and ELVIRA_TEMPLATE.is_file()


def _servico(library):
    from services.minutas_modelos import MinutasModeloVisualService

    repository = getattr(library, "repository", None)
    store = getattr(repository, "store", None) if repository is not None else None
    storage = getattr(library, "storage", None)
    specialty = getattr(library, "specialty_id", None)
    if store is None or storage is None or not specialty:
        return None
    return MinutasModeloVisualService(library)


def impressao_modelo(library):
    """Identidade do modelo vigente, sem baixar o arquivo nem registrar o texto."""
    service = _servico(library)
    if service is None or not service.enabled():
        return ""
    row = service.active(getattr(library, "specialty_id", None))
    if not row:
        return "ausente"
    return f"{row['id']}:{row['versao']}:{row['sha256']}"


def validar_pacote(content):
    """Recusa pacote malformado, macro ou conteúdo ativo. Não altera o original."""
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
    blob = bytes(content)
    if len(blob) > _MAX_BYTES or not blob.startswith(b"PK"):
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
    try:
        package = ZipFile(BytesIO(blob))
    except Exception as exc:
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO) from exc
    with package:
        return _validar_zip(package, blob)


def _validar_zip(package, blob):
    total = 0
    names = package.namelist()
    if len(names) > _MAX_ENTRIES:
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
    for info in package.infolist():
        name = str(info.filename or "").replace("\\", "/")
        if not name or name.startswith("/") or ".." in name.split("/"):
            raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
        lowered = name.casefold()
        if any(token in lowered for token in _BLOQUEADOS) or lowered.endswith(".docm"):
            raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
        total += int(info.file_size or 0)
        if total > _MAX_UNCOMPRESSED:
            raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
    if "[Content_Types].xml" not in names or not any(
        name.startswith("word/") for name in names
    ):
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
    return blob


def consultar_modelo(library):
    """Metadados e bytes do modelo ativo deste Procurador e desta especialidade."""
    service = _servico(library)
    if service is None or not service.enabled():
        return None
    specialty = getattr(library, "specialty_id", None)
    row = service.active(specialty)
    if not row:
        return {
            "estado": "ausente",
            "procuradora_chave": service._chave(),
            "especialidade_id": str(specialty),
        }
    try:
        content = library.storage.download(row["path"])
        blob = validar_pacote(content)
    except MinutasDocxError:
        return {"estado": "invalido", "id": str(row["id"]), "versao": int(row["versao"])}
    except Exception:
        return {"estado": "invalido", "id": str(row["id"]), "versao": int(row["versao"])}
    if sha256(blob).hexdigest() != str(row["sha256"]):
        return {"estado": "invalido", "id": str(row["id"]), "versao": int(row["versao"])}
    return {
        "estado": "valido",
        "id": str(row["id"]),
        "versao": int(row["versao"]),
        "sha256": str(row["sha256"]),
        "procuradora_chave": str(row["procuradora_chave"]),
        "especialidade_id": str(row["especialidade_id"]),
        "content": blob,
    }


def garantir_modelo_exportavel(library):
    """Impede a chamada ao modelo de IA quando o cadastro consultável não tem DOCX válido.

    Sem tabela ou sem armazenamento não há o que resolver aqui; a exportação é que
    recusa outro Procurador sem modelo próprio.
    """
    found = consultar_modelo(library)
    if found is None or (found["estado"] == "ausente" and _legado_institucional(library)):
        return None
    if found["estado"] == "valido":
        return found
    if found["estado"] == "invalido":
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
    raise MinutasDocxError(MENSAGEM_MODELO_AUSENTE)


def resolver_para_exportacao(library):
    """Modelo vigente. O arquivo institucional legado não substitui outro Procurador."""
    found = consultar_modelo(library)
    if found is not None and found["estado"] == "valido":
        return found
    if found is not None and found["estado"] == "invalido":
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
    if _legado_institucional(library):
        return None
    raise MinutasDocxError(MENSAGEM_MODELO_AUSENTE)


def _snapshot(paragraph):
    if paragraph is None:
        return None
    properties = paragraph._p.find(qn("w:pPr"))
    run_properties = None
    for run in paragraph.runs:
        if str(run.text or "").strip():
            run_properties = run._r.find(qn("w:rPr"))
            break
    return {
        "pPr": deepcopy(properties) if properties is not None else None,
        "rPr": deepcopy(run_properties) if run_properties is not None else None,
    }


_MESES = (
    "janeiro", "fevereiro", "março", "marco", "abril", "maio", "junho",
    "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
)


def _eh_data(text):
    plain = str(text or "").casefold()
    return any(month in plain for month in _MESES)


def _prototipos(document):
    samples = [
        (paragraph, _paragraph_metrics(paragraph))
        for paragraph in document.paragraphs
        if str(paragraph.text or "").strip()
    ]
    dated = next((item for item, _metrics in samples if _eh_data(item.text)), None)
    if dated is not None:
        samples = [(item, metrics) for item, metrics in samples if item is not dated]
    body = next((item for item, metrics in samples if metrics.get("first_cm")), None)
    if body is None:
        body = next(
            (item for item, metrics in samples if not metrics["center"]),
            samples[0][0] if samples else None,
        )
    indented = [
        (item, metrics)
        for item, metrics in samples
        if (metrics.get("left_cm") or 0) >= 5
    ]
    ementa = max(indented, key=lambda item: item[1]["left_cm"])[0] if indented else None
    if ementa is None:
        ementa = next(
            (item for item, _metrics in samples if item.alignment == WD_ALIGN_PARAGRAPH.RIGHT),
            None,
        )
    heading = next(
        (
            item
            for item, metrics in samples
            if metrics["bold"] and metrics["italic"] and not metrics["center"]
        ),
        None,
    )
    centered = [(item, metrics) for item, metrics in samples if metrics["center"]]
    named = next((item for item, metrics in reversed(centered) if metrics["bold"]), None)
    role = next((item for item, metrics in reversed(centered) if not metrics["bold"]), None)
    if named is None:
        named = next(
            (
                item
                for item, metrics in reversed(samples)
                if metrics["bold"] and len(str(item.text or "").strip()) <= 80
            ),
            None,
        )
    ordered = [item for item, _metrics in samples]
    if role is None and named in ordered:
        index = ordered.index(named)
        if index + 1 < len(ordered):
            role = ordered[index + 1]
    return {
        "corpo": _snapshot(body),
        "ementa": _snapshot(ementa or body),
        "titulo": _snapshot(heading or body),
        "assinatura": _snapshot(named or body),
        "cargo": _snapshot(role or named or body),
        "data": _snapshot(dated),
    }


def _limpar_corpo(document):
    body = document.element.body
    for child in list(body):
        if child.tag != qn("w:sectPr"):
            body.remove(child)


def _sem_rastreio(snapshot):
    if not snapshot or snapshot["pPr"] is None:
        return snapshot
    properties = deepcopy(snapshot["pPr"])
    for tag in ("w:numPr", "w:sectPr", "w:rPr"):
        node = properties.find(qn(tag))
        if node is not None:
            properties.remove(node)
    return {"pPr": properties, "rPr": snapshot["rPr"]}


def _escrever(document, snapshot, text):
    paragraph = document.add_paragraph()
    chosen = _sem_rastreio(snapshot)
    if chosen and chosen["pPr"] is not None:
        current = paragraph._p.find(qn("w:pPr"))
        if current is not None:
            paragraph._p.remove(current)
        paragraph._p.insert(0, deepcopy(chosen["pPr"]))
    lines = str(text or "").splitlines() or [""]
    for index, line in enumerate(lines):
        if index:
            paragraph.add_run().add_break()
        run = paragraph.add_run(line)
        if chosen and chosen["rPr"] is not None:
            current = run._r.find(qn("w:rPr"))
            if current is not None:
                run._r.remove(current)
            run._r.insert(0, deepcopy(chosen["rPr"]))
    return paragraph


def _sanitizar(document):
    properties = document.core_properties
    properties.title = ""
    properties.subject = ""
    properties.comments = ""
    properties.keywords = ""
    properties.category = ""
    properties.author = ""
    properties.last_modified_by = ""
    part = document.part
    for relationship in list(part.rels.values()):
        kind = str(relationship.reltype or "").casefold()
        if any(token in kind for token in ("comment", "customxml", "oleobject", "embedding", "activex")):
            part.drop_rel(relationship.rId)


def _blocos(structure):
    pieces = []
    pieces.append(("ementa", f"Ementa: {structure['ementa']}"))
    for block in _blocks(structure["relatorio"], structure.get("relatorio_paragrafos")):
        _transition, text = _is_report_transition(block)
        pieces.append(("corpo", text))
    if str(structure.get("admissibilidade") or "").strip():
        pieces.append(("titulo", "Admissibilidade"))
        for block in _blocks(structure["admissibilidade"]):
            pieces.append(("corpo", block))
    for block in _blocks(
        structure["fundamentacao_geral"], structure.get("fundamentacao_geral_paragrafos")
    ):
        pieces.append(("corpo", block))
    for topic in structure["topicos_irregularidades"]:
        pieces.append(("titulo", topic["titulo"]))
        for block in _blocks(topic["texto"], topic.get("paragrafos")):
            pieces.append(("corpo", block))
    responsibility = structure.get("responsabilizacao_administrativa")
    if isinstance(responsibility, dict) and str(responsibility.get("texto") or "").strip():
        pieces.append(("titulo", str(responsibility.get("titulo") or "Responsabilização administrativa")))
        for block in _blocks(responsibility["texto"]):
            pieces.append(("corpo", block))
    sanction = structure.get("analise_sancionatoria")
    if isinstance(sanction, dict) and str(sanction.get("texto") or "").strip():
        pieces.append(("titulo", "Análise sancionatória"))
        for block in _blocks(sanction["texto"]):
            pieces.append(("corpo", block))
    conclusion = _conclusion(structure)
    if conclusion:
        for block in _blocks(conclusion):
            pieces.append(("corpo", block))
    return pieces


def renderizar(content, target, structure, profile, header_lines):
    """Copia o modelo em memória, descarta o texto de exemplo e grava a minuta nova."""
    from docx import Document

    blob = validar_pacote(content)
    try:
        document = Document(BytesIO(blob))
    except Exception as exc:
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO) from exc
    if not document.sections:
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
    prototypes = _prototipos(document)
    if prototypes["corpo"] is None:
        raise MinutasDocxError(MENSAGEM_MODELO_INVALIDO)
    _limpar_corpo(document)
    _sanitizar(document)
    for line in header_lines:
        _escrever(document, prototypes["corpo"], line)
    for role, text in _blocos(structure):
        snapshot = prototypes["titulo"] if role == "titulo" else prototypes[role]
        _escrever(document, snapshot, text)
    requests = [str(item) for item in structure.get("pedidos") or () if str(item).strip()]
    if requests:
        number_id = _numbering_definition(
            document, kind="decimal", marker="%1.", left_twips=720, hanging_twips=360,
        )
        alpha_id = None
        for request in requests:
            lines = [line.strip() for line in request.splitlines() if line.strip()]
            if not lines:
                continue
            paragraph = _escrever(document, prototypes["corpo"], lines[0])
            _apply_numbering(paragraph, number_id)
            alineas = []
            for line in lines[1:]:
                match = _ALINEA.match(line)
                if match:
                    alineas.append(match.group(1).strip())
                    continue
                _escrever(document, prototypes["corpo"], line)
            if alineas:
                if alpha_id is None:
                    alpha_id = _numbering_definition(
                        document,
                        kind="lowerLetter",
                        marker="%1)",
                        left_twips=1440,
                        hanging_twips=360,
                    )
                for item in alineas:
                    subparagraph = _escrever(document, prototypes["corpo"], item)
                    _apply_numbering(subparagraph, alpha_id)
    closing = _closing(structure.get("encerramento"))
    if closing:
        _escrever(document, prototypes["corpo"], closing)
    if prototypes.get("data"):
        _escrever(document, prototypes["data"], _signature_date(target, profile))
    _escrever(document, prototypes["assinatura"], profile["nome"])
    _escrever(document, prototypes["cargo"], profile["cargo"])
    _descartar_nos_vazios(document)
    output = BytesIO()
    document.save(output)
    return output.getvalue()


def metadados_publicos(modelo):
    if not modelo:
        return None
    return {
        "id": modelo.get("id"),
        "versao": modelo.get("versao"),
        "sha256": modelo.get("sha256"),
        "procuradora_chave": modelo.get("procuradora_chave"),
        "especialidade_id": modelo.get("especialidade_id"),
    }


def _descartar_nos_vazios(document):
    """Remove marcadores de comentário que tenham sobrado fora do texto substituído."""
    body = document.element.body
    for node in list(body.iter()):
        if node.tag in {
            qn("w:commentRangeStart"),
            qn("w:commentRangeEnd"),
            qn("w:commentReference"),
        }:
            parent = node.getparent()
            if parent is not None:
                parent.remove(node)
