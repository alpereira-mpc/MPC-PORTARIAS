from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader

from database.estagiarios import EstagiariosStore, LOTACOES, limite_padrao
from database.memorandos import MemorandosStore
from document_generator.estagiarios_pdf import generate_estagiarios_pdf
from services.access import Principal
from services.estagiarios_ui import _can_manage


class _InsertCursor:
    def __init__(self, row=None, lastrowid=None):
        self.row = row
        self.lastrowid = lastrowid

    def fetchone(self):
        return self.row


class _PostgresCreateConnection:
    def __init__(self):
        self.insert_statement = None

    def execute(self, statement, values=None):
        if statement.startswith("SELECT 1 FROM servidores"):
            return _InsertCursor((1,))
        if statement.startswith("SELECT COUNT(*)"):
            return _InsertCursor((0,))
        if statement.startswith("SELECT 1 FROM estagiarios_lotacoes"):
            return _InsertCursor(None)
        if statement.startswith("INSERT INTO estagiarios_lotacoes"):
            self.insert_statement = statement
            return _InsertCursor(lastrowid=73)
        return _InsertCursor()


class _PostgresCreateStore:
    backend = "postgresql"

    def __init__(self):
        self.cursor = _PostgresCreateConnection()

    def connection(self, **_kwargs):
        class _Context:
            def __enter__(_self):
                return self.cursor

            def __exit__(_self, *_args):
                return False

        return _Context()


def _people(store):
    base = MemorandosStore(store)
    base.import_servers(
        [
            {"nome": "Ana Estágio", "matricula": "101", "cargo": "Estagiária", "setor": "PROGE"},
            {"nome": "Bruno Estágio", "matricula": "102", "cargo": "Estagiário", "setor": "PROGE"},
            {"nome": "Carla Estágio", "matricula": "103", "cargo": "Estagiária", "setor": "PROGE"},
            {"nome": "Davi Analista", "matricula": "104", "cargo": "Analista", "setor": "PROGE"},
        ],
        actor_email="admin@test", filename="base.xlsx", content_hash="servers", administrator=True,
    )
    return {row["nome"]: row["id"] for row in base.all_servers()}


def _principal(*, perfil="USUARIO", pode_admin=False, email="pessoa@test.local"):
    return Principal(
        id=1,
        nome="Pessoa de teste",
        email=email,
        perfil=perfil,
        ativo=True,
        pode_portarias=False,
        pode_agenda=False,
        pode_oficios=False,
        pode_admin=pode_admin,
        gabinetes=(),
    )


def test_limit_two_active_positions_and_admin_only(store):
    people = _people(store)
    service = EstagiariosStore(store)
    start = date(2026, 3, 10)
    assert len(LOTACOES) == 7
    assert service.summary()["total"] == 14
    with pytest.raises(ValueError, match="Lotação inválida"):
        service.create(people["Ana Estágio"], "PROGE", start, administrator=True)
    with pytest.raises(ValueError, match="administradores"):
        service.create(people["Ana Estágio"], "PROGE", start, administrator=False)
    service.create(people["Ana Estágio"], "ESPO", start, administrator=True)
    service.create(people["Bruno Estágio"], "ESPO", start, administrator=True)
    with pytest.raises(ValueError, match="duas posições"):
        service.create(people["Carla Estágio"], "ESPO", start, administrator=True)
    with pytest.raises(ValueError, match="classificada como estagiária"):
        service.create(people["Davi Analista"], "MTTF", start, administrator=True)


def test_authorized_administrators_can_manage_internships_and_users_cannot(
    store, monkeypatch
):
    from services.estagiarios_ui import render

    people = _people(store)
    service = EstagiariosStore(store)
    profile_admin = _principal(
        perfil="ADMINISTRADOR", pode_admin=True, email="admin@test.local"
    )
    delegated_admin = _principal(pode_admin=True, email="nguedes@test.local")
    regular_user = _principal(email="usuario@test.local")

    assert _can_manage(profile_admin)
    assert _can_manage(delegated_admin)
    assert not _can_manage(regular_user)

    first = service.create(
        people["Ana Estágio"],
        "ESPO",
        date(2026, 3, 10),
        actor_email=profile_admin.email,
        administrator=_can_manage(profile_admin),
    )
    service.update(
        first,
        "LAF",
        date(2026, 3, 10),
        date(2028, 3, 10),
        actor_email=delegated_admin.email,
        administrator=_can_manage(delegated_admin),
    )
    service.close(
        first,
        date(2026, 9, 1),
        actor_email=delegated_admin.email,
        administrator=_can_manage(delegated_admin),
    )
    assert not service.list()[0]["ativo"]

    with pytest.raises(ValueError, match="administradores"):
        service.create(
            people["Bruno Estágio"],
            "ESPO",
            date(2026, 3, 10),
            actor_email=regular_user.email,
            administrator=_can_manage(regular_user),
        )
    messages = []
    monkeypatch.setattr("services.estagiarios_ui.st.error", messages.append)
    assert render(store, regular_user) is None
    assert messages == ["Acesso restrito a usuários com permissão administrativa."]


def test_two_year_limit_close_and_replace_preserve_history(store):
    people = _people(store)
    service = EstagiariosStore(store)
    start = date(2026, 2, 28)
    assert limite_padrao(start) == date(2028, 2, 28)
    identifier = service.create(people["Ana Estágio"], "LAF", start, administrator=True)
    row = service.list(include_inactive=False)[0]
    assert row["data_limite"] == "2028-02-28"
    replacement = service.replace(identifier, people["Bruno Estágio"], date(2026, 6, 1), administrator=True)
    rows = service.list()
    old = next(item for item in rows if item["id"] == identifier)
    new = next(item for item in rows if item["id"] == replacement)
    assert not old["ativo"] and old["data_encerramento"] == "2026-06-01"
    assert new["ativo"] and new["pessoa_id"] == people["Bruno Estágio"]
    service.close(replacement, date(2026, 9, 1), administrator=True)
    assert not service.list(include_inactive=False)


def test_create_uses_cursor_identity_contract_for_postgresql():
    store = _PostgresCreateStore()
    service = EstagiariosStore.__new__(EstagiariosStore)
    service.store = store
    identifier = service.create(17, "ESPO", date(2026, 3, 10), administrator=True)
    assert identifier == 73
    assert "RETURNING" not in store.cursor.insert_statement


def test_current_composition_and_pdf_contain_only_active_valid_placements(store):
    people = _people(store)
    service = EstagiariosStore(store)
    active_id = service.create(people["Ana Estágio"], "ESPO", date(2026, 3, 10), administrator=True)
    closed_id = service.create(people["Bruno Estágio"], "LAF", date(2026, 3, 10), administrator=True)
    service.close(closed_id, date(2026, 4, 1), administrator=True)
    composition = service.current_composition()
    assert composition["ESPO"][0]["id"] == active_id
    assert not composition["LAF"]
    content = generate_estagiarios_pdf(composition)
    text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
    assert content.startswith(b"%PDF-")
    assert "Elvira Samara Pereira de Oliveira" in text
    assert "Ana Estágio" in text
    assert "Bruno Estágio" not in text
    assert "PROGE" not in text
