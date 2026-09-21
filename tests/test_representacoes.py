from io import BytesIO

import pytest
from pypdf import PdfWriter

from database.access import AccessStore
from database.representacoes import DOCUMENT_META, RepresentacoesStore
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
    create,
    delete,
    documents,
    download,
    get,
    kind_label,
    list_records,
    progress,
    reconcile_signatories,
    register_protocol,
    relator_label,
    set_phase,
    signatory_options,
    setor_elegivel,
    update,
)
from tests.access_testing import seed_access


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
    others = [p["id"] for p in store.catalog("procuradores") if p.get("ativo") and p["id"] != member][:2]
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
    assert {item[1] for item in roles if item[0] == "PROCURADOR_SIGNATARIO"} == set(signatories)
    assert {item[1] for item in roles if item[0] == "ASSESSOR"} == {assessor, extra_assessor}
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
    assert len([item for item in updated["integrantes"] if item["papel"] == "PROCURADOR_SIGNATARIO"]) == 1
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
    protocol_events = [item for item in progress(store, record["id"]) if item["tipo"] == "PROTOCOLADA"]
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
        {"tipo_documento": "MINUTA", "descricao": "Minuta inicial", "data_documento": "2026-09-10"},
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
        assert c.execute(
            "SELECT COUNT(*) FROM representacao_integrantes WHERE representacao_id=?",
            (identifier,),
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM representacao_documentos WHERE representacao_id=?",
            (identifier,),
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM representacao_andamentos WHERE representacao_id=?",
            (identifier,),
        ).fetchone()[0] == 0


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
    from services.representacoes_ui import _protocol_form

    source = inspect.getsource(_protocol_form)
    assert 'st.text_input("Relator' not in source
    assert "selectbox" in source
    assert "RELATORES" in source
    assert "Possui pedido de medida cautelar?" in source
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
    stale = {"tipo": "CRIADA", "descricao": "Representação criada.", "data": "2026-09-01"}
    heading, extra = andamento_display(stale)
    assert heading == "Projeto de Representação criado"
    assert extra == ""
    assert "Representação criada" not in extra
