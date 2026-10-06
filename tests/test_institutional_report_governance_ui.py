"""Governance tab, comparison and suggestions in the institutional section."""

import hashlib
from pathlib import Path

from streamlit.testing.v1 import AppTest

from database.institutional_reports import InstitutionalReportsStore
from database.store import Store
from document_generator.institutional_report_pdf import institutional_pdf_filename
from services.institutional_reports import (
    EMPTY_STRUCTURED_CONTENT,
    build_report_snapshot,
)
from services.tramita_reports import import_reference_reports


def _visible(app):
    parts = []
    for collection in (
        app.markdown,
        app.caption,
        app.warning,
        app.error,
        app.info,
        app.success,
        app.text,
    ):
        parts.extend(item.value for item in collection)
    return "\n".join(parts)


def _keys(app):
    keys = []
    for collection in (
        app.button,
        app.radio,
        app.selectbox,
        app.checkbox,
        app.text_area,
        app.text_input,
    ):
        keys.extend(item.key for item in collection if item.key)
    return keys


def _admin_page():
    import os

    from database.store import Store
    from services.access import Principal
    import services.relatorios_ui as ui

    local = Store(os.environ["MPC_GOV_DB"])
    ui.institutional_reports(
        local,
        Principal(
            1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
        ),
    )


def _reader_page():
    import os

    from database.store import Store
    from services.access import Principal
    import services.relatorios_ui as ui

    local = Store(os.environ["MPC_GOV_DB"])
    ui.institutional_reports(
        local,
        Principal(
            2, "Leitor", "leitor@test", "CONSULTA", True, False, False, False, False, ()
        ),
    )


def test_governance_tab_comparison_suggestion_and_pdf_button(tmp_path, monkeypatch):
    database = tmp_path / "governance.db"
    store = Store(database)
    store.configure(export_dir=str(tmp_path / "exports"))
    import_reference_reports(
        store, Path(__file__).resolve().parents[1] / "referencias", "admin@test"
    )
    snapshot = build_report_snapshot(store, tipo="TRIMESTRAL", ano=2026, trimestre=3)
    meta = snapshot["metadados"]
    repository = InstitutionalReportsStore(store)
    first = repository.create(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio=meta["data_inicio"],
        data_fim=meta["data_fim"],
        periodo_parcial=meta["periodo_parcial"],
        descricao_periodo=meta["descricao_periodo"],
        snapshot_dados=snapshot,
        conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
        actor="admin@test",
    )
    repository.finalize(first["id"], "admin@test")
    content = b"%PDF-1.4 oficial"
    repository.save_pdf_artifact(
        first["id"],
        nome_arquivo=institutional_pdf_filename(first),
        conteudo=content,
        sha256=hashlib.sha256(content).hexdigest(),
        tamanho=len(content),
        actor="admin@test",
    )
    second = repository.create_new_version(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio=meta["data_inicio"],
        data_fim=meta["data_fim"],
        periodo_parcial=meta["periodo_parcial"],
        descricao_periodo=meta["descricao_periodo"],
        snapshot_dados=snapshot,
        conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
        actor="admin@test",
    )
    before = len(repository.list_for_period("TRIMESTRAL", 2026, 1))
    calls = {"n": 0}
    real = InstitutionalReportsStore.pdf_artifact

    def counting(self, identifier):
        calls["n"] += 1
        return real(self, identifier)

    monkeypatch.setattr(InstitutionalReportsStore, "pdf_artifact", counting)
    monkeypatch.setenv("MPC_GOV_DB", str(database))
    app = AppTest.from_function(_admin_page, default_timeout=120).run()
    assert not app.exception, app.exception
    app.button(key=f"inst_open_created_{second['id']}").click().run()
    assert not app.exception, app.exception
    options = list(app.radio(key="inst_exibicao").options)
    assert options == ["Visualização", "Conteúdo e revisão", "Distribuição"]
    visible = _visible(app)
    assert "Versão 2" in visible
    assert before == 0
    assert calls["n"] == 1
    assert any(item.label == "Histórico de versões" for item in app.expander)
    assert len(_keys(app)) == len(set(_keys(app)))

    app.selectbox(key="inst_version_history").set_value("1 - Finalizado").run()
    app.button(key=f"inst_open_version_{first['id']}").click().run()
    assert not app.exception, app.exception
    governed = _visible(app)
    assert "Versão 1" in governed
    assert len(_keys(app)) == len(set(_keys(app)))
    assert len(_keys(app)) == len(set(_keys(app)))

    app.button(key="inst_back_to_created").click().run()
    app.radio(key="inst_home_section").set_value("Criar novo").run()
    app.selectbox(key="inst_ano").set_value(2026).run()
    app.button(key="inst_create_TRIMESTRAL_2026_1").click().run()
    assert not app.exception, app.exception
    created = repository.list_for_period("TRIMESTRAL", 2026, 1)
    assert len(created) == 1
    assert created[0]["versao"] == 1
    assert second["id"] != created[0]["id"]

    reader = AppTest.from_function(_reader_page, default_timeout=120).run()
    assert not reader.exception, reader.exception
    reader.button(key=f"inst_open_created_{second['id']}").click().run()
    reader.selectbox(key="inst_version_history").set_value("1 - Finalizado").run()
    reader.button(key=f"inst_open_version_{first['id']}").click().run()
    assert "Versão 1" in _visible(reader)
    assert not any(
        (button.key or "").startswith("institutional_governance_create_")
        for button in reader.button
    )
    assert not any(
        (button.key or "").startswith("inst_finalize_") for button in reader.button
    )
    assert not any(
        (button.key or "").startswith("inst_create_") for button in reader.button
    )
    assert any(item.label == "Histórico de versões" for item in reader.expander)
