from inspect import getsource

from database.tarefas import TarefasStore
from services.search import (
    MIN_CHARS,
    PER_MODULE,
    global_search,
    should_run_search,
)
from tests.cases import sample
from tests.test_oficios import sample as oficio_sample
from tests.test_pending import TODAY, _admin, _prepare, _received, _user


def test_empty_and_short_terms_do_not_query(store):
    admin = _admin(store)
    calls = []
    original = store.connection

    def wrapped(*args, **kwargs):
        calls.append(kwargs.get("read_only"))
        return original(*args, **kwargs)

    store.connection = wrapped
    hits, errors, meta = global_search(store, admin, "  ")
    assert hits == []
    assert errors == {}
    assert meta["status"] == "empty"
    assert calls == []
    hits, _, meta = global_search(store, admin, "ab")
    assert meta["status"] == "short"
    assert hits == []
    assert calls == []
    run, status, _ = should_run_search("15")
    assert run and status == "ok"
    run, status, _ = should_run_search("a" * MIN_CHARS)
    assert run


def test_global_search_reuses_cabinet_map(store, monkeypatch):
    from services import search

    calls = 0
    original = search._cabinet_map

    def counted(connection):
        nonlocal calls
        calls += 1
        return original(connection)

    monkeypatch.setattr(search, "_cabinet_map", counted)
    global_search(store, _admin(store), "consulta")
    assert calls == 1


def test_search_oficios_portarias_agenda(store):
    oficios, agenda, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    oficio_id = oficios.save(oficio_sample(oficios, proge))
    portaria_id = store.save_draft(sample(store))
    agenda.save(
        dict(
            tipo="EVENTO",
            procuradores=[1],
            inicio="2026-09-20T09:00:00",
            fim="2026-09-20T10:00:00",
            situacao="Agendado",
            titulo="Seminário de licitações",
            categoria="Curso",
            local="TCE-PB",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    admin = _admin(store)
    hits, errors, meta = global_search(store, admin, "licitações")
    assert meta["status"] == "ok"
    assert not any(errors.values())
    modules = {item.source_module for item in hits}
    assert "agenda" in modules
    oficios_hits, _, _ = global_search(store, admin, "informações")
    assert any(item.source_id == oficio_id for item in oficios_hits)
    portarias_hits, _, _ = global_search(store, admin, "Sheyla")
    assert any(item.source_id == portaria_id for item in portarias_hits)
    numbered, _, _ = global_search(store, admin, "15/2026")
    assert all(item.source_module != "oficios" or item.title for item in numbered)


def test_search_oficios_covers_received_metadata_accents_dates_and_identifiers(store):
    oficios, _, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    received_id = oficios.save(
        {
            "direcao": "RECEBIDO",
            "numero_externo": "5230/2026/MFF/PR-PB/PRDC-JAS/2026",
            "remetente": "JANAINA ANDRADE DE SOUSA",
            "instituicao": "PROCURADORIA DA REPÚBLICA - PARAÍBA",
            "data": "2026-09-16",
            "data_recebimento": "2026-09-17",
            "membros": [proge],
            "assunto": "Encaminha documentação",
            "processo": "Despacho nº 18311/2026 (PR-PB-00053243/2026)",
            "observacoes": "Ofício referente a despacho e imagens da Pedra do Ingá",
            "status": "Recebido",
        }
    )
    sent_id = oficios.save(
        {
            **oficio_sample(oficios, proge),
            "assunto": "Encaminhamento enviado específico",
            "destinatario": "DESTINATÁRIO ENVIADO ESPECÍFICO",
            "processo": "PROC-ENVIADO-2026",
            "observacoes": "Observação enviada específica",
        }
    )
    admin = _admin(store)
    for term in (
        "5230",
        "Encaminha documentação",
        "Janaina",
        "JANAINA",
        "janaina",
        "Procuradoria",
        "Paraíba",
        "paraiba",
        "18311",
        "PR-PB-00053243",
        "00053243",
        "53243",
        "Pedra do Ingá",
        "inga",
        "16/09/2026",
        "17/09/2026",
    ):
        hits, errors, _ = global_search(store, admin, term)
        assert not errors.get("oficios"), term
        assert any(hit.source_id == received_id for hit in hits), term
    for term in ("Destinatário enviado específico", "PROC-ENVIADO-2026"):
        hits, _, _ = global_search(store, admin, term)
        assert any(hit.source_id == sent_id for hit in hits), term
    hits, _, _ = global_search(store, admin, "campo inexistente de oficio")
    assert all(hit.source_id != received_id for hit in hits)


def test_search_oficios_uses_one_query_without_attachment_contents():
    from services.search import search_oficios

    source = getsource(search_oficios)
    assert source.count("connection.execute(") == 1
    assert "conteudo" not in source
    assert "a.nome" in source


def test_permissions_hide_modules_cabinets_and_foreign_tasks(store):
    oficios, _, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    sbbq = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "SBBQ")
    _received(oficios, proge, "2026-09-15", gabinete_member=proge)
    hidden = oficios.save(
        dict(
            direcao="RECEBIDO",
            numero_externo="EXT-SBBQ",
            remetente="Remetente",
            instituicao="Órgão",
            data="2026-09-01",
            data_recebimento="2026-09-10",
            membros=[sbbq],
            assunto="Assunto confidencial SBBQ",
            prazo="2026-09-20",
            status="Recebido",
        )
    )
    admin = _admin(store)
    tasks = TarefasStore(store)
    mine = tasks.create(admin.id, {"titulo": "Minha tarefa exclusiva", "prazo_data": "2026-09-14"})
    other = tasks.create(99, {"titulo": "Tarefa alheia exclusiva", "prazo_data": "2026-09-14"})
    from services.representacoes import create as create_rep
    from tests.test_representacoes import _payload as rep_payload
    from services.ouvidoria import create as create_ouvi
    from tests.test_ouvidoria import _payload as ouvi_payload

    rep = create_rep(store, rep_payload(store, titulo="Representação único tema X")[0], admin)
    nf = create_ouvi(
        store,
        ouvi_payload(store, titulo="Notícia único fato Y", situacao="TRIAGEM")[0],
        admin,
    )
    limited = _user(
        store,
        "busca.proge@test.local",
        pode_agenda=False,
        pode_oficios=True,
        pode_memorandos=False,
        pode_portarias=False,
        pode_admin=False,
        pode_representacoes=False,
        pode_ouvidoria=False,
        gabinetes=["PROGE"],
    )
    hits, _, _ = global_search(store, limited, "exclusiva")
    assert all(item.source_module != "portarias" for item in hits)
    assert all(item.source_module != "representacoes" for item in hits)
    assert all(item.source_module != "ouvidoria" for item in hits)
    cabinet_hits, _, _ = global_search(store, limited, "confidencial")
    assert all(item.source_id != hidden for item in cabinet_hits)
    own_tasks, _, _ = global_search(store, admin, "exclusiva")
    assert any(item.source_id == str(mine["id"]) for item in own_tasks)
    assert all(item.source_id != str(other["id"]) for item in own_tasks)
    denied_rep = _user(
        store,
        "sem.rep.busca@test.local",
        pode_agenda=True,
        pode_oficios=True,
        pode_memorandos=False,
        pode_portarias=True,
        pode_admin=False,
        pode_representacoes=False,
        pode_ouvidoria=False,
        gabinetes=["PROGE"],
    )
    hidden_rep, _, _ = global_search(store, denied_rep, "único")
    assert all(item.source_id != str(rep["id"]) for item in hidden_rep)
    assert all(item.source_id != str(nf["id"]) for item in hidden_rep)


def test_module_limit_and_no_blobs():
    import services.search as search

    source = getsource(search)
    assert "conteudo" not in source
    assert "docx" not in source
    assert "memorandos_arquivos" not in source
    assert "SELECT *" not in source
    assert "LIMIT ?" in source
    assert PER_MODULE <= 10


def test_open_origin_from_search(monkeypatch):
    from services.search import SearchHit
    from services.search_ui import _open

    captured = []

    def fake(module, **state):
        captured.append((module, state))

    monkeypatch.setattr("portal.request_portal_navigation", fake)
    _open(
        SearchHit(
            source_module="portarias",
            source_id="p1",
            gabinete="—",
            title="Portaria",
            subtitle="",
            description="",
            date=None,
            status="Finalizada",
            score=80,
            navigation="Portarias",
        )
    )
    _open(
        SearchHit(
            source_module="oficios",
            source_id="of-1",
            gabinete="PROGE",
            title="Ofício",
            subtitle="",
            description="",
            date=TODAY,
            status="Recebido",
            score=80,
            navigation="Ofícios",
            metadata={"direcao": "RECEBIDO"},
        )
    )
    assert captured[0] == (
        "Portarias",
        {"nav": "Histórico", "portaria_open_id": "p1"},
    )
    assert captured[1][0] == "Ofícios"
    assert captured[1][1]["pending_open_oficio"]["id"] == "of-1"


def test_source_error_is_isolated(store, monkeypatch):
    oficios, _, _ = _prepare(store)
    proge = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    oficios.save(oficio_sample(oficios, proge))

    from services import search as search_mod

    def boom(*args, **kwargs):
        raise RuntimeError("oficios indisponível")

    monkeypatch.setitem(search_mod.LOADERS, "oficios", boom)
    hits, errors, meta = global_search(store, _admin(store), "informações")
    assert meta["status"] == "ok"
    assert errors.get("oficios") == "oficios"
    assert all(item.source_module != "oficios" for item in hits)


def test_source_error_remains_isolated_if_audit_logging_fails(store, monkeypatch):
    from services import search as search_mod

    def boom(*args, **kwargs):
        raise RuntimeError("indisponível")

    monkeypatch.setitem(search_mod.LOADERS, "portarias", boom)
    monkeypatch.setattr(search_mod, "registrar_erro", boom)
    hits, errors, meta = global_search(store, _admin(store), "Sheyla")
    assert meta["status"] == "ok"
    assert errors.get("portarias") == "portarias"
    assert all(item.source_module != "portarias" for item in hits)


def test_home_search_controls(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    captions = [str(c.value) for c in app.caption]
    assert any("Pesquisar no Ferramentas MPC-PB" in str(i.value) for i in app.text_input) or any(
        "Busca global" in str(m.value) for m in app.markdown
    )
    assert any("Digite pelo menos" in c for c in captions)
    app.text_input(key="global_search_q").set_value("ab").run()
    assert app.session_state["global_search_run"] == "ab"
    assert any("ao menos" in str(item.value) for item in app.markdown)
    app.button(key="global_search_submit").click().run()
    assert not app.exception
    blob = " ".join(str(m.value) for m in app.markdown) + " ".join(
        str(c.value) for c in app.caption
    )
    assert "ao menos" in blob or "3 caracteres" in blob


def test_home_search_clear_resets_results_input_and_errors(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    store.save_draft(sample(store))
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()

    app.text_input(key="global_search_q").set_value("Sheyla").run()
    app.button(key="global_search_submit").click().run()
    assert not app.exception and not app.error
    result_text = " ".join(str(c.value) for c in app.caption)
    assert "Resultados para" in result_text
    clear_key = "global_search_clear"
    assert any(button.key == clear_key for button in app.button)

    app.session_state["global_search_error"] = "estado residual"
    app.button(key=clear_key).click().run()

    assert not app.exception and not app.error
    assert next(item for item in app.text_input if item.label == "Busca").value == ""
    assert "global_search_run" not in app.session_state
    assert "global_search_error" not in app.session_state
    reset_text = " ".join(str(c.value) for c in app.caption)
    assert "Digite pelo menos" in reset_text
    assert "Resultados para" not in reset_text
    assert not any(button.key == clear_key for button in app.button)


def test_home_search_clears_residual_state_on_reentry(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.session_state["global_search_q"] = "residual"
    app.session_state["global_search_run"] = "residual"
    app.sidebar.radio(key="portal_module").set_value("Portarias").run()
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    assert not app.exception
    assert app.text_input(key="global_search_q").value == ""
    assert "global_search_run" not in app.session_state
    captions = " ".join(str(c.value) for c in app.caption)
    assert "Digite pelo menos" in captions


def test_home_search_only_shows_persistent_source_error(store, monkeypatch):
    from services import search_ui

    monkeypatch.setattr(
        search_ui,
        "global_search",
        lambda *args, **kwargs: (
            [],
            {"oficios": "oficios"},
            {"status": "ok", "term": "falha", "total": 0},
        ),
    )
    monkeypatch.setattr(search_ui.st, "session_state", {"global_search_run": "falha"})
    errors = []
    warnings = []
    monkeypatch.setattr(search_ui.st, "error", errors.append)
    monkeypatch.setattr(search_ui.st, "warning", warnings.append)
    monkeypatch.setattr(search_ui.st, "caption", lambda *args, **kwargs: None)
    monkeypatch.setattr(search_ui, "section_label", lambda *args, **kwargs: None)
    monkeypatch.setattr(search_ui, "empty_state", lambda *args, **kwargs: None)

    class Column:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(
        search_ui.st, "columns", lambda *args, **kwargs: (Column(), Column())
    )
    monkeypatch.setattr(search_ui.st, "text_input", lambda *args, **kwargs: "falha")
    monkeypatch.setattr(search_ui.st, "button", lambda *args, **kwargs: False)
    search_ui.st.session_state[search_ui.HOME_SEARCH_ACTIVE] = True

    search_ui.render_home_search(store, _admin(store))
    assert errors == [] and warnings == []
    search_ui.render_home_search(store, _admin(store))
    assert errors == ["Não foi possível concluir a busca agora."]


def _ids(hits, module):
    return {item.source_id for item in hits if item.source_module == module}


def test_search_representation_ignores_case_and_diacritics(store):
    from services.representacoes import (
        add_progress,
        create as create_rep,
        exclude_progress,
        register_protocol,
    )
    from services.ouvidoria import create as create_ouvi
    from services.search import search_ouvidoria, search_representacoes
    from tests.test_ouvidoria import _payload as ouvi_payload
    from tests.test_representacoes import _payload as rep_payload
    from tests.test_representacoes import _server

    admin = _admin(store)
    elvira = next(
        person["id"]
        for person in store.catalog("procuradores")
        if person["nome"] == "Elvira Samara Pereira de Oliveira"
    )
    data, _member, others, _assessor = rep_payload(store)
    data.update(
        titulo="REPRESENTAÇÃO em face da Prefeitura Municipal de Baía da Traição",
        representado="Prefeitura Municipal de Baía da Traição",
        procurador_responsavel=elvira,
        procuradores_signatarios=[item for item in others if item != elvira],
        assessores=[_server(store, "Lúcia Patrício de Souza Araújo")],
    )
    created = create_rep(store, data, admin)
    register_protocol(
        store,
        created["id"],
        {
            "numero_processo": "05483/26",
            "relator": "André Carlo Torres Pontes",
            "fase_processual": "INSTRUCAO",
            "data_protocolo": "2026-09-25",
        },
        admin,
    )
    add_progress(
        store,
        created["id"],
        {"tipo": "LIVRE", "descricao": "Andamento visível da instrução"},
        admin,
    )
    hidden = add_progress(
        store,
        created["id"],
        {"tipo": "LIVRE", "descricao": "Segredo excluído da busca global"},
        admin,
    )
    secret = next(item for item in hidden if item["descricao"].startswith("Segredo"))
    exclude_progress(store, created["id"], secret["id"], "Registro indevido", admin)
    tasks = TarefasStore(store)
    task = tasks.create(
        admin.id,
        {
            "titulo": "Noticiar representação de Baía da Traição",
            "prazo_data": "2026-09-14",
        },
    )
    tasks.change_status(task["id"], admin.id, "CONCLUIDA")
    _oficios, agenda, _memorandos = _prepare(store)
    agenda.save(
        dict(
            tipo="EVENTO",
            procuradores=[elvira],
            inicio="2026-09-20T09:00:00",
            fim="2026-09-20T10:00:00",
            situacao="Agendado",
            titulo="Seminário de licitações",
            categoria="Curso",
            local="TCE-PB",
        ),
        institutional_confirmed=True,
        conflict_confirmed=True,
    )
    news = create_ouvi(
        store,
        ouvi_payload(
            store,
            titulo="Irregularidade em Conceição",
            manifestante_identificado=True,
            manifestante_nome="Zenóbia Secreta",
        )[0],
        admin,
    )
    representation_id = str(created["id"])
    task_id = str(task["id"])

    def found(term):
        hits, errors, _meta = global_search(store, admin, term)
        assert not any(errors.values()), errors
        return hits

    for term in ("baia da traicao", "Baía da Traição", "BAIA DA TRAICAO"):
        hits = found(term)
        assert representation_id in _ids(hits, "representacoes"), term
        assert task_id in _ids(hits, "tarefas"), term
        assert any(item.navigation == "Representações" for item in hits if item.source_id == representation_id)
    for term, module, identifier in (
        ("05483/26", "representacoes", representation_id),
        ("Elvira Samara", "representacoes", representation_id),
        ("Lucia Patricio", "representacoes", representation_id),
        ("Andre Carlo Torres Pontes", "representacoes", representation_id),
        ("andamento visivel", "representacoes", representation_id),
        ("seminario", "agenda", None),
        ("conceicao", "ouvidoria", str(news["id"])),
    ):
        hits = found(term)
        if identifier is None:
            assert _ids(hits, module), term
        else:
            assert identifier in _ids(hits, module), term
    hidden_hits = found("segredo excluido")
    assert representation_id not in _ids(hidden_hits, "representacoes")
    private_hits = found("zenobia secreta")
    assert str(news["id"]) not in _ids(private_hits, "ouvidoria")
    denied = _user(
        store,
        "sem.rep.acento@test.local",
        pode_representacoes=False,
        pode_ouvidoria=False,
    )
    denied_hits, denied_errors, _meta = global_search(store, denied, "05483/26")
    assert not any(denied_errors.values())
    assert representation_id not in _ids(denied_hits, "representacoes")
    empty, empty_errors, empty_meta = global_search(store, admin, "zzzz-sem-resultado-xyz")
    assert empty == [] and not any(empty_errors.values()) and empty_meta["total"] == 0
    assert getsource(search_representacoes).count("connection.execute(") == 1
    assert "excluido=0" in getsource(search_representacoes)
    assert "manifestante" not in getsource(search_ouvidoria)


def test_like_metacharacters_stay_literal_and_portuguese_diacritics_match(store):
    from services.search import _normalized_sql, like_value, normalize_search_text

    folded = {
        "Antônio": "antonio",
        "ANTÔNIO": "antonio",
        "Avô": "avo",
        "AVÔ": "avo",
        "Conceição": "conceicao",
        "Lúcia": "lucia",
        "João": "joao",
        "José": "jose",
        "Ângela": "angela",
        "Márcia": "marcia",
        "Baía da Traição": "baia da traicao",
        "D'Ávila": "d'avila",
    }
    for original, expected in folded.items():
        assert normalize_search_text(original) == expected
    with store.connection() as connection:
        connection.execute("CREATE TEMP TABLE fold_probe(valor TEXT)")
        connection.executemany(
            "INSERT INTO fold_probe VALUES(?)", [(value,) for value in folded]
        )
        expression = _normalized_sql("valor")
        for original, expected in folded.items():
            stored = connection.execute(
                f"SELECT {expression} FROM fold_probe WHERE valor=?",
                (original,),
            ).fetchone()[0]
            assert stored == expected
        connection.executemany(
            "INSERT INTO fold_probe VALUES(?)",
            [
                ("Alíquota de 50%",),
                ("Registro comum sem curinga",),
                ("abcXdef sem underline",),
                ("Código abc_def literal",),
                ("teste de rotina",),
                ("teste% oficial",),
            ],
        )
        probe = f"SELECT valor FROM fold_probe WHERE {expression} LIKE ? ESCAPE '\\'"

        def matched(term):
            return {
                row[0]
                for row in connection.execute(
                    probe, (like_value(normalize_search_text(term)),)
                )
            }

        assert "Registro comum sem curinga" not in matched("%%%")
        assert "Registro comum sem curinga" not in matched("___")
        assert "abcXdef sem underline" not in matched("abc_def")
        assert "Código abc_def literal" in matched("abc_def")
        assert "teste de rotina" not in matched("teste%")
        assert "teste% oficial" in matched("teste%")
        assert matched("50%") == {"Alíquota de 50%"}
        assert "D'Ávila" in matched("d'ávila")
        assert "D'Ávila" in matched("d'avila")

    admin = _admin(store)
    tasks = TarefasStore(store)
    titles = {
        "Alíquota de 50%": "percent",
        "Registro comum sem curinga": "plain",
        "abcXdef sem underline": "wild",
        "Código abc_def literal": "literal",
        "Parecer de D'Ávila": "apostrophe",
        "Antônio Gomes": "antonio",
        "Avô remoto": "avo",
        "Conceição Lima": "conceicao",
        "Lúcia Patrício": "lucia",
    }
    created = {
        key: str(
            tasks.create(admin.id, {"titulo": title, "prazo_data": "2026-09-14"})["id"]
        )
        for title, key in titles.items()
    }

    def found(term):
        hits, errors, meta = global_search(store, admin, term)
        assert not any(errors.values()), errors
        return hits, meta

    for term in ("%", "_", "'"):
        hits, meta = found(term)
        assert hits == [] and meta["status"] == "short"
    for term in ("%%%", "___", "teste%", "%' OR '1'='1"):
        hits, meta = found(term)
        assert meta["status"] == "ok"
        assert created["plain"] not in _ids(hits, "tarefas")
    percent, _meta = found("50%")
    assert created["percent"] in _ids(percent, "tarefas")
    assert created["plain"] not in _ids(percent, "tarefas")
    underlined, _meta = found("abc_def")
    assert created["literal"] in _ids(underlined, "tarefas")
    assert created["wild"] not in _ids(underlined, "tarefas")
    apostrophe, _meta = found("d'ávila")
    assert created["apostrophe"] in _ids(apostrophe, "tarefas")
    for term, key in (
        ("antonio", "antonio"),
        ("avo", "avo"),
        ("conceicao", "conceicao"),
        ("lucia", "lucia"),
    ):
        hits, _meta = found(term)
        assert created[key] in _ids(hits, "tarefas")


def test_authenticated_home_search_never_mounts_a_streamlit_form(store, monkeypatch):
    from inspect import getsource
    from streamlit.testing.v1 import AppTest

    from database.store import ROOT
    from services.search_ui import render_home_search
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()

    assert not app.exception and not app.error
    assert any(button.key == "global_search_submit" for button in app.button)
    assert "st.form(" not in getsource(render_home_search)
    assert "st.form_submit_button" not in getsource(render_home_search)
