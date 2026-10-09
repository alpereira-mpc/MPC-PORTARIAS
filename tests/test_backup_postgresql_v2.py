"""End-to-end PostgreSQL V2 recovery checks for the disposable GitHub Actions service."""

from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile
import hashlib
import json
import uuid

import pytest

from database.peticoes import PeticoesStore
from database.store import Store
from services.backup import generate_backup
from services.backup_format import decode_cell, quoted, table_schema
from services.restore import (
    Incompatible,
    PG_TEST_MODE_ENV,
    PG_TEST_URL_ENV,
    _row_digest,
    restore_postgresql_disposable_test,
)
from tests.access_testing import seed_access
from tests.test_backup import _insert_blobs, _prepare, _principal
from tests.test_postgresql import pg_url  # noqa: F401


def _store(url, tmp_path):
    store = Store(database_url=url, postgres_schema="mpc_test_" + uuid.uuid4().hex)
    store.path = tmp_path / store._postgres.schema
    store.configure(export_dir=str(tmp_path / "exports"))
    seed_access(store)
    _prepare(store)
    PeticoesStore(store)
    return store


@pytest.fixture
def pg_pair(pg_url, tmp_path, monkeypatch):
    monkeypatch.setenv(PG_TEST_MODE_ENV, "1")
    return _store(pg_url, tmp_path), _store(pg_url, tmp_path)


def _seed_all_types(store):
    blobs = _insert_blobs(store)
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
        c.execute("INSERT INTO oficio_quarentena VALUES('empty',?,'teste','{}',?)", (stamp, b""))
        c.execute("INSERT INTO eventos(id,instante,acao,detalhes) VALUES(999,?,'teste','{\"nullable\":null}')", (stamp,))
        c.execute("SELECT setval(pg_get_serial_sequence('eventos','id'), 999, true)")
        c.execute("INSERT INTO tarefas(id,owner_user_id,titulo,criado_em,atualizado_em) VALUES(905,1,'Teste',?,?)", (stamp, stamp))
        c.execute("INSERT INTO tarefas_checklist(tarefa_id,texto,ordem,criado_em,atualizado_em) VALUES(905,'Item',1,?,?)", (stamp, stamp))
    return blobs, binary


def _archive(store, tmp_path):
    path = tmp_path / "postgres-v2.zip"
    generate_backup(store, _principal(store), path)
    return path


def _archive_rows(path, table):
    with ZipFile(path) as archive:
        schema = json.loads(archive.read("schema/schema_manifest.json"))
        rows = []
        for line in archive.read("dados/" + table + ".jsonl").splitlines():
            rows.append([decode_cell(value, lambda ref: archive.read(ref["arquivo"])) for value in json.loads(line)])
    return schema, rows


def test_postgresql_v2_roundtrip_data_documents_sequences_and_reads(pg_pair, tmp_path):
    source, target = pg_pair
    blobs, binary = _seed_all_types(source)
    path = _archive(source, tmp_path)
    restored = restore_postgresql_disposable_test(path, _principal(source), source, target)
    assert restored["status"] == "Válido para restauração"
    details = restored["restore"]
    assert details["source_schema"] != details["target_schema"]
    assert details["functional_reads"]["portaria"] == "ok"
    assert details["functional_reads"]["representacao"] == "ok"
    assert details["functional_reads"]["relatorio_institucional"] == "ok"
    for table in details["counts"]:
        schema, expected = _archive_rows(path, table)
        columns = [item["name"] for item in schema["tables"][table]["columns"]]
        with target.connection(read_only=True) as c:
            actual = [tuple(row) for row in c.execute("SELECT " + ",".join(map(quoted, columns)) + " FROM " + quoted(table))]
        assert Counter(_row_digest(row) for row in actual) == Counter(_row_digest(row) for row in expected), table
    with target.connection() as c:
        assert c.execute("SELECT arquivo FROM representacao_documentos WHERE id='rep-doc'").fetchone()[0] == binary
        assert c.execute("SELECT arquivos FROM oficio_quarentena WHERE id='empty'").fetchone()[0] == b""
        assert c.execute("SELECT snapshot_dados->>'null' FROM relatorios_institucionais WHERE id=804").fetchone()[0] is None
        c.execute("INSERT INTO eventos(instante,acao,detalhes) VALUES('2026-10-10T00:00:00+00:00','after','{}')")
        assert c.execute("SELECT max(id) FROM eventos").fetchone()[0] > 999
        c.rollback()
    assert target.get(blobs["portaria_id"])["pdf"] == blobs["portaria_pdf"]


def test_postgresql_v2_failure_rolls_back_and_guards_operational_targets(pg_pair, tmp_path, monkeypatch):
    source, target = pg_pair
    _seed_all_types(source)
    path = _archive(source, tmp_path)
    with target.connection() as c:
        c.execute("INSERT INTO configuracoes(chave,valor) VALUES('restore_sentinel','preservar')")
    import services.restore as restore_module

    original = restore_module._row_digest
    calls = {"count": 0}
    def fail_after_insert(values):
        calls["count"] += 1
        if calls["count"] > 1:
            raise RuntimeError("falha sintética")
        return original(values)
    monkeypatch.setattr(restore_module, "_row_digest", fail_after_insert)
    with pytest.raises(RuntimeError, match="falha sintética"):
        restore_postgresql_disposable_test(path, _principal(source), source, target)
    with target.connection(read_only=True) as c:
        assert c.execute("SELECT valor FROM configuracoes WHERE chave='restore_sentinel'").fetchone()[0] == "preservar"
    monkeypatch.setattr(restore_module, "_row_digest", original)
    unsafe = SimpleNamespace(
        backend="postgresql",
        _postgres=SimpleNamespace(schema="mpc_portarias", _options=source._postgres._options),
        connection=source.connection,
    )
    with pytest.raises(Incompatible, match="schema descartável"):
        restore_postgresql_disposable_test(path, _principal(source), source, unsafe)
    monkeypatch.setenv(PG_TEST_URL_ENV, "postgresql://postgres@production.invalid/production")
    with pytest.raises(Incompatible, match="loopback"):
        restore_postgresql_disposable_test(path, _principal(source), source, target)