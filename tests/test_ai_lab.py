import json
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from database.store import ROOT
from services.access import resolve_principal
from services.ai_service import GeminiErro
from tests.access_testing import TEST_IDENTITY, enable_login, seed_access

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
KEY = "chave-de-teste-laboratorio"


def _visible(app):
    chunks = []
    for group in (
        app.markdown,
        app.subheader,
        app.caption,
        app.text,
        app.warning,
        app.error,
        app.success,
    ):
        for item in group:
            chunks.append(str(item.value))
    return "\n".join(chunks)


def _principal(store, email=TEST_IDENTITY["email"]):
    return resolve_principal(store, {"email": email})


LAB_SCRIPT = """
from services.access import Principal
from services.ai_lab_ui import render

render(
    Principal(
        id=1,
        nome="Administrador de Teste",
        email="admin@test.local",
        perfil="ADMINISTRADOR",
        ativo=True,
        pode_portarias=True,
        pode_agenda=True,
        pode_oficios=True,
        pode_admin=True,
        gabinetes=(),
    )
)
"""


def _lab():
    return AppTest.from_string(LAB_SCRIPT, default_timeout=30)


def test_lab_page_keeps_the_fixed_copy():
    source = Path("services/ai_lab_ui.py").read_text(encoding="utf-8")
    assert 'st.spinner("Analisando documento com IA...")' in source
    assert "Gerar resumo com IA" in source
    assert "Resumo gerado por IA" in source
    assert "Confira as informações no documento original." in source
    assert "session_state" not in source


def test_non_admin_cannot_open_the_lab(store):
    seed_access(
        store,
        email="comum.lab@test.local",
        perfil="USUARIO",
        pode_admin=False,
        pode_agenda=True,
    )
    principal = _principal(store, "comum.lab@test.local")
    with pytest.raises(ValueError, match="módulo"):
        from services.ai_lab_ui import render

        render(principal)


def test_non_admin_portal_does_not_show_the_lab(store, monkeypatch):
    seed_access(
        store,
        email="comum.lab.portal@test.local",
        perfil="USUARIO",
        pode_admin=False,
        pode_agenda=True,
    )
    monkeypatch.setattr(
        "services.access.oidc_identity",
        lambda: {
            "email": "comum.lab.portal@test.local",
            "name": "Comum",
            "email_verified": True,
        },
    )
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    portal = next(radio for radio in app.sidebar.radio if radio.key == "portal_module")
    assert "Administração" not in portal.options
    assert "Laboratório de IA" not in _visible(app)
    assert not any(radio.key == "admin_secao" for radio in app.radio)


def test_admin_opens_the_lab_from_administracao(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    app.button(key="open_admin").click().run()
    assert not app.exception
    options = next(radio for radio in app.radio if radio.key == "admin_secao").options
    assert options[0] == "Usuários"
    assert options[-1] == "Laboratório de IA"
    app.radio(key="admin_secao").set_value("Laboratório de IA").run()
    assert not app.exception
    visible = _visible(app)
    assert "Laboratório de IA" in visible
    assert "documentos fictícios ou autorizados" in visible
    assert "Gemini API não configurada neste ambiente." in visible
    uploader = next(
        item for item in app.file_uploader if item.label == "Selecionar PDF para teste"
    )
    assert uploader.allowed_type == [".pdf"]
    assert not any(button.label == "Gerar resumo com IA" for button in app.button)
    uploader.set_value(("ficticio.pdf", PDF, "application/pdf")).run()
    assert any(button.label == "Gerar resumo com IA" for button in app.button)
    assert "Arquivo: ficticio.pdf" in _visible(app)
    assert "Tamanho: " + str(len(PDF)) + " bytes" in _visible(app)
    app.button(key="ai_lab_gerar").click().run()
    assert not app.exception
    assert "Resumo gerado por IA" not in _visible(app)
    assert KEY not in _visible(app)


def test_lab_shows_summary_without_touching_the_database(store, monkeypatch):
    import services.ai_service as ai_service
    import streamlit as st

    before = Path(store.path).read_bytes()
    monkeypatch.setattr(st, "secrets", {"GEMINI_API_KEY": KEY})

    def fake(request):
        assert KEY not in request.full_url
        body = json.loads(request.data.decode("utf-8"))
        encoded = body["contents"][0]["parts"][1]["inlineData"]["data"]
        assert encoded
        return json.dumps(
            {
                "candidates": [
                    {"content": {"parts": [{"text": "Objeto: teste fictício."}]}}
                ]
            }
        ).encode()

    monkeypatch.setattr(ai_service, "_post", fake)
    app = _lab().run()
    assert "Gemini API não configurada neste ambiente." not in _visible(app)
    app.file_uploader[0].set_value(("ficticio.pdf", PDF, "application/pdf")).run()
    app.button(key="ai_lab_gerar").click().run()
    assert not app.exception
    visible = _visible(app)
    assert "Resumo gerado por IA" in visible
    assert "Objeto: teste fictício." in visible
    assert "Confira as informações no documento original." in visible
    assert KEY not in visible
    assert Path(store.path).read_bytes() == before


def test_lab_rejects_empty_and_invalid_pdf(store, monkeypatch):
    import services.ai_lab_ui as ai_lab_ui

    monkeypatch.setattr(ai_lab_ui, "gemini_disponivel", lambda: True)
    app = _lab().run()
    app.file_uploader[0].set_value(("vazio.pdf", b"", "application/pdf")).run()
    app.button(key="ai_lab_gerar").click().run()
    assert not app.exception
    assert "está vazio" in _visible(app)
    app.file_uploader[0].set_value(("nota.pdf", b"nao e pdf", "application/pdf")).run()
    app.button(key="ai_lab_gerar").click().run()
    assert not app.exception
    assert "não é um PDF válido" in _visible(app)


def test_lab_shows_api_error_without_traceback(store, monkeypatch):
    import services.ai_lab_ui as ai_lab_ui

    def fail(pdf_bytes):
        assert pdf_bytes.startswith(b"%PDF")
        raise GeminiErro("O limite de uso da Gemini API foi atingido.")

    monkeypatch.setattr(ai_lab_ui, "gemini_disponivel", lambda: True)
    monkeypatch.setattr(ai_lab_ui, "resumir_documento_pdf", fail)
    app = _lab().run()
    app.file_uploader[0].set_value(("ficticio.pdf", PDF, "application/pdf")).run()
    app.button(key="ai_lab_gerar").click().run()
    assert not app.exception
    visible = _visible(app)
    assert "limite de uso" in visible
    assert "Traceback" not in visible
    assert KEY not in visible


def test_lab_hides_unexpected_failures(store, monkeypatch, caplog):
    import logging

    import services.ai_lab_ui as ai_lab_ui

    def fail(pdf_bytes):
        raise RuntimeError("falha com " + KEY + " e corpo do pdf")

    monkeypatch.setattr(ai_lab_ui, "gemini_disponivel", lambda: True)
    monkeypatch.setattr(ai_lab_ui, "resumir_documento_pdf", fail)
    app = _lab().run()
    app.file_uploader[0].set_value(("ficticio.pdf", PDF, "application/pdf")).run()
    with caplog.at_level(logging.DEBUG):
        app.button(key="ai_lab_gerar").click().run()
    assert not app.exception
    visible = _visible(app)
    assert "Não foi possível concluir a análise do documento." in visible
    assert KEY not in visible
    assert "corpo do pdf" not in visible
    assert KEY not in caplog.text
    assert "corpo do pdf" not in caplog.text
