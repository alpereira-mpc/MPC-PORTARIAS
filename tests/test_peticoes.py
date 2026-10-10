from io import BytesIO
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter

from database.peticoes import PeticoesStore
from services.access import has_permission, principal_from_record, resolve_principal
from services.peticoes import (
    SITUACOES_ABERTAS,
    add_progress,
    bloco_pedidos,
    conclude,
    create,
    remove_progress,
    save_requests,
    texto_pedidos,
    texto_pedidos_ia,
    update,
    update_request_result,
)


def _pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _payload(store, **changes):
    signer = next(row["id"] for row in store.catalog("procuradores") if row["ativo"])
    data = {
        "numero_tramita": "116439/26",
        "data_protocolo": "2026-09-29",
        "destinatario_tipo": "Presidente do TCE-PB",
        "destinatario": "Presidência",
        "natureza": "PROVIDENCIAS",
        "assunto": "Fiscalização",
        "objeto": "Apurar fatos.",
        "signatarios": [signer],
        "pedidos": ["Expedir Nota Recomendatória", "Fiscalização temática"],
    }
    data.update(changes)
    return data


def _principal(store):
    return resolve_principal(store, {"email": "admin@test.local"})


def test_petition_badges_map_status_and_keep_date_neutral():
    from services.peticoes_ui import _badge_items

    badges = _badge_items(
        {"situacao": "EM_ACOMPANHAMENTO", "data_protocolo": "2026-09-30"}
    )
    assert badges == [
        ("Em acompanhamento", "neutral", "pet-status pet-status-active"),
        ("30/09/2026", "neutral", "pet-date"),
    ]
    unknown = _badge_items({"situacao": "FUTURA", "data_protocolo": "2026-09-30"})
    assert unknown[0] == ("FUTURA", "neutral", "pet-status pet-status-unknown")
    assert unknown[1][0] == "30/09/2026"


def test_petition_download_preparation_is_audited_without_false_completion(
    store, monkeypatch
):
    import services.peticoes_ui as ui
    from database.audit import AuditStore

    monkeypatch.setattr(ui, "st", SimpleNamespace(session_state={}))
    ui._open_pdf_panel(77, store, _principal(store), "116439/26")
    events = AuditStore(store).list_events({"modulo": "peticoes"}, limit=10)
    assert len(events) == 1
    assert events[0]["evento"] == "DOWNLOAD_PREPARADO"
    assert events[0]["entidade_id"] == "77"
    assert ui.st.session_state["peticoes_painel"]["p77"] is True
    source = __import__("inspect").getsource(ui._card)
    assert source.count('"operacao": "solicitado"') == 2
    assert "DOCUMENTO_BAIXADO" not in source


def test_create_list_document_and_constraints(store):
    principal = _principal(store)
    record = create(
        store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf())
    )
    assert record["situacao"] == "PROTOCOLADA" and len(record["pedidos"]) == 2
    rows = PeticoesStore(store).list()
    assert rows and "arquivo" not in rows[0]
    assert PeticoesStore(store).document(record["id"])["arquivo"].startswith(b"%PDF-")
    with pytest.raises(ValueError, match="Já existe"):
        create(
            store,
            _payload(store),
            principal,
            ("peticao.pdf", "application/pdf", _pdf()),
        )
    with pytest.raises(ValueError, match="signatário"):
        create(
            store,
            _payload(store, signatarios=[]),
            principal,
            ("peticao.pdf", "application/pdf", _pdf()),
        )
    with pytest.raises(ValueError, match="PDF"):
        create(
            store,
            _payload(store, numero_tramita="116440/26"),
            principal,
            ("peticao.pdf", "application/pdf", b"nao pdf"),
        )


def test_edit_requests_progress_results_conclusion_and_audit(store):
    principal = _principal(store)
    record = create(
        store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf())
    )
    changed = _payload(
        store, assunto="Fiscalização revisada", pedidos=["Pedido revisado"]
    )
    assert (
        update(store, record["id"], changed, principal)["assunto"]
        == "Fiscalização revisada"
    )
    current = PeticoesStore(store).get(record["id"])
    request = current["pedidos"][0]
    update_request_result(
        store, request["id"], "ACOLHIDO", "DIAFI acionada.", "2026-10-01", principal
    )
    add_progress(store, record["id"], "2026-10-02", "Encaminhada à DIAFI.", principal)
    timeline = PeticoesStore(store).progress(record["id"])
    assert timeline[0]["descricao"] == "Encaminhada à DIAFI."
    remove_progress(store, timeline[0]["id"], "Lançamento duplicado.", principal)
    conclude(store, record["id"], "Fiscalização autorizada.", principal)
    finished = PeticoesStore(store).get(record["id"])
    assert (
        finished["situacao"] == "CONCLUIDA"
        and finished["pedidos"][0]["situacao_resultado"] == "ACOLHIDO"
    )
    with store.connection(read_only=True) as c:
        events = {
            r[0]
            for r in c.execute(
                "SELECT evento FROM auditoria_eventos WHERE modulo='peticoes'"
            )
        }
        hidden = c.execute(
            "SELECT excluido,motivo_exclusao FROM peticoes_andamentos WHERE id=?",
            (timeline[0]["id"],),
        ).fetchone()
    assert {
        "PETICAO_CRIADA",
        "PETICAO_EDITADA",
        "PETICAO_RESULTADO_REGISTRADO",
        "PETICAO_ANDAMENTO_CRIADO",
        "PETICAO_ANDAMENTO_EXCLUIDO_LOGICAMENTE",
        "PETICAO_CONCLUIDA",
    } <= events
    assert hidden[0] == 1 and hidden[1] == "Lançamento duplicado."


def test_progress_documents_are_linked_without_replacing_the_principal_pdf(store):
    principal = _principal(store)
    record = create(
        store, _payload(store), principal, ("principal.pdf", "application/pdf", _pdf())
    )
    main_before = PeticoesStore(store).document(record["id"])["arquivo"]
    first, second = _pdf(), _pdf()
    add_progress(
        store,
        record["id"],
        "2026-10-05",
        "Recebido ofício-resposta com anexos.",
        principal,
        (
            ("Oficio-resposta.pdf", "application/pdf", first, "OFICIO_RESPOSTA"),
            ("Anexo-I.pdf", "application/pdf", second, "ANEXO"),
        ),
    )
    timeline = PeticoesStore(store).progress(record["id"])
    progress = next(item for item in timeline if item["descricao"].startswith("Recebido"))
    assert {(item["nome"], item["categoria"]) for item in progress["documentos"]} == {
        ("Oficio-resposta.pdf", "OFICIO_RESPOSTA"),
        ("Anexo-I.pdf", "ANEXO"),
    }
    assert PeticoesStore(store).document(record["id"])["arquivo"] == main_before
    with pytest.raises(ValueError, match="PDF"):
        add_progress(
            store,
            record["id"],
            "2026-10-06",
            "Arquivo inválido.",
            principal,
            (("texto.pdf", "application/pdf", b"invalido", "OUTRO"),),
        )


def test_permissions_are_independent(store):
    from tests.access_testing import seed_access

    seed_access(
        store,
        email="reader@test.local",
        nome="Leitor",
        perfil="USUARIO",
        pode_portarias=False,
        pode_agenda=False,
        pode_oficios=False,
        pode_admin=False,
        pode_representacoes=False,
        pode_peticoes=True,
        pode_peticoes_cadastrar=False,
        pode_peticoes_editar=False,
        pode_peticoes_registrar_andamento=False,
        pode_peticoes_registrar_resultado=False,
        pode_peticoes_concluir=False,
    )
    principal = resolve_principal(store, {"email": "reader@test.local"})
    assert has_permission(principal, "peticoes")
    assert not has_permission(principal, "peticoes_cadastrar")
    assert not has_permission(principal, "representacoes")


def test_request_removal_is_logical_and_order_preserves_results(store):
    principal = _principal(store)
    record = create(
        store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf())
    )
    first, second = PeticoesStore(store).get(record["id"])["pedidos"]
    update_request_result(
        store, first["id"], "ACOLHIDO", "Providência adotada.", "2026-10-01", principal
    )
    save_requests(
        store,
        record["id"],
        [{"id": second["id"], "descricao": "Fiscalização reordenada."}],
        principal,
    )
    current = PeticoesStore(store).get(record["id"])
    assert [(item["id"], item["ordem"]) for item in current["pedidos"]] == [
        (second["id"], 1)
    ]
    with store.connection(read_only=True) as c:
        archived = c.execute(
            "SELECT excluido,excluido_por,motivo_exclusao,resultado FROM peticoes_pedidos WHERE id=?",
            (first["id"],),
        ).fetchone()
        event = c.execute(
            "SELECT 1 FROM auditoria_eventos WHERE evento='PETICAO_PEDIDO_EXCLUIDO_LOGICAMENTE'"
        ).fetchone()
    assert (
        archived[0] == 1
        and archived[1] == "admin@test.local"
        and archived[3] == "Providência adotada."
        and event
    )


def test_pdf_is_preserved_until_explicit_replacement_and_audited(store):
    principal = _principal(store)
    record = create(
        store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf())
    )
    original = PeticoesStore(store).document(record["id"])["arquivo"]
    update(store, record["id"], _payload(store, assunto="Alterado"), principal)
    assert PeticoesStore(store).document(record["id"])["arquivo"] == original
    replacement = _pdf()
    update(
        store,
        record["id"],
        _payload(store, assunto="Alterado"),
        principal,
        ("novo.pdf", "application/pdf", replacement),
    )
    with store.connection(read_only=True) as c:
        event = c.execute(
            "SELECT 1 FROM auditoria_eventos WHERE evento='PETICAO_PDF_SUBSTITUIDO'"
        ).fetchone()
    assert (
        PeticoesStore(store).document(record["id"])["arquivo"] == replacement and event
    )


@pytest.mark.parametrize(
    "change",
    [
        {"numero_tramita": ""},
        {"data_protocolo": ""},
        {"signatarios": []},
        {"pedidos": []},
    ],
)
def test_required_protocol_fields_are_validated(store, change):
    principal = _principal(store)
    with pytest.raises(ValueError):
        create(
            store,
            _payload(store, **change),
            principal,
            ("peticao.pdf", "application/pdf", _pdf()),
        )


def test_conclusion_with_pending_request_is_allowed_and_audited(store):
    principal = _principal(store)
    record = create(
        store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf())
    )
    conclude(store, record["id"], "Resultado excepcional.", principal)
    assert PeticoesStore(store).get(record["id"])["situacao"] == "CONCLUIDA"
    with store.connection(read_only=True) as c:
        assert c.execute(
            "SELECT 1 FROM auditoria_eventos WHERE evento='PETICAO_CONCLUIDA'"
        ).fetchone()


def test_child_failure_rolls_back_and_tramita_can_be_reused(store, monkeypatch):
    principal = _principal(store)
    original = PeticoesStore._replace_relations

    def fail_after_parent_insert(self, connection, identifier, data, stamp):
        raise RuntimeError("falha filha simulada")

    monkeypatch.setattr(PeticoesStore, "_replace_relations", fail_after_parent_insert)
    with pytest.raises(RuntimeError, match="falha filha"):
        create(
            store,
            _payload(store),
            principal,
            ("peticao.pdf", "application/pdf", _pdf()),
        )
    with store.connection(read_only=True) as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM peticoes WHERE numero_tramita='116439/26'"
            ).fetchone()[0]
            == 0
        )
    monkeypatch.setattr(PeticoesStore, "_replace_relations", original)
    assert (
        create(
            store,
            _payload(store),
            principal,
            ("peticao.pdf", "application/pdf", _pdf()),
        )["numero_tramita"]
        == "116439/26"
    )


def test_new_petition_stores_every_request_in_one_block(store):
    principal = _principal(store)
    text = "a) Expedição de Nota Recomendatória\nb) Determinação à DIAFI\nb.1) Planejamento temático\nb.2) Execução nos municípios"
    record = create(
        store,
        _payload(store, pedidos=bloco_pedidos(text)),
        principal,
        ("peticao.pdf", "application/pdf", _pdf()),
    )
    assert len(record["pedidos"]) == 1
    assert record["pedidos"][0]["descricao"] == text
    assert texto_pedidos(record["pedidos"]) == text


def test_historical_requests_stay_intact_until_an_explicit_edit(store):
    principal = _principal(store)
    record = create(
        store,
        _payload(store, pedidos=["Pedido 1", "Pedido 2", "b.1) Subpedido"]),
        principal,
        ("peticao.pdf", "application/pdf", _pdf()),
    )
    with store.connection(read_only=True) as c:
        before = c.execute(
            "SELECT id,excluido,descricao FROM peticoes_pedidos WHERE peticao_id=? ORDER BY ordem",
            (record["id"],),
        ).fetchall()
    shown = texto_pedidos(PeticoesStore(store).get(record["id"])["pedidos"])
    with store.connection(read_only=True) as c:
        after = c.execute(
            "SELECT id,excluido,descricao FROM peticoes_pedidos WHERE peticao_id=? ORDER BY ordem",
            (record["id"],),
        ).fetchall()
    assert shown == "Pedido 1\n\nPedido 2\n\nb.1) Subpedido"
    assert [(row["id"], row["excluido"], row["descricao"]) for row in after] == [
        (row["id"], row["excluido"], row["descricao"]) for row in before
    ]


def test_global_edit_archives_previous_requests_without_physical_delete(store):
    principal = _principal(store)
    record = create(
        store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf())
    )
    first = record["pedidos"][0]
    update_request_result(
        store, first["id"], "ACOLHIDO", "DIAFI acionada.", "2026-10-01", principal
    )
    consolidated = "Expedir Nota Recomendatória\n\nFiscalização temática\nb.1) Municípios paraibanos"
    update(store, record["id"], _payload(store, pedidos=[consolidated]), principal)
    current = PeticoesStore(store).get(record["id"])
    assert [item["descricao"] for item in current["pedidos"]] == [consolidated]
    with store.connection(read_only=True) as c:
        rows = list(
            c.execute(
                "SELECT id,excluido,descricao,resultado FROM peticoes_pedidos WHERE peticao_id=?",
                (record["id"],),
            )
        )
        event = c.execute(
            "SELECT 1 FROM auditoria_eventos WHERE evento='PETICAO_PEDIDO_EXCLUIDO_LOGICAMENTE'"
        ).fetchone()
    assert len(rows) == 3
    archived = next(row for row in rows if row["id"] == first["id"])
    assert (
        archived["excluido"] == 1
        and archived["resultado"] == "DIAFI acionada."
        and event
    )
    assert any(
        row["excluido"] == 0 and consolidated in row["descricao"] for row in rows
    )


def test_ai_requests_become_one_ordered_block():
    block = texto_pedidos_ia(
        [
            {"descricao": "Pedido A"},
            {"descricao": "Pedido B"},
            {"descricao": "Pedido C"},
        ]
    )
    assert block == "a) Pedido A\nb) Pedido B\nc) Pedido C"
    assert texto_pedidos_ia([{"descricao": "Único pedido"}]) == "Único pedido"


def test_followup_summary_is_batched_and_skips_pdf_bytes(store):
    from contextlib import contextmanager

    principal = _principal(store)
    signers = [row["id"] for row in store.catalog("procuradores") if row["ativo"]][:2]
    create(
        store,
        _payload(
            store,
            numero_tramita="116435/26",
            data_protocolo="2026-09-01",
            assunto="Tema antigo",
            signatarios=[signers[0]],
        ),
        principal,
        ("peticao.pdf", "application/pdf", _pdf()),
    )
    newer = create(
        store,
        _payload(
            store,
            numero_tramita="116439/26",
            data_protocolo="2026-09-29",
            assunto="Proteção das Itacoatiaras do Ingá",
            signatarios=signers,
        ),
        principal,
        ("peticao.pdf", "application/pdf", _pdf()),
    )
    add_progress(store, newer["id"], "2026-09-30", "Petição encaminhada.", principal)
    add_progress(store, newer["id"], "2026-09-28", "Andamento anterior.", principal)
    concluded = create(
        store,
        _payload(store, numero_tramita="116440/26", data_protocolo="2026-09-30"),
        principal,
        ("peticao.pdf", "application/pdf", _pdf()),
    )
    conclude(store, concluded["id"], "Resultado institucional.", principal)
    db = PeticoesStore(store)
    calls = []
    original = store.connection

    @contextmanager
    def wrapped(*args, **kwargs):
        with original(*args, **kwargs) as conn:

            class Logging:
                def execute(self, sql, params=()):
                    calls.append(" ".join(str(sql).split()))
                    return conn.execute(sql, params)

                def __getattr__(self, name):
                    return getattr(conn, name)

            yield Logging()

    store.connection = wrapped
    try:
        rows = db.list_resumo(situacoes=SITUACOES_ABERTAS, limit=20)
    finally:
        store.connection = original
    assert [row["numero_tramita"] for row in rows] == ["116439/26", "116435/26"]
    assert rows[0]["ultimo_andamento"]["descricao"] == "Petição encaminhada."
    assert rows[0]["ultimo_andamento"]["data"] == "2026-09-30"
    assert "Itacoatiaras" in rows[0]["objeto"] or rows[0]["assunto"].startswith(
        "Proteção"
    )
    assert len(rows[0]["signatarios"]) == 2
    assert "arquivo" not in rows[0]
    assert len(calls) == 4
    assert sum("peticoes_andamentos" in sql.lower() for sql in calls) == 1
    assert any("ROW_NUMBER()" in sql for sql in calls)
    assert all(
        "peticoes_documentos" not in sql.lower() and "arquivo" not in sql.lower()
        for sql in calls
    )
    assert all(
        "representacoes" not in sql.lower()
        and "oficios" not in sql.lower()
        and "ouvidoria" not in sql.lower()
        for sql in calls
    )
    history = db.list_resumo(situacoes=("CONCLUIDA",), limit=20)
    assert [row["numero_tramita"] for row in history] == ["116440/26"]
    assert history[0]["resultado_global"] == "Resultado institucional."
    found = db.list_resumo(
        situacoes=SITUACOES_ABERTAS,
        texto="Itacoatiaras",
        signatario_id=signers[0],
        limit=20,
    )
    assert [row["numero_tramita"] for row in found] == ["116439/26"]
    assert [
        row["numero_tramita"]
        for row in db.list_resumo(situacoes=SITUACOES_ABERTAS, limit=1)
    ] == ["116439/26"]
    assert [
        row["numero_tramita"]
        for row in db.list_resumo(situacoes=SITUACOES_ABERTAS, limit=1, offset=1)
    ] == ["116435/26"]


def test_editing_other_fields_does_not_rewrite_requests(store):
    principal = _principal(store)
    record = create(
        store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf())
    )
    before = [item["id"] for item in record["pedidos"]]
    data = _payload(store, assunto="Somente o assunto")
    data.pop("pedidos")
    update(store, record["id"], data, principal)
    current = PeticoesStore(store).get(record["id"])
    assert current["assunto"] == "Somente o assunto"
    assert [item["id"] for item in current["pedidos"]] == before
