import json

import pytest
from streamlit.testing.v1 import AppTest

from database.agenda import AgendaStore
from database.audit import AuditStore, ensure_schema
from database.oficios import OficiosStore
from database.store import ROOT
from services.access import has_permission, resolve_principal
from services.audit import registrar_evento
from services.system_health import (
    ATTENTION,
    ERROR,
    OK,
    diagnose,
    inspect_audit,
    inspect_documents,
    inspect_schema,
    ping_database,
)
from tests.access_testing import TEST_IDENTITY, enable_login, seed_access
from tests.test_postgresql import pg_store, pg_url  # noqa: F401


def _full_schema(store):
    AgendaStore(store)
    OficiosStore(store)
    AuditStore(store)
    return store


def test_sqlite_engine_and_healthy_connection(store):
    _full_schema(store)
    report = diagnose(store)
    assert report["database"]["status"] == OK
    assert report["database"]["engine"] == "SQLite"
    assert report["database"]["backend"] == "sqlite"
    assert report["schema"]["status"] == OK
    assert (
        report["schema"]["tables_present"] == report["schema"]["tables_expected"]
    )
    assert "DATABASE_URL" not in json.dumps(report)


def test_connection_failure_is_error(store, monkeypatch):
    class Boom:
        def __enter__(self):
            raise RuntimeError("offline")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(store, "connection", lambda **kwargs: Boom())
    result = ping_database(store)
    assert result["status"] == ERROR
    assert "Não foi possível consultar o banco" in result["summary"]
    report = diagnose(store)
    assert report["status"] == ERROR
    assert report["database"]["status"] == ERROR


def test_missing_table_is_attention(store):
    _full_schema(store)
    with store.connection() as c:
        c.execute("DROP TABLE IF EXISTS auditoria_eventos")
    schema = inspect_schema(store)
    assert schema["status"] == ATTENTION
    assert "auditoria_eventos" in schema["missing_tables"]
    assert "Estrutura esperada não encontrada" in schema["summary"]
    audit = inspect_audit(store)
    assert audit["status"] == ATTENTION
    assert "auditoria_eventos" in audit["summary"]


def test_audit_without_events_is_attention(store):
    ensure_schema(store)
    audit = inspect_audit(store)
    assert audit["status"] == ATTENTION
    assert "Nenhum evento de auditoria registrado" in audit["summary"]


def test_audit_healthy_after_event(store):
    principal = resolve_principal(store, TEST_IDENTITY)
    registrar_evento(
        store,
        evento="SESSAO_INICIADA",
        acao="ACESSO",
        principal=principal,
        state={},
    )
    audit = inspect_audit(store)
    assert audit["status"] == OK
    assert audit["last_event"] == "SESSAO_INICIADA"


def test_pdf_converter_absent_and_present(monkeypatch):
    monkeypatch.setattr(
        "document_generator.pdf.libreoffice_path", lambda: None
    )
    monkeypatch.setattr("services.system_health.sys.platform", "linux")
    documents = inspect_documents()
    assert documents["pdf"]["status"] == ATTENTION
    monkeypatch.setattr(
        "document_generator.pdf.libreoffice_path", lambda: "/usr/bin/soffice"
    )
    documents = inspect_documents()
    assert documents["pdf"]["status"] == OK


def test_missing_git_is_not_an_error(store, monkeypatch):
    monkeypatch.setattr("services.system_health.git_build", lambda: None)
    report = diagnose(store)
    assert report["application"]["build"] == "não identificado"
    assert report["application"]["status"] == OK


def test_ordinary_user_cannot_open_system(store):
    from services.system_ui import render

    seed_access(
        store,
        email="comum.sistema@test.local",
        perfil="USUARIO",
        pode_admin=False,
        pode_agenda=True,
    )
    principal = resolve_principal(store, {"email": "comum.sistema@test.local"})
    assert not has_permission(principal, "admin")
    with pytest.raises(ValueError, match="módulo"):
        render(store, principal)


def test_administrator_opens_system_health(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_admin").click().run()
    assert not app.exception
    options = next(r for r in app.radio if r.key == "admin_secao").options
    assert options == ["Usuários", "Acessos e Auditoria", "Sistema"]
    app.radio(key="admin_secao").set_value("Sistema").run()
    assert not app.exception and not app.error
    headings = [str(h.value) for h in app.subheader]
    assert any("Sistema" in h or "Saúde" in h for h in headings)
    assert any(b.label == "Atualizar diagnóstico" for b in app.button)
    app.button(key="sistema_health_refresh").click().run()
    assert not app.exception


def test_admin_navigation_helpers_queue_without_writing_widgets(monkeypatch):
    from inspect import getsource

    from services import access_ui, system_ui

    reruns = []
    state = {}
    monkeypatch.setattr(access_ui.st, "session_state", state)
    monkeypatch.setattr(access_ui.st, "rerun", lambda: reruns.append(True))
    access_ui.queue_admin_navigation(
        secao="Acessos e Auditoria", audit_tab="Auditoria"
    )
    assert not reruns
    assert state["pending_open_admin"]["secao"] == "Acessos e Auditoria"
    assert "admin_secao" not in state
    access_ui.request_admin_navigation(secao="Sistema", aba="Backup")
    assert reruns == [True]
    assert state["pending_open_admin"]["aba"] == "Backup"
    access_ui.consume_pending_open_admin()
    assert "pending_open_admin" not in state
    assert state["admin_secao"] == "Sistema"
    assert state["admin_sistema_aba"] == "Backup"
    access_ui.consume_pending_open_admin()
    assert state["admin_secao"] == "Sistema"
    assert "st.rerun()" not in getsource(access_ui.queue_admin_navigation)
    assert "st.rerun()" in getsource(access_ui.request_admin_navigation)
    health_src = getsource(system_ui.render_health)
    assert 'st.session_state["admin_secao"]' not in health_src
    assert "request_admin_navigation" in health_src


def test_health_goto_audit_before_admin_widgets(store, monkeypatch):
    from services.audit import registrar_erro

    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    registrar_erro(
        store, modulo="admin", acao="DIAGNOSTICO", erro=RuntimeError("falha sintética")
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_admin").click().run()
    app.radio(key="admin_secao").set_value("Sistema").run()
    assert any(getattr(b, "key", None) == "sistema_goto_audit" for b in app.button)
    app.button(key="sistema_goto_audit").click().run()
    assert not app.exception and not app.error
    assert app.sidebar.radio(key="portal_module").value == "Administração"
    assert app.radio(key="admin_secao").value == "Acessos e Auditoria"
    assert app.radio(key="audit_tab").value == "Auditoria"
    assert "pending_open_admin" not in app.session_state
    app.run()
    assert app.radio(key="admin_secao").value == "Acessos e Auditoria"
    assert "pending_open_admin" not in app.session_state


def test_system_saude_backup_toggle(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_admin").click().run()
    app.radio(key="admin_secao").set_value("Sistema").run()
    assert any("Saúde" in str(h.value) for h in app.subheader)
    app.radio(key="admin_sistema_aba").set_value("Backup").run()
    assert not app.exception
    assert any("Backup" in str(h.value) for h in app.subheader)
    app.radio(key="admin_sistema_aba").set_value("Saúde").run()
    assert not app.exception
    assert any("Saúde" in str(h.value) for h in app.subheader)


def test_postgres_engine_identified(pg_store):
    _full_schema(pg_store)
    report = diagnose(pg_store)
    assert report["database"]["engine"] == "PostgreSQL"
    assert report["database"]["status"] == OK
    assert report["schema"]["status"] == OK
    assert 1 in (report["schema"].get("postgres_schema_versions") or [])
