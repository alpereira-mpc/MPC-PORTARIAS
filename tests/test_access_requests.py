import pytest
from streamlit.testing.v1 import AppTest

from database.access_requests import AccessRequestStore
from database.store import ROOT
from services.access_requests import (
    ALREADY_REGISTERED,
    DUPLICATE_PENDING,
    GABINETE_OPTIONS,
    OTHER_UNIT,
    normalize_request_email,
    submit_access_request,
    validate_access_request,
)
from services.mail import MailError
from tests.access_testing import seed_access


def _payload(**overrides):
    data = {
        "nome": "  Maria  Silva  ",
        "email": "  Maria.Silva@TCE.PB.GOV.BR ",
        "gabinete": GABINETE_OPTIONS[0],
        "unidade_outro": None,
    }
    data.update(overrides)
    return data


def test_external_email_is_rejected():
    with pytest.raises(ValueError, match="@tce.pb.gov.br"):
        normalize_request_email("pessoa@gmail.com")
    with pytest.raises(ValueError, match="@tce.pb.gov.br"):
        validate_access_request(
            "Maria Silva", "pessoa@tce.pb.gov.br.com", GABINETE_OPTIONS[0]
        )


def test_institutional_email_is_normalized_and_accepted():
    assert (
        normalize_request_email("  Maria.Silva@TCE.PB.GOV.BR ")
        == "maria.silva@tce.pb.gov.br"
    )
    payload = validate_access_request(**_payload())
    assert payload["email"] == "maria.silva@tce.pb.gov.br"
    assert payload["nome"] == "Maria Silva"


def test_gabinete_options_are_closed_and_include_other_unit():
    assert OTHER_UNIT in GABINETE_OPTIONS
    assert GABINETE_OPTIONS[-1] == OTHER_UNIT
    assert "Procuradoria-Geral — PROGE" in GABINETE_OPTIONS
    with pytest.raises(ValueError, match="gabinete"):
        validate_access_request("Maria Silva", "maria@tce.pb.gov.br", "Texto livre")
    with pytest.raises(ValueError, match="unidade"):
        validate_access_request(
            "Maria Silva", "maria@tce.pb.gov.br", OTHER_UNIT, ""
        )
    extra = validate_access_request(
        "Maria Silva", "maria@tce.pb.gov.br", OTHER_UNIT, "  Núcleo X  "
    )
    assert extra["unidade_outro"] == "Núcleo X"
    assert validate_access_request(**_payload())["unidade_outro"] is None


def test_authorized_email_does_not_create_request(store):
    seed_access(store, email="ja.tem@tce.pb.gov.br", perfil="USUARIO")
    mailed = []
    import services.access_requests as module

    original = module.notify_access_request
    module.notify_access_request = lambda record: mailed.append(record)
    try:
        outcome = submit_access_request(
            store,
            "Já Cadastrada",
            "JA.TEM@tce.pb.gov.br",
            GABINETE_OPTIONS[0],
        )
    finally:
        module.notify_access_request = original
    assert outcome.code == "already_registered"
    assert outcome.message == ALREADY_REGISTERED
    assert AccessRequestStore(store).get_pending_by_email("ja.tem@tce.pb.gov.br") is None
    assert mailed == []
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM access_requests").fetchone()[0] == 0


def test_pending_duplicate_is_blocked(store, monkeypatch):
    monkeypatch.setattr(
        "services.access_requests.notify_access_request", lambda record: None
    )
    first = submit_access_request(store, **_payload())
    assert first.ok
    assert first.record["status"] == "pendente"
    second = submit_access_request(store, **_payload())
    assert not second.ok
    assert second.message == DUPLICATE_PENDING
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM access_requests").fetchone()[0] == 1


def test_new_request_is_pending_and_mail_failure_keeps_row(store, monkeypatch):
    def boom(record):
        raise MailError("falha")

    monkeypatch.setattr("services.access_requests.notify_access_request", boom)
    outcome = submit_access_request(store, **_payload())
    assert outcome.ok
    assert outcome.code == "created_notify_failed"
    stored = AccessRequestStore(store).get(outcome.record["id"])
    assert stored["status"] == "pendente"
    assert stored["email"] == "maria.silva@tce.pb.gov.br"
    assert stored["processed_at"] is None
    assert stored["processed_by"] is None


def test_created_request_notifies_admin_inbox(store, monkeypatch):
    sent = []

    def fake_send(subject, body, to_address):
        sent.append((subject, body, to_address))

    monkeypatch.setattr("services.access_requests.send_mail", fake_send)
    outcome = submit_access_request(
        store,
        "Maria Silva",
        "maria.silva@tce.pb.gov.br",
        OTHER_UNIT,
        "Núcleo de Apoio",
    )
    assert outcome.ok
    assert outcome.code == "created"
    assert sent[0][0] == "[Ferramentas MPC-PB] Nova solicitação de acesso"
    assert sent[0][2] == "mpc@tce.pb.gov.br"
    assert "Núcleo de Apoio" in sent[0][1]
    assert "maria.silva@tce.pb.gov.br" in sent[0][1]


def test_login_still_offers_gmail_and_request_access(monkeypatch):
    import streamlit as st

    monkeypatch.setattr("services.access.oidc_identity", lambda: None)
    started = []
    monkeypatch.setattr(st, "login", lambda: started.append(True))
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    visible = "\n".join(str(getattr(item, "value", item)) for item in app.markdown)
    assert any(b.label == "Entrar com Gmail" for b in app.button)
    assert any(b.label == "Solicitar acesso" for b in app.button)
    assert "Ainda não possui acesso?" in visible
    assert "Cadastre-se" not in visible
    app.button(key="access_request_open").click().run()
    assert not app.exception
    assert any(b.label == "Entrar com Gmail" for b in app.button)
    assert any(i.label == "Nome completo" for i in app.text_input)
    app.button(key="access_request_back").click().run()
    assert any(b.label == "Solicitar acesso" for b in app.button)
    app.button(key="oidc_gmail_login").click().run()
    assert started == [True]
