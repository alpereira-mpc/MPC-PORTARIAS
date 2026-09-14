"""Administrative UI for authorized users. Isolated from Portarias/Agenda/Ofícios."""

import streamlit as st
from database.access import AccessStore
from services.access import require_permission
from services.audit import aplicar_exclusao_usuario, aplicar_usuario
from services.oficios import GABINETES

ADMIN_SECTIONS = ("Usuários", "Acessos e Auditoria", "Sistema")
ADMIN_SISTEMA_TABS = ("Saúde", "Backup")
AUDIT_TABS = ("Visão Geral", "Acessos", "Auditoria")
ADMIN_NAV_REQUEST = "pending_open_admin"


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
    st.subheader("ADMINISTRAÇÃO — Usuários e Acessos")
    area = st.radio(
        "Seção",
        ["Usuários", "Acessos e Auditoria", "Sistema"],
        horizontal=True,
        key="admin_secao",
    )
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
    st.dataframe(
        [
            {
                "Nome": u["nome"],
                "E-mail": u["email"],
                "Perfil": u["perfil"],
                "Ativo": "Sim" if u["ativo"] else "Não",
                "Portarias": "Sim" if u["pode_portarias"] else "Não",
                "Agenda": "Sim" if u["pode_agenda"] else "Não",
                "Ofícios": "Sim" if u["pode_oficios"] else "Não",
                "Memorandos": "Sim" if u["pode_memorandos"] else "Não",
                "Admin": "Sim" if u["pode_admin"] else "Não",
                "Gabinetes": ", ".join(u["gabinetes"]) or "—",
            }
            for u in users
        ],
        hide_index=True,
        use_container_width=True,
    )
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
            "Agenda", value=current["pode_agenda"], key=prefix + "agenda"
        )
        oficios = st.checkbox(
            "Ofícios", value=current["pode_oficios"], key=prefix + "oficios"
        )
        memorandos = st.checkbox(
            "Memorandos", value=current["pode_memorandos"], key=prefix + "memorandos"
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
