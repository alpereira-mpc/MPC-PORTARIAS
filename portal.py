"""Lightweight portal navigation. Modules are initialized only after selection."""

from dataclasses import dataclass
from pathlib import Path
import base64
import logging

import streamlit as st

from services.branding import (
    APP_NAME,
    MODULE_ICONS,
    SIDEBAR_LOGO,
    render_app_identity,
    render_institutional_header,
    render_sidebar_brand,
)
from services.ui_store import asset
from services.ui_theme import apply_theme, badge, empty_state, institutional_card_mark, render_html
from services.themes import THEME_LABELS, valid_theme
from services.versioning import APP_VERSION

LOGGER = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent

PORTAL_NAV_REQUEST = "portal_navigation_request"
PORTAL_ALERTS_REQUEST = "portal_alerts_request"
PORTAL_SPECIAL_VIEW = "portal_special_view"
PORTAL_SPECIAL_RETURN = "portal_special_return"
PORTAL_SPECIAL_ANCHOR = "portal_special_anchor"
ALERTS_VIEW = "alerts"
PORTAL_NAV_STATE_KEYS = frozenset(
    {
        "nav",
        "memorandos_nav",
        "pending_open_oficio",
        "pending_open_agenda",
        "pending_open_memorando",
        "pending_open_admin",
        "tarefas_open_id",
        "tarefas_new_origin",
        "representacoes_view",
        "ouvidoria_open_id",
        "portaria_open_id",
    }
)


def _session_get(key, default=None):
    state = st.session_state
    return state[key] if key in state else default


@st.fragment
def _render_module_fragment(renderer, store, principal):
    """Keep the portal shell stable during interactions inside one module."""
    renderer(store, principal)


def _active_theme(identity):
    if identity is None:
        return "dourado"
    email = identity["email"]
    cached = _session_get("_portal_theme")
    if cached and cached.get("email") == email:
        return valid_theme(cached.get("name"))
    try:
        from database.access import AccessStore

        saved = AccessStore(_application_store()).get_theme_by_email(email)
    except Exception:
        LOGGER.exception("Falha ao carregar preferência visual")
        st.session_state["_portal_theme"] = {"email": email, "name": "vermelho"}
        return "vermelho"
    name = valid_theme(saved)
    st.session_state["_portal_theme"] = {"email": email, "name": name}
    return name


def _change_theme(store, user_id, email, widget_key):
    chosen = valid_theme(st.session_state[widget_key])
    current = _session_get("_portal_theme", {"name": "vermelho"})["name"]
    if chosen == current:
        return
    try:
        from database.access import AccessStore

        AccessStore(store).set_theme(user_id, chosen)
    except Exception:
        LOGGER.exception("Falha ao salvar preferência visual")
        st.session_state[widget_key] = current
        st.session_state["_portal_theme_error"] = (
            "Não foi possível salvar o tema. O tema anterior foi mantido."
        )
        return
    st.session_state["_portal_theme"] = {"email": email, "name": chosen}


@dataclass(frozen=True)
class Module:
    key: str
    label: str
    title: str
    description: str
    icon: str
    active: bool = False


MODULES = (
    Module(
        "portarias",
        "Portarias",
        "Gerador de Portarias PROGE",
        "Elaboração, numeração, geração e gerenciamento de Portarias de substituição.",
        MODULE_ICONS["portarias"],
        True,
    ),
    Module(
        "memorandos",
        "Memorandos",
        "Memorandos de Substituição",
        "Geração e gerenciamento de memorandos relacionados às substituições de servidores.",
        MODULE_ICONS["memorandos"],
        True,
    ),
    Module(
        "oficios",
        "Ofícios",
        "Ofícios — Geração e Controle",
        "Geração, registro e acompanhamento de ofícios enviados e recebidos.",
        MODULE_ICONS["oficios"],
        True,
    ),
    Module(
        "agenda",
        "Agenda",
        "Agenda e Afastamentos dos Procuradores",
        "Organização de eventos, reuniões, despachos, afastamentos e substituições dos procuradores.",
        MODULE_ICONS["agenda"],
        True,
    ),
    Module(
        "tarefas",
        "Tarefas",
        "Tarefas",
        "Organização pessoal de demandas, prazos e prioridades.",
        MODULE_ICONS["tarefas"],
        True,
    ),
    Module(
        "relatorios",
        "Relatórios",
        "Relatórios e Indicadores",
        "Acompanhamento da movimentação e do estoque processual do MPC-PB.",
        MODULE_ICONS["relatorios"],
        True,
    ),
    Module(
        "representacoes",
        "Representações",
        "Representações",
        "Acompanhamento interno das Representações do MPC-PB, da elaboração ao encerramento processual.",
        MODULE_ICONS["representacoes"],
        True,
    ),
    Module(
        "ouvidoria",
        "Ouvidoria",
        "Ouvidoria",
        "Registro interno de notícias de fato da Ouvidoria do MPC-PB, da triagem às providências.",
        MODULE_ICONS["ouvidoria"],
        True,
    ),
)

ADMIN_MODULE = Module(
    "admin",
    "Administração",
    "Usuários e Acessos",
    "Cadastro de contas autorizadas, módulos e gabinetes de Ofícios.",
    MODULE_ICONS["admin"],
    True,
)


def queue_portal_navigation(module, **state):
    """Enqueue a module change. Safe inside on_click/on_change.

    Only mutate transient session state here. Streamlit already reruns after a
    callback; invoking the rerun API inside one is a no-op and surfaces a warning.
    """
    payload = {"module": module, "state": dict(state)}
    st.session_state[PORTAL_NAV_REQUEST] = payload
    return payload


def request_portal_navigation(module, **state):
    """Enqueue a module change and rerun. Use only in script body.

    Example: ``if st.button(...): request_portal_navigation(...)``.
    Never pass this function to on_click/on_change.
    """
    queue_portal_navigation(module, **state)
    # Cross-module requests must re-enter render_portal so the shell can
    # consume PORTAL_NAV_REQUEST even when this helper runs inside a fragment.
    st.rerun(scope="app")


def apply_portal_navigation(allowed):
    request = st.session_state.pop(PORTAL_NAV_REQUEST, None)
    if not request:
        return None
    if isinstance(request, str):
        module, extra = request, {}
    else:
        module = request.get("module")
        extra = request.get("state") or {}
    if module not in allowed:
        return None
    st.session_state["portal_module"] = module
    for key, value in extra.items():
        if key in PORTAL_NAV_STATE_KEYS:
            st.session_state[key] = value
    return module


def clear_alerts_overlay():
    st.session_state.pop(PORTAL_ALERTS_REQUEST, None)
    st.session_state.pop(PORTAL_SPECIAL_VIEW, None)
    st.session_state.pop(PORTAL_SPECIAL_ANCHOR, None)


def queue_alerts_view():
    """Enqueue the alerts overlay. Safe inside on_click/on_change (no rerun call)."""
    current = _session_get("portal_module")
    if current and current != "Alertas" and PORTAL_SPECIAL_VIEW not in st.session_state:
        st.session_state[PORTAL_SPECIAL_RETURN] = current
    st.session_state[PORTAL_ALERTS_REQUEST] = True


def request_alerts_view():
    """Enqueue the alerts overlay and rerun. Use only outside callbacks."""
    queue_alerts_view()
    st.rerun(scope="app")


def apply_alerts_view_request():
    if not st.session_state.pop(PORTAL_ALERTS_REQUEST, None):
        return None
    current = _session_get("portal_module")
    if current == "Alertas":
        current = "Início"
    st.session_state[PORTAL_SPECIAL_VIEW] = ALERTS_VIEW
    st.session_state[PORTAL_SPECIAL_ANCHOR] = current or "Início"
    if current and PORTAL_SPECIAL_RETURN not in st.session_state:
        st.session_state[PORTAL_SPECIAL_RETURN] = current
    return ALERTS_VIEW


def close_alerts_view():
    target = st.session_state.pop(PORTAL_SPECIAL_RETURN, None) or "Início"
    clear_alerts_overlay()
    current = _session_get("portal_module")
    if target != current:
        queue_portal_navigation(target)
    st.rerun(scope="app")


def alerts_overlay_active(selected):
    if _session_get(PORTAL_SPECIAL_VIEW) != ALERTS_VIEW:
        return False
    anchor = _session_get(PORTAL_SPECIAL_ANCHOR) or "Início"
    if selected != anchor:
        clear_alerts_overlay()
        st.session_state.pop(PORTAL_SPECIAL_RETURN, None)
        return False
    return True


def open_portarias():
    queue_portal_navigation("Portarias", nav="Nova Portaria")


def open_home():
    """Return to Home through the same queued Portal navigation flow."""
    queue_portal_navigation("Início")


def open_agenda():
    queue_portal_navigation("Agenda")


def open_oficios():
    queue_portal_navigation("Ofícios")


def open_memorandos():
    queue_portal_navigation("Memorandos", memorandos_nav="Visão Geral")


def open_admin():
    queue_portal_navigation("Administração")


def open_tarefas():
    queue_portal_navigation("Tarefas")


def open_relatorios():
    queue_portal_navigation("Relatórios e Indicadores")


def open_representacoes():
    queue_portal_navigation("Representações")


def open_ouvidoria():
    queue_portal_navigation("Ouvidoria")


def card(module):
    with st.container(border=True):
        institutional_card_mark()
        st.caption(module.label.upper())
        st.markdown(f"### :material/{module.icon}: {module.title}")
        st.write(module.description)
        if module.active:
            render_html(badge("ATIVO", "success"))
            st.button(
                f"Acessar {module.label}",
                key=f"open_{module.key}",
                type="primary",
                icon=":material/arrow_forward:",
                on_click={
                    "portarias": open_portarias,
                    "agenda": open_agenda,
                    "oficios": open_oficios,
                    "memorandos": open_memorandos,
                    "admin": open_admin,
                    "tarefas": open_tarefas,
                    "relatorios": open_relatorios,
                    "representacoes": open_representacoes,
                    "ouvidoria": open_ouvidoria,
                }[module.key],
            )
        else:
            st.caption("EM BREVE")
            st.markdown(
                '<div class="mpc-home-soon-slot" aria-hidden="true"></div>',
                unsafe_allow_html=True,
            )


def visible_modules(principal):
    from services.access import has_permission

    visible = []
    for module in MODULES:
        if module.active and not has_permission(principal, module.key):
            continue
        visible.append(module)
    if has_permission(principal, "admin"):
        visible.append(ADMIN_MODULE)
    visible.sort(key=lambda module: not module.active)
    return visible


def _home_layout_style():
    st.markdown(
        "<style>"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock']{"
        "align-items:stretch;gap:1rem;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock']>div{"
        "display:flex;min-width:0;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock'] "
        "[data-testid='stVerticalBlockBorderWrapper']{"
        "flex:1 1 auto;width:100%;min-height:19.5rem;height:100%;"
        "border-left:3px solid var(--mpc-brand);"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock'] "
        "[data-testid='stVerticalBlockBorderWrapper']>div{"
        "height:100%;display:flex;flex-direction:column;"
        "}"
        "section[data-testid='stMain'] .mpc-home-soon-slot{"
        "min-height:2.85rem;flex:1 1 auto;"
        "}"
        "@media (max-width:768px){"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock']{"
        "flex-direction:column;flex-wrap:nowrap;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock']>div{"
        "width:100%;flex:1 1 auto;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock'] "
        "[data-testid='stVerticalBlockBorderWrapper']{"
        "height:auto;min-height:0;flex-grow:0;"
        "}"
        "section[data-testid='stMain'] [data-testid='stHorizontalBlock'] "
        "[data-testid='stVerticalBlockBorderWrapper']>div{"
        "height:auto;min-height:0;justify-content:flex-start;"
        "}"
        "section[data-testid='stMain'] .mpc-home-soon-slot{"
        "min-height:0;flex:0 0 auto;"
        "}"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )


def open_pendencias():
    request_portal_navigation("Pendências")


def open_alertas():
    request_alerts_view()


def home(principal):
    render_app_identity(
        variant="home",
        prompt="Selecione uma ferramenta para iniciar.",
    )
    # Future modules remain registered, but only active tools occupy the Home grid.
    visible = [module for module in visible_modules(principal) if module.active]
    if not visible:
        empty_state("Nenhum módulo disponível para este usuário.")
        return
    _home_layout_style()
    for start in range(0, len(visible), 2):
        columns = st.columns(2)
        for column, module in zip(columns, visible[start : start + 2]):
            with column:
                card(module)


ACCESS_REQUEST_VIEW = "_access_request_view"


def _clear_access_request_state():
    st.session_state.pop(ACCESS_REQUEST_VIEW, None)


def _open_access_request_form():
    st.session_state[ACCESS_REQUEST_VIEW] = "form"


def render_login():
    icon = base64.standard_b64encode(asset(ROOT / "assets/gmail.png")).decode("ascii")
    st.markdown(
        "<style>"
        "section[data-testid='stMain'] [data-testid='stMainBlockContainer']{"
        "max-width:50rem;width:100%;margin-left:0;margin-right:auto;padding-top:2.25rem;"
        "}"
        "section[data-testid='stMain'] div.st-key-oidc_gmail_login{"
        "width:fit-content;max-width:100%;margin:.15rem 0 .95rem 0;"
        "}"
        "div.st-key-oidc_gmail_login button{"
        "display:inline-flex;align-items:center;justify-content:center;"
        "gap:.975rem;min-height:4.125rem;width:auto;max-width:100%;"
        "padding:.825rem 1.875rem .825rem 1.575rem;"
        "font-size:1.5rem;font-weight:650;cursor:pointer;"
        "}"
        "div.st-key-oidc_gmail_login button::before{"
        "content:'';width:1.875rem;height:1.875rem;flex:0 0 1.875rem;"
        "background-image:url('data:image/png;base64," + icon + "');"
        "background-size:contain;background-repeat:no-repeat;background-position:center;"
        "}"
        ".login-domain-hint{"
        "font-size:18px!important;font-weight:400!important;"
        "line-height:1.6;color:#000000!important;margin:0 0 16px;"
        "}"
        ".login-access-prompt{"
        "margin:1.35rem 0 .45rem 0;font-size:1.05rem;line-height:1.5;"
        "color:inherit;"
        "}"
        "section[data-testid='stMain'] div.st-key-access_request_open,"
        "section[data-testid='stMain'] div.st-key-access_request_submit,"
        "section[data-testid='stMain'] div.st-key-access_request_back{"
        "width:fit-content;max-width:100%;margin:.15rem 0 .75rem 0;"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )
    render_app_identity(variant="login", action="Acesso restrito")
    if st.button("Entrar com Gmail", type="primary", key="oidc_gmail_login"):
        try:
            st.login()
        except Exception:
            st.error(
                "Não foi possível iniciar o login com Google. Confira a configuração OIDC nos Secrets."
            )
    st.markdown(
        '<div class="login-domain-hint">'
        "Utilize uma conta previamente autorizada do domínio @tce.pb.gov.br."
        "</div>",
        unsafe_allow_html=True,
    )
    _render_access_request()


def _render_access_request():
    view = _session_get(ACCESS_REQUEST_VIEW)
    if view == "done":
        from services.access_requests import SUCCESS_BODY, SUCCESS_TITLE

        st.success(SUCCESS_TITLE)
        st.write(SUCCESS_BODY)
        if st.button("Voltar para o login", key="access_request_back"):
            _clear_access_request_state()
            st.rerun()
        return
    st.markdown(
        '<div class="login-access-prompt">Ainda não possui acesso?</div>',
        unsafe_allow_html=True,
    )
    if view != "form":
        if st.button("Solicitar acesso", key="access_request_open"):
            _open_access_request_form()
            st.rerun()
        return
    from services.access_requests import (
        GABINETE_OPTIONS,
        OTHER_UNIT,
        submit_access_request,
    )

    nome = st.text_input("Nome completo", key="access_request_nome")
    email = st.text_input("E-mail institucional", key="access_request_email")
    gabinete = st.selectbox(
        "Gabinete / Unidade",
        GABINETE_OPTIONS,
        index=None,
        placeholder="Selecione",
        key="access_request_gabinete",
    )
    unidade_outro = None
    if gabinete == OTHER_UNIT:
        unidade_outro = st.text_input(
            "Informe a unidade", key="access_request_unidade_outro"
        )
    submitted = st.button(
        "Enviar solicitação", type="primary", key="access_request_submit"
    )
    if st.button("Voltar para o login", key="access_request_back"):
        _clear_access_request_state()
        st.rerun()
    if not submitted:
        return
    try:
        store = _application_store()
        outcome = submit_access_request(store, nome, email, gabinete, unidade_outro)
    except Exception:
        LOGGER.exception("Falha ao registrar solicitação de acesso")
        st.error("Não foi possível registrar a solicitação. Tente novamente.")
        return
    if not outcome.ok:
        st.error(outcome.message)
        return
    st.session_state[ACCESS_REQUEST_VIEW] = "done"
    st.rerun()


def _logout():
    """End the native OIDC session and drop this user's authorization cache."""
    try:
        from services.audit import registrar_logout

        registrar_logout(
            st.session_state["_mpc_store"] if "_mpc_store" in st.session_state else None,
            st.session_state.get("_audit_actor"),
            identity=st.session_state.get("_audit_identity"),
        )
    except Exception:
        pass
    if "_access_cache" in st.session_state:
        del st.session_state["_access_cache"]
    if "portal_module" in st.session_state:
        del st.session_state["portal_module"]
    actor = st.session_state.get("_audit_actor")
    if actor is not None:
        st.session_state.pop(f"portal_theme_select_{actor.id}", None)
    for key in (
        "_portal_theme",
        "_portal_theme_error",
        "_audit_actor",
        "_audit_identity",
        "audit_sessao_registrada",
        "audit_modulo_atual",
        "audit_negado_registrado",
        PORTAL_ALERTS_REQUEST,
        PORTAL_SPECIAL_VIEW,
        PORTAL_SPECIAL_RETURN,
        PORTAL_SPECIAL_ANCHOR,
        "_alerts_bell_cache",
        "_alerts_bell_open",
        "_alerts_bell_epoch",
        "_alerts_bell_intent",
        "_reports_read_cache",
        "_tramita_previews",
    ):
        st.session_state.pop(key, None)
    for key in list(st.session_state):
        if str(key).startswith("_alerts_bell_open_"):
            st.session_state.pop(key, None)
    st.logout()


def render_denied(identity):
    render_app_identity()
    st.error("Acesso não autorizado.")
    st.write(
        "A conta "
        + identity["email"]
        + " foi autenticada pelo Google, mas não possui autorização "
        "para acessar o " + APP_NAME + "."
    )
    st.write("Entre em contato com o administrador do sistema.")
    if st.button("Sair", type="primary"):
        _logout()


def _application_store():
    from database.store import Store, unwrap_store

    store = _session_get("_mpc_store")
    if store is not None:
        store = unwrap_store(store)
        st.session_state["_mpc_store"] = store
        return store
    store = Store()
    st.session_state["_mpc_store"] = store
    return store


def render_portal(sidebar_context=None):
    from services.access import (
        current_user,
        has_permission,
        oidc_identity,
        require_permission,
    )

    st.set_page_config(
        page_title=APP_NAME,
        page_icon=str(SIDEBAR_LOGO),
        layout="wide",
    )
    identity = oidc_identity()
    apply_theme(_active_theme(identity))
    if identity is None:
        with st.sidebar:
            render_sidebar_brand()
        render_institutional_header()
        render_login()
        st.stop()
    try:
        store = _application_store()
    except Exception:
        # logger.exception records the traceback only on the server. Do not
        # interpolate configuration or identity values into this message.
        LOGGER.exception("Falha ao inicializar Store")
        with st.sidebar:
            render_sidebar_brand()
        render_institutional_header()
        render_app_identity()
        st.error("Não foi possível verificar a autorização. Tente novamente.")
        if st.button("Sair", type="primary"):
            _logout()
        st.stop()
    try:
        principal = current_user(store)
    except Exception:
        LOGGER.exception("Falha ao verificar autorização")
        with st.sidebar:
            render_sidebar_brand()
        render_institutional_header()
        render_app_identity()
        st.error("Não foi possível verificar a autorização. Tente novamente.")
        if st.button("Sair", type="primary"):
            _logout()
        st.stop()
    st.session_state["_audit_identity"] = identity
    if principal is None:
        from database.access import AccessStore
        from services.audit import registrar_acesso_negado

        record = AccessStore(store).get_by_email(identity["email"])
        registrar_acesso_negado(
            store, identity, inativo=bool(record and not record["ativo"])
        )
        with st.sidebar:
            render_sidebar_brand()
        render_institutional_header()
        render_denied(identity)
        st.stop()
    st.session_state["_audit_actor"] = principal
    from services.audit import iniciar_sessao_autorizada, registrar_modulo

    iniciar_sessao_autorizada(store, principal)
    options = ["Início", "Busca Global"]
    if has_permission(principal, "pendencias"):
        options.append("Pendências")
    if has_permission(principal, "portarias"):
        options.append("Portarias")
    if has_permission(principal, "agenda"):
        options.append("Agenda")
    if has_permission(principal, "oficios"):
        options.append("Ofícios")
    if has_permission(principal, "memorandos"):
        options.append("Memorandos")
    if has_permission(principal, "tarefas"):
        options.append("Tarefas")
    if has_permission(principal, "relatorios"):
        options.append("Relatórios e Indicadores")
    if has_permission(principal, "representacoes"):
        options.append("Representações")
    if has_permission(principal, "ouvidoria"):
        options.append("Ouvidoria")
    if has_permission(principal, "admin"):
        options.append("Administração")
    apply_portal_navigation(options)
    if _session_get("portal_module") not in options:
        st.session_state["portal_module"] = "Início"
    apply_alerts_view_request()
    with st.sidebar:
        render_sidebar_brand(on_click=open_home)
        st.caption(principal.nome + " · " + principal.email)
        from services.alerts import can_view_alertas
        from services.alerts_ui import render_bell

        _, logout_column = st.columns([3, 2])
        st.markdown(
            "<style>div.st-key-portal_logout{display:flex;justify-content:flex-end;}</style>",
            unsafe_allow_html=True,
        )
        if logout_column.button("Sair", type="primary", key="portal_logout"):
            _logout()
        if can_view_alertas(principal):
            render_bell(store, principal)
        selected = st.radio(
            "Portal",
            options,
            key="portal_module",
            format_func=lambda option: (
                "Agenda e Afastamentos" if option == "Agenda" else option
            ),
        )
        if (
            selected == "Portarias"
            and sidebar_context is not None
            and not alerts_overlay_active(selected)
        ):
            portarias_menu = sidebar_context()
        theme_key = f"portal_theme_select_{principal.id}"
        active_theme = _active_theme(identity)
        if st.session_state.get(theme_key) != active_theme:
            st.session_state[theme_key] = active_theme
        with st.container(key="portal_theme_footer"):
            st.selectbox(
                "Tema",
                tuple(THEME_LABELS),
                format_func=THEME_LABELS.get,
                key=theme_key,
                on_change=_change_theme,
                args=(store, principal.id, identity["email"], theme_key),
            )
            if message := st.session_state.pop("_portal_theme_error", None):
                st.warning(message)
        st.caption(f"Versão {APP_VERSION}", text_alignment="right")
        if selected != "Memorandos":
            st.session_state["memorando_form_active"] = False
        if selected != "Relatórios e Indicadores":
            st.session_state.pop("_tramita_previews", None)
    render_institutional_header(home=selected == "Início")
    if alerts_overlay_active(selected):
        st.session_state.pop("_global_search_home_active", None)
        from services.alerts_ui import render as render_alerts

        try:
            require_permission(principal, "alertas")
        except ValueError as exc:
            clear_alerts_overlay()
            st.session_state.pop(PORTAL_SPECIAL_RETURN, None)
            st.error(str(exc))
            st.stop()
        registrar_modulo(store, principal, "Alertas")
        _render_module_fragment(render_alerts, store, principal)
        st.stop()
    if selected == "Início":
        st.session_state.pop("_global_search_home_active", None)
        st.session_state["audit_modulo_atual"] = None
        home(principal)
        st.stop()
    if selected == "Busca Global":
        st.session_state["audit_modulo_atual"] = None
        from services.search_ui import render_home_search

        render_home_search(store, principal)
        st.stop()
    st.session_state.pop("_global_search_home_active", None)
    registrar_modulo(store, principal, selected)
    try:
        if selected == "Pendências":
            require_permission(principal, "pendencias")
            from services.pending_ui import render

            _render_module_fragment(render, store, principal)
            st.stop()
        if selected == "Agenda":
            require_permission(principal, "agenda")
            from services.agenda_ui import render

            _render_module_fragment(render, store, principal)
            st.stop()
        if selected == "Ofícios":
            require_permission(principal, "oficios")
            from services.oficios_ui import render

            _render_module_fragment(render, store, principal)
            st.stop()
        if selected == "Memorandos":
            require_permission(principal, "memorandos")
            from services.memorandos_ui import render

            _render_module_fragment(render, store, principal)
            st.stop()
        if selected == "Tarefas":
            require_permission(principal, "tarefas")
            from services.tarefas_ui import render

            _render_module_fragment(render, store, principal)
            st.stop()
        if selected == "Relatórios e Indicadores":
            require_permission(principal, "relatorios")
            from services.relatorios_ui import render

            _render_module_fragment(render, store, principal)
            st.stop()
        if selected == "Representações":
            require_permission(principal, "representacoes")
            from services.representacoes_ui import render

            _render_module_fragment(render, store, principal)
            st.stop()
        if selected == "Ouvidoria":
            require_permission(principal, "ouvidoria")
            from services.ouvidoria_ui import render

            _render_module_fragment(render, store, principal)
            st.stop()
        if selected == "Administração":
            require_permission(principal, "admin")
            from services.access_ui import render

            _render_module_fragment(render, store, principal)
            st.stop()
        require_permission(principal, "portarias")
    except ValueError as exc:
        from services.audit import MODULE_KEYS, registrar_evento

        registrar_evento(
            store,
            evento="PERMISSAO_NEGADA",
            modulo=MODULE_KEYS.get(selected, ""),
            acao="ACESSAR",
            resultado="NEGADO",
            principal=principal,
            detalhes={"modulo": selected, "motivo": str(exc)[:200]},
        )
        st.error(str(exc))
        st.stop()
    return portarias_menu
