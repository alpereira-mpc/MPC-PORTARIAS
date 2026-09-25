"""Supervised protocol notices: one message, no send without confirmation."""

from threading import Barrier, Thread
import sys

import pytest

from database.notifications import NotificationsStore
from services.email_transport import NOT_CONFIGURED, DeliveryRejected, DeliveryUncertain, NotConfigured
from services.notifications import (
    confirm_send,
    ensure_draft,
    idempotency_key,
    notice_summary,
    save_recipient,
)
from services.representacoes import RELATORES, create, get, list_records, register_protocol
from tests.test_representacoes import _payload, _principal


class FakeTransport:
    def __init__(self, result="gmail-1", error=None):
        self.result = result
        self.error = error
        self.calls = []

    def send(self, message):
        self.calls.append(message)
        if self.error:
            raise self.error
        return self.result


def _protocolled(store, principal=None):
    principal = principal or _principal(store)
    payload, *_rest = _payload(store)
    record = create(store, payload, principal)
    saved = register_protocol(
        store,
        record["id"],
        {
            "numero_processo": "TC 012345/26",
            "data_protocolo": "2026-09-18",
            "relator": RELATORES[0],
            "fase_processual": "INSTRUCAO",
        },
        principal,
    )
    return principal, saved


def _people(store, count=1):
    from services.access import resolve_principal
    from tests.test_representacoes import _server

    admin = resolve_principal(store, {"email": "admin@test.local"})
    members = [item for item in store.catalog("procuradores") if item.get("ativo")]
    chosen = []
    for index, member in enumerate(members):
        if len(chosen) >= count:
            break
        save_recipient(
            store,
            admin,
            membro_tipo="PROCURADOR",
            membro_id=member["id"],
            email="pessoa{0}@tce.pb.gov.br".format(index + 1),
            ativo=True,
        )
        chosen.append(("PROCURADOR", member["id"]))
    while len(chosen) < count:
        identifier = _server(store, "Chefe de Cartório " + str(len(chosen)))
        save_recipient(
            store,
            admin,
            membro_tipo="SERVIDOR",
            membro_id=identifier,
            email="pessoa{0}@tce.pb.gov.br".format(len(chosen) + 1),
            ativo=True,
        )
        chosen.append(("SERVIDOR", identifier))
    return admin, chosen


def test_unprotocolled_representation_does_not_prepare_notice(store):
    principal = _principal(store)
    payload, *_rest = _payload(store)
    record = create(store, payload, principal)
    assert notice_summary(store, record) is None
    with pytest.raises(ValueError, match="depois do protocolo"):
        ensure_draft(store, record, principal)
    assert NotificationsStore(store).get_by_key(idempotency_key(record["id"])) is None


def test_protocol_makes_notice_available_without_creating_or_sending(store):
    principal, record = _protocolled(store)
    assert NotificationsStore(store).get_by_key(idempotency_key(record["id"])) is None
    summary = notice_summary(store, record)
    assert summary["status"] is None
    assert summary["label"] == "Pendente de envio"
    listed = list_records(store, {})
    assert listed[0]["id"] == record["id"]
    assert "googleapiclient" not in sys.modules


def test_preparing_twice_keeps_a_single_draft(store):
    principal, record = _protocolled(store)
    first = ensure_draft(store, record, principal)
    second = ensure_draft(store, record, principal)
    assert first["id"] == second["id"]
    assert second["status"] == "DRAFT"
    with store.connection() as connection:
        count = connection.execute("SELECT COUNT(*) FROM notificacoes_email").fetchone()[0]
    assert count == 1


def test_recipients_come_from_the_registry_and_block_bad_addresses(store):
    from pathlib import Path

    from database.store import ROOT
    from services.notifications import build_preview

    principal, record = _protocolled(store)
    admin, members = _people(store, count=7)
    assert "pessoa1@tce.pb.gov.br" not in (ROOT / "services" / "representacoes.py").read_text(encoding="utf-8")
    assert "pessoa1@tce.pb.gov.br" not in Path(build_preview.__code__.co_filename).read_text(encoding="utf-8")
    preview = build_preview(store, record, principal)
    assert len(preview["destinatarios"]) == 7
    assert "warning" not in preview
    assert not preview["blockers"]
    kind, identifier = members[0]
    save_recipient(store, admin, membro_tipo=kind, membro_id=identifier, email="", ativo=True)
    blocked = build_preview(store, get(store, record["id"]), principal)
    assert any("sem e-mail" in item for item in blocked["blockers"])
    transport = FakeTransport()
    with pytest.raises(ValueError, match="sem e-mail"):
        confirm_send(store, record, principal, transport)
    assert transport.calls == []
    with pytest.raises(ValueError, match="válido"):
        save_recipient(store, admin, membro_tipo=kind, membro_id=identifier, email="invalido", ativo=True)


def test_duplicate_active_email_is_blocked(store):
    admin, members = _people(store, count=2)
    kind, identifier = members[1]
    with pytest.raises(ValueError, match="já está atribuído"):
        save_recipient(
            store,
            admin,
            membro_tipo=kind,
            membro_id=identifier,
            email="pessoa1@tce.pb.gov.br",
            ativo=True,
        )


def test_zero_active_recipients_block_send(store):
    from services.notifications import build_preview

    principal, record = _protocolled(store)
    preview = build_preview(store, record, principal)
    assert preview["destinatarios"] == []
    assert any("Não há destinatários ativos" in item for item in preview["blockers"])
    transport = FakeTransport()
    with pytest.raises(ValueError, match="Não há destinatários ativos"):
        confirm_send(store, record, principal, transport)
    assert transport.calls == []


@pytest.mark.parametrize("count", [1, 3, 8, 10])
def test_send_uses_exactly_the_active_recipients(store, count):
    from services.notifications import build_preview

    principal, record = _protocolled(store)
    _people(store, count=count)
    preview = build_preview(store, record, principal)
    assert len(preview["destinatarios"]) == count
    assert preview["blockers"] == []
    assert "8 destinatários" not in " ".join(preview["blockers"])
    transport = FakeTransport(result="msg-" + str(count))
    sent = confirm_send(store, record, principal, transport)
    assert sent["status"] == "SENT"
    assert len(transport.calls) == 1
    assert len(transport.calls[0]["to"]) == count
    assert len(set(transport.calls[0]["to"])) == count


def test_invalid_active_email_blocks_send(store):
    from services.notifications import SUBSCRIPTION_PROTOCOLO, build_preview

    principal, record = _protocolled(store)
    admin, members = _people(store, count=1)
    kind, identifier = members[0]
    with pytest.raises(ValueError, match="válido"):
        save_recipient(store, admin, membro_tipo=kind, membro_id=identifier, email="invalido", ativo=True)
    with store.connection() as connection:
        connection.execute(
            "UPDATE notificacao_destinatarios SET email=? WHERE evento=? AND membro_tipo=? AND membro_id=?",
            ("invalido", SUBSCRIPTION_PROTOCOLO, kind, identifier),
        )
    preview = build_preview(store, record, principal)
    assert any("inválido" in item for item in preview["blockers"])
    transport = FakeTransport()
    with pytest.raises(ValueError, match="inválido"):
        confirm_send(store, record, principal, transport)
    assert transport.calls == []


def test_duplicate_stored_email_blocks_a_second_copy(store):
    from services.notifications import SUBSCRIPTION_PROTOCOLO, build_preview

    principal, record = _protocolled(store)
    _admin, members = _people(store, count=2)
    kind, identifier = members[1]
    with store.connection() as connection:
        connection.execute(
            "UPDATE notificacao_destinatarios SET email=? WHERE evento=? AND membro_tipo=? AND membro_id=?",
            ("pessoa1@tce.pb.gov.br", SUBSCRIPTION_PROTOCOLO, kind, identifier),
        )
    preview = build_preview(store, record, principal)
    assert any("repetido" in item for item in preview["blockers"])
    transport = FakeTransport()
    with pytest.raises(ValueError, match="repetido"):
        confirm_send(store, record, principal, transport)
    assert transport.calls == []


def test_success_persists_snapshot_and_refuses_a_second_send(store):
    principal, record = _protocolled(store)
    _people(store, count=1)
    transport = FakeTransport(result="abc-123")
    sent = confirm_send(store, record, principal, transport)
    assert sent["status"] == "SENT"
    assert sent["enviado_em"]
    assert sent["provedor_mensagem_id"] == "abc-123"
    assert sent["assunto_snapshot"].endswith("TC 012345/26")
    assert sent["corpo_snapshot"]["texto"]
    assert len(transport.calls) == 1
    assert transport.calls[0]["to"][0].endswith("@tce.pb.gov.br")
    assert len(transport.calls[0]["to"]) == 1
    with pytest.raises(ValueError, match="já enviada"):
        confirm_send(store, record, principal, transport)
    assert len(transport.calls) == 1
    with store.connection() as connection:
        connection.execute(
            "UPDATE representacoes SET numero_processo=? WHERE id=?",
            ("TC 999999/26", record["id"]),
        )
    changed = get(store, record["id"])
    summary = notice_summary(store, changed)
    assert summary["process_changed"] is True
    assert summary["status"] == "SENT"
    with pytest.raises(ValueError, match="já enviada"):
        confirm_send(store, changed, transport=transport, principal=principal)
    stored = NotificationsStore(store).get_by_key(idempotency_key(record["id"]))
    assert stored["assunto_snapshot"].endswith("TC 012345/26")
    assert len(transport.calls) == 1


def test_provider_rejection_marks_failed_and_does_not_retry(store):
    principal, record = _protocolled(store)
    _people(store, count=1)
    transport = FakeTransport(error=DeliveryRejected("token secret=abc private_key -----"))
    with pytest.raises(ValueError, match="não foi concluído"):
        confirm_send(store, record, principal, transport)
    saved = NotificationsStore(store).get_by_key(idempotency_key(record["id"]))
    assert saved["status"] == "FAILED"
    assert saved["tentativas"] == 1
    assert "secret" not in saved["erro"]
    assert "private_key" not in saved["erro"]
    assert len(transport.calls) == 1
    transport.error = None
    transport.result = "retry-1"
    retried = confirm_send(store, record, principal, transport)
    assert retried["status"] == "SENT"
    assert len(transport.calls) == 2


def test_uncertain_delivery_is_not_retried(store):
    principal, record = _protocolled(store)
    _people(store, count=1)
    transport = FakeTransport(error=DeliveryUncertain("timeout"))
    with pytest.raises(ValueError, match="repetido automaticamente"):
        confirm_send(store, record, principal, transport)
    saved = NotificationsStore(store).get_by_key(idempotency_key(record["id"]))
    assert saved["status"] == "SENDING"
    with pytest.raises(ValueError, match="repetido automaticamente"):
        confirm_send(store, record, principal, FakeTransport())
    assert len(transport.calls) == 1


def test_missing_configuration_does_not_mark_sent(store, monkeypatch):
    monkeypatch.delenv("GMAIL_SEND_ENABLED", raising=False)
    monkeypatch.delenv("GMAIL_SEND_SERVICE_ACCOUNT", raising=False)
    principal, record = _protocolled(store)
    _people(store, count=1)
    with pytest.raises(NotConfigured, match=NOT_CONFIGURED):
        confirm_send(store, record, principal)
    saved = NotificationsStore(store).get_by_key(idempotency_key(record["id"]))
    assert saved["status"] == "DRAFT"
    assert "googleapiclient" not in sys.modules


def test_concurrent_claims_send_once(store):
    principal, record = _protocolled(store)
    _people(store, count=1)
    ensure_draft(store, record, principal)
    barrier = Barrier(2)
    transport = FakeTransport()
    errors = []

    def worker():
        barrier.wait()
        try:
            confirm_send(store, record, principal, transport)
        except Exception as exc:
            errors.append(exc)

    threads = [Thread(target=worker), Thread(target=worker)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    saved = NotificationsStore(store).get_by_key(idempotency_key(record["id"]))
    assert saved["status"] == "SENT"
    assert len(transport.calls) == 1
    assert errors


def test_unauthorized_user_cannot_confirm_or_edit_recipients(store):
    principal, record = _protocolled(store)
    _people(store, count=1)
    denied = _principal(store, email="sem-rep-mail@test.local", representacoes=False)
    with pytest.raises(ValueError, match="não autorizado"):
        confirm_send(store, record, denied, FakeTransport())
    member = store.catalog("procuradores")[0]
    with pytest.raises(ValueError, match="não autorizado"):
        save_recipient(
            store,
            denied,
            membro_tipo="PROCURADOR",
            membro_id=member["id"],
            email="novo@tce.pb.gov.br",
            ativo=True,
        )


def test_rendering_the_notice_does_not_call_the_provider(store, monkeypatch):
    called = []
    monkeypatch.setattr(
        "services.email_transport.institutional_transport",
        lambda: called.append("transport") or FakeTransport(),
    )
    _principal_record = _protocolled(store)
    notice_summary(store, _principal_record[1])
    import services.representacoes as rules
    import services.representacoes_ui as ui

    assert "gmail_transport" not in rules.__file__
    assert "googleapiclient" not in open(ui.__file__, encoding="utf-8").read()
    assert "notificacoes_email" not in open(rules.__file__, encoding="utf-8").read()
    assert called == []
