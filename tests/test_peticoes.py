from io import BytesIO

import pytest
from pypdf import PdfWriter

from database.peticoes import PeticoesStore
from services.access import has_permission, principal_from_record, resolve_principal
from services.peticoes import (
    add_progress,
    conclude,
    create,
    remove_progress,
    update,
    update_request_result,
    save_requests,
)


def _pdf():
    writer = PdfWriter(); writer.add_blank_page(width=72, height=72)
    buffer = BytesIO(); writer.write(buffer); return buffer.getvalue()


def _payload(store, **changes):
    signer = next(row["id"] for row in store.catalog("procuradores") if row["ativo"])
    data = {"numero_tramita":"116439/26","data_protocolo":"2026-09-29","destinatario_tipo":"Presidente do TCE-PB","destinatario":"Presidência","natureza":"PROVIDENCIAS","assunto":"Fiscalização","objeto":"Apurar fatos.","signatarios":[signer],"pedidos":["Expedir Nota Recomendatória","Fiscalização temática"]}
    data.update(changes); return data


def _principal(store): return resolve_principal(store,{"email":"admin@test.local"})


def test_create_list_document_and_constraints(store):
    principal = _principal(store); record = create(store,_payload(store),principal,("peticao.pdf","application/pdf",_pdf()))
    assert record["situacao"] == "PROTOCOLADA" and len(record["pedidos"]) == 2
    rows = PeticoesStore(store).list(); assert rows and "arquivo" not in rows[0]
    assert PeticoesStore(store).document(record["id"])["arquivo"].startswith(b"%PDF-")
    with pytest.raises(ValueError,match="Já existe"): create(store,_payload(store),principal,("peticao.pdf","application/pdf",_pdf()))
    with pytest.raises(ValueError,match="signatário"): create(store,_payload(store,signatarios=[]),principal,("peticao.pdf","application/pdf",_pdf()))
    with pytest.raises(ValueError,match="PDF"): create(store,_payload(store,numero_tramita="116440/26"),principal,("peticao.pdf","application/pdf",b"nao pdf"))


def test_edit_requests_progress_results_conclusion_and_audit(store):
    principal = _principal(store); record = create(store,_payload(store),principal,("peticao.pdf","application/pdf",_pdf()))
    changed = _payload(store,assunto="Fiscalização revisada",pedidos=["Pedido revisado"])
    assert update(store,record["id"],changed,principal)["assunto"] == "Fiscalização revisada"
    current = PeticoesStore(store).get(record["id"]); request = current["pedidos"][0]
    update_request_result(store,request["id"],"ACOLHIDO","DIAFI acionada.","2026-10-01",principal)
    add_progress(store,record["id"],"2026-10-02","Encaminhada à DIAFI.",principal)
    timeline = PeticoesStore(store).progress(record["id"]); assert timeline[0]["descricao"] == "Encaminhada à DIAFI."
    remove_progress(store,timeline[0]["id"],"Lançamento duplicado.",principal)
    conclude(store,record["id"],"Fiscalização autorizada.",principal)
    finished = PeticoesStore(store).get(record["id"]); assert finished["situacao"] == "CONCLUIDA" and finished["pedidos"][0]["situacao_resultado"] == "ACOLHIDO"
    with store.connection(read_only=True) as c:
        events = {r[0] for r in c.execute("SELECT evento FROM auditoria_eventos WHERE modulo='peticoes'")}
        hidden = c.execute("SELECT excluido,motivo_exclusao FROM peticoes_andamentos WHERE id=?",(timeline[0]["id"],)).fetchone()
    assert {"PETICAO_CRIADA","PETICAO_EDITADA","PETICAO_RESULTADO_REGISTRADO","PETICAO_ANDAMENTO_CRIADO","PETICAO_ANDAMENTO_EXCLUIDO_LOGICAMENTE","PETICAO_CONCLUIDA"} <= events
    assert hidden[0] == 1 and hidden[1] == "Lançamento duplicado."


def test_permissions_are_independent(store):
    from tests.access_testing import seed_access
    seed_access(store,email="reader@test.local",nome="Leitor",perfil="USUARIO",pode_portarias=False,pode_agenda=False,pode_oficios=False,pode_admin=False,pode_representacoes=False,pode_peticoes=True,pode_peticoes_cadastrar=False,pode_peticoes_editar=False,pode_peticoes_registrar_andamento=False,pode_peticoes_registrar_resultado=False,pode_peticoes_concluir=False)
    principal = resolve_principal(store,{"email":"reader@test.local"})
    assert has_permission(principal,"peticoes")
    assert not has_permission(principal,"peticoes_cadastrar")
    assert not has_permission(principal,"representacoes")


def test_request_removal_is_logical_and_order_preserves_results(store):
    principal = _principal(store)
    record = create(store,_payload(store),principal,("peticao.pdf","application/pdf",_pdf()))
    first, second = PeticoesStore(store).get(record["id"])["pedidos"]
    update_request_result(store,first["id"],"ACOLHIDO","Providência adotada.","2026-10-01",principal)
    save_requests(store,record["id"],[{"id":second["id"],"descricao":"Fiscalização reordenada."}],principal)
    current = PeticoesStore(store).get(record["id"])
    assert [(item["id"],item["ordem"]) for item in current["pedidos"]] == [(second["id"],1)]
    with store.connection(read_only=True) as c:
        archived = c.execute("SELECT excluido,excluido_por,motivo_exclusao,resultado FROM peticoes_pedidos WHERE id=?",(first["id"],)).fetchone()
        event = c.execute("SELECT 1 FROM auditoria_eventos WHERE evento='PETICAO_PEDIDO_EXCLUIDO_LOGICAMENTE'").fetchone()
    assert archived[0] == 1 and archived[1] == "admin@test.local" and archived[3] == "Providência adotada." and event


def test_pdf_is_preserved_until_explicit_replacement_and_audited(store):
    principal = _principal(store); record = create(store,_payload(store),principal,("peticao.pdf","application/pdf",_pdf()))
    original = PeticoesStore(store).document(record["id"])["arquivo"]
    update(store,record["id"],_payload(store,assunto="Alterado"),principal)
    assert PeticoesStore(store).document(record["id"])["arquivo"] == original
    replacement = _pdf()
    update(store,record["id"],_payload(store,assunto="Alterado"),principal,("novo.pdf","application/pdf",replacement))
    with store.connection(read_only=True) as c: event=c.execute("SELECT 1 FROM auditoria_eventos WHERE evento='PETICAO_PDF_SUBSTITUIDO'").fetchone()
    assert PeticoesStore(store).document(record["id"])["arquivo"] == replacement and event


@pytest.mark.parametrize("change",[{"numero_tramita":""},{"data_protocolo":""},{"signatarios":[]},{"pedidos":[]}])
def test_required_protocol_fields_are_validated(store,change):
    principal=_principal(store)
    with pytest.raises(ValueError): create(store,_payload(store,**change),principal,("peticao.pdf","application/pdf",_pdf()))


def test_conclusion_with_pending_request_is_allowed_and_audited(store):
    principal=_principal(store); record=create(store,_payload(store),principal,("peticao.pdf","application/pdf",_pdf()))
    conclude(store,record["id"],"Resultado excepcional.",principal)
    assert PeticoesStore(store).get(record["id"])["situacao"] == "CONCLUIDA"
    with store.connection(read_only=True) as c: assert c.execute("SELECT 1 FROM auditoria_eventos WHERE evento='PETICAO_CONCLUIDA'").fetchone()


def test_child_failure_rolls_back_and_tramita_can_be_reused(store, monkeypatch):
    principal = _principal(store)
    original = PeticoesStore._replace_relations

    def fail_after_parent_insert(self, connection, identifier, data, stamp):
        raise RuntimeError("falha filha simulada")

    monkeypatch.setattr(PeticoesStore, "_replace_relations", fail_after_parent_insert)
    with pytest.raises(RuntimeError, match="falha filha"):
        create(store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf()))
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM peticoes WHERE numero_tramita='116439/26'").fetchone()[0] == 0
    monkeypatch.setattr(PeticoesStore, "_replace_relations", original)
    assert create(store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf()))["numero_tramita"] == "116439/26"
