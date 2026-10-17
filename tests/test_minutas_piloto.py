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


def test_access_accepts_any_verified_identity_and_ignores_the_old_email_list():
    secrets = {
        "MINUTAS_PILOTO_ENABLED": False,
        "MINUTAS_PILOTO_EMAILS": ["ADMIN@Test.Local"],
    }

    assert can_access_minutas_piloto(TEST_IDENTITY, secrets) is True
    assert can_access_minutas_piloto(
        {**TEST_IDENTITY, "email": "outro@test.local"}, secrets
    ) is True
    assert can_access_minutas_piloto(
        {**TEST_IDENTITY, "email_verified": False}, secrets
    ) is False
    assert can_access_minutas_piloto(None, secrets) is False
    assert can_access_minutas_piloto({"email": "invalido", "email_verified": True}, secrets) is False


def test_access_does_not_depend_on_pilot_secrets():
    for secrets in (
        {},
        {"MINUTAS_PILOTO_ENABLED": True},
        {"MINUTAS_PILOTO_ENABLED": "true", "MINUTAS_PILOTO_EMAILS": ["admin@test.local"]},
        {"MINUTAS_PILOTO_ENABLED": True, "MINUTAS_PILOTO_EMAILS": "admin@test.local"},
        {"MINUTAS_PILOTO_ENABLED": True, "MINUTAS_PILOTO_EMAILS": ["invalido"]},
    ):
        assert can_access_minutas_piloto(TEST_IDENTITY, secrets) is True


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
    assert "MINUTAS IA" in titles
    captions = "\n".join(item.value for item in app.caption)
    assert "Selecione o Procurador" in captions
    assert any(item.key == "minutas_procurador_ESPO" for item in app.button)
    assert any(item.key == "minutas_procurador_LAF" for item in app.button)
    assert len([item for item in app.button if str(item.key or "").startswith("minutas_procurador_")]) == 7
    assert not any(item.key == "minutas_active_view" for item in app.radio)


def test_cada_procurador_abre_especialidades_e_a_troca_volta_a_selecao(monkeypatch):
    from services import minutas_ia_ui
    from services import minutas_specialties_ui
    from services.minutas_procuradores import PROCURADORES

    class Acervo:
        def __init__(self, _library):
            pass

        @staticmethod
        def catalog():
            from services.minutas_specialties import CATALOG
            return list(CATALOG)

    monkeypatch.setattr(minutas_specialties_ui, "MinutasAcervoService", Acervo)
    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args, **_kwargs: object())

    def page():
        from services import minutas_ia_ui as ui
        ui.render(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    for pessoa in PROCURADORES:
        app.button(key="minutas_procurador_" + pessoa.sigla).click().run()
        assert not app.exception
        assert any(pessoa.nome in item.value for item in app.title)
        assert any(item.key == "minutas_category_PCA" for item in app.button)
        assert any(item.key == "minutas_category_DENUNCIAS" for item in app.button)
        assert any(item.key == "minutas_category_RECURSOS" for item in app.button)
        app.button(key="minutas_trocar_procurador").click().run()
        assert any(item.value == "MINUTAS IA" for item in app.title)


def test_module_navigation_executes_only_the_active_view(monkeypatch):
    from services import minutas_ia_ui
    from services import minutas_specialties_ui
    from services.minutas_specialties import scope, RPPS

    calls = []
    monkeypatch.setattr(
        minutas_specialties_ui, "acervo", lambda *_args: calls.append("library")
    )
    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(minutas_specialties_ui, "selection", lambda *_args: scope(RPPS))
    monkeypatch.setattr(minutas_specialties_ui, "synchronize_scope", lambda *_args: None)
    monkeypatch.setattr(
        minutas_ia_ui, "_search_tab", lambda *_args: calls.append("search")
    )
    monkeypatch.setattr(
        minutas_ia_ui, "_generator_tab", lambda *_args: calls.append("generator")
    )

    def page():
        import streamlit as st
        from services import minutas_ia_ui as ui

        st.session_state["minutas_procurador"] = "ESPO"
        ui.render(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    assert calls == ["library"]

    app.radio(key="minutas_active_view").set_value("Pesquisa Jurídica").run()
    assert calls == ["library", "search"]
    app.radio(key="minutas_active_view").set_value("Gerar Minuta de Parecer").run()
    assert calls == ["library", "search", "generator"]
    app.radio(key="minutas_active_view").set_value("Acervo").run()
    assert calls == ["library", "search", "generator", "library"]


def test_troca_de_procurador_volta_a_selecao_e_limpa_transitorio(monkeypatch):
    from services import minutas_ia_ui
    from services import minutas_specialties_ui

    class Acervo:
        def __init__(self, _library):
            pass

        @staticmethod
        def catalog():
            from services.minutas_specialties import CATALOG
            return list(CATALOG)

    monkeypatch.setattr(minutas_specialties_ui, "MinutasAcervoService", Acervo)
    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args, **_kwargs: object())

    def page():
        from services import minutas_ia_ui as ui
        ui.render(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    app.button(key="minutas_procurador_MTTF").click().run()
    assert not app.exception
    assert any("Marcílio Toscano Franca Filho" in item.value for item in app.title)
    app.session_state["minutas_specialty"] = "elvira.denuncias"
    app.session_state["minutas_generation_result_demo"] = {"resposta": "transitória"}
    app.button(key="minutas_trocar_procurador").click().run()
    assert any(item.value == "MINUTAS IA" for item in app.title)

    def ausente(chave):
        try:
            app.session_state[chave]
        except KeyError:
            return True
        return False

    assert ausente("minutas_specialty")
    assert ausente("minutas_generation_result_demo")
    assert ausente("minutas_procurador")


def test_camaras_so_abre_geracao_quando_habilitada(monkeypatch):
    from services import minutas_ia_ui
    from services import minutas_specialties_ui
    from services.minutas_specialties import (
        CAMARAS, SHARED_INSTRUCTION_MOTOR, scope,
    )

    calls = []
    profile = dict(scope(CAMARAS), motor=SHARED_INSTRUCTION_MOTOR, estado="Em testes")
    monkeypatch.setattr(minutas_specialties_ui, "acervo", lambda *_args: calls.append("library"))
    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(minutas_specialties_ui, "selection", lambda *_args: profile)
    monkeypatch.setattr(minutas_specialties_ui, "synchronize_scope", lambda *_args: None)
    monkeypatch.setattr(minutas_ia_ui, "_search_tab", lambda *_args: calls.append("search"))
    monkeypatch.setattr(minutas_ia_ui, "_generator_tab", lambda *_args: calls.append("generator"))

    def page():
        import streamlit as st
        from services import minutas_ia_ui as ui
        st.session_state["minutas_procurador"] = "ESPO"
        ui.render(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    app.radio(key="minutas_active_view").set_value("Gerar Minuta de Parecer").run()
    assert calls[-1] == "generator"
    profile["motor"] = ""
    profile["estado"] = "A iniciar"
    calls.clear()
    app.run()
    assert "generator" not in calls


def test_configured_library_shows_phase_2a_fields(store, monkeypatch):
    from services import minutas_ia_ui
    from services.minutas_library import SUBTIPOS

    class Repository:
        def __init__(self, _store, **_kwargs):
            pass

        def list(self):
            return []

    class Storage:
        @classmethod
        def from_secrets(cls, _secrets):
            return cls()

    monkeypatch.setattr(minutas_ia_ui, "MinutasReferenciasStore", Repository)
    monkeypatch.setattr(minutas_ia_ui, "SupabaseMinutasStorage", Storage)
    # Keep coverage of the legacy paired-file component independently of the
    # new specialized navigation, which has dedicated tests.
    monkeypatch.setattr(minutas_ia_ui, "render", minutas_ia_ui._library_tab)
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
    monkeypatch.setattr(minutas_ia_ui, "render", minutas_ia_ui._library_tab)

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
    extraction = {
        "arquivo_path": record["auditoria_path"],
        "arquivo_nome": record["auditoria_nome"],
        "arquivo_mime": record["auditoria_mime"],
        "tipo": "Relatório da Auditoria",
        "status": "PROCESSADO",
        "texto_integral": "Texto integral da auditoria",
        "unidades_json": (
            '[{"pagina": 1, "texto": "Texto da página 1\\nSegunda linha"}]'
        ),
        "secoes_json": "[]",
        "erro": "",
    }

    class Repository:
        def __init__(self, _store, **_kwargs):
            pass

        def list(self):
            return [record]

        def get(self, identifier):
            return record if identifier == record["id"] else None

        def list_documents(self, _identifier):
            return [document]

        def list_extractions(self, _identifier):
            return [extraction]

        def get_extraction(self, path):
            return extraction if path == extraction["arquivo_path"] else None

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
    captions = "\n".join(item.value for item in app.caption)
    assert "Extração: Processado" in captions
    assert "Extração: Pendente" in captions
    assert app.button(key=f"minutas_extract_process_{record['id']}")

    app.button(key=f"minutas_extract_view_open_{record['id']}_auditoria").click().run()

    assert not app.exception
    assert app.selectbox(key=f"minutas_extract_page_{record['id']}_auditoria")
    extracted_text = next(
        item for item in app.code if "Texto da página 1" in item.value
    )
    assert extracted_text.value == "Texto da página 1\nSegunda linha"
    assert extracted_text.proto.language == "plaintext"
    assert extracted_text.proto.wrap_lines is True
    assert not app.text_area
    app.button(key=f"minutas_extract_view_close_{record['id']}_auditoria").click().run()
    app.run()

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


def test_authenticated_user_outside_the_old_email_list_opens_minutas(store, monkeypatch):
    identity = {**TEST_IDENTITY, "email": "outro@test.local", "name": "Outro usuário"}
    seed_access(
        store, email=identity["email"], nome=identity["name"], perfil="USUARIO", pode_admin=False,
    )
    secrets = {
        "MINUTAS_PILOTO_ENABLED": False,
        "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]],
    }
    app = _run_portal(store, monkeypatch, identity, secrets)

    navigation = app.sidebar.radio(key="portal_module")
    assert any(option.endswith("Minutas IA") for option in navigation.options)
    navigation.set_value("Minutas IA").run()
    assert not app.exception
    assert "MINUTAS IA" in [item.value for item in app.title]
    assert not any(item.key == "minutas_initial_administration_open" for item in app.button)


def test_unverified_identity_cannot_open_minutas(store, monkeypatch):
    identity = {**TEST_IDENTITY, "email_verified": False}
    secrets = {"MINUTAS_PILOTO_ENABLED": True, "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]]}
    app = _run_portal(store, monkeypatch, identity, secrets)
    assert not any(
        option.endswith("Minutas IA")
        for option in app.sidebar.radio(key="portal_module").options
    )


def test_disabled_pilot_flag_no_longer_hides_the_module(store, monkeypatch):
    secrets = {
        "MINUTAS_PILOTO_ENABLED": False,
        "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]],
    }
    app = _run_portal(store, monkeypatch, TEST_IDENTITY, secrets)
    navigation = app.sidebar.radio(key="portal_module")

    assert any(option.endswith("Minutas IA") for option in navigation.options)
    assert any(option.endswith("Portarias") for option in navigation.options)
    navigation.set_value("Portarias").run()
    assert not app.exception and not app.error
    assert app.sidebar.radio(key="portal_module").value == "Portarias"
