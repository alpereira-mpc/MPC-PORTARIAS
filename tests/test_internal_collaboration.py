import json

import pytest

from database.access import AccessStore
from database.internal_collaboration import InternalCollaborationStore
from database.oficios import OficiosStore
from database.store import now
from tests.test_oficios import files, ready, sample


def _user(store, email, *, oficios=False, memorandos=False, gabinetes=None):
    return AccessStore(store).save_user(
        {
            "nome": email.split("@")[0].title(),
            "email": email,
            "perfil": "USUARIO",
            "pode_oficios": oficios,
            "pode_memorandos": memorandos,
            "gabinetes": gabinetes or [],
        }
    )


def _received(service, member):
    return service.save(
        {
            "direcao": "RECEBIDO",
            "numero_externo": "42/2026",
            "remetente": "Órgão externo",
            "instituicao": "Instituição",
            "data": "2026-09-15",
            "data_recebimento": "2026-09-16",
            "membros": [member],
            "assunto": "Resposta recebida",
        }
    )


def test_response_tracking_link_and_unlink_preserve_numbering(store):
    service = ready(store)
    sent_id = service.save(sample(service))
    sent = service.finalize(sent_id, files)
    member = sent["membro_id"]
    received_id = _received(service, member)

    service.set_response_tracking(sent_id, True, "2026-10-20")
    tracked = service.get(sent_id)
    assert tracked["aguarda_resposta"] is True
    assert tracked["data_esperada_resposta"] == "2026-10-20"
    assert tracked["status"] == "Aguardando resposta"

    service.link_response(sent_id, received_id)
    linked = service.get(sent_id)
    assert linked["responde_a"] == received_id
    assert linked["status"] == "Respondido"
    assert service.response_candidates(sent_id, member)[0]["id"] == received_id
    other_sent = service.finalize(service.save(sample(service)), files)
    with pytest.raises(ValueError, match="já está vinculado"):
        service.link_response(other_sent["id"], received_id)

    sequence_before_unlink = service.sequence("PROGE", 2026)["proximo"]
    service.unlink_response(sent_id)
    unlinked = service.get(sent_id)
    assert unlinked["responde_a"] is None
    assert unlinked["status"] == "Aguardando resposta"
    assert unlinked["numero"] == sent["numero"]
    assert service.sequence("PROGE", 2026)["proximo"] == sequence_before_unlink


def test_notes_are_isolated_by_module_and_removed_with_origin(store):
    actor = AccessStore(store).get_by_email("admin@test.local")["id"]
    service = OficiosStore(store)
    office_id = service.save(sample(service))
    stamp = now()
    with store.connection() as c:
        c.execute(
            "INSERT INTO memorandos(id,tipo,status,criado_por,criado_em,atualizado_em,"
            "finalizado_em,numero_oficial,payload) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                office_id,
                "SUBSTITUICAO",
                "RASCUNHO",
                "admin@test.local",
                stamp,
                stamp,
                None,
                "",
                json.dumps({}),
            ),
        )
    repository = InternalCollaborationStore(store)
    repository.add_note("oficio_enviado", office_id, actor, "Nota do ofício")
    repository.add_note("memorando", office_id, actor, "Nota do memorando")

    assert [
        n["texto"]
        for n in repository.list_notes("oficio_enviado", office_id, actor)
    ] == ["Nota do ofício"]
    assert [
        n["texto"] for n in repository.list_notes("memorando", office_id, actor)
    ] == ["Nota do memorando"]

    service.delete_draft(office_id, True)
    with store.connection(read_only=True) as c:
        assert c.execute(
            "SELECT COUNT(*) FROM notas_internas "
            "WHERE origem_modulo='oficio_enviado' AND origem_id=?",
            (office_id,),
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM notas_internas WHERE origem_modulo='memorando' AND origem_id=?",
            (office_id,),
        ).fetchone()[0] == 1


def test_forwarding_explicit_acknowledgement_completion_and_permissions(store):
    sender = AccessStore(store).get_by_email("admin@test.local")["id"]
    recipient = _user(
        store,
        "recipient@test.local",
        oficios=True,
        gabinetes=["PROGE"],
    )
    outsider = _user(store, "outsider@test.local", memorandos=True)
    service = OficiosStore(store)
    office_id = service.save(sample(service))
    repository = InternalCollaborationStore(store)

    forwarding_id = repository.create_forwarding(
        "oficio_enviado",
        office_id,
        sender,
        "Analisar e responder.",
        recipient_id=recipient,
        due="2026-10-30",
    )
    row = repository.list_forwardings("oficio_enviado", office_id, recipient)[0]
    assert row["id"] == forwarding_id
    assert row["status"] == "PENDENTE"
    assert row["ciente_em"] is None

    with pytest.raises(ValueError, match="Acesso não autorizado|destinatário"):
        repository.mark_acknowledged(forwarding_id, outsider)
    repository.mark_acknowledged(forwarding_id, recipient)
    acknowledged = repository.list_forwardings("oficio_enviado", office_id, recipient)[0]
    assert acknowledged["status"] == "CIENTE"
    assert acknowledged["ciente_por_id"] == recipient

    repository.conclude(forwarding_id, recipient)
    concluded = repository.list_forwardings("oficio_enviado", office_id, recipient)[0]
    assert concluded["status"] == "CONCLUIDO"
    assert concluded["ciente_em"]
    assert concluded["concluido_por_id"] == recipient

    with pytest.raises(ValueError, match="não possui acesso"):
        repository.create_forwarding(
            "oficio_enviado",
            office_id,
            sender,
            "Não deve ampliar acesso.",
            recipient_id=outsider,
        )


def test_forwarding_author_cannot_acknowledge_or_conclude_own_request(store):
    author = _user(
        store,
        "author-cabinet@test.local",
        oficios=True,
        gabinetes=["PROGE"],
    )
    other_member = _user(
        store,
        "other-cabinet@test.local",
        oficios=True,
        gabinetes=["PROGE"],
    )
    service = OficiosStore(store)
    office_id = service.save(sample(service))
    repository = InternalCollaborationStore(store)
    forwarding_id = repository.create_forwarding(
        "oficio_enviado",
        office_id,
        author,
        "Providência para o gabinete.",
        cabinet="PROGE",
    )

    author_view = repository.list_forwardings("oficio_enviado", office_id, author)[0]
    assert author_view["pode_agir"] is False
    with pytest.raises(ValueError, match="destinatário"):
        repository.mark_acknowledged(forwarding_id, author)
    with pytest.raises(ValueError, match="destinatário"):
        repository.conclude(forwarding_id, author)

    other_view = repository.list_forwardings(
        "oficio_enviado", office_id, other_member
    )[0]
    assert other_view["pode_agir"] is True
    repository.mark_acknowledged(forwarding_id, other_member)
    repository.conclude(forwarding_id, other_member)
    concluded = repository.list_forwardings(
        "oficio_enviado", office_id, other_member
    )[0]
    assert concluded["status"] == "CONCLUIDO"
    assert concluded["ciente_por_id"] == other_member
    assert concluded["concluido_por_id"] == other_member


def test_administrator_author_and_self_recipient_cannot_act(store):
    administrator = AccessStore(store).get_by_email("admin@test.local")["id"]
    service = OficiosStore(store)
    office_id = service.save(sample(service))
    repository = InternalCollaborationStore(store)
    forwarding_id = repository.create_forwarding(
        "oficio_enviado",
        office_id,
        administrator,
        "Providência individual.",
        recipient_id=administrator,
    )

    row = repository.list_forwardings(
        "oficio_enviado", office_id, administrator
    )[0]
    assert row["pode_agir"] is False
    with pytest.raises(ValueError, match="destinatário"):
        repository.mark_acknowledged(forwarding_id, administrator)
    with pytest.raises(ValueError, match="destinatário"):
        repository.conclude(forwarding_id, administrator)
