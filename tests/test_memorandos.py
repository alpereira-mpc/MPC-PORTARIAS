from pathlib import Path

import pytest

from database.memorandos import MemorandosStore
from services.memorandos import STATUS_FINALIZADO, fingerprint
from services.memorandos import cabinet_text, validate
from services.memorandos import read_server_xlsx


def record():
    return {
        "tipo": "SUBSTITUICAO", "data_inicio": "2026-09-11", "data_fim": "2026-09-12",
        "natureza_funcao": "Cargo comissionado", "motivo": "Licença especial", "motivo_texto": "",
        "gabinete_snapshot": "lotada na Procuradoria-Geral", "signatario_id": 1,
        "signatario_nome": "Elvira Samara Pereira de Oliveira", "signatario_cargo": "Procuradora-Geral",
        "etapas": [{"substituido": {"nome": "Ana", "genero": "Feminino", "matricula": "1", "cargo": "A"},
                    "substituto": {"nome": "Beto", "genero": "Masculino", "matricula": "2", "cargo": "B", "lotacao": "1CAM"}}],
    }


def test_server_import_upsert_and_invalid_rows(store):
    service = MemorandosStore(store)
    rows = [{"nome": "Ana", "matricula": "100", "cargo": "A", "setor": "1CAM"},
            {"nome": "Sem matrícula", "matricula": "", "cargo": "B", "setor": "X"}]
    first = service.import_servers(rows, actor_email="admin@test", filename="x.xlsx", content_hash="a", administrator=True)
    assert first["incluidos"] == 1 and first["inconsistentes"] == 1
    service.import_servers([{"nome": "Ana Atualizada", "matricula": "100", "cargo": "Novo", "setor": "2CAM"}], actor_email="admin@test", filename="x.xlsx", content_hash="b", administrator=True)
    found = service.servers("100")
    assert len(found) == 1 and found[0]["cargo"] == "Novo"


def test_server_administration_requires_administrator(store):
    service = MemorandosStore(store)
    with pytest.raises(ValueError, match="não autorizado"):
        service.import_servers([], actor_email="user@test", filename="x.xlsx", content_hash="a")


def test_synthetic_xlsx_reports_duplicate_zero_and_blank():
    from io import BytesIO
    from openpyxl import Workbook
    book = Workbook(); sheet = book.active
    sheet.append(["Nome", "Matrícula", "Cargo", "Setor"])
    sheet.append(["Ana", "100", "A", "1CAM"]); sheet.append(["Ana repetida", "100", "B", "2CAM"])
    sheet.append(["Zero", "0", "C", "X"]); sheet.append(["Vazia", "", "D", "Y"])
    out = BytesIO(); book.save(out); rows = read_server_xlsx(out.getvalue())
    from database.store import Store
    import tempfile
    service = MemorandosStore(Store(path=Path(tempfile.mkdtemp()) / "memo.db"))
    report = service.preview_import(rows)
    assert report["novos"] == 1 and len(report["duplicidades"]) == 1
    assert len(report["matriculas_zero"]) == 1 and len(report["sem_matricula"]) == 1


def test_draft_snapshot_preview_final_file_and_official_number(store):
    service = MemorandosStore(store)
    payload = record(); identifier = service.save_draft(payload, actor_email="author@test")
    draft = service.get(identifier)
    assert draft["status"] == "RASCUNHO" and draft["situacao"] == "EM ANDAMENTO"
    service.finalize(identifier, preview_hash=fingerprint(payload), pdf_bytes=b"%PDF- synthetic", filename="memo.pdf")
    assert service.get(identifier)["status"] == STATUS_FINALIZADO
    assert service.file(identifier) == ("memo.pdf", b"%PDF- synthetic")
    service.set_official_number(identifier, "123/2026", "author@test")
    assert service.get(identifier)["numero_oficial"] == "123/2026"
    assert service.file(identifier)[1] == b"%PDF- synthetic"
    with pytest.raises(ValueError):
        service.save_draft(payload, actor_email="author@test", identifier=identifier)


def test_cascade_cycle_is_rejected(store):
    payload = record()
    payload["etapas"].append({"substituido": payload["etapas"][0]["substituto"], "substituto": payload["etapas"][0]["substituido"]})
    with pytest.raises(ValueError, match="cadeia"):
        MemorandosStore(store).save_draft(payload, actor_email="author@test")


def test_two_and_three_stage_cascades_are_valid(store):
    payload = record()
    payload["etapas"].append({"substituido": dict(payload["etapas"][0]["substituto"]), "substituto": {"nome":"Caio","genero":"Masculino","matricula":"3","cargo":"C"}})
    validate(payload)
    payload["etapas"].append({"substituido": dict(payload["etapas"][1]["substituto"]), "substituto": {"nome":"Dora","genero":"Feminino","matricula":"4","cargo":"D"}})
    identifier = MemorandosStore(store).save_draft(payload, actor_email="author@test")
    assert MemorandosStore(store).get(identifier)["etapas"][2]["substituido"]["nome"] == "Caio"


def test_general_prosecution_cabinet_rule():
    elvira = {"nome": "Elvira Samara Pereira de Oliveira", "genero": "feminino"}
    assert cabinet_text(elvira, "Feminino") == "lotada na Procuradoria-Geral"
    assert "gabinete" not in cabinet_text(elvira, "Masculino")


def test_document_contains_institutional_fields():
    from document_generator.memorandos import generate
    from docx import Document
    from io import BytesIO
    content = generate(record())
    text = "\n".join(p.text for p in Document(BytesIO(content)).paragraphs)
    for item in ("MEMORANDO", "Presidente do Tribunal de Contas", "Senhor Presidente", "ELVIRA SAMARA", "Procuradora-Geral"):
        assert item in text
