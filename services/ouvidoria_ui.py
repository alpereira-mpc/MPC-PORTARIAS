"""Streamlit UI for Ouvidoria. Follows existing portal visual patterns."""

from datetime import date
import streamlit as st

from services.access import has_permission, require_permission
from services.branding import module_title
from services.date_format import format_date_br
from services.ouvidoria import (
    ANDAMENTOS,
    CLASSIFICACOES,
    DOCUMENTOS,
    DOCUMENTOS_RECEBIDOS,
    FORMAS,
    PRIORIDADES,
    PROVIDENCIAS,
    RESULTADOS,
    SITUACOES,
    TIPOS,
    actions,
    add_action,
    add_document,
    add_progress,
    andamento_display,
    assessores,
    can_delete,
    close,
    create,
    create_projeto_representacao,
    default_ouvidor_id,
    delete,
    delete_blocked_reason,
    documents,
    download,
    get,
    grouped_members,
    label,
    list_records,
    overview,
    progress,
    procuradores,
    representation_open_label,
    tipo_visivel,
    update,
    update_action,
)
from services.representacoes import kind_label
from services.ui_theme import (
    actions_mark,
    badges,
    card_container,
    definition_block,
    empty_state,
    filter_mark,
    form_mark,
    html_text,
    kpi_mark,
    render_record,
    section_label,
    status_tone,
)


_FILTER_ALL = "__todas__"


def _detail_chrome(prefix):
    st.markdown(
        "<style>"
        f'div[class*="st-key-{prefix}_detail"]{{'
        "background:transparent!important;"
        "background-color:transparent!important;"
        "border:none!important;"
        "box-shadow:none!important;"
        "}"
        f'div[class*="st-key-{prefix}_toolbar"] [data-testid="stHorizontalBlock"]{{'
        "justify-content:flex-start;align-items:center;gap:.45rem;flex-wrap:wrap;"
        "}"
        f'div[class*="st-key-{prefix}_toolbar"] [data-testid="stHorizontalBlock"]>div{{'
        "flex:0 1 auto!important;width:auto!important;min-width:0;"
        "}"
        f'div[class*="st-key-{prefix}_note"]{{'
        "background:var(--mpc-info-soft)!important;"
        "background-color:var(--mpc-info-soft)!important;"
        "border-left:3px solid var(--mpc-info)!important;"
        "}"
        "@media (max-width:768px){"
        f'div[class*="st-key-{prefix}_toolbar"] [data-testid="stHorizontalBlock"]{{'
        "flex-wrap:wrap;"
        "}"
        "}"
        "</style>",
        unsafe_allow_html=True,
    )


def _filter_select(label, mapping, empty, key):
    chosen = st.selectbox(
        label,
        [_FILTER_ALL, *mapping],
        format_func=lambda x, names=mapping, blank=empty: blank if x == _FILTER_ALL else names[x],
        placeholder=empty,
        key=key,
    )
    return None if chosen == _FILTER_ALL else chosen


def _toolbar(prefix, items):
    shown = [item for item in items if item]
    if not shown:
        return None
    with st.container(key=prefix):
        actions_mark()
        columns = st.columns(len(shown))
        pressed = None
        for column, (title, key, kind) in zip(columns, shown):
            if column.button(title, key=key, type=kind or "secondary"):
                pressed = key
        return pressed


def _done(message):
    st.session_state["ouvidoria_message"] = message
    st.rerun()


def _people_options(store):
    members = procuradores(store)
    servers = assessores(store)
    return {row["id"]: row["nome"] for row in members}, {row["id"]: row["nome"] for row in servers}


def _badge_items(record):
    chips = [
        record.get("numero_interno") or "",
        label(SITUACOES, record.get("situacao")),
        label(CLASSIFICACOES, record.get("classificacao_acesso")),
    ]
    if record.get("representacao_id"):
        chips.append("Vinculada a Representações")
    return [(chip, status_tone(chip)) for chip in chips if chip]


def _member_defaults(record, people):
    groups = grouped_members(record or {})
    ouvidor = next((m["membro_id"] for m in groups.get("OUVIDOR") or []), None)
    responsible = (record or {}).get("procurador_responsavel_id") or next(
        (m["membro_id"] for m in groups.get("PROCURADOR_RESPONSAVEL") or []), None
    )
    participants = [m["membro_id"] for m in groups.get("PROCURADOR_PARTICIPANTE") or []]
    helpers = [m["membro_id"] for m in groups.get("ASSESSOR") or []]
    return ouvidor, responsible, participants, helpers


def _form(store, current=None):
    prefix = "ouvi_form_" + str((current or {}).get("id") or "new")
    current = current or {}
    people, servers = _people_options(store)
    if not people:
        st.warning("Cadastre um Procurador ativo na base institucional.")
        return
    form_mark()
    title = st.text_input("Título/assunto *", value=current.get("titulo") or "", key=prefix + "titulo")
    resumo = st.text_area("Resumo do fato", value=current.get("resumo") or "", key=prefix + "resumo")
    a, b, c = st.columns(3)
    tipo = a.selectbox(
        "Tipo",
        list(TIPOS),
        index=list(TIPOS).index(current.get("tipo") or "NOTICIA_FATO"),
        format_func=TIPOS.get,
        key=prefix + "tipo",
    )
    forma = b.selectbox(
        "Forma de recebimento",
        list(FORMAS),
        index=list(FORMAS).index(current.get("forma_recebimento") or "EMAIL"),
        format_func=FORMAS.get,
        key=prefix + "forma",
    )
    received = c.date_input(
        "Data de recebimento",
        date.fromisoformat(current["data_recebimento"]) if current.get("data_recebimento") else date.today(),
        format="DD/MM/YYYY",
        key=prefix + "data",
    )
    representado = st.text_input("Representado/órgão", value=current.get("representado") or "", key=prefix + "rep")
    tema = st.text_input("Tema/área", value=current.get("tema") or "", key=prefix + "tema")
    d, e, f = st.columns(3)
    classificacao = d.selectbox(
        "Classificação de acesso",
        list(CLASSIFICACOES),
        index=list(CLASSIFICACOES).index(current.get("classificacao_acesso") or "RESTRITA"),
        format_func=CLASSIFICACOES.get,
        key=prefix + "class",
    )
    prioridade = e.selectbox(
        "Prioridade",
        list(PRIORIDADES),
        index=list(PRIORIDADES).index(current.get("prioridade") or "NORMAL"),
        format_func=PRIORIDADES.get,
        key=prefix + "prio",
    )
    situacao = f.selectbox(
        "Situação",
        list(SITUACOES),
        index=list(SITUACOES).index(current.get("situacao") or "RECEBIDA"),
        format_func=SITUACOES.get,
        key=prefix + "sit",
    )
    resultado = st.selectbox(
        "Resultado/desfecho",
        [_FILTER_ALL, *RESULTADOS],
        index=(
            [_FILTER_ALL, *RESULTADOS].index(current["resultado"])
            if current.get("resultado") in RESULTADOS
            else 0
        ),
        format_func=lambda x: "Não informado" if x == _FILTER_ALL else RESULTADOS[x],
        placeholder="Não informado",
        key=prefix + "res",
    )
    conclusao = st.text_area(
        "Conclusão da análise", value=current.get("conclusao_analise") or "", key=prefix + "conc"
    )
    identified = st.radio(
        "Manifestante identificado?",
        (False, True),
        index=1 if current.get("manifestante_identificado") else 0,
        format_func=lambda v: "Sim" if v else "Não",
        horizontal=True,
        key=prefix + "id",
    )
    nome = email = phone = ""
    if identified:
        nome = st.text_input("Nome", value=current.get("manifestante_nome") or "", key=prefix + "mnome")
        email = st.text_input("E-mail", value=current.get("manifestante_email") or "", key=prefix + "memail")
        phone = st.text_input("Telefone", value=current.get("manifestante_telefone") or "", key=prefix + "mphone")
    observacoes = st.text_area("Observações internas", value=current.get("observacoes") or "", key=prefix + "obs")
    section_label("Equipe")
    ouvidor_default, responsible_default, participants, helpers = _member_defaults(current, people)
    if not current:
        ouvidor_default = default_ouvidor_id(store) or next(iter(people), None)
        responsible_default = ouvidor_default
    ouvidor = st.selectbox(
        "Ouvidor *",
        list(people),
        index=list(people).index(ouvidor_default) if ouvidor_default in people else 0,
        format_func=people.get,
        key=prefix + "ouv",
    )
    responsible = st.selectbox(
        "Procurador responsável *",
        list(people),
        index=list(people).index(responsible_default) if responsible_default in people else 0,
        format_func=people.get,
        key=prefix + "resp",
    )
    participantes = st.multiselect(
        "Procuradores participantes",
        [i for i in people if i != responsible],
        default=[i for i in participants if i in people and i != responsible],
        format_func=people.get,
        key=prefix + "part",
    )
    assessor_ids = st.multiselect(
        "Assessores",
        list(servers),
        default=[i for i in helpers if i in servers],
        format_func=servers.get,
        key=prefix + "ass",
    )
    uploads = []
    documento_tipo = "DOCUMENTO_RECEBIDO"
    if not current.get("id"):
        section_label("Documentos recebidos")
        st.caption("PDF ou DOCX, até 10 MB por arquivo. A classificação detalhada pode ser ajustada depois.")
        documento_tipo = st.selectbox(
            "Tipo documental dos anexos",
            list(DOCUMENTOS_RECEBIDOS),
            index=0,
            format_func=DOCUMENTOS_RECEBIDOS.get,
            key=prefix + "doctype",
        )
        uploaded = st.file_uploader(
            "Arquivos (PDF ou DOCX, até 10 MB cada)",
            type=["pdf", "docx"],
            accept_multiple_files=True,
            key=prefix + "files",
        )
        uploads = [(item.name, item.getvalue()) for item in uploaded or []]
    return {
        "titulo": title,
        "resumo": resumo,
        "tipo": tipo,
        "forma_recebimento": forma,
        "data_recebimento": received.isoformat(),
        "representado": representado,
        "tema": tema,
        "classificacao_acesso": classificacao,
        "prioridade": prioridade,
        "situacao": situacao,
        "resultado": None if resultado == _FILTER_ALL else resultado,
        "conclusao_analise": conclusao,
        "manifestante_identificado": identified,
        "manifestante_nome": nome,
        "manifestante_email": email,
        "manifestante_telefone": phone,
        "observacoes": observacoes,
        "ouvidor_id": ouvidor,
        "procurador_responsavel_id": responsible,
        "procuradores_participantes": participantes,
        "assessores": assessor_ids,
        "documento_tipo": documento_tipo,
        "_uploads": uploads,
    }


def _card(record, index, people, servers):
    groups = grouped_members(record)
    last = record.get("ultimo_andamento") or {}
    names = lambda items: ", ".join(people.get(m["membro_id"], "—") for m in items) or "—"
    natureza = tipo_visivel(record.get("tipo"))
    received = "Recebida em: " + format_date_br(record.get("data_recebimento"))
    if natureza:
        received = natureza + " · " + received
    with card_container(index, "ouvi_" + str(record["id"])):
        render_record(
            (record.get("numero_interno") or "") + " — " + record["titulo"],
            badges_html=badges(*_badge_items(record)),
            secondary=received
            + " · Representado: " + (record.get("representado") or "—"),
            meta="Responsável: "
            + (people.get(record.get("procurador_responsavel_id")) or "—")
            + " · Participantes: "
            + names(groups.get("PROCURADOR_PARTICIPANTE") or []),
            extra=(
                '<p class="mpc-record-meta">'
                + html_text(
                    (andamento_display(last)[0] if last else "")
                    + (" — " + format_date_br(last.get("data")) if last else "")
                )
                + "</p>"
            ),
            boxed=True,
        )
        actions_mark()
        a, b, c, d, e = st.columns(5)
        if a.button("Abrir", key="ouvi_open_" + str(record["id"])):
            st.session_state["ouvidoria_view"] = record["id"]
            st.rerun()
        if b.button("Andamento", key="ouvi_prg_" + str(record["id"])):
            st.session_state["ouvidoria_progress"] = record["id"]
            st.rerun()
        if c.button("Providência", key="ouvi_act_" + str(record["id"])):
            st.session_state["ouvidoria_action"] = record["id"]
            st.rerun()
        if d.button("Anexar", key="ouvi_doc_" + str(record["id"])):
            st.session_state["ouvidoria_file"] = record["id"]
            st.rerun()
        if e.button("Editar", key="ouvi_ed_" + str(record["id"])):
            st.session_state["ouvidoria_edit"] = record["id"]
            st.rerun()


def _detail(store, principal, record):
    _detail_chrome("ouvi")
    with st.container(key="ouvi_detail"):
        people, servers = _people_options(store)
        groups = grouped_members(record)
        st.subheader(record.get("numero_interno") or "Notícia de fato da Ouvidoria")
        render_record(record["titulo"], badges_html=badges(*_badge_items(record)), boxed=True)
        definition_block(
            "Identificação",
            (
                ("Número", record.get("numero_interno")),
                ("Tipo", tipo_visivel(record.get("tipo"))),
                ("Recebimento", label(FORMAS, record.get("forma_recebimento"))),
                ("Data", format_date_br(record.get("data_recebimento"))),
                ("Classificação", label(CLASSIFICACOES, record.get("classificacao_acesso"))),
                ("Prioridade", label(PRIORIDADES, record.get("prioridade"))),
                ("Situação", label(SITUACOES, record.get("situacao"))),
                ("Resultado", label(RESULTADOS, record.get("resultado"))),
                ("Representado", record.get("representado")),
                ("Tema", record.get("tema")),
                ("Resumo", record.get("resumo")),
                ("Observações", record.get("observacoes")),
            ),
        )
        if record.get("manifestante_identificado"):
            definition_block(
                "Manifestante",
                (
                    ("Nome", record.get("manifestante_nome")),
                    ("E-mail", record.get("manifestante_email")),
                    ("Telefone", record.get("manifestante_telefone")),
                ),
            )
        else:
            st.caption("Manifestante não identificado.")
        definition_block(
            "Equipe",
            (
                ("Ouvidor", ", ".join(people.get(m["membro_id"], "—") for m in groups.get("OUVIDOR") or []) or "—"),
                ("Responsável", people.get(record.get("procurador_responsavel_id")) or "—"),
                (
                    "Participantes",
                    ", ".join(people.get(m["membro_id"], "—") for m in groups.get("PROCURADOR_PARTICIPANTE") or []) or "—",
                ),
                (
                    "Assessores",
                    ", ".join(servers.get(m["membro_id"], "—") for m in groups.get("ASSESSOR") or []) or "—",
                ),
            ),
        )
        if record.get("conclusao_analise"):
            definition_block("Conclusão da análise", (("Texto", record["conclusao_analise"]),))
        linked = None
        if record.get("representacao_id"):
            if has_permission(principal, "representacoes"):
                from services.representacoes import get as get_rep

                linked = get_rep(store, record["representacao_id"])
            definition_block(
                "Representação vinculada",
                (
                    ("Situação", kind_label(linked) if linked else "Projeto de Representação"),
                    ("Título", linked["titulo"] if linked else None),
                    ("Processo", linked.get("numero_processo") if linked else None),
                ),
            )
        else:
            section_label("Representação vinculada")
            st.caption("Nenhum Projeto de Representação vinculado.")
            if not has_permission(principal, "representacoes"):
                st.caption("É necessária permissão no módulo Representações para criar o Projeto.")
        section_label("Providências")
        rows = actions(store, record["id"])
        if not rows:
            empty_state("Nenhuma providência registrada.")
        for item in rows:
            st.markdown(
                f"**{format_date_br(item['data'])}** · {label(PROVIDENCIAS, item['tipo'])}  \n{item.get('descricao') or ''}"
            )
            if st.button("Editar providência", key="ouvi_ed_act_" + str(item["id"])):
                st.session_state["ouvidoria_action_edit"] = (record["id"], item["id"])
                st.rerun()
        section_label("Andamentos")
        timeline = progress(store, record["id"])
        for item in timeline:
            heading, descricao = andamento_display(item)
            line = f"**{format_date_br(item['data'])}** · {heading}"
            if descricao:
                line += f"  \n{descricao}"
            st.markdown(line)
        section_label("Documentos")
        files = documents(store, record["id"])
        if not files:
            empty_state("Nenhum documento anexado.")
        pending = st.session_state.get("ouvidoria_download")
        for item in files:
            cols = st.columns([3, 2, 2, 2])
            cols[0].write(label(DOCUMENTOS, item["tipo_documento"]))
            cols[1].caption(item.get("descricao") or item["nome_arquivo"])
            cols[2].caption(format_date_br(item.get("data_documento"), empty=""))
            if pending == item["id"]:
                file = download(store, item["id"], principal)
                cols[3].download_button("Baixar", file["conteudo"], file["nome"], file["tipo"], key="ouvi_dl_" + item["id"])
            elif cols[3].button("Preparar download", key="ouvi_prep_" + item["id"]):
                from services.audit import registrar_download

                registrar_download(
                    store,
                    modulo="ouvidoria",
                    entidade_tipo="noticia_fato",
                    entidade_id=record["id"],
                    arquivo=item.get("nome_arquivo"),
                    formato=item.get("mime_type"),
                    rotulo=record.get("numero_interno") or record.get("titulo"),
                    principal=principal,
                )
                st.session_state["ouvidoria_download"] = item["id"]
                st.rerun()
        from services.internal_collaboration_ui import render_internal_collaboration

        render_internal_collaboration(store, principal, "ouvidoria", record["id"])
        from services.record_engagement_ui import render_origin_tools

        render_origin_tools(store, principal, "ouvidoria", record["id"], record["titulo"])
        flow = _toolbar(
            "ouvi_toolbar_flow",
            [
                (representation_open_label(linked), "ouvi_goto_rep", "primary")
                if linked and has_permission(principal, "representacoes")
                else None,
                ("Criar Projeto de Representação", "ouvi_mk_rep", "primary")
                if not record.get("representacao_id") and has_permission(principal, "representacoes")
                else None,
                ("Andamento", "ouvi_dt_prg", "secondary"),
                ("Providência", "ouvi_dt_act", "secondary"),
                ("Anexar documento", "ouvi_dt_doc", "secondary"),
                ("Editar", "ouvi_dt_ed", "secondary"),
            ],
        )
        if flow == "ouvi_goto_rep":
            from portal import request_portal_navigation

            request_portal_navigation("Representações", representacoes_view=linked["id"])
        elif flow == "ouvi_mk_rep":
            st.session_state["ouvidoria_to_rep"] = record["id"]
            st.rerun()
        elif flow == "ouvi_dt_prg":
            st.session_state["ouvidoria_progress"] = record["id"]
            st.rerun()
        elif flow == "ouvi_dt_act":
            st.session_state["ouvidoria_action"] = record["id"]
            st.rerun()
        elif flow == "ouvi_dt_doc":
            st.session_state["ouvidoria_file"] = record["id"]
            st.rerun()
        elif flow == "ouvi_dt_ed":
            st.session_state["ouvidoria_edit"] = record["id"]
            st.rerun()
        state = _toolbar(
            "ouvi_toolbar_state",
            [
                ("Arquivar", "ouvi_arch", "secondary"),
                ("Encerrar", "ouvi_end", "secondary"),
            ],
        )
        if state == "ouvi_arch":
            close(store, record["id"], "ARQUIVADA", principal)
            _done("Notícia de fato arquivada.")
        elif state == "ouvi_end":
            close(store, record["id"], "ENCERRADA", principal)
            _done("Notícia de fato encerrada.")
        reason = delete_blocked_reason(
            record, providencias=rows, documentos=files, andamentos=timeline
        )
        if reason:
            with st.container(key="ouvi_note"):
                st.caption(reason)
        elif st.session_state.get("ouvidoria_confirm_delete") == record["id"]:
            st.warning(
                "Tem certeza de que deseja excluir definitivamente esta notícia de fato?  \n"
                "Esta ação removerá o registro de forma irreversível."
            )
            confirm = _toolbar(
                "ouvi_toolbar_confirm",
                [
                    ("Excluir definitivamente", "ouvi_del_yes", "primary"),
                    ("Cancelar", "ouvi_del_no", "secondary"),
                ],
            )
            if confirm == "ouvi_del_yes":
                delete(store, record["id"])
                st.session_state.pop("ouvidoria_view", None)
                st.session_state.pop("ouvidoria_confirm_delete", None)
                _done("Notícia de fato excluída.")
            elif confirm == "ouvi_del_no":
                st.session_state.pop("ouvidoria_confirm_delete", None)
                st.rerun()
        extra = _toolbar(
            "ouvi_toolbar_more",
            [
                None if reason or st.session_state.get("ouvidoria_confirm_delete") == record["id"] else ("Excluir definitivamente", "ouvi_del", "secondary"),
                ("Voltar", "ouvi_dt_back", "secondary"),
            ],
        )
        if extra == "ouvi_del":
            st.session_state["ouvidoria_confirm_delete"] = record["id"]
            st.rerun()
        elif extra == "ouvi_dt_back":
            st.session_state.pop("ouvidoria_view", None)
            st.rerun()


def render(store, principal):
    require_permission(principal, "ouvidoria")
    st.title(module_title("ouvidoria", "OUVIDORIA"))
    st.caption("Notícias de fato da Ouvidoria. O TRAMITA permanece o sistema oficial.")
    if message := st.session_state.pop("ouvidoria_message", None):
        st.success(message)
    opened = st.session_state.pop("ouvidoria_open_id", None)
    if opened:
        st.session_state["ouvidoria_view"] = opened
    counts = overview(store)
    items = (
        ("Recebidas", counts["recebidas"], "muted"),
        ("Em análise", counts["analise"], "brand"),
        ("Aguardando providência", counts["aguardando"], "warning"),
        ("Providência em andamento", counts["providencia"], "info"),
        ("Geraram Representação", counts["representacao"], "success"),
        ("Arquivadas", counts["arquivadas"], "muted"),
    )
    columns = st.columns(len(items))
    for column, (title, value, tone) in zip(columns, items):
        with column:
            kpi_mark(tone)
            st.metric(title, value)
    if st.button("+ Nova notícia de fato", type="primary", key="ouvi_new"):
        st.session_state["ouvidoria_edit"] = {}
        st.rerun()
    if st.session_state.get("ouvidoria_to_rep"):
        identifier = st.session_state["ouvidoria_to_rep"]
        current = get(store, identifier)
        section_label("Criar Projeto de Representação")
        st.caption("Revise os dados antes de salvar. Documentos da Ouvidoria não são copiados.")
        title = st.text_input("Título", value=current["titulo"], key="ouvi_rep_title")
        objeto = st.text_area("Objeto/resumo", value=current.get("resumo") or "", key="ouvi_rep_obj")
        if st.button("Confirmar criação", type="primary", key="ouvi_rep_ok"):
            try:
                saved = create_projeto_representacao(
                    store, identifier, principal, {"titulo": title, "objeto": objeto}
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("ouvidoria_to_rep", None)
                st.session_state["ouvidoria_view"] = saved["id"]
                _done("Projeto de Representação criado e vinculado.")
        if st.button("Cancelar", key="ouvi_rep_cancel"):
            st.session_state.pop("ouvidoria_to_rep", None)
            st.rerun()
        return
    if st.session_state.get("ouvidoria_progress"):
        identifier = st.session_state["ouvidoria_progress"]
        section_label("Novo andamento")
        tipo = st.selectbox("Tipo", list(ANDAMENTOS), format_func=ANDAMENTOS.get, key="ouvi_p_tipo")
        day = st.date_input("Data", date.today(), format="DD/MM/YYYY", key="ouvi_p_data")
        descricao = st.text_area("Descrição", key="ouvi_p_desc")
        if st.button("Registrar", type="primary", key="ouvi_p_ok"):
            add_progress(store, identifier, {"tipo": tipo, "data": day.isoformat(), "descricao": descricao}, principal)
            st.session_state.pop("ouvidoria_progress", None)
            _done("Andamento registrado.")
        if st.button("Cancelar", key="ouvi_p_cancel"):
            st.session_state.pop("ouvidoria_progress", None)
            st.rerun()
        return
    if st.session_state.get("ouvidoria_action"):
        identifier = st.session_state["ouvidoria_action"]
        section_label("Registrar providência")
        tipo = st.selectbox("Tipo", list(PROVIDENCIAS), format_func=PROVIDENCIAS.get, key="ouvi_a_tipo")
        day = st.date_input("Data", date.today(), format="DD/MM/YYYY", key="ouvi_a_data")
        descricao = st.text_area("Descrição", key="ouvi_a_desc")
        if st.button("Registrar", type="primary", key="ouvi_a_ok"):
            add_action(store, identifier, {"tipo": tipo, "data": day.isoformat(), "descricao": descricao}, principal)
            st.session_state.pop("ouvidoria_action", None)
            _done("Providência registrada.")
        if st.button("Cancelar", key="ouvi_a_cancel"):
            st.session_state.pop("ouvidoria_action", None)
            st.rerun()
        return
    if st.session_state.get("ouvidoria_action_edit"):
        identifier, action_id = st.session_state["ouvidoria_action_edit"]
        current_action = next((row for row in actions(store, identifier) if row["id"] == action_id), None)
        section_label("Editar providência")
        tipo = st.selectbox(
            "Tipo",
            list(PROVIDENCIAS),
            index=list(PROVIDENCIAS).index(current_action["tipo"]) if current_action and current_action["tipo"] in PROVIDENCIAS else 0,
            format_func=PROVIDENCIAS.get,
            key="ouvi_ae_tipo",
        )
        day = st.date_input(
            "Data",
            date.fromisoformat(current_action["data"]) if current_action else date.today(),
            format="DD/MM/YYYY",
            key="ouvi_ae_data",
        )
        descricao = st.text_area(
            "Descrição",
            value=(current_action or {}).get("descricao") or "",
            key="ouvi_ae_desc",
        )
        if st.button("Salvar", type="primary", key="ouvi_ae_ok"):
            update_action(
                store,
                identifier,
                action_id,
                {"tipo": tipo, "data": day.isoformat(), "descricao": descricao},
                principal,
            )
            st.session_state.pop("ouvidoria_action_edit", None)
            _done("Providência atualizada.")
        if st.button("Cancelar", key="ouvi_ae_cancel"):
            st.session_state.pop("ouvidoria_action_edit", None)
            st.rerun()
        return
    if st.session_state.get("ouvidoria_file"):
        identifier = st.session_state["ouvidoria_file"]
        section_label("Anexar documento")
        tipo = st.selectbox("Tipo", list(DOCUMENTOS), format_func=DOCUMENTOS.get, key="ouvi_f_tipo")
        day = st.date_input("Data", date.today(), format="DD/MM/YYYY", key="ouvi_f_data")
        descricao = st.text_input("Descrição", key="ouvi_f_desc")
        uploaded = st.file_uploader("Arquivo (PDF ou DOCX, até 10 MB)", type=["pdf", "docx"], key="ouvi_f_file")
        if st.button("Anexar", type="primary", key="ouvi_f_ok"):
            if uploaded is None:
                st.error("Selecione um arquivo.")
            else:
                try:
                    add_document(
                        store,
                        identifier,
                        {"tipo_documento": tipo, "descricao": descricao, "data_documento": day.isoformat()},
                        uploaded.name,
                        uploaded.getvalue(),
                        principal,
                    )
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    st.session_state.pop("ouvidoria_file", None)
                    _done("Documento anexado.")
        if st.button("Cancelar", key="ouvi_f_cancel"):
            st.session_state.pop("ouvidoria_file", None)
            st.rerun()
        return
    edit_id = st.session_state.get("ouvidoria_edit")
    if edit_id is not None:
        current = None if edit_id == {} else get(store, edit_id)
        st.subheader("Editar notícia de fato" if current else "Nova notícia de fato recebida pela Ouvidoria")
        payload = _form(store, current)
        if payload and st.button("Salvar", type="primary", key="ouvi_save"):
            try:
                uploads = payload.pop("_uploads", None)
                if current:
                    saved = update(store, current["id"], payload, principal)
                else:
                    saved = create(store, payload, principal, uploads)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state.pop("ouvidoria_edit", None)
                st.session_state["ouvidoria_view"] = saved["id"]
                _done("Notícia de fato recebida pela Ouvidoria.")
        if st.button("Cancelar", key="ouvi_cancel"):
            st.session_state.pop("ouvidoria_edit", None)
            st.rerun()
        return
    view_id = st.session_state.get("ouvidoria_view")
    if view_id:
        record = get(store, view_id)
        if record is None:
            st.session_state.pop("ouvidoria_view", None)
            st.warning("Registro não encontrado.")
        else:
            _detail(store, principal, record)
            return
    filter_mark()
    people, servers = _people_options(store)
    a, b, c, d = st.columns(4)
    with a:
        situacao = _filter_select("Situação", SITUACOES, "Todas", "ouvi_fs")
    with b:
        resultado = _filter_select("Resultado", RESULTADOS, "Todos", "ouvi_fr")
    with c:
        tipo = _filter_select("Tipo", TIPOS, "Todos", "ouvi_ft")
    with d:
        forma = _filter_select("Recebimento", FORMAS, "Todas", "ouvi_ff")
    e, f, g, h = st.columns(4)
    year_options = {year: str(year) for year in range(date.today().year, 2023, -1)}
    with e:
        ano = _filter_select("Ano", year_options, "Todos", "ouvi_fyear")
    with f:
        classificacao = _filter_select("Classificação", CLASSIFICACOES, "Todas", "ouvi_fc")
    representado = g.text_input("Representado", key="ouvi_frep")
    pesquisa = h.text_input("Pesquisa / OUVI", key="ouvi_fq")
    i, j, k = st.columns(3)
    with i:
        resp = _filter_select("Procurador responsável", people, "Todos", "ouvi_fresp")
    with j:
        part = _filter_select("Procurador participante", people, "Todos", "ouvi_fpart")
    with k:
        ass = _filter_select("Assessor", servers, "Todos", "ouvi_fass")
    rows = list_records(
        store,
        {
            "situacao": situacao,
            "resultado": resultado,
            "tipo": tipo,
            "forma_recebimento": forma,
            "ano": ano,
            "classificacao_acesso": classificacao,
            "representado": representado,
            "pesquisa": pesquisa,
            "procurador_responsavel_id": resp,
            "procurador_id": part,
            "assessor_id": ass,
        },
    )
    if not rows:
        empty_state("Nenhuma notícia de fato encontrada.")
        return
    for index, record in enumerate(rows):
        _card(record, index, people, servers)
