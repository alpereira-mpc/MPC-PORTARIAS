"""Lazy native Streamlit correspondence UI."""

from datetime import date
import streamlit as st
from services.oficios import open_service, SENT, RECEIVED, attention, suggest_vocative
from services.ui_store import display_store


@st.cache_data(ttl=30, max_entries=128, show_spinner=False)
def cached_metadata(key, operation, arguments, _service):
    if operation == "list":
        return _service.list(**dict(arguments))
    return _service.overview(arguments)


def read_list(service, **kwargs):
    if service.store.backend != "postgresql":
        return service.list(**kwargs)
    key = (
        service.store.read_cache_key(("oficios", "oficio_destinatarios")),
        date.today().isoformat(),
    )
    return cached_metadata(key, "list", tuple(sorted(kwargs.items())), service)


def read_overview(service, year):
    if service.store.backend != "postgresql":
        return service.overview(year)
    key = (service.store.read_cache_key(("oficios",)), date.today().isoformat())
    return cached_metadata(key, "overview", year, service)


def done(message):
    st.session_state["oficio_message"] = message
    st.rerun()


def label(r):
    return f"{r['serie'] or ''} {r['numero'] or r['numero_externo'] or 'Rascunho'}/{r['ano']} — {r['assunto'][:75]}"


def configuration(service, people):
    with st.expander("Séries e configuração inicial das sequências"):
        st.caption(
            "As referências são sugestões incompletas. Confirme o próximo número oficialmente disponível antes de finalizar."
        )
        series = service.series()
        sigla = st.selectbox("Série", [s["sigla"] for s in series])
        year = st.number_input("Ano da sequência", 1000, 9999, date.today().year)
        seq = service.sequence(sigla, year) if sigla else {"proximo": 1}
        with st.form("oficio_sequence"):
            number = st.number_input(
                "Próximo número a confirmar", 1, value=seq["proximo"]
            )
            confirmed = st.checkbox(
                "Conferi os ofícios já expedidos e confirmo o próximo número"
            )
            submit = st.form_submit_button("Confirmar sequência")
        if sigla:
            seq = service.sequence(sigla, year)
            st.info(
                f"{sigla}/{year}: próximo {seq['proximo']} · "
                + (
                    "confirmado"
                    if seq["confirmada"]
                    else "sugestão ainda não confirmada"
                )
            )
        if submit:
            service.confirm_sequence(sigla, year, number, confirmed)
            done("Sequência confirmada.")
        with st.form("oficio_series"):
            member = st.selectbox(
                "Membro da série", list(people), format_func=lambda x: people[x]["nome"]
            )
            code = st.text_input("Sigla comprovada")
            model = st.selectbox("Pacote institucional", ["PROGE", "BTLC"])
            heading = st.text_input("Cabeçalho com {numero} e {ano}")
            digits = st.number_input("Quantidade mínima de dígitos", 1, 6, 3)
            confirmed = st.checkbox(
                "Conferi a sigla e o padrão institucional deste membro"
            )
            submit = st.form_submit_button("Salvar configuração da série")
        if submit:
            service.configure_series(
                member, code.strip().upper(), model, heading, digits, confirmed
            )
            done("Série configurada.")


def editor(service, people):
    identifier = st.session_state.get("oficio_edit")
    seed = service.get(identifier) if identifier else {}
    if identifier:
        st.info("Editando rascunho salvo.")
    member = st.selectbox(
        "Remetente / signatário",
        list(people),
        index=(
            list(people).index(seed["membro_id"])
            if seed and seed["membro_id"] in people
            else 0
        ),
        format_func=lambda x: people[x]["nome"],
        key="oficio_member",
    )
    day = st.date_input(
        "Data do ofício",
        date.fromisoformat(seed["data"]) if seed else date.today(),
        format="DD/MM/YYYY",
        key="oficio_day",
    )
    series = next((s for s in service.series() if s["membro_id"] == member), None)
    if series:
        seq = service.sequence(series["sigla"], day.year)
        st.caption(
            f"Série {series['sigla']} · próximo previsto: {seq['proximo']} / {day.year} · "
            + (
                "sequência confirmada"
                if seq["confirmada"]
                else "confirmação inicial pendente"
            )
        )
    else:
        st.warning(
            "Este membro ainda não possui série configurada. É possível salvar rascunho."
        )
    with st.form("oficio_editor"):
        options = [
            "A Sua Excelência o Senhor",
            "A Sua Excelência a Senhora",
            "Ao Senhor",
            "À Senhora",
            "Outro",
        ]
        treatment = st.selectbox(
            "Forma de tratamento",
            options,
            index=(
                options.index(seed.get("tratamento"))
                if seed.get("tratamento") in options
                else 0
            ),
        )
        custom = st.text_input(
            "Tratamento livre (se Outro)",
            seed.get("tratamento", "") if seed.get("tratamento") not in options else "",
        )
        record = {"direcao": "ENVIADO", "membro_id": member, "data": day.isoformat()}
        for field, title in [
            ("destinatario", "Nome do destinatário"),
            ("cargo", "Cargo"),
            ("unidade", "Órgão/Unidade"),
            ("instituicao", "Instituição"),
            ("assunto", "Assunto"),
            ("processo", "Processo relacionado"),
            ("procedimento", "Procedimento/inquérito"),
            ("referencia", "Ofício de referência"),
        ]:
            record[field] = st.text_input(title, seed.get(field, ""))
        record["vocativo"] = st.text_input(
            "Vocativo (editável)",
            seed.get("vocativo", suggest_vocative(treatment, seed.get("cargo", ""))),
        )
        record["corpo"] = st.text_area(
            "Corpo do ofício — separe parágrafos com linha em branco",
            seed.get("corpo", ""),
            height=250,
        )
        record["fechamento"] = st.text_input(
            "Fechamento", seed.get("fechamento", "Atenciosamente,")
        )
        record["titulo_assinatura"] = st.text_input(
            "Título institucional da assinatura (opcional)",
            seed.get("titulo_assinatura", ""),
        )
        related_search = st.text_input(
            "Pesquisar recebido para vincular (salve o rascunho para atualizar a pesquisa)"
        )
        received = read_list(
            service,
            direction="RECEBIDO",
            search=st.session_state.get("oficio_related_search", ""),
            limit=200,
        )
        choices = {r["id"]: label(r) for r in received}
        if seed.get("responde_a") and seed["responde_a"] not in choices:
            choices[seed["responde_a"]] = label(service.get(seed["responde_a"]))
        record["responde_a"] = st.selectbox(
            "Este ofício responde a um recebido?",
            [None, *choices],
            index=(
                [None, *choices].index(seed.get("responde_a"))
                if seed.get("responde_a") in choices
                else 0
            ),
            format_func=lambda x: choices[x] if x else "Não",
        )
        record["observacoes"] = st.text_area("Observações", seed.get("observacoes", ""))
        save = st.form_submit_button("Salvar rascunho")
        preview = st.form_submit_button("Pré-visualizar DOCX")
    record["tratamento"] = custom if treatment == "Outro" else treatment
    if save:
        st.session_state["oficio_edit"] = service.save(record, identifier)
        st.session_state["oficio_related_search"] = related_search
        done("Rascunho salvo sem consumir número.")
    if preview:
        from document_generator.oficios import generate
        from services.oficios import validate

        validate(record)
        if not series or not series["modelo"]:
            raise ValueError("Configure um modelo para a prévia.")
        content = generate(
            {
                **record,
                "signatario": people[member]["nome"],
                "cargo_base": people[member]["cargo_base"],
            },
            series,
        )
        st.download_button("Baixar prévia sem número oficial", content, "RASCUNHO.docx")
    if identifier:
        if st.button("Finalizar e gerar ofício", type="primary"):
            service.finalize(identifier)
            st.session_state.pop("oficio_edit", None)
            done("Ofício finalizado. DOCX e PDF disponíveis em Enviados.")
        st.caption(
            "A finalização usa o rascunho salvo. Salve alterações antes de finalizar."
        )
        if st.button("Iniciar outro rascunho"):
            st.session_state.pop("oficio_edit", None)
            done("Novo rascunho.")


def received_form(service, people):
    with st.expander("Registrar ofício recebido"):
        with st.form("oficio_received", clear_on_submit=True):
            r = {"direcao": "RECEBIDO"}
            for field, title in [
                ("numero_externo", "Número do ofício recebido"),
                ("remetente", "Remetente"),
                ("cargo_remetente", "Cargo do remetente"),
                ("instituicao", "Órgão/Instituição"),
                ("assunto", "Assunto"),
                ("processo", "Processo/referência"),
            ]:
                r[field] = st.text_input(title)
            r["data"] = st.date_input(
                "Data do documento", format="DD/MM/YYYY"
            ).isoformat()
            r["data_recebimento"] = st.date_input(
                "Data de recebimento", format="DD/MM/YYYY"
            ).isoformat()
            r["membros"] = st.multiselect(
                "Destinatários internos",
                list(people),
                format_func=lambda x: people[x]["nome"],
            )
            due = st.date_input("Prazo opcional", value=None, format="DD/MM/YYYY")
            r["prazo"] = due.isoformat() if due else None
            r["status"] = st.selectbox("Status inicial", RECEIVED)
            r["observacoes"] = st.text_area("Observações do recebido")
            uploads = st.file_uploader(
                "Originais PDF/DOCX — até 10 MB por arquivo",
                type=["pdf", "docx"],
                accept_multiple_files=True,
            )
            submit = st.form_submit_button("Registrar recebido")
        if submit:
            service.save(r, uploads=[(f.name, f.getvalue()) for f in uploads])
            done("Ofício recebido registrado.")


def edit_draft(identifier):
    st.session_state["oficio_edit"] = identifier
    st.session_state["oficio_page"] = "Novo Ofício"
    for key in ("oficio_member", "oficio_day"):
        st.session_state.pop(key, None)


def details(service, r):
    st.write(label(r))
    st.write("Status: " + r["status"])
    for key, title in [
        ("signatario", "Signatário"),
        ("remetente", "Remetente"),
        ("destinatario", "Destinatário"),
        ("instituicao", "Instituição"),
        ("processo", "Processo"),
        ("procedimento", "Procedimento"),
        ("referencia", "Referência"),
        ("observacoes", "Observações"),
    ]:
        if r.get(key):
            st.write(f"{title}: {r[key]}")
    st.write("Data: " + date.fromisoformat(r["data"]).strftime("%d/%m/%Y"))
    for field, title in (
        ("data_envio", "Envio"),
        ("data_recebimento", "Recebimento"),
        ("prazo", "Prazo"),
        ("cancelada", "Cancelamento"),
    ):
        if r.get(field):
            st.write(
                title + ": " + date.fromisoformat(r[field][:10]).strftime("%d/%m/%Y")
            )
    if r.get("membros"):
        people = {p["id"]: p["nome"] for p in service.store.catalog("procuradores")}
        st.write(
            "Destinatários internos: "
            + ", ".join(people.get(i, "Membro indisponível") for i in r["membros"])
        )
    if r.get("corpo"):
        st.text(r["corpo"])
    if r.get("responde_a"):
        st.info("Em resposta ao Ofício " + label(service.get(r["responde_a"])))
    if r["direcao"] == "RECEBIDO":
        for reply in read_list(service, related=r["id"]):
            st.info(
                (
                    "Respondido pelo Ofício "
                    if reply["numero"] and reply["status"] != "Cancelado"
                    else "Resposta vinculada: "
                )
                + label(reply)
            )
    for f in service.files(r["id"]):
        st.caption(f"{f['nome']} · {f['tamanho']:,} bytes · {f['incluida'][:10]}")
        if st.button("Preparar download: " + f["nome"], key="oficio_file_" + f["id"]):
            st.download_button(
                "Baixar arquivo",
                service.download(f["id"]),
                f["nome"],
                mime=f["tipo"],
                key="oficio_download_" + f["id"],
            )
    if r["status"] == "Rascunho":
        st.button(
            "Editar rascunho",
            key="edit_" + r["id"],
            on_click=edit_draft,
            args=(r["id"],),
        )
        confirmed = st.checkbox(
            "Confirmo a exclusão deste rascunho", key="confirm_" + r["id"]
        )
        if st.button("Excluir rascunho", key="delete_" + r["id"]):
            service.delete_draft(r["id"], confirmed)
            done("Rascunho excluído.")
    elif r["status"] != "Cancelado":
        with st.form("status_" + r["id"]):
            choices = [
                s
                for s in (SENT if r["direcao"] == "ENVIADO" else RECEIVED)
                if s not in ("Rascunho", "Gerado")
            ]
            status = st.selectbox(
                "Novo status",
                choices,
                index=choices.index(r["status"]) if r["status"] in choices else 0,
            )
            due = st.date_input(
                "Prazo",
                value=date.fromisoformat(r["prazo"]) if r.get("prazo") else None,
                format="DD/MM/YYYY",
            )
            sent = st.date_input(
                "Data de envio (para status Enviado)", value=None, format="DD/MM/YYYY"
            )
            note = st.text_area("Observação / motivo do cancelamento")
            submit = st.form_submit_button("Registrar movimentação")
        if submit:
            service.update_status(
                r["id"],
                status,
                note,
                due.isoformat() if due else None,
                sent.isoformat() if sent else None,
            )
            done("Movimentação registrada.")
    st.write("Histórico")
    st.dataframe(service.movements(r["id"]), hide_index=True, use_container_width=True)


def listing(service, people, direction=None, tracking=False):
    with st.expander("Filtros", expanded=True):
        a, b, c = st.columns(3)
        with a:
            search = st.text_input("Pesquisa textual")
            member = st.selectbox(
                "Procurador",
                [None, *people],
                format_func=lambda x: people[x]["nome"] if x else "Todos",
            )
            series = st.selectbox(
                "Série do filtro",
                [None, *[s["sigla"] for s in service.series()]],
                format_func=lambda x: x or "Todas",
            )
        with b:
            status = st.selectbox(
                "Status",
                [None, *dict.fromkeys(SENT + RECEIVED)],
                format_func=lambda x: x or "Todos",
            )
            year = st.number_input("Ano (0 = todos)", 0, 9999, 0)
            number = st.number_input("Número oficial (0 = todos)", 0, value=0)
            recipient = st.text_input("Destinatário")
        with c:
            start = st.date_input("Período inicial", value=None, format="DD/MM/YYYY")
            end = st.date_input("Período final", value=None, format="DD/MM/YYYY")
            deadline = st.selectbox(
                "Prazo do filtro",
                [None, "Vencido", "Próximos 7 dias", "Com prazo"],
                format_func=lambda x: x or "Todos",
            )
            subject = st.text_input("Assunto do filtro")
        if tracking:
            direction = st.selectbox(
                "Direção",
                [None, "ENVIADO", "RECEBIDO"],
                format_func=lambda x: x or "Todas",
            )
    page = st.number_input("Página", 1, value=1)
    rows = read_list(
        service,
        direction=direction,
        series=series,
        member=member,
        number=number or None,
        year=year or None,
        recipient=recipient,
        subject=subject,
        status=status,
        start=start.isoformat() if start else None,
        end=end.isoformat() if end else None,
        search=search,
        deadline=deadline,
        attention_only=tracking,
        offset=(page - 1) * 50,
    )
    if not rows:
        st.info("Nenhum ofício nesta página para os filtros selecionados.")
        return
    for r in rows:
        with st.container(border=True):
            st.write(label(r))
            st.caption(
                r["status"]
                + " · "
                + date.fromisoformat(r["data"]).strftime("%d/%m/%Y")
                + " · "
                + attention(r)
            )
            if st.button("Abrir detalhes", key="open_oficio_" + r["id"]):
                st.session_state["oficio_detail"] = r["id"]
    if st.session_state.get("oficio_detail") in {r["id"] for r in rows}:
        details(service, service.get(st.session_state["oficio_detail"]))


def render():
    from database.store import Store

    st.subheader("Ofícios — Geração e Controle")
    try:
        service = open_service(display_store(Store()))
        people = {
            p["id"]: p for p in service.store.catalog("procuradores") if p["ativo"]
        }
        if not people:
            st.warning("Cadastre um membro ativo na base de procuradores.")
            return
        page = st.radio(
            "Ofícios",
            ["Visão Geral", "Novo Ofício", "Enviados", "Recebidos", "Acompanhamento"],
            horizontal=True,
            key="oficio_page",
        )
        if st.session_state.get("oficio_message"):
            st.success(st.session_state.pop("oficio_message"))
        if page == "Visão Geral":
            values = read_overview(service, date.today().year)
            labels = [
                "Enviados no ano",
                "Recebidos no ano",
                "Aguardando resposta",
                "Aguardando providência",
                "Prazos vencidos",
                "Prazos próximos (7 dias)",
            ]
            for col, title, value in zip(st.columns(3) * 2, labels, values.values()):
                with col:
                    st.metric(title, value)
            configuration(service, people)
        elif page == "Novo Ofício":
            editor(service, people)
        elif page == "Recebidos":
            received_form(service, people)
            listing(service, people, "RECEBIDO")
        elif page == "Enviados":
            listing(service, people, "ENVIADO")
        else:
            listing(service, people, tracking=True)
    except (ValueError, RuntimeError) as exc:
        st.error(str(exc))
