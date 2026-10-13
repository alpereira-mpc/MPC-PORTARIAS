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
    subtype = next(item for item in app.selectbox if item.label == "Subtipo de PCA *")
    assert list(subtype.options) == list(SUBTIPOS)
    assert any(item.label == "Cadastrar referência" for item in app.button)


def test_registered_reference_renders_complement_section(store, monkeypatch):
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

    class Repository:
        def __init__(self, _store):
            pass

        def list(self):
            return [record]

        def get(self, identifier):
            return record if identifier == record["id"] else None

        def list_documents(self, _identifier):
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
    labels = [item.label for item in app.file_uploader]
    assert "Documento — PDF ou DOCX, até 10 MB *" in labels
    assert any(item.label == "Adicionar documento" for item in app.button)
    assert any(
        "Nenhum documento complementar" in item.value for item in app.markdown
    )


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
