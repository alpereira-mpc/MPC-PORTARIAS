"""Assisted AI fill for received ofícios. No live Gemini call."""

from datetime import date
import logging
from pathlib import Path

from streamlit.testing.v1 import AppTest

import services.ai_service as ai_service
from services.access import Principal
from services.ai_service import GeminiErro
from tests.test_oficios_extraction import SAMPLE, _pdf

FORM = """
import streamlit as st
from services.oficios_ui import received_form


class Service:
    def save(self, record, uploads=None):
        st.session_state["saved_record"] = dict(record)
        st.session_state["saved_files"] = [
            (name, content) for name, content in (uploads or [])
        ]
        return {
            "id": "of-1",
            "direcao": "RECEBIDO",
            "status": record.get("status"),
            "assunto": record.get("assunto") or "",
        }

    def get(self, identifier):
        row = dict(st.session_state["saved_record"])
        row["id"] = identifier
        return row


st.session_state["oficio_gabinete_member"] = "membro-1"
received_form(Service(), {})
"""

_HOLD = {}

FILLED = {
    "numero_externo": "Ofício n. 15/2026",
    "remetente": "João da Silva",
    "cargo_remetente": "Procurador de Justiça",
    "instituicao": "Ministério Público de Contas",
    "assunto": "Solicitação de informações",
    "processo": "0001/2026",
    "data": "2026-09-10",
    "prazo": "2026-10-01",
    "providencias_sugeridas": [
        "Avaliar resposta ao órgão remetente até a data indicada no Ofício.",
        "Verificar os documentos solicitados pelo remetente.",
    ],
    "data_recebimento": "1999-01-01",
    "status": "Arquivado",
    "observacoes": "texto que não pode ir para o campo",
}


def _form():
    return AppTest.from_string(FORM, default_timeout=30).run()


def _upload(app, content=None):
    app.file_uploader(key="oficio_received_uploader").set_value(
        ("oficio.pdf", content or _pdf(SAMPLE), "application/pdf")
    )
    return app.run()


def _submit(app):
    for button in app.button:
        if button.label == "Registrar recebido":
            button.click()
            return app.run()
    raise AssertionError("Registrar recebido")


def _texts(app):
    return "\n".join(item.value for item in app.text)


def test_manual_registration_does_not_call_ai(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ai_service,
        "extrair_dados_oficio_pdf",
        lambda pdf: calls.append(pdf),
    )
    app = _form()
    app.text_input(key="oficio_received_numero_externo").set_value("9/2026")
    app = app.run()
    app = _submit(app)
    saved = app.session_state["saved_record"]
    assert saved["numero_externo"] == "9/2026"
    assert saved["direcao"] == "RECEBIDO"
    assert saved["status"] == "Recebido"
    assert calls == []
    assert "saved_record" in app.session_state


def test_unreadable_pdf_still_asks_for_manual_entry(monkeypatch):
    from tests.test_oficios_extraction import _blank_pdf

    monkeypatch.setattr(
        ai_service,
        "extrair_dados_oficio_pdf",
        lambda _pdf: (_ for _ in ()).throw(AssertionError("IA")),
    )
    app = _upload(_form(), _blank_pdf())
    app.button(key="oficio_received_analisar_pdf").click()
    app = app.run()
    assert any("digitalizado" in item.value for item in app.warning)
    assert "saved_record" not in app.session_state


def test_current_pdf_analyzer_still_fills_the_form(monkeypatch):
    def forbidden(_pdf):
        raise AssertionError("IA não deve ser chamada pelo analisador atual")

    monkeypatch.setattr(ai_service, "extrair_dados_oficio_pdf", forbidden)
    app = _upload(_form())
    app.button(key="oficio_received_analisar_pdf").click()
    app = app.run()
    assert "123/2026" in app.text_input(key="oficio_received_numero_externo").value
    assert "Solicitacao" in app.text_input(key="oficio_received_assunto").value
    assert any("Documento analisado" in item.value for item in app.success)
    assert "saved_record" not in app.session_state


def test_ai_button_fills_fields_only_on_click_and_registration_stays_human(
    monkeypatch,
):
    calls = []

    def fake(pdf_bytes):
        calls.append(pdf_bytes)
        return dict(FILLED)

    monkeypatch.setattr(ai_service, "extrair_dados_oficio_pdf", fake)
    app = _upload(_form())
    assert calls == []
    app.run()
    assert calls == []
    app.button(key="oficio_received_analisar_ia").click()
    app = app.run()
    assert len(calls) == 1
    assert calls[0].startswith(b"%PDF-")
    assert app.text_input(key="oficio_received_numero_externo").value == (
        FILLED["numero_externo"]
    )
    assert app.text_input(key="oficio_received_remetente").value == FILLED["remetente"]
    assert app.text_input(key="oficio_received_cargo_remetente").value == (
        FILLED["cargo_remetente"]
    )
    assert app.text_input(key="oficio_received_instituicao").value == (
        FILLED["instituicao"]
    )
    assert app.text_input(key="oficio_received_assunto").value == FILLED["assunto"]
    assert app.text_input(key="oficio_received_processo").value == FILLED["processo"]
    dates = {item.label: item.value for item in app.date_input}
    assert dates["Data do documento"] == date(2026, 9, 10)
    assert dates["Prazo opcional"] == date(2026, 10, 1)
    assert dates["Data de recebimento"] == date.today()
    assert app.selectbox[0].value == "Recebido"
    assert app.text_area[0].value in ("", None)
    visible = _texts(app)
    assert "1. " + FILLED["providencias_sugeridas"][0] in visible
    assert "2. " + FILLED["providencias_sugeridas"][1] in visible
    assert "3. " not in visible
    assert any("sujeitas à avaliação" in item.value for item in app.caption)
    assert "saved_record" not in app.session_state
    app.run()
    assert len(calls) == 1
    app = _submit(app)
    saved = app.session_state["saved_record"]
    assert saved["numero_externo"] == FILLED["numero_externo"]
    assert saved["data"] == "2026-09-10"
    assert saved["prazo"] == "2026-10-01"
    assert saved["data_recebimento"] == date.today().isoformat()
    assert saved["status"] == "Recebido"
    assert saved["observacoes"] == ""
    assert "providencias_sugeridas" not in saved
    assert len(calls) == 1
    assert app.session_state["saved_files"][0][0] == "oficio.pdf"


def test_partial_ai_response_leaves_missing_fields_empty(monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "extrair_dados_oficio_pdf",
        lambda _pdf: {"numero_externo": "8/2026", "status": "Arquivado"},
    )
    app = _upload(_form())
    app.button(key="oficio_received_analisar_ia").click()
    app = app.run()
    assert app.text_input(key="oficio_received_numero_externo").value == "8/2026"
    assert app.text_input(key="oficio_received_assunto").value == ""
    assert app.text_input(key="oficio_received_remetente").value == ""
    dates = {item.label: item.value for item in app.date_input}
    assert dates["Prazo opcional"] is None
    assert dates["Data de recebimento"] == date.today()
    assert app.selectbox[0].value == "Recebido"
    assert "Nenhuma providência específica" in _texts(app)
    assert "saved_record" not in app.session_state


def test_invalid_ai_result_keeps_the_form_usable(monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "extrair_dados_oficio_pdf",
        lambda _pdf: ["inesperado"],
    )
    app = _upload(_form())
    app.text_input(key="oficio_received_assunto").set_value("Digitado")
    app = app.run()
    app.button(key="oficio_received_analisar_ia").click()
    app = app.run()
    assert any("interpretar a resposta" in item.value for item in app.error)
    assert app.text_input(key="oficio_received_assunto").value == "Digitado"
    assert app.button(key="oficio_received_analisar_pdf")
    app = _submit(app)
    assert app.session_state["saved_record"]["assunto"] == "Digitado"
    assert app.session_state["saved_record"]["status"] == "Recebido"


def test_api_and_timeout_errors_keep_manual_registration(monkeypatch):
    state = {"error": GeminiErro("O serviço de IA está indisponível no momento.")}

    def fake(_pdf):
        raise state["error"]

    monkeypatch.setattr(ai_service, "extrair_dados_oficio_pdf", fake)
    app = _upload(_form())
    app.button(key="oficio_received_analisar_ia").click()
    app = app.run()
    assert any("indisponível" in item.value for item in app.error)
    assert "saved_record" not in app.session_state
    state["error"] = GeminiErro("O serviço de IA não respondeu a tempo.")
    app.button(key="oficio_received_analisar_ia").click()
    app = app.run()
    assert any("não respondeu a tempo" in item.value for item in app.error)
    app.text_input(key="oficio_received_numero_externo").set_value("4/2026")
    app = app.run()
    app = _submit(app)
    assert app.session_state["saved_record"]["numero_externo"] == "4/2026"


def test_unexpected_ai_failure_does_not_log_the_document(monkeypatch, caplog):
    def fake(_pdf):
        raise RuntimeError("Maria Aparecida dos Santos e o texto integral do PDF")

    monkeypatch.setattr(ai_service, "extrair_dados_oficio_pdf", fake)
    app = _upload(_form())
    with caplog.at_level(logging.DEBUG, logger="mpc.ai"):
        app.button(key="oficio_received_analisar_ia").click()
        app = app.run()
    assert any("Preencha os dados manualmente" in item.value for item in app.error)
    assert "Maria" not in caplog.text
    assert "texto integral" not in caplog.text
    assert "RuntimeError" in caplog.text
    app.text_input(key="oficio_received_assunto").set_value("Manual")
    app = app.run()
    app = _submit(app)
    assert app.session_state["saved_record"]["assunto"] == "Manual"


def test_user_without_oficios_permission_does_not_get_the_ai_button(store):
    principal = Principal(
        id=2,
        nome="Sem Ofícios",
        email="sem.oficios@test.local",
        perfil="USUARIO",
        ativo=True,
        pode_portarias=False,
        pode_agenda=True,
        pode_oficios=False,
        pode_admin=False,
        gabinetes=(),
    )
    _HOLD["store"] = store
    _HOLD["principal"] = principal

    def page():
        from services.oficios_ui import render
        from tests.test_oficios_ai import _HOLD

        render(_HOLD["store"], _HOLD["principal"])

    app = AppTest.from_function(page, default_timeout=30).run()
    labels = [button.label for button in app.button]
    assert "✨ Analisar com IA" not in labels
    assert "Analisar PDF" not in labels
    assert any("autorizado" in item.value for item in app.error)


def test_ai_handler_does_not_persist_or_call_other_modules():
    source = Path("services/oficios_ui.py").read_text(encoding="utf-8")
    handler = source.split("if analyze_ai:", 1)[1].split(
        'if st.session_state.get("oficio_received_ok")', 1
    )[0]
    assert "extrair_dados_oficio_pdf" in handler
    assert "service.save" not in handler
    for needle in (
        "agenda",
        "tarefa",
        "pendenc",
        "representac",
        "ouvidoria",
        "memorando",
        "portaria",
    ):
        assert needle not in handler.lower()
    service = Path("services/oficios.py").read_text(encoding="utf-8")
    assert "ai_service" not in service
    assert "extrair_dados_oficio_pdf" not in service
