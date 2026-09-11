"""Administrative UI for authorized users. Isolated from Portarias/Agenda/Ofícios."""

import streamlit as st
from database.access import AccessStore, PROFILES
from services.access import require_permission
from services.oficios import GABINETES


def render(store, principal):
    require_permission(principal, "admin")
    st.subheader("ADMINISTRAÇÃO — Usuários e Acessos")
    access = AccessStore(store)
    users = access.list_users()
    if st.session_state.get("acesso_message"):
        st.success(st.session_state.pop("acesso_message"))
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
    current = (
        dict(
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
        if selected == 0
        else access.get(selected)
    )
    with st.form("acesso_user"):
        nome = st.text_input("Nome", current["nome"])
        email = st.text_input("E-mail", current["email"])
        perfil = st.selectbox(
            "Perfil",
            list(PROFILES),
            index=0 if current["perfil"] == "ADMINISTRADOR" else 1,
        )
        st.write("Acesso")
        portarias = st.checkbox("Portarias", current["pode_portarias"])
        agenda = st.checkbox("Agenda", current["pode_agenda"])
        oficios = st.checkbox("Ofícios", current["pode_oficios"])
        admin = st.checkbox("Administração", current["pode_admin"])
        gabinetes = st.multiselect(
            "Gabinetes de Ofícios",
            list(GABINETES),
            default=[g for g in current["gabinetes"] if g in GABINETES],
        )
        ativo = st.checkbox("Ativo", current["ativo"])
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
