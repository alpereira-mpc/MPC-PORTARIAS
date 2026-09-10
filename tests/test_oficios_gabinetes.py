from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import base64
import json

from docx import Document
import pytest

from database.oficios import OficiosStore
from document_generator.oficios import generate
from services.oficios import GABINETES, SERIES, fingerprint
from tests.test_oficios import ready, sample, files
from tests.test_postgresql import pg_url, pg_store


def test_future_own_template_replaces_fallback(tmp_path, monkeypatch):
    import document_generator.oficios as generator

    folder = tmp_path / "templates" / "oficios"
    folder.mkdir(parents=True)
    monkeypatch.setattr(generator, "ROOT", tmp_path)
    config = {"sigla": "LAF", "modelo": "PROGE"}
    assert generator.template_path(config) == folder / "PROGE.docx"
    (folder / "LAF.docx").write_bytes(b"future package")
    assert generator.template_path(config) == folder / "LAF.docx"


def test_seven_offices_and_templates(store):
    service = ready(store)
    configs = {s["sigla"]: s for s in service.series()}
    assert set(configs) == set(GABINETES)
    people = {p["id"]: p for p in store.catalog("procuradores")}
    for name, rule in SERIES.items():
        config = configs[rule[0]]
        assert config["nome"] == name
        assert config["modelo"] == ("BTLC" if rule[0] == "BTLC" else "PROGE")
        record = {
            **sample(service, config["membro_id"]),
            "signatario": name,
            "cargo_base": people[config["membro_id"]]["cargo_base"],
            "corpo": "Primeiro.\nSegundo.\n\nTerceiro.",
            "referencia": "Processo 123",
        }
        doc = Document(BytesIO(generate(record, config, 1)))
        paragraphs = [p.text for p in doc.paragraphs]
        text = "\n".join(paragraphs)
        assert rule[0] in text and name.upper() in text
        assert all(p in paragraphs for p in ("Primeiro.", "Segundo.", "Terceiro."))
        assert "Processo 123" in text and "BLTC" not in text
        if rule[0] != "PROGE":
            assert "PROGE" not in text
        assert record["cargo_base"] in text


def test_isolation_and_overview(store):
    service = ready(store)
    configs = service.series()
    for config in configs:
        member = config["membro_id"]
        service.save(sample(service, member))
        service.save(
            dict(
                direcao="RECEBIDO",
                data="2026-09-10",
                data_recebimento="2026-09-10",
                numero_externo="1",
                remetente="Teste",
                instituicao="MPC",
                assunto=config["sigla"],
                membros=[member],
            )
        )
    for config in configs:
        rows = service.list(member=config["membro_id"])
        assert len(rows) == 2
        assert service.overview(2026, config["membro_id"])["recebidos"] == 1
        assert (
            service.list(direction="ENVIADO", member=config["membro_id"])[0]["serie"]
            == config["sigla"]
        )


def release_contract(store):
    service = ready(store)
    identifiers = [service.save(sample(service)) for _ in range(2)]
    for identifier in identifiers:
        service.finalize(identifier, files)
    original_file = service.files(identifiers[0])[0]["id"]
    original = service.download(original_file)
    for args in [
        ("", True, "EXCLUIR", True),
        ("Teste", False, "EXCLUIR", True),
        ("Teste", True, "excluir", True),
        ("Teste", True, "EXCLUIR", False),
    ]:
        with pytest.raises(ValueError):
            service.delete_generated(identifiers[0], *args)
    service.delete_generated(identifiers[0], "Erro de emissão", True, "EXCLUIR", True)
    assert service.get(identifiers[1])["numero"] == 9
    assert service.sequence("PROGE", 2026)["proximo"] == 8
    with store.connection(read_only=True) as c:
        quarantine = c.execute(
            "SELECT dados,arquivos FROM oficio_quarentena"
        ).fetchone()
        audit = json.loads(quarantine["dados"])
        assert audit["numero_liberado"] and audit["numero"] == 8 and audit["signatario"]
        archived = json.loads(bytes(quarantine["arquivos"]))
        assert (
            base64.b64decode(
                next(f["conteudo"] for f in archived if f["id"] == original_file)
            )
            == original
        )
    with pytest.raises(ValueError):
        service.delete_generated(identifiers[0], "Repetido", True, "EXCLUIR", True)
    draft = service.save(sample(service))

    def fail(*args):
        raise RuntimeError("Falha PDF")

    with pytest.raises(RuntimeError):
        service.finalize(draft, fail)
    assert service.sequence("PROGE", 2026)["proximo"] == 8
    assert not service.files(draft)
    assert service.finalize(draft, files)["numero"] == 8
    assert service.sequence("PROGE", 2026)["proximo"] == 10


def test_release_sqlite(store):
    release_contract(store)


def test_release_postgresql(pg_store):
    release_contract(pg_store)


def test_quarantine_failure_rolls_back(store, monkeypatch):
    service = ready(store)
    identifier = service.save(sample(service))
    service.finalize(identifier, files)
    original_files = service.files(identifier)

    def fail(*args):
        raise RuntimeError("Auditoria indisponível")

    monkeypatch.setattr(store, "event", fail)
    with pytest.raises(RuntimeError):
        service.delete_generated(identifier, "Erro", True, "EXCLUIR", True)
    assert service.get(identifier)["numero"] == 8
    assert service.files(identifier) == original_files
    assert service.sequence("PROGE", 2026)["proximo"] == 9
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM oficio_quarentena").fetchone()[0] == 0


def test_delete_preserves_received_link_target(store):
    service = ready(store)
    member = sample(service)["membro_id"]
    received = service.save(
        dict(
            direcao="RECEBIDO",
            data="2026-09-10",
            data_recebimento="2026-09-10",
            numero_externo="1",
            remetente="Teste",
            instituicao="MPC",
            assunto="Recebido",
            membros=[member],
        )
    )
    identifier = service.save({**sample(service), "responde_a": received})
    service.finalize(identifier, files)
    service.delete_generated(identifier, "Erro", True, "EXCLUIR", True)
    assert service.get(received)["status"] == "Recebido"
    assert not service.list(related=received)
    with store.connection(read_only=True) as c:
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_delete_received_and_attachments(store):
    from pypdf import PdfWriter

    service = ready(store)
    member = sample(service)["membro_id"]
    sequence = service.sequence("PROGE", 2026)
    sent = service.save(sample(service))
    service.finalize(sent, files)
    pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.write(pdf)
    received = service.save(
        dict(
            direcao="RECEBIDO",
            data="2026-09-10",
            data_recebimento="2026-09-10",
            numero_externo="12/2026",
            remetente="Pessoa",
            instituicao="Órgão",
            assunto="Original",
            membros=[member],
        ),
        uploads=[("original.pdf", pdf.getvalue())],
    )
    with pytest.raises(ValueError, match="ciência"):
        service.delete_received(received, False, "EXCLUIR")
    with pytest.raises(ValueError, match="EXCLUIR"):
        service.delete_received(received, True, "excluir")
    service.delete_received(received, True, "EXCLUIR")
    with pytest.raises(ValueError):
        service.get(received)
    with store.connection(read_only=True) as c:
        assert not c.execute(
            "SELECT 1 FROM oficio_arquivos WHERE oficio_id=?", (received,)
        ).fetchone()
        assert not c.execute(
            "SELECT 1 FROM oficio_movimentacoes WHERE oficio_id=?", (received,)
        ).fetchone()
        assert not c.execute(
            "SELECT 1 FROM oficio_destinatarios WHERE oficio_id=?", (received,)
        ).fetchone()
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    assert service.get(sent)["numero"] == 8
    assert service.sequence("PROGE", 2026) == {
        "proximo": sequence["proximo"] + 1,
        "confirmada": True,
    }


def test_delete_received_blocked_when_linked_sent(store):
    service = ready(store)
    member = sample(service)["membro_id"]
    received = service.save(
        dict(
            direcao="RECEBIDO",
            data="2026-09-10",
            data_recebimento="2026-09-10",
            numero_externo="1",
            remetente="Teste",
            instituicao="MPC",
            assunto="Recebido",
            membros=[member],
        )
    )
    reply = service.save({**sample(service), "responde_a": received})
    with pytest.raises(ValueError, match="enviado relacionado"):
        service.delete_received(received, True, "EXCLUIR")
    assert service.get(received)["id"] == received
    service.finalize(reply, files)
    with pytest.raises(ValueError, match="enviado relacionado"):
        service.delete_received(received, True, "EXCLUIR")
    assert service.get(reply)["numero"] == 8
    assert service.sequence("PROGE", 2026)["proximo"] == 9


def test_reviewed_finalization_requires_matching_preview(store, monkeypatch):
    service = ready(store)
    record = sample(service)
    config = next(s for s in service.series() if s["sigla"] == "PROGE")
    member = next(
        p for p in store.catalog("procuradores") if p["id"] == record["membro_id"]
    )
    document = {
        **record,
        "signatario": member["nome"],
        "cargo_base": member["cargo_base"],
    }
    identifier = service.save(record)
    for receipt in (None, "old-hash"):
        with pytest.raises(ValueError):
            service.finalize_reviewed(identifier, document, config, receipt)
    receipt = fingerprint(document, config, ["PROGE", identifier])
    monkeypatch.setattr("document_generator.oficios.official_documents", files)
    assert (
        service.finalize_reviewed(identifier, document, config, receipt)["numero"] == 8
    )
    assert (
        service.finalize_reviewed(identifier, document, config, receipt)["numero"] == 8
    )
    assert service.sequence("PROGE", 2026)["proximo"] == 9


@pytest.mark.parametrize(
    "status", ["Enviado", "Aguardando resposta", "Respondido", "Concluído", "Cancelado"]
)
def test_sent_never_releases(store, status):
    service = ready(store)
    identifier = service.save(sample(service))
    service.finalize(identifier, files)
    service.update_status(identifier, status, "Motivo", sent="2026-09-10")
    with pytest.raises(ValueError):
        service.delete_generated(identifier, "Erro", True, "EXCLUIR", True)
    assert service.get(identifier)["numero"] == 8
    assert service.sequence("PROGE", 2026)["proximo"] == 9


def concurrent_release(store):
    service = ready(store)
    ids = [service.save(sample(service)) for _ in range(3)]
    for identifier in ids:
        service.finalize(identifier, files)
    for identifier in ids[:2]:
        service.delete_generated(identifier, "Erro", True, "EXCLUIR", True)
    drafts = [service.save(sample(service)) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        numbers = list(
            pool.map(
                lambda identifier: service.finalize(identifier, files)["numero"], drafts
            )
        )
    assert sorted(numbers) == [8, 9, 11, 12]
    assert service.get(ids[2])["numero"] == 10


def test_concurrent_release_sqlite(store):
    concurrent_release(store)


def test_concurrent_release_postgresql(pg_store):
    concurrent_release(pg_store)


def test_preview_fingerprint_and_stale_draft(store):
    service = ready(store)
    record = sample(service)
    config = next(s for s in service.series() if s["sigla"] == "PROGE")
    previous = fingerprint(record, config, "draft")
    for field in record:
        assert fingerprint({**record, field: "Changed"}, config, "draft") != previous
    assert fingerprint(record, config, "other") != previous
    identifier = service.save(record)
    service.save({**record, "assunto": "Alterado"}, identifier)
    with pytest.raises(ValueError, match="alterado"):
        service.finalize(
            identifier, files, expected_record=record, expected_series=config
        )
    assert service.sequence("PROGE", 2026)["proximo"] == 8


def test_upgrade_is_additive_and_idempotent(store):
    service = ready(store)
    identifier = service.save(sample(service))
    service.finalize(identifier, files)
    before = service.get(identifier)
    snapshots = [service.download(f["id"]) for f in service.files(identifier)]
    with store.connection() as c:
        c.execute("DELETE FROM configuracoes WHERE chave='oficios_schema_v2'")
        c.execute(
            "UPDATE oficio_series SET modelo='',cabecalho='',digitos=0 WHERE sigla='LAF'"
        )
    for _ in range(2):
        service = OficiosStore(store)
    assert service.get(identifier) == before
    assert snapshots == [service.download(f["id"]) for f in service.files(identifier)]
    assert next(s for s in service.series() if s["sigla"] == "LAF")["modelo"] == "PROGE"


def test_preview_and_switch_ui(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT

    service = ready(store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    converted = []

    def convert(content):
        converted.append(content)
        return b"%PDF-test", "test"

    monkeypatch.setattr("document_generator.pdf.convert", convert)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_oficios").click().run()
    assert len([b for b in app.button if b.key and b.key.startswith("gabinete_")]) == 7
    app.button(key="gabinete_PROGE").click().run()
    app.radio(key="oficio_page").set_value("Novo Ofício").run()
    app.text_input(key="oficio_input_assunto").set_value("Teste")
    app.text_input(key="oficio_input_destinatario").set_value("Pessoa")
    app.text_area(key="oficio_input_corpo").set_value("Corpo")

    def button(label):
        return next(b for b in app.button if b.label == label)

    assert button("Finalizar e gerar ofício").disabled
    button("Pré-visualizar PDF").click().run()
    assert not app.exception and not app.error
    assert not button("Finalizar e gerar ofício").disabled
    assert converted[-1] == app.session_state["oficio_preview"]["docx"]
    assert not service.list() and service.sequence("PROGE", 2026)["proximo"] == 8
    app.text_input(key="oficio_input_assunto").set_value("Novo assunto").run()
    assert button("Finalizar e gerar ofício").disabled
    assert len(converted) == 1
    button("Pré-visualizar DOCX").click().run()
    assert not button("Finalizar e gerar ofício").disabled
    button("← Trocar gabinete").click().run()
    app.button(key="gabinete_LAF").click().run()
    app.radio(key="oficio_page").set_value("Novo Ofício").run()
    assert button("Finalizar e gerar ofício").disabled
    assert app.text_input(key="oficio_input_assunto").value == ""
    assert not app.exception and not app.error
