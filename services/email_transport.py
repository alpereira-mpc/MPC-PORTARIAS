"""Institutional mail transport. Login OAuth and SMTP stay untouched.

The Gmail client is imported only when a confirmed send actually needs it.
"""

import json
import os

SENDER_ADDRESS = "mpc@tce.pb.gov.br"
SENDER_NAME = "Ministério Público de Contas da Paraíba"
NOT_CONFIGURED = "Envio institucional ainda não configurado."
GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"


class NotConfigured(RuntimeError):
    pass


class DeliveryRejected(RuntimeError):
    """The provider refused the message before accepting it."""

    def __init__(self, message, *, http_status=None, error_code=None, detail=""):
        super().__init__(message)
        self.http_status = http_status
        self.error_code = error_code
        self.detail = detail


class DeliveryUncertain(RuntimeError):
    """The provider may already have accepted the message."""


def _secret_section():
    try:
        import streamlit as st

        value = st.secrets.get("gmail_send")
    except Exception:
        value = None
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def _enabled(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "sim"}


def _text(value):
    return str(value or "").strip()


def _service_account_info(raw_account):
    if isinstance(raw_account, str):
        try:
            info = json.loads(raw_account)
        except ValueError:
            return None
    else:
        try:
            info = dict(raw_account)
        except Exception:
            return None
    if not info.get("client_email") or not info.get("private_key"):
        return None
    return info


def gmail_configuration():
    """Secrets-only status. Does not import Google or contact the network."""
    section = _secret_section()
    enabled = section.get("enabled")
    raw_account = section.get("service_account")
    if not section:
        enabled = os.environ.get("GMAIL_SEND_ENABLED")
        raw_account = os.environ.get("GMAIL_SEND_SERVICE_ACCOUNT")
    sender = _text(section.get("sender")).lower() or SENDER_ADDRESS
    if sender != SENDER_ADDRESS:
        return {"configured": False, "mode": None, "problem": "sender"}
    if not _enabled(enabled):
        return {"configured": False, "mode": None, "problem": None}
    mode = _text(section.get("mode")).lower()
    if not mode:
        mode = "user_oauth" if section.get("refresh_token") else "service_account"
    if mode == "user_oauth":
        complete = all(_text(section.get(name)) for name in ("client_id", "client_secret", "refresh_token"))
        if not complete:
            return {"configured": False, "mode": "user_oauth", "problem": "incomplete"}
        return {"configured": True, "mode": "user_oauth", "problem": None}
    if mode != "service_account":
        return {"configured": False, "mode": None, "problem": "incomplete"}
    if _service_account_info(raw_account) is None:
        return {"configured": False, "mode": "service_account", "problem": "incomplete"}
    return {"configured": True, "mode": "service_account", "problem": None}


def gmail_settings():
    """Read the send configuration. This does not contact Google."""
    section = _secret_section()
    status = gmail_configuration()
    if not status["configured"]:
        return None
    if status["mode"] == "user_oauth":
        return {
            "mode": "user_oauth",
            "sender": SENDER_ADDRESS,
            "client_id": _text(section.get("client_id")),
            "client_secret": _text(section.get("client_secret")),
            "refresh_token": _text(section.get("refresh_token")),
        }
    raw_account = section.get("service_account")
    if not section:
        raw_account = os.environ.get("GMAIL_SEND_SERVICE_ACCOUNT")
    return {
        "mode": "service_account",
        "sender": SENDER_ADDRESS,
        "info": _service_account_info(raw_account),
    }


def transport_available():
    settings = gmail_settings()
    if settings is None:
        return False
    try:
        import googleapiclient.discovery  # noqa: F401

        if settings["mode"] == "user_oauth":
            import google.oauth2.credentials  # noqa: F401
        else:
            import google.oauth2.service_account  # noqa: F401
    except ImportError:
        return False
    return True


def institutional_transport():
    if gmail_configuration().get("problem") == "sender":
        raise NotConfigured("O remetente institucional deve ser mpc@tce.pb.gov.br.")
    settings = gmail_settings()
    if settings is None or not transport_available():
        raise NotConfigured(NOT_CONFIGURED)
    from services.gmail_transport import GmailTransport

    return GmailTransport(settings)
