from pathlib import Path
from zipfile import ZipFile
import csv
import hashlib
import io
import json

import pytest
from streamlit.testing.v1 import AppTest

from database.access import AccessStore
from database.agenda import AgendaStore
from database.audit import AuditStore
from database.memorandos import MemorandosStore
from database.oficios import OficiosStore
from database.store import ROOT
from services.access import resolve_principal
from services.backup import backup_filename, generate_backup, unique_backup_path
from services.system_ui import BACKUP_OFFERED, BACKUP_RESULT
from tests.access_testing import TEST_IDENTITY, enable_login, seed_access
from tests.cases import sample
from tests.test_postgresql import pg_store, pg_url  # noqa: F401

FORBIDDEN = (
    b"DATABASE_URL",
    b"client_secret",
    b"cookie_secret",
    b"postgresql://",
    b"postgres://",
    b"oauth",
)


def _principal(store, email=TEST_IDENTITY["email"]):
    return resolve_principal(store, {"email": email})


def _prepare(store):
    AgendaStore(store)
    OficiosStore(store)
    AuditStore(store)
    MemorandosStore(store)
    return store


def _insert_blobs(store):
    identifier = store.save_draft(sample(store))
    store.finalize(identifier)
    pdf = b"%PDF-1.4 portaria-original\n"
    store.cache_pdf(identifier, pdf)
    portaria = store.get(identifier)
    memo_id = "memo-backup-teste"
    pdf_memo = b"%PDF-1.4 memorando-original\n"
    docx_memo = b"PK\x03\x04memorando-docx-original"
    stamp = "2026-09-14T12:00:00+00:00"
    with store.connection() as c:
        c.execute(
            "INSERT INTO memorandos VALUES(?,?,?,?,?,?,?,?,?)",
            (
                memo_id,
                "SUBSTITUICAO",
                "FINALIZADO",
                "admin@test.local",
                stamp,
                stamp,
                stamp,
                "1/2026",
                '{"assunto":"José da Conceição"}',
            ),
        )
        c.execute(
            "INSERT INTO memorandos_arquivos VALUES(?,?,?,?,?,?,?,?)",
            (
                "arq-memo-pdf",
                memo_id,
                "memorando.pdf",
                "application/pdf",
                len(pdf_memo),
                hashlib.sha256(pdf_memo).hexdigest(),
                stamp,
                pdf_memo,
            ),
        )
        c.execute(
            "INSERT INTO memorandos_arquivos VALUES(?,?,?,?,?,?,?,?)",
            (
                "arq-memo-docx",
                memo_id,
                "memorando.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                len(docx_memo),
                hashlib.sha256(docx_memo).hexdigest(),
                stamp,
                docx_memo,
            ),
        )
        c.execute(
            "INSERT INTO oficios(id,direcao,serie,ano,numero,status,data,prazo,membro_id,"
            "assunto,destinatario,numero_externo,responde_a,payload,criada,atualizada,"
            "data_envio,cancelada) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "oficio-backup",
                "RECEBIDO",
                None,
                2026,
                None,
                "Protocolado",
                "2026-09-14",
                None,
                None,
                "Ofício de José da Conceição",
                "MPC",
                "EXT-1",
                None,
                "{}",
                stamp,
                stamp,
                None,
                None,
            ),
        )
        oficio_pdf = b"%PDF-1.4 oficio-original\n"
        c.execute(
            "INSERT INTO oficio_arquivos VALUES(?,?,?,?,?,?,?)",
            (
                "arq-oficio",
                "oficio-backup",
                "oficio.pdf",
                "application/pdf",
                len(oficio_pdf),
                stamp,
                oficio_pdf,
            ),
        )
    AccessStore(store).save_user(
        {
            "nome": "José da Conceição",
            "email": "jose.conceicao@test.local",
            "perfil": "USUARIO",
            "ativo": True,
            "pode_portarias": True,
            "pode_agenda": False,
            "pode_oficios": False,
            "pode_memorandos": False,
            "pode_admin": False,
            "gabinetes": [],
        }
    )
    return {
        "portaria_id": identifier,
        "portaria_docx": portaria["docx"],
        "portaria_pdf": pdf,
        "memo_pdf": pdf_memo,
        "memo_docx": docx_memo,
        "oficio_pdf": oficio_pdf,
    }


def _scan_zip(path):
    with ZipFile(path) as archive:
        for info in archive.infolist():
            data = archive.read(info.filename)
            for token in FORBIDDEN:
                if token in data:
                    raise AssertionError(
                        f"{token.decode()} encontrado em {info.filename}"
                    )


def _generate(store, tmp_path, name="backup.zip"):
    principal = _principal(store)
    destination = tmp_path / name
    result = generate_backup(store, principal, destination)
    return result, destination


def test_backup_zip_manifest_readme_and_utf8(store, tmp_path):
    _prepare(store)
    blobs = _insert_blobs(store)
    result, path = _generate(store, tmp_path)
    assert path.is_file()
    assert ZipFile(path).testzip() is None
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "README.txt" in names
        assert "schema/schema_manifest.json" in names
        assert "dados/usuarios_acesso.csv" in names
        assert "dados/usuario_gabinetes.csv" in names
        assert "dados/portarias.csv" in names
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["aplicacao"] == "Ferramentas MPC-PB"
        assert manifest["formato"] == 1
        assert manifest["engine"] == "SQLite"
        assert manifest["quantidade_tabelas"] >= 1
        assert manifest["quantidade_documentos"] >= 4
        readme = archive.read("README.txt").decode("utf-8")
        assert "Ferramentas MPC-PB" in readme
        assert "Portal Integrado de Gestão e Apoio Operacional" in readme
        assert "restauração" in readme.casefold() or "restauracao" in readme.casefold()
        users = archive.read("dados/usuarios_acesso.csv").decode("utf-8")
        assert "José da Conceição" in users
        rows = list(csv.reader(io.StringIO(users)))
        assert "email" in rows[0]
        documents = json.loads(archive.read("schema/documentos.json"))
        by_id = {item["id"]: item for item in documents}
        portaria_pdf = next(
            i for i in documents if i["tabela"] == "portarias" and i["coluna"] == "pdf"
        )
        assert archive.read(portaria_pdf["arquivo"]) == blobs["portaria_pdf"]
        assert portaria_pdf["sha256"] == hashlib.sha256(blobs["portaria_pdf"]).hexdigest()
        portaria_docx = next(
            i for i in documents if i["tabela"] == "portarias" and i["coluna"] == "docx"
        )
        assert archive.read(portaria_docx["arquivo"]) == blobs["portaria_docx"]
        memo_pdf = next(
            i
            for i in documents
            if i["id"] == "arq-memo-pdf"
        )
        assert archive.read(memo_pdf["arquivo"]) == blobs["memo_pdf"]
        assert memo_pdf["sha256"] == hashlib.sha256(blobs["memo_pdf"]).hexdigest()
        memo_docx = next(i for i in documents if i["id"] == "arq-memo-docx")
        assert archive.read(memo_docx["arquivo"]) == blobs["memo_docx"]
        oficio = next(i for i in documents if i["id"] == "arq-oficio")
        assert archive.read(oficio["arquivo"]) == blobs["oficio_pdf"]
        assert "backup_snapshots" not in "".join(names)
        assert not any("secrets.toml" in n for n in names)
    _scan_zip(path)
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    with store.connection(read_only=True) as c:
        events = [
            r[0]
            for r in c.execute("SELECT evento FROM auditoria_eventos ORDER BY id")
        ]
    assert "BACKUP_GERADO" in events


def test_null_and_dates_in_csv(store, tmp_path):
    _prepare(store)
    store.save_draft(sample(store))
    with store.connection() as c:
        c.execute(
            "INSERT INTO eventos(instante,acao,detalhes) VALUES(?,?,?)",
            ("2026-09-14T12:00:00+00:00", "teste", "{}"),
        )
    _, path = _generate(store, tmp_path, "datas.zip")
    with ZipFile(path) as archive:
        text = archive.read("dados/eventos.csv").decode("utf-8")
        portarias = archive.read("dados/portarias.csv").decode("utf-8")
    assert "2026-09-14T12:00:00+00:00" in text
    reader = csv.DictReader(io.StringIO(portarias))
    rows = list(reader)
    assert rows
    assert "docx" not in reader.fieldnames
    assert any(row["numero"] == "" for row in rows)


def test_secrets_are_not_exported(store, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@example/db")
    monkeypatch.setenv("client_secret", "never-export")
    _prepare(store)
    _, path = _generate(store, tmp_path, "secrets.zip")
    _scan_zip(path)
    raw = path.read_bytes()
    assert b"never-export" not in raw
    assert b"postgresql://user:secret" not in raw


def test_non_admin_cannot_generate_backup(store, tmp_path):
    seed_access(
        store,
        email="comum.backup@test.local",
        perfil="USUARIO",
        pode_admin=False,
        pode_agenda=True,
    )
    principal = resolve_principal(store, {"email": "comum.backup@test.local"})
    with pytest.raises(ValueError, match="módulo"):
        generate_backup(store, principal, tmp_path / "denied.zip")
    assert not (tmp_path / "denied.zip").exists()


def test_failed_export_does_not_leave_valid_backup(store, tmp_path, monkeypatch):
    _prepare(store)
    destination = tmp_path / "parcial.zip"

    def boom(*args, **kwargs):
        raise RuntimeError("falha sintética")

    monkeypatch.setattr("services.backup.ZipFile", boom)
    with pytest.raises(ValueError, match="Não foi possível gerar o backup"):
        generate_backup(store, _principal(store), destination)
    assert not destination.exists()


def test_rerun_does_not_generate_backup(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_admin").click().run()
    app.radio(key="admin_secao").set_value("Sistema").run()
    app.radio(key="admin_sistema_aba").set_value("Backup").run()
    assert not app.exception
    with store.connection(read_only=True) as c:
        before = c.execute(
            "SELECT COUNT(*) FROM auditoria_eventos WHERE evento='BACKUP_GERADO'"
        ).fetchone()[0]
    app.run()
    app.run()
    with store.connection(read_only=True) as c:
        after = c.execute(
            "SELECT COUNT(*) FROM auditoria_eventos WHERE evento='BACKUP_GERADO'"
        ).fetchone()[0]
    assert before == after == 0
    assert any(b.label == "Gerar backup" for b in app.button)
    assert not any(b.label == "Baixar backup" for b in app.get("download_button"))


def _audit_count(store, evento):
    with store.connection(read_only=True) as c:
        return c.execute(
            "SELECT COUNT(*) FROM auditoria_eventos WHERE evento=?", (evento,)
        ).fetchone()[0]


def test_unique_internal_paths_and_friendly_download_name(tmp_path):
    first = unique_backup_path(tmp_path)
    second = unique_backup_path(tmp_path)
    assert first != second
    assert not first.exists() and not second.exists()
    assert first.name.startswith("backup_") and first.suffix == ".zip"
    assert first.name != backup_filename()
    assert backup_filename().startswith("backup_ferramentas_mpcpb_")
    assert backup_filename().endswith(".zip")


def test_two_generations_same_minute_do_not_collide(store, tmp_path):
    _prepare(store)
    first_path = unique_backup_path(tmp_path)
    second_path = unique_backup_path(tmp_path)
    first = generate_backup(store, _principal(store), first_path)
    second = generate_backup(store, _principal(store), second_path)
    assert first_path.is_file() and second_path.is_file()
    assert first["caminho"] != second["caminho"]
    assert first["arquivo"].startswith("backup_ferramentas_mpcpb_")
    assert second["arquivo"].startswith("backup_ferramentas_mpcpb_")
    assert Path(first["caminho"]).name != first["arquivo"]
    assert _audit_count(store, "BACKUP_GERADO") == 2


def test_leftover_temp_file_does_not_block_new_generation(store, tmp_path):
    from tempfile import NamedTemporaryFile

    _prepare(store)
    leftover = NamedTemporaryFile(
        prefix="mpc_backup_", suffix=".zip", delete=False, dir=tmp_path
    )
    leftover.close()
    assert Path(leftover.name).exists()
    destination = unique_backup_path(tmp_path)
    result = generate_backup(store, _principal(store), destination)
    assert destination.is_file()
    assert result["arquivo"].startswith("backup_ferramentas_mpcpb_")
    assert Path(leftover.name).exists()


def test_existing_external_path_is_not_overwritten(store, tmp_path):
    _prepare(store)
    destination = tmp_path / "externo.zip"
    destination.write_bytes(b"preserve")
    with pytest.raises(ValueError, match="já existe"):
        generate_backup(store, _principal(store), destination)
    assert destination.read_bytes() == b"preserve"


def test_failed_second_generation_keeps_previous_and_drops_partial(
    store, tmp_path, monkeypatch
):
    _prepare(store)
    previous = unique_backup_path(tmp_path)
    first = generate_backup(store, _principal(store), previous)
    assert previous.is_file()
    failed = unique_backup_path(tmp_path)

    def boom(*args, **kwargs):
        raise RuntimeError("falha sintética")

    monkeypatch.setattr("services.backup.ZipFile", boom)
    with pytest.raises(ValueError, match="Não foi possível gerar o backup"):
        generate_backup(store, _principal(store), failed)
    assert previous.is_file()
    assert hashlib.sha256(previous.read_bytes()).hexdigest() == first["sha256"]
    assert not failed.exists()


def test_ui_second_generation_replaces_session_without_duplicate_audit(
    store, monkeypatch
):
    from services.system_ui import BACKUP_RESULT as RESULT_KEY

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    app.button(key="open_admin").click().run()
    app.radio(key="admin_secao").set_value("Sistema").run()
    app.radio(key="admin_sistema_aba").set_value("Backup").run()
    app.button(key="sistema_backup_gerar").click().run()
    assert not app.exception and not app.error
    first = dict(app.session_state[RESULT_KEY])
    first_path = Path(first["caminho"])
    assert first_path.is_file()
    assert first["arquivo"].startswith("backup_ferramentas_mpcpb_")
    assert any(b.label == "Baixar backup" for b in app.get("download_button"))
    assert _audit_count(store, "BACKUP_GERADO") == 1
    assert _audit_count(store, "BACKUP_DISPONIBILIZADO") == 1
    offered = app.session_state[BACKUP_OFFERED]
    app.run()
    app.radio(key="admin_sistema_aba").set_value("Saúde").run()
    app.radio(key="admin_sistema_aba").set_value("Backup").run()
    assert _audit_count(store, "BACKUP_GERADO") == 1
    assert _audit_count(store, "BACKUP_DISPONIBILIZADO") == 1
    assert app.session_state[BACKUP_OFFERED] == offered
    app.button(key="sistema_backup_gerar").click().run()
    assert not app.exception
    second = dict(app.session_state[RESULT_KEY])
    second_path = Path(second["caminho"])
    assert second_path.is_file()
    assert first["caminho"] != second["caminho"]
    assert not first_path.exists()
    assert _audit_count(store, "BACKUP_GERADO") == 2
    assert _audit_count(store, "BACKUP_DISPONIBILIZADO") == 2
    app.run()
    assert _audit_count(store, "BACKUP_GERADO") == 2
    assert _audit_count(store, "BACKUP_DISPONIBILIZADO") == 2


def test_postgres_logical_backup_contract(pg_store, tmp_path):
    _prepare(pg_store)
    blobs = _insert_blobs(pg_store)
    result, path = _generate(pg_store, tmp_path, "pg.zip")
    assert result["engine"] == "postgresql"
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["engine"] == "PostgreSQL"
        schema = json.loads(archive.read("schema/schema_manifest.json"))
        assert "schema_migrations" in schema["tabelas_exportadas"]
        assert "backup_snapshots" not in schema["tabelas_exportadas"]
        documents = json.loads(archive.read("schema/documentos.json"))
        memo = next(i for i in documents if i["id"] == "arq-memo-pdf")
        assert archive.read(memo["arquivo"]) == blobs["memo_pdf"]
    _scan_zip(path)
