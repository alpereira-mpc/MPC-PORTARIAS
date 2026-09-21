import pytest
from streamlit.testing.v1 import AppTest

from database.access_requests import AccessRequestStore
from database.store import ROOT
from services.access_requests import (
    ALREADY_REGISTERED,
    DUPLICATE_PENDING,
    GABINETE_OPTIONS,
    OTHER_UNIT,
    SUCCESS_TITLE,
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


def test_new_request_is_pending_without_smtp(store, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("SMTP não deve ser chamado")

    monkeypatch.setattr("services.mail.send_mail", boom)
    outcome = submit_access_request(store, **_payload())
    assert outcome.ok
    assert outcome.code == "created"
    assert outcome.title == SUCCESS_TITLE
    stored = AccessRequestStore(store).get(outcome.record["id"])
    assert stored["status"] == "pendente"
    assert stored["email"] == "maria.silva@tce.pb.gov.br"
    assert stored["processed_at"] is None
    assert stored["processed_by"] is None


def test_created_request_does_not_send_mail(store, monkeypatch):
    sent = []

    def fake_send(*args, **kwargs):
        sent.append(args)

    monkeypatch.setattr("services.mail.send_mail", fake_send)
    outcome = submit_access_request(
        store,
        "Maria Silva",
        "maria.silva@tce.pb.gov.br",
        OTHER_UNIT,
        "Núcleo de Apoio",
    )
    assert outcome.ok
    assert outcome.code == "created"
    assert outcome.title == SUCCESS_TITLE
    assert sent == []
    stored = AccessRequestStore(store).get(outcome.record["id"])
    assert stored["gabinete"] == OTHER_UNIT
    assert stored["unidade_outro"] == "Núcleo de Apoio"


def test_admin_service_lists_filters_and_counts(store):
    from database.access import AccessStore
    from services.access_requests import (
        FILTER_ALL,
        FILTER_APPROVED,
        FILTER_PENDING,
        FILTER_REJECTED,
        approve_access_request,
        count_pending_access_requests,
        list_access_requests_by_filter,
        reject_access_request,
    )

    first = submit_access_request(store, **_payload()).record
    second = submit_access_request(
        store,
        "Ana Souza",
        "ana.souza@tce.pb.gov.br",
        GABINETE_OPTIONS[1],
    ).record
    assert count_pending_access_requests(store) == 2
    pending = list_access_requests_by_filter(store, FILTER_PENDING)
    assert [row["id"] for row in pending] == [second["id"], first["id"]]
    approved = approve_access_request(store, first["id"], "  Admin@test.local ")
    assert approved["status"] == "aprovado"
    assert approved["processed_by"] == "admin@test.local"
    assert approved["processed_at"]
    rejected = reject_access_request(store, second["id"], "admin@test.local")
    assert rejected["status"] == "recusado"
    assert rejected["processed_by"] == "admin@test.local"
    assert count_pending_access_requests(store) == 0
    assert list_access_requests_by_filter(store, FILTER_APPROVED)[0]["id"] == first["id"]
    assert list_access_requests_by_filter(store, FILTER_REJECTED)[0]["id"] == second["id"]
    assert len(list_access_requests_by_filter(store, FILTER_ALL)) == 2
    with pytest.raises(ValueError, match="processada"):
        approve_access_request(store, first["id"], "admin@test.local")
    with pytest.raises(ValueError, match="processada"):
        reject_access_request(store, first["id"], "admin@test.local")
    with pytest.raises(ValueError, match="processada"):
        reject_access_request(store, second["id"], "admin@test.local")
    with pytest.raises(ValueError, match="Administrador"):
        approve_access_request(store, first["id"], "  ")
    assert AccessStore(store).get_by_email("maria.silva@tce.pb.gov.br") is None
    assert AccessStore(store).get_by_email("ana.souza@tce.pb.gov.br") is None


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


def test_admin_requests_section_filters_and_processes(store, monkeypatch):
    from database.access import AccessStore
    from tests.access_testing import TEST_IDENTITY, enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    first = submit_access_request(store, **_payload()).record
    second = submit_access_request(
        store,
        "Ana Souza",
        "ana.souza@tce.pb.gov.br",
        GABINETE_OPTIONS[1],
    ).record
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_admin").click().run()
    assert not app.exception
    options = next(r for r in app.radio if r.key == "admin_secao").options
    assert "Solicitações" in options
    captions = "\n".join(str(c.value) for c in app.caption)
    assert "Solicitações pendentes: 2" in captions
    app.radio(key="admin_secao").set_value("Solicitações").run()
    assert not app.exception and not app.error
    assert any(r.key == "access_request_admin_filter" for r in app.radio)
    assert app.radio(key="access_request_admin_filter").value == "Pendentes"
    assert any(
        getattr(b, "key", None) == "access_req_approve_" + str(first["id"])
        for b in app.button
    )
    app.checkbox(key="access_req_confirm_approve_" + str(first["id"])).set_value(True).run()
    app.button(key="access_req_approve_" + str(first["id"])).click().run()
    assert not app.exception
    stored = AccessRequestStore(store).get(first["id"])
    assert stored["status"] == "aprovado"
    assert stored["processed_by"] == TEST_IDENTITY["email"]
    assert stored["processed_at"]
    assert AccessStore(store).get_by_email("maria.silva@tce.pb.gov.br") is None
    captions = "\n".join(str(c.value) for c in app.caption)
    assert "Solicitações pendentes: 1" in captions
    assert not any(
        getattr(b, "key", None) == "access_req_approve_" + str(first["id"])
        for b in app.button
    )
    app.checkbox(key="access_req_confirm_reject_" + str(second["id"])).set_value(True).run()
    app.button(key="access_req_reject_" + str(second["id"])).click().run()
    rejected = AccessRequestStore(store).get(second["id"])
    assert rejected["status"] == "recusado"
    assert rejected["processed_by"] == TEST_IDENTITY["email"]
    app.radio(key="access_request_admin_filter").set_value("Aprovadas").run()
    assert not any(
        getattr(b, "key", None) == "access_req_reject_" + str(first["id"])
        for b in app.button
    )
    app.radio(key="access_request_admin_filter").set_value("Recusadas").run()
    app.radio(key="access_request_admin_filter").set_value("Todas").run()
    captions = "\n".join(str(c.value) for c in app.caption)
    assert "Solicitações pendentes: 0" in captions


def test_common_user_does_not_see_admin_requests(store, monkeypatch):
    from tests.access_testing import seed_access

    seed_access(
        store,
        email="comum.solic@test.local",
        perfil="USUARIO",
        pode_admin=False,
        pode_agenda=True,
    )
    monkeypatch.setattr(
        "services.access.oidc_identity",
        lambda: {
            "email": "comum.solic@test.local",
            "name": "Comum",
            "email_verified": True,
        },
    )
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    portal = next(r for r in app.sidebar.radio if r.key == "portal_module")
    assert "Administração" not in portal.options
    assert not any(r.key == "admin_secao" for r in app.radio)
    assert not any(r.key == "access_request_admin_filter" for r in app.radio)
