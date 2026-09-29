from io import BytesIO

import pytest
from pypdf import PdfWriter

from database.store import now
from scripts.backfill_representacoes_2026 import (
    BACKFILL_MAX_FILE,
    SOURCES,
    run,
    validate_backfill_pdf,
)
from services.oficios import MAX_FILE


def _pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    data = BytesIO()
    writer.write(data)
    return data.getvalue()


def _sources(tmp_path):
    for source in SOURCES:
        (tmp_path / source.arquivo).write_bytes(_pdf())


def _sized_pdf(size):
    content = _pdf()
    return content + b"\0" * (size - len(content))


def test_dry_run_does_not_write_and_apply_is_idempotent(store, tmp_path, monkeypatch):
    _sources(tmp_path)
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM representacoes").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM representacao_documentos").fetchone()[0] == 0
    planned = run(store, tmp_path)
    assert {row[4] for row in planned} == {"CREATE"}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("O backfill não pode chamar serviço externo.")

    monkeypatch.setattr("services.notifications.confirm_send", forbidden)
    monkeypatch.setattr("services.email_transport.institutional_transport", forbidden)
    monkeypatch.setattr("services.ai_service.resumir_documento_pdf", forbidden)
    run(store, tmp_path, apply=True, actor="admin@test.local")
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM representacoes").fetchone()[0] == 8
        assert not c.execute(
            "SELECT 1 FROM representacoes WHERE numero_processo='04124/26'"
        ).fetchone()
        first = c.execute(
            "SELECT id FROM representacoes WHERE numero_processo='00534/26'"
        ).fetchone()[0]
        assert c.execute(
            "SELECT COUNT(*) FROM representacao_integrantes WHERE representacao_id=?",
            (first,),
        ).fetchone()[0] == 2
        assert c.execute(
            "SELECT COUNT(*) FROM representacao_documentos WHERE representacao_id=?",
            (first,),
        ).fetchone()[0] == 1
        names = {
            row[0]
            for row in c.execute(
                "SELECT p.nome FROM procuradores p JOIN representacao_integrantes i "
                "ON i.membro_id=p.id WHERE i.representacao_id=?",
                (first,),
            )
        }
        assert names == {"Luciano Andrade Farias", "Manoel Antônio dos Santos Neto"}
    assert len(run(store, tmp_path)) == 8
    assert {row[4] for row in run(store, tmp_path)} == {"SKIP"}
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM representacao_documentos").fetchone()[0] == 8


def test_04022_uses_only_the_bounded_exception(store, tmp_path):
    _sources(tmp_path)
    exceptional = next(source for source in SOURCES if source.numero == "04022/26")
    assert MAX_FILE == 10 * 1024 * 1024
    content = _sized_pdf(20_500_907)
    (tmp_path / exceptional.arquivo).write_bytes(content)
    planned = run(store, tmp_path)
    row = next(item for item in planned if item[0].numero == "04022/26")
    assert row[4] == "CREATE"
    assert validate_backfill_pdf(exceptional, content) == (
        exceptional.arquivo,
        "application/pdf",
    )
    other = next(source for source in SOURCES if source.numero == "00534/26")
    with pytest.raises(ValueError, match="limite excepcional"):
        validate_backfill_pdf(other, _sized_pdf(MAX_FILE + 1))
    with pytest.raises(ValueError, match="limite excepcional"):
        validate_backfill_pdf(exceptional, _sized_pdf(BACKFILL_MAX_FILE + 1))


def test_conflicting_existing_process_is_never_overwritten(store, tmp_path):
    _sources(tmp_path)
    source = SOURCES[0]
    stamp = now()
    with store.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute(
            "INSERT INTO representacoes(titulo,objeto,origem,data_abertura,representado,tema,prioridade,situacao,fase_processual,numero_processo,data_protocolo,relator,observacoes,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("Divergente", "", "DE_OFICIO", "2026-01-01", "Outro", "", "NORMAL", "PROTOCOLADA", "INSTRUCAO", source.numero, "2026-01-01", source.relator, "", stamp, "teste", stamp, "teste"),
        )
    planned = run(store, tmp_path)
    row = next(item for item in planned if item[0].numero == source.numero)
    assert row[4] == "CONFLICT"

    with pytest.raises(ValueError, match="Apply bloqueado"):
        run(store, tmp_path, apply=True, actor="admin@test.local")
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT titulo FROM representacoes WHERE numero_processo=?", (source.numero,)).fetchone()[0] == "Divergente"
