from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
import sqlite3
import pytest
from database.store import Store
from services.exports import export_record
from tests.cases import sample


def finalized(store, count=1):
    ids = [store.save_draft(sample(store)) for _ in range(count)]
    for identifier in ids:
        store.finalize(identifier, confirmed_warnings=True)
    return ids


def remove(store, identifier, **kwargs):
    return store.delete_portaria(
        identifier, "Portaria criada para teste", True, "EXCLUIR", **kwargs
    )


@pytest.mark.parametrize("count,index,expected", [(1, 0, 9), (2, 1, 10), (3, 0, 12)])
def test_deletion_cases_a_b_c(store, count, index, expected):
    ids = finalized(store, count)
    result = remove(store, ids[index])
    assert store.next_number(2026) == expected
    assert store.baseline(2026) == 8
    assert ids[index] not in [r["id"] for r in store.history()]
    for i, identifier in enumerate(ids):
        if i != index:
            assert store.get(identifier)["numero"] == 9 + i
    backup = Store(Path(result["backup"]))
    assert backup.get(ids[index])["status"] == "Finalizada"
    assert backup.next_number(2026) == 9 + count


def test_cancelled_lower_number_occupied(store):
    first, second = finalized(store, 2)
    store.cancel(first, "Teste", True)
    remove(store, second)
    assert store.next_number(2026) == 10
    assert store.get(first)["status"] == "Cancelada"
    assert store.get(first)["numero"] == 9


def test_successive_deletions(store):
    ids = finalized(store, 3)
    for identifier, next_value in zip(reversed(ids), [11, 10, 9]):
        remove(store, identifier)
        assert store.next_number(2026) == next_value
    assert store.baseline(2026) == 8


def test_delete_cancelled(store):
    identifier = finalized(store)[0]
    store.cancel(identifier, "Teste", True)
    assert store.next_number(2026) == 10
    remove(store, identifier)
    assert store.next_number(2026) == 9
    assert store.deletion_history()[0]["status_anterior"] == "Cancelada"


def test_rollback_all_on_log_failure(store, monkeypatch):
    identifier = finalized(store)[0]
    before = store.get(identifier)

    def fail(*args):
        raise OSError("Falha de log injetada")

    monkeypatch.setattr(store, "event", fail)
    with pytest.raises(OSError):
        remove(store, identifier)
    assert store.get(identifier) == before
    assert store.next_number(2026) == 10
    assert not store.deletion_history()
    with store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM substituicoes WHERE portaria_id=?", (identifier,)
            ).fetchone()[0]
            == 1
        )
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_failed_backup_blocks_deletion(store, monkeypatch):
    identifier = finalized(store)[0]

    def fail(*args):
        raise OSError("Permissão negada")

    monkeypatch.setattr(store, "backup", fail)
    with pytest.raises(
        ValueError, match="Não foi possível criar o backup de segurança"
    ):
        remove(store, identifier)
    assert store.get(identifier)["status"] == "Finalizada"
    assert store.next_number(2026) == 10
    assert not store.deletion_history()


def test_relations_removed_and_audit_complete(store):
    p = sample(store, 6)
    identifier = store.save_draft(p)
    store.finalize(identifier)
    remove(store, identifier)
    with store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM substituicoes").fetchone()[0] == 0
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    log = store.deletion_history()[0]
    assert log["portaria_id_original"] == identifier
    assert log["numero"] == 9 and log["ano"] == 2026
    assert log["acao"] == "EXCLUSAO_DEFINITIVA"
    assert log["motivo"] == "Portaria criada para teste"
    assert json.loads(log["dados_resumidos"]) == p
    assert log["data_hora"] and Path(log["backup"]).is_file()
    new = finalized(store)[0]
    assert store.get(new)["numero"] == 9  # Audit rows never occupy numbers.


def test_delete_draft_no_sequence_change(store):
    identifier = store.save_draft(sample(store))
    store.delete_draft(identifier, True)
    assert store.next_number(2026) == 9
    assert store.deletion_history()[0]["acao"] == "EXCLUSAO_RASCUNHO"
    assert store.deletion_history()[0]["numero"] is None
    assert not store.history()


def test_delete_repeat_does_not_touch_reused_number(store):
    identifier = finalized(store)[0]
    remove(store, identifier)
    new = finalized(store)[0]
    assert remove(store, identifier)["already_deleted"]
    assert store.get(new)["numero"] == 9
    assert store.next_number(2026) == 10
    assert len(store.deletion_history()) == 1
    with pytest.raises(ValueError, match="já foi excluída"):
        store.save_draft(sample(store), identifier)


@pytest.mark.parametrize(
    "confirmed,text",
    [(False, "EXCLUIR"), (True, ""), (True, "excluir"), (True, "EXCLUIR ")],
)
def test_server_requires_confirmation(store, confirmed, text):
    identifier = finalized(store)[0]
    with pytest.raises(ValueError):
        store.delete_portaria(identifier, "Teste", confirmed, text)
    assert store.get(identifier)["status"] == "Finalizada"


def test_invalid_id_and_empty_reason(store):
    identifier = finalized(store)[0]
    for invalid in ["1 OR 1=1", "bad", None]:
        with pytest.raises(ValueError):
            remove(store, invalid)
    with pytest.raises(ValueError):
        store.delete_portaria(identifier, " ", True, "EXCLUIR")
    with pytest.raises(ValueError):
        store.delete_draft(identifier, True)
    assert store.get(identifier)["numero"] == 9


def test_linked_files_only_quarantined(store, tmp_path):
    identifier = finalized(store)[0]
    docx, content = export_record(store, identifier)
    # Cache is synthetic here; actual Word generation is covered by integration checks.
    store.cache_pdf(identifier, b"%PDF-1.4\nTEST")
    pdf, _ = export_record(store, identifier, "pdf")
    unrelated = docx.with_name("Portaria_PROGE_009_2026_alheia.docx")
    unrelated.write_bytes(content)
    result = remove(store, identifier)
    assert not docx.exists() and not pdf.exists()
    assert unrelated.exists()
    with store.connection() as c:
        files = c.execute(
            "SELECT * FROM audit_arquivos WHERE audit_id=?", (result["audit_id"],)
        ).fetchall()
        assert len(files) == 2 and all(f["estado"] == "QUARENTENA" for f in files)
        assert all(Path(f["quarentena"]).exists() for f in files)
        assert c.execute("SELECT COUNT(*) FROM exportacoes").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["missing", "changed", "retained", "permission"])
def test_file_edge_cases_preserve_database(store, monkeypatch, mode):
    identifier = finalized(store)[0]
    path, _ = export_record(store, identifier)
    if mode == "missing":
        path.unlink()
    if mode == "changed":
        path.write_bytes(b"Arquivo do usuario alterado")
    if mode == "permission":

        def fail(*args):
            raise PermissionError("Acesso negado simulado")

        monkeypatch.setattr("services.deletion.digest", fail)
    remove(store, identifier, delete_files=mode != "retained")
    assert not store.history() and store.next_number(2026) == 9
    with store.connection() as c:
        state = c.execute("SELECT estado FROM audit_arquivos").fetchone()[0]
    assert (
        state
        == {
            "missing": "AUSENTE",
            "changed": "MANTIDO_ALTERADO",
            "retained": "MANTIDO",
            "permission": "MANTIDO_ERRO",
        }[mode]
    )
    if mode != "missing":
        assert path.exists()


def test_file_original_intact_if_database_rollback(store, monkeypatch):
    identifier = finalized(store)[0]
    path, content = export_record(store, identifier)

    def fail(*args):
        raise OSError("Falha após preparar quarentena")

    monkeypatch.setattr(store, "event", fail)
    with pytest.raises(OSError):
        remove(store, identifier)
    assert path.read_bytes() == content
    assert store.get(identifier)["docx"] == content
    assert not store.deletion_history()


def test_permission_error_after_commit_is_logged(store, monkeypatch):
    identifier = finalized(store)[0]
    path, content = export_record(store, identifier)
    original = Path.unlink

    def fail_original(self, *args, **kwargs):
        if self == path:
            raise PermissionError("Arquivo aberto no Word")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_original)
    remove(store, identifier)
    assert not store.history() and store.next_number(2026) == 9
    assert path.read_bytes() == content
    with store.connection() as c:
        row = c.execute("SELECT * FROM audit_arquivos").fetchone()
    assert row["estado"] == "MANTIDO_ERRO"
    assert Path(row["quarentena"]).read_bytes() == content


@pytest.mark.parametrize("attempt", range(4))
def test_deletion_finalization_concurrency(store, attempt):
    old = finalized(store)[0]
    new = store.save_draft(sample(store))
    with ThreadPoolExecutor(max_workers=2) as pool:
        a = pool.submit(remove, store, old)
        b = pool.submit(store.finalize, new, confirmed_warnings=True)
        a.result()
        b.result()
    record = store.get(new)
    assert record["numero"] in (9, 10)
    assert store.next_number(2026) == record["numero"] + 1
    assert len(store.history()) == 1
    with store.connection() as c:
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_admin_gaps_and_other_year(store):
    store.configure(admin_number="1")
    identifier = store.save_draft(sample(store))
    store.finalize(identifier, 15)
    store.set_sequence(2027, 4, True)
    remove(store, identifier)
    assert store.next_number(2026) == 9 and store.next_number(2027) == 5
    with pytest.raises(ValueError):
        store.set_sequence(2026, 0, True)


def test_deletion_survives_restart(store):
    identifier = finalized(store)[0]
    remove(store, identifier)
    restarted = Store(store.path)
    assert restarted.baseline(2026) == 8 and restarted.next_number(2026) == 9
    assert len(restarted.deletion_history()) == 1
    assert len(restarted.catalog("procuradores")) == 7


def legacy_store(store):
    finalized(store)
    with store.connection() as c:
        c.execute("DROP TABLE audit_arquivos")
        c.execute("DROP TABLE exportacoes")
        c.execute("DROP TABLE audit_log")
        c.execute("DROP TABLE sequencia_baselines")
        c.execute("DROP TRIGGER manter_portaria")
        c.execute(
            "CREATE TRIGGER manter_portaria BEFORE DELETE ON portarias WHEN OLD.status!='Rascunho' BEGIN SELECT RAISE(ABORT,'protected'); END"
        )
        c.execute("PRAGMA user_version=1")
    return store.path


def test_migration_preserves_initial_baseline_and_backup(store):
    path = legacy_store(store)
    before = store.history()
    migrated = Store(path)
    assert migrated.baseline(2026) == 8
    assert migrated.history() == before
    assert list((path.parent / "backups").glob("*antes_migration*"))
    assert len(migrated.catalog("procuradores")) == 7
    with migrated.connection() as c:
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migration_reconfirming_app_number_keeps_original_baseline(store):
    path = legacy_store(store)
    with store.connection() as c:
        store.event(c, "ajustar_sequencia", {"ano": 2026, "ultimo": 9})
    migrated = Store(path)
    assert migrated.baseline(2026) == 8
    remove(migrated, migrated.history()[0]["id"])
    assert migrated.next_number(2026) == 9


def test_migration_backup_failure_no_changes(store, monkeypatch):
    path = legacy_store(store)

    def fail(*args):
        raise OSError("Backup indisponível")

    monkeypatch.setattr(Store, "backup", fail)
    with pytest.raises(OSError):
        Store(path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='audit_log'"
        ).fetchone()
        assert c.execute("SELECT COUNT(*) FROM portarias").fetchone()[0] == 1


def test_migration_ddl_rollback(store, monkeypatch):
    path = legacy_store(store)

    def fail(*args):
        raise OSError("Falha durante migração")

    monkeypatch.setattr(Store, "event", fail)
    with pytest.raises(OSError):
        Store(path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 1
        assert not c.execute(
            "SELECT 1 FROM sqlite_master WHERE name='audit_log'"
        ).fetchone()
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
