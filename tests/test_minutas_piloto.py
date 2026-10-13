from streamlit.testing.v1 import AppTest

from database.store import ROOT
from portal import PORTAL_NAV_REQUEST
from services.minutas_piloto import can_access_minutas_piloto
from tests.access_testing import TEST_IDENTITY, enable_login, seed_access


def _run_portal(store, monkeypatch, identity, secrets):
    enable_login(monkeypatch, store, identity)
    monkeypatch.setattr("database.store.Store", lambda: store)
    monkeypatch.setattr("streamlit.secrets", secrets)
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()


def test_access_requires_enabled_flag_and_authorized_verified_identity():
    secrets = {
        "MINUTAS_PILOTO_ENABLED": True,
        "MINUTAS_PILOTO_EMAILS": ["ADMIN@Test.Local"],
    }

    assert can_access_minutas_piloto(TEST_IDENTITY, secrets) is True
    assert can_access_minutas_piloto(
        {**TEST_IDENTITY, "email": "outro@test.local"}, secrets
    ) is False
    assert can_access_minutas_piloto(
        {**TEST_IDENTITY, "email_verified": False}, secrets
    ) is False
    assert (
        can_access_minutas_piloto(
            TEST_IDENTITY, {**secrets, "MINUTAS_PILOTO_ENABLED": False}
        )
        is False
    )


def test_access_denies_missing_or_invalid_configuration():
    invalid = (
        {},
        {"MINUTAS_PILOTO_ENABLED": True},
        {
            "MINUTAS_PILOTO_ENABLED": "true",
            "MINUTAS_PILOTO_EMAILS": ["admin@test.local"],
        },
        {
            "MINUTAS_PILOTO_ENABLED": True,
            "MINUTAS_PILOTO_EMAILS": "admin@test.local",
        },
        {"MINUTAS_PILOTO_ENABLED": True, "MINUTAS_PILOTO_EMAILS": ["invalido"]},
    )

    for secrets in invalid:
        assert can_access_minutas_piloto(TEST_IDENTITY, secrets) is False


def test_authorized_user_sees_and_opens_private_module(store, monkeypatch):
    secrets = {
        "MINUTAS_PILOTO_ENABLED": True,
        "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]],
    }
    app = _run_portal(store, monkeypatch, TEST_IDENTITY, secrets)

    navigation = app.sidebar.radio(key="portal_module")
    assert any(option.endswith("Minutas IA") for option in navigation.options)
    navigation.set_value("Minutas IA").run()

    assert not app.exception
    titles = [item.value for item in app.title]
    assert "Gerador de Minutas de Pareceres — Projeto-Piloto" in titles
    captions = "\n".join(item.value for item in app.caption)
    assert "Elvira Samara" in captions
    assert [tab.label for tab in app.tabs] == [
        "Biblioteca de Referências",
        "Gerar Minuta",
    ]
    assert any("ainda não está configurada" in item.value for item in app.warning)


def test_configured_library_shows_phase_2a_fields(store, monkeypatch):
    from services import minutas_ia_ui
    from services.minutas_library import SUBTIPOS

    class Repository:
        def __init__(self, _store):
            pass

        def list(self):
            return []

    class Storage:
        @classmethod
        def from_secrets(cls, _secrets):
            return cls()

    monkeypatch.setattr(minutas_ia_ui, "MinutasReferenciasStore", Repository)
    monkeypatch.setattr(minutas_ia_ui, "SupabaseMinutasStorage", Storage)
    secrets = {
        "MINUTAS_PILOTO_ENABLED": True,
        "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]],
    }
    app = _run_portal(store, monkeypatch, TEST_IDENTITY, secrets)
    app.sidebar.radio(key="portal_module").set_value("Minutas IA").run()

    assert not app.exception
    assert [item.label for item in app.file_uploader] == [
        "Relatório da Auditoria — PDF, até 10 MB *",
        "Parecer definitivo — PDF ou DOCX, até 10 MB *",
    ]
    assert all(item.proto.max_upload_size_mb == 10 for item in app.file_uploader)
    subtype = next(item for item in app.selectbox if item.label == "Subtipo de PCA *")
    assert list(subtype.options) == list(SUBTIPOS)
    assert any(item.label == "Cadastrar referência" for item in app.button)


def test_registered_reference_uses_compact_document_flow(store, monkeypatch):
    from services import minutas_ia_ui

    record = {
        "id": "11111111-1111-1111-1111-111111111111",
        "procuradora": "Elvira Samara",
        "numero_processo": "TC 00001/24",
        "exercicio": 2024,
        "subtipo": "Prefeitura",
        "auditoria_nome": "auditoria.pdf",
        "auditoria_mime": "application/pdf",
        "auditoria_tamanho": 100,
        "auditoria_path": "referencias/auditoria.pdf",
        "parecer_nome": "parecer.pdf",
        "parecer_mime": "application/pdf",
        "parecer_tamanho": 200,
        "parecer_path": "referencias/parecer.pdf",
    }
    document = {
        "id": "22222222-2222-2222-2222-222222222222",
        "referencia_id": record["id"],
        "tipo": "Relatório de Análise de Defesa",
        "descricao": "",
        "posicao": 7,
        "data_documento": None,
        "nome": "analise-defesa.pdf",
        "mime": "application/pdf",
        "tamanho": 300,
        "path": "referencias/analise-defesa.pdf",
    }

    class Repository:
        def __init__(self, _store):
            pass

        def list(self):
            return [record]

        def get(self, identifier):
            return record if identifier == record["id"] else None

        def list_documents(self, _identifier):
            return [document]

    class Storage:
        @classmethod
        def from_secrets(cls, _secrets):
            return cls()

    monkeypatch.setattr(minutas_ia_ui, "MinutasReferenciasStore", Repository)
    monkeypatch.setattr(minutas_ia_ui, "SupabaseMinutasStorage", Storage)
    secrets = {
        "MINUTAS_PILOTO_ENABLED": True,
        "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]],
    }
    app = _run_portal(store, monkeypatch, TEST_IDENTITY, secrets)
    app.sidebar.radio(key="portal_module").set_value("Minutas IA").run()

    assert not app.exception
    assert app.button(key=f"minutas_add_open_{record['id']}").label == (
        "+ Adicionar documento"
    )
    assert not any(
        item.key == f"minutas_add_file_{record['id']}"
        for item in app.file_uploader
    )
    assert all(item.proto.max_upload_size_mb == 10 for item in app.file_uploader)
    text = "\n".join(item.value for item in app.markdown)
    assert "auditoria.pdf" in text
    assert "analise-defesa.pdf" in text
    assert "parecer.pdf" in text
    assert app.button(key=f"minutas_promote_open_{document['id']}")

    app.button(key=f"minutas_add_open_{record['id']}").click().run()

    assert not app.exception
    complement_uploader = app.file_uploader(
        key=f"minutas_add_file_{record['id']}"
    )
    assert complement_uploader.proto.max_upload_size_mb == 10
    assert not any(
        item.key == f"minutas_add_date_{record['id']}" for item in app.date_input
    )
    assert not any(
        item.label == "Posição cronológica *" for item in app.number_input
    )
    assert any(
        "Classificação selecionada" in item.value for item in app.caption
    )

    app.checkbox(key=f"minutas_add_known_date_{record['id']}").check().run()

    assert app.date_input(key=f"minutas_add_date_{record['id']}")
    assert app.session_state["minutas_active_panel"] == f"add:{record['id']}"
    process_expander = next(
        item for item in app.expander if "TC 00001/24" in item.label
    )
    assert process_expander.proto.expanded is True

    app.button(key=f"minutas_add_cancel_{record['id']}").click().run()
    app.run()

    assert not app.exception
    assert "minutas_active_panel" not in app.session_state
    assert not any(
        item.key == f"minutas_add_file_{record['id']}"
        for item in app.file_uploader
    )

    app.button(key=f"minutas_edit_open_{document['id']}").click().run()

    assert not app.exception
    assert app.selectbox(key=f"minutas_edit_type_{document['id']}")
    assert any(
        "Classificação selecionada" in item.value for item in app.caption
    )
    assert not any(
        item.key == f"minutas_edit_date_{document['id']}"
        for item in app.date_input
    )
    app.button(key=f"minutas_edit_cancel_{document['id']}").click().run()
    app.run()

    app.button(key=f"minutas_promote_open_{document['id']}").click().run()

    assert not app.exception
    assert app.checkbox(key=f"minutas_promote_confirm_{document['id']}")
    assert "SEDH" not in (ROOT / "services" / "minutas_ia_ui.py").read_text(
        encoding="utf-8"
    )


def test_compact_order_controls_preserve_preexisting_position_gaps():
    from services.minutas_ia_ui import _reordered_values

    documents = [
        {
            "id": f"document-{index}",
            "tipo": "Cota Ministerial",
            "descricao": "",
            "posicao": position,
            "data_documento": None,
        }
        for index, position in enumerate((2, 7, 11))
    ]

    moved = _reordered_values(documents, 2, -1)

    assert [item["posicao"] for item in moved] == [2, 11, 7]
    assert sorted(item["posicao"] for item in moved) == [2, 7, 11]
    assert _reordered_values(documents, 0, -1) is None


def test_unauthorized_user_cannot_see_or_force_private_module(store, monkeypatch):
    identity = {**TEST_IDENTITY, "email": "outro@test.local", "name": "Outro usuário"}
    seed_access(store, email=identity["email"], nome=identity["name"])
    secrets = {
        "MINUTAS_PILOTO_ENABLED": True,
        "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]],
    }
    app = _run_portal(store, monkeypatch, identity, secrets)

    assert not any(
        option.endswith("Minutas IA")
        for option in app.sidebar.radio(key="portal_module").options
    )
    app.session_state[PORTAL_NAV_REQUEST] = {"module": "Minutas IA", "state": {}}
    app.run()

    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Início"
    titles = [item.value for item in app.title]
    assert "Gerador de Minutas de Pareceres — Projeto-Piloto" not in titles


def test_disabled_pilot_stays_hidden_and_preserves_existing_navigation(
    store, monkeypatch
):
    secrets = {
        "MINUTAS_PILOTO_ENABLED": False,
        "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]],
    }
    app = _run_portal(store, monkeypatch, TEST_IDENTITY, secrets)
    navigation = app.sidebar.radio(key="portal_module")

    assert not any(option.endswith("Minutas IA") for option in navigation.options)
    assert any(option.endswith("Portarias") for option in navigation.options)
    navigation.set_value("Portarias").run()
    assert not app.exception and not app.error
    assert app.sidebar.radio(key="portal_module").value == "Portarias"
