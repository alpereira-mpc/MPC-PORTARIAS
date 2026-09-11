"""Administrative UI for authorized users. Isolated from Portarias/Agenda/Ofícios."""

import streamlit as st
from database.access import AccessStore
from services.access import require_permission
from services.oficios import GABINETES


def render(store, principal):
    require_permission(principal, "admin")
    st.subheader("ADMINISTRAÇÃO — Usuários e Acessos")
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
        pode_admin=False,
        gabinetes=[],
    )
    if selected != 0:
        current = next((dict(u) for u in users if u["id"] == selected), None)
        if current is None:
            current = access.get(selected)
    prefix = "acesso_form_" + str(selected) + "_"
    profile_options = ("USUARIO", "ADMINISTRADOR")
    with st.form("acesso_user_" + str(selected)):
        nome = st.text_input("Nome", value=current["nome"], key=prefix + "nome")
        email = st.text_input("E-mail", value=current["email"], key=prefix + "email")
        perfil = st.selectbox(
            "Perfil",
            profile_options,
            index=profile_options.index(
                current["perfil"] if current["perfil"] in profile_options else "USUARIO"
            ),
            key=prefix + "perfil",
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
        admin = st.checkbox(
            "Administração", value=current["pode_admin"], key=prefix + "admin"
        )
        gabinetes = st.multiselect(
            "Gabinetes de Ofícios",
            list(GABINETES),
            default=[g for g in current["gabinetes"] if g in GABINETES],
            key=prefix + "gabinetes",
        )
        ativo = st.checkbox("Ativo", value=current["ativo"], key=prefix + "ativo")
        submit = st.form_submit_button("Salvar")
    if submit:
        identifier = None if selected == 0 else selected
        try:
            access.save_user(
                {
                    "nome": nome,
                    "email": email,
                    "perfil": perfil,
                    "ativo": ativo,
                    "pode_portarias": portarias,
                    "pode_agenda": agenda,
                    "pode_oficios": oficios,
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
    if selected:
        if current["ativo"]:
            if st.button("Desativar"):
                access.set_active(selected, False)
                st.session_state.pop("_access_cache", None)
                st.session_state["acesso_message"] = "Usuário desativado."
                st.rerun()
        elif st.button("Ativar"):
            access.set_active(selected, True)
            st.session_state.pop("_access_cache", None)
            st.session_state["acesso_message"] = "Usuário ativado."
            st.rerun()
        self_delete = selected == principal.id
        last_admin = (
            current["perfil"] == "ADMINISTRADOR"
            and current["ativo"]
            and access.active_administrator_count(exclude_id=selected) == 0
        )
        st.divider()
        if self_delete:
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
                            access.delete_user(selected, actor_id=principal.id)
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
