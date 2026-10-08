from io import BytesIO
import inspect

import pytest
from pypdf import PdfWriter
from streamlit.testing.v1 import AppTest

from database.access import AccessStore
from database.representacoes import DOCUMENT_META, RepresentacoesStore
from database.store import Store
from services.access import (
    can_access_representacoes,
    has_permission,
    principal_from_record,
    require_permission,
    resolve_principal,
)
from services.representacoes import (
    KIND_PROJECT,
    KIND_REPRESENTATION,
    RELATORES,
    RELATORES_SUBSTITUTOS,
    RELATORES_TITULARES,
    add_document,
    add_progress,
    andamento_display,
    assessores,
    can_delete,
    create,
    delete,
    delete_blocked_reason,
    documents,
    download,
    exclude_progress,
    get,
    kind_label,
    list_records,
    overview,
    progress,
    reconcile_signatories,
    register_protocol,
    register_direct_protocol,
    relator_label,
    set_phase,
    set_status,
    signatory_options,
    setor_elegivel,
    update,
)
from tests.access_testing import seed_access

_UI_HOLD = {}


def _pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _principal(store, email="rep@test.local", **flags):
    seed_access(
        store,
        email=email,
        nome="Usuário Representações",
        perfil="USUARIO",
        pode_portarias=flags.get("portarias", True),
        pode_agenda=flags.get("agenda", True),
        pode_oficios=flags.get("oficios", True),
        pode_admin=False,
        pode_memorandos=flags.get("memorandos", True),
        pode_relatorios=flags.get("relatorios", True),
        pode_representacoes=flags.get("representacoes", True),
        pode_representacoes_registrar_protocolo=flags.get("registrar_protocolo", True),
        pode_representacoes_enviar_comunicacao=flags.get("enviar_comunicacao", True),
    )
    return resolve_principal(store, {"email": email})


def _server(store, name="Assessor Teste", setor="PROGE", ativo=1):
    from database.store import now

    stamp = now()
    with store.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        inserted = c.execute(
            "INSERT INTO servidores(nome,nome_normalizado,matricula_original,cargo,setor,"
            "ativo,criado_em,atualizado_em) VALUES(?,?,?,?,?,?,?,?)",
            (name, name.casefold(), "999", "Assessor", setor, ativo, stamp, stamp),
        )
        return inserted.lastrowid


def _payload(store, **changes):
    member = next(p["id"] for p in store.catalog("procuradores") if p.get("ativo"))
    others = [
        p["id"]
        for p in store.catalog("procuradores")
        if p.get("ativo") and p["id"] != member
    ][:2]
    assessor = _server(store)
    data = {
        "titulo": "Representação sobre licitação",
        "objeto": "Apurar irregularidades",
        "origem": "DE_OFICIO",
        "data_abertura": "2026-09-01",
        "representado": "Município X",
        "tema": "Licitações",
        "prioridade": "ALTA",
        "observacoes": "Uso interno",
        "procurador_responsavel": member,
        "procuradores_signatarios": others,
        "assessores": [assessor],
    }
    data.update(changes)
    return data, member, others, assessor


def test_unauthorized_user_cannot_see_or_access_module(store):
    from portal import visible_modules

    denied = _principal(store, email="sem-rep@test.local", representacoes=False)
    assert not can_access_representacoes(denied)
    assert not has_permission(denied, "representacoes")
    with pytest.raises(ValueError, match="não autorizado"):
        require_permission(denied, "representacoes")
    assert "representacoes" not in {module.key for module in visible_modules(denied)}
    assert has_permission(denied, "portarias")
    assert has_permission(denied, "agenda")
    assert has_permission(denied, "oficios")


def test_authorized_user_sees_module(store):
    from portal import visible_modules

    allowed = _principal(store, email="com-rep@test.local", representacoes=True)
    assert can_access_representacoes(allowed)
    assert "representacoes" in {module.key for module in visible_modules(allowed)}


def test_representation_badges_use_central_semantic_variants_and_fallback():
    from services.representacoes_ui import _badge_items

    record = {
        "situacao": "EM_TRAMITACAO",
        "numero_processo": "04421/26",
        "fase_processual": "INSTRUCAO",
    }
    items = _badge_items(record)
    assert [
        (text, tone, item.semantic_class) for item, (text, tone) in zip(items, items)
    ] == [
        ("Representação", "neutral", "rep-type"),
        ("04421/26", "neutral", "rep-identifier"),
        ("Em tramitação", "neutral", "rep-status rep-status-active"),
        ("Instrução inicial", "neutral", "rep-phase rep-phase-instruction"),
    ]
    unknown = _badge_items(
        {"situacao": "FUTURA", "numero_processo": "1/26", "fase_processual": "NOVA"}
    )
    assert unknown[2][0] == "FUTURA"
    assert unknown[2].semantic_class == "rep-status rep-status-unknown"
    assert unknown[3][0] == "NOVA"
    assert unknown[3].semantic_class == "rep-phase rep-phase-unknown"


def test_direct_ui_load_is_blocked(store):
    from services.representacoes_ui import render

    denied = _principal(store, email="ui-rep@test.local", representacoes=False)
    with pytest.raises(ValueError, match="não autorizado"):
        render(store, denied)


def test_admin_receives_representacoes_flag(store):
    admin = resolve_principal(store, {"email": "admin@test.local"})
    assert admin.pode_representacoes
    assert can_access_representacoes(admin)


def test_create_edit_team_protocol_phase_and_delete_rules(store):
    principal = _principal(store)
    payload, responsible, signatories, assessor = _payload(store)
    extra_assessor = _server(store, "Segundo Assessor")
    payload["assessores"] = [assessor, extra_assessor]
    record = create(store, payload, principal)
    assert record["numero_processo"] is None
    assert record["relator"] is None
    assert record["situacao"] == "IDEIA"
    assert record["fase_processual"] is None
    roles = {(item["papel"], item["membro_id"]) for item in record["integrantes"]}
    assert ("PROCURADOR_RESPONSAVEL", responsible) in roles
    assert {item[1] for item in roles if item[0] == "PROCURADOR_SIGNATARIO"} == set(
        signatories
    )
    assert {item[1] for item in roles if item[0] == "ASSESSOR"} == {
        assessor,
        extra_assessor,
    }
    created_events = progress(store, record["id"])
    assert created_events[0]["tipo"] == "CRIADA"
    assert not (created_events[0].get("descricao") or "").strip()
    heading, extra = andamento_display(created_events[0])
    assert heading == "Projeto de Representação criado"
    assert extra == ""
    assert "Representação criada" not in heading
    assert record.get("possui_medida_cautelar") is False
    assert kind_label(record) == KIND_PROJECT
    assert kind_label(get(store, record["id"])) == KIND_PROJECT
    payload["titulo"] = "Título revisado"
    payload["procuradores_signatarios"] = signatories[:1]
    updated = update(store, record["id"], payload, principal)
    assert updated["titulo"] == "Título revisado"
    assert (
        len(
            [
                item
                for item in updated["integrantes"]
                if item["papel"] == "PROCURADOR_SIGNATARIO"
            ]
        )
        == 1
    )
    listed = list_records(store, {"pesquisa": "revisado"})
    assert listed[0]["id"] == record["id"]
    assert "arquivo" not in (listed[0].get("ultimo_andamento") or {})
    protocolled = register_protocol(
        store,
        record["id"],
        {
            "numero_processo": "TC 012345/26",
            "data_protocolo": "2026-09-18",
            "relator": RELATORES[0],
            "fase_processual": "INSTRUCAO",
        },
        principal,
        ("final.pdf", _pdf()),
    )
    assert protocolled["numero_processo"] == "TC 012345/26"
    assert protocolled["relator"] == RELATORES[0]
    assert protocolled["possui_medida_cautelar"] is False
    assert protocolled["situacao"] == "PROTOCOLADA"
    assert protocolled["fase_processual"] == "INSTRUCAO"
    types = [item["tipo"] for item in progress(store, record["id"])]
    assert types.count("PROTOCOLADA") == 1
    protocol_events = [
        item for item in progress(store, record["id"]) if item["tipo"] == "PROTOCOLADA"
    ]
    assert protocol_events[0]["descricao"].startswith("Representação protocolada.")
    assert kind_label(protocolled) == KIND_REPRESENTATION
    assert kind_label(get(store, record["id"])) == KIND_REPRESENTATION
    assert protocolled["id"] == record["id"]
    moved = set_phase(store, record["id"], "ANALISE_DEFESA", principal)
    assert moved["fase_processual"] == "ANALISE_DEFESA"
    assert moved["situacao"] == "PROTOCOLADA"
    with pytest.raises(ValueError, match="não pode ser excluída"):
        delete(store, record["id"])
    with store.connection(read_only=True) as c:
        leftover = c.execute(
            "SELECT COUNT(*) FROM representacao_integrantes WHERE representacao_id=?",
            (record["id"],),
        ).fetchone()[0]
    assert leftover >= 1


def test_direct_protocol_is_atomic_and_uses_the_existing_followup_flow(store):
    principal = _principal(store, email="direct-rep@test.local")
    base, responsible, *_ = _payload(store)
    protocol = {
        "numero_processo": "TC 030001/26",
        "data_protocolo": "2026-09-20",
        "relator": RELATORES[0],
        "fase_processual": "INSTRUCAO",
        "possui_medida_cautelar": True,
    }
    before = overview(store)
    with pytest.raises(ValueError, match="PDF final"):
        register_direct_protocol(store, base, protocol, principal, None)
    assert list_records(store, {"numero_processo": "030001"}) == []

    content = _pdf()
    saved = register_direct_protocol(
        store, base, protocol, principal, ("representacao.pdf", content)
    )
    assert saved["registro_origem"] == "DIRETO"
    assert saved["situacao"] == "PROTOCOLADA"
    assert saved["fase_processual"] == "INSTRUCAO"
    assert saved["possui_medida_cautelar"] is True
    assert saved["numero_processo"] == protocol["numero_processo"]
    assert any(member["membro_id"] == responsible for member in saved["integrantes"])
    assert [item["tipo"] for item in progress(store, saved["id"])] == ["PROTOCOLADA"]
    assert overview(store) == {**before, "tramitacao": before["tramitacao"] + 1}
    assert list_records(store, {"numero_processo": "030001"})[0]["id"] == saved["id"]
    official = next(
        item
        for item in documents(store, saved["id"])
        if item["tipo_documento"] == "REPRESENTACAO_FINAL"
    )
    assert download(store, official["id"])["conteudo"] == content
    with store.connection(read_only=True) as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM auditoria_eventos "
                "WHERE evento=? AND entidade_id=?",
                ("REPRESENTACAO_PROTOCOLADA", str(saved["id"])),
            ).fetchone()[0]
            == 1
        )
    moved = add_progress(
        store,
        saved["id"],
        {"data": "2026-09-21", "tipo": "LIVRE", "descricao": "Em análise"},
        principal,
    )
    assert moved[0]["tipo"] == "LIVRE"
    assert get(store, saved["id"])["situacao"] == "EM_TRAMITACAO"
    assert overview(store)["tramitacao"] == before["tramitacao"] + 1


def test_overview_counts_active_protocols_once_regardless_of_phase(store):
    principal = _principal(store, email="kpis-rep@test.local")
    base, *_ = _payload(store)
    preparation = create(store, {**base, "titulo": "Em preparação"}, principal)
    waiting = create(store, {**base, "titulo": "Aguardando protocolo"}, principal)
    set_status(store, waiting["id"], "AGUARDANDO_PROTOCOLO", principal)

    active_ids = []
    for index, phase in enumerate(("MPC", "PAUTA", "MPC", "PAUTA"), start=1):
        project = create(store, {**base, "titulo": f"Protocolo {index}"}, principal)
        saved = register_protocol(
            store,
            project["id"],
            {"numero_processo": f"TC {index:06d}/26", "relator": RELATORES[0]},
            principal,
        )
        if index > 2:
            add_progress(
                store,
                saved["id"],
                {"tipo": "LIVRE", "descricao": "Andamento registrado"}, principal,
            )
        set_phase(store, saved["id"], phase, principal)
        active_ids.append(saved["id"])

    judged_project = create(store, {**base, "titulo": "Julgada"}, principal)
    judged = register_protocol(
        store, judged_project["id"],
        {"numero_processo": "TC 000005/26", "relator": RELATORES[0]}, principal,
    )
    set_phase(store, judged["id"], "PAUTA", principal)
    set_status(store, judged["id"], "JULGADA", principal)
    closed_project = create(store, {**base, "titulo": "Encerrada"}, principal)
    closed = register_protocol(
        store, closed_project["id"],
        {"numero_processo": "TC 000006/26", "relator": RELATORES[0]}, principal,
    )
    set_phase(store, closed["id"], "MPC", principal)
    set_status(store, closed["id"], "ENCERRADA", principal)

    assert overview(store) == {
        "preparacao": 1,
        "aguardando_protocolo": 1,
        "tramitacao": 4,
        "julgadas": 1,
    }
    assert get(store, preparation["id"])["situacao"] == "IDEIA"
    assert [get(store, identifier)["situacao"] for identifier in active_ids] == [
        "PROTOCOLADA", "PROTOCOLADA", "EM_TRAMITACAO", "EM_TRAMITACAO"
    ]
    assert get(store, judged["id"])["fase_processual"] == "PAUTA"
    assert get(store, closed["id"])["fase_processual"] == "MPC"
    assert {item["id"] for item in list_records(store, {"fase_processual": "MPC"})} >= {
        active_ids[0], active_ids[2], closed["id"]
    }
    assert {
        item["id"] for item in list_records(store, {"fase_processual": "PAUTA"})
    } >= {
        active_ids[1], active_ids[3], judged["id"]
    }


def test_home_kpis_show_only_four_situation_cards():
    def page():
        from services.representacoes_ui import _kpis

        _kpis(
            {"preparacao": 1, "aguardando_protocolo": 2, "tramitacao": 3, "julgadas": 4}
        )

    app = AppTest.from_function(page, default_timeout=30).run()
    assert [(item.label, int(item.value)) for item in app.metric] == [
        ("Em preparação", 1),
        ("Aguardando protocolo", 2),
        ("Em tramitação", 3),
        ("Julgadas", 4),
    ]
    assert not app.exception


def test_protocol_number_cannot_be_duplicated_across_registration_paths(store):
    principal = _principal(store, email="dupe-rep@test.local")
    base, *_ = _payload(store)
    protocol = {"numero_processo": "TC 003/26", "relator": RELATORES[0]}
    direct = register_direct_protocol(
        store, base, protocol, principal, ("representacao.pdf", _pdf())
    )
    with pytest.raises(ValueError, match="já está cadastrado"):
        register_direct_protocol(
            store,
            base,
            {**protocol, "numero_processo": "003-26"},
            principal,
            ("representacao.pdf", _pdf()),
        )
    assert len(list_records(store, {})) == 1
    project = create(store, {**base, "titulo": "Outro projeto"}, principal)
    with pytest.raises(ValueError, match="já está cadastrado"):
        register_protocol(
            store, project["id"], protocol, principal, ("representacao.pdf", _pdf())
        )
    assert get(store, project["id"])["numero_processo"] is None
    assert get(store, direct["id"])["numero_processo"] == "TC 003/26"


def test_direct_protocol_requires_both_module_and_protocol_permission(store):
    base, *_ = _payload(store)
    protocol = {"numero_processo": "TC 004/26", "relator": RELATORES[0]}
    for flags in ({"representacoes": False}, {"registrar_protocolo": False}):
        principal = _principal(
            store,
            email=f"denied-{len(flags)}-{flags.get('representacoes')}@test.local",
            **flags,
        )
        with pytest.raises(ValueError, match="Acesso não autorizado"):
            register_direct_protocol(
                store, base, protocol, principal, ("representacao.pdf", _pdf())
            )
    assert list_records(store, {}) == []


def test_direct_protocol_rolls_back_when_document_write_fails(store, monkeypatch):
    from database.notifications import NotificationsStore

    principal = _principal(store, email="rollback-rep@test.local")
    base, *_ = _payload(store)

    def fail(*_args):
        raise RuntimeError("Falha simulada ao gravar PDF")

    monkeypatch.setattr(RepresentacoesStore, "_add_protocol_artifacts", fail)
    with pytest.raises(RuntimeError, match="Falha simulada"):
        register_direct_protocol(
            store,
            base,
            {"numero_processo": "TC 005/26", "relator": RELATORES[0]},
            principal,
            ("representacao.pdf", _pdf()),
        )
    assert list_records(store, {}) == []
    with store.connection(read_only=True) as c:
        assert (
            c.execute("SELECT COUNT(*) FROM representacao_integrantes").fetchone()[0]
            == 0
        )
        assert (
            c.execute("SELECT COUNT(*) FROM representacao_andamentos").fetchone()[0]
            == 0
        )
        assert (
            c.execute("SELECT COUNT(*) FROM representacao_documentos").fetchone()[0]
            == 0
        )
    NotificationsStore(store)
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM notificacoes_email").fetchone()[0] == 0


def test_first_registered_progress_moves_only_protocolled_record_to_tramitacao(store):
    principal = _principal(store, email="status-rep@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    protocolled = register_protocol(
        store,
        record["id"],
        {
            "numero_processo": "TC 05657/26",
            "data_protocolo": "2026-10-01",
            "relator": RELATORES[0],
            "fase_processual": "INSTRUCAO",
        },
        principal,
    )
    assert protocolled["situacao"] == "PROTOCOLADA"
    assert protocolled["fase_processual"] == "INSTRUCAO"

    payload["titulo"] = "Cadastro sem andamento"
    update(store, record["id"], payload, principal)
    add_document(
        store,
        record["id"],
        {
            "tipo_documento": "OUTROS",
            "data_documento": "2026-10-02",
            "descricao": "Anexo isolado.",
        },
        "anexo.pdf",
        _pdf(),
        principal,
    )
    assert get(store, record["id"])["situacao"] == "PROTOCOLADA"

    add_progress(
        store,
        record["id"],
        {
            "tipo": "LIVRE",
            "data": "2026-10-03",
            "descricao": "Primeiro andamento no Tramita.",
        },
        principal,
    )
    assert get(store, record["id"])["situacao"] == "EM_TRAMITACAO"
    assert get(store, record["id"])["fase_processual"] == "INSTRUCAO"
    listed = next(item for item in list_records(store) if item["id"] == record["id"])
    assert listed["situacao"] == "EM_TRAMITACAO"
    with store.connection(read_only=True) as c:
        audit = c.execute(
            "SELECT detalhes_json FROM auditoria_eventos WHERE evento=? AND entidade_id=? "
            "ORDER BY id DESC LIMIT 1",
            ("REPRESENTACAO_STATUS_ALTERADO", str(record["id"])),
        ).fetchone()
    assert audit is not None and "primeiro_andamento" in audit["detalhes_json"]

    reopened = Store(store.path)
    assert get(reopened, record["id"])["situacao"] == "EM_TRAMITACAO"
    add_progress(
        reopened,
        record["id"],
        {"tipo": "LIVRE", "data": "2026-10-04", "descricao": "Novo andamento."},
        principal,
    )
    assert get(reopened, record["id"])["situacao"] == "EM_TRAMITACAO"

    final_record = create(reopened, payload, principal)
    final_protocolled = register_protocol(
        reopened,
        final_record["id"],
        {
            "numero_processo": "TC 05483/26",
            "data_protocolo": "2026-10-01",
            "relator": RELATORES[0],
        },
        principal,
    )
    from services.representacoes import set_status

    set_status(reopened, final_record["id"], "JULGADA", principal)
    add_progress(
        reopened,
        final_record["id"],
        {"tipo": "JULGADO", "data": "2026-10-04", "descricao": ""},
        principal,
    )
    assert final_protocolled["situacao"] == "PROTOCOLADA"
    assert get(reopened, final_record["id"])["situacao"] == "JULGADA"


def test_progress_documents_metadata_and_orphan_cleanup(store):
    principal = _principal(store, email="docs-rep@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    add_progress(
        store,
        record["id"],
        {"tipo": "MINUTA_PREPARADA", "data": "2026-09-10", "descricao": ""},
        principal,
    )
    timeline = progress(store, record["id"])
    assert [item["tipo"] for item in timeline][0] == "MINUTA_PREPARADA"
    add_document(
        store,
        record["id"],
        {
            "tipo_documento": "MINUTA",
            "descricao": "Minuta inicial",
            "data_documento": "2026-09-10",
        },
        "minuta.pdf",
        _pdf(),
        principal,
    )
    meta = documents(store, record["id"])
    assert meta and "arquivo" not in meta[0]
    assert set(meta[0]) <= set(DOCUMENT_META.split(",")) | set(meta[0])
    for column in DOCUMENT_META.split(","):
        assert column in meta[0]
    assert "arquivo" not in meta[0]
    file = download(store, meta[0]["id"])
    assert file["conteudo"].startswith(b"%PDF-")
    with pytest.raises(ValueError, match="Aceitos apenas"):
        add_document(
            store,
            record["id"],
            {"tipo_documento": "OUTROS"},
            "nota.txt",
            b"texto",
            principal,
        )
    identifier = record["id"]
    delete(store, identifier)
    assert get(store, identifier) is None
    with store.connection(read_only=True) as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM representacao_integrantes WHERE representacao_id=?",
                (identifier,),
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM representacao_documentos WHERE representacao_id=?",
                (identifier,),
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM representacao_andamentos WHERE representacao_id=?",
                (identifier,),
            ).fetchone()[0]
            == 0
        )


def test_list_does_not_select_blob(store):
    principal = _principal(store, email="blob-rep@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    add_document(
        store,
        record["id"],
        {"tipo_documento": "PESQUISA"},
        "pesquisa.pdf",
        _pdf(),
        principal,
    )
    rows = list_records(store)
    assert rows
    assert "arquivo" not in rows[0]
    sql = RepresentacoesStore.list.__doc__ or ""
    assert "BLOB" not in DOCUMENT_META


def test_permission_does_not_grant_other_modules(store):
    only = _principal(
        store,
        email="somente-rep@test.local",
        portarias=False,
        agenda=False,
        oficios=False,
        memorandos=False,
        relatorios=False,
        representacoes=True,
    )
    assert has_permission(only, "representacoes")
    assert not has_permission(only, "portarias")
    assert not has_permission(only, "admin")
    record = AccessStore(store).get_by_email("somente-rep@test.local")
    principal = principal_from_record(record)
    assert principal.pode_representacoes
    assert not principal.pode_portarias


def test_responsible_procurador_is_excluded_from_signatories(store):
    people = [p["id"] for p in store.catalog("procuradores") if p.get("ativo")]
    responsible = people[0]
    others = people[1:]
    options = signatory_options(people, responsible)
    assert responsible not in options
    assert options == others
    assert set(options) == set(people) - {responsible}
    kept = reconcile_signatories([responsible, others[0]], options)
    assert kept == [others[0]]
    principal = _principal(store, email="sign-rep@test.local")
    payload, member, signatories, assessor = _payload(store)
    payload["procuradores_signatarios"] = [member, *signatories]
    record = create(store, payload, principal)
    saved = [
        item["membro_id"]
        for item in record["integrantes"]
        if item["papel"] == "PROCURADOR_SIGNATARIO"
    ]
    assert member not in saved
    assert set(saved) == set(signatories)


def test_assessores_only_from_allowed_lotacoes(store):
    allowed = _server(store, "Na PROGE", setor="PROGE")
    gabinete = _server(store, "No gabinete", setor="Gabinete BTLC")
    espo = _server(store, "Na ESPO", setor="espo")
    other = _server(store, "Na Câmara", setor="1CAM")
    tce = _server(store, "Outra unidade", setor="Secretaria da 1ª Câmara")
    inactive = _server(store, "Inativo PROGE", setor="PROGE", ativo=0)
    rows = assessores(store)
    ids = {row["id"] for row in rows}
    assert allowed in ids
    assert gabinete in ids
    assert espo in ids
    assert other not in ids
    assert tce not in ids
    assert inactive not in ids
    assert setor_elegivel("  mtff ")
    assert not setor_elegivel("IBMF")
    principal = _principal(store, email="lotacao-rep@test.local")
    payload, *_ = _payload(store)
    payload["assessores"] = [allowed, gabinete]
    record = create(store, payload, principal)
    saved = [
        item["membro_id"]
        for item in record["integrantes"]
        if item["papel"] == "ASSESSOR"
    ]
    assert set(saved) == {allowed, gabinete}
    payload["assessores"] = [tce]
    with pytest.raises(ValueError, match="Assessor"):
        create(store, payload, principal)


def test_card_badges_unpack_label_and_tone(store):
    from services.representacoes_ui import _badge_items
    from services.ui_theme import TONES, badges

    principal = _principal(store, email="badge-rep@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    items = _badge_items(record)
    assert items[0][0] == KIND_PROJECT
    assert all(isinstance(tone, str) for _, tone in items)
    assert all(tone in TONES for _, tone in items)
    html = badges(*items)
    assert KIND_PROJECT in html
    listed = list_records(store)
    card = next(row for row in listed if row["id"] == record["id"])
    assert badges(*_badge_items(card))

    protocolled = register_protocol(
        store,
        record["id"],
        {
            "numero_processo": "TC 099001/26",
            "data_protocolo": "2026-09-21",
            "relator": RELATORES_SUBSTITUTOS[0],
        },
        principal,
    )
    after = _badge_items(protocolled)
    assert after[0][0] == KIND_REPRESENTATION
    assert all(isinstance(tone, str) for _, tone in after)
    assert all(tone in TONES for _, tone in after)
    html_after = badges(*after)
    assert KIND_REPRESENTATION in html_after
    reloaded = get(store, record["id"])
    assert _badge_items(reloaded)[0][0] == KIND_REPRESENTATION


def test_relatores_and_medida_cautelar(store):
    import inspect
    from services.representacoes_ui import _protocol_fields, _protocol_form

    source = inspect.getsource(_protocol_fields)
    assert 'st.text_input("Relator' not in source
    assert "selectbox" in source
    assert "RELATORES" in source
    assert "Possui pedido de medida cautelar?" in source
    assert "_protocol_fields(prefix)" in inspect.getsource(_protocol_form)
    assert set(RELATORES_TITULARES) | set(RELATORES_SUBSTITUTOS) == set(RELATORES)
    assert set(RELATORES_SUBSTITUTOS) <= set(RELATORES)
    assert " — Conselheiro Substituto" in relator_label(RELATORES_SUBSTITUTOS[0])
    principal = _principal(store, email="cautelar-rep@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    with pytest.raises(ValueError, match="Relator"):
        register_protocol(
            store,
            record["id"],
            {
                "numero_processo": "TC 1/26",
                "data_protocolo": "2026-09-21",
                "relator": "Conselheiro inexistente",
            },
            principal,
        )
    protocolled = register_protocol(
        store,
        record["id"],
        {
            "numero_processo": "TC 1/26",
            "data_protocolo": "2026-09-21",
            "relator": RELATORES_TITULARES[2],
            "possui_medida_cautelar": True,
        },
        principal,
    )
    assert protocolled["relator"] == RELATORES_TITULARES[2]
    assert protocolled["possui_medida_cautelar"] is True
    assert get(store, record["id"])["possui_medida_cautelar"] is True
    other, *_rest = _payload(store)
    other["titulo"] = "Sem cautelar"
    second = create(store, other, principal)
    assert second["possui_medida_cautelar"] is False
    saved = register_protocol(
        store,
        second["id"],
        {
            "numero_processo": "TC 2/26",
            "data_protocolo": "2026-09-21",
            "relator": RELATORES_SUBSTITUTOS[1],
            "possui_medida_cautelar": False,
        },
        principal,
    )
    assert saved["possui_medida_cautelar"] is False
    assert saved["relator"] == RELATORES_SUBSTITUTOS[1]
    stale = {
        "tipo": "CRIADA",
        "descricao": "Representação criada.",
        "data": "2026-09-01",
    }
    heading, extra = andamento_display(stale)
    assert heading == "Projeto de Representação criado"
    assert extra == ""
    assert "Representação criada" not in extra


def test_project_can_be_deleted_but_protocolled_cannot(store):
    principal = _principal(store, email="del-rep@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    identifier = record["id"]
    assert can_delete(record)
    assert delete_blocked_reason(record) is None
    delete(store, identifier)
    assert get(store, identifier) is None
    with store.connection(read_only=True) as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM representacao_integrantes WHERE representacao_id=?",
                (identifier,),
            ).fetchone()[0]
            == 0
        )
    again = create(store, payload, principal)
    protocolled = register_protocol(
        store,
        again["id"],
        {
            "numero_processo": "TC 099999/26",
            "data_protocolo": "2026-09-21",
            "relator": RELATORES[0],
            "fase_processual": "INSTRUCAO",
        },
        principal,
    )
    assert not can_delete(protocolled)
    assert "protocolada" in delete_blocked_reason(protocolled)
    with pytest.raises(ValueError, match="não pode ser excluída"):
        delete(store, again["id"])
    from services.representacoes_ui import _detail_body, _detail_chrome, _filters

    source = inspect.getsource(_detail_body)
    assert "Excluir definitivamente" in source
    assert "rep_dt_prg" in source
    assert "rep_toolbar_flow" in source
    assert "Notícia de Fato permanecerá cadastrada" in source
    chrome = inspect.getsource(_detail_chrome)
    assert "--mpc-info-soft" in chrome
    assert "--mpc-page" not in chrome or "transparent" in chrome
    assert "mpc-danger-zone" not in chrome
    assert "max-width:768px" in chrome
    filters = inspect.getsource(_filters)
    assert "Choose an option" not in filters
    assert "[None," not in filters
    assert "number_input" not in filters
    from services.representacoes_ui import _filter_select

    assert "placeholder=empty" in inspect.getsource(_filter_select)
    assert "value=0" not in filters


def test_detail_uses_conditional_sections_and_keeps_protocol_summary():
    import inspect

    from services.representacoes_ui import _detail, _detail_compact

    source = inspect.getsource(_detail_compact)
    assert "st.radio(" in source
    assert '"Visão geral"' in source
    assert '"Andamentos"' in source
    assert '"Documentos"' in source
    assert '"Organização"' in source
    assert '"Gestão"' in source
    assert 'elif section == "Andamentos"' in source
    assert 'elif section == "Documentos"' in source
    assert "Dados do projeto original" in source
    assert source.count("progress(store, record[") == 1
    assert source.count("documents(store, record[") == 1
    assert "_detail_compact(store, principal, record)" in inspect.getsource(_detail)


def test_detail_back_returns_to_filtered_list_from_all_sections(store):
    import services.representacoes_ui as ui

    principal = _principal(store, email="voltar-rep@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    identifier = record["id"]
    _UI_HOLD.clear()
    _UI_HOLD.update(ui=ui, store=store, principal=principal)

    def page():
        from tests.test_representacoes import _UI_HOLD

        _UI_HOLD["ui"].render(_UI_HOLD["store"], _UI_HOLD["principal"])

    for section in (
        "Visão geral",
        "Andamentos",
        "Documentos",
        "Organização",
        "Gestão",
    ):
        app = AppTest.from_function(page, default_timeout=30).run()
        app.text_input(key="rep_f_q").set_value("licitação").run()
        app.text_input(key="rep_f_rep").set_value("Município X").run()
        app.selectbox(key="rep_f_sit").set_value(record["situacao"]).run()
        app.button(key=f"rep_open_{identifier}").click().run()
        assert app.session_state["representacoes_view"] == identifier
        app.radio(key=f"rep_detail_section_{identifier}").set_value(section).run()
        assert not app.exception
        assert sum(button.key == "rep_dt_back" for button in app.button) == 1
        assert app.button[-1].key == "rep_dt_back"
        app.button(key="rep_dt_back").click().run()
        assert "representacoes_view" not in app.session_state
        assert app.text_input(key="rep_f_q").value == "licitação"
        assert app.text_input(key="rep_f_rep").value == "Município X"
        assert app.selectbox(key="rep_f_sit").value == record["situacao"]
        assert not app.exception


def test_representation_navigation_callbacks_restore_list_and_clear_temporary_state(
    monkeypatch,
):
    import services.representacoes_ui as ui

    state = {
        "representacoes_view": 1,
        "representacoes_progress": 1,
        "representacoes_file": 1,
        "representacoes_edit": 1,
        "representacoes_confirm_delete": 1,
        "representacoes_exclude_progress": "andamento-1",
        "representacoes_download": "documento-1",
        "rep_prg_form_2desc": "valor antigo",
        "_representacoes_list_filters": {
            "rep_f_q": "licitação",
            "rep_f_sit": "EM_TRAMITACAO",
        },
    }
    monkeypatch.setattr(ui.st, "session_state", state)

    ui._open_representation(2)

    assert state["representacoes_view"] == 2
    assert state["rep_detail_section_2"] == "Visão geral"
    assert "representacoes_progress" not in state
    assert "representacoes_file" not in state
    assert "representacoes_edit" not in state
    assert "representacoes_confirm_delete" not in state
    assert "representacoes_exclude_progress" not in state
    assert "representacoes_download" not in state

    ui._set_ui_state("representacoes_progress", 2, "rep_prg_form_2")
    assert state["representacoes_progress"] == 2
    assert "rep_prg_form_2desc" not in state

    state["rep_doc_form_2desc"] = "documento antigo"
    ui._queue_widget_cleanup("rep_doc_form_2")
    assert state["rep_doc_form_2desc"] == "documento antigo"
    ui._consume_widget_cleanup()
    assert "rep_doc_form_2desc" not in state

    ui._return_to_listing()
    assert "representacoes_view" not in state
    assert "representacoes_progress" not in state
    assert state["rep_f_q"] == "licitação"
    assert state["rep_f_sit"] == "EM_TRAMITACAO"


def test_representation_internal_actions_use_fragment_reruns_and_callbacks():
    import inspect

    from services.notification_ui import render_protocol_notice
    from services.record_engagement_ui import render_origin_tools
    from services.representacoes_ui import (
        _detail_compact,
        _done,
        _document_form,
        _progress_form,
        _protocol_form,
        render,
    )

    assert 'st.rerun(scope="fragment")' in inspect.getsource(_done)
    detail = inspect.getsource(_detail_compact)
    assert "st.rerun()" not in detail
    assert 'st.rerun(scope="fragment")' in detail
    assert "on_click=_return_to_listing" in detail
    assert detail.count('rerun_scope="fragment"') == 2
    for form in (_progress_form, _document_form, _protocol_form):
        source = inspect.getsource(form)
        assert "on_click=_clear_ui_state" in source
        assert "st.rerun()" not in source
    home = inspect.getsource(render)
    assert "on_click=_set_ui_state" in home
    assert "st.rerun()" not in home
    notice = inspect.getsource(render_protocol_notice)
    engagement = inspect.getsource(render_origin_tools)
    assert 'rerun_scope="app"' in notice
    assert "st.rerun(scope=rerun_scope)" in notice
    assert 'rerun_scope="app"' in engagement
    assert "st.rerun(scope=rerun_scope)" in engagement


def test_representation_management_and_cancelled_form_state_do_not_leak(store):
    import services.representacoes_ui as ui

    principal = _principal(store, email="estado-rep@test.local")
    first = _protocolled_with_pdf(store, principal, number="TC 077701/26")
    second = _protocolled_with_pdf(store, principal, number="TC 077702/26")
    set_status(store, first, "JULGADA", principal)
    _UI_HOLD.clear()
    _UI_HOLD.update(ui=ui, store=store, principal=principal)

    def page():
        from tests.test_representacoes import _UI_HOLD

        _UI_HOLD["ui"].render(_UI_HOLD["store"], _UI_HOLD["principal"])

    app = AppTest.from_function(page, default_timeout=30).run()
    app.button(key=f"rep_open_{first}").click().run()
    app.radio(key=f"rep_detail_section_{first}").set_value("Gestão").run()
    assert app.selectbox(key=f"rep_dt_sit_{first}").value == "JULGADA"
    app.button(key="rep_dt_back").click().run()

    app.button(key=f"rep_open_{second}").click().run()
    app.radio(key=f"rep_detail_section_{second}").set_value("Gestão").run()
    assert app.selectbox(key=f"rep_dt_sit_{second}").value == "EM_TRAMITACAO"
    assert f"rep_dt_sit_{first}" not in {item.key for item in app.selectbox}
    app.button(key="rep_dt_back").click().run()

    app.button(key=f"rep_prg_{second}").click().run()
    description_key = f"rep_prg_form_{second}desc"
    app.text_area(key=description_key).set_value("Não deve reaparecer.").run()
    app.button(key=f"rep_prg_form_{second}cancel").click().run()
    assert "representacoes_progress" not in app.session_state
    assert description_key not in app.session_state
    app.button(key=f"rep_prg_{second}").click().run()
    assert app.text_area(key=description_key).value == ""
    assert not app.exception


def test_home_shows_temporal_notice_and_both_registration_routes(store):
    import services.representacoes_ui as ui

    principal = _principal(store, email="home-direct@test.local")
    _UI_HOLD.clear()
    _UI_HOLD.update(ui=ui, store=store, principal=principal)

    def page():
        from tests.test_representacoes import _UI_HOLD

        _UI_HOLD["ui"].render(_UI_HOLD["store"], _UI_HOLD["principal"])

    app = AppTest.from_function(page, default_timeout=30).run()
    assert any(
        "Este módulo acompanha as Representações protocoladas no TRAMITA a partir de janeiro de 2026."
        in item.value
        for item in app.caption
    )
    assert app.button(key="rep_new")
    assert app.button(key="rep_direct_new")
    app.button(key="rep_direct_new").click().run()
    assert app.session_state["representacoes_direct"] is True
    assert app.button(key="rep_direct_save")
    assert not app.exception
    app.button(key="rep_direct_cancel").click().run()
    assert "representacoes_direct" not in app.session_state
    app = AppTest.from_function(page, default_timeout=30).run()
    app.button(key="rep_new").click().run()
    assert app.button(key="rep_save")
    assert not app.exception


def test_exclude_progress_keeps_the_row_and_hides_it(store, monkeypatch):
    import json

    def _blocked(*_args, **_kwargs):
        raise AssertionError("e-mail")

    monkeypatch.setattr("services.email_transport.institutional_transport", _blocked)
    monkeypatch.setattr("services.notifications.confirm_send", _blocked)
    monkeypatch.setattr("services.gmail_transport._service_credentials", _blocked)
    principal = _principal(store, email="andamento@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    add_progress(
        store,
        record["id"],
        {
            "tipo": "MINUTA_PREPARADA",
            "data": "2026-09-10",
            "descricao": "Minuta válida.",
        },
        principal,
    )
    add_progress(
        store,
        record["id"],
        {
            "tipo": "LIVRE",
            "data": "2026-09-25",
            "descricao": "Lançamento que não deveria constar.",
        },
        principal,
    )
    by_text = {item["descricao"]: item["id"] for item in progress(store, record["id"])}
    kept = by_text["Minuta válida."]
    mistaken = by_text["Lançamento que não deveria constar."]
    with store.connection(read_only=True) as c:
        before = dict(
            c.execute(
                "SELECT data,tipo,descricao,criado_em,criado_por "
                "FROM representacao_andamentos WHERE id=?",
                (mistaken,),
            ).fetchone()
        )
        notices = c.execute("SELECT COUNT(*) FROM notificacoes_email").fetchone()[0]
        recipients = c.execute(
            "SELECT COUNT(*) FROM notificacao_destinatarios"
        ).fetchone()[0]
    exclude_progress(
        store,
        record["id"],
        mistaken,
        "Lançamento realizado por engano.",
        principal,
    )
    visible = progress(store, record["id"])
    assert mistaken not in {item["id"] for item in visible}
    assert kept in {item["id"] for item in visible}
    listed = next(item for item in list_records(store) if item["id"] == record["id"])
    assert listed["ultimo_andamento"]["descricao"] == "Minuta válida."
    with store.connection(read_only=True) as c:
        row = dict(
            c.execute(
                "SELECT * FROM representacao_andamentos WHERE id=?",
                (mistaken,),
            ).fetchone()
        )
        kept_row = dict(
            c.execute(
                "SELECT data,tipo,descricao,criado_em,criado_por,excluido "
                "FROM representacao_andamentos WHERE id=?",
                (kept,),
            ).fetchone()
        )
        event = c.execute(
            "SELECT usuario_email,entidade_tipo,entidade_id,acao,detalhes_json "
            "FROM auditoria_eventos WHERE evento=?",
            ("ANDAMENTO_EXCLUIDO",),
        ).fetchone()
        assert (
            c.execute("SELECT COUNT(*) FROM notificacoes_email").fetchone()[0]
            == notices
        )
        assert (
            c.execute("SELECT COUNT(*) FROM notificacao_destinatarios").fetchone()[0]
            == recipients
        )
    assert row["excluido"] == 1
    assert row["excluido_por"] == "andamento@test.local"
    assert row["excluido_em"]
    assert row["motivo_exclusao"] == "Lançamento realizado por engano."
    assert row["data"] == before["data"] == "2026-09-25"
    assert row["tipo"] == before["tipo"] == "LIVRE"
    assert row["descricao"] == before["descricao"]
    assert row["criado_em"] == before["criado_em"]
    assert row["criado_por"] == before["criado_por"]
    assert kept_row["excluido"] == 0
    assert kept_row["descricao"] == "Minuta válida."
    assert event["usuario_email"] == "andamento@test.local"
    assert event["entidade_tipo"] == "representacao"
    assert event["entidade_id"] == str(record["id"])
    assert event["acao"] == "EXCLUIR"
    details = json.loads(event["detalhes_json"])
    assert details["andamento_id"] == mistaken
    assert details["motivo"] == "Lançamento realizado por engano."


def test_exclude_progress_rejects_blank_reason_and_missing_access(store):
    principal = _principal(store, email="acesso@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    add_progress(
        store,
        record["id"],
        {"tipo": "LIVRE", "data": "2026-09-25", "descricao": "Registro a preservar."},
        principal,
    )
    progress_id = next(
        item["id"]
        for item in progress(store, record["id"])
        if item["descricao"] == "Registro a preservar."
    )
    for blank in ("", "   ", None):
        with pytest.raises(ValueError, match="Informe o motivo da exclusão"):
            exclude_progress(store, record["id"], progress_id, blank, principal)
    denied = _principal(store, email="sem-acesso@test.local", representacoes=False)
    with pytest.raises(ValueError, match="Acesso não autorizado"):
        exclude_progress(
            store,
            record["id"],
            progress_id,
            "Lançamento realizado por engano.",
            denied,
        )
    assert progress_id in {item["id"] for item in progress(store, record["id"])}
    with store.connection(read_only=True) as c:
        row = c.execute(
            "SELECT excluido,descricao FROM representacao_andamentos WHERE id=?",
            (progress_id,),
        ).fetchone()
        assert row["excluido"] == 0
        assert row["descricao"] == "Registro a preservar."
        assert (
            c.execute(
                "SELECT COUNT(*) FROM auditoria_eventos WHERE evento=?",
                ("ANDAMENTO_EXCLUIDO",),
            ).fetchone()[0]
            == 0
        )


def test_existing_progress_table_gains_exclusion_columns(tmp_path):
    import sqlite3

    path = tmp_path / "legacy.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE representacao_andamentos ("
        "id INTEGER PRIMARY KEY,"
        "representacao_id INTEGER NOT NULL,"
        "data TEXT NOT NULL,"
        "tipo TEXT NOT NULL,"
        "descricao TEXT NOT NULL DEFAULT '',"
        "criado_em TEXT NOT NULL,"
        "criado_por TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO representacao_andamentos("
        "id,representacao_id,data,tipo,descricao,criado_em,criado_por) "
        "VALUES(7,3,'2026-09-25','LIVRE','Texto original',"
        "'2026-09-25T12:00:00','autor@test.local')"
    )
    connection.commit()
    connection.close()
    legacy = Store(path)
    with legacy.connection(read_only=True) as c:
        names = {
            row[1] for row in c.execute("PRAGMA table_info(representacao_andamentos)")
        }
        row = c.execute("SELECT * FROM representacao_andamentos WHERE id=7").fetchone()
    assert {"excluido", "excluido_em", "excluido_por", "motivo_exclusao"} <= names
    assert row["descricao"] == "Texto original"
    assert row["data"] == "2026-09-25"
    assert row["tipo"] == "LIVRE"
    assert row["criado_em"] == "2026-09-25T12:00:00"
    assert row["criado_por"] == "autor@test.local"
    assert row["excluido"] == 0
    assert not row["excluido_em"]
    assert row["excluido_por"] == ""
    assert row["motivo_exclusao"] == ""


def _protocolled_with_pdf(store, principal, content=None, number="TC 077777/26"):
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    register_protocol(
        store,
        record["id"],
        {
            "numero_processo": number,
            "data_protocolo": "2026-09-18",
            "relator": RELATORES[0],
            "fase_processual": "INSTRUCAO",
        },
        principal,
        ("representacao.pdf", content or _pdf()),
    )
    return record["id"]


def test_official_pdf_summary_is_manual_and_keeps_the_previous_text(store, monkeypatch):
    import services.ai_service as ai_service
    from services.ai_service import GeminiErro
    from services.representacoes import (
        atualizar_resumo_representacao,
        hash_documento,
        pdf_oficial,
        resumo_desatualizado,
        resumo_ia,
    )
    from services.representacoes_ui import (
        AVISO_PDF_ALTERADO,
        AVISO_RESUMO_IA,
        _detail_compact,
        _render_official_document,
    )

    principal = _principal(store, email="resumo-rep@test.local")
    calls = []

    def fake_summary(pdf_bytes, **_ignored):
        calls.append(pdf_bytes)
        return "Objeto: resumo " + str(len(calls)) + "."

    monkeypatch.setattr(ai_service, "resumir_documento_pdf", fake_summary)
    payload, *_ = _payload(store)
    bare = create(store, payload, principal)
    assert pdf_oficial(store, bare["id"]) is None
    assert resumo_ia(store, bare["id"]) is None
    with pytest.raises(ValueError, match="Não há PDF oficial"):
        atualizar_resumo_representacao(store, bare["id"], principal)
    assert calls == []

    first_pdf = _pdf()
    identifier = _protocolled_with_pdf(store, principal, first_pdf, "TC 088888/26")
    official = pdf_oficial(store, identifier)
    assert official["tipo_documento"] == "REPRESENTACAO_FINAL"
    assert official["nome_arquivo"] == "representacao.pdf"
    assert "arquivo" not in official
    assert download(store, official["id"])["conteudo"] == first_pdf
    from services.representacoes_ui import _official_download_payload

    delivered = _official_download_payload(
        store, principal, get(store, identifier), official
    )
    assert delivered["conteudo"] == first_pdf
    assert delivered["nome"] == official["nome_arquivo"]
    with store.connection(read_only=True) as c:
        audited = c.execute(
            "SELECT COUNT(*) FROM auditoria_eventos WHERE evento=? AND entidade_id=?",
            ("DOCUMENTO_BAIXADO", str(identifier)),
        ).fetchone()[0]
    assert audited == 1
    assert resumo_ia(store, identifier) is None
    assert "resumo_ia" not in get(store, identifier)
    assert calls == []

    saved = atualizar_resumo_representacao(store, identifier, principal)
    assert saved["texto"] == "Objeto: resumo 1."
    assert saved["sha256"] == hash_documento(store, official["id"])
    assert saved["documento_id"] == official["id"]
    assert saved["modelo"]
    assert saved["gerado_em"]
    assert len(calls) == 1
    assert calls[0] == first_pdf
    again = resumo_ia(store, identifier)
    assert again["texto"] == saved["texto"]
    assert len(calls) == 1
    assert not resumo_desatualizado(again, again["sha256"])

    updated = atualizar_resumo_representacao(store, identifier, principal)
    assert updated["texto"] == "Objeto: resumo 2."
    assert resumo_ia(store, identifier)["texto"] == "Objeto: resumo 2."
    assert len(calls) == 2

    def fail_summary(_pdf_bytes, **_ignored):
        calls.append(b"falha")
        raise GeminiErro("O serviço de IA está indisponível no momento.")

    monkeypatch.setattr(ai_service, "resumir_documento_pdf", fail_summary)
    with pytest.raises(GeminiErro, match="indisponível"):
        atualizar_resumo_representacao(store, identifier, principal)
    assert resumo_ia(store, identifier)["texto"] == "Objeto: resumo 2."

    empty_id = _protocolled_with_pdf(store, principal, _pdf(), "TC 099999/26")
    with pytest.raises(GeminiErro, match="indisponível"):
        atualizar_resumo_representacao(store, empty_id, principal)
    assert resumo_ia(store, empty_id) is None

    replacement_writer = PdfWriter()
    replacement_writer.add_blank_page(width=144, height=144)
    replacement_buffer = BytesIO()
    replacement_writer.write(replacement_buffer)
    replacement = replacement_buffer.getvalue()
    assert replacement != first_pdf
    add_document(
        store,
        identifier,
        {
            "tipo_documento": "REPRESENTACAO_FINAL",
            "descricao": "PDF substituído",
            "data_documento": "2026-09-20",
        },
        "representacao-nova.pdf",
        replacement,
        principal,
    )
    current = pdf_oficial(store, identifier)
    assert current["id"] != official["id"]
    assert download(store, current["id"])["conteudo"] == replacement
    assert download(store, official["id"])["conteudo"] == first_pdf
    stored = resumo_ia(store, identifier)
    assert stored["texto"] == "Objeto: resumo 2."
    assert resumo_desatualizado(stored, hash_documento(store, current["id"]))
    assert not resumo_desatualizado(stored, stored["sha256"])

    denied = _principal(
        store,
        email="sem-rep@test.local",
        representacoes=False,
        registrar_protocolo=False,
    )
    with pytest.raises(ValueError, match="não autorizado"):
        atualizar_resumo_representacao(store, identifier, denied)
    assert resumo_ia(store, identifier)["texto"] == "Objeto: resumo 2."

    screen = inspect.getsource(_render_official_document)
    compact = inspect.getsource(_detail_compact)
    assert screen.index("update_requested = summary_column.button") < screen.index(
        "atualizar_resumo_representacao("
    )
    assert "resumir_documento_pdf" not in screen
    assert "Baixar representação" in screen
    assert 'on_click="ignore"' in screen
    assert "_official_download_payload(" in screen
    assert "Preparar download" not in screen
    assert "rep_visao_prep_" not in screen
    assert "✨ Gerar resumo com IA" in screen
    assert "↻ Atualizar resumo" in screen
    assert "### Resumo da representação — gerado por IA" in screen
    assert "formatar_resumo_markdown(saved" in screen
    assert "AVISO_RESUMO_IA" in screen
    assert "AVISO_PDF_ALTERADO" in screen
    assert "documento protocolado" in AVISO_RESUMO_IA
    assert "documento oficial foi alterado" in AVISO_PDF_ALTERADO
    assert "_render_official_document(" in compact
    assert "Preparar download" in compact
    assert 'download_button("Baixar"' in compact or "download_button(\n" in compact
    assert "rep_dl_" in compact
    assert "rep_prep_" in compact


def test_representation_summary_normalizes_markdown_without_changing_values():
    from services.representacoes import formatar_resumo_markdown

    text = (
        "# MINISTÉRIO PÚBLICO DE CONTAS\n\n"
        "## Síntese\n\n"
        "**Valor:** `R$ 1.296.265,90` (12,5%).\n"
        "- **Cautelar:** R$ 516.344,64.\n"
        "- **Processo:** TC 12345/2026 — Maria da Silva."
    )

    formatted = formatar_resumo_markdown(text)

    assert formatted == (
        "**MINISTÉRIO PÚBLICO DE CONTAS**\n\n"
        "**Síntese**\n\n"
        "**Valor:** R\\$ 1.296.265,90 (12,5%).\n"
        "- **Cautelar:** R\\$ 516.344,64.\n"
        "- **Processo:** TC 12345/2026 — Maria da Silva."
    )
    assert "`" not in formatted
    assert "#" not in formatted


def test_summary_update_stores_normalized_markdown(store, monkeypatch):
    import services.ai_service as ai_service
    from services.representacoes import atualizar_resumo_representacao, resumo_ia

    principal = _principal(store, email="resumo-formatado@test.local")
    identifier = _protocolled_with_pdf(store, principal, number="TC 12345/2026")
    raw = "## Síntese\n\n- **Valor:** R$ 516.344,64."
    monkeypatch.setattr(
        ai_service, "resumir_documento_pdf", lambda *_args, **_kwargs: raw
    )

    saved = atualizar_resumo_representacao(store, identifier, principal)

    assert saved["texto"] == "**Síntese**\n\n- **Valor:** R\\$ 516.344,64."
    assert resumo_ia(store, identifier)["texto"] == saved["texto"]


def test_representation_summary_can_be_hidden_without_regeneration(store, monkeypatch):
    from database.representacoes import RepresentacoesStore
    from services.representacoes import pdf_oficial, resumo_ia
    import services.representacoes_ui as ui

    principal = _principal(store, email="resumo-oculto@test.local")
    identifier = _protocolled_with_pdf(store, principal, number="TC 066666/26")
    official = pdf_oficial(store, identifier)
    RepresentacoesStore(store).save_resumo_ia(
        identifier,
        "Resumo preservado.",
        "2026-09-30T12:00:00",
        "mock",
        "abc",
        official["id"],
    )
    calls = []

    def update(*_args):
        calls.append(True)
        return resumo_ia(store, identifier)

    monkeypatch.setattr(ui, "atualizar_resumo_representacao", update)
    _UI_HOLD.clear()
    _UI_HOLD.update(
        ui=ui, store=store, principal=principal, record=get(store, identifier)
    )

    def page():
        from tests.test_representacoes import _UI_HOLD

        _UI_HOLD["ui"]._render_official_document(
            _UI_HOLD["store"], _UI_HOLD["principal"], _UI_HOLD["record"]
        )

    app = AppTest.from_function(page, default_timeout=30).run()
    assert any("Resumo preservado." in item.value for item in app.markdown)
    app.button(key="rep_visao_resumo_hide_" + str(identifier)).click().run()
    assert calls == []
    assert not any("Resumo preservado." in item.value for item in app.markdown)
    app.run()
    assert calls == []
    app.button(key="rep_visao_resumo_show_" + str(identifier)).click().run()
    assert calls == []
    assert any("Resumo preservado." in item.value for item in app.markdown)
    app.button(key="rep_visao_resumo_" + str(identifier)).click().run()
    assert calls == [True]
    assert any("Resumo preservado." in item.value for item in app.markdown)
    assert "representacoes_resumo_ia_oculto_" + str(identifier) not in app.session_state


def test_summary_persists_the_model_that_answered(store, monkeypatch):
    import email
    import io
    import json
    import urllib.error

    import streamlit as st

    import services.ai_service as ai_service
    from database.store import now
    from services.ai_service import (
        GEMINI_FALLBACK_MODEL,
        GEMINI_MODEL,
        GeminiErro,
    )
    from services.representacoes import (
        atualizar_resumo_representacao,
        open_store,
        pdf_oficial,
        resumo_ia,
    )

    monkeypatch.setattr(st, "secrets", {"GEMINI_API_KEY": "chave-de-teste"})
    monkeypatch.setattr(ai_service, "_sleep", lambda seconds: None)
    principal = _principal(store, email="modelo-efetivo@test.local")
    primary_id = _protocolled_with_pdf(store, principal, _pdf(), "TC 055551/26")
    legacy_id = _protocolled_with_pdf(store, principal, _pdf(), "TC 055552/26")
    legacy_document = pdf_oficial(store, legacy_id)

    def answer(text):
        return json.dumps(
            {"candidates": [{"content": {"parts": [{"text": text}]}}]}
        ).encode()

    def primary_only(request):
        assert GEMINI_MODEL in request.full_url
        assert GEMINI_FALLBACK_MODEL not in request.full_url
        return answer("Objeto: principal.")

    monkeypatch.setattr(ai_service, "_post", primary_only)
    saved = atualizar_resumo_representacao(store, primary_id, principal)
    assert saved["texto"] == "Objeto: principal."
    assert saved["modelo"] == "gemini-3.5-flash-lite"

    open_store(store).save_resumo_ia(
        legacy_id,
        "Resumo anterior.",
        now(),
        "gemini-3.6-flash",
        "legado",
        legacy_document["id"],
    )

    def expire(_request):
        raise TimeoutError()

    monkeypatch.setattr(ai_service, "_post", expire)
    with pytest.raises(GeminiErro, match="não respondeu a tempo"):
        atualizar_resumo_representacao(store, legacy_id, principal)
    kept = resumo_ia(store, legacy_id)
    assert kept["texto"] == "Resumo anterior."
    assert kept["modelo"] == "gemini-3.6-flash"

    def reserve(request):
        if GEMINI_FALLBACK_MODEL in request.full_url:
            return answer("Objeto: reserva.")
        body = json.dumps(
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "quota",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {
                                    "quotaMetric": (
                                        "generativelanguage.googleapis.com/"
                                        "generate_content_free_tier_requests"
                                    ),
                                    "quotaId": (
                                        "GenerateRequestsPerDayPerProjectPerModel"
                                        "-FreeTier"
                                    ),
                                    "quotaValue": "500",
                                }
                            ],
                        }
                    ],
                }
            }
        ).encode()
        raise urllib.error.HTTPError(
            request.full_url,
            429,
            "erro",
            email.message_from_string(""),
            io.BytesIO(body),
        )

    monkeypatch.setattr(ai_service, "_post", reserve)
    updated = atualizar_resumo_representacao(store, primary_id, principal)
    assert updated["texto"] == "Objeto: reserva."
    assert updated["modelo"] == "gemini-3.1-flash-lite"
    assert resumo_ia(store, legacy_id)["modelo"] == "gemini-3.6-flash"
    assert resumo_ia(store, legacy_id)["texto"] == "Resumo anterior."


def test_new_database_creates_summary_columns(tmp_path):
    fresh = Store(tmp_path / "novo.db")
    with fresh.connection(read_only=True) as c:
        names = {row[1] for row in c.execute("PRAGMA table_info(representacoes)")}
    assert {
        "resumo_ia",
        "resumo_ia_em",
        "resumo_ia_modelo",
        "resumo_ia_sha256",
        "resumo_ia_documento_id",
    } <= names
    source = inspect.getsource(RepresentacoesStore.ensure_schema)
    assert source.index("resumo_ia TEXT") < source.index("_ensure_resumo_ia")


def test_official_pdf_hash_cache_follows_the_document_id(monkeypatch):
    import services.representacoes_ui as ui

    calls = []

    def fake_hash(_store, file_id):
        calls.append(file_id)
        return "sha-" + file_id

    monkeypatch.setattr(ui, "hash_documento", fake_hash)
    monkeypatch.setattr(ui.st, "session_state", {})
    current = {"id": "doc-atual", "tamanho": 12, "criado_em": "2026-09-26T00:00:00"}
    assert ui._official_sha256(None, current) == "sha-doc-atual"
    assert ui._official_sha256(None, current) == "sha-doc-atual"
    assert calls == ["doc-atual"]
    replacement = {"id": "doc-novo", "tamanho": 12, "criado_em": "2026-09-26T00:00:00"}
    assert ui._official_sha256(None, replacement) == "sha-doc-novo"
    assert calls == ["doc-atual", "doc-novo"]


def test_existing_representation_gains_summary_columns(tmp_path):
    import sqlite3

    path = tmp_path / "legacy-resumo.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE representacoes ("
        "id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, objeto TEXT NOT NULL DEFAULT '',"
        "origem TEXT NOT NULL, data_abertura TEXT NOT NULL, representado TEXT NOT NULL DEFAULT '',"
        "tema TEXT NOT NULL DEFAULT '', prioridade TEXT NOT NULL DEFAULT 'NORMAL',"
        "situacao TEXT NOT NULL, fase_processual TEXT, numero_processo TEXT,"
        "data_protocolo TEXT, relator TEXT, observacoes TEXT NOT NULL DEFAULT '',"
        "criado_em TEXT NOT NULL, criado_por TEXT NOT NULL, atualizado_em TEXT NOT NULL,"
        "atualizado_por TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO representacoes(id,titulo,origem,data_abertura,situacao,criado_em,"
        "criado_por,atualizado_em,atualizado_por) VALUES(4,'Título legado','DE_OFICIO',"
        "'2026-09-01','IDEIA','2026-09-01T00:00:00','autor@test.local',"
        "'2026-09-01T00:00:00','autor@test.local')"
    )
    connection.commit()
    connection.close()
    legacy = Store(path)
    with legacy.connection(read_only=True) as c:
        names = {row[1] for row in c.execute("PRAGMA table_info(representacoes)")}
        row = c.execute("SELECT * FROM representacoes WHERE id=4").fetchone()
    assert {
        "resumo_ia",
        "resumo_ia_em",
        "resumo_ia_modelo",
        "resumo_ia_sha256",
        "resumo_ia_documento_id",
        "registro_origem",
    } <= names
    assert row["titulo"] == "Título legado"
    assert row["resumo_ia"] is None
    assert row["registro_origem"] == "PROJETO"


def test_andamento_exclusion_is_offered_in_the_timeline():
    from services.representacoes_ui import _detail_compact, _render_progress_item

    screen = inspect.getsource(_detail_compact)
    action = inspect.getsource(_render_progress_item)
    assert "_render_progress_item(" in screen
    assert "Excluir andamento" in action
    assert "Confirma a exclusão deste andamento?" in action
    assert "Motivo da exclusão" in action
    assert "exclude_progress(" in action
    assert ".strip()" in action
    assert "DELETE" not in inspect.getsource(RepresentacoesStore.exclude_progress)
    assert "gmail" not in action.casefold()
    assert "notificac" not in action.casefold()
