import json
from copy import deepcopy

import psycopg
import pytest
from streamlit.testing.v1 import AppTest

from database.store import ROOT
from services.ui_store import display_store, _read
from tests.cases import sample
from tests.test_deletion import finalized, remove
from tests.test_postgresql import pg_url, pg_store


@pytest.fixture(autouse=True)
def clean_read_cache():
    _read.clear()
    yield
    _read.clear()


def test_cache_shared_between_reruns_and_defensive_copy(pg_store, monkeypatch):
    first = display_store(pg_store)
    people = first.catalog("procuradores")
    first.settings()
    people[0]["nome"] = "must not leak"

    def no_queries(*args, **kwargs):
        pytest.fail("Warm display cache accessed PostgreSQL")

    monkeypatch.setattr(psycopg.Connection, "execute", no_queries)
    second = display_store(pg_store)
    assert second.catalog("procuradores")[0]["nome"] != "must not leak"
    assert second.settings()["sequence_confirmed"] == "1"


def test_only_changed_catalog_invalidated(pg_store, monkeypatch):
    ui = display_store(pg_store)
    people = ui.catalog("procuradores")
    settings = ui.settings()
    ui.catalog("bases_legais")
    changed = deepcopy(people[0])
    changed["nome"] = "Atualizado"
    pg_store.save_member(changed)
    assert ui.catalog("procuradores")[0]["nome"] == "Atualizado"

    def no_queries(*args, **kwargs):
        pytest.fail("Unrelated cache was invalidated")

    monkeypatch.setattr(psycopg.Connection, "execute", no_queries)
    assert ui.settings() == settings
    assert ui.catalog("bases_legais")


def test_config_and_role_writes_refresh_display(pg_store):
    ui = display_store(pg_store)
    ui.settings()
    ui.catalog("procuradores")
    pg_store.configure(signer_id=2)
    assert ui.settings()["signer_id"] == "2"
    member = pg_store.catalog("procuradores")[0]
    member["ativo"] = 0
    pg_store.save_member(member)
    assert ui.catalog("procuradores")[0]["ativo"] == 0


def test_rollback_does_not_evict_cache(pg_store, monkeypatch):
    ui = display_store(pg_store)
    before = ui.settings()
    with pytest.raises(RuntimeError):
        with pg_store.connection() as c:
            c.execute("UPDATE configuracoes SET valor='bad'")
            raise RuntimeError("abort")
    monkeypatch.setattr(pg_store, "settings", lambda: pytest.fail("Unnecessary reload"))
    assert ui.settings() == before


def test_history_limit_filters_and_no_binary(pg_store):
    payload = sample(pg_store)
    rows = []
    for index in range(55):
        p = deepcopy(payload)
        p["test_text"] = f"Registro {index:02d}"
        rows.append(
            (
                str(index),
                2025 if index == 0 else 2026,
                json.dumps(p, ensure_ascii=False),
                f"2026-01-01T00:00:{index:02d}",
                "now",
            )
        )
    with pg_store.connection() as c:
        c.executemany(
            "INSERT INTO portarias(id,ano,status,payload,criada,atualizada) VALUES(?,?,'Rascunho',?,?,?)",
            rows,
        )
    ui = display_store(pg_store)
    page = ui.history_page()
    assert len(page) == 51
    assert page[0]["id"] == "54"
    assert all("docx" not in r and "pdf" not in r for r in page)
    assert len(ui.history_page(offset=50)) == 5
    assert ui.history_years() == [2026, 2025]
    assert [r["id"] for r in ui.history_page(year=2025)] == ["0"]
    assert [r["id"] for r in ui.history_page(search="REGISTRO 00")] == ["0"]
    assert ui.history_page(person=7)
    assert ui.history_page(person=999) == []
    assert ui.history_page(search="%' OR 1=1 --") == []


def test_finalization_cancel_delete_invalidate_display_only(pg_store):
    ui = display_store(pg_store)
    assert ui.display_number(2026) == 9
    assert ui.history_page() == []
    assert ui.warning_payloads() == []
    identifier = finalized(pg_store)[0]
    assert ui.display_number(2026) == 10
    assert ui.history_page()[0]["status"] == "Finalizada"
    assert len(ui.warning_payloads()) == 1
    pg_store.cancel(identifier, "Teste", True)
    assert ui.history_page()[0]["status"] == "Cancelada"
    assert ui.warning_payloads() == []
    assert ui.display_number(2026) == 10
    remove(pg_store, identifier)
    assert ui.history_page() == []
    assert ui.display_number(2026) == 9
    assert pg_store.deletion_history()[0]["portaria_id_original"] == identifier


def test_display_estimate_never_replaces_authoritative_number(pg_store):
    ui = display_store(pg_store)
    assert ui.display_number(2026) == 9
    # Raw SQL simulates an external writer without in-process invalidation.
    with pg_store.connection() as c:
        c.raw.execute("UPDATE sequencias SET ultimo=12 WHERE ano=2026")
    assert ui.display_number(2026) == 9
    assert ui.next_number(2026) == 13
    identifier = finalized(ui)[0]
    assert pg_store.get(identifier)["numero"] == 13


def test_sqlite_uses_original_store(store):
    assert display_store(store) is store


def test_history_index_and_summary_are_lightweight(pg_store):
    identifier = pg_store.save_draft(sample(pg_store))
    summary = display_store(pg_store).record_summary(identifier)
    assert set(summary) == {"id", "numero", "ano", "status"}
    with pg_store.connection(read_only=True) as c:
        assert c.execute(
            "SELECT 1 FROM pg_indexes WHERE schemaname=current_schema() AND indexname='portarias_recent_idx'"
        ).fetchone()


def test_warm_pages_and_editor_fields_execute_zero_queries(pg_store, monkeypatch):
    pg_store.save_draft(sample(pg_store))
    monkeypatch.setattr("database.store.Store", lambda: pg_store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    for screen in ("Histórico", "Procuradores", "Configurações", "Nova Portaria"):
        app.sidebar.radio[0].set_value(screen).run()
        assert not app.exception and not app.error

    def no_queries(*args, **kwargs):
        pytest.fail("Warm navigation performed a query")

    monkeypatch.setattr(psycopg.Connection, "execute", no_queries)
    for screen in ("Histórico", "Procuradores", "Configurações", "Nova Portaria"):
        app.sidebar.radio[0].set_value(screen).run()
        assert not app.exception and not app.error
    next(x for x in app.selectbox if x.label == "Procurador titular").set_value(2).run()
    next(x for x in app.selectbox if x.label == "Procurador substituto").set_value(
        7
    ).run()
    assert not app.exception and not app.error


def test_fragment_preview_finalize_and_lazy_download(pg_store, monkeypatch):
    monkeypatch.setattr("database.store.Store", lambda: pg_store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    for label, value in (("Procurador titular", 2), ("Procurador substituto", 7)):
        next(x for x in app.selectbox if x.label == label).set_value(value).run()
    next(x for x in app.button if x.label == "Preparar prévia").click().run()
    assert not app.exception and not app.error
    assert pg_store.next_number(2026) == 9
    next(x for x in app.button if x.label == "FINALIZAR PORTARIA").click().run()
    assert not app.exception and not app.error
    assert pg_store.next_number(2026) == 10
    app.sidebar.radio[0].set_value("Histórico").run()
    assert not app.exception and not app.error
    assert not app.get("download_button")
    assert not any(x.label == "Carregar arquivos para download" for x in app.checkbox)
    next(x for x in app.selectbox if x.label == "Abrir Portaria").set_value(
        pg_store.history()[0]["id"]
    ).run()
    next(
        x for x in app.checkbox if x.label == "Carregar arquivos para download"
    ).check().run()
    assert app.get("download_button")


def test_dependent_period_updates_after_substitution_edit(pg_store, monkeypatch):
    from datetime import timedelta

    monkeypatch.setattr("database.store.Store", lambda: pg_store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    next(
        x for x in app.button if x.label == "+ Adicionar outra substituição"
    ).click().run()
    next(
        x
        for x in app.checkbox
        if x.label == "Usar o mesmo período da substituição anterior"
    ).check().run()
    start = next(x for x in app.date_input if x.label == "Data inicial do afastamento")
    changed = start.value + timedelta(days=1)
    start.set_value(changed).run()
    assert not app.exception and not app.error
    assert app.session_state["e0_substitution_0"]["inicio"] == changed.isoformat()
    assert app.session_state["e0_substitution_1"]["inicio"] == changed.isoformat()


def test_edited_fragment_cannot_finalize_old_preview(pg_store, monkeypatch):
    monkeypatch.setattr("database.store.Store", lambda: pg_store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    for label, value in (("Procurador titular", 2), ("Procurador substituto", 7)):
        next(x for x in app.selectbox if x.label == label).set_value(value).run()
    next(x for x in app.button if x.label == "Preparar prévia").click().run()
    next(x for x in app.selectbox if x.label == "Assento").set_value(
        "Tribunal Pleno"
    ).run()
    button = next(x for x in app.button if x.label == "FINALIZAR PORTARIA")
    assert button.disabled
    # A stale event from the browser must also be rejected on the server.
    button.click().run()
    assert pg_store.next_number(2026) == 9
    assert pg_store.history()[0]["status"] == "Rascunho"
