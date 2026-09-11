from services.access import (
    allowed_gabinetes,
    can_use_gabinete,
    has_permission,
    principal_from_record,
    require_gabinete,
    require_permission,
    resolve_principal,
)
from database.access import MARKER, AccessStore, ensure_schema, normalize_email
from services.oficios import GABINETES
from tests.access_testing import TEST_IDENTITY, seed_access
from tests.test_postgresql import pg_store, pg_url  # noqa: F401
import pytest


def test_normalize_email():
    assert normalize_email("  Admin@Example.COM ") == "admin@example.com"
    assert normalize_email("") == ""


def test_active_user_resolves(store):
    seed_access(store, email="user@mpc.pb.gov.br", perfil="USUARIO", pode_admin=False)
    principal = resolve_principal(store, {"email": "USER@mpc.pb.gov.br"})
    assert principal.email == "user@mpc.pb.gov.br"
    assert principal.ativo


def test_missing_and_inactive_are_denied(store):
    seed_access(store, email="off@test.local", ativo=False)
    assert resolve_principal(store, {"email": "nobody@test.local"}) is None
    assert resolve_principal(store, {"email": "off@test.local"}) is None
    assert resolve_principal(store, None) is None


def test_administrator_has_all_modules_and_offices(store):
    seed_access(store)
    principal = resolve_principal(store, TEST_IDENTITY)
    assert principal.administrator
    for module in ("portarias", "agenda", "oficios", "admin"):
        assert has_permission(principal, module)
    assert allowed_gabinetes(principal) == GABINETES


def test_common_user_permissions_and_offices(store):
    seed_access(
        store,
        email="limite@test.local",
        perfil="USUARIO",
        pode_portarias=True,
        pode_agenda=False,
        pode_oficios=True,
        pode_admin=False,
        gabinetes=["PROGE", "LAF"],
    )
    principal = resolve_principal(store, {"email": "limite@test.local"})
    assert has_permission(principal, "portarias")
    assert not has_permission(principal, "agenda")
    assert has_permission(principal, "oficios")
    assert not has_permission(principal, "admin")
    assert allowed_gabinetes(principal) == ("PROGE", "LAF")
    assert can_use_gabinete(principal, "PROGE")
    assert not can_use_gabinete(principal, "BTLC")
    with pytest.raises(ValueError, match="módulo"):
        require_permission(principal, "agenda")
    with pytest.raises(ValueError, match="gabinete"):
        require_gabinete(principal, "BTLC")


def test_oficios_without_flag_has_no_offices(store):
    seed_access(
        store,
        email="semoficio@test.local",
        perfil="USUARIO",
        pode_oficios=False,
        pode_admin=False,
        gabinetes=["PROGE"],
    )
    principal = resolve_principal(store, {"email": "semoficio@test.local"})
    assert allowed_gabinetes(principal) == ()


def test_save_edit_deactivate_and_unique_email(store):
    access = AccessStore(store)
    identifier = access.save_user(
        {
            "nome": "Ana",
            "email": "Ana@Test.LOCAL",
            "perfil": "USUARIO",
            "pode_agenda": True,
            "gabinetes": ["SBBQ"],
        }
    )
    user = access.get(identifier)
    assert user["email"] == "ana@test.local"
    assert user["pode_agenda"] and not user["pode_portarias"]
    with pytest.raises(ValueError, match="e-mail"):
        access.save_user(
            {"nome": "Outra", "email": "ana@test.local", "perfil": "USUARIO"}
        )
    access.save_user(
        {**user, "nome": "Ana Silva", "pode_portarias": True, "gabinetes": ["PROGE"]},
        identifier,
    )
    updated = access.get(identifier)
    assert updated["nome"] == "Ana Silva"
    assert updated["pode_portarias"]
    access.set_active(identifier, False)
    assert not access.get(identifier)["ativo"]
    access.set_active(identifier, True)
    assert access.get(identifier)["ativo"]


def test_postgres_access_schema_marker_and_idempotency(pg_store):
    with pg_store.connection(read_only=True) as c:
        tables = {
            r[0]
            for r in c.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname=current_schema()"
            )
        }
        assert {"usuarios_acesso", "usuario_gabinetes"} <= tables
        assert (
            c.execute(
                "SELECT valor FROM configuracoes WHERE chave=?", (MARKER,)
            ).fetchone()[0]
            == "1"
        )
        versions = [r[0] for r in c.execute("SELECT version FROM schema_migrations")]
        assert versions == [1]
        before = c.execute("SELECT COUNT(*) FROM usuarios_acesso").fetchone()[0]
    ensure_schema(pg_store)
    AccessStore(pg_store)
    with pg_store.connection(read_only=True) as c:
        after = c.execute("SELECT COUNT(*) FROM usuarios_acesso").fetchone()[0]
        assert after == before
        assert (
            c.execute(
                "SELECT valor FROM configuracoes WHERE chave=?", (MARKER,)
            ).fetchone()[0]
            == "1"
        )


def test_postgres_access_crud_permissions_and_revocation(pg_store):
    access = AccessStore(pg_store)
    identifier = access.save_user(
        {
            "nome": "Ana",
            "email": "Ana@PG.TEST.LOCAL",
            "perfil": "USUARIO",
            "pode_portarias": True,
            "pode_agenda": False,
            "pode_oficios": True,
            "pode_admin": False,
            "gabinetes": ["PROGE", "LAF"],
        }
    )
    created = access.get(identifier)
    assert created["email"] == "ana@pg.test.local"
    principal = resolve_principal(pg_store, {"email": "ANA@pg.test.local"})
    assert principal.email == "ana@pg.test.local"
    assert has_permission(principal, "portarias")
    assert not has_permission(principal, "agenda")
    assert has_permission(principal, "oficios")
    assert not has_permission(principal, "admin")
    assert allowed_gabinetes(principal) == ("PROGE", "LAF")
    assert can_use_gabinete(principal, "PROGE")
    assert not can_use_gabinete(principal, "BTLC")
    access.save_user({**created, "nome": "Ana Silva", "pode_agenda": True}, identifier)
    edited = resolve_principal(pg_store, {"email": "ana@pg.test.local"})
    assert edited.nome == "Ana Silva"
    assert has_permission(edited, "agenda")
    access.set_active(identifier, False)
    assert resolve_principal(pg_store, {"email": "ana@pg.test.local"}) is None
    assert resolve_principal(pg_store, {"email": "nobody@pg.test.local"}) is None
    access.set_active(identifier, True)
    assert resolve_principal(pg_store, {"email": "ana@pg.test.local"}).ativo


def test_postgres_administrator_has_full_module_and_office_access(pg_store):
    seed_access(
        pg_store,
        email="admin.pg@test.local",
        perfil="ADMINISTRADOR",
        pode_portarias=False,
        pode_agenda=False,
        pode_oficios=False,
        pode_admin=False,
        gabinetes=[],
    )
    principal = resolve_principal(pg_store, {"email": "Admin.PG@test.local"})
    assert principal.administrator
    for module in ("portarias", "agenda", "oficios", "admin"):
        assert has_permission(principal, module)
    assert allowed_gabinetes(principal) == GABINETES
    assert can_use_gabinete(principal, "MASN")


def test_admin_profile_ignores_partial_checkboxes():
    record = {
        "id": 1,
        "nome": "Admin",
        "email": "a@test.local",
        "perfil": "ADMINISTRADOR",
        "ativo": True,
        "pode_portarias": False,
        "pode_agenda": False,
        "pode_oficios": False,
        "pode_admin": False,
        "gabinetes": [],
    }
    principal = principal_from_record(record)
    assert principal.pode_admin and principal.gabinetes == GABINETES


def test_unverified_oidc_email_is_rejected(monkeypatch):
    class User:
        is_logged_in = True
        email = "A@Test.COM"
        email_verified = False
        name = "A"

    monkeypatch.setattr("streamlit.user", User(), raising=False)
    from services.access import oidc_identity

    assert oidc_identity() is None
