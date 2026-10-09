"""Direct Representação form behavior with mocked AI and real isolated persistence."""

from datetime import date
from io import BytesIO
import unicodedata

from pypdf import PdfWriter
from streamlit.testing.v1 import AppTest

from services import ai_service
from services.representacoes import (
    RELATORES,
    create,
    documents,
    download,
    get,
    list_records,
)
from tests.test_representacoes import _payload, _principal, _server

_HOLD = {}


def _page():
    from services.representacoes_ui import render
    from tests.test_representacoes_ai_ui import _HOLD

    render(_HOLD["store"], _HOLD["principal"])


def _open_direct(store, principal):
    _HOLD.update(store=store, principal=principal)
    app = AppTest.from_function(_page, default_timeout=30).run()
    return app.button(key="rep_direct_new").click().run()


def _ai_button(app):
    found = [button for button in app.button if "Preencher com IA" in button.label]
    assert len(found) == 1
    return found[0]


def _pdf(size=72):
    writer = PdfWriter()
    writer.add_blank_page(width=size, height=size)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _unaccent(text):
    return "".join(
        char
        for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    ).upper()


def _suggestion(**changes):
    data = {
        "titulo": "Fiscalização de contratação pública",
        "objeto": "Apurar possível irregularidade na contratação.",
        "origem": "DE_OFICIO",
        "data_abertura": "2026-09-10",
        "representado": "Município de Exemplo",
        "tema": "Licitações",
        "procurador_responsavel": "",
        "prioridade": "",
        "procuradores_signatarios": [],
        "assessores": [],
        "observacoes_internas": "",
        "numero_processo": "TC 012345/26",
        "data_protocolo": "2026-09-20",
        "relator": RELATORES[1],
        "fase_processual": "INSTRUCAO",
        "possui_medida_cautelar": "SIM",
        "observacoes_protocolo": "",
    }
    data.update(changes)
    return data


def test_direct_ai_runs_only_on_click_and_populates_safe_fields(store, monkeypatch):
    principal = _principal(store, email="ai-direct-fill@test.local")
    people = [person for person in store.catalog("procuradores") if person["ativo"]]
    assert len(people) >= 2
    assessor_id = _server(store, "João Teste")
    calls = []
    pdf = _pdf()

    def fake(document):
        calls.append(document)
        return _suggestion(
            procurador_responsavel=_unaccent(people[1]["nome"]),
            procuradores_signatarios=[
                _unaccent(people[0]["nome"]),
                "Pessoa inexistente",
            ],
            assessores=["JOAO TESTE", "Assessor inexistente"],
            relator=_unaccent(RELATORES[1]),
        )

    monkeypatch.setattr(ai_service, "extrair_dados_representacao_pdf", fake)
    app = _open_direct(store, principal)
    assert not app.exception
    assert calls == []
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("representacao.pdf", pdf, "application/pdf")
    )
    app = app.run()
    assert calls == []
    app.text_input(key="rep_direct_base_representado").set_value("Representado manual")
    app = app.run()
    app = _ai_button(app).click().run()
    assert not app.exception
    assert calls == [pdf]
    assert app.text_input(key="rep_direct_base_titulo").value == (
        "Fiscalização de contratação pública"
    )
    assert app.text_input(key="rep_direct_base_representado").value == (
        "Representado manual"
    )
    assert app.date_input(key="rep_direct_base_abertura").value == date(2026, 9, 10)
    assert app.date_input(key="rep_direct_prot_data").value == date(2026, 9, 20)
    assert app.selectbox(key="rep_direct_base_resp").value == people[1]["id"]
    assert app.multiselect(key="rep_direct_base_sign").value == [people[0]["id"]]
    assert app.multiselect(key="rep_direct_base_ass").value == [assessor_id]
    assert app.selectbox(key="rep_direct_prot_rel").value == RELATORES[1]
    assert app.radio(key="rep_direct_prot_cautelar").value is True
    assert app.selectbox(key="rep_direct_base_prioridade").value == "NORMAL"
    assert list_records(store, {}) == []
    app = app.run()
    assert calls == [pdf]
    assert list_records(store, {}) == []


def test_direct_ai_pdf_change_drops_old_suggestions_and_keeps_manual_edits(
    store, monkeypatch
):
    principal = _principal(store, email="ai-pdf-change@test.local")
    first, second = _pdf(72), _pdf(144)
    calls = []

    def fake(document):
        calls.append(document)
        return _suggestion(
            titulo="Primeiro PDF" if document == first else "Segundo PDF"
        )

    monkeypatch.setattr(ai_service, "extrair_dados_representacao_pdf", fake)
    app = _open_direct(store, principal)
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("primeiro.pdf", first, "application/pdf")
    )
    app = app.run()
    app = _ai_button(app).click().run()
    assert app.text_input(key="rep_direct_base_titulo").value == "Primeiro PDF"
    app.text_input(key="rep_direct_base_representado").set_value("Correção manual")
    app = app.run()
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("segundo.pdf", second, "application/pdf")
    )
    app = app.run()
    assert calls == [first]
    assert app.text_input(key="rep_direct_base_titulo").value != "Primeiro PDF"
    assert app.text_input(key="rep_direct_base_representado").value == "Correção manual"
    app = _ai_button(app).click().run()
    assert calls == [first, second]
    assert app.text_input(key="rep_direct_base_titulo").value == "Segundo PDF"
    assert app.text_input(key="rep_direct_base_representado").value == "Correção manual"
    assert list_records(store, {}) == []


def test_direct_ai_missing_fields_and_error_do_not_persist(store, monkeypatch):
    principal = _principal(store, email="ai-errors@test.local")
    calls = []

    def fake(document):
        calls.append(document)
        if len(calls) == 1:
            raise ai_service.GeminiErro("Serviço de IA indisponível.")
        return _suggestion(
            titulo="",
            numero_processo="",
            relator="",
            possui_medida_cautelar="NAO_IDENTIFICADO",
        )

    monkeypatch.setattr(ai_service, "extrair_dados_representacao_pdf", fake)
    app = _open_direct(store, principal)
    app = _ai_button(app).click().run()
    assert calls == []
    assert any("PDF final" in item.value for item in app.warning)
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("representacao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    app = _ai_button(app).click().run()
    assert any("indisponível" in item.value for item in app.error)
    assert list_records(store, {}) == []
    app = _ai_button(app).click().run()
    assert not app.exception
    messages = " ".join(item.value for item in app.warning) + " ".join(
        item.value for item in app.caption
    )
    assert "Título" in messages
    assert "Número do processo" in messages
    assert "Relator" in messages
    assert app.radio(key="rep_direct_prot_cautelar").value is False
    assert list_records(store, {}) == []


def test_direct_ai_cancel_clears_pending_state_and_does_not_create_record(
    store, monkeypatch
):
    principal = _principal(store, email="ai-cancel@test.local")
    monkeypatch.setattr(
        ai_service, "extrair_dados_representacao_pdf", lambda _: _suggestion()
    )
    app = _open_direct(store, principal)
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("representacao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    app = _ai_button(app).click().run()
    assert app.text_input(key="rep_direct_base_titulo").value
    app = app.button(key="rep_direct_cancel").click().run()
    assert "representacoes_direct" not in app.session_state
    assert all(
        key not in app.session_state
        for key in (
            "rep_direct_ai_file",
            "rep_direct_ai_pending",
            "rep_direct_ai_applied",
            "rep_direct_ai_missing",
            "rep_direct_ai_notice",
            "rep_direct_ai_dirty",
        )
    )
    assert list_records(store, {}) == []
    app = app.button(key="rep_direct_new").click().run()
    assert app.text_input(key="rep_direct_base_titulo").value == ""


def test_direct_ai_saves_original_pdf_once_and_does_not_run_on_save(store, monkeypatch):
    import services.representacoes_ui as ui

    principal = _principal(store, email="ai-save@test.local")
    pdf = _pdf()
    calls = []
    people = [person for person in store.catalog("procuradores") if person["ativo"]]

    def fake(document):
        calls.append(document)
        return _suggestion(procurador_responsavel=people[0]["nome"])

    monkeypatch.setattr(ai_service, "extrair_dados_representacao_pdf", fake)
    monkeypatch.setattr(ui.st, "rerun", lambda **_kwargs: None)
    app = _open_direct(store, principal)
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("final.pdf", pdf, "application/pdf")
    )
    app = app.run()
    app = _ai_button(app).click().run()
    assert list_records(store, {}) == []
    app = app.button(key="rep_direct_save").click().run()
    assert not app.exception
    assert calls == [pdf]
    saved = list_records(store, {})
    assert len(saved) == 1
    record = get(store, saved[0]["id"])
    assert record["registro_origem"] == "DIRETO"
    official = [
        item
        for item in documents(store, record["id"])
        if item["tipo_documento"] == "REPRESENTACAO_FINAL"
    ]
    assert len(official) == 1
    assert download(store, official[0]["id"])["conteudo"] == pdf


def test_conversion_form_does_not_offer_direct_ai_action(store, monkeypatch):
    import services.representacoes_ui as ui

    monkeypatch.setattr(ui.st, "rerun", lambda **_kwargs: None)
    principal = _principal(store, email="ai-conversion@test.local")
    payload, *_ = _payload(store, titulo="Projeto para conversão")
    project = create(store, payload, principal)
    _HOLD.update(store=store, principal=principal)
    app = AppTest.from_function(_page, default_timeout=30).run()
    app = app.button(key=f"rep_open_{project['id']}").click().run()
    app = app.radio(key=f"rep_detail_section_{project['id']}").set_value("Gestão").run()
    app = app.button(key="rep_dt_prot").click().run()
    app = app.run()
    assert not any("Preencher com IA" in button.label for button in app.button)
    assert app.file_uploader(key=f"rep_prot_{project['id']}pdf")
    assert get(store, project["id"])["numero_processo"] is None


def test_direct_ai_respects_manual_select_date_and_radio_choices(store, monkeypatch):
    principal = _principal(store, email="ai-default-manual@test.local")
    monkeypatch.setattr(
        ai_service,
        "extrair_dados_representacao_pdf",
        lambda _: _suggestion(
            origem="OUVIDORIA",
            prioridade="URGENTE",
            data_protocolo="2026-09-20",
            possui_medida_cautelar="SIM",
        ),
    )
    app = _open_direct(store, principal)
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("representacao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    app = (
        app.selectbox(key="rep_direct_base_origem")
        .set_value("PROVOCACAO_EXTERNA")
        .run()
    )
    app = app.selectbox(key="rep_direct_base_prioridade").set_value("ALTA").run()
    app = app.date_input(key="rep_direct_prot_data").set_value(date(2026, 10, 1)).run()
    app = app.radio(key="rep_direct_prot_cautelar").set_value(True).run()
    app = app.radio(key="rep_direct_prot_cautelar").set_value(False).run()
    app = _ai_button(app).click().run()
    assert not app.exception
    assert app.selectbox(key="rep_direct_base_origem").value == ("PROVOCACAO_EXTERNA")
    assert app.selectbox(key="rep_direct_base_prioridade").value == "ALTA"
    assert app.date_input(key="rep_direct_prot_data").value == date(2026, 10, 1)
    assert app.radio(key="rep_direct_prot_cautelar").value is False
    assert app.text_input(key="rep_direct_base_titulo").value == (
        "Fiscalização de contratação pública"
    )
    assert list_records(store, {}) == []


def test_direct_upload_and_ai_action_precede_general_fields():
    import inspect
    import services.representacoes_ui as ui

    source = inspect.getsource(ui._direct_protocol_form)
    assert source.index("rep_direct_prot_pdf") < source.index(
        'key="rep_direct_ai_fill"'
    )
    assert source.index('key="rep_direct_ai_fill"') < source.index(
        '_form(store, prefix="rep_direct_base_")'
    )
    assert "show_upload=False" in source


def test_direct_ai_signer_uses_manually_retained_responsible(store, monkeypatch):
    principal = _principal(store, email="ai-sign-retained@test.local")
    people = [person for person in store.catalog("procuradores") if person["ativo"]]
    assert len(people) >= 2
    first, second = people[:2]
    monkeypatch.setattr(
        ai_service,
        "extrair_dados_representacao_pdf",
        lambda _: _suggestion(
            procurador_responsavel=first["nome"],
            procuradores_signatarios=[_unaccent(first["nome"])],
        ),
    )
    app = _open_direct(store, principal)
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("representacao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    app = app.selectbox(key="rep_direct_base_resp").set_value(second["id"]).run()
    app = _ai_button(app).click().run()
    assert not app.exception
    assert app.selectbox(key="rep_direct_base_resp").value == second["id"]
    assert app.multiselect(key="rep_direct_base_sign").value == [first["id"]]
    assert list_records(store, {}) == []


def test_direct_ai_unknown_caution_calls_for_explicit_review(store, monkeypatch):
    principal = _principal(store, email="ai-caution-unknown@test.local")
    monkeypatch.setattr(
        ai_service,
        "extrair_dados_representacao_pdf",
        lambda _: _suggestion(possui_medida_cautelar="NAO_IDENTIFICADO"),
    )
    app = _open_direct(store, principal)
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("representacao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    app = _ai_button(app).click().run()
    assert not app.exception
    assert app.radio(key="rep_direct_prot_cautelar").value is False
    review = " ".join(
        str(item.value)
        for group in (app.caption, app.warning, app.info)
        for item in group
    ).casefold()
    assert "cautelar" in review
    assert any(token in review for token in ("confer", "revis", "confirm", "confir"))


def test_direct_ai_keeps_manual_signer_when_ai_names_it_responsible(store, monkeypatch):
    principal = _principal(store, email="ai-manual-signer@test.local")
    people = [person for person in store.catalog("procuradores") if person["ativo"]]
    assert len(people) >= 2
    current_responsible, manual_signer = people[:2]
    monkeypatch.setattr(
        ai_service,
        "extrair_dados_representacao_pdf",
        lambda _: _suggestion(
            procurador_responsavel=manual_signer["nome"],
            procuradores_signatarios=[manual_signer["nome"]],
        ),
    )
    app = _open_direct(store, principal)
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("representacao.pdf", _pdf(), "application/pdf")
    )
    app = app.run()
    assert app.selectbox(key="rep_direct_base_resp").value == (
        current_responsible["id"]
    )
    app = (
        app.multiselect(key="rep_direct_base_sign")
        .set_value([manual_signer["id"]])
        .run()
    )
    app = _ai_button(app).click().run()
    assert not app.exception
    assert app.selectbox(key="rep_direct_base_resp").value == (
        current_responsible["id"]
    )
    assert app.multiselect(key="rep_direct_base_sign").value == [manual_signer["id"]]
    assert list_records(store, {}) == []


def test_direct_registration_works_without_ai_with_top_pdf_upload(store, monkeypatch):
    import services.representacoes_ui as ui

    principal = _principal(store, email="without-ai@test.local")
    pdf = _pdf()
    ai_calls = []

    def unexpected_ai(document):
        ai_calls.append(document)
        raise AssertionError("AI must run only on explicit click")

    monkeypatch.setattr(ai_service, "extrair_dados_representacao_pdf", unexpected_ai)
    monkeypatch.setattr(ui.st, "rerun", lambda **_kwargs: None)
    app = _open_direct(store, principal)
    app.file_uploader(key="rep_direct_prot_pdf").set_value(
        ("sem-ia.pdf", pdf, "application/pdf")
    )
    app = app.run()
    app = (
        app.text_input(key="rep_direct_base_titulo")
        .set_value("Representação cadastrada manualmente")
        .run()
    )
    app = app.text_input(key="rep_direct_prot_num").set_value("TC 035555/26").run()
    app = app.selectbox(key="rep_direct_prot_rel").set_value(RELATORES[0]).run()
    app = app.button(key="rep_direct_save").click().run()
    assert not app.exception
    assert ai_calls == []
    saved = list_records(store, {})
    assert len(saved) == 1
    record = get(store, saved[0]["id"])
    assert record["titulo"] == "Representação cadastrada manualmente"
    official = [
        item
        for item in documents(store, record["id"])
        if item["tipo_documento"] == "REPRESENTACAO_FINAL"
    ]
    assert len(official) == 1
    assert download(store, official[0]["id"])["conteudo"] == pdf
