"""Sidebar returns open the module home; deep links still win."""

from inspect import getsource

import pytest
from streamlit.testing.v1 import AppTest

from database.store import ROOT
from portal import (
    MODULE_NAVIGATION_RESET,
    PORTAL_LAST_MODULE,
    PORTAL_NAV_REQUEST,
    enter_portal_module,
)
from tests.access_testing import enable_login

ALLOWED = (
    "Início",
    "Busca Global",
    "Pendências",
    "Portarias",
    "Agenda",
    "Ofícios",
    "Memorandos",
    "Tarefas",
    "Relatórios e Indicadores",
    "Representações",
    "Ouvidoria",
    "Administração",
)


def _enter(state, monkeypatch):
    import portal

    monkeypatch.setattr(portal.st, "session_state", state)
    return enter_portal_module(ALLOWED)


def test_reset_lists_are_explicit_keys():
    source = getsource(enter_portal_module) + getsource(
        __import__("portal")._reset_module_navigation
    )
    assert "session_state.clear" not in source
    assert "startswith" not in source
    assert "st.rerun" not in getsource(enter_portal_module)
    preserved = (
        "tarefas_q",
        "tarefas_form_newtitle",
        "rep_f_q",
        "rep_form_newtitulo",
        "ouvi_fq",
        "ouvi_f_file",
        "agenda_filter_member",
        "agenda_history_search",
        "agenda_analise_ia",
        "oficio_gabinete",
        "oficio_received_pdf_bytes",
        "pending_q",
        "alerts_period",
        "history_year",
        "editor_seed",
        "sistema_backup_running",
        "_tramita_previews",
        "_oficios_service",
        "agenda_store",
    )
    listed = {key for keys in MODULE_NAVIGATION_RESET.values() for key in keys}
    assert not listed.intersection(preserved)


def test_sidebar_return_clears_only_transient_screens(monkeypatch):
    state = {
        PORTAL_LAST_MODULE: "Início",
        "portal_module": "Tarefas",
        "tarefas_edit": {"id": 7},
        "tarefas_open_id": 7,
        "tarefas_new_origin": {"titulo": "rascunho"},
        "tarefas_q": "protocolo",
        "tarefas_active_page": 2,
        "tarefas_form_7title": "texto ainda aqui",
        "tarefas_analise_ia_resultado": {"texto": "briefing"},
    }
    assert _enter(state, monkeypatch) is None
    assert "tarefas_edit" not in state
    assert "tarefas_open_id" not in state
    assert "tarefas_new_origin" not in state
    assert state["tarefas_q"] == "protocolo"
    assert state["tarefas_active_page"] == 2
    assert state["tarefas_form_7title"] == "texto ainda aqui"
    assert state["tarefas_analise_ia_resultado"]["texto"] == "briefing"


def test_internal_rerun_keeps_the_open_screen(monkeypatch):
    state = {
        PORTAL_LAST_MODULE: "Representações",
        "portal_module": "Representações",
        "representacoes_edit": {"id": 3},
        "rep_f_q": "tema",
        "rep_form_3titulo": "minuta",
    }
    _enter(state, monkeypatch)
    assert state["representacoes_edit"] == {"id": 3}
    assert state["rep_f_q"] == "tema"
    assert state["rep_form_3titulo"] == "minuta"


def test_same_module_deep_link_replaces_incompatible_screen(monkeypatch):
    state = {
        PORTAL_LAST_MODULE: "Representações",
        "portal_module": "Representações",
        "representacoes_edit": {"id": 3},
        "representacoes_protocol": 3,
        "rep_f_q": "tema",
        PORTAL_NAV_REQUEST: {
            "module": "Representações",
            "state": {"representacoes_view": 9},
        },
    }
    assert _enter(state, monkeypatch) == "Representações"
    assert PORTAL_NAV_REQUEST not in state
    assert state["representacoes_view"] == 9
    assert "representacoes_edit" not in state
    assert "representacoes_protocol" not in state
    assert state["rep_f_q"] == "tema"


@pytest.mark.parametrize(
    ("module", "previous_screen", "request_state", "kept", "gone"),
    [
        (
            "Ouvidoria",
            {"ouvidoria_edit": {}, "ouvidoria_action": 4, "ouvi_fq": "ouvi"},
            {"ouvidoria_open_id": 15},
            {"ouvi_fq": "ouvi"},
            ("ouvidoria_edit", "ouvidoria_action"),
        ),
        (
            "Agenda",
            {
                "agenda_edit": {"id": "velho"},
                "agenda_leave_edit": {"id": "af"},
                "agenda_view": "Semana",
                "agenda_filter_member": 2,
                "agenda_analise_ia": {"texto": "ok"},
            },
            {"pending_open_agenda": "ag-novo"},
            {
                "agenda_view": "Semana",
                "agenda_filter_member": 2,
                "agenda_analise_ia": {"texto": "ok"},
                "pending_open_agenda": "ag-novo",
            },
            ("agenda_edit", "agenda_leave_edit"),
        ),
        (
            "Ofícios",
            {
                "oficio_edit": "old",
                "oficio_detail": "old",
                "oficio_open_ids": {"old"},
                "oficio_page": "Novo Ofício",
                "oficio_gabinete": "PROGE",
                "oficio_received_pdf_bytes": b"pdf",
            },
            {
                "pending_open_oficio": {
                    "id": "novo",
                    "gabinete": "PROGE",
                    "page": "Recebidos",
                }
            },
            {
                "oficio_gabinete": "PROGE",
                "oficio_received_pdf_bytes": b"pdf",
                "pending_open_oficio": {
                    "id": "novo",
                    "gabinete": "PROGE",
                    "page": "Recebidos",
                },
            },
            ("oficio_edit", "oficio_detail", "oficio_open_ids"),
        ),
    ],
)
def test_same_module_deep_link_for_record_modules(
    monkeypatch, module, previous_screen, request_state, kept, gone
):
    state = {
        PORTAL_LAST_MODULE: module,
        "portal_module": module,
        PORTAL_NAV_REQUEST: {"module": module, "state": request_state},
        **previous_screen,
    }
    assert _enter(state, monkeypatch) == module
    for key in gone:
        assert key not in state
    for key, value in kept.items():
        assert state[key] == value


def test_clean_entry_normalizes_home_without_touching_filters(monkeypatch):
    state = {
        PORTAL_LAST_MODULE: "Início",
        "portal_module": "Agenda",
        "agenda_edit": {"id": "x"},
        "agenda_section": "Histórico",
        "agenda_view": "Mês",
        "agenda_history_search": "julho",
        "agenda_pdf_export": {"ok": True},
    }
    _enter(state, monkeypatch)
    assert "agenda_edit" not in state
    assert state["agenda_section"] == "Agenda"
    assert state["agenda_view"] == "Hoje"
    assert state["agenda_history_search"] == "julho"
    assert state["agenda_pdf_export"] == {"ok": True}

    state = {
        PORTAL_LAST_MODULE: "Agenda",
        "portal_module": "Ofícios",
        "oficio_detail": "of-1",
        "oficio_page": "Recebidos",
        "oficio_gabinete": "PROGE",
        "oficio_gabinete_member": 4,
        "oficio_received_text": "extraído",
    }
    _enter(state, monkeypatch)
    assert "oficio_detail" not in state
    assert state["oficio_page"] == "Visão Geral"
    assert state["oficio_gabinete"] == "PROGE"
    assert state["oficio_gabinete_member"] == 4
    assert state["oficio_received_text"] == "extraído"

    state = {
        PORTAL_LAST_MODULE: "Ofícios",
        "portal_module": "Memorandos",
        "memorandos_nav": "Histórico",
        "memo_engagement_open": "m1",
        "memorando_id": "draft",
        "memorando_preview": {"id": "draft"},
    }
    _enter(state, monkeypatch)
    assert state["memorandos_nav"] == "Visão Geral"
    assert "memo_engagement_open" not in state
    assert state["memorando_id"] == "draft"
    assert state["memorando_preview"]["id"] == "draft"

    state = {
        PORTAL_LAST_MODULE: "Memorandos",
        "portal_module": "Portarias",
        "nav": "Histórico",
        "history_open": "port-1",
        "next_nav": "Histórico",
        "last_finalized": "port-1",
        "history_year": 2026,
        "history_page": 3,
        "editor_seed": {"data": "2026-01-01"},
        "editor_id": "port-1",
    }
    _enter(state, monkeypatch)
    assert state["nav"] == "Nova Portaria"
    assert "history_open" not in state
    assert "next_nav" not in state
    assert "last_finalized" not in state
    assert state["history_year"] == 2026
    assert state["history_page"] == 3
    assert state["editor_seed"]["data"] == "2026-01-01"
    assert state["editor_id"] == "port-1"

    state = {
        PORTAL_LAST_MODULE: "Portarias",
        "portal_module": "Administração",
        "admin_secao": "Sistema",
        "sistema_backup_running": True,
        "sistema_health": {"ok": True},
    }
    _enter(state, monkeypatch)
    assert state["admin_secao"] == "Usuários"
    assert state["sistema_backup_running"] is True
    assert state["sistema_health"]["ok"] is True


def test_portarias_pagination_is_not_a_new_entry(monkeypatch):
    state = {
        PORTAL_LAST_MODULE: "Portarias",
        "portal_module": "Portarias",
        "nav": "Histórico",
        "history_open": "port-9",
        "history_page": 2,
        "preview": {"intro": "prévia"},
    }
    _enter(state, monkeypatch)
    assert state["nav"] == "Histórico"
    assert state["history_open"] == "port-9"
    assert state["history_page"] == 2
    assert state["preview"]["intro"] == "prévia"


def test_deep_link_from_another_module_is_applied_after_reset(monkeypatch):
    state = {
        PORTAL_LAST_MODULE: "Pendências",
        "portal_module": "Pendências",
        "tarefas_edit": {"titulo": "antiga"},
        "pending_q": "prazo",
        PORTAL_NAV_REQUEST: {
            "module": "Tarefas",
            "state": {"tarefas_open_id": 12},
        },
    }
    assert _enter(state, monkeypatch) == "Tarefas"
    assert state["portal_module"] == "Tarefas"
    assert state["tarefas_open_id"] == 12
    assert "tarefas_edit" not in state
    assert state["pending_q"] == "prazo"


def test_admin_pending_link_overrides_the_clean_section(monkeypatch):
    state = {
        PORTAL_LAST_MODULE: "Pendências",
        "portal_module": "Pendências",
        "admin_secao": "Solicitações",
        PORTAL_NAV_REQUEST: {
            "module": "Administração",
            "state": {
                "pending_open_admin": {"secao": "Sistema", "aba": "Saúde"},
            },
        },
    }
    _enter(state, monkeypatch)
    assert state["portal_module"] == "Administração"
    assert state["pending_open_admin"]["aba"] == "Saúde"
    from services.access_ui import consume_pending_open_admin
    import services.access_ui as access_ui

    monkeypatch.setattr(access_ui.st, "session_state", state)
    consume_pending_open_admin()
    assert "pending_open_admin" not in state
    assert state["admin_secao"] == "Sistema"
    assert state["admin_sistema_aba"] == "Saúde"


def test_alerts_overlay_is_not_a_module_change(monkeypatch):
    state = {
        PORTAL_LAST_MODULE: "Tarefas",
        "portal_module": "Tarefas",
        "tarefas_edit": {"id": 4},
        "tarefas_q": "hoje",
        "portal_alerts_request": True,
        "portal_special_view": "alerts",
    }
    _enter(state, monkeypatch)
    assert state["tarefas_edit"] == {"id": 4}
    assert state["tarefas_q"] == "hoje"
    assert state["portal_special_view"] == "alerts"


def test_first_paint_does_not_wipe_a_screen_already_in_session(monkeypatch):
    state = {"portal_module": "Tarefas", "tarefas_edit": {"id": 1}}
    _enter(state, monkeypatch)
    assert state["tarefas_edit"] == {"id": 1}


def _app(store, monkeypatch):
    enable_login(monkeypatch, store)
    monkeypatch.setattr("database.store.Store", lambda: store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception
    return app


def _away_and_back(app, module):
    app.sidebar.radio(key="portal_module").set_value("Início").run()
    assert not app.exception
    app.sidebar.radio(key="portal_module").set_value(module).run()
    assert not app.exception
    return app


def test_sidebar_return_opens_tarefas_listing(store, monkeypatch):
    app = _app(store, monkeypatch)
    app.sidebar.radio(key="portal_module").set_value("Tarefas").run()
    app.text_input(key="tarefas_q").set_value("protocolo").run()
    app.session_state["tarefas_edit"] = {}
    app.session_state["tarefas_form_newtitle"] = "rascunho digitado"
    app = _away_and_back(app, "Tarefas")
    assert "tarefas_edit" not in app.session_state
    assert app.text_input(key="tarefas_q").value == "protocolo"
    assert app.session_state["tarefas_form_newtitle"] == "rascunho digitado"
    assert not any("Nova tarefa" in str(item.value) for item in app.subheader)
    app.run()
    assert "tarefas_edit" not in app.session_state


def test_sidebar_return_opens_representacoes_listing(store, monkeypatch):
    app = _app(store, monkeypatch)
    app.sidebar.radio(key="portal_module").set_value("Representações").run()
    app.text_input(key="rep_f_q").set_value("relator").run()
    app.session_state["representacoes_view"] = 99999
    app.session_state["representacoes_edit"] = {}
    app.session_state["rep_form_newtitulo"] = "minuta"
    app = _away_and_back(app, "Representações")
    assert "representacoes_view" not in app.session_state
    assert "representacoes_edit" not in app.session_state
    assert app.text_input(key="rep_f_q").value == "relator"
    assert app.session_state["rep_form_newtitulo"] == "minuta"
    assert not any(
        "projeto de Representação" in str(item.value) for item in app.subheader
    )


def test_sidebar_return_opens_agenda_home(store, monkeypatch):
    app = _app(store, monkeypatch)
    app.sidebar.radio(key="portal_module").set_value("Agenda").run()
    app.session_state["agenda_edit"] = {}
    app.session_state["agenda_section"] = "Histórico"
    app.session_state["agenda_view"] = "Mês"
    app.session_state["agenda_history_search"] = "julho"
    app.session_state["agenda_form_new_type"] = "REUNIAO"
    app = _away_and_back(app, "Agenda")
    assert "agenda_edit" not in app.session_state
    assert app.session_state["agenda_section"] == "Agenda"
    assert app.session_state["agenda_view"] == "Hoje"
    assert app.session_state["agenda_history_search"] == "julho"
    assert app.session_state["agenda_form_new_type"] == "REUNIAO"
    assert not any("Novo compromisso" in str(item.value) for item in app.subheader)


def test_sidebar_return_opens_oficios_overview(store, monkeypatch):
    app = _app(store, monkeypatch)
    app.sidebar.radio(key="portal_module").set_value("Ofícios").run()
    app.button(key="gabinete_PROGE").click().run()
    assert not app.exception
    app.session_state["oficio_page"] = "Recebidos"
    app.session_state["oficio_detail"] = "of-1"
    app.session_state["oficio_open_ids"] = {"of-1"}
    app.session_state["oficio_edit"] = "of-1"
    app.session_state["oficio_received_pdf_bytes"] = b"%PDF"
    gabinete = app.session_state["oficio_gabinete"]
    app = _away_and_back(app, "Ofícios")
    assert app.session_state["oficio_gabinete"] == gabinete
    assert app.session_state["oficio_page"] == "Visão Geral"
    assert "oficio_detail" not in app.session_state
    assert "oficio_open_ids" not in app.session_state
    assert "oficio_edit" not in app.session_state
    assert app.session_state["oficio_received_pdf_bytes"] == b"%PDF"
    assert app.radio(key="oficio_page").value == "Visão Geral"


def test_sidebar_return_opens_ouvidoria_listing(store, monkeypatch):
    app = _app(store, monkeypatch)
    app.sidebar.radio(key="portal_module").set_value("Ouvidoria").run()
    app.text_input(key="ouvi_fq").set_value("manifestante").run()
    app.session_state["ouvidoria_view"] = 99999
    app.session_state["ouvidoria_edit"] = {}
    app = _away_and_back(app, "Ouvidoria")
    assert "ouvidoria_view" not in app.session_state
    assert "ouvidoria_edit" not in app.session_state
    assert app.text_input(key="ouvi_fq").value == "manifestante"
    assert not any(
        "Ouvidoria" in str(item.value) and "Nova" in str(item.value)
        for item in app.subheader
    )


def test_sidebar_return_opens_nova_portaria(store, monkeypatch):
    app = _app(store, monkeypatch)
    app.sidebar.radio(key="portal_module").set_value("Portarias").run()
    app.radio(key="nav").set_value("Histórico").run()
    app.session_state["history_open"] = "port-1"
    app.session_state["history_year"] = 2026
    app.session_state["editor_seed"] = {"data": "2026-02-02"}
    app.session_state["last_finalized"] = "port-1"
    app = _away_and_back(app, "Portarias")
    assert app.radio(key="nav").value == "Nova Portaria"
    assert "history_open" not in app.session_state
    assert "last_finalized" not in app.session_state
    assert app.session_state["history_year"] == 2026
    assert app.session_state["editor_seed"]["data"] == "2026-02-02"
    app.radio(key="nav").set_value("Histórico").run()
    assert app.radio(key="nav").value == "Histórico"
    assert "history_open" not in app.session_state


def test_alerts_overlay_keeps_the_anchor_screen(store, monkeypatch):
    from portal import PORTAL_ALERTS_REQUEST

    app = _app(store, monkeypatch)
    app.sidebar.radio(key="portal_module").set_value("Tarefas").run()
    app.session_state["tarefas_edit"] = {}
    app.session_state["tarefas_q"] = "hoje"
    app.session_state[PORTAL_ALERTS_REQUEST] = True
    app.run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Tarefas"
    assert any("ALERTAS" in str(item.value) for item in app.subheader)
    assert app.session_state["tarefas_edit"] == {}
    assert app.session_state["tarefas_q"] == "hoje"
    app.button(key="alerts_back").click().run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Tarefas"
    assert app.session_state["tarefas_edit"] == {}
    assert any("Nova tarefa" in str(item.value) for item in app.subheader)


def test_app_same_module_deep_links_replace_the_open_screen(store, monkeypatch):
    from database.agenda import AgendaStore
    from database.tarefas import TarefasStore
    from database.access import AccessStore
    from tests.access_testing import TEST_IDENTITY
    from tests.test_agenda import draft

    owner = AccessStore(store).get_by_email(TEST_IDENTITY["email"])
    task = TarefasStore(store).create(owner["id"], {"titulo": "Tarefa do atalho"})
    appointment = AgendaStore(store).save(draft())
    app = _app(store, monkeypatch)

    app.sidebar.radio(key="portal_module").set_value("Representações").run()
    app.session_state["representacoes_edit"] = {}
    app.session_state["rep_f_q"] = "tema"
    app.session_state[PORTAL_NAV_REQUEST] = {
        "module": "Representações",
        "state": {"representacoes_view": 99999},
    }
    app.run()
    assert not app.exception
    assert "representacoes_edit" not in app.session_state
    assert app.session_state["rep_f_q"] == "tema"
    assert any("não encontrado" in str(item.value).lower() for item in app.warning)

    app.sidebar.radio(key="portal_module").set_value("Ouvidoria").run()
    app.session_state["ouvidoria_edit"] = {}
    app.session_state["ouvidoria_action"] = 4
    app.session_state["ouvi_fq"] = "busca"
    app.session_state[PORTAL_NAV_REQUEST] = {
        "module": "Ouvidoria",
        "state": {"ouvidoria_open_id": 99999},
    }
    app.run()
    assert not app.exception
    assert "ouvidoria_edit" not in app.session_state
    assert "ouvidoria_action" not in app.session_state
    assert app.session_state["ouvi_fq"] == "busca"
    assert any("não encontrado" in str(item.value).lower() for item in app.warning)

    app.sidebar.radio(key="portal_module").set_value("Agenda").run()
    app.session_state["agenda_edit"] = {"id": "velho", "situacao": "Agendado"}
    app.session_state["agenda_view"] = "Semana"
    app.session_state["agenda_filter_type"] = None
    app.session_state[PORTAL_NAV_REQUEST] = {
        "module": "Agenda",
        "state": {"pending_open_agenda": appointment},
    }
    app.run()
    assert not app.exception
    assert app.session_state["agenda_edit"]["id"] == appointment
    assert app.session_state["agenda_view"] == "Semana"

    app.sidebar.radio(key="portal_module").set_value("Ofícios").run()
    app.button(key="gabinete_PROGE").click().run()
    app.session_state["oficio_edit"] = "antigo"
    app.session_state["oficio_page"] = "Novo Ofício"
    app.session_state["oficio_received_pdf_bytes"] = b"pdf"
    app.session_state[PORTAL_NAV_REQUEST] = {
        "module": "Ofícios",
        "state": {
            "pending_open_oficio": {
                "id": "of-destino",
                "gabinete": "PROGE",
                "page": "Recebidos",
            }
        },
    }
    app.run()
    assert not app.exception
    assert "oficio_edit" not in app.session_state
    assert app.session_state["oficio_detail"] == "of-destino"
    assert app.session_state["oficio_page"] == "Recebidos"
    assert app.session_state["oficio_gabinete"] == "PROGE"
    assert app.session_state["oficio_received_pdf_bytes"] == b"pdf"

    app.session_state["portal_alerts_request"] = True
    app.run()
    assert any("CENTRAL DE ALERTAS" in str(item.value) for item in app.subheader)
    app.button(key="alerts_back").click().run()
    app.session_state["tarefas_edit"] = {"titulo": "outra"}
    app.session_state[PORTAL_NAV_REQUEST] = {
        "module": "Tarefas",
        "state": {"tarefas_open_id": task["id"]},
    }
    app.run()
    assert not app.exception
    assert app.sidebar.radio(key="portal_module").value == "Tarefas"
    assert any("Tarefa do atalho" in str(item.value) for item in app.text_input)
