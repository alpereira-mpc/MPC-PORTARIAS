from datetime import datetime, timezone

import pytest
from streamlit.testing.v1 import AppTest

from database.access import AccessStore, LAST_ADMIN_MESSAGE, PROTECTED_ADMIN_EMAIL
from database.audit import AuditStore, EXPORT_LIMIT, PAGE_SIZE, MARKER, ensure_schema
from database.store import ROOT
from services.access import has_permission, resolve_principal
from services.audit import (
    aplicar_usuario,
    export_csv,
    format_local,
    iniciar_sessao_autorizada,
    overview,
    period_bounds,
    registrar_acesso_negado,
    registrar_evento,
    registrar_modulo,
    sanitize_details,
)
from tests.access_testing import TEST_IDENTITY, enable_login, seed_access
from tests.test_postgresql import pg_store, pg_url  # noqa: F401


def _count(store, evento=None):
    with store.connection(read_only=True) as c:
        if evento:
            return c.execute(
                "SELECT COUNT(*) FROM auditoria_eventos WHERE evento=?", (evento,)
            ).fetchone()[0]
        return c.execute("SELECT COUNT(*) FROM auditoria_eventos").fetchone()[0]


def _principal(store, email=TEST_IDENTITY["email"]):
    return resolve_principal(store, {"email": email})


def test_sanitize_drops_secrets_and_document_bodies():
    clean = sanitize_details(
        {
            "gabinete": "PROGE",
            "numero": "12/2026",
            "token": "abc",
            "client_secret": "x",
            "assunto": "texto longo demais",
            "docx": b"bytes",
            "registros_novos": 12,
        }
    )
    assert clean["gabinete"] == "PROGE"
    assert clean["numero"] == "12/2026"
    assert clean["registros_novos"] == 12
    assert "token" not in clean
    assert "client_secret" not in clean
    assert "assunto" not in clean
    assert "docx" not in clean


def test_session_rerun_does_not_count_as_new_access(store):
    principal = _principal(store)
    state = {}
    iniciar_sessao_autorizada(store, principal, state=state)
    iniciar_sessao_autorizada(store, principal, state=state)
    iniciar_sessao_autorizada(store, principal, state=state)
    assert _count(store, "SESSAO_INICIADA") == 1
    assert _count(store, "ACESSO_AUTORIZADO") == 1
    assert state["audit_sessao_id"]
    assert state["audit_sessao_registrada"]


def test_authorized_and_denied_access_are_recorded(store):
    principal = _principal(store)
    iniciar_sessao_autorizada(store, principal, state={})
    denied = {}
    registrar_acesso_negado(
        store,
        {"email": "intruso@test.local", "name": "Intruso"},
        state=denied,
    )
    registrar_acesso_negado(
        store,
        {"email": "intruso@test.local", "name": "Intruso"},
        state=denied,
    )
    seed_access(store, email="off@test.local", ativo=False, perfil="USUARIO")
    registrar_acesso_negado(
        store,
        {"email": "off@test.local", "name": "Inativo"},
        inativo=True,
        state={},
    )
    assert _count(store, "ACESSO_AUTORIZADO") == 1
    assert _count(store, "ACESSO_NEGADO") == 1
    assert _count(store, "USUARIO_INATIVO") == 1


def test_module_entry_is_deduplicated_until_leave(store):
    principal = _principal(store)
    state = {}
    registrar_modulo(store, principal, "Portarias", state=state)
    registrar_modulo(store, principal, "Portarias", state=state)
    registrar_modulo(store, principal, "Agenda", state=state)
    registrar_modulo(store, principal, "Agenda", state=state)
    state["audit_modulo_atual"] = None
    registrar_modulo(store, principal, "Portarias", state=state)
    assert _count(store, "MODULO_ACESSADO") == 3


def test_document_and_admin_actions_are_recorded(store):
    principal = _principal(store)
    registrar_evento(
        store,
        evento="PORTARIA_FINALIZADA",
        modulo="portarias",
        acao="FINALIZAR",
        principal=principal,
        entidade_tipo="portaria",
        entidade_id="abc",
        detalhes={"numero": "12/2026"},
        state={},
    )
    other = AccessStore(store).save_user(
        {
            "nome": "Alvo",
            "email": "alvo.audit@test.local",
            "perfil": "USUARIO",
            "pode_agenda": True,
        }
    )
    aplicar_usuario(
        store,
        principal,
        {**AccessStore(store).get(other), "pode_portarias": True, "ativo": False},
        other,
    )
    events = {r["evento"] for r in AuditStore(store).list_events(limit=50)}
    assert "PORTARIA_FINALIZADA" in events
    assert "USUARIO_EDITADO" in events
    assert "PERMISSOES_ALTERADAS" in events
    assert "USUARIO_DESATIVADO" in events


def test_protected_admin_block_is_recorded(store):
    from database.access import PROTECTED_ADMIN_MESSAGE
    import database.access as access_mod

    principal = _principal(store)
    payload = {
        "nome": "André Luiz Pereira",
        "email": PROTECTED_ADMIN_EMAIL,
        "perfil": "ADMINISTRADOR",
    }
    identifier = AccessStore(store).save_user(payload)
    access_mod._READY.clear()
    AccessStore(store)
    protected = AccessStore(store).get(identifier)
    with pytest.raises(ValueError, match="protegido"):
        aplicar_usuario(
            store,
            principal,
            {**protected, "perfil": "USUARIO", "pode_admin": False},
            protected["id"],
        )
    assert _count(store, "ADMIN_PROTEGIDO_BLOQUEADO") == 1
    row = AuditStore(store).list_events({"evento": "ADMIN_PROTEGIDO_BLOQUEADO"})[0]
    assert row["usuario_email"] == principal.email
    assert PROTECTED_ADMIN_EMAIL in (row["detalhes_json"] or "")
    assert LAST_ADMIN_MESSAGE
    assert PROTECTED_ADMIN_MESSAGE


def test_ordinary_user_cannot_open_audit(store):
    from services.audit_ui import render

    seed_access(
        store,
        email="comum.audit@test.local",
        perfil="USUARIO",
        pode_admin=False,
        pode_agenda=True,
    )
    principal = resolve_principal(store, {"email": "comum.audit@test.local"})
    assert not has_permission(principal, "admin")
    with pytest.raises(ValueError, match="módulo"):
        render(store, principal)


def test_administrator_opens_audit_ui(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_admin").click().run()
    assert not app.exception
    app.radio(key="admin_secao").set_value("Acessos e Auditoria").run()
    assert not app.exception and not app.error
    headings = [h.value for h in app.subheader]
    assert any("Acessos e Auditoria" in str(h) for h in headings)
    app.radio(key="audit_tab").set_value("Auditoria").run()
    assert not app.exception
    assert any(b.label == "Exportar auditoria (CSV)" for b in app.get("download_button"))


def test_filters_pagination_dates_and_csv(store):
    principal = _principal(store)
    for index in range(5):
        registrar_evento(
            store,
            evento="MODULO_ACESSADO" if index else "SESSAO_INICIADA",
            modulo="agenda" if index % 2 else "portarias",
            acao="ENTRAR",
            principal=principal,
            state={"audit_sessao_id": "sessao-teste"},
        )
    audit = AuditStore(store)
    filtered = audit.list_events({"modulo": "agenda"}, limit=50, offset=0)
    assert filtered and all(r["modulo"] == "agenda" for r in filtered)
    page1 = audit.list_events(limit=2, offset=0)
    page2 = audit.list_events(limit=2, offset=2)
    assert len(page1) == 2
    assert {r["id"] for r in page1}.isdisjoint({r["id"] for r in page2})
    utc = datetime(2026, 9, 14, 12, 0, 0, tzinfo=timezone.utc)
    assert format_local(utc.isoformat()) == "14/09/2026 09:00:00"
    start, end = period_bounds("hoje")
    assert start <= datetime.now(timezone.utc).isoformat() <= end
    csv_text, total, truncated = export_csv(store, {"modulo": "portarias"})
    assert "data_hora_local" in csv_text.splitlines()[0]
    assert total >= 1
    assert truncated is False
    assert EXPORT_LIMIT >= PAGE_SIZE


def test_logger_failure_does_not_break_main_operation(store, monkeypatch):
    principal = _principal(store)

    def boom(*args, **kwargs):
        raise RuntimeError("auditoria indisponível")

    monkeypatch.setattr("database.audit.AuditStore.insert", boom)
    assert (
        registrar_evento(
            store,
            evento="PORTARIA_FINALIZADA",
            modulo="portarias",
            acao="FINALIZAR",
            principal=principal,
            state={},
        )
        is None
    )
    identifier = store.save_draft(
        {
            "data": "2026-03-02",
            "signatario": store.catalog("procuradores")[0],
            "substituicoes": [],
        }
    )
    assert identifier
    assert store.get(identifier)["status"] == "Rascunho"


def test_sqlite_schema_is_idempotent(store):
    first = AuditStore(store)
    ensure_schema(store)
    AuditStore(store)
    with store.connection(read_only=True) as c:
        names = {r[1] for r in c.execute("PRAGMA table_info(auditoria_eventos)")}
        indexes = {
            r[1] for r in c.execute("PRAGMA index_list(auditoria_eventos)")
        }
        marker = c.execute(
            "SELECT valor FROM configuracoes WHERE chave=?", (MARKER,)
        ).fetchone()[0]
    assert "criado_em" in names and "detalhes_json" in names
    assert "auditoria_eventos_criado_em_idx" in indexes
    assert marker == "1"
    assert first is not None


def test_postgres_schema_is_compatible(pg_store):
    ensure_schema(pg_store)
    AuditStore(pg_store)
    with pg_store.connection(read_only=True) as c:
        columns = {
            r[0]
            for r in c.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema=current_schema() AND table_name='auditoria_eventos'"
            )
        }
        indexes = {
            r[0]
            for r in c.execute(
                "SELECT indexname FROM pg_catalog.pg_indexes "
                "WHERE schemaname=current_schema() AND tablename='auditoria_eventos'"
            )
        }
    expected = {
        "id",
        "usuario_id",
        "usuario_email",
        "usuario_nome",
        "sessao_id",
        "evento",
        "modulo",
        "acao",
        "entidade_tipo",
        "entidade_id",
        "resultado",
        "detalhes_json",
        "criado_em",
    }
    assert expected <= columns
    assert "auditoria_eventos_criado_em_idx" in indexes
    principal = _principal(pg_store)
    registrar_evento(
        pg_store,
        evento="SESSAO_INICIADA",
        acao="ACESSO",
        principal=principal,
        state={"audit_sessao_id": "pg-sessao"},
    )
    assert AuditStore(pg_store).count({"evento": "SESSAO_INICIADA"}) >= 1


def test_overview_not_queried_from_login(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Home must not query audit")

    monkeypatch.setattr("services.access.oidc_identity", lambda: None)
    monkeypatch.setattr("database.audit.AuditStore.period_summary", forbidden)
    monkeypatch.setattr("services.audit.overview", forbidden)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert app.title[0].value == "Acesso restrito"


def test_portal_counts_one_session_across_reruns(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.sidebar.radio(key="portal_module").set_value("Agenda").run()
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    app.sidebar.radio(key="portal_module").set_value("Agenda").run()
    assert _count(store, "SESSAO_INICIADA") == 1
    assert _count(store, "MODULO_ACESSADO") == 2
    data = overview(store)
    assert data["usuarios_ativos"] >= 1
    assert data["hoje"]["sessoes"] >= 1


def test_last_admin_block_is_recorded(store):
    principal = _principal(store)
    admin = AccessStore(store).get_by_email(TEST_IDENTITY["email"])
    with pytest.raises(ValueError, match="administrador ativo"):
        aplicar_usuario(
            store,
            principal,
            {**admin, "perfil": "USUARIO", "pode_admin": False, "ativo": False},
            admin["id"],
        )
    assert _count(store, "ULTIMO_ADMIN_BLOQUEADO") == 1
