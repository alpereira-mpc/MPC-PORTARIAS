"""Proof-of-concept Gmail OAuth. These tests never contact Google."""

import sys

import pytest

from services.access import resolve_principal
from services.email_transport import (
    SENDER_ADDRESS,
    NotConfigured,
    gmail_configuration,
    gmail_settings,
    institutional_transport,
)
from services.gmail_test import send_institutional_test
from services.gmail_transport import GmailTransport, _rejected
from services.representacoes import create, get
from tests.test_representacoes import _payload, _principal


class FakeTransport:
    def __init__(self, result="msg-test", error=None):
        self.result = result
        self.error = error
        self.calls = []

    def send(self, message):
        self.calls.append(message)
        if self.error:
            raise self.error
        return self.result


def _secrets(monkeypatch, section):
    import streamlit as st

    monkeypatch.setattr(st, "secrets", {"gmail_send": section})


def _oauth(**extra):
    data = {
        "enabled": True,
        "mode": "user_oauth",
        "sender": SENDER_ADDRESS,
        "client_id": "client-id",
        "client_secret": "client-secret",
        "refresh_token": "refresh-token",
    }
    data.update(extra)
    return data


def test_missing_configuration_does_not_build_a_client(monkeypatch):
    monkeypatch.delenv("GMAIL_SEND_ENABLED", raising=False)
    monkeypatch.delenv("GMAIL_SEND_SERVICE_ACCOUNT", raising=False)
    assert gmail_configuration()["configured"] is False
    assert gmail_settings() is None
    with pytest.raises(NotConfigured):
        institutional_transport()


def test_other_sender_is_refused(monkeypatch):
    _secrets(monkeypatch, _oauth(sender="outra@tce.pb.gov.br"))
    assert gmail_configuration()["problem"] == "sender"
    with pytest.raises(NotConfigured, match="mpc@tce.pb.gov.br"):
        institutional_transport()


def test_user_oauth_settings_keep_only_the_institutional_sender(monkeypatch):
    _secrets(monkeypatch, _oauth())
    settings = gmail_settings()
    assert settings["mode"] == "user_oauth"
    assert settings["sender"] == SENDER_ADDRESS
    assert settings["refresh_token"] == "refresh-token"


def test_service_account_mode_remains_available(monkeypatch):
    _secrets(
        monkeypatch,
        {
            "enabled": True,
            "mode": "service_account",
            "sender": SENDER_ADDRESS,
            "service_account": {"client_email": "bot@example.iam.gserviceaccount.com", "private_key": "key"},
        },
    )
    settings = gmail_settings()
    assert settings["mode"] == "service_account"
    assert settings["info"]["client_email"].endswith(".iam.gserviceaccount.com")


def test_refresh_happens_before_send_and_tokens_are_not_in_the_error(monkeypatch):
    refreshed = {}
    sent = {}

    class Credentials:
        def __init__(self, **kwargs):
            self.token = None
            self.refresh_token = kwargs["refresh_token"]

        def refresh(self, request):
            refreshed["token"] = self.refresh_token
            self.token = "access-token"

    class Request:
        pass

    class Service:
        def users(self):
            return self

        def messages(self):
            return self

        def send(self, userId, body):
            sent["user"] = userId
            sent["body"] = body
            return self

        def execute(self):
            return {"id": "abc"}

    _install_google(monkeypatch, Credentials, Request, lambda *args, **kwargs: Service())
    transport = GmailTransport(gmail_settings_from(_oauth()))
    assert transport.send({"to": ["pessoa@tce.pb.gov.br"], "subject": "s", "text": "t"}) == "abc"
    assert refreshed["token"] == "refresh-token"
    assert sent["user"] == "me"
    assert "access-token" not in str(sent["body"])


def test_invalid_grant_does_not_call_gmail(monkeypatch):
    called = []

    class Credentials:
        def __init__(self, **kwargs):
            self.refresh_token = kwargs["refresh_token"]

        def refresh(self, request):
            raise RuntimeError("invalid_grant")

    class Request:
        pass

    _install_google(monkeypatch, Credentials, Request, lambda *args, **kwargs: called.append("api"))
    transport = GmailTransport(gmail_settings_from(_oauth()))
    with pytest.raises(Exception, match="revogada") as caught:
        transport.send({"to": ["pessoa@tce.pb.gov.br"], "subject": "s", "text": "t"})
    assert caught.value.error_code == "invalid_grant"
    assert "refresh-token" not in str(caught.value)
    assert called == []


def test_workspace_policy_error_is_explicit():
    class Response:
        status = 403

    exc = RuntimeError("blocked")
    exc.resp = Response()
    exc.content = (
        b'{"error":{"code":403,"message":"policy","errors":[{"reason":"admin_policy_enforced"}]}}'
    )
    rejected = _rejected(exc)
    assert rejected.http_status == 403
    assert rejected.error_code == "admin_policy_enforced"
    assert "DITEC" in str(rejected)


def test_test_send_is_isolated_from_representations(store, monkeypatch):
    monkeypatch.delenv("GMAIL_SEND_ENABLED", raising=False)
    principal = _principal(store, email="gmail-admin@test.local")
    admin = resolve_principal(store, {"email": "admin@test.local"})
    payload, *_rest = _payload(store)
    record = create(store, payload, principal)
    before = get(store, record["id"])
    with store.connection() as connection:
        notices = connection.execute("SELECT COUNT(*) FROM notificacoes_email").fetchone()[0]
    with pytest.raises(ValueError, match="@tce.pb.gov.br"):
        send_institutional_test(store, admin, "pessoa@example.com", FakeTransport())
    denied = _principal(store, email="sem-admin-gmail@test.local", representacoes=True)
    denied_admin = resolve_principal(store, {"email": "sem-admin-gmail@test.local"})
    transport = FakeTransport()
    with pytest.raises(ValueError, match="não autorizado"):
        send_institutional_test(store, denied_admin, "pessoa@tce.pb.gov.br", transport)
    assert transport.calls == []
    provider = send_institutional_test(store, admin, "Pessoa@TCE.PB.GOV.BR", transport)
    assert provider == "msg-test"
    assert transport.calls[0]["to"] == ["pessoa@tce.pb.gov.br"]
    assert "Representação" not in transport.calls[0]["text"]
    after = get(store, record["id"])
    assert after["numero_processo"] == before["numero_processo"]
    assert after["titulo"] == before["titulo"]
    with store.connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM notificacoes_email").fetchone()[0] == notices
        audited = connection.execute(
            "SELECT COUNT(*) FROM auditoria_eventos WHERE evento='GMAIL_TESTE_ENVIO'"
        ).fetchone()[0]
    assert audited == 1


def test_rendering_status_does_not_call_the_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "services.email_transport.institutional_transport",
        lambda: calls.append("api"),
    )
    import services.notification_ui as ui
    import services.representacoes_ui as representations

    source = open(ui.__file__, encoding="utf-8").read() + open(representations.__file__, encoding="utf-8").read()
    assert "import googleapiclient" not in source
    assert "import google.oauth2" not in source
    gmail_configuration()
    assert calls == []


def gmail_settings_from(section):
    return {
        "mode": "user_oauth",
        "sender": SENDER_ADDRESS,
        "client_id": section["client_id"],
        "client_secret": section["client_secret"],
        "refresh_token": section["refresh_token"],
    }


def _install_google(monkeypatch, credentials, request, build):
    import types

    modules = {
        "google": types.ModuleType("google"),
        "google.oauth2": types.ModuleType("google.oauth2"),
        "google.oauth2.credentials": types.ModuleType("google.oauth2.credentials"),
        "google.auth": types.ModuleType("google.auth"),
        "google.auth.transport": types.ModuleType("google.auth.transport"),
        "google.auth.transport.requests": types.ModuleType("google.auth.transport.requests"),
        "googleapiclient": types.ModuleType("googleapiclient"),
        "googleapiclient.discovery": types.ModuleType("googleapiclient.discovery"),
    }
    modules["google.oauth2.credentials"].Credentials = credentials
    modules["google.auth.transport.requests"].Request = request
    modules["googleapiclient.discovery"].build = build
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
