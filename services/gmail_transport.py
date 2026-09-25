"""Gmail send for mpc@tce.pb.gov.br. Imported only after an explicit send."""

import base64
from email.message import EmailMessage
import json
import re

from services.email_transport import (
    GMAIL_SEND_SCOPE,
    SENDER_ADDRESS,
    SENDER_NAME,
    DeliveryRejected,
    DeliveryUncertain,
)

_UNCERTAIN = ("timeout", "timed out", "connection reset", "remote end closed", "temporarily unavailable")
_KNOWN_CODES = ("admin_policy_enforced", "access_denied", "invalid_grant", "insufficient_scope")
_SECRET = re.compile(r"(?i)(bearer\s+\S+|ya29\.[A-Za-z0-9_\-]+|refresh_token[\"'\s:=]+\S+|client_secret[\"'\s:=]+\S+)")


class GmailTransport:
    def __init__(self, settings):
        self.settings = settings

    def send(self, message):
        credentials = self._credentials()
        raw = _raw_message(message)
        try:
            from googleapiclient.discovery import build

            service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
            sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        except DeliveryRejected:
            raise
        except DeliveryUncertain:
            raise
        except Exception as exc:
            if _is_uncertain(exc):
                raise DeliveryUncertain("O provedor não confirmou o envio.") from None
            raise _rejected(exc) from None
        return str((sent or {}).get("id") or "")

    def _credentials(self):
        mode = self.settings.get("mode")
        if self.settings.get("sender") != SENDER_ADDRESS:
            raise DeliveryRejected("O remetente institucional deve ser mpc@tce.pb.gov.br.")
        if mode == "user_oauth":
            return _user_credentials(self.settings)
        return _service_credentials(self.settings)


def _user_credentials(settings):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise DeliveryRejected("Envio institucional ainda não configurado.") from exc
    credentials = Credentials(
        token=None,
        refresh_token=settings["refresh_token"],
        client_id=settings["client_id"],
        client_secret=settings["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=[GMAIL_SEND_SCOPE],
    )
    try:
        credentials.refresh(Request())
    except Exception as exc:
        text = str(exc).lower()
        code = next((item for item in _KNOWN_CODES if item in text), "invalid_grant")
        raise DeliveryRejected(_friendly(code), error_code=code) from None
    return credentials


def _service_credentials(settings):
    try:
        from google.oauth2 import service_account
    except ImportError as exc:
        raise DeliveryRejected("Envio institucional ainda não configurado.") from exc
    return service_account.Credentials.from_service_account_info(
        settings["info"], scopes=[GMAIL_SEND_SCOPE], subject=SENDER_ADDRESS
    )


def _raw_message(message):
    mail = EmailMessage()
    mail["From"] = f"{SENDER_NAME} <{SENDER_ADDRESS}>"
    mail["To"] = ", ".join(message["to"])
    mail["Subject"] = message["subject"]
    mail.set_content(message["text"])
    if message.get("html"):
        mail.add_alternative(message["html"], subtype="html")
    return base64.urlsafe_b64encode(mail.as_bytes()).decode("ascii")


def _is_uncertain(exc):
    if getattr(exc, "resp", None) is not None or getattr(exc, "content", None):
        return False
    return any(token in str(exc).lower() for token in _UNCERTAIN)


def _rejected(exc):
    status, code, description = _google_details(exc)
    if not code:
        lowered = description.lower()
        code = next((item for item in _KNOWN_CODES if item in lowered), "")
    return DeliveryRejected(
        _friendly(code),
        http_status=status,
        error_code=code or None,
        detail=_scrub(description),
    )


def _google_details(exc):
    status = getattr(getattr(exc, "resp", None), "status", None)
    raw = getattr(exc, "content", None)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    description = ""
    code = ""
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        error = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(error, dict):
            status = status or error.get("code")
            description = str(error.get("message") or "")
            errors = error.get("errors") or []
            if errors and isinstance(errors[0], dict):
                code = str(errors[0].get("reason") or "")
            if not code:
                code = str(error.get("status") or "")
    lowered = (code + " " + description).lower()
    known = next((item for item in _KNOWN_CODES if item in lowered), "")
    return status, known or code, description


def _scrub(value):
    return _SECRET.sub("[omitido]", " ".join(str(value or "").split()))[:300]


def _friendly(code):
    if code == "admin_policy_enforced":
        return (
            "A política do Google Workspace bloqueou o envio. "
            "A DITEC precisa liberar este cliente e o escopo gmail.send."
        )
    if code == "access_denied":
        return "O Google recusou o acesso da conta institucional a este aplicativo."
    if code == "invalid_grant":
        return "A autorização institucional expirou ou foi revogada."
    if code == "insufficient_scope":
        return "A autorização não inclui o escopo exclusivo de envio de e-mail."
    return "O provedor recusou o envio."
