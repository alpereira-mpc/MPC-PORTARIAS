from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
from zipfile import ZipFile
import json
import os
import sqlite3
import subprocess
import sys
import pytest
from database.store import Store
from document_generator.pdf import PdfUnavailable, convert
from services.exports import export_record, write_new
from tests.cases import sample


def test_draft_preview_finalize_cancel_year(store):
    p = sample(store)
    identifier = store.save_draft(p)
    assert store.next_number(2026) == 9
    assert store.get(identifier)["numero"] is None
    store.finalize(identifier)
    assert store.get(identifier)["numero"] == 9
    assert store.next_number(2026) == 10
    store.finalize(identifier)
    assert store.next_number(2026) == 10
    store.cancel(identifier, "Emitida com dados incorretos", True)
    assert store.get(identifier)["numero"] == 9
    clone = store.duplicate(identifier)
    assert store.get(clone)["status"] == "Rascunho"
    assert store.get(clone)["numero"] is None
    p["data"] = "2027-01-01"
    p["substituicoes"][0].update(inicio="2027-01-02", fim="2027-01-10")
    other = store.save_draft(p)
    store.finalize(other)
    assert store.get(other)["numero"] == 1


def test_unconfigured_database(tmp_path):
    s = Store(tmp_path / "fresh.db")
    p = sample(s)
    identifier = s.save_draft(p)
    with pytest.raises(ValueError, match="Confirme"):
        s.finalize(identifier)
    assert s.get(identifier)["status"] == "Rascunho"


def test_concurrent_numbering(store):
    identifiers = [store.save_draft(sample(store)) for _ in range(6)]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(
            pool.map(lambda i: store.finalize(i, confirmed_warnings=True), identifiers)
        )
    numbers = [store.get(i)["numero"] for i in identifiers]
    assert sorted(numbers) == list(range(9, 15))
    assert store.next_number(2026) == 15


def test_same_draft_concurrent(store):
    identifier = store.save_draft(sample(store))
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(store.finalize, [identifier] * 4)) == {identifier}
    assert store.next_number(2026) == 10


def test_generator_failure_rolls_back(store, monkeypatch):
    identifier = store.save_draft(sample(store))

    def fail(*args):
        raise OSError("disk failure")

    monkeypatch.setattr("document_generator.docx.generate", fail)
    with pytest.raises(OSError):
        store.finalize(identifier)
    assert store.next_number(2026) == 9
    assert store.get(identifier)["status"] == "Rascunho"


def test_finalized_immutable(store):
    identifier = store.save_draft(sample(store))
    store.finalize(identifier)
    with pytest.raises(ValueError):
        store.save_draft(sample(store), identifier)
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection() as c:
            c.execute("DELETE FROM portarias WHERE id=?", (identifier,))
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection() as c:
            c.execute("UPDATE portarias SET numero=999 WHERE id=?", (identifier,))


def test_restart_real_process(store):
    identifier = store.save_draft(sample(store))
    store.finalize(identifier)
    before = store.get(identifier)["docx"]
    code = 'from database.store import Store; import sys,json; s=Store(sys.argv[1]); print(json.dumps([s.next_number(2026),s.get(sys.argv[2])["numero"],len(s.catalog("procuradores"))]))'
    result = subprocess.run(
        [sys.executable, "-c", code, str(store.path), identifier],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [10, 9, 7]
    restarted = Store(store.path)
    assert restarted.get(identifier)["docx"] == before
    assert restarted.settings()["sequence_confirmed"] == "1"


def test_catalog_edits_preserve_history(store):
    identifier = store.save_draft(sample(store))
    store.finalize(identifier)
    before = store.get(identifier)
    person = store.catalog("procuradores")[0]
    person["nome"] = "Novo nome"
    store.save_member(person)
    assert store.get(identifier)["payload"] == before["payload"]
    assert Store(store.path).catalog("procuradores")[0]["nome"] == "Novo nome"


def test_duplicate_unique_constraint(store):
    a = store.save_draft(sample(store))
    store.finalize(a)
    b = store.save_draft(sample(store))
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection() as c:
            c.execute(
                "UPDATE portarias SET numero=9,status='Finalizada' WHERE id=?", (b,)
            )


def test_export_never_overwrites(store):
    identifier = store.save_draft(sample(store))
    store.finalize(identifier)
    one, data = export_record(store, identifier)
    two, data2 = export_record(store, identifier)
    assert one != two
    assert one.read_bytes() == two.read_bytes() == data == data2


def test_pdf_absent_is_recoverable(store, monkeypatch):
    monkeypatch.setattr("document_generator.pdf.sys.platform", "linux")
    monkeypatch.setattr("document_generator.pdf.libreoffice_path", lambda: None)
    identifier = store.save_draft(sample(store))
    store.finalize(identifier)
    with pytest.raises(PdfUnavailable):
        export_record(store, identifier, "pdf")
    assert store.get(identifier)["docx"]
    assert store.get(identifier)["status"] == "Finalizada"
    assert store.next_number(2026) == 10


def test_sequence_admin_safeguards(store):
    with pytest.raises(ValueError):
        store.set_sequence(2026, 8)
    identifier = store.save_draft(sample(store))
    with pytest.raises(ValueError):
        store.finalize(identifier, 12)
    store.configure(admin_number="1")
    store.finalize(identifier, 12)
    assert store.next_number(2026) == 13
    with pytest.raises(ValueError):
        store.set_sequence(2026, 11, True)


def test_warning_confirmation(store):
    identifier = store.save_draft(sample(store, 5))
    with pytest.raises(ValueError, match="avisos"):
        store.finalize(identifier)
    store.finalize(identifier, confirmed_warnings=True)
    assert store.get(identifier)["status"] == "Finalizada"


def test_backup(store, tmp_path):
    identifier = store.save_draft(sample(store))
    store.finalize(identifier)
    target = tmp_path / "backup.db"
    store.backup(target)
    assert Store(target).next_number(2026) == 10
    with pytest.raises(ValueError):
        store.backup(target)


def test_office_assignment_atomic(store):
    store.assign_roles(
        [
            ("Procurador-Geral", "Tribunal Pleno", 3),
            ("Subprocurador-Geral", "1ª Câmara", 2),
            ("Subprocurador-Geral", "2ª Câmara", 1),
            ("Ouvidor", "Não se aplica", 4),
            ("Corregedor", "Não se aplica", 5),
        ]
    )
    members = {p["id"]: p for p in store.catalog("procuradores")}
    assert members[3]["funcao"] == "Procurador-Geral"
    assert members[1]["funcao"] == "Subprocurador-Geral"
    with pytest.raises(ValueError):
        store.assign_roles(
            [("Procurador-Geral", "Tribunal Pleno", 1), ("Ouvidor", "Não se aplica", 1)]
        )
    assert store.catalog("procuradores") == list(members.values())


def test_inactive_member_blocks_finalization(store):
    identifier = store.save_draft(sample(store))
    p = store.catalog("procuradores")[6]
    p["ativo"] = 0
    store.save_member(p)
    with pytest.raises(ValueError, match="inativo"):
        store.finalize(identifier)
    assert store.next_number(2026) == 9


def test_duplicate_drops_manual_template_override(store):
    p = sample(store)
    p["manual"] = {"signature_name": "NOME EXCEPCIONAL"}
    identifier = store.save_draft(p)
    duplicate = store.duplicate(identifier)
    assert "manual" not in store.get(duplicate)["payload"]
    assert (
        store.get(identifier)["payload"]["manual"]["signature_name"]
        == "NOME EXCEPCIONAL"
    )
