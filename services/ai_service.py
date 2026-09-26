"""Isolated Gemini prototype. It does not persist files, prompts or answers.

The institutional API key stays in Streamlit Secrets (GEMINI_API_KEY).
User login remains the existing OAuth flow and is not used for this call.
"""

import base64
import json
import logging
import re
import time
import urllib.error
import urllib.request

LOGGER = logging.getLogger("mpc.ai")

GEMINI_MODEL = "gemini-3.6-flash"
GEMINI_ENDPOINT = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    + GEMINI_MODEL
    + ":generateContent"
)
TIMEOUT_SECONDS = 120
MAX_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)
RETRY_AFTER_MAX_SECONDS = 30
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_RESPONSE_BYTES = 1_000_000
MENSAGEM_NAO_CONFIGURADA = "Gemini API não configurada neste ambiente."
PROMPT_RESUMO = (
    "Analise exclusivamente o documento PDF fornecido.\n"
    "\n"
    "Produza um resumo objetivo, em português, contendo, quando existirem no documento:\n"
    "\n"
    "- objeto;\n"
    "- fatos principais;\n"
    "- possíveis irregularidades apontadas;\n"
    "- pedido cautelar;\n"
    "- pedidos finais.\n"
    "\n"
    "Não acrescente informações que não estejam no documento.\n"
    "Não faça inferências jurídicas além do conteúdo apresentado.\n"
    "Se determinada informação não estiver presente, "
    "informe que não foi identificada no documento."
)
_STATUS_TOKEN = re.compile(r"[A-Z0-9_]{1,40}")


class GeminiNaoConfigurada(RuntimeError):
    def __init__(self):
        super().__init__(MENSAGEM_NAO_CONFIGURADA)


class GeminiErro(RuntimeError):
    """Message is already safe to show to an administrator."""


def gemini_disponivel():
    return bool(_api_key())


def resumir_documento_pdf(pdf_bytes):
    """Send one PDF and the fixed prompt to Gemini. Nothing is stored."""
    document = _validar_pdf(pdf_bytes)
    key = _api_key()
    if not key:
        raise GeminiNaoConfigurada()
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            raw = _post(_request(document, key))
        except TimeoutError:
            # The call already waited TIMEOUT_SECONDS. Another round could
            # hold the page for several minutes, so this failure is final.
            LOGGER.warning("Chamada ao laboratório de IA expirou.")
            raise GeminiErro("O serviço de IA não respondeu a tempo.") from None
        except urllib.error.HTTPError as exc:
            code, status, retry_after = _detalhe_http(exc)
            if attempt < MAX_ATTEMPTS and _repetir_http(code, status):
                LOGGER.warning(
                    "Tentativa %s falhou com HTTP %s. Nova tentativa será realizada.",
                    attempt,
                    code,
                )
                _sleep(_espera(attempt, retry_after))
                continue
            LOGGER.warning(
                "Chamada ao laboratório de IA falhou (HTTP %s, %s).",
                code,
                status or "sem_status",
            )
            raise GeminiErro(_mensagem_http(code, status)) from None
        except urllib.error.URLError as exc:
            if isinstance(getattr(exc, "reason", None), TimeoutError):
                LOGGER.warning("Chamada ao laboratório de IA expirou.")
                raise GeminiErro("O serviço de IA não respondeu a tempo.") from None
            if attempt < MAX_ATTEMPTS:
                LOGGER.warning(
                    "Tentativa %s falhou sem conexão. Nova tentativa será realizada.",
                    attempt,
                )
                _sleep(_espera(attempt, None))
                continue
            LOGGER.warning("Chamada ao laboratório de IA sem conexão.")
            raise GeminiErro("Não foi possível conectar ao serviço de IA.") from None
        return _texto_resposta(raw)


def _api_key():
    try:
        import streamlit as st

        value = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return ""
    if not isinstance(value, str):
        return ""
    return value.strip()


def _validar_pdf(pdf_bytes):
    if not isinstance(pdf_bytes, (bytes, bytearray)):
        raise GeminiErro("O arquivo enviado não é um PDF válido.")
    document = bytes(pdf_bytes)
    if not document:
        raise GeminiErro("O arquivo PDF está vazio.")
    if len(document) > MAX_PDF_BYTES:
        megabytes = MAX_PDF_BYTES // (1024 * 1024)
        raise GeminiErro(f"O PDF excede o limite de {megabytes} MB deste teste.")
    if b"%PDF" not in document[:1024]:
        raise GeminiErro("O arquivo enviado não é um PDF válido.")
    return document


def _request(document, key):
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": PROMPT_RESUMO},
                    {
                        "inlineData": {
                            "mimeType": "application/pdf",
                            "data": base64.b64encode(document).decode("ascii"),
                        }
                    },
                ],
            }
        ]
    }
    return urllib.request.Request(
        GEMINI_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        },
        method="POST",
    )


def _sleep(seconds):
    time.sleep(seconds)


def _espera(attempt, retry_after):
    if retry_after is not None:
        return retry_after
    return RETRY_DELAYS_SECONDS[attempt - 1]


def _repetir_http(code, status):
    if code in {400, 401, 403, 404, 408, 413, 504}:
        return False
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return True
    return code in {500, 502, 503}


def _detalhe_http(exc):
    code = int(getattr(exc, "code", 0) or 0)
    status = _status_from_error(exc)
    retry_after = None
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        retry_after = _retry_after_seconds(exc)
    return code, status, retry_after


def _retry_after_seconds(exc):
    headers = getattr(exc, "headers", None)
    if not headers:
        return None
    try:
        raw = headers.get("Retry-After")
    except Exception:
        return None
    if raw is None:
        return None
    text = str(raw).strip()
    if not text.isdigit():
        return None
    seconds = int(text)
    if seconds > RETRY_AFTER_MAX_SECONDS:
        return None
    return seconds


def _post(request):
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read(MAX_RESPONSE_BYTES + 1)


def _texto_resposta(raw):
    if not raw or len(raw) > MAX_RESPONSE_BYTES:
        LOGGER.warning("Resposta do laboratório de IA vazia ou longa demais.")
        raise GeminiErro("Não foi possível interpretar a resposta do serviço de IA.")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        LOGGER.warning("Resposta do laboratório de IA não era JSON.")
        raise GeminiErro(
            "Não foi possível interpretar a resposta do serviço de IA."
        ) from None
    if not isinstance(data, dict):
        raise GeminiErro("Não foi possível interpretar a resposta do serviço de IA.")
    candidates = data.get("candidates") or []
    if not candidates:
        LOGGER.warning("Resposta do laboratório de IA sem candidatos.")
        if (data.get("promptFeedback") or {}).get("blockReason"):
            raise GeminiErro("A análise foi recusada pelo serviço de IA.")
        raise GeminiErro("A IA não retornou conteúdo.")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    texts = []
    for part in parts:
        if not isinstance(part, dict) or part.get("thought"):
            continue
        text = str(part.get("text") or "").strip()
        if text:
            texts.append(text)
    summary = "\n".join(texts).strip()
    if not summary:
        LOGGER.warning("Resposta do laboratório de IA sem texto.")
        raise GeminiErro("A IA não retornou conteúdo.")
    return summary


def _status_from_error(exc):
    try:
        raw = exc.read(4096)
    except Exception:
        return ""
    if not raw:
        return ""
    try:
        payload = json.loads(raw.decode("utf-8", errors="replace"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return ""
    status = str(error.get("status") or "")
    if _STATUS_TOKEN.fullmatch(status):
        return status
    return ""


def _mensagem_http(code, status):
    if code == 429 or status == "RESOURCE_EXHAUSTED":
        return "O limite de uso da Gemini API foi atingido."
    if code == 404 or status in {"NOT_FOUND", "UNIMPLEMENTED"}:
        return "O modelo de IA não está disponível neste ambiente."
    if code in {401, 403} or status in {"UNAUTHENTICATED", "PERMISSION_DENIED"}:
        return "A Gemini API recusou a credencial deste ambiente."
    if code in {400, 413} or status == "INVALID_ARGUMENT":
        return "O serviço de IA não conseguiu ler o PDF enviado."
    if code in {408, 504} or status == "DEADLINE_EXCEEDED":
        return "O serviço de IA não respondeu a tempo."
    if code in {500, 502, 503} or status in {"UNAVAILABLE", "INTERNAL"}:
        return "O serviço de IA está indisponível no momento."
    return "O serviço de IA recusou a solicitação."
