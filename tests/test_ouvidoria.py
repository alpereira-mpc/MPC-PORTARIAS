from io import BytesIO
import inspect
import pytest
from pypdf import PdfWriter

from database.access import AccessStore
from database.ouvidoria import DOCUMENT_META, OuvidoriaStore
from services.access import (
    can_access_ouvidoria,
    can_access_representacoes,
    has_permission,
    principal_from_record,
    require_permission,
    resolve_principal,
)
from services.ouvidoria import (
    OUVIDOR_NOME,
    actions,
    add_action,
    add_document,
    add_progress,
    andamento_display,
    close,
    create,
    create_projeto_representacao,
    default_ouvidor_id,
    delete,
    delete_blocked_reason,
    documents,
    download,
    get,
    list_records,
    progress,
    update,
    update_action,
)
from tests.access_testing import seed_access


def _pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _principal(store, email="ouvi@test.local", **flags):
    seed_access(
        store,
        email=email,
        nome="Usuário Ouvidoria",
        perfil="USUARIO",
        pode_portarias=flags.get("portarias", True),
        pode_agenda=flags.get("agenda", True),
        pode_oficios=flags.get("oficios", True),
        pode_admin=False,
        pode_memorandos=flags.get("memorandos", True),
        pode_relatorios=flags.get("relatorios", True),
        pode_representacoes=flags.get("representacoes", False),
        pode_ouvidoria=flags.get("ouvidoria", True),
    )
    return resolve_principal(store, {"email": email})


def _server(store, name="Assessor Ouvidoria", setor="PROGE"):
    from database.store import now

    stamp = now()
    with store.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        inserted = c.execute(
            "INSERT INTO servidores(nome,nome_normalizado,matricula_original,cargo,setor,"
            "ativo,criado_em,atualizado_em) VALUES(?,?,?,?,?,?,?,?)",
            (name, name.casefold(), "888", "Assessor", setor, 1, stamp, stamp),
        )
        return inserted.lastrowid


def _payload(store, **changes):
    people = [p for p in store.catalog("procuradores") if p.get("ativo")]
    ouvidor = default_ouvidor_id(store) or people[0]["id"]
    others = [p["id"] for p in people if p["id"] != ouvidor][:2]
    responsible = others[0] if others else ouvidor
    participants = [i for i in others if i != responsible][:1]
    assessor = _server(store)
    data = {
        "titulo": "Denúncia sobre contratação temporária",
        "resumo": "Relato de irregularidade em contratação",
        "tipo": "DENUNCIA",
        "forma_recebimento": "EMAIL",
        "data_recebimento": "2026-09-21",
        "representado": "Município X",
        "tema": "Pessoal",
        "classificacao_acesso": "RESTRITA",
        "prioridade": "NORMAL",
        "situacao": "RECEBIDA",
        "manifestante_identificado": False,
        "observacoes": "Uso interno",
        "ouvidor_id": ouvidor,
        "procurador_responsavel_id": responsible,
        "procuradores_participantes": participants,
        "assessores": [assessor],
    }
    data.update(changes)
    return data, ouvidor, responsible, participants, assessor


def test_unauthorized_user_cannot_see_or_access_module(store):
    from portal import visible_modules

    denied = _principal(store, email="sem-ouvi@test.local", ouvidoria=False, representacoes=True)
    assert not can_access_ouvidoria(denied)
    assert can_access_representacoes(denied)
    assert not has_permission(denied, "ouvidoria")
    with pytest.raises(ValueError, match="não autorizado"):
        require_permission(denied, "ouvidoria")
    assert "ouvidoria" not in {module.key for module in visible_modules(denied)}
    assert "representacoes" in {module.key for module in visible_modules(denied)}


def test_authorized_user_sees_module(store):
    from portal import visible_modules

    allowed = _principal(store, email="com-ouvi@test.local", ouvidoria=True)
    assert can_access_ouvidoria(allowed)
    assert "ouvidoria" in {module.key for module in visible_modules(allowed)}


def test_direct_ui_load_is_blocked(store):
    from services.ouvidoria_ui import render

    denied = _principal(store, email="ui-ouvi@test.local", ouvidoria=False)
    with pytest.raises(ValueError, match="não autorizado"):
        render(store, denied)


def test_permission_is_independent_from_representacoes(store):
    only_ouvi = _principal(
        store,
        email="somente-ouvi@test.local",
        portarias=False,
        agenda=False,
        oficios=False,
        memorandos=False,
        relatorios=False,
        representacoes=False,
        ouvidoria=True,
    )
    assert has_permission(only_ouvi, "ouvidoria")
    assert not has_permission(only_ouvi, "representacoes")
    only_rep = _principal(
        store,
        email="somente-rep-ouvi@test.local",
        representacoes=True,
        ouvidoria=False,
    )
    assert has_permission(only_rep, "representacoes")
    assert not has_permission(only_rep, "ouvidoria")


def test_admin_receives_ouvidoria_flag(store):
    admin = resolve_principal(store, {"email": "admin@test.local"})
    assert admin.pode_ouvidoria
    assert can_access_ouvidoria(admin)


def test_create_number_team_and_edit(store):
    principal = _principal(store)
    payload, ouvidor, responsible, participants, assessor = _payload(store)
    extra = _server(store, "Segundo Assessor Ouvidoria")
    payload["assessores"] = [assessor, extra]
    record = create(store, payload, principal)
    assert record["numero_interno"] == "OUVI-2026-001"
    assert record["classificacao_acesso"] == "RESTRITA"
    assert record["situacao"] == "RECEBIDA"
    assert record["resultado"] is None
    roles = {(item["papel"], item["membro_id"]) for item in record["integrantes"]}
    assert ("OUVIDOR", ouvidor) in roles
    assert record["procurador_responsavel_id"] == responsible
    assert {item[1] for item in roles if item[0] == "PROCURADOR_PARTICIPANTE"} == set(participants)
    assert {item[1] for item in roles if item[0] == "ASSESSOR"} == {assessor, extra}
    events = progress(store, record["id"])
    types = [item["tipo"] for item in events]
    assert types.count("CRIADA") == 1
    assert "ENCAMINHADA_OUVIDOR" not in types
    heading, extra_text = andamento_display(next(i for i in events if i["tipo"] == "CRIADA"))
    assert heading == "Notícia de fato recebida pela Ouvidoria"
    assert extra_text == ""
    second = create(store, payload, principal)
    assert second["numero_interno"] == "OUVI-2026-002"
    payload["titulo"] = "Denúncia atualizada"
    payload["situacao"] = "ANALISE"
    payload["conclusao_analise"] = "Há elementos para providência."
    updated = update(store, record["id"], payload, principal)
    assert updated["titulo"] == "Denúncia atualizada"
    assert updated["situacao"] == "ANALISE"
    assert updated["conclusao_analise"] == "Há elementos para providência."
    assert default_ouvidor_id(store)
    names = {p["id"]: p["nome"] for p in store.catalog("procuradores") if p.get("ativo")}
    assert names[default_ouvidor_id(store)] == OUVIDOR_NOME


def test_transfer_responsible_keeps_history(store):
    principal = _principal(store, email="transf-ouvi@test.local")
    payload, ouvidor, responsible, participants, assessor = _payload(store)
    record = create(store, payload, principal)
    people = [p["id"] for p in store.catalog("procuradores") if p.get("ativo")]
    other = next(i for i in people if i not in {ouvidor, responsible})
    payload["procurador_responsavel_id"] = other
    updated = update(store, record["id"], payload, principal)
    assert updated["procurador_responsavel_id"] == other
    notes = [item for item in progress(store, record["id"]) if item["tipo"] == "RESPONSAVEL_ALTERADO"]
    assert notes
    assert "Responsabilidade transferida" in notes[0]["descricao"]
    assert get(store, record["id"])["procurador_responsavel_id"] == other


def test_actions_progress_documents_and_delete_rules(store):
    principal = _principal(store, email="docs-ouvi@test.local")
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    action_id = add_action(
        store,
        record["id"],
        {"tipo": "SOLICITACAO_INFORMACAO", "descricao": "Pedido de cópias"},
        principal,
    )
    assert action_id
    rows = actions(store, record["id"])
    assert rows[0]["tipo"] == "SOLICITACAO_INFORMACAO"
    update_action(
        store,
        record["id"],
        action_id,
        {"tipo": "SOLICITACAO_INFORMACAO", "descricao": "Pedido retificado"},
        principal,
    )
    assert actions(store, record["id"])[0]["descricao"] == "Pedido retificado"
    add_progress(
        store,
        record["id"],
        {"tipo": "LIVRE", "descricao": "Análise documental"},
        principal,
    )
    file_id = add_document(
        store,
        record["id"],
        {"tipo_documento": "DENUNCIA", "providencia_id": action_id},
        "denuncia.pdf",
        _pdf(),
        principal,
    )
    files = documents(store, record["id"])
    assert files[0]["id"] == file_id
    assert "arquivo" not in files[0]
    with pytest.raises(ValueError, match="PDF|DOCX|Aceitos"):
        add_document(
            store,
            record["id"],
            {"tipo_documento": "DENUNCIA"},
            "nota.txt",
            b"nao permitido",
            principal,
        )
    loaded = download(store, file_id, principal)
    assert loaded["conteudo"].startswith(b"%PDF")
    denied = _principal(store, email="sem-dl-ouvi@test.local", ouvidoria=False)
    with pytest.raises(ValueError, match="não autorizado"):
        download(store, file_id, denied)
    with pytest.raises(ValueError, match="documentos anexados|providências registradas"):
        delete(store, record["id"])
    closed = close(store, record["id"], "ARQUIVADA", principal)
    assert closed["situacao"] == "ARQUIVADA"
    assert closed["resultado"] == "ARQUIVAMENTO"
    types = [item["tipo"] for item in progress(store, record["id"])]
    assert "ARQUIVADA" in types
    other = create(store, payload, principal)
    ended = close(store, other["id"], "ENCERRADA", principal, resultado="ENCAMINHAMENTO_INTERNO")
    assert ended["situacao"] == "ENCERRADA"
    assert ended["resultado"] == "ENCAMINHAMENTO_INTERNO"
    fresh = create(store, payload, principal)
    assert delete(store, fresh["id"])


def test_list_has_no_blob_and_no_n_plus_one(store):
    principal = _principal(store, email="list-ouvi@test.local")
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
    rows = list_records(store, {"situacao": "RECEBIDA", "pesquisa": "OUVI-2026"})
    assert rows
    assert "arquivo" not in rows[0]
    assert rows[0]["ultimo_andamento"]
    sql = inspect.getsource(OuvidoriaStore.list)
    assert "arquivo" not in sql
    assert "BLOB" not in DOCUMENT_META
    assert "IN (" in sql
    assert sql.count("execute(") <= 3


def test_create_projeto_de_representacao_without_copying_documents(store):
    principal = _principal(
        store, email="link-ouvi@test.local", ouvidoria=True, representacoes=True
    )
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    add_document(
        store,
        record["id"],
        {"tipo_documento": "DENUNCIA"},
        "origem.pdf",
        _pdf(),
        principal,
    )
    linked = create_projeto_representacao(
        store, record["id"], principal, {"titulo": "Projeto revisado"}
    )
    assert linked["representacao_id"]
    assert linked["resultado"] == "PROJETO_REPRESENTACAO"
    from services.representacoes import get as get_rep

    representation = get_rep(store, linked["representacao_id"])
    assert representation["origem"] == "OUVIDORIA"
    assert representation["titulo"] == "Projeto revisado"
    assert representation["objeto"] == payload["resumo"]
    assert representation["numero_processo"] is None
    with store.connection(read_only=True) as c:
        assert c.execute(
            "SELECT COUNT(*) FROM representacao_documentos WHERE representacao_id=?",
            (representation["id"],),
        ).fetchone()[0] == 0
    types = [item["tipo"] for item in progress(store, record["id"])]
    assert "PROJETO_REPRESENTACAO" in types
    with pytest.raises(ValueError, match="Representação vinculada"):
        delete(store, record["id"])
    only_ouvi = _principal(
        store, email="ouvi-sem-rep@test.local", ouvidoria=True, representacoes=False
    )
    other = create(store, payload, only_ouvi)
    with pytest.raises(ValueError, match="não autorizado"):
        create_projeto_representacao(store, other["id"], only_ouvi)


def test_representation_link_does_not_bypass_ouvidoria_permission(store):
    from portal import visible_modules
    from services.ouvidoria import by_representation
    from services.ouvidoria_ui import render
    from services.representacoes_ui import _detail_body

    principal = _principal(
        store, email="both-ouvi@test.local", ouvidoria=True, representacoes=True
    )
    payload, *_ = _payload(store)
    record = create(store, payload, principal)
    linked = create_projeto_representacao(store, record["id"], principal)
    found = by_representation(store, linked["representacao_id"])
    assert found["numero_interno"] == record["numero_interno"]
    denied = _principal(
        store, email="rep-sem-ouvi@test.local", representacoes=True, ouvidoria=False
    )
    assert "ouvidoria" not in {module.key for module in visible_modules(denied)}
    with pytest.raises(ValueError, match="não autorizado"):
        render(store, denied)
    source = inspect.getsource(_detail_body)
    assert 'has_permission(principal, "ouvidoria")' in source
    assert "by_representation" in source
    assert "Notícia de fato" in source


def test_anonymous_sender_is_not_required(store):
    principal = _principal(store, email="anon-ouvi@test.local")
    payload, *_ = _payload(
        store,
        manifestante_identificado=True,
        manifestante_nome="Nome",
        manifestante_email="pessoa@example.com",
    )
    identified = create(store, payload, principal)
    assert identified["manifestante_nome"] == "Nome"
    payload["manifestante_identificado"] = False
    payload["manifestante_email"] = "nao-deve-ficar@example.com"
    anonymous = create(store, payload, principal)
    assert anonymous["manifestante_identificado"] is False
    assert anonymous["manifestante_email"] == ""
    assert anonymous["manifestante_nome"] == ""
    listed = list_records(store)
    from services.ouvidoria_ui import _card

    assert "manifestante_email" not in inspect.getsource(_card)
    assert listed


def test_ui_uses_noticia_de_fato_and_avoids_tipo_redundancy():
    from services.ouvidoria import tipo_visivel
    from services.ouvidoria_ui import _card, _detail, render

    ui = inspect.getsource(render) + inspect.getsource(_card) + inspect.getsource(_detail)
    assert "Nova notícia de fato" in ui
    assert "Notícias de fato da Ouvidoria" in ui
    assert "Notícia de fato recebida pela Ouvidoria." in ui
    assert "Nova manifestação" not in ui
    assert "Manifestação salva" not in ui
    assert tipo_visivel("NOTICIA_FATO") is None
    assert tipo_visivel("DENUNCIA") == "Denúncia"


def test_create_with_and_without_documents(store):
    principal = _principal(store, email="files-ouvi@test.local")
    payload, *_ = _payload(store)
    empty = create(store, payload, principal)
    assert documents(store, empty["id"]) == []
    before = len(list_records(store))
    with pytest.raises(ValueError, match="PDF|DOCX|Aceitos"):
        create(
            store,
            payload,
            principal,
            uploads=[("nota.txt", b"nao permitido")],
        )
    assert len(list_records(store)) == before
    one = create(
        store,
        payload,
        principal,
        uploads=[("denuncia.pdf", _pdf())],
    )
    files = documents(store, one["id"])
    assert len(files) == 1
    assert files[0]["tipo_documento"] == "DOCUMENTO_RECEBIDO"
    assert files[0]["nome_arquivo"]
    assert "arquivo" not in files[0]
    many = create(
        store,
        {**payload, "documento_tipo": "OFICIO"},
        principal,
        uploads=[("oficio.pdf", _pdf()), ("anexo.pdf", _pdf())],
    )
    attached = documents(store, many["id"])
    assert len(attached) == 2
    assert {item["tipo_documento"] for item in attached} == {"OFICIO"}
    loaded = download(store, attached[0]["id"], principal)
    assert loaded["conteudo"].startswith(b"%PDF")
    rows = list_records(store)
    assert all("arquivo" not in row for row in rows)


def test_delete_rules_and_toolbar_copy(store):
    principal = _principal(store, email="del-ouvi@test.local")
    payload, *_ = _payload(store)
    eligible = create(store, payload, principal)
    assert delete_blocked_reason(eligible, andamentos=progress(store, eligible["id"])) is None
    identifier = eligible["id"]
    assert delete(store, identifier)
    assert get(store, identifier) is None
    with store.connection(read_only=True) as c:
        assert c.execute(
            "SELECT COUNT(*) FROM ouvidoria_andamentos WHERE manifestacao_id=?",
            (identifier,),
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM ouvidoria_integrantes WHERE manifestacao_id=?",
            (identifier,),
        ).fetchone()[0] == 0
    with_docs = create(store, payload, principal, uploads=[("denuncia.pdf", _pdf())])
    assert "documentos anexados" in delete_blocked_reason(
        with_docs,
        documentos=documents(store, with_docs["id"]),
        andamentos=progress(store, with_docs["id"]),
    )
    with pytest.raises(ValueError, match="documentos anexados"):
        delete(store, with_docs["id"])
    with_action = create(store, payload, principal)
    add_action(store, with_action["id"], {"tipo": "SOLICITACAO_INFORMACAO"}, principal)
    assert "providências registradas" in delete_blocked_reason(
        with_action,
        providencias=actions(store, with_action["id"]),
        andamentos=progress(store, with_action["id"]),
    )
    linked = create(store, payload, principal)
    create_projeto_representacao(
        store,
        linked["id"],
        _principal(store, email="del-ouvi-rep@test.local", ouvidoria=True, representacoes=True),
    )
    current = get(store, linked["id"])
    assert "Representação vinculada" in delete_blocked_reason(current)
    archived = create(store, payload, principal)
    close(store, archived["id"], "ARQUIVADA", principal)
    assert "encerrada ou arquivada" in delete_blocked_reason(get(store, archived["id"]))
    from services.ouvidoria_ui import _detail, _detail_chrome, _filter_select

    source = inspect.getsource(_detail)
    assert "Excluir definitivamente" in source
    assert "ouvi_dt_prg" in source
    assert "ouvi_toolbar_flow" in source
    assert "representation_open_label" in source
    chrome = inspect.getsource(_detail_chrome)
    assert "--mpc-info-soft" in chrome
    assert "max-width:768px" in chrome
    assert "mpc-danger-zone" not in chrome
    helper = inspect.getsource(_filter_select)
    assert "Choose an option" not in helper
    assert "placeholder=empty" in helper
    assert "[None," not in helper


def test_unprotocolled_project_from_ouvidoria_can_be_deleted(store):
    from services.ouvidoria import representation_open_label
    from services.representacoes import delete as delete_project
    from services.representacoes import get as get_rep
    from services.representacoes import RELATORES, register_protocol

    both = _principal(store, email="unlink-ouvi@test.local", ouvidoria=True, representacoes=True)
    payload, *_ = _payload(store)
    noticia = create(store, payload, both)
    linked = create_projeto_representacao(store, noticia["id"], both)
    project_id = linked["representacao_id"]
    project = get_rep(store, project_id)
    assert representation_open_label(project) == "Abrir Projeto de Representação"
    assert "Representação vinculada" in delete_blocked_reason(get(store, noticia["id"]))
    delete_project(store, project_id, both)
    assert get_rep(store, project_id) is None
    remaining = get(store, noticia["id"])
    assert remaining is not None
    assert remaining["representacao_id"] is None
    types = [item["tipo"] for item in progress(store, noticia["id"])]
    headings = [andamento_display(item)[0] for item in progress(store, noticia["id"])]
    assert "PROJETO_REPRESENTACAO" in types
    assert "PROJETO_REPRESENTACAO_EXCLUIDO" in types
    assert "Projeto de Representação criado" in headings
    assert "Projeto de Representação excluído." in headings
    with store.connection(read_only=True) as c:
        assert c.execute(
            "SELECT COUNT(*) FROM ouvidoria_manifestacoes WHERE representacao_id=?",
            (project_id,),
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM representacao_integrantes WHERE representacao_id=?",
            (project_id,),
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM representacao_andamentos WHERE representacao_id=?",
            (project_id,),
        ).fetchone()[0] == 0
        assert c.execute(
            "SELECT COUNT(*) FROM representacao_documentos WHERE representacao_id=?",
            (project_id,),
        ).fetchone()[0] == 0
    refreshed = get(store, noticia["id"])
    assert delete_blocked_reason(
        refreshed,
        andamentos=progress(store, noticia["id"]),
    ) is None
    assert delete(store, noticia["id"])
    assert get(store, noticia["id"]) is None

    other = create(store, payload, both)
    protocolled_link = create_projeto_representacao(store, other["id"], both)
    protocolled = register_protocol(
        store,
        protocolled_link["representacao_id"],
        {
            "numero_processo": "TC 088888/26",
            "data_protocolo": "2026-09-21",
            "relator": RELATORES[0],
            "fase_processual": "INSTRUCAO",
        },
        both,
    )
    assert representation_open_label(protocolled) == "Abrir Representação"
    with pytest.raises(ValueError, match="não pode ser excluída"):
        delete_project(store, protocolled["id"], both)
    assert get(store, other["id"])["representacao_id"] == protocolled["id"]
