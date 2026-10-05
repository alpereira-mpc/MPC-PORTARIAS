"""Acompanhamento, global requests and assisted fill for Petições."""

from contextlib import contextmanager

from streamlit.testing.v1 import AppTest

from database.peticoes import PeticoesStore
from services.access import resolve_principal
from services.ai_service import GeminiErro
from tests.access_testing import seed_access
from tests.test_peticoes import _payload, _pdf, _principal


_HOLD = {}


def _page():
    from services.peticoes_ui import render
    from tests.test_peticoes_fluxo import _HOLD

    render(_HOLD["store"], _HOLD["principal"])


def _open():
    return AppTest.from_function(_page, default_timeout=30).run()


def _spy(store):
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
    return calls, original


def test_acompanhamento_lists_petitions_without_a_selector(store):
    principal = _principal(store)
    older = _payload(
        store,
        numero_tramita="116435/26",
        data_protocolo="2026-09-01",
        assunto="Tema antigo",
    )
    newer = _payload(
        store,
        numero_tramita="116439/26",
        data_protocolo="2026-09-29",
        assunto="Proteção das Itacoatiaras do Ingá",
        objeto="Fiscalização acerca das medidas de proteção, conservação e governança.",
    )
    from services.peticoes import add_progress, create

    first = create(store, older, principal, ("peticao.pdf", "application/pdf", _pdf()))
    second = create(store, newer, principal, ("peticao.pdf", "application/pdf", _pdf()))
    add_progress(store, second["id"], "2026-09-30", "Petição protocolada.", principal)
    _HOLD["store"] = store
    _HOLD["principal"] = principal
    calls, original = _spy(store)
    try:
        app = _open()
    finally:
        store.connection = original
    assert not app.exception
    assert app.radio(key="peticoes_secao").value == "Acompanhamento"
    assert [item.label for item in app.selectbox].count("Petição") == 0
    markdown = " ".join(item.value for item in app.markdown)
    assert "116439/26 — Proteção das Itacoatiaras do Ingá" in markdown
    assert "116435/26 — Tema antigo" in markdown
    assert markdown.index("116439/26") < markdown.index("116435/26")
    assert "Protocolada" in markdown
    assert "29/09/2026" in markdown
    assert "Fiscalização acerca das medidas" in markdown
    assert "Petição protocolada." in markdown
    text_keys = {item.key for item in app.text_area}
    date_keys = {item.key for item in app.date_input}
    button_keys = {item.key for item in app.button}
    assert f"peticoes_andamento_texto_{first['id']}" in text_keys
    assert f"peticoes_andamento_texto_{second['id']}" in text_keys
    assert f"peticoes_andamento_data_{first['id']}" in date_keys
    assert f"peticoes_andamento_data_{second['id']}" in date_keys
    assert f"peticoes_andamento_salvar_{first['id']}" in button_keys
    assert f"peticoes_andamento_salvar_{second['id']}" in button_keys
    assert not any(
        key.startswith("peticoes_request_") for key in button_keys | text_keys
    )
    assert all(not str(item.label).startswith("Resultado:") for item in app.expander)
    assert any(item.label == "Resultado da Petição" for item in app.expander)
    andamentos = [sql for sql in calls if "peticoes_andamentos" in sql.lower()]
    assert len(andamentos) == 1 and "ROW_NUMBER()" in andamentos[0]
    assert not any(
        "peticoes_documentos" in sql.lower() or "arquivo" in sql.lower()
        for sql in calls
    )
    assert not any(
        token in sql.lower()
        for sql in calls
        for token in (
            "representacoes",
            "oficios",
            "ouvidoria",
            "memorandos",
            "portarias",
        )
    )
    app.text_area(key=f"peticoes_andamento_texto_{second['id']}").set_value(
        "Encaminhada à DIAFI."
    )
    app = app.run()
    app.button(key=f"peticoes_andamento_salvar_{second['id']}").click()
    app = app.run()
    assert not app.exception
    timeline = PeticoesStore(store).progress(second["id"])
    assert timeline[0]["descricao"] == "Encaminhada à DIAFI."
    assert app.text_area(key=f"peticoes_andamento_texto_{second['id']}").value == ""
    assert app.session_state[f"peticoes_andamento_upload_nonce_{second['id']}"] == 1
    assert any("Andamento registrado com sucesso." in item.value for item in app.success)
    assert all(
        item["descricao"] != "Encaminhada à DIAFI."
        for item in PeticoesStore(store).progress(first["id"])
    )


def test_reader_can_see_followup_but_cannot_register_progress(store):
    from services.peticoes import create

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
    principal = _principal(store)
    create(
        store, _payload(store), principal, ("peticao.pdf", "application/pdf", _pdf())
    )
    _HOLD["store"] = store
    _HOLD["principal"] = resolve_principal(store, {"email": "reader@test.local"})
    app = _open()
    assert not app.exception
    assert "116439/26" in " ".join(item.value for item in app.markdown)
    assert not any(
        str(item.key).startswith("peticoes_andamento_salvar_") for item in app.button
    )
    app.radio(key="peticoes_secao").set_value("Cadastrar Petição").run()
    assert any("visualização" in item.value for item in app.info)


def test_manual_registration_stores_one_request_block(store):
    from services.peticoes import texto_pedidos

    signer = next(row["id"] for row in store.catalog("procuradores") if row["ativo"])
    _HOLD["store"] = store
    _HOLD["principal"] = _principal(store)
    app = _open()
    app.radio(key="peticoes_secao").set_value("Cadastrar Petição")
    app = app.run()
    assert app.radio(key="peticoes_secao").value == "Cadastrar Petição"
    app = app.run()
    assert app.radio(key="peticoes_secao").value == "Cadastrar Petição"
    labels = [item.label for item in app.file_uploader]
    assert labels[0] == "PDF protocolado *"
    assert "Pedidos * (um por linha)" not in [item.label for item in app.text_area]
    app.file_uploader(key="peticoes_new_pdf").set_value(
        ("peticao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    app.text_input(key="peticoes_new_numero").set_value("116450/26")
    app.text_input(key="peticoes_new_destinatario").set_value("Presidência")
    app.text_input(key="peticoes_new_assunto").set_value("Fiscalização temática")
    app.text_area(key="peticoes_new_objeto").set_value("Apurar os fatos relevantes.")
    app.text_area(key="peticoes_new_pedidos").set_value(
        "a) Nota Recomendatória\nb.1) Planejamento\nb.2) Execução"
    )
    app.multiselect(key="peticoes_new_signatarios").set_value([signer])
    app = app.run()
    app.button(key="peticoes_create").click()
    app = app.run()
    assert not app.exception
    record = PeticoesStore(store).get(PeticoesStore(store).list()[0]["id"])
    assert len(record["pedidos"]) == 1
    assert (
        texto_pedidos(record["pedidos"])
        == "a) Nota Recomendatória\nb.1) Planejamento\nb.2) Execução"
    )


def test_editing_the_global_block_does_not_delete_old_requests(store):
    from services.peticoes import create

    principal = _principal(store)
    record = create(
        store,
        _payload(store, pedidos=["Pedido 1", "Pedido 2", "b.1) Subpedido"]),
        principal,
        ("peticao.pdf", "application/pdf", _pdf()),
    )
    _HOLD["store"] = store
    _HOLD["principal"] = principal
    app = _open()
    app.button(key=f"peticoes_edit_open_{record['id']}").click()
    app = app.run()
    assert not app.exception
    shown = app.text_area(key="peticoes_edit_pedidos").value
    assert "Pedido 1" in shown and "Pedido 2" in shown and "b.1) Subpedido" in shown
    assert [item.label for item in app.text_area].count("Pedidos da Petição *") == 1
    app.text_area(key="peticoes_edit_pedidos").set_value(
        "Pedido 1\n\nPedido 2\n\nb.1) Subpedido revisado"
    )
    app = app.run()
    app.button(key="FormSubmitter:peticoes_edit_form-Salvar alterações").click()
    app = app.run()
    assert not app.exception
    current = PeticoesStore(store).get(record["id"])
    assert len(current["pedidos"]) == 1
    assert "revisado" in current["pedidos"][0]["descricao"]
    with store.connection(read_only=True) as c:
        rows = list(
            c.execute(
                "SELECT excluido FROM peticoes_pedidos WHERE peticao_id=?",
                (record["id"],),
            )
        )
    assert len(rows) == 4
    assert sum(row["excluido"] for row in rows) == 3


def test_ai_fill_uses_one_call_and_one_request_field(store, monkeypatch):
    calls = []

    def fake(_pdf):
        calls.append(1)
        return {
            "numero_tramita": "116435/26",
            "data_protocolo": "2026-09-29",
            "destinatario": "Presidência",
            "natureza": "PROVIDENCIAS",
            "assunto": "Assunto da IA",
            "objeto": "Objeto da IA",
            "origem": "Origem da IA",
            "processo_tc": "",
            "signatarios": [],
            "pedidos": [
                {"descricao": "Pedido A"},
                {"descricao": "Pedido B"},
                {"descricao": "Pedido C"},
            ],
        }

    monkeypatch.setattr("services.ai_service.analisar_peticao_pdf", fake)
    signer = next(row["id"] for row in store.catalog("procuradores") if row["ativo"])
    _HOLD["store"] = store
    _HOLD["principal"] = _principal(store)
    app = _open()
    assert calls == []
    app.radio(key="peticoes_secao").set_value("Cadastrar Petição")
    app = app.run()
    assert calls == []
    app.file_uploader(key="peticoes_new_pdf").set_value(
        ("peticao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    assert calls == []
    app.text_input(key="peticoes_new_assunto").set_value("Assunto manual")
    app = app.run()
    app.button(key="peticoes_ai_fill").click()
    app = app.run()
    assert calls == [1]
    assert not app.exception
    assert app.text_input(key="peticoes_new_numero").value == "116435/26"
    assert app.text_input(key="peticoes_new_assunto").value == "Assunto manual"
    assert app.text_input(key="peticoes_new_origem").value == "Origem da IA"
    pedidos = app.text_area(key="peticoes_new_pedidos").value
    assert pedidos == "a) Pedido A\nb) Pedido B\nc) Pedido C"
    app = app.run()
    assert calls == [1]
    assert PeticoesStore(store).list() == []
    app.multiselect(key="peticoes_new_signatarios").set_value([signer])
    app = app.run()
    app.button(key="peticoes_create").click()
    app = app.run()
    assert calls == [1]
    saved = PeticoesStore(store).list()
    assert len(saved) == 1
    record = PeticoesStore(store).get(saved[0]["id"])
    assert len(record["pedidos"]) == 1
    assert "Pedido A" in record["pedidos"][0]["descricao"]
    assert "Pedido C" in record["pedidos"][0]["descricao"]


def test_invalid_ai_natureza_does_not_break_the_form(store, monkeypatch):
    def fake(_pdf):
        return {
            "numero_tramita": "116436/26",
            "data_protocolo": "",
            "destinatario": "",
            "natureza": "INVALIDA",
            "assunto": "",
            "objeto": "",
            "origem": "",
            "processo_tc": "",
            "signatarios": ["Nome inexistente"],
            "pedidos": [],
        }

    monkeypatch.setattr("services.ai_service.analisar_peticao_pdf", fake)
    _HOLD["store"] = store
    _HOLD["principal"] = _principal(store)
    app = _open()
    app.radio(key="peticoes_secao").set_value("Cadastrar Petição")
    app = app.run()
    app.file_uploader(key="peticoes_new_pdf").set_value(
        ("peticao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    app.button(key="peticoes_ai_fill").click()
    app = app.run()
    assert not app.exception
    assert app.selectbox(key="peticoes_new_natureza").value == "PROVIDENCIAS"
    assert PeticoesStore(store).list() == []


def test_ai_failure_does_not_persist(store, monkeypatch):
    def fake(_pdf):
        raise GeminiErro("O serviço de IA está indisponível no momento.")

    monkeypatch.setattr("services.ai_service.analisar_peticao_pdf", fake)
    _HOLD["store"] = store
    _HOLD["principal"] = _principal(store)
    app = _open()
    app.radio(key="peticoes_secao").set_value("Cadastrar Petição")
    app = app.run()
    app.button(key="peticoes_ai_fill").click()
    app = app.run()
    assert any("PDF protocolado" in item.value for item in app.warning)
    app.file_uploader(key="peticoes_new_pdf").set_value(
        ("peticao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    app.button(key="peticoes_ai_fill").click()
    app = app.run()
    assert any("indisponível" in item.value for item in app.error)
    assert PeticoesStore(store).list() == []
    app = app.run()
    assert PeticoesStore(store).list() == []
