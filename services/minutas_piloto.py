"""Fail-closed access configuration for the private Minutas IA pilot."""

import re

import streamlit as st

from services.access import normalize_email


_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _authorized_emails(secrets):
    try:
        raw = secrets.get("MINUTAS_PILOTO_EMAILS")
    except Exception:
        return None
    if not isinstance(raw, (list, tuple)) or not raw:
        return None

    emails = set()
    for value in raw:
        if not isinstance(value, str):
            return None
        email = normalize_email(value)
        if not email or not _EMAIL_PATTERN.fullmatch(email):
            return None
        emails.add(email)
    return emails or None


def can_access_minutas_piloto(identity, secrets=None):
    """Authorize an OIDC identity only when the complete pilot config is valid."""
    if not identity or identity.get("email_verified") is False:
        return False
    try:
        source = st.secrets if secrets is None else secrets
        if source.get("MINUTAS_PILOTO_ENABLED") is not True:
            return False
        emails = _authorized_emails(source)
        email = normalize_email(identity.get("email"))
    except Exception:
        return False
    return bool(emails and email in emails)
