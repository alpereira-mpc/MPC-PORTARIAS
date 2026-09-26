import base64
import email
import io
import json
import logging
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

import services.ai_service as ai_service
from services.ai_service import (
    GEMINI_MODEL,
    GeminiErro,
    GeminiNaoConfigurada,
    gemini_disponivel,
    resumir_documento_pdf,
)

ROOT = Path(__file__).resolve().parents[1]
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
KEY = "chave-de-teste-laboratorio"
PROMPT_MARK = "Não faça inferências jurídicas além do conteúdo apresentado."


def _key(monkeypatch, value=KEY):
    import streamlit as st

    monkeypatch.setattr(st, "secrets", {"GEMINI_API_KEY": value})


def _http_error(url, code, status, message="detalhe interno"):
    body = json.dumps(
        {"error": {"code": code, "status": status, "message": message + " " + KEY}}
    ).encode()
    return urllib.error.HTTPError(
        url,
        code,
        "erro",
        email.message_from_string(""),
        io.BytesIO(body),
    )


def test_missing_key_does_not_call_the_network(monkeypatch):
    called = []
    monkeypatch.setattr(ai_service, "_post", lambda request: called.append(request))
    with pytest.raises(GeminiNaoConfigurada, match="não configurada neste ambiente"):
        resumir_documento_pdf(PDF)
    assert called == []
    assert gemini_disponivel() is False


def test_secrets_failure_stays_unconfigured(monkeypatch):
    import streamlit as st

    class Boom(dict):
        def get(self, *args, **kwargs):
            raise FileNotFoundError("secrets ausentes")

    monkeypatch.setattr(st, "secrets", Boom())
    assert gemini_disponivel() is False
    with pytest.raises(GeminiNaoConfigurada):
        resumir_documento_pdf(PDF)


def test_non_string_secret_stays_unconfigured(monkeypatch):
    import streamlit as st

    monkeypatch.setattr(st, "secrets", {"GEMINI_API_KEY": {"valor": KEY}})
    assert gemini_disponivel() is False


def test_blank_secret_stays_unconfigured(monkeypatch):
    _key(monkeypatch, "   ")
    assert gemini_disponivel() is False


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (None, "não é um PDF válido"),
        ("texto", "não é um PDF válido"),
        (b"", "está vazio"),
        (b"apenas texto", "não é um PDF válido"),
    ],
)
def test_rejects_invalid_pdf_before_the_network(monkeypatch, payload, message):
    called = []
    _key(monkeypatch)
    monkeypatch.setattr(ai_service, "_post", lambda request: called.append(request))
    with pytest.raises(GeminiErro, match=message):
        resumir_documento_pdf(payload)
    assert called == []


def test_rejects_pdf_above_the_prototype_limit(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setattr(ai_service, "MAX_PDF_BYTES", 16)
    with pytest.raises(GeminiErro, match="excede o limite"):
        resumir_documento_pdf(b"%PDF-1.4" + b"x" * 20)


def test_default_pdf_limit_is_ten_megabytes():
    assert ai_service.MAX_PDF_BYTES == 10 * 1024 * 1024


def test_sends_pdf_prompt_and_model_without_putting_the_key_in_the_url(
    monkeypatch, caplog
):
    seen = {}

    def fake(request):
        seen["url"] = request.full_url
        seen["headers"] = {
            name.lower(): value for name, value in request.header_items()
        }
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Objeto: exemplo."},
                                {"text": "Pedido final: arquivamento."},
                            ]
                        }
                    }
                ]
            }
        ).encode()

    _key(monkeypatch)
    monkeypatch.setattr(ai_service, "_post", fake)
    with caplog.at_level(logging.DEBUG):
        summary = resumir_documento_pdf(PDF)
    assert summary == "Objeto: exemplo.\nPedido final: arquivamento."
    assert seen["url"] == ai_service.GEMINI_ENDPOINT
    assert GEMINI_MODEL in seen["url"]
    assert seen["url"].count(GEMINI_MODEL) == 1
    assert "key=" not in seen["url"]
    assert KEY not in seen["url"]
    assert seen["headers"]["x-goog-api-key"] == KEY
    parts = seen["body"]["contents"][0]["parts"]
    assert parts[0]["text"] == ai_service.PROMPT_RESUMO
    assert PROMPT_MARK in parts[0]["text"]
    assert parts[1]["inlineData"]["mimeType"] == "application/pdf"
    assert base64.b64decode(parts[1]["inlineData"]["data"]) == PDF
    assert KEY not in caplog.text
    assert "1 0 obj" not in caplog.text


def test_timeout(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setattr(
        ai_service, "_post", lambda request: (_ for _ in ()).throw(TimeoutError())
    )
    with pytest.raises(GeminiErro, match="não respondeu a tempo"):
        resumir_documento_pdf(PDF)


def test_wrapped_timeout(monkeypatch):
    _key(monkeypatch)

    def fail(request):
        raise urllib.error.URLError(TimeoutError("timed out"))

    monkeypatch.setattr(ai_service, "_post", fail)
    with pytest.raises(GeminiErro, match="não respondeu a tempo"):
        resumir_documento_pdf(PDF)


def test_connection_failure_hides_the_cause(monkeypatch, caplog):
    _key(monkeypatch)

    def fail(request):
        raise urllib.error.URLError(
            ConnectionError("recusou " + KEY + " " + PDF.decode())
        )

    monkeypatch.setattr(ai_service, "_post", fail)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(GeminiErro, match="Não foi possível conectar") as caught:
            resumir_documento_pdf(PDF)
    assert KEY not in str(caught.value)
    assert "1 0 obj" not in str(caught.value)
    assert KEY not in caplog.text
    assert "1 0 obj" not in caplog.text


@pytest.mark.parametrize(
    ("code", "status", "message"),
    [
        (429, "RESOURCE_EXHAUSTED", "limite de uso"),
        (404, "NOT_FOUND", "não está disponível"),
        (403, "PERMISSION_DENIED", "recusou a credencial"),
        (400, "INVALID_ARGUMENT", "não conseguiu ler o PDF"),
        (503, "UNAVAILABLE", "indisponível"),
        (418, "WEIRD", "recusou a solicitação"),
    ],
)
def test_http_errors_are_safe(monkeypatch, caplog, code, status, message):
    _key(monkeypatch)

    def fail(request):
        raise _http_error(request.full_url, code, status)

    monkeypatch.setattr(ai_service, "_post", fail)
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(GeminiErro, match=message) as caught:
            resumir_documento_pdf(PDF)
    assert KEY not in str(caught.value)
    assert KEY not in caplog.text
    assert "detalhe interno" not in str(caught.value)
    assert "detalhe interno" not in caplog.text


def test_empty_response(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_post",
        lambda request: json.dumps({"candidates": []}).encode(),
    )
    with pytest.raises(GeminiErro, match="não retornou conteúdo"):
        resumir_documento_pdf(PDF)


def test_blocked_response(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_post",
        lambda request: json.dumps(
            {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}
        ).encode(),
    )
    with pytest.raises(GeminiErro, match="recusada pelo serviço"):
        resumir_documento_pdf(PDF)


def test_thought_parts_are_not_shown(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_post",
        lambda request: json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "raciocinio interno", "thought": True},
                                {"text": "Objeto: somente o resumo."},
                            ]
                        }
                    }
                ]
            }
        ).encode(),
    )
    assert resumir_documento_pdf(PDF) == "Objeto: somente o resumo."


def test_blank_text_is_an_empty_response(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setattr(
        ai_service,
        "_post",
        lambda request: json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "   "}]}}]}
        ).encode(),
    )
    with pytest.raises(GeminiErro, match="não retornou conteúdo"):
        resumir_documento_pdf(PDF)


def test_invalid_json_response(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setattr(ai_service, "_post", lambda request: b"<html>segredo")
    with pytest.raises(GeminiErro, match="interpretar a resposta") as caught:
        resumir_documento_pdf(PDF)
    assert "segredo" not in str(caught.value)


def test_oversized_response(monkeypatch):
    _key(monkeypatch)
    monkeypatch.setattr(ai_service, "MAX_RESPONSE_BYTES", 8)
    monkeypatch.setattr(ai_service, "_post", lambda request: b'{"candidates":[]}')
    with pytest.raises(GeminiErro, match="interpretar a resposta"):
        resumir_documento_pdf(PDF)


def test_model_and_prompt_stay_in_the_service():
    assert GEMINI_MODEL == "gemini-3.6-flash"
    assert ai_service.GEMINI_ENDPOINT.count(GEMINI_MODEL) == 1
    services = ROOT / "services"
    model_hits = []
    prompt_hits = []
    for path in services.rglob("*.py"):
        if path.name == "ai_service.py":
            continue
        text = path.read_text(encoding="utf-8")
        if GEMINI_MODEL in text:
            model_hits.append(path.name)
        if "pedido cautelar" in text:
            prompt_hits.append(path.name)
    assert model_hits == []
    assert prompt_hits == []
    ui = (services / "ai_lab_ui.py").read_text(encoding="utf-8")
    for needle in (
        "generativelanguage",
        "x-goog-api-key",
        "GEMINI_API_KEY",
        "inlineData",
        GEMINI_MODEL,
    ):
        assert needle not in ui


def test_example_secrets_do_not_assign_a_key():
    text = (ROOT / ".streamlit" / "secrets.example.toml").read_text(encoding="utf-8")
    assert "GEMINI_API_KEY" in text
    for line in text.splitlines():
        assert not line.strip().startswith("GEMINI_API_KEY")


def test_service_does_not_import_product_modules():
    script = """
import sys
import services.ai_service
banned = (
    "services.representacoes",
    "services.oficios",
    "services.memorandos",
    "services.agenda",
    "database.representacoes",
)
found = [name for name in banned if name in sys.modules]
print(",".join(found))
raise SystemExit(0 if not found else 1)
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_stable_modules_do_not_reference_the_lab():
    services = ROOT / "services"
    for name in (
        "representacoes.py",
        "representacoes_ui.py",
        "oficios.py",
        "oficios_ui.py",
        "memorandos.py",
        "memorandos_ui.py",
        "agenda.py",
        "agenda_ui.py",
        "pending.py",
        "pending_ui.py",
        "search.py",
        "search_ui.py",
    ):
        text = (services / name).read_text(encoding="utf-8")
        assert "ai_service" not in text
        assert "ai_lab_ui" not in text
        assert "Laboratório de IA" not in text
