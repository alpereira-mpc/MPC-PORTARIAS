"""Busca Global finds one structured Petição per record, with the same rules as other modules."""

from inspect import getsource

from database.peticoes import PeticoesStore
from database.store import now
from portal import (
    MODULE_LISTING_KEYS,
    MODULE_NAVIGATION_RESET,
    PORTAL_LAST_MODULE,
    PORTAL_NAV_REQUEST,
    enter_portal_module,
)
from services import search as search_mod
from services.search import PER_MODULE, SearchHit, global_search, search_peticoes
from tests.cases import sample
from tests.test_pending import _admin, _user
from tests.test_peticoes import _payload, _pdf, _principal
from services.peticoes import add_progress, create, save_resultado


def _create(store, principal, **changes):
    payload = _payload(store, **changes)
    return create(store, payload, principal, ("peticao.pdf", "application/pdf", _pdf()))


def _marcilio(store):
    return next(
        row["id"] for row in store.catalog("procuradores") if "Marcílio" in row["nome"]
    )


def _ids(hits):
    return [item.source_id for item in hits if item.source_module == "peticoes"]


def _found(store, principal, term):
    hits, errors, meta = global_search(store, principal, term)
    assert meta["status"] == "ok", meta
    assert not any(errors.values()), errors
    return hits


def _insert_pedido(store, peticao_id, descricao, ordem, *, excluido=0, resultado=""):
    stamp = now()
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO peticoes_pedidos("
            "peticao_id,descricao,ordem,resultado,criado_em,atualizado_em,excluido"
            ") VALUES(?,?,?,?,?,?,?)",
            (peticao_id, descricao, ordem, resultado, stamp, stamp, excluido),
        )


def _insert_andamento(store, peticao_id, descricao, *, excluido=0):
    stamp = now()
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO peticoes_andamentos("
            "peticao_id,data,descricao,criado_em,criado_por,excluido"
            ") VALUES(?,?,?,?,?,?)",
            (peticao_id, "2026-09-30", descricao, stamp, "teste", excluido),
        )


def test_structured_fields_and_tramita_number(store):
    principal = _principal(store)
    admin = _admin(store)
    record = _create(
        store,
        principal,
        numero_tramita="116439/26",
        assunto="Assunto exclusivo Ingá",
        objeto="Fiscalização em Itacoatiaras",
        origem="APBFISCO",
        natureza="NOTA_RECOMENDATORIA",
        processo_tc="TC-991884/2026",
        destinatario="Relatoria Singular Zeta",
        signatarios=[_marcilio(store)],
        pedidos=["Pedido neutro da petição principal"],
    )
    add_progress(
        store,
        record["id"],
        "2026-09-30",
        "Encaminhada à unidade local.",
        principal,
    )
    identifier = str(record["id"])

    hits = _found(store, admin, "116439/26")
    match = next(item for item in hits if item.source_id == identifier)
    assert match.source_module == "peticoes"
    assert match.module_label == "Petições"
    assert match.status == "Em acompanhamento"
    assert "116439/26" in match.title
    assert "Ingá" in match.title
    assert "Itacoatiaras" in match.subtitle
    assert match.description == "APBFISCO"
    assert _ids(hits) == [identifier]

    expectations = (
        "Ingá",
        "inga",
        "Itacoatiaras",
        "itacoatiaras",
        "APBFISCO",
        "apbfisco",
        "Nota Recomendatória",
        "nota recomendatoria",
        "TC-991884/2026",
        "Singular Zeta",
        "Em acompanhamento",
        "Marcílio",
        "marcilio",
    )
    for term in expectations:
        assert identifier in _ids(_found(store, admin, term)), term


def test_requests_progress_result_history_and_one_hit(store):
    principal = _principal(store)
    admin = _admin(store)
    pedido = _create(
        store,
        principal,
        numero_tramita="200001/26",
        assunto="Cadastro comum alfa",
        objeto="Objeto comum alfa",
        pedidos=["Pedido neutro alfa"],
    )
    _insert_pedido(store, pedido["id"], "linha histórica dois", 2)
    _insert_pedido(store, pedido["id"], "linha histórica três com termo lagoa", 3)
    andamento = _create(
        store,
        principal,
        numero_tramita="200002/26",
        assunto="Cadastro comum beta",
        objeto="Objeto comum beta",
        pedidos=["Pedido neutro beta"],
    )
    _insert_andamento(store, andamento["id"], "Descrição isolada de diligência verde")
    resultado = _create(
        store,
        principal,
        numero_tramita="200003/26",
        assunto="Cadastro comum gama",
        objeto="Objeto comum gama",
        pedidos=["Pedido neutro gama"],
    )
    save_resultado(
        store, resultado["id"], "Desfecho exclusivo da petição roxa", principal
    )
    repeated = _create(
        store,
        principal,
        numero_tramita="200004/26",
        assunto="tokenrepetido77 no assunto",
        objeto="tokenrepetido77 no objeto",
        pedidos=["tokenrepetido77 no pedido"],
    )
    _insert_andamento(store, repeated["id"], "tokenrepetido77 no andamento")
    hidden = _create(
        store,
        principal,
        numero_tramita="200005/26",
        assunto="Cadastro comum delta",
        objeto="Objeto comum delta",
        pedidos=["Pedido neutro delta"],
        observacoes="observacao secreta oculta",
    )
    _insert_pedido(
        store,
        hidden["id"],
        "termo somente pedido excluido qx",
        4,
        excluido=1,
    )
    _insert_andamento(
        store,
        hidden["id"],
        "termo somente andamento excluido qy",
        excluido=1,
    )
    with store.connection() as connection:
        connection.execute(
            "UPDATE peticoes_pedidos SET resultado=? "
            "WHERE peticao_id=? AND excluido=0",
            ("resultado individual antigo qw", hidden["id"]),
        )

    assert _ids(_found(store, admin, "termo lagoa")) == [str(pedido["id"])]
    assert _ids(_found(store, admin, "diligência verde")) == [str(andamento["id"])]
    assert _ids(_found(store, admin, "diligencia verde")) == [str(andamento["id"])]
    assert _ids(_found(store, admin, "petição roxa")) == [str(resultado["id"])]
    assert _ids(_found(store, admin, "peticao roxa")) == [str(resultado["id"])]
    assert _ids(_found(store, admin, "tokenrepetido77")) == [str(repeated["id"])]
    for term in (
        "pedido excluido qx",
        "andamento excluido qy",
        "individual antigo qw",
        "observacao secreta oculta",
        "secreta oculta",
    ):
        assert _ids(_found(store, admin, term)) == [], term


def test_permission_literals_limit_and_other_modules(store, monkeypatch):
    principal = _principal(store)
    admin = _admin(store)
    record = _create(
        store,
        principal,
        numero_tramita="300010/26",
        assunto="Alíquota de 100%",
        objeto="código abc_def literal",
        pedidos=["Pedido neutro percentual"],
    )
    decoy = _create(
        store,
        principal,
        numero_tramita="300011/26",
        assunto="cem por cento integral",
        objeto="abcXdef sem underline",
        pedidos=["Pedido neutro decoy"],
    )
    portaria_id = store.save_draft(sample(store))
    denied = _user(
        store,
        "sem.peticao.busca@test.local",
        pode_peticoes=False,
        pode_portarias=True,
        pode_oficios=False,
        pode_agenda=False,
        pode_memorandos=False,
        pode_admin=False,
        pode_representacoes=False,
        pode_ouvidoria=False,
    )
    with store.connection(read_only=True) as connection:
        assert search_peticoes(connection, store, denied, "100%", limit=8) == []
    hidden, errors, _meta = global_search(store, denied, "300010/26")
    assert not any(errors.values())
    leaked = " ".join(
        " ".join((item.title, item.subtitle, item.description, item.source_id))
        for item in hidden
    )
    assert "300010/26" not in leaked
    assert "Alíquota" not in leaked
    assert all(item.source_module != "peticoes" for item in hidden)
    assert _ids(global_search(store, denied, "100%")[0]) == []

    percent = _ids(_found(store, admin, "100%"))
    assert percent == [str(record["id"])]
    assert str(decoy["id"]) not in _ids(_found(store, admin, "%%%"))
    assert _ids(_found(store, admin, "abc_def")) == [str(record["id"])]
    assert str(decoy["id"]) not in _ids(_found(store, admin, "abc_def"))
    assert str(record["id"]) not in _ids(_found(store, admin, "___"))

    kept = _found(store, admin, "Sheyla")
    assert any(
        item.source_module == "portarias" and item.source_id == portaria_id
        for item in kept
    )
    assert all(item.source_id != str(record["id"]) for item in kept)

    oldest = None
    for index, day in enumerate(
        ["2020-01-01", *[f"2026-01-{number:02d}" for number in range(1, 9)]]
    ):
        identifier = _insert_plain(
            store, f"4000{index:02d}/26", "LoteLimitePeticao", day
        )
        if day == "2020-01-01":
            oldest = str(identifier)
    limited = _ids(_found(store, admin, "LoteLimitePeticao"))
    assert len(limited) == PER_MODULE
    assert oldest not in limited
    first = _insert_plain(store, "500001/26", "OrdemRecentePeticao", "2024-02-01")
    second = _insert_plain(store, "500002/26", "OrdemRecentePeticao", "2026-08-01")
    ordered, order_errors, _meta = global_search(
        store, admin, "OrdemRecentePeticao", limit_per_module=1
    )
    assert not any(order_errors.values())
    assert _ids(ordered) == [str(second)]
    assert str(first) not in _ids(ordered)

    def boom(*_args, **_kwargs):
        raise RuntimeError("peticoes indisponível")

    monkeypatch.setitem(search_mod.LOADERS, "peticoes", boom)
    hits, failures, meta = global_search(store, admin, "Sheyla")
    assert meta["status"] == "ok"
    assert failures.get("peticoes") == "peticoes"
    assert any(item.source_module == "portarias" for item in hits)


def _insert_plain(store, numero, assunto, day):
    stamp = now()
    with store.connection() as connection:
        row = connection.execute(
            "INSERT INTO peticoes(numero_tramita,data_protocolo,destinatario_tipo,"
            "destinatario,natureza,assunto,objeto,criado_em,criado_por,atualizado_em,"
            "atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                numero,
                day,
                "Presidente do TCE-PB",
                "Presidência",
                "PROVIDENCIAS",
                assunto,
                "objeto neutro",
                stamp,
                "teste",
                stamp,
                "teste",
            ),
        )
        return row.lastrowid


def test_search_does_not_touch_pdf_storage_or_model(store, monkeypatch):
    principal = _principal(store)
    record = _create(
        store,
        principal,
        numero_tramita="600001/26",
        assunto="Consulta estruturada",
        objeto="Somente colunas",
    )

    def forbid(*_args, **_kwargs):
        raise AssertionError("efeito colateral indevido")

    monkeypatch.setattr(PeticoesStore, "document", forbid)
    import services.ai_service as ai_service

    monkeypatch.setattr(ai_service, "_gravar_telemetria", forbid, raising=False)
    hits = _found(store, _admin(store), "600001/26")
    assert _ids(hits) == [str(record["id"])]
    source = getsource(search_peticoes)
    assert source.count("connection.execute(") == 1
    folded = source.lower()
    for banned in (
        "peticoes_documentos",
        "arquivo",
        "gemini",
        "ocr",
        "d.resultado",
        "situacao_resultado",
        "p.observacoes",
    ):
        assert banned not in folded
    assert folded.count("excluido=0") == 2
    assert "select *" not in folded


def test_open_peticao_reuses_portal_navigation(monkeypatch):
    captured = []

    def fake(module, **state):
        captured.append((module, state))

    monkeypatch.setattr("portal.request_portal_navigation", fake)
    from services.search_ui import _open

    _open(
        SearchHit(
            source_module="peticoes",
            source_id="15",
            gabinete="—",
            title="116439/26 — Assunto",
            subtitle="Objeto",
            description="APBFISCO",
            date=None,
            status="Protocolada",
            score=100,
            navigation="Petições",
        )
    )
    assert captured == [("Petições", {"peticoes_view": 15})]


def test_deep_link_survives_apply_and_sidebar_clears_it(monkeypatch):
    import portal

    allowed = ["Busca Global", "Agenda", "Petições"]
    state = {
        PORTAL_LAST_MODULE: "Busca Global",
        "portal_module": "Busca Global",
        PORTAL_NAV_REQUEST: {"module": "Petições", "state": {"peticoes_view": 15}},
        "peticoes_edit_id": 3,
        "peticoes_painel": {"d1": True},
        "peticoes_secao": "Cadastrar Petição",
    }
    monkeypatch.setattr(portal.st, "session_state", state)
    enter_portal_module(allowed)
    assert state["portal_module"] == "Petições"
    assert state["peticoes_view"] == 15
    assert state["peticoes_secao"] == "Acompanhamento"
    assert "peticoes_edit_id" not in state
    assert "peticoes_painel" not in state

    assert "peticoes_view" not in MODULE_LISTING_KEYS.get("Petições", ())
    assert "peticoes_view" in MODULE_NAVIGATION_RESET["Petições"]
    state.update(
        {
            PORTAL_LAST_MODULE: "Agenda",
            "portal_module": "Petições",
            "peticoes_view": 15,
            "peticoes_view_ready": 15,
            "peticoes_secao": "Histórico",
        }
    )
    enter_portal_module(allowed)
    assert "peticoes_view" not in state
    assert "peticoes_view_ready" not in state
    assert state["peticoes_secao"] == "Acompanhamento"


def test_search_focus_selects_section_once(monkeypatch):
    import services.peticoes_ui as ui

    class DB:
        def __init__(self, situacao):
            self.situacao = situacao

        def get(self, identifier):
            return {
                "id": identifier,
                "situacao": self.situacao,
                "numero_tramita": "9/26",
            }

    state = {"peticoes_view": "4"}
    monkeypatch.setattr(ui.st, "session_state", state)
    assert ui._prepare_search_focus(DB("CONCLUIDA")) == 4
    assert state["peticoes_secao"] == "Histórico"
    assert state["peticoes_painel"]["d4"] is True
    state["peticoes_secao"] = "Cadastrar Petição"
    state["peticoes_painel"]["d4"] = False
    assert ui._prepare_search_focus(DB("CONCLUIDA")) == 4
    assert state["peticoes_secao"] == "Cadastrar Petição"
    assert state["peticoes_painel"]["d4"] is False

    state = {"peticoes_view": 8}
    monkeypatch.setattr(ui.st, "session_state", state)
    assert ui._prepare_search_focus(DB("PROTOCOLADA")) == 8
    assert state["peticoes_secao"] == "Acompanhamento"
    assert ui._section_shows_focus({"situacao": "CONCLUIDA"}, False)
    assert ui._section_shows_focus({"situacao": "PROTOCOLADA"}, True)
    assert not ui._section_shows_focus({"situacao": "CONCLUIDA"}, True)
