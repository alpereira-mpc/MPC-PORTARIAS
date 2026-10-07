"""Compact administrator screen for internship placement history."""

from datetime import date

import streamlit as st

from database.estagiarios import GABINETES, EstagiariosStore, LOTACOES, limite_padrao
from services.audit import registrar_evento
from services.ui_theme import badge, card_container, record_html, render_html, section_label


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
    with st.expander(
        "Cadastrar vínculo",
        expanded=bool(st.session_state.get("estagiario_create_expanded", False)),
    ):
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
                st.session_state["estagiario_create_expanded"] = False
                st.success("Vínculo cadastrado."); st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def _active_controls(service, store, principal, row):
    key = str(row["id"])
    with st.popover("Ações", use_container_width=False):
        with st.form("estagiario_edit_" + key):
            selected = LOTACOES.index(row["lotacao"]) if row["lotacao"] in LOTACOES else 0
            lotacao = st.selectbox("Lotação", LOTACOES, index=selected, key="estagiario_edit_lotacao_" + key)
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


def _compact_styles():
    render_html(
        """
        <style>
        [class*="st-key-estagiarios_panel"] [data-testid="stMetric"] {
            padding:.35rem .55rem !important; min-height:0 !important;
        }
        [class*="st-key-estagiarios_panel"] [data-testid="stMetricLabel"] {
            font-size:.72rem !important; line-height:1.1 !important;
        }
        [class*="st-key-estagiarios_panel"] [data-testid="stMetricValue"] {
            font-size:1.4rem !important; line-height:1.15 !important;
        }
        [class*="st-key-estagiarios_cabinet_"] {
            padding:.35rem .55rem .45rem !important;
        }
        [class*="st-key-estagiarios_cabinet_"] [data-testid="stVerticalBlock"] {
            gap:.25rem !important;
        }
        [class*="st-key-estagiarios_person_"] {
            padding:.1rem 0 !important;
            border-bottom:1px solid var(--mpc-border) !important;
        }
        [class*="st-key-estagiarios_person_"]:last-child { border-bottom:0 !important; }
        [class*="st-key-estagiarios_person_"] .mpc-record { padding:.05rem 0 !important; }
        [class*="st-key-estagiarios_person_"] .mpc-record-title { font-size:.92rem !important; }
        [class*="st-key-estagiarios_person_"] .mpc-record-secondary,
        [class*="st-key-estagiarios_person_"] .mpc-record-meta { margin:.1rem 0 0 !important; font-size:.76rem !important; }
        [class*="st-key-estagiarios_cabinet_"] .stPopover button,
        [class*="st-key-estagiarios_cabinet_"] button[kind="secondary"] { min-height:1.8rem !important; padding:.1rem .45rem !important; font-size:.76rem !important; }
        @media (max-width:700px) {
          [class*="st-key-estagiarios_row_"] > [data-testid="stVerticalBlock"] > [data-testid="stHorizontalBlock"] {
            flex-direction:column !important;
          }
        }
        </style>
        """
    )


def _person_row(service, store, principal, row):
    limit = date.fromisoformat(row["data_limite"])
    days = (limit - date.today()).days
    tone = "warning" if days <= 60 else "neutral"
    columns = st.columns([9, 2])
    with columns[0]:
        render_html(
            record_html(
                row["nome"],
                badges_html=badge(_remaining(limit), tone),
                meta=(
                    f"Início {date.fromisoformat(row['data_inicio']).strftime('%d/%m/%Y')}"
                    f" · Limite {limit.strftime('%d/%m/%Y')}"
                ),
                accent="neutral",
            )
        )
    with columns[1]:
        _active_controls(service, store, principal, row)


def _vacancy(lotacao, position):
    left, right = st.columns([8, 3])
    left.caption("Vaga disponível")
    if right.button("Cadastrar", key=f"estagiario_vacancy_{lotacao}_{position}"):
        st.session_state["estagiario_create_expanded"] = True
        st.rerun()


def render(store, principal):
    if not principal.administrator:
        raise ValueError("Apenas administradores podem acessar Estagiários.")
    service = EstagiariosStore(store)
    st.subheader("Estagiários")
    with st.container(key="estagiarios_panel"):
        _compact_styles()
        summary = service.summary()
        for col, label, value in zip(st.columns(4), ("Posições", "Ocupadas", "Disponíveis", "Encerramentos próximos"), (summary["total"], summary["ocupadas"], summary["disponiveis"], summary["encerramentos_proximos"])):
            col.metric(label, value)
        st.caption("Encerramentos próximos: vínculos com término em até 60 dias.")
        _add_form(service, store, principal)
        rows = service.list()
        active = [row for row in rows if row["ativo"] and row["lotacao"] in LOTACOES]
        legacy = service.legacy_proge_active()
        if legacy:
            st.warning("Há vínculo(s) ativo(s) legado(s) em PROGE. Reatribua cada um a um dos sete gabinetes confirmados; nenhuma reatribuição foi presumida.")
            for row in legacy:
                with st.container(border=True):
                    st.markdown("**PROGE - ajuste administrativo pendente**")
                    st.caption(row["nome"] + " · vínculo ativo legado")
                    _active_controls(service, store, principal, row)
        section_label("Composição atual")
        by_lotacao = {lotacao: [row for row in active if row["lotacao"] == lotacao] for lotacao in LOTACOES}
        for first in range(0, len(LOTACOES), 2):
            with st.container(key=f"estagiarios_row_{first}"):
                columns = st.columns(2)
                for column, lotacao in zip(columns, LOTACOES[first:first + 2]):
                    with column:
                        occupied = by_lotacao[lotacao]
                        with card_container(first, f"estagiarios_cabinet_{lotacao}"):
                            render_html(
                                record_html(
                                    lotacao,
                                    secondary=GABINETES[lotacao],
                                    badges_html=badge(f"{len(occupied)}/2", "brand"),
                                    accent="brand",
                                )
                            )
                            for row in occupied:
                                with st.container(key=f"estagiarios_person_{row['id']}"):
                                    _person_row(service, store, principal, row)
                            for position in range(len(occupied) + 1, 3):
                                _vacancy(lotacao, position)
                            history = [row for row in rows if row["lotacao"] == lotacao and not row["ativo"]]
                            if history:
                                with st.popover("Ver histórico", use_container_width=False):
                                    for item in history:
                                        end = date.fromisoformat(item["data_encerramento"]).strftime("%d/%m/%Y") if item["data_encerramento"] else "—"
                                        st.caption(f"{item['nome']} · {date.fromisoformat(item['data_inicio']).strftime('%d/%m/%Y')} a {end}")
    st.divider()
    section_label("Composição atual")
    from document_generator.estagiarios_pdf import generate_estagiarios_pdf

    pdf = generate_estagiarios_pdf(service.current_composition())
    st.download_button(
        "Baixar composição atual em PDF",
        pdf,
        file_name="Estagiarios_MPC_PB.pdf",
        mime="application/pdf",
        key="estagiarios_download_pdf",
    )
