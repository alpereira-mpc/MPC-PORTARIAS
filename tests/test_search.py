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


def test_home_search_form(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from tests.access_testing import enable_login

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    store.save_draft(sample(store))
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    captions = [str(c.value) for c in app.caption]
    assert any("Pesquisar no Ferramentas MPC-PB" in str(i.value) for i in app.text_input) or any(
        "Busca global" in str(m.value) for m in app.markdown
    )
    assert any("Digite pelo menos" in c for c in captions)
    app.text_input(key="global_search_q").set_value("Sheyla").run()
    # Form submit needs the button inside the form.
    app.button(key="FormSubmitter:global_search_form-Buscar").click().run()
    assert not app.exception
    blob = " ".join(str(m.value) for m in app.markdown) + " ".join(
        str(c.value) for c in app.caption
    )
    assert "Resultados para" in blob
    assert app.button(key="FormSubmitter:global_search_form-Limpar busca")
    app.button(key="FormSubmitter:global_search_form-Limpar busca").click().run()
    assert not app.exception
    assert app.text_input(key="global_search_q").value == ""
    reset_blob = " ".join(str(m.value) for m in app.markdown) + " ".join(
        str(c.value) for c in app.caption
    )
    assert "Digite pelo menos" in reset_blob
    assert "Informe ao menos" not in reset_blob
    assert "Resultados para" not in reset_blob
