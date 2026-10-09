from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from datetime import date, datetime, time, timezone
from decimal import Decimal
import hashlib
import json
import re
import sqlite3
import uuid

import pytest

from database.inventory import (
    APPLICATION_TABLES,
    BLOB_COLUMNS,
    DOCUMENT_FOLDERS,
    TRANSIENT_SCHEMA_TABLES,
)
from database.store import ROOT, Store
from services.backup import generate_backup
from services.backup_format import encode_cell, decode_cell, json_bytes
from services.restore import (
    validate_backup,
    restore_isolated,
    CONFIRMATION,
    VALID,
    INVALID,
    INCOMPATIBLE,
    LEGACY,
)
from tests.test_backup import _prepare, _principal, _insert_blobs


def test_inventory_covers_declared_schema_and_binary_folders():
    declared = set()
    for folder in (ROOT / "database", ROOT / "services"):
        for path in folder.rglob("*"):
            if path.suffix in (".py", ".sql"):
                declared.update(
                    re.findall(
                        r"CREATE TABLE\s+(?:IF NOT EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)",
                        path.read_text(encoding="utf-8"),
                        re.I,
                    )
                )
    assert declared - TRANSIENT_SCHEMA_TABLES <= set(APPLICATION_TABLES)
    assert set(BLOB_COLUMNS) - {"backup_snapshots"} <= set(DOCUMENT_FOLDERS)


def _archive(store, tmp_path):
    _prepare(store)
    path = tmp_path / "backup.zip"
    generate_backup(store, _principal(store), path)
    return path


def _rewrite(path, target, alter):
    with ZipFile(path) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    alter(entries)
    with ZipFile(target, "w", ZIP_DEFLATED) as output:
        for name, content in entries.items():
            output.writestr(name, content)
    return target


def _rehash(entries, name, value):
    entries[name] = json_bytes(value)
    manifest = json.loads(entries["manifest.json"])
    manifest["arquivos"][name] = {
        "bytes": len(entries[name]),
        "sha256": hashlib.sha256(entries[name]).hexdigest(),
    }
    entries["manifest.json"] = json_bytes(manifest)


def test_roundtrip_all_data_documents_ids_and_sequences(store, tmp_path):
    _prepare(store)
    blobs = _insert_blobs(store)
    from database.peticoes import PeticoesStore

    PeticoesStore(store)
    stamp = "2026-10-09T12:00:00+00:00"
    binary = b"%PDF synthetic\x00\xff\x01"
    with store.connection() as c:
        c.execute(
            "INSERT INTO representacoes(id,titulo,origem,data_abertura,situacao,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(501,'Teste','DE_OFICIO','2026-10-09','IDEIA',?,?,?,?)",
            (stamp, "teste", stamp, "teste"),
        )
        c.execute(
            "INSERT INTO representacao_documentos(id,representacao_id,tipo_documento,nome_arquivo,mime_type,tamanho,arquivo,criado_em,criado_por) VALUES('rep-doc',501,'OUTRO','ação.pdf','application/pdf',?,?,?,'teste')",
            (len(binary), binary, stamp),
        )
        c.execute(
            "INSERT INTO ouvidoria_manifestacoes(id,numero_interno,tipo,forma_recebimento,data_recebimento,titulo,situacao,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(602,'1/2026','NOTICIA_FATO','EMAIL','2026-10-09','Teste','RECEBIDA',?,?,?,?)",
            (stamp, "teste", stamp, "teste"),
        )
        c.execute(
            "INSERT INTO ouvidoria_documentos(id,manifestacao_id,tipo_documento,nome_arquivo,mime_type,tamanho,arquivo,criado_em,criado_por) VALUES('ouv-doc',602,'OUTRO','documento.pdf','application/pdf',?,?,?,'teste')",
            (len(binary), binary, stamp),
        )
        c.execute(
            "INSERT INTO peticoes(id,numero_tramita,data_protocolo,destinatario_tipo,destinatario,natureza,assunto,objeto,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(703,'999/26','2026-10-09','OUTRO','Teste','OUTRA','Teste','Teste',?,?,?,?)",
            (stamp, "teste", stamp, "teste"),
        )
        c.execute(
            "INSERT INTO peticoes_documentos(id,peticao_id,nome,mime_type,tamanho,arquivo,criado_em,criado_por) VALUES('pet-doc',703,'teste.pdf','application/pdf',?,?,?,'teste')",
            (len(binary), binary, stamp),
        )
        c.execute(
            "INSERT INTO relatorios_institucionais(id,tipo,ano,data_inicio,data_fim,versao,data_corte,snapshot_dados,conteudo_estruturado,criado_em) VALUES(804,'ANUAL',2026,'2026-01-01','2026-12-31',1,?,'{\"null\":null,\"empty\":\"\"}','{}',?)",
            (stamp, stamp),
        )
        c.execute(
            "INSERT INTO relatorios_institucionais_pdf(relatorio_id,nome_arquivo,conteudo,sha256,tamanho,gerado_em) VALUES(804,'teste.pdf',?,?,?,?)",
            (binary, hashlib.sha256(binary).hexdigest(), len(binary), stamp),
        )
        c.execute(
            "INSERT INTO oficio_quarentena VALUES('empty',?,'teste','{}',?)",
            (stamp, b""),
        )
        c.execute(
            "INSERT INTO eventos(id,instante,acao,detalhes) VALUES(999,?,'teste','{}')",
            (stamp,),
        )
        c.execute(
            "INSERT INTO tarefas(id,owner_user_id,titulo,criado_em,atualizado_em) VALUES(905,1,'Teste',?,?)",
            (stamp, stamp),
        )
        c.execute(
            "INSERT INTO tarefas_checklist(tarefa_id,texto,ordem,criado_em,atualizado_em) VALUES(905,'Item',1,?,?)",
            (stamp, stamp),
        )
    path = _archive(store, tmp_path)
    report = validate_backup(path, _principal(store))
    assert report["status"] == VALID, report
    assert report["restore"]["functional_reads"]["portaria"] == "ok"
    restored = restore_isolated(
        path, _principal(store), tmp_path, confirmation=CONFIRMATION
    )
    target = Path(restored["restore"]["destination"])
    assert target.exists() and target != store.path
    with (
        store.connection(read_only=True) as original,
        sqlite3.connect(target) as recovered,
    ):
        for table in restored["restore"]["counts"]:
            left = [tuple(r) for r in original.execute(f'SELECT * FROM "{table}"')]
            # BACKUP_GERADO is recorded after snapshot capture.
            right = [tuple(r) for r in recovered.execute(f'SELECT * FROM "{table}"')]
            if table == "auditoria_eventos":
                assert all(row in left for row in right)
            else:
                assert left == right, table
        assert recovered.execute("PRAGMA foreign_key_check").fetchall() == []
        assert (
            recovered.execute(
                "SELECT arquivo FROM representacao_documentos"
            ).fetchone()[0]
            == binary
        )
        assert (
            recovered.execute("SELECT arquivos FROM oficio_quarentena").fetchone()[0]
            == b""
        )
        recovered.execute(
            "INSERT INTO eventos(instante,acao,detalhes) VALUES(?,'after','{}')",
            (stamp,),
        )
        assert recovered.execute("SELECT max(id) FROM eventos").fetchone()[0] > 999
        recovered.rollback()
    # Exercise application reads after reopening, rather than just SQL INSERT success.
    reopened = Store(target)
    assert reopened.get(blobs["portaria_id"])["pdf"] == blobs["portaria_pdf"]
    from services.representacoes import get

    assert get(reopened, 501)["titulo"] == "Teste"
    from database.institutional_reports import InstitutionalReportsStore

    assert InstitutionalReportsStore(reopened).pdf_artifact(804)["conteudo"] == binary



def test_optional_legacy_alert_attention_is_backed_up_and_reconstructed(store, tmp_path):
    from database.legacy_alert_attention import ensure_backup_schema

    _prepare(store)
    ensure_backup_schema(store)
    stamp = "2026-10-09T12:00:00+00:00"
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO alertas_atencao(usuario_id,chave_alerta,lido_em,adiado_ate,criado_em,atualizado_em) VALUES(?,?,?,?,?,?)",
            (1, "v1:oficios:42:prazo", stamp, None, stamp, stamp),
        )
    path = _archive(store, tmp_path)
    with ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        schema = json.loads(archive.read("schema/schema_manifest.json"))
    assert "alertas_atencao" in manifest["tabelas"]
    assert [column["name"] for column in schema["tables"]["alertas_atencao"]["columns"]] == [
        "id", "usuario_id", "chave_alerta", "lido_em", "adiado_ate", "criado_em", "atualizado_em"
    ]
    restored = restore_isolated(
        path, _principal(store), tmp_path, confirmation=CONFIRMATION
    )
    with sqlite3.connect(restored["restore"]["destination"]) as recovered:
        row = recovered.execute(
            "SELECT usuario_id,chave_alerta,lido_em,adiado_ate FROM alertas_atencao"
        ).fetchone()
        assert row == (1, "v1:oficios:42:prazo", stamp, None)
        assert recovered.execute("PRAGMA foreign_key_check").fetchall() == []

def test_unknown_table_blocks_complete_backup(store, tmp_path):
    with store.connection() as c:
        c.execute("CREATE TABLE novo_modulo(id INTEGER PRIMARY KEY)")
    with pytest.raises(ValueError, match="não|Não|inventariadas"):
        _archive(store, tmp_path)
    assert not (tmp_path / "backup.zip").exists()


def test_missing_required_table_blocks_backup(store, tmp_path):
    with store.connection() as c:
        c.execute("DROP TABLE tarefas_checklist")
    with pytest.raises(ValueError, match="ausentes"):
        _archive(store, tmp_path)


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        False,
        0,
        1.25,
        2**60,
        Decimal("1.2300"),
        {"a": None, "b": "", "c": [1, False]},
        date(2026, 1, 1),
        datetime(2026, 1, 1, tzinfo=timezone.utc),
        time(12, 30),
        uuid.UUID(int=1),
        b"",
        b"\x00\xff",
    ],
)
def test_typed_codec(value):
    encoded = encode_cell(
        value, lambda data: {"arquivo": "x", "sha256": "x", "bytes": len(data)}
    )
    assert decode_cell(json.loads(json_bytes(encoded)), lambda ref: value) == value


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "hash",
        "traversal",
        "extra",
        "count",
        "schema",
        "relationships",
        "v1",
        "duplicate",
    ],
)
def test_invalid_incompatible_and_legacy(store, tmp_path, damage):
    path = _archive(store, tmp_path)

    def alter(entries):
        if damage == "missing":
            entries.pop("dados/portarias.jsonl")
        if damage == "hash":
            entries["dados/portarias.jsonl"] += b"[]\n"
        if damage == "traversal":
            entries["../outside"] = b"no"
        if damage == "extra":
            entries["unexpected.sql"] = b"DROP TABLE portarias"
        if damage == "count":
            manifest = json.loads(entries["manifest.json"])
            manifest["quantidade_registros"] += 1
            entries["manifest.json"] = json_bytes(manifest)
        if damage == "schema":
            schema = json.loads(entries["schema/schema_manifest.json"])
            schema["tables"]["portarias"]["columns"][0]["type"] = "EVIL"
            _rehash(entries, "schema/schema_manifest.json", schema)
        if damage == "relationships":
            table = "usuario_gabinetes"
            columns = json.loads(entries["schema/schema_manifest.json"])["tables"][
                table
            ]["columns"]
            row = [
                ["int", "999999"] if c["name"] == "usuario_id" else ["str", "Teste"]
                for c in columns
            ]
            name = f"dados/{table}.jsonl"
            entries[name] = json_bytes(row) + b"\n"
            manifest = json.loads(entries["manifest.json"])
            manifest["quantidade_registros"] += (
                1 - manifest["registros_por_tabela"][table]
            )
            manifest["registros_por_tabela"][table] = 1
            manifest["arquivos"][name] = {
                "bytes": len(entries[name]),
                "sha256": hashlib.sha256(entries[name]).hexdigest(),
            }
            entries["manifest.json"] = json_bytes(manifest)
        if damage == "v1":
            entries["manifest.json"] = b'{"formato":1}'

    damaged = _rewrite(path, tmp_path / "damaged.zip", alter)
    if damage == "duplicate":
        with ZipFile(damaged, "a") as a:
            a.writestr("manifest.json", b"{}")
    report = validate_backup(damaged, _principal(store))
    assert report["status"] == (
        {"v1": LEGACY, "schema": INCOMPATIBLE}.get(damage, INVALID)
    ), report


def test_failed_restore_leaves_no_published_database(store, tmp_path, monkeypatch):
    path = _archive(store, tmp_path)
    import services.restore as restore

    def failure(*args):
        raise ValueError("synthetic failure")

    monkeypatch.setattr(restore, "_row_digest", failure)
    with pytest.raises(ValueError):
        restore_isolated(path, _principal(store), tmp_path, confirmation=CONFIRMATION)
    assert not list(tmp_path.glob("mpc-recovery-*"))
    assert store.path.exists()


def test_restore_refuses_existing_database_urls_and_nonadmins(store, tmp_path):
    path = _archive(store, tmp_path)
    before = store.path.read_bytes()
    for destination in (store.path, "postgresql://production/database"):
        with pytest.raises((ValueError, OSError)):
            restore_isolated(
                path, _principal(store), destination, confirmation=CONFIRMATION
            )
    assert store.path.read_bytes() == before
    with pytest.raises(ValueError):
        restore_isolated(path, _principal(store), tmp_path, confirmation="")
    with pytest.raises(ValueError):
        validate_backup(path, {})
    with pytest.raises(ValueError):
        restore_isolated(path, {}, tmp_path, confirmation=CONFIRMATION)


@pytest.mark.parametrize(
    "damage",
    ["oversize", "expansion", "unexpected_object", "binary_discarded", "postgres"],
)
def test_resource_limits_types_and_postgres_block(store, tmp_path, monkeypatch, damage):
    import services.restore as recovery

    _prepare(store)
    _insert_blobs(store)
    path = _archive(store, tmp_path)
    if damage == "oversize":
        monkeypatch.setattr(recovery, "MAX_ARCHIVE", 1)

    def alter(entries):
        if damage == "unexpected_object":
            manifest = json.loads(entries["manifest.json"])
            manifest["engine"] = 12
            entries["manifest.json"] = json_bytes(manifest)
        if damage == "binary_discarded":
            name = "dados/portarias.jsonl"
            schema = json.loads(entries["schema/schema_manifest.json"])
            columns = [c["name"] for c in schema["tables"]["portarias"]["columns"]]
            rows = [json.loads(line) for line in entries[name].splitlines()]
            rows[0][columns.index("docx")] = ["str", ""]
            entries[name] = b"".join(json_bytes(row) + b"\n" for row in rows)
            manifest = json.loads(entries["manifest.json"])
            manifest["arquivos"][name] = {
                "bytes": len(entries[name]),
                "sha256": hashlib.sha256(entries[name]).hexdigest(),
            }
            entries["manifest.json"] = json_bytes(manifest)
        if damage == "expansion":
            entries["huge.txt"] = b"0" * 100000

    damaged = _rewrite(path, tmp_path / "limits.zip", alter)
    if damage == "postgres":
        # Verify the destination guard directly, without any PostgreSQL connection.
        with ZipFile(path) as archive:
            manifest, schema = recovery._inspect(archive)
            schema["engine"] = "postgresql"
            with pytest.raises(recovery.Incompatible):
                recovery._reconstruct(archive, manifest, schema, tmp_path)
        assert not (tmp_path / "restored.sqlite").exists()
    else:
        assert validate_backup(damaged, _principal(store))["status"] == INVALID


def test_entire_initialized_module_missing_is_not_marked_unused():
    from database.inventory import (
        check_backup_coverage,
        ESSENTIAL_TABLES,
        OFICIOS_TABLES,
    )

    present = set(ESSENTIAL_TABLES) - set(OFICIOS_TABLES)
    with pytest.raises(ValueError, match="ausentes"):
        check_backup_coverage("sqlite", present, ["oficios_schema_v4"])


_UI_CONTEXT = {}


def test_validation_preview_does_not_write_operational_database(
    store, tmp_path, monkeypatch
):
    import io
    import streamlit as st
    from streamlit.testing.v1 import AppTest

    path = _archive(store, tmp_path)
    upload = io.BytesIO(path.read_bytes())
    upload.file_id = "synthetic-upload"
    upload.size = len(upload.getbuffer())
    monkeypatch.setattr(st, "file_uploader", lambda *args, **kwargs: upload)
    _UI_CONTEXT.update(store=store, principal=_principal(store))

    def page():
        from tests.test_backup_v2 import _UI_CONTEXT
        from services.system_ui import _render_backup_validation

        _render_backup_validation(_UI_CONTEXT["store"], _UI_CONTEXT["principal"])

    app = AppTest.from_function(page, default_timeout=30).run()
    with store.connection(read_only=True) as c:
        before = c.execute("SELECT COUNT(*) FROM auditoria_eventos").fetchone()[0]
    app.button(key="backup_validate").click().run()
    assert not app.exception
    assert app.session_state["backup_validation"]["status"] == VALID
    with store.connection(read_only=True) as c:
        assert (
            c.execute("SELECT COUNT(*) FROM auditoria_eventos").fetchone()[0] == before
        )
    app.button(key="backup_validation_audit").click().run()
    assert not app.exception
    with store.connection(read_only=True) as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM auditoria_eventos WHERE evento='BACKUP_VALIDADO'"
            ).fetchone()[0]
            == 1
        )
