"""Streamlit UI for Representações. Follows existing portal visual patterns."""

from datetime import date
import streamlit as st

from services.access import require_permission
from services.branding import module_title
from services.representacoes import (
    ANDAMENTOS,
    DOCUMENTOS,
    FASES,
    ORIGENS,
    PRIORIDADES,
    RELATORES,
    SITUACOES,
    add_document,
    add_progress,
    andamento_display,
    assessores,
    can_delete,
    create,
    delete,
    documents,
    download,
    get,
    grouped_members,
    is_protocolled,
    kind_label,
    kind_saved_message,
    label,
    list_records,
    overview,
    people_index,
    procuradores,
    progress,
    reconcile_signatories,
    signatory_options,
    register_protocol,
    relator_label,
    set_phase,
    set_status,
    update,
)
from services.ui_theme import (
    actions_mark,
    badges,
    card_container,
    definition_block,
    detail_mark,
    empty_state,
    filter_mark,
    form_mark,
    html_text,
    kpi_mark,
    render_record,
    section_label,
    status_tone,
)


def _done(message):
    st.session_state["representacoes_message"] = message
    st.rerun()


def _clear_forms():
    for key in (
        "representacoes_edit",
        "representacoes_view",
        "representacoes_protocol",
        "representacoes_progress",
        "representacoes_file",
        "representacoes_confirm_delete",
        "representacoes_download",
    ):
        st.session_state.pop(key, None)


def _people_options(store):
    members = procuradores(store)
    servers = assessores(store)
    return (
        {row["id"]: row["nome"] for row in members},
        {row["id"]: row["nome"] for row in servers},
    )


def _form(store, current=None):
    prefix = "rep_form_" + str((current or {}).get("id") or "new")
    current = current or {}
    people, servers = _people_options(store)
    if not people:
        st.warning("Cadastre um Procurador ativo na base institucional.")
        return
    form_mark()
    title = st.text_input("Título *", value=current.get("titulo") or "", key=prefix + "titulo")
    objeto = st.text_area("Objeto/resumo", value=current.get("objeto") or "", key=prefix + "objeto")
    left, right = st.columns(2)
    origem_keys = list(ORIGENS)
    origem = left.selectbox(
        "Origem",
        origem_keys,
        index=origem_keys.index(current.get("origem") or "DE_OFICIO"),
        format_func=ORIGENS.get,
        key=prefix + "origem",
    )
    opening = right.date_input(
        "Data de abertura",
        date.fromisoformat(current["data_abertura"]) if current.get("data_abertura") else date.today(),
        format="DD/MM/YYYY",
        key=prefix + "abertura",
    )
    representado = st.text_input(
        "Representado", value=current.get("representado") or "", key=prefix + "representado"
    )
    tema = st.text_input("Tema/área", value=current.get("tema") or "", key=prefix + "tema")
    prioridade_keys = list(PRIORIDADES)
    prioridade = st.selectbox(
        "Prioridade",
        prioridade_keys,
        index=prioridade_keys.index(current.get("prioridade") or "NORMAL"),
        format_func=PRIORIDADES.get,
        key=prefix + "prioridade",
    )
    observacoes = st.text_area(
        "Observações internas", value=current.get("observacoes") or "", key=prefix + "obs"
    )
    existing = current.get("integrantes") or []
    responsible = next(
        (item["membro_id"] for item in existing if item["papel"] == "PROCURADOR_RESPONSAVEL"),
        next(iter(people), None),
    )
    signatories = [
        item["membro_id"]
        for item in existing
        if item["papel"] == "PROCURADOR_SIGNATARIO"
    ]
    helpers = [item["membro_id"] for item in existing if item["papel"] == "ASSESSOR"]
    section_label("Equipe")
    procurador = st.selectbox(
        "Procurador responsável *",
        list(people),
        index=list(people).index(responsible) if responsible in people else 0,
        format_func=people.get,
        key=prefix + "resp",
    )
    sign_key = prefix + "sign"
    allowed_signatories = signatory_options(people, procurador)
    if sign_key in st.session_state:
        st.session_state[sign_key] = reconcile_signatories(
            st.session_state[sign_key], allowed_signatories
        )
        default_signatories = st.session_state[sign_key]
    else:
        default_signatories = reconcile_signatories(signatories, allowed_signatories)
    signatarios = st.multiselect(
        "Procuradores signatários",
        allowed_signatories,
        default=default_signatories,
        format_func=people.get,
        key=sign_key,
    )
    assessor_ids = st.multiselect(
        "Assessores",
        list(servers),
        default=[item for item in helpers if item in servers],
        format_func=servers.get,
        key=prefix + "ass",
    )
    if current:
        situacao_keys = list(SITUACOES)
        situacao = st.selectbox(
            "Situação",
            situacao_keys,
            index=situacao_keys.index(current.get("situacao") or "IDEIA"),
            format_func=SITUACOES.get,
            key=prefix + "sit",
        )
    else:
        situacao = "IDEIA"
    return {
        "titulo": title,
        "objeto": objeto,
        "origem": origem,
        "data_abertura": opening.isoformat(),
        "representado": representado,
        "tema": tema,
        "prioridade": prioridade,
        "observacoes": observacoes,
        "procurador_responsavel": procurador,
        "procuradores_signatarios": signatarios,
        "assessores": assessor_ids,
        "situacao": situacao,
    }


def _kpis(counts):
    items = (
        ("Em preparação", counts["preparacao"], "muted"),
        ("Aguardando protocolo", counts["aguardando_protocolo"], "warning"),
        ("Em tramitação", counts["tramitacao"], "brand"),
        ("Em fase MPC", counts["mpc"], "info"),
        ("Em pauta", counts["pauta"], "warning"),
        ("Julgadas", counts["julgadas"], "success"),
    )
    columns = st.columns(len(items))
    for column, (title, value, tone) in zip(columns, items):
        with column:
            kpi_mark(tone)
            st.metric(title, value)


def _filters():
    filter_mark()
    a, b, c, d = st.columns(4)
    situacao = a.selectbox(
        "Situação",
        [None, *SITUACOES],
        format_func=lambda x: "Todas" if x is None else SITUACOES[x],
        key="rep_f_sit",
    )
    fase = b.selectbox(
        "Fase processual",
        [None, *FASES],
        format_func=lambda x: "Todas" if x is None else FASES[x],
        key="rep_f_fase",
    )
    year = c.number_input("Ano", min_value=0, max_value=2100, value=0, step=1, key="rep_f_ano")
    pesquisa = d.text_input("Pesquisa", key="rep_f_q")
    e, f, g, h = st.columns(4)
    representado = e.text_input("Representado", key="rep_f_rep")
    tema = f.text_input("Tema", key="rep_f_tema")
    relator = g.text_input("Relator", key="rep_f_rel")
    processo = h.text_input("Nº do processo", key="rep_f_proc")
    people, servers = st.session_state.get("_rep_people") or ({}, {})
    i, j = st.columns(2)
    procurador = i.selectbox(
        "Procurador responsável",
        [None, *people],
        format_func=lambda x: "Todos" if x is None else people.get(x, str(x)),
        key="rep_f_procud",
    )
    assessor = j.selectbox(
        "Assessor",
        [None, *servers],
        format_func=lambda x: "Todos" if x is None else servers.get(x, str(x)),
        key="rep_f_ass",
    )
    filters = {
        "situacao": situacao,
        "fase_processual": fase,
        "representado": representado,
        "tema": tema,
        "relator": relator,
        "numero_processo": processo,
        "pesquisa": pesquisa,
        "ano": year or None,
    }
    filters["procurador_id"] = procurador
    filters["assessor_id"] = assessor
    return filters


def _badge_items(record):
    chips = [kind_label(record), label(SITUACOES, record["situacao"])]
    if record.get("numero_processo"):
        chips.insert(1, record["numero_processo"])
    if is_protocolled(record) and record.get("fase_processual"):
        chips.append(label(FASES, record["fase_processual"]))
    return [(chip, status_tone(chip)) for chip in chips]


def _card(record, index, procuradores_map, assessores_map):
    groups = grouped_members(record, procuradores_map, assessores_map)
    last = record.get("ultimo_andamento") or {}
    secondary = "Representado: " + (record.get("representado") or "—")
    meta = (
        "Procuradores: "
        + (", ".join(groups["PROCURADOR_RESPONSAVEL"] + groups["PROCURADOR_SIGNATARIO"]) or "—")
        + " · Assessores: "
        + (", ".join(groups["ASSESSOR"]) or "—")
    )
    if record.get("relator"):
        meta += " · Relator: " + record["relator"]
    extra = ""
    if last:
        extra = (
            '<p class="mpc-record-meta">'
            + html_text(label(ANDAMENTOS, last.get("tipo"), last.get("tipo")))
            + " — "
            + html_text(last.get("data") or "")
            + "</p>"
        )
    with card_container(index, "rep_" + str(record["id"])):
        render_record(
            record["titulo"],
            badges_html=badges(*_badge_items(record)),
            secondary=secondary,
            meta=meta,
            extra=extra,
            boxed=True,
        )
        actions_mark()
        a, b, c, d = st.columns(4)
        if a.button("Abrir", key="rep_open_" + str(record["id"])):
            st.session_state["representacoes_view"] = record["id"]
            st.rerun()
        if b.button("Andamento", key="rep_prg_" + str(record["id"])):
            st.session_state["representacoes_progress"] = record["id"]
            st.rerun()
        if c.button("Anexar", key="rep_doc_" + str(record["id"])):
            st.session_state["representacoes_file"] = record["id"]
            st.rerun()
        if d.button("Editar", key="rep_ed_" + str(record["id"])):
            st.session_state["representacoes_edit"] = record["id"]
            st.rerun()


def _progress_form(store, principal, identifier):
    section_label("Novo andamento")
    prefix = "rep_prg_form_" + str(identifier)
    tipo = st.selectbox(
        "Tipo",
        list(ANDAMENTOS),
        format_func=ANDAMENTOS.get,
        key=prefix + "tipo",
    )
    day = st.date_input("Data", date.today(), format="DD/MM/YYYY", key=prefix + "data")
    descricao = st.text_area("Descrição", key=prefix + "desc")
    if st.button("Registrar andamento", type="primary", key=prefix + "ok"):
        try:
            add_progress(
                store,
                identifier,
                {"tipo": tipo, "data": day.isoformat(), "descricao": descricao},
                principal,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("representacoes_progress", None)
            _done("Andamento registrado.")
    if st.button("Cancelar", key=prefix + "cancel"):
        st.session_state.pop("representacoes_progress", None)
        st.rerun()


def _document_form(store, principal, identifier):
    section_label("Anexar documento")
    prefix = "rep_doc_form_" + str(identifier)
    tipo = st.selectbox(
        "Tipo",
        list(DOCUMENTOS),
        format_func=DOCUMENTOS.get,
        key=prefix + "tipo",
    )
    day = st.date_input("Data do documento", date.today(), format="DD/MM/YYYY", key=prefix + "data")
    descricao = st.text_input("Descrição", key=prefix + "desc")
    uploaded = st.file_uploader("Arquivo (PDF ou DOCX, até 10 MB)", type=["pdf", "docx"], key=prefix + "file")
    if st.button("Anexar", type="primary", key=prefix + "ok"):
        if uploaded is None:
            st.error("Selecione um arquivo.")
            return
        try:
            add_document(
                store,
                identifier,
                {
                    "tipo_documento": tipo,
                    "descricao": descricao,
                    "data_documento": day.isoformat(),
                },
                uploaded.name,
                uploaded.getvalue(),
                principal,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("representacoes_file", None)
            _done("Documento anexado.")
    if st.button("Cancelar", key=prefix + "cancel"):
        st.session_state.pop("representacoes_file", None)
        st.rerun()


def _protocol_form(store, principal, identifier):
    section_label("Registrar protocolo")
    st.caption(
        "Informe os dados atribuídos pelo TRAMITA. Este projeto passa a ser tratado como Representação. "
        "Este sistema não realiza distribuição de Relator."
    )
    prefix = "rep_prot_" + str(identifier)
    number = st.text_input("Número do processo *", key=prefix + "num")
    day = st.date_input("Data do protocolo", date.today(), format="DD/MM/YYYY", key=prefix + "data")
    relator = st.selectbox(
        "Relator *",
        [None, *RELATORES],
        format_func=lambda name: (
            "Selecione o Relator atribuído no TRAMITA"
            if name is None
            else relator_label(name)
        ),
        key=prefix + "rel",
    )
    fase = st.selectbox(
        "Fase processual inicial",
        list(FASES),
        format_func=FASES.get,
        key=prefix + "fase",
    )
    cautelar = st.radio(
        "Possui pedido de medida cautelar?",
        (False, True),
        index=0,
        format_func=lambda value: "Sim" if value else "Não",
        horizontal=True,
        key=prefix + "cautelar",
    )
    observacoes = st.text_area("Observações", key=prefix + "obs")
    uploaded = st.file_uploader("PDF final da Representação", type=["pdf"], key=prefix + "pdf")
    if st.button("Confirmar protocolo", type="primary", key=prefix + "ok"):
        upload = None
        if uploaded is not None:
            upload = (uploaded.name, uploaded.getvalue())
        try:
            register_protocol(
                store,
                identifier,
                {
                    "numero_processo": number,
                    "data_protocolo": day.isoformat(),
                    "relator": relator,
                    "fase_processual": fase,
                    "observacoes": observacoes,
                    "possui_medida_cautelar": cautelar,
                },
                principal,
                upload,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("representacoes_protocol", None)
            _done("Representação protocolada.")
    if st.button("Cancelar", key=prefix + "cancel"):
        st.session_state.pop("representacoes_protocol", None)
        st.rerun()


def _detail(store, principal, record):
    detail_mark()
    procuradores_map, assessores_map = people_index(store)
    groups = grouped_members(record, procuradores_map, assessores_map)
    st.subheader(kind_label(record))
    render_record(
        record["titulo"],
        badges_html=badges(*_badge_items(record)),
        boxed=True,
    )
    definition_block(
        "Identificação",
        (
            ("Objeto", record.get("objeto")),
            ("Origem", label(ORIGENS, record["origem"])),
            ("Abertura", record.get("data_abertura")),
            ("Representado", record.get("representado")),
            ("Tema/área", record.get("tema")),
            ("Prioridade", label(PRIORIDADES, record["prioridade"])),
            ("Observações", record.get("observacoes")),
        ),
    )
    definition_block(
        "Equipe",
        (
            ("Procurador responsável", ", ".join(groups["PROCURADOR_RESPONSAVEL"]) or "—"),
            ("Signatários", ", ".join(groups["PROCURADOR_SIGNATARIO"]) or "—"),
            ("Assessores", ", ".join(groups["ASSESSOR"]) or "—"),
        ),
    )
    if record.get("numero_processo"):
        definition_block(
            "Processo",
            (
                ("Número", record.get("numero_processo")),
                ("Protocolo", record.get("data_protocolo")),
                ("Relator", relator_label(record.get("relator")) if record.get("relator") else None),
                ("Pedido de medida cautelar", "Sim" if record.get("possui_medida_cautelar") else "Não"),
                ("Situação", label(SITUACOES, record["situacao"])),
                ("Fase", label(FASES, record.get("fase_processual"))),
            ),
        )
    else:
        st.caption("Este projeto ainda não foi protocolado no TRAMITA.")
    section_label("Andamentos")
    timeline = progress(store, record["id"])
    if not timeline:
        empty_state("Nenhum andamento registrado.")
    for item in timeline:
        heading, descricao = andamento_display(item)
        line = f"**{item['data']}** · {heading}"
        if descricao:
            line += f"  \n{descricao}"
        st.markdown(line)
    section_label("Documentos")
    files = documents(store, record["id"])
    if not files:
        empty_state("Nenhum documento anexado.")
    pending = st.session_state.get("representacoes_download")
    for item in files:
        cols = st.columns([3, 2, 2, 2])
        cols[0].write(label(DOCUMENTOS, item["tipo_documento"]))
        cols[1].caption(item.get("descricao") or item["nome_arquivo"])
        cols[2].caption(item.get("data_documento") or "")
        if pending == item["id"]:
            file = download(store, item["id"])
            cols[3].download_button(
                "Baixar",
                file["conteudo"],
                file["nome"],
                file["tipo"],
                key="rep_dl_" + item["id"],
            )
        elif cols[3].button("Preparar download", key="rep_prep_" + item["id"]):
            st.session_state["representacoes_download"] = item["id"]
            st.rerun()
    actions_mark()
    a, b, c, d, e = st.columns(5)
    if not record.get("numero_processo") and a.button("Registrar protocolo", key="rep_dt_prot"):
        st.session_state["representacoes_protocol"] = record["id"]
        st.rerun()
    if b.button("Andamento", key="rep_dt_prg"):
        st.session_state["representacoes_progress"] = record["id"]
        st.rerun()
    if c.button("Anexar documento", key="rep_dt_doc"):
        st.session_state["representacoes_file"] = record["id"]
        st.rerun()
    if d.button("Editar", key="rep_dt_ed"):
        st.session_state["representacoes_edit"] = record["id"]
        st.rerun()
    if e.button("Voltar", key="rep_dt_back"):
        st.session_state.pop("representacoes_view", None)
        st.rerun()
    if record.get("numero_processo"):
        section_label("Fase processual")
        fase_keys = list(FASES)
        chosen = st.selectbox(
            "Atualizar fase",
            fase_keys,
            index=fase_keys.index(record["fase_processual"]) if record.get("fase_processual") in FASES else 0,
            format_func=FASES.get,
            key="rep_dt_fase",
        )
        if st.button("Salvar fase", key="rep_dt_fase_ok"):
            try:
                set_phase(store, record["id"], chosen, principal)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _done("Fase processual atualizada.")
        inst = st.selectbox(
            "Situação institucional",
            ["EM_TRAMITACAO", "JULGADA", "ENCERRADA", "SUSPENSA", "CANCELADA"],
            format_func=SITUACOES.get,
            key="rep_dt_sit",
        )
        if st.button("Atualizar situação", key="rep_dt_sit_ok"):
            try:
                set_status(store, record["id"], inst, principal)
            except ValueError as exc:
                st.error(str(exc))
            else:
                _done("Situação atualizada.")
    elif can_delete(record):
        if st.session_state.get("representacoes_confirm_delete") == record["id"]:
            st.warning("Excluir este projeto de Representação e os registros vinculados?")
            x, y = st.columns(2)
            if x.button("Confirmar exclusão", type="primary", key="rep_del_yes"):
                try:
                    delete(store, record["id"])
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    _clear_forms()
                    _done("Projeto de Representação excluído.")
            if y.button("Cancelar", key="rep_del_no"):
                st.session_state.pop("representacoes_confirm_delete", None)
                st.rerun()
        elif st.button("Excluir", key="rep_del"):
            st.session_state["representacoes_confirm_delete"] = record["id"]
            st.rerun()


def render(store, principal):
    require_permission(principal, "representacoes")
    st.title(module_title("representacoes", "REPRESENTAÇÕES"))
    st.caption(
        "Acompanhamento interno dos projetos de Representação e das Representações protocoladas no TRAMITA."
    )
    if message := st.session_state.pop("representacoes_message", None):
        st.success(message)
    people, servers = _people_options(store)
    st.session_state["_rep_people"] = (people, servers)
    counts = overview(store)
    _kpis(counts)
    if st.button("+ Novo projeto de Representação", type="primary", key="rep_new"):
        st.session_state["representacoes_edit"] = {}
        st.rerun()
    view_id = st.session_state.get("representacoes_view")
    edit_id = st.session_state.get("representacoes_edit")
    if st.session_state.get("representacoes_protocol"):
        _protocol_form(store, principal, st.session_state["representacoes_protocol"])
        return
    if st.session_state.get("representacoes_progress"):
        _progress_form(store, principal, st.session_state["representacoes_progress"])
        return
    if st.session_state.get("representacoes_file"):
        _document_form(store, principal, st.session_state["representacoes_file"])
        return
    if edit_id is not None:
        current = None if edit_id == {} else get(store, edit_id)
        if current and is_protocolled(current):
            st.subheader("Editar Representação")
        elif current:
            st.subheader("Editar projeto de Representação")
        else:
            st.subheader("Novo projeto de Representação")
        payload = _form(store, current)
        if payload and st.button("Salvar", type="primary", key="rep_save"):
            try:
                if current:
                    saved = update(store, current["id"], payload, principal)
                    if payload.get("situacao") and payload["situacao"] != current["situacao"]:
                        set_status(store, current["id"], payload["situacao"], principal)
                else:
                    saved = create(store, payload, principal)
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state["representacoes_edit"] = None
                st.session_state.pop("representacoes_edit", None)
                st.session_state["representacoes_view"] = saved["id"]
                _done(kind_saved_message(saved))
        if st.button("Cancelar", key="rep_cancel"):
            st.session_state.pop("representacoes_edit", None)
            st.rerun()
        return
    if view_id:
        record = get(store, view_id)
        if record is None:
            st.session_state.pop("representacoes_view", None)
            st.warning("Registro não encontrado.")
        else:
            _detail(store, principal, record)
            return
    filters = _filters()
    rows = list_records(store, filters)
    if not rows:
        empty_state("Nenhum projeto de Representação encontrado.")
        return
    procuradores_map, assessores_map = people_index(store)
    for index, record in enumerate(rows):
        _card(record, index, procuradores_map, assessores_map)
