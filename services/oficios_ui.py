"""Lazy native Streamlit correspondence UI."""

from datetime import date
import streamlit as st
from services.oficios import (
    open_service,
    SENT,
    RECEIVED,
    attention,
    suggest_vocative,
    GABINETES,
    fingerprint,
)
from services.ui_store import display_store


@st.cache_data(ttl=30, max_entries=128, show_spinner=False)
def cached_metadata(key, operation, arguments, _service):
    if operation == "list":
        return _service.list(**dict(arguments))
    return _service.overview(*arguments)


def read_list(service, **kwargs):
    if st.session_state.get("oficio_gabinete_member"):
        kwargs["member"] = st.session_state["oficio_gabinete_member"]
    if service.store.backend != "postgresql":
        return service.list(**kwargs)
    key = (
        service.store.read_cache_key(("oficios", "oficio_destinatarios")),
        date.today().isoformat(),
    )
    return cached_metadata(key, "list", tuple(sorted(kwargs.items())), service)


def read_overview(service, year):
    member = st.session_state.get("oficio_gabinete_member")
    if service.store.backend != "postgresql":
        return service.overview(year, member)
    key = (service.store.read_cache_key(("oficios",)), date.today().isoformat())
    return cached_metadata(key, "overview", (year, member), service)


def done(message):
    st.session_state["oficio_message"] = message
    st.rerun()


def label(r):
    return f"{r['serie'] or ''} {r['numero'] or r['numero_externo'] or 'Rascunho'}/{r['ano']} — {r['assunto'][:75]}"


def configuration(service, people):
    with st.expander("Configuração administrativa da numeração"):
        st.caption(
            "As referências são sugestões incompletas. Confirme o próximo número oficialmente disponível antes de finalizar."
        )
        series = service.series()
        sigla = st.session_state["oficio_gabinete"]
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
        current = next(s for s in series if s["sigla"] == sigla)
        with st.form("oficio_series"):
            st.caption(
                "Modelo do gabinete — configurações de documentos já emitidos são preservadas."
            )
            model = st.selectbox(
                "Pacote institucional",
                ["PROGE", "BTLC"],
                index=1 if current["modelo"] == "BTLC" else 0,
            )
            heading = st.text_input(
                "Cabeçalho com {numero} e {ano}", current["cabecalho"]
            )
            digits = st.number_input(
                "Quantidade mínima de dígitos", 1, 6, max(1, current["digitos"])
            )
            confirmed_model = st.checkbox(
                "Conferi o padrão institucional deste gabinete"
            )
            submit_model = st.form_submit_button("Salvar modelo do gabinete")
        if submit_model:
            service.configure_series(
                current["membro_id"], sigla, model, heading, digits, confirmed_model
            )
            st.session_state.pop("oficio_preview", None)
            done("Modelo do gabinete configurado.")


def reset_editor():
    for key in list(st.session_state):
        if key.startswith("oficio_input_") or key in (
            "oficio_edit",
            "oficio_preview",
            "oficio_final",
            "oficio_detail",
            "oficio_day",
            "oficio_member",
        ):
            st.session_state.pop(key, None)


def switch_gabinete(code=None, member=None):
    reset_editor()
    st.session_state["oficio_gabinete"] = code
    st.session_state["oficio_gabinete_member"] = member
    st.session_state["oficio_page"] = "Visão Geral"


def editor(service, people):
    identifier = st.session_state.get("oficio_edit")
    member = st.session_state["oficio_gabinete_member"]
    series = next(s for s in service.series() if s["membro_id"] == member)
    final = st.session_state.get("oficio_final")
    if final:
        st.success("Ofício finalizado com sucesso.")
        st.write(
            f"Ofício {series['sigla']} nº {str(final['numero']).zfill(series['digitos'])}/{final['ano']}"
        )
        for f in service.files(final["id"]):
            st.download_button(
                "Baixar "
                + ("PDF" if f["tipo"] == "application/pdf" else "DOCX")
                + " oficial",
                service.download(f["id"]),
                f["nome"],
                mime=f["tipo"],
            )
        st.button(
            "Ver em Enviados",
            on_click=lambda: st.session_state.update(oficio_page="Enviados"),
        )
        st.button("Iniciar outro rascunho", on_click=reset_editor)
        return
    seed = service.get(identifier) if identifier else {}
    if seed and (seed["membro_id"] != member or seed["status"] != "Rascunho"):
        raise ValueError(
            "Rascunho não pertence ao gabinete atual ou já foi finalizado."
        )
    st.write(f"NOVO OFÍCIO — {series['sigla']}")
    st.caption(people[member]["nome"])
    day = st.date_input(
        "Data do ofício",
        date.fromisoformat(seed["data"]) if seed else date.today(),
        format="DD/MM/YYYY",
        key="oficio_day",
    )
    seq = service.sequence(series["sigla"], day.year)
    st.caption(
        f"Próximo número previsto: {str(seq['proximo']).zfill(series['digitos'])}/{day.year}"
        + ("" if seq["confirmada"] else " · confirmação administrativa pendente")
    )
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
            options.index(seed["tratamento"])
            if seed.get("tratamento") in options
            else (4 if seed.get("tratamento") else 0)
        ),
        key="oficio_input_treatment",
    )
    if treatment == "Outro":
        treatment = st.text_input(
            "Tratamento livre", seed.get("tratamento", ""), key="oficio_input_custom"
        )
    record = {
        "direcao": "ENVIADO",
        "membro_id": member,
        "data": day.isoformat(),
        "tratamento": treatment,
    }
    for field, title in [
        ("destinatario", "Nome do destinatário"),
        ("cargo", "Cargo"),
        ("unidade", "Órgão/Unidade"),
        ("instituicao", "Instituição"),
        ("assunto", "Assunto"),
        ("referencia", "Referência (opcional)"),
    ]:
        record[field] = st.text_input(
            title, seed.get(field, ""), key="oficio_input_" + field
        )
    record["vocativo"] = st.text_input(
        "Vocativo (editável)",
        seed.get("vocativo", suggest_vocative(treatment, record["cargo"])),
        key="oficio_input_vocativo",
    )
    record["corpo"] = st.text_area(
        "Corpo do ofício — cada linha forma um parágrafo",
        seed.get("corpo", ""),
        height=250,
        key="oficio_input_corpo",
    )
    with st.expander("Informações complementares"):
        for field, title in [
            ("processo", "Processo relacionado"),
            ("procedimento", "Procedimento/inquérito"),
            ("fechamento", "Fechamento"),
            ("titulo_assinatura", "Título institucional da assinatura (opcional)"),
        ]:
            record[field] = st.text_input(
                title,
                seed.get(field, "Atenciosamente," if field == "fechamento" else ""),
                key="oficio_input_" + field,
            )
        related_search = st.text_input(
            "Pesquisar recebido para vincular", key="oficio_input_search"
        )
        received = read_list(
            service, direction="RECEBIDO", search=related_search, limit=200
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
            key="oficio_input_related",
        )
        record["observacoes"] = st.text_area(
            "Observações", seed.get("observacoes", ""), key="oficio_input_observacoes"
        )
    document = {
        **record,
        "signatario": people[member]["nome"],
        "cargo_base": people[member]["cargo_base"],
    }
    current = fingerprint(document, series, [series["sigla"], identifier])
    st.write("CONFERÊNCIA E FINALIZAÇÃO")
    if st.button("Salvar rascunho"):
        saved = service.save(record, identifier)
        st.session_state["oficio_edit"] = saved
        # Saving the same form must not lose an already checked preview.
        preview = st.session_state.get("oficio_preview")
        if preview and preview["fingerprint"] == current:
            preview["fingerprint"] = fingerprint(
                document, series, [series["sigla"], saved]
            )
        done("Rascunho salvo sem consumir número.")
    a, b = st.columns(2)
    docx_requested = a.button("Pré-visualizar DOCX")
    pdf_requested = b.button("Pré-visualizar PDF")
    if docx_requested or pdf_requested:
        from document_generator.oficios import generate
        from document_generator.pdf import convert
        from services.oficios import validate

        validate(record, official=True)
        preview = st.session_state.get("oficio_preview")
        if not preview or preview["fingerprint"] != current:
            preview = {"fingerprint": current, "docx": generate(document, series)}
        if pdf_requested:
            preview["pdf"], _ = convert(preview["docx"])
        st.session_state["oficio_preview"] = preview
    preview = st.session_state.get("oficio_preview")
    valid = bool(preview and preview["fingerprint"] == current)
    if preview and not valid:
        st.warning(
            "Os dados foram alterados após a última prévia. Gere uma nova prévia antes de finalizar."
        )
    if valid:
        st.success("✓ Prévia gerada. Confira o documento.")
        st.download_button("Baixar prévia DOCX", preview["docx"], "RASCUNHO.docx")
        if preview.get("pdf"):
            st.download_button(
                "Abrir/Baixar prévia PDF",
                preview["pdf"],
                "RASCUNHO.pdf",
                mime="application/pdf",
            )
    if st.button("Finalizar e gerar ofício", type="primary", disabled=not valid):
        from services.oficios import validate

        validate(record, official=True)
        if not valid:
            raise ValueError("Gere uma nova prévia antes de finalizar.")
        identifier = service.save(record, identifier)
        st.session_state["oficio_edit"] = identifier
        # Update the context after assigning a draft ID; failures remain retryable.
        preview["fingerprint"] = fingerprint(
            document, series, [series["sigla"], identifier]
        )
        final = service.finalize_reviewed(
            identifier, document, series, preview["fingerprint"]
        )
        st.session_state["oficio_final"] = final
        st.rerun()
    if identifier:
        st.button("Iniciar outro rascunho", on_click=reset_editor)


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
            r["membros"] = [st.session_state["oficio_gabinete_member"]]
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
    reset_editor()
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
        if r["status"] == "Gerado":
            with st.expander("Excluir definitivamente"):
                reason = st.text_area("Motivo obrigatório", key="reason_" + r["id"])
                acknowledged = st.checkbox(
                    "Estou ciente de que este Ofício será excluído e sua numeração poderá ser reutilizada.",
                    key="ack_" + r["id"],
                )
                typed = st.text_input("Digite EXCLUIR", key="typed_" + r["id"])
                confirmed = st.checkbox(
                    "Confirmação final da exclusão definitiva",
                    key="final_delete_" + r["id"],
                )
                if st.button("Excluir definitivamente", key="purge_" + r["id"]):
                    service.delete_generated(
                        r["id"], reason, acknowledged, typed, confirmed
                    )
                    done(
                        "Ofício preservado em quarentena e número liberado para reutilização."
                    )
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
            member = st.session_state["oficio_gabinete_member"]
            series = None
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

    try:
        service = open_service(display_store(Store()))
        people = {
            p["id"]: p for p in service.store.catalog("procuradores") if p["ativo"]
        }
        if not people:
            st.warning("Cadastre um membro ativo na base de procuradores.")
            return
        offices = {s["sigla"]: s for s in service.series() if s["membro_id"] in people}
        selected = st.session_state.get("oficio_gabinete")
        if selected not in offices:
            st.subheader("Ofícios — Geração e Controle")
            st.write("Selecione o gabinete")
            for code in GABINETES:
                if code in offices:
                    office = offices[code]
                    st.button(
                        f"{code} — {office['nome']}",
                        key="gabinete_" + code,
                        on_click=switch_gabinete,
                        args=(code, office["membro_id"]),
                        use_container_width=True,
                    )
            return
        office = offices[selected]
        st.session_state["oficio_gabinete_member"] = office["membro_id"]
        st.subheader(f"OFÍCIOS — GABINETE {selected}")
        st.caption(office["nome"])
        st.button("← Trocar gabinete", on_click=switch_gabinete)
        page = st.radio(
            "Ofícios",
            ["Visão Geral", "Novo Ofício", "Enviados", "Recebidos", "Acompanhamento"],
            horizontal=True,
            key="oficio_page",
        )
        if st.session_state.get("oficio_message"):
            st.success(st.session_state.pop("oficio_message"))
        if page == "Visão Geral":
            st.button(
                "Novo Ofício",
                on_click=lambda: st.session_state.update(oficio_page="Novo Ofício"),
                type="primary",
            )
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
            st.write("Últimas movimentações")
            for row in read_list(service, limit=5):
                st.write(label(row))
                st.caption(row["status"] + " · " + row["atualizada"][:10])
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
