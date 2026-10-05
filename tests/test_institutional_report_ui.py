"""Interface, snapshot rendering and flows for institutional reports."""

import inspect
import os
from pathlib import Path
from streamlit.testing.v1 import AppTest

from database.store import Store
from services.access import Principal
from services.institutional_reports import build_report_snapshot
from services.relatorios_ui import (
    _comparison_rows,
    _institutional_period_label,
    _period_token,
    _safe_monthly_rows,
    annual,
    current_view,
    production,
    quarterly,
    render,
)
from services.tramita_reports import import_reference_reports
import services.institutional_report_content as content_service
import services.relatorios_ui as relatorios_ui


def _admin():
    return Principal(
        1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
    )


def _keep_orphan_widgets(app):
    """AppTest keeps widgets from the fragment before st.rerun().

    Streamlit already dropped those values. A neutral value lets the next
    interaction serialize the tree.
    """
    for box in app.checkbox:
        if box.key and box.key not in app.session_state:
            app.session_state[box.key] = False


def _month(month):
    return {
        "month": month,
        "summary": {
            "distributed": 10 + month,
            "production": 12 + month,
            "opinions": 8,
            "quotas": 4,
            "production_rate": 110.0,
            "median_days": 7.8,
        },
    }


def _snapshot(tipo="ANUAL", quarter=None, months=None, prior=None):
    months = list(range(1, 10)) if months is None else months
    return {
        "metadados": {
            "tipo": tipo,
            "ano": 2026,
            "trimestre": quarter,
            "data_inicio": "2026-01-01",
            "data_fim": "2027-01-01",
            "periodo_parcial": tipo == "ANUAL",
        },
        "cobertura_historica": {
            "meses_disponiveis": months,
            "meses_ausentes": [],
            "lacunas_no_ano": [],
        },
        "indicadores_gerais": {
            "distributed": 1760,
            "production": 1849,
            "opinions": 1320,
            "quotas": 529,
            "production_rate": 105.1,
            "median_days": 11.0,
        },
        "serie_mensal": [_month(month) for month in months],
        "por_procurador": [
            {
                "procurador": "Procurador A",
                "distributed": 10,
                "production": 12,
                "opinions": 9,
                "quotas": 3,
                "production_rate": 120.0,
                "median_days": 8.0,
            }
        ],
        "composicao_producao": {
            "pareceres_percentual": 71.4,
            "cotas_percentual": 28.6,
        },
        "faixas_permanencia": [{"faixa": "0–7 dias", "quantidade": 100}],
        "comparacao_periodo_anterior": prior,
        "nota_metodologica": {"producao": "Produção considera Parecer e Cota."},
    }


def _report(snapshot, **overrides):
    report = {
        "id": 4,
        "versao": 1,
        "status": "RASCUNHO",
        "data_corte": "2026-10-03T11:00:00",
        "criado_em": "2026-10-03T11:00:00",
        "criado_por": "admin@test",
        "snapshot_dados": snapshot,
        "conteudo_estruturado": {
            "resumo_executivo": {"texto": "Resumo congelado do relatório."},
            "sintese_pontos_atencao": {"texto": "- Ponto de atenção congelado."},
        },
    }
    report.update(overrides)
    return report


def test_navigation_puts_institutional_reports_after_the_analytical_panels():
    source = inspect.getsource(render)
    order = (
        '"Visão Atual"',
        '"Produção Mensal"',
        '"Avaliação Trimestral"',
        '"Avaliação Anual"',
        '"Relatórios Institucionais"',
        '"Importações"',
    )
    positions = [source.index(item) for item in order]
    assert positions == sorted(positions)
    assert "index=0" in source
    for renderer in (quarterly, annual, production, current_view):
        body = inspect.getsource(renderer)
        assert "Relatório do período" not in body
        assert "institutional_reports" not in body
        assert "_render_institutional" not in body


def test_period_labels_and_series_come_from_the_snapshot_only():
    annual_snapshot = _snapshot()
    assert _institutional_period_label(annual_snapshot) == (
        "Acumulado de janeiro a setembro de 2026"
    )
    assert "None" not in _institutional_period_label(annual_snapshot)
    assert [row["Mês"] for row in _safe_monthly_rows(annual_snapshot)] == [
        "Janeiro",
        "Fevereiro",
        "Março",
        "Abril",
        "Maio",
        "Junho",
        "Julho",
        "Agosto",
        "Setembro",
    ]
    quarterly_snapshot = _snapshot("TRIMESTRAL", 3, [7, 8, 9])
    assert _institutional_period_label(quarterly_snapshot) == "3º trimestre de 2026"
    assert [row["Mês"] for row in _safe_monthly_rows(quarterly_snapshot)] == [
        "Julho",
        "Agosto",
        "Setembro",
    ]
    assert _comparison_rows(annual_snapshot) is None
    assert _period_token("ANUAL", 2026, None) == "ANUAL_2026_0"
    compared = _snapshot(
        "TRIMESTRAL",
        3,
        [7, 8, 9],
        {
            "distributed": 400,
            "production": 410,
            "opinions": 300,
            "quotas": 110,
            "median_days": 9.0,
        },
    )
    rows = _comparison_rows(compared)
    assert rows[0]["Período"] == "2º trimestre"
    assert rows[0]["Distribuídos"] == 400
    assert rows[1]["Distribuídos"] == 1760
    document = inspect.getsource(relatorios_ui._render_institutional_document)
    assert "period_data" not in document
    assert "monthly_reports" not in document
    assert "TramitaReportsStore" not in document


def _visible(app):
    parts = []
    for collection in (
        app.markdown,
        app.caption,
        app.subheader,
        app.warning,
        app.error,
        app.success,
        app.info,
    ):
        parts.extend(item.value for item in collection)
    parts.extend(f"{item.label} {item.value}" for item in app.metric)
    return "\n".join(parts)


def test_document_renders_frozen_annual_partial_without_widgets(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "database.tramita_reports.TramitaReportsStore.period_data",
        lambda *args, **kwargs: calls.append("period")
        or (_ for _ in ()).throw(AssertionError("period_data")),
    )

    def page():
        import tests.test_institutional_report_ui as fixture

        fixture.relatorios_ui._render_institutional_document(
            fixture._report(fixture._snapshot())
        )

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception, app.exception
    visible = _visible(app)
    assert "RELATÓRIO ANUAL DE PRODUÇÃO" in visible
    assert "Acumulado de janeiro a setembro de 2026" in visible
    assert "Resumo congelado do relatório." in visible
    assert "Resumo executivo ainda não elaborado." not in visible
    assert "Evolução do período ainda não elaborada." in visible
    assert "Não há período anual anterior disponível para comparação." in visible
    assert "Ponto de atenção congelado." in visible
    assert "1.760" in visible
    assert "11,0" in visible
    assert "Pareceres" in visible
    assert "opiniões" not in visible.lower()
    assert "opinions" not in visible.lower()
    assert "71,4%" in visible
    assert not app.text_area
    assert not calls
    charts = [
        element
        for element in app
        if "vega" in element.type or element.type.endswith("chart")
    ]
    assert len(charts) >= 4, sorted({element.type for element in app})


def test_partial_and_empty_content_do_not_break_the_document():
    def page():
        import tests.test_institutional_report_ui as fixture

        fixture.relatorios_ui._render_institutional_document(
            fixture._report(
                {
                    "metadados": {
                        "tipo": "ANUAL",
                        "ano": 2026,
                        "trimestre": None,
                        "periodo_parcial": True,
                    },
                    "indicadores_gerais": {},
                    "comparacao_periodo_anterior": None,
                },
                conteudo_estruturado={},
                status="EM_REVISAO",
            )
        )

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception, app.exception
    visible = _visible(app)
    assert "Os indicadores deste snapshot estão incompletos." in visible
    assert "Resumo executivo ainda não elaborado." in visible
    assert "Não há período anual anterior disponível para comparação." in visible
    assert "Nota metodológica ainda não registrada." in visible


def test_finalized_and_sent_editors_are_read_only():
    def page():
        import tests.test_institutional_report_ui as fixture
        from services.access import Principal

        admin = Principal(
            1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
        )
        for status, identifier in (("FINALIZADO", 8), ("ENVIADO", 9)):
            fixture.relatorios_ui._render_institutional_editor(
                None,
                admin,
                fixture._report(fixture._snapshot(), id=identifier, status=status),
                is_latest=True,
                tipo="ANUAL",
                year=2026,
                quarter=None,
            )

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception, app.exception
    labels = [button.label for button in app.button]
    assert labels.count("Criar nova versão para editar") == 2
    assert "Salvar alterações" not in labels
    assert relatorios_ui.AI_GENERATE_LABEL not in labels
    assert relatorios_ui.AI_REGENERATE_LABEL not in labels
    assert not app.text_area


def _flow_page():
    import os

    from database.store import Store
    from services.access import Principal
    import services.relatorios_ui as ui

    local = Store(os.environ["MPC_INST_FLOW_DB"])
    admin = Principal(
        1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
    )
    ui.institutional_reports(local, admin)


def _quarterly_panel():
    import os

    from database.store import Store
    from services.access import Principal
    import services.relatorios_ui as ui

    local = Store(os.environ["MPC_INST_FLOW_DB"])
    admin = Principal(
        1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
    )
    ui.quarterly(local, admin)


def _annual_panel():
    import os

    from database.store import Store
    from services.access import Principal
    import services.relatorios_ui as ui

    local = Store(os.environ["MPC_INST_FLOW_DB"])
    admin = Principal(
        1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
    )
    ui.annual(local, admin)


def test_quarterly_and_annual_institutional_flows(tmp_path, monkeypatch):
    database = tmp_path / "institutional-flow.db"
    store = Store(database)
    store.configure(export_dir=str(tmp_path / "exports"))
    import_reference_reports(
        store, Path(__file__).resolve().parents[1] / "referencias", "admin@test"
    )
    quarterly_snapshot = build_report_snapshot(
        store, tipo="TRIMESTRAL", ano=2026, trimestre=3
    )
    metadata = quarterly_snapshot["metadados"]
    from database.institutional_reports import InstitutionalReportsStore
    from services.institutional_reports import EMPTY_STRUCTURED_CONTENT

    InstitutionalReportsStore(store).create(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio=metadata["data_inicio"],
        data_fim=metadata["data_fim"],
        periodo_parcial=metadata["periodo_parcial"],
        descricao_periodo=metadata["descricao_periodo"],
        snapshot_dados=quarterly_snapshot,
        conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
        actor="admin@test",
    )
    monkeypatch.setenv("MPC_INST_FLOW_DB", str(database))
    builds = {"n": 0}
    real_build = relatorios_ui.build_report_snapshot

    def counting_build(*args, **kwargs):
        builds["n"] += 1
        return real_build(*args, **kwargs)

    monkeypatch.setattr(relatorios_ui, "build_report_snapshot", counting_build)

    def fake_ai(_context, secao=None):
        if isinstance(secao, str):
            sections = (secao,)
        elif secao:
            sections = tuple(secao)
        else:
            sections = content_service.AI_SECTIONS
        return {
            key: f"Texto institucional de {key}." for key in sections
        }, "modelo-teste"

    monkeypatch.setattr(
        content_service.ai_service,
        "gerar_conteudo_relatorio_institucional",
        fake_ai,
    )

    app = AppTest.from_function(_flow_page, default_timeout=120).run()
    assert not app.exception, app.exception
    visible = _visible(app)
    assert app.radio(key="inst_home_section").value == "Relatórios criados"
    assert "3º trimestre de 2026" in visible
    assert any(button.key == "inst_open_created_1" for button in app.button)
    app.button(key="inst_open_created_1").click().run()
    assert not app.exception, app.exception
    visible = _visible(app)
    assert "Relatório Trimestral de Produção" in visible
    assert "3º trimestre de 2026" in visible
    assert "545" in visible
    assert builds["n"] == 0
    assert not app.text_area
    assert "Relatório do período" not in visible
    assert all("None" not in (button.key or "") for button in app.button)

    app.radio(key="inst_exibicao").set_value("Conteúdo e revisão").run()
    assert not app.exception, app.exception
    app.button(key="inst_ai_all_1").click().run()
    assert not app.exception, app.exception
    assert "Texto institucional de resumo_executivo." in [
        area.value for area in app.text_area
    ], _visible(app)
    next(
        area
        for area in app.text_area
        if area.key.startswith("inst_text_1_") and "resumo_executivo" in area.key
    ).set_value("Texto manual do trimestre.").run()
    app.button(key="inst_save_1").click().run()
    assert not app.exception, app.exception
    assert next(
        area
        for area in app.text_area
        if area.key.startswith("inst_text_1_") and "resumo_executivo" in area.key
    ).value == "Texto manual do trimestre."
    app.button(key="inst_regen_1_resumo_executivo").click().run()
    assert not app.exception, app.exception
    assert "Salve as alterações" not in _visible(app)
    assert next(
        area
        for area in app.text_area
        if area.key.startswith("inst_text_1_") and "resumo_executivo" in area.key
    ).value.startswith("Texto institucional de resumo_executivo.")
    app.checkbox(key="inst_confirm_1").set_value(True).run()
    attention = [
        box
        for box in app.checkbox
        if (box.key or "").startswith("institutional_governance_attention_")
    ]
    if attention:
        attention[0].set_value(True).run()
    app.button(key="inst_finalize_1").click().run()
    assert not app.exception, app.exception
    assert "Finalizado" in _visible(app)
    assert not app.text_area
    app.button(key="inst_new_1").click().run()
    assert not app.exception, app.exception
    assert "Versão 2" in _visible(app)
    assert builds["n"] == 1

    app.button(key="inst_back_to_created").click().run()
    app.radio(key="inst_home_section").set_value("Criar novo").run()
    creation_keys = {button.key for button in app.button}
    assert "inst_create_TRIMESTRAL_2026_1" in creation_keys
    assert "inst_create_TRIMESTRAL_2026_2" in creation_keys
    assert "inst_create_TRIMESTRAL_2026_3" not in creation_keys
    app.radio(key="inst_tipo").set_value("Relatório Anual").run()
    assert not app.exception, app.exception
    assert "Períodos disponíveis" in _visible(app)
    assert not any(radio.key == "inst_trimestre" for radio in app.radio)
    app.button(key="inst_create_ANUAL_2026_0").click().run()
    assert not app.exception, app.exception
    app.radio(key="inst_exibicao").set_value("Visualização").run()
    assert not app.exception, app.exception
    visible = _visible(app)
    assert "Relatório Anual de Produção" in visible
    assert "Acumulado de janeiro a setembro de 2026" in visible
    assert "1.760" in visible
    assert "1.849" in visible
    assert "1.320" in visible
    assert "529" in visible
    assert "11,0" in visible
    assert "Não há período anual anterior disponível para comparação." in visible
    assert builds["n"] == 2

    app.radio(key="inst_exibicao").set_value("Conteúdo e revisão").run()
    generate = next(
        button for button in app.button if button.key.startswith("inst_ai_all_")
    )
    generate.click().run()
    assert not app.exception, app.exception
    summary = next(
        area
        for area in app.text_area
        if area.key.startswith("inst_text_") and "resumo_executivo" in area.key
    )
    summary.set_value("Texto manual do anual.").run()
    save = next(button for button in app.button if button.key.startswith("inst_save_"))
    save.click().run()
    regen = next(
        button
        for button in app.button
        if button.key.startswith("inst_regen_") and "permanencia" in button.key
    )
    regen.click().run()
    assert "Salve as alterações" not in _visible(app)
    confirm = next(
        box for box in app.checkbox if (box.key or "").startswith("inst_confirm_")
    )
    confirm.set_value(True).run()
    attention = [
        box
        for box in app.checkbox
        if (box.key or "").startswith("institutional_governance_attention_")
    ]
    if attention:
        attention[0].set_value(True).run()
    finish = next(
        button for button in app.button if button.label == "Finalizar relatório"
    )
    finish.click().run()
    assert not app.exception, app.exception
    assert "Finalizado" in _visible(app)
    assert not app.text_area
    new_version = next(
        button
        for button in app.button
        if button.label == "Criar nova versão para editar"
    )
    _keep_orphan_widgets(app)
    new_version.click().run()
    assert not app.exception, app.exception
    assert "Rascunho" in _visible(app)
    assert builds["n"] == 3

    app.radio(key="inst_exibicao").set_value("Versões").run()
    assert not app.exception, app.exception
    versions = list(app.selectbox(key="inst_version_history").options)
    assert "2 - Rascunho" in versions
    assert "1 - Finalizado" in versions
    app.radio(key="inst_exibicao").set_value("Visualização").run()
    assert not app.text_area
    assert not app.exception, app.exception

    quarterly_panel = AppTest.from_function(_quarterly_panel, default_timeout=120).run()
    assert not quarterly_panel.exception, quarterly_panel.exception
    quarterly_visible = _visible(quarterly_panel)
    assert "Relatório do período" not in quarterly_visible
    assert "Evolução da produção" in quarterly_visible
    assert not any(
        button.label == "Abrir relatório" for button in quarterly_panel.button
    )

    annual_panel = AppTest.from_function(_annual_panel, default_timeout=120).run()
    assert not annual_panel.exception, annual_panel.exception
    annual_visible = _visible(annual_panel)
    assert "Relatório do período" not in annual_visible
    assert "Evolução acumulada no ano" in annual_visible
    assert "1760" in annual_visible
    assert not any(button.label == "Abrir relatório" for button in annual_panel.button)


def _annual_reference(tmp_path, name):
    database = tmp_path / name
    store = Store(database)
    store.configure(export_dir=str(tmp_path / "exports"))
    import_reference_reports(
        store, Path(__file__).resolve().parents[1] / "referencias", "admin@test"
    )
    snapshot = build_report_snapshot(store, tipo="ANUAL", ano=2026, trimestre=None)
    metadata = snapshot["metadados"]
    from database.institutional_reports import InstitutionalReportsStore
    from services.institutional_reports import EMPTY_STRUCTURED_CONTENT

    repository = InstitutionalReportsStore(store)
    created = repository.create(
        tipo="ANUAL",
        ano=2026,
        trimestre=None,
        data_inicio=metadata["data_inicio"],
        data_fim=metadata["data_fim"],
        periodo_parcial=metadata["periodo_parcial"],
        descricao_periodo=metadata["descricao_periodo"],
        snapshot_dados=snapshot,
        conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
        actor="admin@test",
    )
    return database, repository, repository.finalize(created["id"], "admin@test")


def test_existing_pdf_is_direct_download_in_the_created_reports_list(tmp_path, monkeypatch):
    database, repository, report = _annual_reference(tmp_path, "pdf-list.db")
    payload = b"%PDF-1.4\nfixture\n"
    import hashlib

    repository.save_pdf_artifact(
        report["id"],
        nome_arquivo="Relatorio_Anual_MPCPB_2026_v1.pdf",
        conteudo=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
        tamanho=len(payload),
        actor="admin@test",
    )
    monkeypatch.setenv("MPC_PDF_LIST_DB", str(database))

    def page():
        import os

        from database.store import Store
        from services.access import Principal
        import services.relatorios_ui as ui

        ui.institutional_reports(
            Store(os.environ["MPC_PDF_LIST_DB"]),
            Principal(
                1,
                "Admin",
                "admin@test",
                "ADMINISTRADOR",
                True,
                True,
                True,
                True,
                True,
                (),
            ),
        )

    app = AppTest.from_function(page, default_timeout=120).run()
    assert not app.exception, app.exception
    assert any(button.label == "Baixar PDF" for button in app.get("download_button"))
    assert not any(button.label == "Baixar PDF" for button in app.button)
    assert "inst_pdf_export" not in app.session_state


def test_finalized_empty_annual_opens_the_new_draft(tmp_path, monkeypatch):
    database, repository, first = _annual_reference(tmp_path, "continue.db")
    from services.institutional_report_validation import snapshot_hash

    frozen_hash = snapshot_hash(first["snapshot_dados"])
    monkeypatch.setenv("MPC_CONTINUE_DB", str(database))

    def page():
        import os

        from database.store import Store
        from services.access import Principal
        import services.relatorios_ui as ui

        local = Store(os.environ["MPC_CONTINUE_DB"])
        admin = Principal(
            1,
            "Admin",
            "admin@test",
            "ADMINISTRADOR",
            True,
            True,
            True,
            True,
            True,
            (),
        )
        ui.institutional_reports(local, admin)

    app = AppTest.from_function(page, default_timeout=120).run()
    assert not app.exception, app.exception
    app.button(key=f"inst_open_created_{first['id']}").click().run()
    app.radio(key="inst_exibicao").set_value("Conteúdo e revisão").run()
    assert not app.exception, app.exception
    visible = _visible(app)
    labels = [button.label for button in app.button]
    assert relatorios_ui.AI_GENERATE_LABEL not in labels
    assert relatorios_ui.AI_REGENERATE_LABEL not in labels
    assert "Criar nova versão para editar" in labels
    assert "Versão encerrada, disponível somente para consulta." in visible
    assert "Seção não preenchida nesta versão." in visible
    assert not app.text_area
    app.button(key=f"inst_new_{first['id']}").click().run()
    assert not app.exception, app.exception
    versions = repository.list_for_period("ANUAL", 2026, None)
    assert [item["versao"] for item in versions] == [2, 1]
    assert versions[0]["status"] == "RASCUNHO"
    kept = repository.get(first["id"])
    assert kept["status"] == "FINALIZADO"
    assert snapshot_hash(kept["snapshot_dados"]) == frozen_hash
    assert app.radio(key="inst_exibicao").value == "Conteúdo e revisão"
    assert "Versão 2" in _visible(app)
    generate = app.button(key=f"inst_ai_all_{versions[0]['id']}")
    assert generate.label == relatorios_ui.AI_GENERATE_LABEL
    assert app.text_area


def test_closed_version_opens_the_existing_review(tmp_path, monkeypatch):
    database, repository, first = _annual_reference(tmp_path, "open-review.db")
    metadata = first["snapshot_dados"]["metadados"]
    second = repository.create_new_version(
        tipo="ANUAL",
        ano=2026,
        trimestre=None,
        data_inicio=metadata["data_inicio"],
        data_fim=metadata["data_fim"],
        periodo_parcial=metadata["periodo_parcial"],
        descricao_periodo=metadata["descricao_periodo"],
        snapshot_dados=first["snapshot_dados"],
        conteudo_estruturado={
            "resumo_executivo": {"texto": "Texto da versão em revisão."}
        },
        actor="admin@test",
    )
    repository.save_content(
        second["id"],
        second["conteudo_estruturado"],
        "admin@test",
        status="EM_REVISAO",
    )
    monkeypatch.setenv("MPC_OPEN_REVIEW_DB", str(database))

    def page():
        import os

        from database.store import Store
        from services.access import Principal
        import services.relatorios_ui as ui

        local = Store(os.environ["MPC_OPEN_REVIEW_DB"])
        admin = Principal(
            1,
            "Admin",
            "admin@test",
            "ADMINISTRADOR",
            True,
            True,
            True,
            True,
            True,
            (),
        )
        ui.institutional_reports(local, admin)

    app = AppTest.from_function(page, default_timeout=120).run()
    assert not app.exception, app.exception
    app.button(key=f"inst_open_created_{second['id']}").click().run()
    app.radio(key="inst_exibicao").set_value("Versões").run()
    app.selectbox(key="inst_version_history").set_value("1 - Finalizado").run()
    app.button(key=f"inst_open_version_{first['id']}").click().run()
    app.radio(key="inst_exibicao").set_value("Conteúdo e revisão").run()
    assert not app.exception, app.exception
    labels = [button.label for button in app.button]
    visible = _visible(app)
    assert "Criar nova versão para editar" not in labels
    assert "Abrir versão em elaboração" in labels
    assert "Já existe uma versão em elaboração." in visible
    assert relatorios_ui.AI_GENERATE_LABEL not in labels
    app.button(key=f"inst_open_editor_{first['id']}").click().run()
    assert not app.exception, app.exception
    assert [
        item["versao"] for item in repository.list_for_period("ANUAL", 2026, None)
    ] == [
        2,
        1,
    ]
    assert repository.get(first["id"])["status"] == "FINALIZADO"
    assert "Versão 2" in _visible(app)
    assert app.radio(key="inst_exibicao").value == "Conteúdo e revisão"
    generate = app.button(key=f"inst_ai_all_{second['id']}")
    assert generate.label
    assert app.text_area
