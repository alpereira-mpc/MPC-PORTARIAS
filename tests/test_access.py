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
    listed = next(u for u in access.list_users() if u["id"] == identifier)
    assert listed["gabinetes"] == user["gabinetes"]
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


def _gabinetes_of(store, identifier):
    with store.connection(read_only=True) as c:
        return [
            r[0]
            for r in c.execute(
                "SELECT gabinete FROM usuario_gabinetes WHERE usuario_id=?",
                (identifier,),
            )
        ]


def _audit_actions(store):
    with store.connection(read_only=True) as c:
        return [r[0] for r in c.execute("SELECT acao FROM eventos ORDER BY id")]


def exercise_user_deletion(store):
    access = AccessStore(store)
    admin = access.get_by_email(TEST_IDENTITY["email"])
    ordinary = access.save_user(
        {
            "nome": "Para Excluir",
            "email": "apagar@test.local",
            "perfil": "USUARIO",
            "pode_oficios": True,
            "gabinetes": ["PROGE", "LAF"],
        }
    )
    assert _gabinetes_of(store, ordinary)
    access.delete_user(ordinary)
    with pytest.raises(ValueError, match="não encontrado"):
        access.get(ordinary)
    assert access.get_by_email("apagar@test.local") is None
    assert _gabinetes_of(store, ordinary) == []
    assert "acesso_excluir_usuario" in _audit_actions(store)

    with pytest.raises(ValueError, match="próprio cadastro"):
        access.delete_user(admin["id"], actor_id=admin["id"])
    assert access.get(admin["id"])["email"] == TEST_IDENTITY["email"]

    with pytest.raises(ValueError, match="último administrador"):
        access.delete_user(admin["id"])
    assert access.get(admin["id"])

    other_admin = access.save_user(
        {
            "nome": "Segundo Admin",
            "email": "segundo.admin@test.local",
            "perfil": "ADMINISTRADOR",
        }
    )
    access.delete_user(other_admin, actor_id=admin["id"])
    assert access.get_by_email("segundo.admin@test.local") is None
    assert access.get(admin["id"])["perfil"] == "ADMINISTRADOR"

    inactive = access.save_user(
        {
            "nome": "Inativo",
            "email": "inativo@test.local",
            "perfil": "USUARIO",
            "ativo": True,
            "pode_agenda": True,
        }
    )
    access.set_active(inactive, False)
    assert not access.get(inactive)["ativo"]
    access.set_active(inactive, True)
    assert access.get(inactive)["ativo"]
    access.delete_user(inactive)
    assert access.get_by_email("inativo@test.local") is None


def test_delete_user_sqlite_rules_and_activation(store):
    exercise_user_deletion(store)


def test_delete_user_postgres_rules_and_activation(pg_store):
    exercise_user_deletion(pg_store)


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


def test_usuario_payloads_are_not_promoted_to_administrator(store):
    access = AccessStore(store)
    empty = access.get(
        access.save_user(
            {
                "nome": "Sem Permissao",
                "email": "sem@test.local",
                "perfil": "USUARIO",
                "pode_admin": False,
            }
        )
    )
    assert empty["perfil"] == "USUARIO"
    assert not any(
        empty[key]
        for key in ("pode_portarias", "pode_agenda", "pode_oficios", "pode_admin")
    )
    assert empty["gabinetes"] == []

    portarias = access.get(
        access.save_user(
            {
                "nome": "So Portarias",
                "email": "portarias@test.local",
                "perfil": "USUARIO",
                "pode_portarias": True,
                "gabinetes": list(GABINETES),
            }
        )
    )
    assert portarias["perfil"] == "USUARIO"
    assert portarias["pode_portarias"] and not portarias["pode_oficios"]
    assert portarias["gabinetes"] == []

    proge = access.get(
        access.save_user(
            {
                "nome": "Elkson Miranda",
                "email": "elkson@test.local",
                "perfil": "USUARIO",
                "pode_oficios": True,
                "pode_admin": False,
                "gabinetes": ["PROGE"],
            }
        )
    )
    assert proge["perfil"] == "USUARIO"
    assert proge["gabinetes"] == ["PROGE"]
    assert not proge["pode_admin"]

    varios = access.get(
        access.save_user(
            {
                "nome": "Varios Gabinetes",
                "email": "varios@test.local",
                "perfil": "USUARIO",
                "pode_oficios": True,
                "gabinetes": ["LAF", "PROGE", "BTLC"],
            }
        )
    )
    assert set(varios["gabinetes"]) == {"PROGE", "BTLC", "LAF"}

    promoted = access.get(
        access.save_user(
            {
                "nome": "Com Admin Modulo",
                "email": "moduloadmin@test.local",
                "perfil": "USUARIO",
                "pode_admin": True,
                "pode_agenda": True,
                "gabinetes": ["SBBQ"],
            }
        )
    )
    assert promoted["perfil"] == "USUARIO"
    assert promoted["pode_admin"] and promoted["pode_agenda"]
    assert not promoted["pode_portarias"]
    assert promoted["gabinetes"] == []
    principal = resolve_principal(store, {"email": "moduloadmin@test.local"})
    assert not principal.administrator
    assert has_permission(principal, "admin")
    assert has_permission(principal, "agenda")
    assert not has_permission(principal, "portarias")
    assert allowed_gabinetes(principal) == ()

    access.save_user(
        {
            **proge,
            "pode_agenda": True,
            "pode_oficios": True,
            "gabinetes": ["PROGE", "LAF"],
        },
        proge["id"],
    )
    edited = access.get(proge["id"])
    assert edited["perfil"] == "USUARIO"
    assert edited["pode_agenda"]
    assert set(edited["gabinetes"]) == {"PROGE", "LAF"}

    full = access.get(
        access.save_user(
            {
                "nome": "Chefe",
                "email": "chefe@test.local",
                "perfil": "ADMINISTRADOR",
                "pode_portarias": False,
                "gabinetes": ["PROGE"],
            }
        )
    )
    assert full["perfil"] == "ADMINISTRADOR"
    assert full["pode_portarias"] and full["pode_admin"]
    assert set(full["gabinetes"]) == set(GABINETES)


def test_admin_form_new_user_is_not_contaminated_by_logged_administrator(
    store, monkeypatch
):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from tests.access_testing import enable_login

    admin_id = AccessStore(store).get_by_email(TEST_IDENTITY["email"])["id"]
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_admin").click().run()
    assert next(s for s in app.selectbox if s.label == "Perfil").value == "USUARIO"
    assert next(c for c in app.checkbox if c.label == "Administração").value is False
    assert next(m for m in app.multiselect if "Gabinetes" in m.label).value == []
    app.selectbox(key="acesso_pick").set_value(admin_id).run()
    assert (
        next(s for s in app.selectbox if s.label == "Perfil").value == "ADMINISTRADOR"
    )
    app.selectbox(key="acesso_pick").set_value(0).run()
    assert next(s for s in app.selectbox if s.label == "Perfil").value == "USUARIO"
    assert next(c for c in app.checkbox if c.label == "Administração").value is False
    assert next(m for m in app.multiselect if "Gabinetes" in m.label).value == []
    next(t for t in app.text_input if t.label == "Nome").set_value(
        "Elkson Miranda"
    ).run()
    next(t for t in app.text_input if t.label == "E-mail").set_value(
        "elkson.form@test.local"
    ).run()
    next(c for c in app.checkbox if c.label == "Ofícios").set_value(True).run()
    next(m for m in app.multiselect if "Gabinetes" in m.label).set_value(
        ["PROGE"]
    ).run()
    app.button(key="FormSubmitter:acesso_user_0-Salvar").click().run()
    saved = AccessStore(store).get_by_email("elkson.form@test.local")
    assert saved["perfil"] == "USUARIO"
    assert saved["pode_oficios"] and not saved["pode_admin"]
    assert saved["gabinetes"] == ["PROGE"]


def test_admin_delete_requires_confirmation_and_can_be_cancelled(store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT
    from tests.access_testing import enable_login

    access = AccessStore(store)
    target = access.save_user(
        {
            "nome": "Candidato",
            "email": "candidato@test.local",
            "perfil": "USUARIO",
            "pode_agenda": True,
        }
    )
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_admin").click().run()
    app.selectbox(key="acesso_pick").set_value(target).run()
    next(b for b in app.button if b.label == "Excluir usuário").click().run()
    next(b for b in app.button if b.label == "Cancelar exclusão").click().run()
    assert access.get_by_email("candidato@test.local")
    app.selectbox(key="acesso_pick").set_value(target).run()
    next(b for b in app.button if b.label == "Excluir usuário").click().run()
    next(
        c for c in app.checkbox if c.label == "Confirmo a exclusão definitiva"
    ).set_value(True).run()
    next(t for t in app.text_input if "EXCLUIR" in t.label).set_value("EXCLUIR").run()
    next(b for b in app.button if b.label == "Confirmar exclusão").click().run()
    assert access.get_by_email("candidato@test.local") is None
    assert not app.exception
    assert app.session_state["acesso_pick"] == 0


def test_unverified_oidc_email_is_rejected(monkeypatch):
    class User:
        is_logged_in = True
        email = "A@Test.COM"
        email_verified = False
        name = "A"

    monkeypatch.setattr("streamlit.user", User(), raising=False)
    from services.access import oidc_identity

    assert oidc_identity() is None
