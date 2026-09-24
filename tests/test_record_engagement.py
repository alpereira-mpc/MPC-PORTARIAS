from datetime import datetime, timedelta

import pytest

from database.access import AccessStore
from database.oficios import OficiosStore
from database.record_engagement import RecordEngagementStore
from database.store import now
from database.tarefas import TarefasStore
from services.access import resolve_principal
from services.audit import INSTITUTIONAL_TZ
from services.audit import registrar_evento
from tests.access_testing import seed_access
from tests.test_oficios import sample


def _admin(store):
    return resolve_principal(store, {"email": "admin@test.local"})


def _user(store, email, *, oficios=True, gabinetes=None):
    identifier = seed_access(
        store,
        email=email,
        nome=email.split("@")[0],
        perfil="USUARIO",
        pode_oficios=oficios,
        pode_memorandos=True,
        pode_representacoes=True,
        pode_ouvidoria=True,
        pode_admin=False,
        gabinetes=gabinetes or (["PROGE"] if oficios else []),
    )
    return AccessStore(store).get(identifier)


def _origins(store):
    office_service = OficiosStore(store)
    office_id = office_service.save(sample(office_service))
    member = next(
        row["membro_id"] for row in office_service.series() if row["sigla"] == "PROGE"
    )
    received_id = office_service.save(
        {
            "direcao": "RECEBIDO",
            "numero_externo": "99/2026",
            "remetente": "Órgão externo",
            "instituicao": "Instituição",
            "data": "2026-09-15",
            "data_recebimento": "2026-09-16",
            "membros": [member],
            "assunto": "Recebido para tarefa",
        }
    )
    stamp = now()
    with store.connection() as c:
        memo_id = "memo-engagement"
        c.execute(
            "INSERT INTO memorandos(id,tipo,status,criado_por,criado_em,atualizado_em,"
            "finalizado_em,numero_oficial,payload) VALUES(?,?,?,?,?,?,?,?,?)",
            (memo_id, "SUBSTITUICAO", "RASCUNHO", "admin@test.local", stamp, stamp, None, "", "{}"),
        )
        rep_id = c.execute(
            "INSERT INTO representacoes(titulo,objeto,origem,data_abertura,prioridade,situacao,"
            "criado_em,criado_por,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?)",
            ("Projeto teste", "Objeto", "DE_OFICIO", "2026-09-01", "NORMAL", "IDEIA", stamp, "admin@test.local", stamp, "admin@test.local"),
        ).lastrowid
        ouvi_id = c.execute(
            "INSERT INTO ouvidoria_manifestacoes(numero_interno,tipo,forma_recebimento,"
            "data_recebimento,titulo,classificacao_acesso,prioridade,situacao,criado_em,"
            "criado_por,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            ("NF-ENG-1", "NOTICIA_FATO", "EMAIL", "2026-09-01", "Notícia teste", "RESTRITA", "NORMAL", "RECEBIDA", stamp, "admin@test.local", stamp, "admin@test.local"),
        ).lastrowid
    return office_service, {
        "oficio_enviado": office_id,
        "oficio_recebido": received_id,
        "memorando": memo_id,
        "representacao": rep_id,
        "ouvidoria": ouvi_id,
    }


def test_tasks_link_to_each_origin_and_unlinked_tasks_still_work(store):
    _, origins = _origins(store)
    owner = _admin(store)
    repo = TarefasStore(store)
    created = []
    for module, identifier in origins.items():
        created.append(
            repo.create(
                owner.id,
                {"titulo": "Tarefa " + module, "origem_modulo": module, "origem_id": identifier},
            )
        )
    plain = repo.create(owner.id, {"titulo": "Sem origem"})
    assert plain["origem_modulo"] is None
    assert {task["origem_modulo"] for task in created} == set(origins)


def test_related_tasks_are_origin_scoped_and_many_to_one(store):
    _, origins = _origins(store)
    owner = _admin(store)
    repo = TarefasStore(store)
    office_id = origins["oficio_enviado"]
    first = repo.create(owner.id, {"titulo": "Primeira", "origem_modulo": "oficio_enviado", "origem_id": office_id})
    second = repo.create(owner.id, {"titulo": "Segunda", "origem_modulo": "oficio_enviado", "origem_id": office_id})
    repo.create(owner.id, {"titulo": "Outra", "origem_modulo": "memorando", "origem_id": origins["memorando"]})
    assert {row["id"] for row in repo.list_related("oficio_enviado", office_id, owner.id)} == {first["id"], second["id"]}


def test_deleting_origin_detaches_but_does_not_delete_task(store):
    office_service, origins = _origins(store)
    owner = _admin(store)
    repo = TarefasStore(store)
    task = repo.create(owner.id, {"titulo": "Preservada", "origem_modulo": "oficio_enviado", "origem_id": origins["oficio_enviado"]})
    office_service.delete_draft(origins["oficio_enviado"], True)
    kept = repo.get(task["id"], owner.id)
    assert kept and kept["origem_modulo"] is None and kept["origem_id"] is None


def test_origin_link_never_expands_permission(store):
    _, origins = _origins(store)
    outsider = _user(store, "without-office@test.local", oficios=False)
    with pytest.raises(ValueError, match="Acesso não autorizado"):
        TarefasStore(store).create(
            outsider["id"],
            {"titulo": "Não autorizada", "origem_modulo": "oficio_enviado", "origem_id": origins["oficio_enviado"]},
        )


def test_task_origin_navigation_revalidates_backend_access(store, monkeypatch):
    _, origins = _origins(store)
    owner = _admin(store)
    captured = []
    monkeypatch.setattr(
        "portal.request_portal_navigation",
        lambda module, **state: captured.append((module, state)),
    )
    from services.record_engagement_ui import open_linked_origin

    open_linked_origin("oficio_enviado", origins["oficio_enviado"], store, owner)
    assert captured == [
        (
            "Ofícios",
            {
                "pending_open_oficio": {
                    "id": origins["oficio_enviado"],
                    "page": "Acompanhamento",
                }
            },
        )
    ]
    outsider = _user(store, "revoked-origin@test.local", oficios=False)
    with pytest.raises(ValueError, match="Acesso não autorizado"):
        open_linked_origin(
            "oficio_enviado", origins["oficio_enviado"], store, resolve_principal(store, {"email": outsider["email"]})
        )


def test_reminder_lifecycle_is_owner_scoped_and_updates_same_row(store):
    _, origins = _origins(store)
    owner = _admin(store)
    other = _user(store, "other-reminder@test.local")
    repo = RecordEngagementStore(store)
    future = datetime.now(INSTITUTIONAL_TZ) + timedelta(days=2)
    past = datetime.now(INSTITUTIONAL_TZ) - timedelta(minutes=1)
    future_id = repo.create_reminder(owner.id, "oficio_enviado", origins["oficio_enviado"], future.isoformat(), "Futuro")
    due_id = repo.create_reminder(owner.id, "oficio_enviado", origins["oficio_enviado"], past.isoformat(), "Vencido")
    repeated = repo.create_reminder(
        owner.id, "oficio_enviado", origins["oficio_enviado"], past.isoformat(), "Vencido de novo"
    )
    assert repeated == due_id
    assert [row["id"] for row in repo.due(owner.id, datetime.now(INSTITUTIONAL_TZ))] == [due_id]
    assert repo.due(other["id"], datetime.now(INSTITUTIONAL_TZ)) == []
    tomorrow = datetime.now(INSTITUTIONAL_TZ) + timedelta(days=1)
    repo.reschedule(due_id, owner.id, tomorrow.isoformat())
    assert repo.due(owner.id, datetime.now(INSTITUTIONAL_TZ)) == []
    repo.remind_tomorrow(due_id, owner.id, datetime.now(INSTITUTIONAL_TZ))
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM avisos_usuario WHERE id=?", (due_id,)).fetchone()[0] == 1
        assert (
            c.execute(
                "SELECT COUNT(*) FROM avisos_usuario WHERE usuario_id=? AND tipo='LEMBRETE'",
                (owner.id,),
            ).fetchone()[0]
            == 2
        )
    repo.reschedule(due_id, owner.id, past.isoformat())
    repo.conclude(due_id, owner.id)
    assert repo.due(owner.id, datetime.now(INSTITUTIONAL_TZ)) == []
    assert future_id != due_id


def test_follow_event_is_deduplicated_excludes_actor_and_unfollow_stops_delivery(store):
    _, origins = _origins(store)
    actor = _admin(store)
    follower = _user(store, "follower@test.local")
    repo = RecordEngagementStore(store)
    office_id = origins["oficio_enviado"]
    assert repo.follow(follower["id"], "oficio_enviado", office_id)
    assert not repo.follow(follower["id"], "oficio_enviado", office_id)
    repo.emit("oficio_enviado", office_id, actor.id, "logical:event:1", "Status alterado")
    repo.emit("oficio_enviado", office_id, actor.id, "logical:event:1", "Status alterado")
    notices = repo.due(follower["id"], datetime.now(INSTITUTIONAL_TZ) + timedelta(seconds=1))
    assert len(notices) == 1 and notices[0]["tipo"] == "ATUALIZACAO"
    repo.follow(actor.id, "oficio_enviado", office_id)
    repo.emit("oficio_enviado", office_id, actor.id, "logical:event:2", "Sem ruído próprio")
    assert repo.due(actor.id, datetime.now(INSTITUTIONAL_TZ) + timedelta(seconds=1)) == []
    assert repo.unfollow(follower["id"], "oficio_enviado", office_id)
    repo.emit("oficio_enviado", office_id, actor.id, "logical:event:3", "Não entregar")
    assert len(repo.due(follower["id"], datetime.now(INSTITUTIONAL_TZ) + timedelta(seconds=1))) == 2


def test_schema_is_idempotent_and_indexes_match_queries(store):
    RecordEngagementStore(store).ensure_schema()
    RecordEngagementStore(store).ensure_schema()
    with store.connection(read_only=True) as c:
        task_columns = {row[1] for row in c.execute("PRAGMA table_info(tarefas)")}
        notice_indexes = {row[1] for row in c.execute("PRAGMA index_list(avisos_usuario)")}
        assert {"origem_modulo", "origem_id"} <= task_columns
        assert "avisos_usuario_pendentes_idx" in notice_indexes
        assert "avisos_lembrete_ativo_idx" in notice_indexes


def test_audited_relevant_event_notifies_followers_but_irrelevant_event_does_not(store):
    _, origins = _origins(store)
    actor = _admin(store)
    follower_record = _user(store, "audit-follower@test.local")
    follower = resolve_principal(store, {"email": follower_record["email"]})
    office_id = origins["oficio_enviado"]
    repo = RecordEngagementStore(store)
    repo.follow(follower.id, "oficio_enviado", office_id)
    registrar_evento(
        store,
        evento="MODULO_ACESSADO",
        modulo="oficios",
        acao="LER",
        principal=actor,
        entidade_tipo="oficio",
        entidade_id=office_id,
    )
    assert repo.due(follower.id, datetime.now(INSTITUTIONAL_TZ) + timedelta(seconds=1)) == []
    registrar_evento(
        store,
        evento="OFICIO_ALTERADO",
        modulo="oficios",
        acao="MOVIMENTAR",
        principal=actor,
        entidade_tipo="oficio",
        entidade_id=office_id,
    )
    assert len(repo.due(follower.id, datetime.now(INSTITUTIONAL_TZ) + timedelta(seconds=1))) == 1


def test_alert_origin_opens_via_linked_origin_module(store, monkeypatch):
    _, origins = _origins(store)
    owner = _admin(store)
    captured = []
    monkeypatch.setattr(
        "portal.request_portal_navigation",
        lambda module, **state: captured.append((module, state)),
    )
    monkeypatch.setattr("portal.clear_alerts_overlay", lambda: None)
    monkeypatch.setattr("services.alerts_ui.st.session_state", {})
    from services.alerts import AlertItem, ATENCAO
    from services.alerts_ui import open_alert_origin

    item = AlertItem(
        source_module="oficios",
        source_id=str(origins["oficio_enviado"]),
        gabinete="—",
        severity=ATENCAO,
        category="lembrete_registro",
        title="LEMBRETE",
        description="Texto",
        date=None,
        datetime=None,
        source_status="PENDENTE",
        navigation_target="oficios",
        metadata={
            "notice_id": 1,
            "notice_type": "LEMBRETE",
            "origin_module": "oficio_enviado",
            "origin_id": origins["oficio_enviado"],
        },
    )
    open_alert_origin(item, store, owner)
    assert captured == [
        (
            "Ofícios",
            {
                "pending_open_oficio": {
                    "id": origins["oficio_enviado"],
                    "page": "Acompanhamento",
                }
            },
        )
    ]


def test_representation_status_change_notifies_followers_once(store):
    office_service, _origins_map = _origins(store)
    actor = _admin(store)
    follower = resolve_principal(
        store, {"email": _user(store, "rep-follower@test.local")["email"]}
    )
    member = next(
        row["membro_id"] for row in office_service.series() if row["sigla"] == "PROGE"
    )
    from services.representacoes import create, set_status, update

    record = create(
        store,
        {
            "titulo": "Acompanhar situação",
            "objeto": "Objeto",
            "origem": "DE_OFICIO",
            "data_abertura": "2026-09-01",
            "prioridade": "NORMAL",
            "situacao": "IDEIA",
            "procurador_responsavel": member,
        },
        actor,
    )
    repo = RecordEngagementStore(store)
    repo.follow(follower.id, "representacao", record["id"])
    update(
        store,
        record["id"],
        {
            "titulo": record["titulo"],
            "objeto": record["objeto"],
            "origem": record["origem"],
            "data_abertura": record["data_abertura"],
            "prioridade": record["prioridade"],
            "situacao": "PESQUISA",
            "procurador_responsavel": member,
        },
        actor,
    )
    set_status(store, record["id"], "PESQUISA", actor)
    notices = repo.due(follower.id, datetime.now(INSTITUTIONAL_TZ) + timedelta(seconds=1))
    assert len(notices) == 1
    assert notices[0]["tipo"] == "ATUALIZACAO"
