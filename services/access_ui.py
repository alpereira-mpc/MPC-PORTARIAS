"""Administrative UI for authorized users. Isolated from Portarias/Agenda/Ofícios."""

from datetime import date
import logging
import streamlit as st
from database.access import AccessStore
from services.access import require_permission
from services.audit import aplicar_exclusao_usuario, aplicar_usuario
from services.oficios import GABINETES
from services.branding import module_title
from services.ui_theme import form_mark, render_html, section_label

LOGGER = logging.getLogger(__name__)
ADMIN_SECTIONS = (
    "Usuários",
    "Solicitações",
    "Funções Institucionais",
    "Acessos e Auditoria",
    "Sistema",
)
ADMIN_SISTEMA_TABS = ("Saúde", "Backup")
AUDIT_TABS = ("Visão Geral", "Acessos", "Auditoria")
ADMIN_NAV_REQUEST = "pending_open_admin"


def institutional_functions(store, principal):
    from database.institutional import FUNCTIONS, InstitutionalFunctions
    from services.audit import registrar_evento

    functions = InstitutionalFunctions(store)
    people = [p for p in store.catalog("procuradores") if p["ativo"]]
    names = {p["id"]: p["nome"] for p in people}
    st.caption("A alteração passa a afetar regras institucionais do sistema a partir da nova vigência.")
    current = functions.current_all()
    st.dataframe([{"Função": FUNCTIONS[code], "Titular atual": row["nome"] if row else "—", "Desde": row["data_inicio"] if row else "—"} for code, row in current.items()], hide_index=True, use_container_width=True)
    code = st.selectbox("Função institucional", list(FUNCTIONS), format_func=FUNCTIONS.get)
    with st.expander("Alterar titular"):
        holder = st.selectbox("Novo titular", list(names), format_func=names.get, key="funcao_institucional_holder")
        start = st.date_input("Início da vigência", value=date.today(), format="DD/MM/YYYY")
        confirmed = st.checkbox("Confirmo a alteração da função institucional.")
        if st.button("Salvar alteração", disabled=not confirmed):
            old, identifier = functions.change(code, holder, start, getattr(principal, "email", ""))
            registrar_evento(store, evento="FUNCAO_INSTITUCIONAL_ALTERADA", modulo="admin", acao="ALTERAR", principal=principal, entidade_tipo="funcao_institucional", entidade_id=identifier, detalhes={"funcao": code, "titular_anterior": old["procurador_id"] if old else None, "novo_titular": holder, "inicio": start.isoformat()})
            st.success("Titular alterado; a vigência anterior foi preservada no histórico.")
            st.rerun()
    with st.expander("Histórico das funções"):
        st.dataframe([{"Função": FUNCTIONS[row["funcao"]], "Procurador": row["nome"], "Início": row["data_inicio"], "Fim": row["data_fim"] or "—"} for row in functions.history()], hide_index=True, use_container_width=True)


def _gabinete_display(row):
    extra = (row.get("unidade_outro") or "").strip()
    gabinete = row.get("gabinete") or ""
    if extra:
        return gabinete + " — " + extra
    return gabinete


def _invalidate_alerts():
    try:
        from services.alerts import invalidate_alert_summary

        invalidate_alert_summary()
    except Exception:
        pass


def _render_access_requests(store, principal):
    from services.access_requests import (
        ADMIN_FILTERS,
        FILTER_PENDING,
        approve_access_request,
        delete_access_request,
        list_access_requests_by_filter,
        reject_access_request,
    )
    from database.access_requests import STATUS_PENDING

    section_label("Solicitações")
    message = st.session_state.pop("access_request_admin_message", None)
    if message:
        st.success(message)
    filtro = st.radio(
        "Filtro",
        ADMIN_FILTERS,
        horizontal=True,
        key="access_request_admin_filter",
    )
    if filtro not in ADMIN_FILTERS:
        filtro = FILTER_PENDING
    rows = list_access_requests_by_filter(store, filtro)
    if not rows:
        st.caption("Nenhuma solicitação neste filtro.")
        return
    st.dataframe(
        [
            {
                "Nome": row["nome"],
                "E-mail institucional": row["email"],
                "Gabinete / Unidade": _gabinete_display(row),
                "Data da solicitação": row["created_at"],
                "Status": row["status"],
            }
            for row in rows
        ],
        hide_index=True,
        use_container_width=True,
    )
    actor = getattr(principal, "email", "")
    pending = [row for row in rows if row["status"] == STATUS_PENDING]
    if pending:
        st.caption("Aprovar ou recusar não cria usuário nem altera permissões.")
    for row in rows:
        identifier = row["id"]
        with st.expander(row["nome"] + " · " + row["email"]):
            if row["status"] == STATUS_PENDING:
                confirm_approve = st.checkbox(
                    "Confirmo a aprovação",
                    key="access_req_confirm_approve_" + str(identifier),
                )
                if st.button(
                    "Aprovar",
                    key="access_req_approve_" + str(identifier),
                    disabled=not confirm_approve,
                ):
                    try:
                        approve_access_request(store, identifier, actor)
                    except ValueError as exc:
                        st.error(str(exc))
                    except Exception:
                        LOGGER.exception("Falha ao aprovar solicitação de acesso")
                        st.error("Não foi possível aprovar a solicitação.")
                    else:
                        _invalidate_alerts()
                        st.session_state["access_request_admin_message"] = (
                            "Solicitação marcada como aprovada."
                        )
                        st.rerun()
                confirm_reject = st.checkbox(
                    "Confirmo a recusa",
                    key="access_req_confirm_reject_" + str(identifier),
                )
                if st.button(
                    "Recusar",
                    key="access_req_reject_" + str(identifier),
                    disabled=not confirm_reject,
                ):
                    try:
                        reject_access_request(store, identifier, actor)
                    except ValueError as exc:
                        st.error(str(exc))
                    except Exception:
                        LOGGER.exception("Falha ao recusar solicitação de acesso")
                        st.error("Não foi possível recusar a solicitação.")
                    else:
                        _invalidate_alerts()
                        st.session_state["access_request_admin_message"] = (
                            "Solicitação marcada como recusada."
                        )
                        st.rerun()
            confirm_delete = st.checkbox(
                "Confirmo a exclusão definitiva",
                key="access_req_confirm_delete_" + str(identifier),
            )
            if st.button(
                "Excluir",
                key="access_req_delete_" + str(identifier),
                disabled=not confirm_delete,
            ):
                try:
                    deleted = delete_access_request(store, identifier)
                except Exception:
                    LOGGER.exception("Falha ao excluir solicitação de acesso")
                    st.error("Não foi possível excluir a solicitação.")
                else:
                    _invalidate_alerts()
                    if deleted:
                        st.session_state["access_request_admin_message"] = (
                            "Solicitação excluída."
                        )
                    else:
                        st.session_state["access_request_admin_message"] = (
                            "Esta solicitação já havia sido excluída."
                        )
                    st.rerun()


def queue_admin_navigation(secao=None, aba=None, audit_tab=None):
    """Enqueue an admin subsection. Safe inside on_click/on_change.

    Consumed by consume_pending_open_admin() before the admin radios exist.
    Do not write admin_secao / admin_sistema_aba / audit_tab after those widgets.
    """
    pending = st.session_state.get(ADMIN_NAV_REQUEST)
    if not isinstance(pending, dict):
        pending = {}
    else:
        pending = dict(pending)
    if secao:
        pending["secao"] = secao
    if aba:
        pending["aba"] = aba
    if audit_tab:
        pending["audit_tab"] = audit_tab
    st.session_state[ADMIN_NAV_REQUEST] = pending
    return pending


def request_admin_navigation(secao=None, aba=None, audit_tab=None):
    """Enqueue an admin subsection and rerun. Use only in script body, never in callbacks."""
    queue_admin_navigation(secao=secao, aba=aba, audit_tab=audit_tab)
    st.rerun()


def consume_pending_open_admin():
    if ADMIN_NAV_REQUEST not in st.session_state:
        return None
    pending = st.session_state.pop(ADMIN_NAV_REQUEST)
    if not pending:
        return None
    if isinstance(pending, str):
        pending = {"secao": pending}
    if not isinstance(pending, dict):
        return None
    secao = pending.get("secao")
    if secao in ADMIN_SECTIONS:
        st.session_state["admin_secao"] = secao
    aba = pending.get("aba")
    if aba in ADMIN_SISTEMA_TABS:
        st.session_state["admin_sistema_aba"] = aba
    audit_tab = pending.get("audit_tab")
    if audit_tab in AUDIT_TABS:
        st.session_state["audit_tab"] = audit_tab
    return pending


def render(store, principal):
    require_permission(principal, "admin")
    consume_pending_open_admin()
    st.subheader(module_title("admin", "ADMINISTRAÇÃO — Usuários e Acessos"))
    pending_count = None
    try:
        from services.access_requests import count_pending_access_requests

        pending_count = count_pending_access_requests(store)
    except Exception:
        LOGGER.exception("Falha ao contar solicitações de acesso pendentes")
    area = st.radio(
        "Seção",
        list(ADMIN_SECTIONS),
        horizontal=True,
        key="admin_secao",
    )
    if pending_count is not None:
        st.caption("Solicitações pendentes: " + str(pending_count))
    if area == "Solicitações":
        try:
            _render_access_requests(store, principal)
        except Exception:
            LOGGER.exception("Falha ao carregar solicitações de acesso")
            st.error(
                "Não foi possível carregar as solicitações. "
                "Os demais recursos administrativos permanecem disponíveis."
            )
        return
    if area == "Funções Institucionais":
        institutional_functions(store, principal)
        return
    if area == "Acessos e Auditoria":
        from services.audit_ui import render as render_audit

        render_audit(store, principal)
        return
    if area == "Sistema":
        from services.system_ui import render as render_system

        render_system(store, principal)
        return
    access = AccessStore(store)
    users = access.list_users()
    if st.session_state.get("acesso_message"):
        st.success(st.session_state.pop("acesso_message"))
    if st.session_state.pop("acesso_select_new", False):
        st.session_state["acesso_pick"] = 0
    section_label("Usuários")
    st.dataframe(
        [
            {
                "Nome": u["nome"],
                "E-mail": u["email"],
                "Perfil": u["perfil"],
                "Ativo": "Sim" if u["ativo"] else "Não",
                "Portarias": "Sim" if u["pode_portarias"] else "Não",
                "Agenda e Afastamentos": "Sim" if u["pode_agenda"] else "Não",
                "Ofícios": "Sim" if u["pode_oficios"] else "Não",
                "Memorandos": "Sim" if u["pode_memorandos"] else "Não",
                "Relatórios": "Sim" if u["pode_relatorios"] else "Não",
                "Representações": "Sim" if u["pode_representacoes"] else "Não",
                "Ouvidoria": "Sim" if u["pode_ouvidoria"] else "Não",
                "Admin": "Sim" if u["pode_admin"] else "Não",
                "Gabinetes": ", ".join(u["gabinetes"]) or "—",
            }
            for u in users
        ],
        hide_index=True,
        use_container_width=True,
    )
    section_label("Cadastro")
    choices = {0: "Novo usuário"}
    for user in users:
        choices[user["id"]] = f"{user['nome']} ({user['email']})"
    selected = st.selectbox(
        "Editar cadastro",
        list(choices),
        format_func=lambda i: choices[i],
        key="acesso_pick",
    )
    current = dict(
        nome="",
        email="",
        perfil="USUARIO",
        ativo=True,
        pode_portarias=False,
        pode_agenda=False,
        pode_oficios=False,
        pode_memorandos=False,
        pode_relatorios=False,
        pode_representacoes=False,
        pode_ouvidoria=False,
        pode_admin=False,
        gabinetes=[],
    )
    if selected != 0:
        current = next((dict(u) for u in users if u["id"] == selected), None)
        if current is None:
            current = access.get(selected)
    prefix = "acesso_form_" + str(selected) + "_"
    protected = bool(current.get("protegido"))
    last_admin = (
        selected
        and current["perfil"] == "ADMINISTRADOR"
        and current["ativo"]
        and access.active_administrator_count(exclude_id=selected) == 0
    )
    profile_options = ("USUARIO", "ADMINISTRADOR")
    if protected:
        st.caption("Administrador protegido")
        profile_options = ("ADMINISTRADOR",)
    with st.form("acesso_user_" + str(selected)):
        form_mark()
        nome = st.text_input("Nome", value=current["nome"], key=prefix + "nome")
        email = st.text_input(
            "E-mail",
            value=current["email"],
            key=prefix + "email",
            disabled=protected,
        )
        perfil = st.selectbox(
            "Perfil",
            profile_options,
            index=profile_options.index(
                current["perfil"] if current["perfil"] in profile_options else profile_options[0]
            ),
            key=prefix + "perfil",
            disabled=protected,
        )
        st.write("Acesso")
        portarias = st.checkbox(
            "Portarias", value=current["pode_portarias"], key=prefix + "portarias"
        )
        agenda = st.checkbox(
            "Agenda e Afastamentos", value=current["pode_agenda"], key=prefix + "agenda"
        )
        oficios = st.checkbox(
            "Ofícios", value=current["pode_oficios"], key=prefix + "oficios"
        )
        memorandos = st.checkbox(
            "Memorandos", value=current["pode_memorandos"], key=prefix + "memorandos"
        )
        relatorios = st.checkbox(
            "Relatórios e Indicadores",
            value=current["pode_relatorios"],
            key=prefix + "relatorios",
        )
        representacoes = st.checkbox(
            "Representações",
            value=current["pode_representacoes"],
            key=prefix + "representacoes",
        )
        ouvidoria = st.checkbox(
            "Ouvidoria",
            value=current.get("pode_ouvidoria", False),
            key=prefix + "ouvidoria",
        )
        admin = st.checkbox(
            "Administração",
            value=current["pode_admin"],
            key=prefix + "admin",
            disabled=protected,
        )
        gabinetes = st.multiselect(
            "Gabinetes de Ofícios",
            list(GABINETES),
            default=[g for g in current["gabinetes"] if g in GABINETES],
            key=prefix + "gabinetes",
        )
        ativo = st.checkbox(
            "Ativo",
            value=current["ativo"],
            key=prefix + "ativo",
            disabled=protected or last_admin,
        )
        submit = st.form_submit_button("Salvar")
    if submit:
        identifier = None if selected == 0 else selected
        try:
            aplicar_usuario(
                store,
                principal,
                {
                    "nome": nome,
                    "email": email,
                    "perfil": perfil,
                    "ativo": ativo,
                    "pode_portarias": portarias,
                    "pode_agenda": agenda,
                    "pode_oficios": oficios,
                    "pode_memorandos": memorandos,
                    "pode_relatorios": relatorios,
                    "pode_representacoes": representacoes,
                    "pode_ouvidoria": ouvidoria,
                    "pode_admin": admin,
                    "gabinetes": gabinetes,
                },
                identifier,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("_access_cache", None)
            st.session_state["acesso_message"] = "Usuário salvo."
            st.rerun()
    if selected and not protected:
        if current["ativo"]:
            if last_admin:
                st.caption("O sistema deve manter pelo menos um administrador ativo.")
            elif st.button("Desativar"):
                try:
                    current["ativo"] = False
                    aplicar_usuario(store, principal, current, selected)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop("_access_cache", None)
                    st.session_state["acesso_message"] = "Usuário desativado."
                    st.rerun()
        elif st.button("Ativar"):
            try:
                current["ativo"] = True
                aplicar_usuario(store, principal, current, selected)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("_access_cache", None)
                st.session_state["acesso_message"] = "Usuário ativado."
                st.rerun()
        self_delete = selected == principal.id
        st.divider()
        if protected:
            st.caption("Este cadastro não pode ser excluído.")
        elif self_delete:
            st.caption("Você não pode excluir o próprio cadastro.")
        elif last_admin:
            st.caption("Não é possível excluir o último administrador ativo.")
        elif st.session_state.get("acesso_delete_id") == selected:
            with st.container(border=True):
                render_html('<div class="mpc-danger-zone" hidden></div>')
                st.error("Exclusão definitiva")
                st.write("Será excluído: **" + current["nome"] + "** · " + current["email"])
                confirmed = st.checkbox(
                    "Confirmo a exclusão definitiva",
                    key="acesso_delete_confirm_" + str(selected),
                )
                typed = st.text_input(
                    "Digite EXCLUIR para confirmar",
                    key="acesso_delete_typed_" + str(selected),
                )
                cancel, destroy = st.columns(2)
                with cancel:
                    if st.button("Cancelar exclusão"):
                        st.session_state.pop("acesso_delete_id", None)
                        st.rerun()
                with destroy:
                    if st.button("Confirmar exclusão"):
                        if not confirmed or typed.strip() != "EXCLUIR":
                            st.error(
                                "Marque a confirmação e digite EXCLUIR para excluir o usuário."
                            )
                        else:
                            try:
                                aplicar_exclusao_usuario(store, principal, selected)
                            except ValueError as exc:
                                st.error(str(exc))
                            else:
                                st.session_state.pop("acesso_delete_id", None)
                                st.session_state.pop("_access_cache", None)
                                st.session_state["acesso_select_new"] = True
                                st.session_state["acesso_message"] = "Usuário excluído."
                                st.rerun()
        elif st.button("Excluir usuário"):
            st.session_state["acesso_delete_id"] = selected
            st.rerun()
