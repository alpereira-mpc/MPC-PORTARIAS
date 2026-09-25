"""Test helpers that mock OIDC identity without Google."""

from database.access import AccessStore
from services.oficios import GABINETES

TEST_IDENTITY = {
    "email": "admin@test.local",
    "name": "Administrador de Teste",
    "email_verified": True,
}


def seed_access(
    store,
    *,
    email=TEST_IDENTITY["email"],
    nome="Administrador de Teste",
    perfil="ADMINISTRADOR",
    ativo=True,
    pode_portarias=True,
    pode_agenda=True,
    pode_oficios=True,
    pode_admin=True,
    pode_memorandos=None,
    pode_relatorios=None,
    pode_representacoes=None,
    pode_ouvidoria=None,
    pode_representacoes_registrar_protocolo=None,
    pode_representacoes_enviar_comunicacao=None,
    pode_comunicacoes_configurar_destinatarios=None,
    pode_comunicacoes_enviar_teste=None,
    gabinetes=None,
):
    access = AccessStore(store)
    existing = access.get_by_email(email)
    payload = {
        "nome": nome,
        "email": email,
        "perfil": perfil,
        "ativo": ativo,
        "pode_portarias": pode_portarias,
        "pode_agenda": pode_agenda,
        "pode_oficios": pode_oficios,
        "pode_admin": pode_admin,
        "pode_memorandos": perfil == "ADMINISTRADOR" if pode_memorandos is None else pode_memorandos,
        "pode_relatorios": perfil == "ADMINISTRADOR" if pode_relatorios is None else pode_relatorios,
        "pode_representacoes": perfil == "ADMINISTRADOR" if pode_representacoes is None else pode_representacoes,
        "pode_ouvidoria": perfil == "ADMINISTRADOR" if pode_ouvidoria is None else pode_ouvidoria,
        "pode_representacoes_registrar_protocolo": perfil == "ADMINISTRADOR" if pode_representacoes_registrar_protocolo is None else pode_representacoes_registrar_protocolo,
        "pode_representacoes_enviar_comunicacao": perfil == "ADMINISTRADOR" if pode_representacoes_enviar_comunicacao is None else pode_representacoes_enviar_comunicacao,
        "pode_comunicacoes_configurar_destinatarios": perfil == "ADMINISTRADOR" if pode_comunicacoes_configurar_destinatarios is None else pode_comunicacoes_configurar_destinatarios,
        "pode_comunicacoes_enviar_teste": perfil == "ADMINISTRADOR" if pode_comunicacoes_enviar_teste is None else pode_comunicacoes_enviar_teste,
        "gabinetes": list(GABINETES) if gabinetes is None else gabinetes,
    }
    return access.save_user(payload, existing["id"] if existing else None)


def enable_login(monkeypatch, store, identity=None):
    seed_access(store)
    current = dict(identity or TEST_IDENTITY)
    monkeypatch.setattr("services.access.oidc_identity", lambda: current)
    return current
