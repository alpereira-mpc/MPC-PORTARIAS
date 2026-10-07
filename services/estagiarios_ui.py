"""Compact administrator screen for internship placement history."""

from datetime import date

import streamlit as st

from database.estagiarios import EstagiariosStore, LOTACOES, limite_padrao
from services.audit import registrar_evento
from services.ui_theme import section_label


def _remaining(limit, today=None):
    days = (limit - (today or date.today())).days
    if days <= 60:
        return "Encerrado" if days < 0 else f"Encerra em {days} dias"
    years, remainder = divmod(days, 365)
    months = remainder // 30
    parts = []
    if years:
        parts.append(f"{years} {'ano' if years == 1 else 'anos'}")
    if months:
        parts.append(f"{months} {'mês' if months == 1 else 'meses'}")
    return " e ".join(parts or [f"{days} dias"]) + " restantes"


def _audit(store, principal, event, action, identifier, details):
    registrar_evento(store, evento=event, modulo="admin", acao=action, principal=principal,
                    entidade_tipo="estagiario_lotacao", entidade_id=identifier, detalhes=details)


def _form_people(service, key):
    people = service.eligible_people()
    if not people:
        st.info("Não há pessoas ativas classificadas explicitamente como estagiárias na Base de Servidores.")
        return None
    by_id = {row["id"]: row for row in people}
    identifier = st.selectbox("Estagiário", list(by_id), format_func=lambda i: f"{by_id[i]['nome']} — {by_id[i]['matricula_original'] or 'sem matrícula'}", key=key)
    return identifier


def _add_form(service, store, principal):
    with st.expander("Cadastrar vínculo", expanded=False):
        with st.form("estagiario_create"):
            person = _form_people(service, "estagiario_create_person")
            lotacao = st.selectbox("Lotação", LOTACOES)
            start = st.date_input("Data de início", value=date.today(), format="DD/MM/YYYY")
            default_limit = limite_padrao(start)
            limit = st.date_input("Data limite", value=default_limit, format="DD/MM/YYYY")
            submitted = st.form_submit_button("Cadastrar vínculo", type="primary", disabled=person is None)
        if submitted:
            try:
                identifier = service.create(person, lotacao, start, limit, actor_email=principal.email, administrator=principal.administrator)
                _audit(store, principal, "ESTAGIARIO_VINCULO_CRIADO", "CADASTRAR", identifier, {"pessoa_id": person, "lotacao": lotacao})
                st.success("Vínculo cadastrado."); st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def _active_controls(service, store, principal, row):
    key = str(row["id"])
    with st.expander("Gerenciar vínculo", expanded=False):
        with st.form("estagiario_edit_" + key):
            lotacao = st.selectbox("Lotação", LOTACOES, index=LOTACOES.index(row["lotacao"]), key="estagiario_edit_lotacao_" + key)
            start = st.date_input("Data de início", value=date.fromisoformat(row["data_inicio"]), format="DD/MM/YYYY", key="estagiario_edit_start_" + key)
            limit = st.date_input("Data limite", value=date.fromisoformat(row["data_limite"]), format="DD/MM/YYYY", key="estagiario_edit_limit_" + key)
            edited = st.form_submit_button("Salvar dados administrativos")
        if edited:
            try:
                service.update(row["id"], lotacao, start, limit, actor_email=principal.email, administrator=True)
                _audit(store, principal, "ESTAGIARIO_VINCULO_EDITADO", "EDITAR", row["id"], {"lotacao": lotacao})
                st.success("Vínculo atualizado."); st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        with st.form("estagiario_close_" + key):
            end = st.date_input("Data de encerramento", value=date.today(), format="DD/MM/YYYY", key="estagiario_close_date_" + key)
            close = st.form_submit_button("Encerrar vínculo")
        if close:
            try:
                service.close(row["id"], end, actor_email=principal.email, administrator=True)
                _audit(store, principal, "ESTAGIARIO_VINCULO_ENCERRADO", "ENCERRAR", row["id"], {"encerramento": end.isoformat()})
                st.success("Vínculo encerrado; o histórico foi preservado."); st.rerun()
            except ValueError as exc:
                st.error(str(exc))
        with st.form("estagiario_replace_" + key):
            person = _form_people(service, "estagiario_replace_person_" + key)
            start = st.date_input("Início do substituto", value=date.today(), format="DD/MM/YYYY", key="estagiario_replace_start_" + key)
            limit = st.date_input("Limite do substituto", value=limite_padrao(start), format="DD/MM/YYYY", key="estagiario_replace_limit_" + key)
            replace = st.form_submit_button("Substituir estagiário", disabled=person is None)
        if replace:
            try:
                new_id = service.replace(row["id"], person, start, limit, actor_email=principal.email, administrator=True)
                _audit(store, principal, "ESTAGIARIO_SUBSTITUIDO", "SUBSTITUIR", new_id, {"anterior_id": row["id"], "pessoa_id": person, "lotacao": row["lotacao"]})
                st.success("Substituição registrada; o vínculo anterior foi encerrado."); st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def render(store, principal):
    if not principal.administrator:
        raise ValueError("Apenas administradores podem acessar Estagiários.")
    service = EstagiariosStore(store)
    st.subheader("Estagiários")
    summary = service.summary()
    for col, label, value in zip(st.columns(4), ("Posições", "Ocupadas", "Disponíveis", "Encerramentos próximos"), (summary["total"], summary["ocupadas"], summary["disponiveis"], summary["encerramentos_proximos"])):
        col.metric(label, value)
    st.caption("Apenas vínculos com término em até 60 dias são considerados próximos.")
    _add_form(service, store, principal)
    rows = service.list()
    active = [row for row in rows if row["ativo"]]
    section_label("Lotação atual")
    by_lotacao = {lotacao: [row for row in active if row["lotacao"] == lotacao] for lotacao in LOTACOES}
    for first in range(0, len(LOTACOES), 2):
        columns = st.columns(2)
        for column, lotacao in zip(columns, LOTACOES[first:first + 2]):
            with column:
                with st.container(border=True):
                    st.markdown("**" + lotacao + "**")
                    for row in by_lotacao[lotacao]:
                        limit = date.fromisoformat(row["data_limite"])
                        st.write(row["nome"])
                        st.caption(f"Início: {date.fromisoformat(row['data_inicio']).strftime('%d/%m/%Y')} · Limite: {limit.strftime('%d/%m/%Y')}")
                        st.caption(_remaining(limit))
                        _active_controls(service, store, principal, row)
                    for _ in range(2 - len(by_lotacao[lotacao])):
                        st.caption("+ Vaga disponível")
                    history = [row for row in rows if row["lotacao"] == lotacao and not row["ativo"]]
                    with st.expander("Histórico"):
                        if not history:
                            st.caption("Sem vínculos encerrados.")
                        for item in history:
                            end = date.fromisoformat(item["data_encerramento"]).strftime("%d/%m/%Y") if item["data_encerramento"] else "—"
                            st.caption(f"{item['nome']} — {date.fromisoformat(item['data_inicio']).strftime('%d/%m/%Y')} a {end}")
