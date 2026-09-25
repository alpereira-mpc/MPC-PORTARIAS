"""Permissions for institutional actions must be enforced by services, not only UI."""

import pytest

from database.access import AccessStore
from database.notifications import NotificationsStore
from services.access import resolve_principal
from services.gmail_test import send_institutional_test
from services.notifications import confirm_send, save_recipient
from services.representacoes import RELATORES, create, get, register_protocol
from tests.test_notifications import FakeTransport
from tests.test_representacoes import _payload


def _user(store, email, **permissions):
    identifier = AccessStore(store).save_user(
        {
            "nome": email.split("@")[0],
            "email": email,
            "perfil": "USUARIO",
            "pode_representacoes": True,
            **permissions,
        }
    )
    return resolve_principal(store, {"email": AccessStore(store).get(identifier)["email"]})


def _project(store, principal):
    payload, *_ = _payload(store)
    return create(store, payload, principal)


def _protocol_values():
    return {
        "numero_processo": "TC 654321/26",
        "data_protocolo": "2026-09-25",
        "relator": RELATORES[0],
        "fase_processual": "INSTRUCAO",
    }


def test_protocol_permission_is_independent_and_backend_blocks_mutation(store):
    denied = _user(store, "sem-protocolo@test.local")
    record = _project(store, denied)
    with pytest.raises(ValueError, match="não autorizado"):
        register_protocol(store, record["id"], _protocol_values(), denied)
    assert not get(store, record["id"])["numero_processo"]

    allowed = _user(
        store,
        "com-protocolo@test.local",
        pode_representacoes_registrar_protocolo=True,
    )
    registered = register_protocol(store, record["id"], _protocol_values(), allowed)
    assert registered["numero_processo"] == "TC 654321/26"


def test_protocol_sender_permission_blocks_transport_but_not_protocol(store):
    actor = _user(
        store,
        "somente-protocolo@test.local",
        pode_representacoes_registrar_protocolo=True,
    )
    record = register_protocol(store, _project(store, actor)["id"], _protocol_values(), actor)
    transport = FakeTransport()
    with pytest.raises(ValueError, match="não autorizado"):
        confirm_send(store, record, actor, transport)
    assert transport.calls == []
    assert NotificationsStore(store).get_by_key("representation:%s:protocolled" % record["id"]) is None


def test_recipients_and_test_email_require_their_own_capabilities(store):
    admin = resolve_principal(store, {"email": "admin@test.local"})
    member = next(item for item in store.catalog("procuradores") if item.get("ativo"))
    delegated_admin = _user(store, "admin-sem-comunicacao@test.local", pode_admin=True)

    with pytest.raises(ValueError, match="não autorizado"):
        save_recipient(
            store,
            delegated_admin,
            membro_tipo="PROCURADOR",
            membro_id=member["id"],
            email="destinatario@tce.pb.gov.br",
            ativo=True,
        )
    transport = FakeTransport()
    with pytest.raises(ValueError, match="não autorizado"):
        send_institutional_test(store, delegated_admin, "destinatario@tce.pb.gov.br", transport)
    assert transport.calls == []

    assert save_recipient(
        store,
        admin,
        membro_tipo="PROCURADOR",
        membro_id=member["id"],
        email="destinatario@tce.pb.gov.br",
        ativo=True,
    )["ativo"]
    assert send_institutional_test(store, admin, "destinatario@tce.pb.gov.br", FakeTransport()) == "gmail-1"
