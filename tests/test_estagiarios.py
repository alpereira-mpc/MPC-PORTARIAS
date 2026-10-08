from datetime import date
from io import BytesIO

import pytest
from pypdf import PdfReader
from streamlit.testing.v1 import AppTest

from database.estagiarios import EstagiariosStore, LOTACOES, limite_padrao
from database.memorandos import MemorandosStore
from document_generator.estagiarios_pdf import generate_estagiarios_pdf
from services.access import Principal
from services.estagiarios_ui import _can_manage, _open_create_form, _sync_edit_limit

_UI_HOLD = {}


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


def test_limit_is_always_calculated_from_start_and_keeps_early_closure(store):
    people = _people(store)
    service = EstagiariosStore(store)
    identifier = service.create(
        people["Ana Estágio"],
        "ESPO",
        date(2026, 3, 9),
        date(2035, 1, 1),
        administrator=True,
    )
    row = service.list(include_inactive=False)[0]
    assert row["data_limite"] == "2028-03-09"

    service.update(
        identifier,
        "ESPO",
        date(2024, 2, 29),
        date(2035, 1, 1),
        administrator=True,
    )
    row = service.list(include_inactive=False)[0]
    assert row["data_inicio"] == "2024-02-29"
    assert row["data_limite"] == "2026-02-28"

    service.close(identifier, date(2025, 1, 10), administrator=True)
    closed = service.list()[0]
    assert closed["data_encerramento"] == "2025-01-10"
    assert closed["data_limite"] == "2026-02-28"


def test_edit_limit_widget_is_updated_when_start_changes(monkeypatch):
    state = {
        "estagiario_edit_start_1": date(2024, 2, 29),
        "estagiario_edit_limit_1": date(2028, 3, 9),
    }
    monkeypatch.setattr("services.estagiarios_ui.st.session_state", state)

    _sync_edit_limit("estagiario_edit_start_1", "estagiario_edit_limit_1")

    assert state["estagiario_edit_limit_1"] == limite_padrao(date(2024, 2, 29))
    assert state["estagiario_edit_limit_1"] == date(2026, 2, 28)


def test_actions_keep_admin_fields_together_and_remove_replacement(store, monkeypatch):
    import services.estagiarios_ui as ui

    # AppTest does not execute fragment-scoped reruns; persistence is verified below.
    monkeypatch.setattr(ui.st, "rerun", lambda **_kwargs: None)

    people = _people(store)
    service = EstagiariosStore(store)
    identifier = service.create(
        people["Ana Estágio"], "ESPO", date(2026, 3, 10), administrator=True
    )
    _UI_HOLD.clear()
    _UI_HOLD.update(
        ui=ui, service=service, store=store,
        principal=_principal(pode_admin=True, email="admin@test.local"),
        identifier=identifier,
    )

    def page():
        import streamlit as st
        from tests.test_estagiarios import _UI_HOLD

        @st.fragment
        def controls():
            current = next(
                row for row in _UI_HOLD["service"].list()
                if row["id"] == _UI_HOLD["identifier"]
            )
            _UI_HOLD["ui"]._active_controls(
                _UI_HOLD["service"], _UI_HOLD["store"],
                _UI_HOLD["principal"], current,
            )

        controls()

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception
    assert [item.label for item in app.date_input] == [
        "Data de início", "Data limite", "Data de encerramento"
    ]
    assert app.date_input(key=f"estagiario_edit_limit_{identifier}").proto.disabled
    assert not any("substitu" in item.label.lower() for item in app.button)
    assert not any("substituto" in item.label.lower() for item in app.date_input)
    app.date_input(key=f"estagiario_edit_start_{identifier}").set_value(
        date(2024, 2, 29)
    ).run()
    assert app.date_input(key=f"estagiario_edit_limit_{identifier}").value == date(
        2026, 2, 28
    )
    app.selectbox(key=f"estagiario_edit_lotacao_{identifier}").set_value("LAF").run()
    app.button(key=f"estagiario_edit_save_{identifier}").click().run()
    saved = next(row for row in service.list() if row["id"] == identifier)
    assert saved["lotacao"] == "LAF"
    assert saved["data_inicio"] == "2024-02-29"
    assert saved["data_limite"] == "2026-02-28"
    app.date_input(key=f"estagiario_close_date_{identifier}").set_value(
        date(2025, 1, 10)
    ).run()
    next(item for item in app.button if item.label == "Encerrar vínculo").click().run()
    closed = next(row for row in service.list() if row["id"] == identifier)
    assert closed["ativo"] == 0
    assert closed["data_encerramento"] == "2025-01-10"


def test_internship_actions_keep_reruns_inside_the_portal_fragment(monkeypatch):
    import inspect

    from services.estagiarios_ui import _active_controls, _add_form, _vacancy

    state = {}
    monkeypatch.setattr("services.estagiarios_ui.st.session_state", state)
    _open_create_form()

    assert state["estagiario_create_expanded"] is True
    assert 'st.rerun(scope="fragment")' in inspect.getsource(_active_controls)
    assert 'st.rerun(scope="fragment")' in inspect.getsource(_add_form)
    assert "on_click=_open_create_form" in inspect.getsource(_vacancy)
    assert "st.rerun(" not in inspect.getsource(_vacancy)


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
