import pytest
from streamlit.testing.v1 import AppTest

from database.access import AccessStore
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
    outcome = submit_access_request(
        store,
        "Já Cadastrada",
        "JA.TEM@tce.pb.gov.br",
        GABINETE_OPTIONS[0],
    )
    assert outcome.code == "already_registered"
    assert outcome.message == ALREADY_REGISTERED
    assert AccessRequestStore(store).get_pending_by_email("ja.tem@tce.pb.gov.br") is None
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM access_requests").fetchone()[0] == 0


def test_pending_duplicate_is_blocked(store):
    first = submit_access_request(store, **_payload())
    assert first.ok
    assert first.record["status"] == "pendente"
    second = submit_access_request(store, **_payload())
    assert not second.ok
    assert second.message == DUPLICATE_PENDING
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM access_requests").fetchone()[0] == 1


def test_new_request_is_pending_unviewed_and_does_not_send_mail(store, monkeypatch):
    sent = []
    monkeypatch.setattr("services.mail.send_mail", lambda *args: sent.append(args))
    outcome = submit_access_request(store, **_payload())
    assert outcome.ok
    assert outcome.code == "created"
    stored = AccessRequestStore(store).get(outcome.record["id"])
    assert stored["status"] == "pendente"
    assert stored["viewed_at"] is None
    assert stored["email"] == "maria.silva@tce.pb.gov.br"
    assert stored["processed_at"] is None
    assert stored["processed_by"] is None
    assert sent == []


def test_count_view_and_process_requests_are_idempotent(store):
    requests = AccessRequestStore(store)
    first = requests.create("Maria Silva", "maria@tce.pb.gov.br", GABINETE_OPTIONS[0])
    second = requests.create(
        "João Silva", "joao@tce.pb.gov.br", OTHER_UNIT, "Núcleo de Apoio"
    )
    assert requests.count_new() == 2
    assert requests.mark_viewed(first["id"])
    viewed_at = requests.get(first["id"])["viewed_at"]
    assert viewed_at
    assert requests.get(first["id"])["status"] == "pendente"
    assert not requests.mark_viewed(first["id"])
    assert requests.get(first["id"])["viewed_at"] == viewed_at
    assert requests.count_new() == 1

    assert requests.process(first["id"], "aprovado", "admin@tce.pb.gov.br")
    approved = requests.get(first["id"])
    assert approved["status"] == "aprovado"
    assert approved["processed_at"]
    assert approved["processed_by"] == "admin@tce.pb.gov.br"
    assert not requests.process(first["id"], "recusado", "outro@tce.pb.gov.br")
    assert requests.get(first["id"])["status"] == "aprovado"

    assert requests.process(second["id"], "recusado", "admin@tce.pb.gov.br")
    rejected = requests.get(second["id"])
    assert rejected["status"] == "recusado"
    assert rejected["viewed_at"]
    assert requests.count_new() == 0
    assert [item["id"] for item in requests.list("aprovado")] == [first["id"]]


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


def test_administrator_can_view_and_approve_without_creating_user(store, monkeypatch):
    from tests.access_testing import TEST_IDENTITY, enable_login

    request_store = AccessRequestStore(store)
    record = request_store.create(
        "Nova Servidora", "nova.servidora@tce.pb.gov.br", GABINETE_OPTIONS[0]
    )
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_admin").click().run()
    app.radio(key="admin_secao").set_value("Solicitações").run()
    assert "NOVA" in "\n".join(str(item.value) for item in app.markdown)

    app.button(key=f"access_request_open_{record['id']}").click().run()
    viewed = request_store.get(record["id"])
    assert viewed["viewed_at"]
    assert viewed["status"] == "pendente"
    app.button(key=f"access_request_approve_{record['id']}").click().run()
    app.checkbox(key=f"access_request_confirm_aprovar_{record['id']}").set_value(
        True
    ).run()
    app.button(key=f"access_request_execute_aprovar_{record['id']}").click().run()

    approved = request_store.get(record["id"])
    assert approved["status"] == "aprovado"
    assert approved["processed_at"]
    assert approved["processed_by"] == TEST_IDENTITY["email"]
    assert AccessStore(store).get_by_email("nova.servidora@tce.pb.gov.br") is None
    assert not app.exception
