from pathlib import Path
from streamlit.testing.v1 import AppTest
import pytest
from database.store import ROOT, Store
from tests.access_testing import enable_login


@pytest.mark.parametrize("fails", [False, True])
def test_pdf_download_does_not_retain_session_bytes(tmp_path, monkeypatch, fails):
    import database.store as persistence
    from document_generator.pdf import PdfUnavailable
    from tests.cases import sample
    import services.exports as exports

    original = persistence.Store
    path = tmp_path / "download.db"
    store = original(path)
    store.configure(export_dir=str(tmp_path / "exports"))
    store.set_sequence(2026, 8, True)
    identifier = store.save_draft(sample(store))
    store.finalize(identifier)  # Isolated test database only.
    monkeypatch.setattr(persistence, "Store", lambda: original(path))
    enable_login(monkeypatch, store)
    output = b"%PDF-1.4\nsynthetic"

    def convert(*args):
        if fails:
            raise PdfUnavailable("PDF indisponível. O DOCX permanece disponível.")
        return output, "test"

    monkeypatch.setattr(exports, "convert", convert)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_portarias").click().run()
    app.session_state["old_recordfilepdf"] = ("old.pdf", output)
    app.sidebar.radio(key="nav").set_value("Histórico").run()
    next(x for x in app.selectbox if x.label == "Abrir Portaria").set_value(
        identifier
    ).run()
    next(x for x in app.button if x.label == "GERAR PDF").click().run()
    assert not app.exception
    assert bool(app.error) is fails
    assert not any(
        k.endswith(("filedocx", "filepdf")) for k in app.session_state.filtered_state
    )
    assert any(x.label == "Baixar DOCX" for x in app.get("download_button"))
    app.run()
    assert not app.exception
    if not fails:
        assert any(x.label == "Baixar PDF" for x in app.get("download_button"))
        assert store.get(identifier)["pdf"] == output


def test_ui_administrative_deletion_confirmation(tmp_path, monkeypatch):
    import database.store as persistence
    from tests.cases import sample

    original = persistence.Store
    path = tmp_path / "delete_ui.db"
    store = original(path)
    store.set_sequence(2026, 8, True)
    identifier = store.save_draft(sample(store))
    store.finalize(identifier)
    draft_id = store.save_draft(sample(store))
    monkeypatch.setattr(persistence, "Store", lambda: original(path))
    enable_login(monkeypatch, store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_portarias").click().run()
    app.sidebar.radio(key="nav").set_value("Histórico").run()
    next(x for x in app.selectbox if x.label == "Abrir Portaria").set_value(
        identifier
    ).run()
    next(
        x for x in app.button if x.label == "Excluir Portaria definitivamente"
    ).click().run()
    button = lambda: next(x for x in app.button if x.label == "EXCLUIR DEFINITIVAMENTE")
    assert button().disabled
    next(
        x for x in app.checkbox if x.label.startswith("Confirmo que esta Portaria")
    ).check().run()
    assert button().disabled
    next(
        x for x in app.text_input if x.label == "Digite EXCLUIR para confirmar"
    ).set_value("EXCLUIR").run()
    assert not button().disabled
    button().click().run()
    assert not app.error and not app.exception
    assert len(store.history()) == 1 and store.next_number(2026) == 9
    assert len(store.deletion_history()) == 1
    assert any(x.label == "Registro de exclusões" for x in app.expander)
    next(
        x for x in app.checkbox if x.label == "Confirmo a exclusão deste rascunho"
    ).check().run()
    next(x for x in app.button if x.label == "Excluir rascunho").click().run()
    assert not app.error and not app.exception
    assert not store.history()


def test_ui_draft_delete_and_natural_legacy_reason(tmp_path, monkeypatch):
    import database.store as persistence
    from tests.cases import sample
    import json

    original = persistence.Store
    path = tmp_path / "draft_delete_ui.db"
    store = original(path)
    store.set_sequence(2026, 8, True)
    p = sample(store)
    identifier = store.save_draft(p)
    p["substituicoes"][0].update(
        motivo_id=2, motivo_texto="por motivo de licença especial {do_titular}"
    )
    with store.connection() as c:
        c.execute(
            "UPDATE portarias SET payload=? WHERE id=?", (json.dumps(p), identifier)
        )
    monkeypatch.setattr(persistence, "Store", lambda: original(path))
    enable_login(monkeypatch, store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_portarias").click().run()
    app.sidebar.radio(key="nav").set_value("Histórico").run()
    assert "da titular" in app.dataframe[0].value.iloc[0]["Motivo"]
    next(
        x for x in app.checkbox if x.label == "Confirmo a exclusão deste rascunho"
    ).check().run()
    next(x for x in app.button if x.label == "Excluir rascunho").click().run()
    assert not app.error and not app.exception
    assert not store.history() and store.next_number(2026) == 9


def test_member_editor_preserves_custom_seat(tmp_path, monkeypatch):
    import database.store as persistence

    original = persistence.Store
    path = tmp_path / "custom_seat.db"
    store = original(path)
    person = store.catalog("procuradores")[5]
    person["assento"] = "Câmara Especial"
    store.save_member(person)
    monkeypatch.setattr(persistence, "Store", lambda: original(path))
    enable_login(monkeypatch, store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_portarias").click().run()
    app.sidebar.radio(key="nav").set_value("Procuradores").run()
    next(x for x in app.selectbox if x.label == "Editar cadastro").set_value(6).run()
    next(x for x in app.button if x.label == "Salvar procurador").click().run()
    assert not app.error
    assert store.catalog("procuradores")[5]["assento"] == "Câmara Especial"


def test_history_displays_feminine_role_and_draft(tmp_path, monkeypatch):
    import database.store as persistence
    from tests.cases import sample

    original = persistence.Store
    path = tmp_path / "history.db"
    store = original(path)
    store.save_draft(sample(store))
    monkeypatch.setattr(persistence, "Store", lambda: original(path))
    enable_login(monkeypatch, store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_portarias").click().run()
    app.sidebar.radio(key="nav").set_value("Histórico").run()
    assert not app.error
    row = app.dataframe[0].value.iloc[0]
    assert row["Função"] == "Subprocuradora-Geral"
    assert row["Número"] == "Rascunho"


def test_all_screens(tmp_path, monkeypatch):
    path = tmp_path / "ui.db"
    import database.store as persistence

    monkeypatch.setattr(persistence, "DB_PATH", path)
    # Store default arguments are captured at definition; isolate using a factory.
    original = persistence.Store
    store = original(path)
    monkeypatch.setattr(persistence, "Store", lambda: original(path))
    enable_login(monkeypatch, store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_portarias").click().run()
    assert not app.exception
    assert not app.error
    assert any(
        x.label == "Procurador substituto" and x.value is None for x in app.selectbox
    )
    for screen in ["Histórico", "Procuradores", "Configurações", "Nova Portaria"]:
        app.sidebar.radio(key="nav").set_value(screen).run()
        assert not app.exception, screen
        assert not app.error, screen


def test_ui_finalize(tmp_path, monkeypatch):
    import database.store as persistence

    path = tmp_path / "flow.db"
    original = persistence.Store
    s = original(path)
    s.set_sequence(2026, 8, True)
    s.configure(export_dir=str(tmp_path / "exports"))
    monkeypatch.setattr(persistence, "Store", lambda: original(path))
    enable_login(monkeypatch, s)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_portarias").click().run()

    def select(label, value):
        next(x for x in app.selectbox if x.label == label).set_value(value).run()

    select("Procurador titular", 2)
    select("Procurador substituto", 7)
    next(x for x in app.button if x.label == "Preparar prévia").click().run()
    assert not app.error
    assert len(s.history()) == 1
    assert s.next_number(2026) == 9
    next(x for x in app.button if x.label == "FINALIZAR PORTARIA").click().run()
    assert not app.exception
    assert not app.error
    assert s.history()[0]["status"] == "Finalizada"
    assert s.next_number(2026) == 10
    app.sidebar.radio(key="nav").set_value("Histórico").run()
    assert not app.exception
    assert not app.error
