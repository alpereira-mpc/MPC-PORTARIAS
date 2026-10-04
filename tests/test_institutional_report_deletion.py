"""Deletion of editable institutional versions and reuse of the version number."""

import os

import pytest
from streamlit.testing.v1 import AppTest

from database.audit import AuditStore
from database.institutional_reports import InstitutionalReportsStore
from database.store import Store
from services.access import Principal
from services.institutional_report_content import InstitutionalReportContentService
from services.institutional_reports import (
    EMPTY_STRUCTURED_CONTENT,
    delete_editable_period_versions,
    delete_editable_report_version,
)


SNAPSHOT = {
    "metadados": {
        "tipo": "TRIMESTRAL",
        "ano": 2026,
        "trimestre": 3,
        "periodo_parcial": False,
        "descricao_periodo": "3º trimestre de 2026",
        "data_inicio": "2026-07-01",
        "data_fim": "2026-10-01",
    },
    "indicadores_gerais": {
        "distributed": 545,
        "production": 594,
        "opinions": 424,
        "quotas": 170,
        "production_rate": 109.0,
        "median_days": 7.8,
    },
    "cobertura_historica": {
        "meses_disponiveis": [7, 8, 9],
        "meses_ausentes": [],
        "lacunas_no_ano": [],
    },
    "nota_metodologica": {"producao": "Pareceres e Cotas."},
}


def _admin():
    return Principal(
        1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
    )


def _reader():
    return Principal(
        2, "Leitor", "leitor@test", "CONSULTA", True, False, False, False, False, ()
    )


def _repository(tmp_path):
    return InstitutionalReportsStore(Store(tmp_path / "delete.db"))


def _open(repository, *, status="RASCUNHO"):
    report = repository.create(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio="2026-07-01",
        data_fim="2026-10-01",
        periodo_parcial=False,
        descricao_periodo="3º trimestre de 2026",
        snapshot_dados=SNAPSHOT,
        conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
        actor="admin@test",
    )
    if status == "RASCUNHO":
        return report
    report = repository.save_content(
        report["id"],
        {"resumo_executivo": {"texto": "Texto em revisão."}},
        "admin@test",
        status="EM_REVISAO",
    )
    if status == "EM_REVISAO":
        return report
    report = repository.finalize(report["id"], "admin@test")
    if status == "ENVIADO":
        report = repository.mark_distributed(report["id"], "admin@test")
    return report


def _next(repository, source):
    return repository.create_new_version(
        tipo="TRIMESTRAL",
        ano=2026,
        trimestre=3,
        data_inicio="2026-07-01",
        data_fim="2026-10-01",
        periodo_parcial=False,
        descricao_periodo="3º trimestre de 2026",
        snapshot_dados=source,
        conteudo_estruturado=EMPTY_STRUCTURED_CONTENT,
        actor="admin@test",
    )


def test_editing_does_not_create_another_version(tmp_path):
    repository = _repository(tmp_path)
    report = _open(repository)
    saved = InstitutionalReportContentService(repository.store).save_manual(
        report["id"], {"resumo_executivo": "Texto salvo na mesma versão."}, _admin()
    )
    assert saved["versao"] == 1
    assert len(repository.list_for_period("TRIMESTRAL", 2026, 3)) == 1


def test_latest_draft_and_review_can_be_deleted(tmp_path):
    repository = _repository(tmp_path)
    draft = _open(repository)
    removed = delete_editable_report_version(repository.store, draft["id"], _admin())
    assert removed["versao"] == 1 and removed["status"] == "RASCUNHO"
    assert repository.get(draft["id"]) is None
    review = _open(repository, status="EM_REVISAO")
    delete_editable_report_version(repository.store, review["id"], _admin())
    assert repository.list_for_period("TRIMESTRAL", 2026, 3) == []


def test_finalized_and_sent_versions_cannot_be_deleted(tmp_path):
    repository = _repository(tmp_path)
    finalized = _open(repository, status="FINALIZADO")
    with pytest.raises(ValueError, match="não podem ser excluídas"):
        delete_editable_report_version(repository.store, finalized["id"], _admin())
    sent = repository.mark_distributed(finalized["id"], "admin@test")
    with pytest.raises(ValueError, match="não podem ser excluídas"):
        delete_editable_report_version(repository.store, sent["id"], _admin())
    assert repository.get(finalized["id"])["status"] == "ENVIADO"


def test_only_the_latest_version_can_be_deleted(tmp_path):
    repository = _repository(tmp_path)
    first = _open(repository, status="FINALIZADO")
    second = _next(repository, SNAPSHOT)
    with repository.store.connection() as connection:
        connection.execute(
            "UPDATE relatorios_institucionais SET status='RASCUNHO' WHERE id=?",
            (first["id"],),
        )
    with pytest.raises(ValueError, match="mais recente"):
        delete_editable_report_version(repository.store, first["id"], _admin())
    assert repository.get(second["id"])["versao"] == 2
    delete_editable_report_version(repository.store, second["id"], _admin())
    assert [
        item["versao"] for item in repository.list_for_period("TRIMESTRAL", 2026, 3)
    ] == [1]


def test_distribution_and_official_pdf_block_deletion(tmp_path):
    repository = _repository(tmp_path)
    report = _open(repository)
    repository.begin_distribution(
        {
            "relatorio_id": report["id"],
            "origem_envio_id": None,
            "tipo": "TRIMESTRAL",
            "ano": 2026,
            "trimestre": 3,
            "versao": 1,
            "remetente": "admin@test",
            "assunto": "Assunto",
            "corpo": "Corpo",
            "pdf_nome": "previa.pdf",
            "pdf_sha256": "abc",
            "pdf_tamanho": 4,
            "criado_por": "admin@test",
            "chave_idempotencia": "bloqueio-distribuicao",
        },
        [],
    )
    with pytest.raises(ValueError, match="distribuição"):
        delete_editable_report_version(repository.store, report["id"], _admin())
    assert repository.get(report["id"])["status"] == "RASCUNHO"

    store = Store(tmp_path / "pdf.db")
    clean = InstitutionalReportsStore(store)
    inconsistent = _open(clean)
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO relatorios_institucionais_pdf("
            "relatorio_id, nome_arquivo, conteudo, sha256, tamanho, gerado_por, gerado_em) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                inconsistent["id"],
                "oficial.pdf",
                b"%PDF-1.4",
                "abc",
                8,
                "admin@test",
                "2026-10-04T00:00:00",
            ),
        )
    with pytest.raises(ValueError, match="PDF oficial"):
        delete_editable_report_version(store, inconsistent["id"], _admin())
    assert clean.get(inconsistent["id"]) is not None


def test_deletion_keeps_other_versions_and_records_audit(tmp_path):
    repository = _repository(tmp_path)
    first = _open(repository, status="FINALIZADO")
    repository.save_pdf_artifact(
        first["id"],
        nome_arquivo="oficial.pdf",
        conteudo=b"%PDF-1.4 oficial",
        sha256="hash-v1",
        tamanho=16,
        actor="admin@test",
    )
    frozen = repository.get(first["id"])["snapshot_dados"]
    second = _next(repository, SNAPSHOT)
    removed = delete_editable_report_version(repository.store, second["id"], _admin())
    assert repository.get(first["id"])["snapshot_dados"] == frozen
    assert repository.pdf_artifact(first["id"])["sha256"] == "hash-v1"
    events = AuditStore(repository.store).list_events(
        {"eventos": ["RELATORIO_INSTITUCIONAL_VERSAO_EXCLUIDA"]}
    )
    assert len(events) == 1
    details = events[0]["detalhes_json"]
    assert str(removed["versao"]) in details
    assert "EM_REVISAO" not in details
    assert "RASCUNHO" in details
    assert "snapshot" not in details
    with pytest.raises(PermissionError):
        delete_editable_report_version(repository.store, first["id"], _reader())


def test_deleted_period_restarts_at_version_one_and_gap_is_reused(tmp_path):
    repository = _repository(tmp_path)
    only = _open(repository)
    delete_editable_report_version(repository.store, only["id"], _admin())
    recreated = _open(repository)
    assert recreated["versao"] == 1
    repository.finalize(recreated["id"], "admin@test")
    second = _next(repository, SNAPSHOT)
    delete_editable_report_version(repository.store, second["id"], _admin())
    again = _next(repository, SNAPSHOT)
    assert again["versao"] == 2
    assert [
        item["versao"] for item in repository.list_for_period("TRIMESTRAL", 2026, 3)
    ] == [
        2,
        1,
    ]


def test_bulk_deletion_stops_at_the_frozen_version(tmp_path):
    repository = _repository(tmp_path)
    first = _open(repository, status="FINALIZADO")
    second = _next(repository, SNAPSHOT)
    repository.finalize(second["id"], "admin@test")
    third = _next(repository, SNAPSHOT)
    repository.finalize(third["id"], "admin@test")
    fourth = _next(repository, SNAPSHOT)
    with repository.store.connection() as connection:
        connection.execute(
            "UPDATE relatorios_institucionais SET status='EM_REVISAO' WHERE id=?",
            (third["id"],),
        )
    removed = delete_editable_period_versions(
        repository.store, "TRIMESTRAL", 2026, 3, _admin()
    )
    assert [item["versao"] for item in removed] == [4, 3]
    remaining = repository.list_for_period("TRIMESTRAL", 2026, 3)
    assert [item["versao"] for item in remaining] == [2, 1]
    assert remaining[0]["id"] == second["id"]
    assert repository.get(first["id"])["status"] == "FINALIZADO"
    with pytest.raises(ValueError, match="não podem ser excluídas"):
        delete_editable_report_version(repository.store, first["id"], _admin())


def test_deletion_screen_confirms_before_removing_and_selects_the_rest(
    tmp_path, monkeypatch
):
    repository = _repository(tmp_path)
    first = _open(repository, status="FINALIZADO")
    second = _next(repository, SNAPSHOT)
    monkeypatch.setenv("MPC_DEL_DB", str(tmp_path / "delete.db"))
    monkeypatch.setenv("MPC_DEL_ID", str(second["id"]))

    def page():
        import os

        import streamlit as st
        from database.institutional_reports import InstitutionalReportsStore
        from database.store import Store
        from services.access import Principal
        import services.relatorios_ui as ui

        local = Store(os.environ["MPC_DEL_DB"])
        reports = InstitutionalReportsStore(local)
        admin = Principal(
            1, "Admin", "admin@test", "ADMINISTRADOR", True, True, True, True, True, ()
        )
        ui._apply_forgotten_reports()
        versions = reports.list_for_period("TRIMESTRAL", 2026, 3)
        if not versions:
            if st.session_state.pop("inst_prefer_latest", False):
                st.session_state.pop("inst_versao", None)
            st.caption("Nenhum relatório criado para este período.")
            return
        labels = [ui._version_label(item) for item in versions]
        if st.session_state.pop("inst_prefer_latest", False):
            st.session_state.pop("inst_versao", None)
        ui._drop_invalid_widget("inst_versao", labels)
        selected = st.selectbox("Versão", labels, index=0, key="inst_versao")
        report = versions[labels.index(selected)]
        ui._render_delete_buttons(report, versions, placement="header")
        ui._render_delete_confirmation(local, admin, versions)

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception, app.exception
    text_key = f"inst_text_{second['id']}_resumo_executivo"
    app.session_state[text_key] = "texto preso da versão excluída"
    app.button(key=f"inst_delete_ask_header_{second['id']}").click().run()
    assert not app.exception, app.exception
    assert repository.get(second["id"]) is not None
    assert "excluída permanentemente" in "\n".join(item.value for item in app.warning)
    app.button(key=f"inst_delete_confirm_{second['id']}").click().run()
    assert not app.exception, app.exception
    assert repository.get(second["id"]) is None
    assert repository.get(first["id"])["status"] == "FINALIZADO"
    assert text_key not in app.session_state
    assert app.selectbox(key="inst_versao").value == "1 - Finalizado"
    app.run()
    labels = [button.label for button in app.button]
    assert not any(label.startswith("Excluir") for label in labels), labels
