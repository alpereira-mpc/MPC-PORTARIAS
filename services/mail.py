"""Minimal SMTP helper. Credentials come only from environment or Secrets."""

from email.message import EmailMessage
import logging
import os
import smtplib
import ssl

LOGGER = logging.getLogger("mpc.mail")
DEFAULT_PORT = 587


class MailError(ValueError):
    pass


def _secret_mapping():
    try:
        import streamlit as st

        value = st.secrets.get("smtp")
    except Exception:
        return {}
    if value is None:
        return {}
    try:
        return dict(value)
    except Exception:
        return {}


def smtp_settings():
    secrets = _secret_mapping()

    def read(name, env_name):
        env = os.environ.get(env_name)
        if env is not None and str(env).strip():
            return str(env).strip()
        raw = secrets.get(name)
        if raw is None:
            return ""
        text = str(raw).strip()
        return text

    host = read("host", "SMTP_HOST")
    if not host:
        return None
    port_raw = read("port", "SMTP_PORT") or str(DEFAULT_PORT)
    try:
        port = int(port_raw)
    except ValueError:
        raise MailError("Configuração de e-mail inválida.") from None
    return {
        "host": host,
        "port": port,
        "username": read("username", "SMTP_USERNAME"),
        "password": read("password", "SMTP_PASSWORD"),
        "from_address": read("from_address", "SMTP_FROM")
        or read("username", "SMTP_USERNAME"),
    }


def send_mail(subject, body, to_address):
    settings = smtp_settings()
    if not settings:
        raise MailError("Envio de e-mail não configurado.")
    sender = settings["from_address"]
    if not sender:
        raise MailError("Envio de e-mail não configurado.")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_address
    message.set_content(body)
    try:
        if settings["port"] == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                settings["host"], settings["port"], timeout=20, context=context
            ) as smtp:
                if settings["username"]:
                    smtp.login(settings["username"], settings["password"])
                smtp.send_message(message)
            return
        with smtplib.SMTP(settings["host"], settings["port"], timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            if settings["username"]:
                smtp.login(settings["username"], settings["password"])
            smtp.send_message(message)
    except MailError:
        raise
    except Exception:
        LOGGER.exception("Falha ao enviar e-mail administrativo")
        raise MailError("Não foi possível enviar o e-mail de notificação.") from None
