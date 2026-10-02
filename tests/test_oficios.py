from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from io import BytesIO
from pathlib import Path
import sqlite3
import pytest
from docx import Document
from database.oficios import OficiosStore
from database.store import Store
from document_generator.oficios import generate
from services.oficios import attention, validate_upload, safe_name
from tests.test_postgresql import pg_url, pg_store


def sample(service, member=None, year=2026):
    series = service.series()
    member = member or next(s["membro_id"] for s in series if s["sigla"] == "PROGE")
    return dict(
        direcao="ENVIADO",
        membro_id=member,
        data=f"{year}-09-10",
        assunto="Solicitação de informações",
        destinatario="DESTINATÁRIO TESTE",
        cargo="Promotor de Justiça",
        instituicao="Instituição de teste",
        tratamento="A Sua Excelência o Senhor",
        vocativo="Excelentíssimo Senhor Promotor de Justiça,",
        corpo="Primeiro parágrafo.\n\nSegundo parágrafo.",
        fechamento="Atenciosamente,",
    )


def files(record, series, number):
    return [
        ("docx", "teste.docx", generate(record, series, number)),
        ("pdf", "teste.pdf", b"%PDF-test"),
    ]


def ready(store):
    service = OficiosStore(store)
    service.confirm_sequence("PROGE", 2026, 8, True)
    service.confirm_sequence("BTLC", 2026, 2, True)
    return service


def test_number_lifecycle(store):
    s = ready(store)
    r = sample(s)
    identifier = s.save(r)
    assert s.sequence("PROGE", 2026)["proximo"] == 8
    series = next(x for x in s.series() if x["sigla"] == "PROGE")
    assert (
        b"PK"
        == generate({**r, "signatario": "Teste", "cargo_base": "Procuradora"}, series)[
            :2
        ]
    )
    assert s.sequence("PROGE", 2026)["proximo"] == 8
    result = s.finalize(identifier, files)
    assert result["numero"] == 8 and result["status"] == "Gerado"
    assert s.finalize(identifier, files)["numero"] == 8
    assert s.sequence("PROGE", 2026)["proximo"] == 9
    s.update_status(identifier, "Cancelado", "Teste cancelamento")
    assert s.sequence("PROGE", 2026)["proximo"] == 9
    with pytest.raises(ValueError):
        s.delete_draft(identifier, True)
    with pytest.raises(ValueError):
        s.update_status(identifier, "Enviado", sent="2026-09-10")
    assert s.get(identifier)["numero"] == 8


def test_response_tracking_persists_metadata_and_excludes_closed(store):
    s = ready(store)
    identifier = s.save(sample(s))
    s.finalize(identifier, files)
    s.set_response_tracking(
        identifier,
        True,
        "2026-10-08",
        quantidade=5,
        tipo="DIAS_UTEIS",
        inicio="2026-10-01",
    )
    tracked = s.get(identifier)
    assert tracked["aguarda_resposta"] is True
    assert tracked["data_esperada_resposta"] == "2026-10-08"
    assert tracked["prazo_resposta_quantidade"] == 5
    assert tracked["prazo_resposta_tipo"] == "DIAS_UTEIS"
    s.update_status(identifier, "Concluído", "Encerrado")
    assert s.overview(2026)["aguardando_resposta"] == 0
    s.set_response_tracking(identifier, False)
    assert s.get(identifier)["data_esperada_resposta"] is None


def test_manual_response_adjustment_requires_reason(store):
    s = ready(store)
    identifier = s.save(sample(s))
    s.finalize(identifier, files)
    with pytest.raises(ValueError, match="motivo"):
        s.set_response_tracking(
            identifier, True, "2026-10-08", quantidade=5,
            tipo="DIAS_UTEIS", inicio="2026-10-01", manual=True,
        )


def test_draft_preserves_response_tracking_metadata(store):
    s = ready(store)
    identifier = s.save(
        {
            **sample(s),
            "aguarda_resposta": True,
            "data_esperada_resposta": "2026-10-08",
            "prazo_resposta_quantidade": 5,
            "prazo_resposta_tipo": "DIAS_UTEIS",
            "prazo_resposta_inicio": "2026-10-01",
        }
    )
    record = s.get(identifier)
    assert record["data_esperada_resposta"] == "2026-10-08"
    assert record["prazo_resposta_inicio"] == "2026-10-01"


def test_v5_migrates_legacy_response_columns_before_listing(store):
    service = ready(store)
    identifier = service.save(sample(service))
    with store.connection() as c:
        c.execute("DELETE FROM configuracoes WHERE chave='oficios_schema_v5'")
        for column in (
            "prazo_resposta_quantidade",
            "prazo_resposta_tipo",
            "prazo_resposta_inicio",
            "prazo_resposta_manual",
            "prazo_resposta_motivo_ajuste",
        ):
            c.execute("ALTER TABLE oficios DROP COLUMN " + column)
    upgraded = OficiosStore(store)
    with store.connection(read_only=True) as c:
        columns = {row[1] for row in c.execute("PRAGMA table_info(oficios)")}
    assert "prazo_resposta_quantidade" in columns
    assert "prazo_resposta_motivo_ajuste" in columns
    assert upgraded.list()[0]["id"] == identifier
    assert upgraded.overview(2026)["enviados"] == 0


def test_administrative_edit_preserves_identification_and_files(store):
    s = ready(store)
    identifier = s.save(sample(s))
    final = s.finalize(identifier, files)
    before_files = s.files(identifier)
    with pytest.raises(ValueError, match="não autorizado"):
        s.edit_finalized(identifier, {"assunto": "Novo"}, administrator=False)
    result = s.edit_finalized(
        identifier,
        {"assunto": "Assunto administrativo", "serie": "BTLC", "ano": 2030},
        administrator=True,
        actor_email="admin@test.local",
    )
    updated = result["record"]
    assert set(result["changed"]) == {"assunto"}
    assert updated["assunto"] == "Assunto administrativo"
    assert (updated["serie"], updated["ano"], updated["numero"]) == (
        final["serie"], final["ano"], final["numero"]
    )
    assert s.files(identifier) == before_files


def test_administrative_final_delete_requires_permission_and_keeps_used_number(store):
    s = ready(store)
    identifier = s.save(sample(s))
    final = s.finalize(identifier, files)
    s.update_status(identifier, "Enviado", "Envio oficial", sent="2026-09-10")
    reference = f"{final['serie']} {final['numero']}/{final['ano']}"
    with pytest.raises(ValueError, match="não autorizado"):
        s.delete_finalized(identifier, "Motivo", reference, administrator=False)
    with pytest.raises(ValueError, match="identificação"):
        s.delete_finalized(identifier, "Motivo", "EXCLUIR", administrator=True)
    snapshot = s.delete_finalized(
        identifier, "Motivo administrativo", reference,
        administrator=True, actor_email="admin@test.local",
    )
    assert snapshot["usuario"] == "admin@test.local"
    with pytest.raises(ValueError, match="não encontrado"):
        s.get(identifier)
    assert s.sequence("PROGE", 2026)["proximo"] == 9
    with s.store.connection(read_only=True) as c:
        assert c.execute("SELECT 1 FROM oficio_numeros_liberados WHERE numero=?", (final["numero"],)).fetchone() is None
        event = c.execute("SELECT detalhes FROM eventos WHERE acao='oficio_excluir_definitivamente'").fetchone()
    assert event is not None


def test_administrative_delete_blocks_received_with_linked_response(store):
    s = ready(store)
    member = s.series()[0]["membro_id"]
    received = s.save(
        {
            "direcao": "RECEBIDO", "numero_externo": "88/2026", "remetente": "Pessoa",
            "instituicao": "Órgão", "data": "2026-09-01", "data_recebimento": "2026-09-01",
            "membros": [member], "assunto": "Resposta recebida",
        }
    )
    sent = s.save({**sample(s), "responde_a": received})
    final = s.finalize(sent, files)
    reference = "88/2026"
    with pytest.raises(ValueError, match="vinculado como resposta"):
        s.delete_finalized(received, "Motivo", reference, administrator=True)
    assert s.get(sent)["id"] == final["id"]


def test_administrative_delete_keeps_generated_quarantine(store):
    s = ready(store)
    identifier = s.save(sample(s))
    final = s.finalize(identifier, files)
    reference = f"{final['serie']} {final['numero']}/{final['ano']}"
    result = s.delete_finalized(
        identifier, "Falha antes do envio", reference,
        administrator=True, actor_email="admin@test.local",
    )
    assert result["quarentena"] and result["numero_liberado"]
    with s.store.connection(read_only=True) as c:
        assert c.execute("SELECT 1 FROM oficio_quarentena").fetchone() is not None
        assert c.execute("SELECT 1 FROM oficio_numeros_liberados WHERE numero=?", (final["numero"],)).fetchone()


def test_independent_series_years(store):
    s = ready(store)
    for code, year, expected in [
        ("PROGE", 2026, 8),
        ("BTLC", 2026, 2),
        ("PROGE", 2027, 1),
    ]:
        member = next(x["membro_id"] for x in s.series() if x["sigla"] == code)
        if year == 2027:
            s.confirm_sequence(code, year, 1, True)
        assert s.finalize(s.save(sample(s, member, year)), files)["numero"] == expected


def test_generator_failure_rolls_back(store):
    s = ready(store)
    identifier = s.save(sample(s))

    def fail(*args):
        raise RuntimeError("conversion failure")

    with pytest.raises(RuntimeError):
        s.finalize(identifier, fail)
    assert s.get(identifier)["status"] == "Rascunho"
    assert s.sequence("PROGE", 2026)["proximo"] == 8
    assert not s.files(identifier)


@pytest.mark.parametrize("same", [False, True])
def test_concurrent_sqlite(store, same):
    concurrent(store, same)


def concurrent(store, same):
    s = ready(store)
    ids = [s.save(sample(s)) for _ in range(4)]
    if same:
        ids = [ids[0]] * 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda x: s.finalize(x, files)["numero"], ids))
    assert sorted(results) == ([8] * 4 if same else [8, 9, 10, 11])
    assert s.sequence("PROGE", 2026)["proximo"] == (9 if same else 12)


@pytest.mark.parametrize("same", [False, True])
def test_concurrent_postgresql(pg_store, same):
    concurrent(pg_store, same)


def test_postgresql_control(pg_store):
    control(pg_store)


def test_sqlite_control(store):
    control(store)


def control(store):
    s = ready(store)
    member = s.series()[0]["membro_id"]
    d = Document()
    d.add_paragraph("Original recebido")
    out = BytesIO()
    d.save(out)
    identifier = s.save(
        dict(
            direcao="RECEBIDO",
            numero_externo="12/2026",
            remetente="Pessoa",
            instituicao="Órgão",
            data="2026-09-01",
            data_recebimento="2026-09-10",
            membros=[member],
            assunto="Assunto recebido",
            prazo="2026-09-11",
        ),
        uploads=[("original.docx", out.getvalue())],
    )
    metadata = s.files(identifier)
    assert s.download(metadata[0]["id"]) == out.getvalue()
    assert set(metadata[0]) == {
        "id",
        "nome",
        "tipo",
        "tamanho",
        "incluida",
        "papel",
    }
    assert metadata[0]["papel"] == "DOCUMENTO"
    assert "conteudo" not in str(s.list()) and "payload" not in s.list()[0]
    s.update_status(
        identifier, "Em análise", "Encaminhado ao gabinete.", due="2026-09-12"
    )
    assert s.get(identifier)["prazo"] == "2026-09-12"
    assert s.movements(identifier)[-1]["anterior"] == "Recebido"
    draft = s.save({**sample(s), "responde_a": identifier})
    s.finalize(draft, files)
    assert s.list(related=identifier)[0]["id"] == draft
    assert s.get(identifier)["status"] == "Em análise"
    assert (
        s.list(direction="RECEBIDO", member=member, search="Pessoa")[0]["id"]
        == identifier
    )
    assert not s.list(direction="RECEBIDO", search="inexistente")
    assert s.overview(2026)["recebidos"] == 1


def test_baseline_safeguards(store):
    s = OficiosStore(store)
    assert s.sequence("PROGE", 2026) == {"proximo": 8, "confirmada": False}
    identifier = s.save(sample(s))
    with pytest.raises(ValueError):
        s.finalize(identifier, files)
    for number, confirmed in [(8, False), (7, True), (-1, True)]:
        with pytest.raises(ValueError):
            s.confirm_sequence("PROGE", 2026, number, confirmed)
    s.confirm_sequence("PROGE", 2026, 10, True)
    with pytest.raises(ValueError):
        s.confirm_sequence("PROGE", 2026, 9, True)
    assert s.finalize(identifier, files)["numero"] == 10
    with pytest.raises(ValueError):
        s.confirm_sequence("PROGE", 2026, 10, True)


def test_edit_delete_reopen(store):
    s = ready(store)
    r = sample(s)
    identifier = s.save(r)
    s.save({**r, "assunto": "Alterado"}, identifier)
    with pytest.raises(ValueError):
        s.delete_draft(identifier)
    reopened = OficiosStore(Store(store.path))
    assert reopened.get(identifier)["assunto"] == "Alterado"
    reopened.delete_draft(identifier, True)
    assert not reopened.list()
    assert reopened.sequence("PROGE", 2026)["proximo"] == 8


@pytest.mark.parametrize(
    "series,digits,prefix",
    [("PROGE", 3, "Ofício MPC/PB - PROGE n."), ("BTLC", 2, "Ofício BTLC-MPC-PB nº")],
)
def test_documents(store, series, digits, prefix):
    s = ready(store)
    config = next(x for x in s.series() if x["sigla"] == series)
    person = next(
        x for x in store.catalog("procuradores") if x["id"] == config["membro_id"]
    )
    r = {
        **sample(s, person["id"]),
        "signatario": person["nome"],
        "cargo_base": person["cargo_base"],
    }
    doc = Document(BytesIO(generate(r, config, 8)))
    text = "\n".join(p.text for p in doc.paragraphs)
    for expected in [
        prefix,
        str(8).zfill(digits) + "/2026",
        "10 de setembro de 2026.",
        r["destinatario"],
        r["assunto"],
        r["vocativo"],
        r["signatario"].upper(),
    ]:
        assert expected in text
    assert any(p.text == "Primeiro parágrafo." for p in doc.paragraphs)
    assert any(p.text == "Segundo parágrafo." for p in doc.paragraphs)
    assert "BLTC" not in text
    assert doc.sections[0].header._element.xml


def test_heading_date_and_subject_bold(store):
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    s = ready(store)
    config = next(x for x in s.series() if x["sigla"] == "PROGE")
    person = next(
        x for x in store.catalog("procuradores") if x["id"] == config["membro_id"]
    )
    record = {
        **sample(s, person["id"]),
        "assunto": "Excessos cometidos no certame",
        "signatario": person["nome"],
        "cargo_base": person["cargo_base"],
        "referencia": "Processo 123",
    }
    content = generate(record, config, 8)
    doc = Document(BytesIO(content))
    heading = next(
        p for p in doc.paragraphs if "PROGE" in p.text and "João Pessoa" in p.text
    )
    assert heading.alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert "\t" in heading.text
    assert "10 de setembro de 2026." in heading.text
    assert heading.text.count("de 2026.") == 1
    subject = next(p for p in doc.paragraphs if p.text.startswith("Assunto:"))
    assert subject.runs[0].text == "Assunto: "
    assert subject.runs[1].text == "Excessos cometidos no certame"
    assert subject.runs[1].bold is True
    assert "Processo 123" in "\n".join(p.text for p in doc.paragraphs)
    from document_generator.pdf import convert, PdfUnavailable

    try:
        pdf, engine = convert(content)
    except (PdfUnavailable, RuntimeError):
        return
    from pypdf import PdfReader

    extracted = "\n".join(
        page.extract_text() or "" for page in PdfReader(BytesIO(pdf)).pages
    )
    assert "João Pessoa (PB), 10 de setembro de 2026." in extracted.replace(
        "\u00a0", " "
    )
    assert "setembro\nde 2026" not in extracted


def test_btlc_heading_uses_tab(store):
    s = ready(store)
    config = next(x for x in s.series() if x["sigla"] == "BTLC")
    person = next(
        x for x in store.catalog("procuradores") if x["id"] == config["membro_id"]
    )
    record = {
        **sample(s, person["id"]),
        "signatario": person["nome"],
        "cargo_base": person["cargo_base"],
    }
    heading = next(
        p
        for p in Document(BytesIO(generate(record, config, 1))).paragraphs
        if "BTLC" in p.text and "João Pessoa" in p.text
    )
    assert "\t" in heading.text
    assert " " * 20 not in heading.text


@pytest.mark.parametrize(
    "name,content",
    [
        ("file.exe", b"a"),
        ("a.pdf", b"bad"),
        ("a.docx", b"PKbad"),
        pytest.param("a.pdf", b"%PDF-" + b"x" * (10 * 1024 * 1024), id="oversized"),
    ],
)
def test_invalid_upload(name, content):
    with pytest.raises(ValueError):
        validate_upload(name, content)


@pytest.mark.parametrize(
    "offset,expected", [(-1, "vencido"), (0, "próximo"), (7, "próximo"), (8, "")]
)
def test_deadlines(offset, expected):
    r = {
        "direcao": "RECEBIDO",
        "status": "Recebido",
        "prazo": (date.today() + timedelta(days=offset)).isoformat(),
    }
    assert expected in attention(r)
    r["status"] = "Concluído"
    assert not attention(r)


def test_series_unconfirmed(store):
    s = ready(store)
    laf = next(x for x in s.series() if x["sigla"] == "LAF")
    identifier = s.save(sample(s, laf["membro_id"]))
    with pytest.raises(ValueError):
        s.finalize(identifier, files)
    assert laf["modelo"] == "PROGE"
    s.confirm_sequence("LAF", 2026, 1, True)
    assert s.finalize(identifier, files)["numero"] == 1
    with pytest.raises(ValueError):
        s.configure_series(
            laf["membro_id"], "LAF", "PROGE", "Ofício LAF {numero}/{ano}", 3, True
        )


def test_uniqueness_database(store):
    s = ready(store)
    a = s.save(sample(s))
    b = s.save(sample(s))
    s.finalize(a, files)
    with pytest.raises(sqlite3.IntegrityError):
        with store.connection() as c:
            c.execute("UPDATE oficios SET numero=8,status='Gerado' WHERE id=?", (b,))


def test_home_and_oficios_navigation(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT

    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_oficios").click().run()
    app.button(key="gabinete_PROGE").click().run()
    assert not app.exception and not app.error
    for page in [
        "Novo Ofício",
        "Enviados",
        "Recebidos",
        "Acompanhamento",
        "Numeração",
        "Visão Geral",
    ]:
        app.radio(key="oficio_page").set_value(page).run()
        assert not app.exception and not app.error


def test_numbering_configuration_is_rendered_only_in_its_page(store, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from database.store import ROOT
    from tests.access_testing import enable_login

    service = ready(store)
    service.confirm_sequence("LAF", 2026, 4, True)
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_oficios").click().run()
    app.button(key="gabinete_PROGE").click().run()

    assert "Numeração" in app.radio(key="oficio_page").options
    assert not any(input.label == "Ano da sequência" for input in app.number_input)
    app.radio(key="oficio_page").set_value("Numeração").run()
    assert any(input.label == "Ano da sequência" for input in app.number_input)
    assert any("PROGE/2026: próximo 8" in item.value for item in app.markdown)

    next(button for button in app.button if button.label == "← Trocar gabinete").click().run()
    app.button(key="gabinete_LAF").click().run()
    app.radio(key="oficio_page").set_value("Numeração").run()
    assert any("LAF/2026: próximo 4" in item.value for item in app.markdown)


def test_oficio_detail_uses_conditional_sections(store, monkeypatch):
    from streamlit.testing.v1 import AppTest

    from database.store import ROOT
    from tests.access_testing import enable_login

    service = ready(store)
    identifier = service.save(sample(service))
    service.finalize(identifier, files)
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_oficios").click().run()
    app.button(key="gabinete_PROGE").click().run()
    app.radio(key="oficio_page").set_value("Enviados").run()
    app.button(key="open_oficio_" + identifier).click().run()

    section = app.radio(key="oficio_detail_section_" + identifier)
    assert section.value == "Resumo"
    assert set(section.options) == {"Resumo", "Movimentações", "Documentos", "Mais"}
    assert not any(item.label == "Novo status" for item in app.selectbox)

    app.button(key="oficio_register_movement_" + identifier).click().run()
    assert app.radio(key="oficio_detail_section_" + identifier).value == "Movimentações"
    assert any(item.label == "Novo status" for item in app.selectbox)

    app.button(key="oficio_conclude_" + identifier).click().run()
    assert app.radio(key="oficio_detail_section_" + identifier).value == "Movimentações"
    assert app.selectbox(key="oficio_status_" + identifier).value == "Concluído"

    app.radio(key="oficio_detail_section_" + identifier).set_value("Documentos").run()
    assert any(
        metadata["nome"] in caption.value
        for metadata in service.files(identifier)
        for caption in app.caption
    )


@pytest.mark.parametrize(
    "scenario",
    [
        test_number_lifecycle,
        test_independent_series_years,
        test_generator_failure_rolls_back,
        test_baseline_safeguards,
        test_series_unconfirmed,
    ],
)
def test_postgresql_numbering_contract(pg_store, scenario):
    scenario(pg_store)


def test_filters_and_attention_pagination(store):
    s = ready(store)
    member = s.series()[0]["membro_id"]
    for i in range(4):
        s.save(
            dict(
                direcao="RECEBIDO",
                numero_externo=str(i),
                remetente="Origem",
                instituicao="Órgão",
                data="2026-09-10",
                data_recebimento="2026-09-10",
                membros=[member],
                assunto=f"Assunto {i}",
                status="Recebido" if i % 2 else "Em análise",
            )
        )
    results = s.list(direction="RECEBIDO", attention_only=True, limit=1)
    assert len(results) == 1 and results[0]["status"] == "Em análise"
    assert s.list(attention_only=True, offset=1, limit=1)[0]["id"] != results[0]["id"]
    assert not s.list(attention_only=True, offset=2, limit=1)
    assert (
        len(s.list(subject="Assunto", start="2026-09-10", end="2026-09-10", year=2026))
        == 4
    )
    assert not s.list(start="2026-09-11")


def test_metadata_queries_exclude_binary(store, monkeypatch):
    from contextlib import contextmanager

    s = ready(store)
    identifier = s.save(sample(s))
    s.finalize(identifier, files)
    statements = []
    original = store.connection

    @contextmanager
    def traced(**kwargs):
        with original(**kwargs) as c:
            c.set_trace_callback(statements.append)
            yield c

    monkeypatch.setattr(store, "connection", traced)
    s.list()
    s.overview(2026)
    s.files(identifier)
    s.get(identifier)
    assert all("conteudo" not in sql.lower() for sql in statements)
    s.download(s.files(identifier)[0]["id"])
    assert any("select conteudo" in sql.lower() for sql in statements)


def test_pdf_upload_and_fresh_process(store):
    from pypdf import PdfWriter
    import subprocess, sys

    s = ready(store)
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    output = BytesIO()
    writer.write(output)
    content = output.getvalue()
    member = s.series()[0]["membro_id"]
    identifier = s.save(
        dict(
            direcao="RECEBIDO",
            numero_externo="12/2026",
            remetente="Pessoa",
            instituicao="Órgão",
            data="2026-09-10",
            data_recebimento="2026-09-10",
            membros=[member],
            assunto="Original",
        ),
        uploads=[("original.pdf", content)],
    )
    file = s.files(identifier)[0]
    code = "from database.store import Store; from database.oficios import OficiosStore; import sys,hashlib; print(hashlib.sha256(OficiosStore(Store(sys.argv[1])).download(sys.argv[2])).hexdigest())"
    import hashlib

    result = subprocess.run(
        [sys.executable, "-c", code, str(store.path), file["id"]],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == hashlib.sha256(content).hexdigest()


def test_invalid_relation_and_status(store):
    s = ready(store)
    draft = s.save(sample(s))
    with pytest.raises(ValueError):
        s.save({**sample(s), "responde_a": draft})
    with pytest.raises(ValueError):
        s.update_status(draft, "Enviado", sent="2026-09-10")
    s.finalize(draft, files)
    with pytest.raises(ValueError):
        s.update_status(draft, "Cancelado", "")
    with pytest.raises(ValueError):
        s.update_status(draft, "Enviado")
    s.update_status(draft, "Enviado", sent="2026-09-10")
    assert s.get(draft)["data_envio"] == "2026-09-10"
    s.update_status(draft, "Cancelado", "Sem efeito")
    assert (
        s.get(draft)["cancelada"]
        and s.movements(draft)[-1]["observacao"] == "Sem efeito"
    )


def test_migration_preserves_existing_data(store):
    before = store.catalog("procuradores")
    sequence = store.next_number(2026)
    s = ready(store)
    identifier = s.save(sample(s))
    OficiosStore(store)
    assert store.catalog("procuradores") == before
    assert store.next_number(2026) == sequence
    assert s.get(identifier)["status"] == "Rascunho"


def test_real_pdf_generation_when_enabled(store):
    import os

    if os.environ.get("MPC_TEST_OFFICE") != "1":
        pytest.skip(
            "Conversor local real habilitado apenas na validação visual explícita"
        )
    from pypdf import PdfReader

    s = ready(store)
    for code in ("PROGE", "BTLC"):
        member = next(x["membro_id"] for x in s.series() if x["sigla"] == code)
        identifier = s.save(sample(s, member))
        result = s.finalize(identifier)
        metadata = s.files(identifier)
        assert len(metadata) == 2
        pdf = next(x for x in metadata if x["tipo"] == "application/pdf")
        text = "\n".join(
            p.extract_text() for p in PdfReader(BytesIO(s.download(pdf["id"]))).pages
        )
        assert code in text and "2026" in text


def test_editor_save_reopen_finalize_ui(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT

    s = ready(store)
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    monkeypatch.setattr("document_generator.oficios.official_documents", files)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_oficios").click().run()
    app.button(key="gabinete_PROGE").click().run()
    app.radio(key="oficio_page").set_value("Novo Ofício").run()
    for label, value in [
        ("Assunto", "Assunto UI"),
        ("Nome do destinatário", "Pessoa UI"),
    ]:
        next(x for x in app.text_input if x.label == label).set_value(value)
    next(x for x in app.text_area if x.label.startswith("Corpo do ofício")).set_value(
        "Texto UI.\n\nSegundo parágrafo."
    )
    next(x for x in app.button if x.label == "Salvar rascunho").click().run()
    assert not app.exception and not app.error
    identifier = s.list()[0]["id"]
    assert s.get(identifier)["numero"] is None
    app.radio(key="oficio_page").set_value("Enviados").run()
    app.button(key="open_oficio_" + identifier).click().run()
    app.radio(key="oficio_detail_section_" + identifier).set_value("Mais").run()
    app.button(key="edit_" + identifier).click().run()
    assert app.radio(key="oficio_page").value == "Novo Ofício"
    assert next(x for x in app.button if x.label == "Finalizar e gerar ofício").disabled
    next(x for x in app.button if x.label == "Pré-visualizar DOCX").click().run()
    next(x for x in app.button if x.label == "Finalizar e gerar ofício").click().run()
    assert not app.exception and not app.error
    assert s.get(identifier)["numero"] == 8


def _blank_pdf():
    from pypdf import PdfWriter

    buffer = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(72, 72)
    writer.write(buffer)
    return buffer.getvalue()


def _blank_docx():
    buffer = BytesIO()
    document = Document()
    document.add_paragraph("Ofício pronto de teste.")
    document.save(buffer)
    return buffer.getvalue()


def attached_sample(service, member=None):
    member = member or next(
        s["membro_id"] for s in service.series() if s["sigla"] == "PROGE"
    )
    return dict(
        direcao="ENVIADO",
        membro_id=member,
        data="2026-09-10",
        assunto="Ofício anexo de teste",
        destinatario="DESTINATÁRIO ANEXO",
        unidade="TCE-PB",
    )


def test_attached_pdf_and_docx_use_existing_numbering(store):
    from services.oficios import official_attached_files, validate

    s = ready(store)
    pdf = _blank_pdf()
    validate(attached_sample(s), official=True, attached=True)
    with pytest.raises(ValueError, match="corpo"):
        validate(attached_sample(s), official=True)
    identifier = s.save(attached_sample(s))
    assert s.sequence("PROGE", 2026)["proximo"] == 8
    assert s.get(identifier)["numero"] is None
    result = s.finalize(
        identifier, attached_files=official_attached_files("pronto.pdf", pdf)
    )
    assert result["numero"] == 8
    assert result["serie"] == "PROGE"
    assert result["status"] == "Gerado"
    stored = s.files(identifier)
    assert len(stored) == 1
    assert stored[0]["tipo"] == "application/pdf"
    assert stored[0]["nome"].endswith(".pdf")
    assert ".." not in stored[0]["nome"]
    assert s.download(stored[0]["id"]) == pdf
    listed = s.list(direction="ENVIADO", status="Gerado")
    assert any(row["id"] == identifier for row in listed)
    docx = _blank_docx()
    second = s.save(attached_sample(s))
    other = s.finalize(
        second, attached_files=official_attached_files("pronto.docx", docx)
    )
    assert other["numero"] == 9
    files = s.files(second)
    assert len(files) == 1
    assert files[0]["tipo"].endswith("wordprocessingml.document")
    assert s.download(files[0]["id"]) == docx


def test_attached_rejects_invalid_files_without_consuming_number(store):
    from services.oficios import MAX_FILE, official_attached_files

    s = ready(store)
    identifier = s.save(attached_sample(s))
    with pytest.raises(ValueError):
        official_attached_files("file.exe", b"a")
    with pytest.raises(ValueError):
        official_attached_files("a.pdf", b"bad")
    with pytest.raises(ValueError):
        official_attached_files("a.docx", b"PKbad")
    with pytest.raises(ValueError):
        official_attached_files("a.pdf", b"%PDF-" + b"x" * MAX_FILE)
    with pytest.raises(ValueError):
        s.finalize(identifier, attached_files=[("pdf", "a.pdf", b"bad")])
    assert s.get(identifier)["status"] == "Rascunho"
    assert s.get(identifier)["numero"] is None
    assert s.sequence("PROGE", 2026)["proximo"] == 8
    assert not s.files(identifier)


def test_attached_office_persists_pdf_and_image_complementary_files(store):
    from services.oficios import official_attached_files

    service = ready(store)
    identifier = service.save(attached_sample(service))
    principal = _blank_pdf()
    image = b"\x89PNG\r\n\x1a\nimagem"
    service.finalize(
        identifier,
        attached_files=official_attached_files("oficio pronto.pdf", principal),
        complementary_files=[("parecer.pdf", _blank_pdf()), ("imagem.png", image)],
    )

    files = service.files(identifier)
    assert service.sequence("PROGE", 2026)["proximo"] == 9
    assert [row["papel"] for row in files] == ["PRINCIPAL", "ANEXO", "ANEXO"]
    assert {row["nome"] for row in files[1:]} == {"parecer.pdf", "imagem.png"}
    image_file = next(row for row in files if row["nome"] == "imagem.png")
    assert service.download(image_file["id"]) == image
    assert all(row["id"] for row in files)


def test_attached_office_accepts_one_pdf_complementary_file(store):
    from services.oficios import official_attached_files

    service = ready(store)
    identifier = service.save(attached_sample(service))
    service.finalize(
        identifier,
        attached_files=official_attached_files("pronto.pdf", _blank_pdf()),
        complementary_files=[("anexo.pdf", _blank_pdf())],
    )

    files = service.files(identifier)
    assert len(files) == 2
    assert [row["papel"] for row in files] == ["PRINCIPAL", "ANEXO"]
    assert files[1]["nome"] == "anexo.pdf"


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("arquivo.exe", b"MZ"),
        ("imagem.jpg", b"imagem sem assinatura"),
        ("imagem.png", b"imagem sem assinatura"),
    ],
)
def test_invalid_complementary_attachment_rolls_back_finalization(store, name, content):
    from services.oficios import official_attached_files

    service = ready(store)
    identifier = service.save(attached_sample(service))
    with pytest.raises(ValueError):
        service.finalize(
            identifier,
            attached_files=official_attached_files("pronto.pdf", _blank_pdf()),
            complementary_files=[(name, content)],
        )
    assert service.get(identifier)["status"] == "Rascunho"
    assert service.get(identifier)["numero"] is None
    assert service.sequence("PROGE", 2026)["proximo"] == 8
    assert service.files(identifier) == []


def test_attachment_persistence_failure_rolls_back_entire_finalization(store, monkeypatch):
    from services.oficios import official_attached_files

    service = ready(store)
    identifier = service.save(attached_sample(service))
    original_file = service._file

    def fail_attachment(connection, *args, **kwargs):
        role = args[4] if len(args) > 4 else kwargs.get("role")
        if role == "ANEXO":
            raise RuntimeError("Falha ao persistir anexo")
        return original_file(connection, *args, **kwargs)

    monkeypatch.setattr(service, "_file", fail_attachment)
    with pytest.raises(RuntimeError, match="persistir anexo"):
        service.finalize(
            identifier,
            attached_files=official_attached_files("pronto.pdf", _blank_pdf()),
            complementary_files=[("anexo.pdf", _blank_pdf())],
        )
    assert service.get(identifier)["status"] == "Rascunho"
    assert service.get(identifier)["numero"] is None
    assert service.sequence("PROGE", 2026)["proximo"] == 8
    assert service.files(identifier) == []


def test_new_oficio_defaults_to_system_creation(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from tests.access_testing import enable_login
    from services.oficios_ui import PREP_ATTACH, PREP_CREATE

    ready(store)
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_oficios").click().run()
    app.button(key="gabinete_PROGE").click().run()
    app.radio(key="oficio_page").set_value("Novo Ofício").run()
    mode = next(r for r in app.radio if r.key == "oficio_prep_mode")
    assert mode.value == PREP_CREATE
    assert any(i.label == "Nome do destinatário" for i in app.text_input)
    assert any(a.label.startswith("Corpo do ofício") for a in app.text_area)
    app.radio(key="oficio_prep_mode").set_value(PREP_ATTACH).run()
    assert not any(a.label.startswith("Corpo do ofício") for a in app.text_area)
    assert any(
        i.label == "Documento principal do ofício" for i in app.get("file_uploader")
    )
    assert any(
        i.label == "Anexos complementares — opcional"
        for i in app.get("file_uploader")
    )
    finalize = next(b for b in app.button if b.key == "oficio_attach_finalize")
    assert finalize.disabled
    assert any(
        c.label.startswith("Confirmo o registro deste documento")
        for c in app.checkbox
    )


def test_attached_editor_persists_uploaded_pdf_and_complementary_pdf(
    store, monkeypatch
):
    from streamlit.testing.v1 import AppTest

    from database.store import ROOT
    from tests.access_testing import enable_login
    from services.oficios_ui import PREP_ATTACH

    ready(store)
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_oficios").click().run()
    app.button(key="gabinete_PROGE").click().run()
    app.radio(key="oficio_page").set_value("Novo Ofício").run()
    app.radio(key="oficio_prep_mode").set_value(PREP_ATTACH).run()
    principal = _blank_pdf()
    attachment = _blank_pdf()
    app.file_uploader(key="oficio_attach_file").set_value(
        ("principal.pdf", principal, "application/pdf")
    )
    app.file_uploader(key="oficio_attach_extras").set_value(
        [("anexo.pdf", attachment, "application/pdf")]
    )
    app.text_input(key="oficio_attach_destinatario").set_value("Destinatário")
    app.text_input(key="oficio_attach_unidade").set_value("Unidade")
    app.text_input(key="oficio_attach_assunto").set_value("Assunto")
    app.checkbox(key="oficio_attach_confirm").check().run()
    app.button(key="oficio_attach_finalize").click().run()

    assert not app.exception
    from database.oficios import OficiosStore

    service = OficiosStore(store)
    created = service.list(direction="ENVIADO", status="Gerado")
    assert len(created) == 1
    files = service.files(created[0]["id"])
    assert [row["papel"] for row in files] == ["PRINCIPAL", "ANEXO"]
    assert service.download(files[0]["id"]) == principal
    assert service.download(files[1]["id"]) == attachment


def test_ultimas_movimentacoes_show_brazilian_dates():
    from datetime import datetime

    from services.oficios_ui import data_movimentacao
    from services.ui_theme import record_html

    assert data_movimentacao("2026-09-29") == "29/09/2026"
    assert data_movimentacao(date(2026, 9, 16)) == "16/09/2026"
    assert data_movimentacao(datetime(2026, 9, 14, 18, 30)) == "14/09/2026"
    assert data_movimentacao("2026-09-29T08:15:00") == "29/09/2026"
    markup = record_html(
        "5230/2026/MPF/PR-PB/PRDC-JAS/2026 — Encaminha documentação",
        meta=data_movimentacao("2026-09-29"),
    )
    assert "29/09/2026" in markup
    assert "2026-09-29" not in markup


def test_ultimas_movimentacoes_keep_the_full_subject():
    from services.oficios_ui import label, recent_movement_label

    subject = "Requisição de informações e cópia integral do Protocolo nº 88.362/2026 - Ministério Público"
    row = {"serie": "LAF", "numero": 1, "numero_externo": "", "ano": 2026, "assunto": subject}

    assert recent_movement_label(row) == f"LAF 1/2026 — {subject}"
    assert label(row) != recent_movement_label(row)


def test_empty_oficio_listing_uses_the_themed_notice():
    from inspect import getsource

    from services.oficios_ui import listing

    source = getsource(listing)
    assert (
        'guidance_note("Nenhum ofício nesta página para os filtros selecionados.")'
        in source
    )
    assert "empty_state(" not in source
