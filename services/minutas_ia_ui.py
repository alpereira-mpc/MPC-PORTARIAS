"""Private UI for the Minutas IA reference library."""

from datetime import date, datetime

import streamlit as st

from database.minutas_referencias import MinutasReferenciasStore
from services.minutas_library import (
    COMPLEMENT_TYPES,
    MinutasAccessDenied,
    MinutasDuplicateError,
    MinutasLibraryService,
    MinutasValidationError,
    OTHER_COMPLEMENT_TYPE,
    PROCURADORA,
    SUBTIPOS,
)
from services.minutas_storage import (
    StorageConfigurationError,
    StorageOperationError,
    SupabaseMinutasStorage,
)
from services.ui_theme import badge, empty_state, render_html, section_label


def _service(store, identity):
    try:
        storage = SupabaseMinutasStorage.from_secrets(st.secrets)
        repository = MinutasReferenciasStore(store)
    except (StorageConfigurationError, RuntimeError):
        st.warning(
            "A Biblioteca de Referências ainda não está configurada neste ambiente."
        )
        return None
    return MinutasLibraryService(repository, storage, identity, st.secrets)


def _error(exc):
    if isinstance(
        exc,
        (
            MinutasAccessDenied,
            MinutasDuplicateError,
            MinutasValidationError,
            StorageOperationError,
        ),
    ):
        st.error(str(exc))
    else:
        st.error("Não foi possível concluir a operação. Tente novamente.")


def _upload_tuple(upload):
    return (upload.name, upload.getvalue()) if upload is not None else None


def _create_form(service):
    section_label("Nova referência")
    with st.form("minutas_reference_create", clear_on_submit=True, border=True):
        first, second = st.columns(2)
        with first:
            st.text_input("Procuradora", value=PROCURADORA, disabled=True)
            process = st.text_input("Número do processo *")
        with second:
            year = st.number_input(
                "Exercício *",
                min_value=1900,
                max_value=datetime.now().year + 1,
                value=datetime.now().year - 1,
                step=1,
            )
            subtype = st.selectbox("Subtipo de PCA *", SUBTIPOS)
        audit = st.file_uploader(
            "Relatório da Auditoria — PDF, até 10 MB *",
            type=["pdf"],
            key="minutas_create_audit",
            max_upload_size=10,
        )
        opinion = st.file_uploader(
            "Parecer definitivo — PDF ou DOCX, até 10 MB *",
            type=["pdf", "docx"],
            key="minutas_create_opinion",
            max_upload_size=10,
        )
        submitted = st.form_submit_button(
            "Cadastrar referência", type="primary", icon=":material/add:"
        )
    if not submitted:
        return
    try:
        service.create(
            {
                "procuradora": PROCURADORA,
                "numero_processo": process,
                "exercicio": year,
                "subtipo": subtype,
            },
            _upload_tuple(audit),
            _upload_tuple(opinion),
        )
    except Exception as exc:
        _error(exc)
    else:
        st.success("Referência cadastrada com sucesso.")


def _prepare_download(service, record, kind):
    try:
        document = service.download(record["id"], kind)
    except Exception as exc:
        _error(exc)
        return False
    st.session_state[f"minutas_download_{record['id']}_{kind}"] = {
        "name": document.name,
        "mime": document.mime,
        "content": document.content,
    }
    return True


def _prepare_complement_download(service, record, document):
    try:
        download = service.download_document(record["id"], document["id"])
    except Exception as exc:
        _error(exc)
        return False
    st.session_state[f"minutas_download_complement_{document['id']}"] = {
        "name": download.name,
        "mime": download.mime,
        "content": download.content,
    }
    return True


def _document_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)) if value else None


def _keep_reference_open(identifier):
    st.session_state["minutas_keep_open"] = identifier


def _panel_name(action, identifier):
    return f"{action}:{identifier}"


def _show_panel(action, identifier, reference_id):
    st.session_state["minutas_active_panel"] = _panel_name(action, identifier)
    _keep_reference_open(reference_id)


def _hide_panel(reference_id):
    st.session_state.pop("minutas_active_panel", None)
    _keep_reference_open(reference_id)


def _panel_is(action, identifier):
    return st.session_state.get("minutas_active_panel") == _panel_name(
        action, identifier
    )


def _finish_action(reference_id, level, message):
    _hide_panel(reference_id)
    st.session_state["minutas_message"] = (level, message)
    st.rerun()


def _metadata_values(document, **changes):
    values = {
        "id": document["id"],
        "tipo": document["tipo"],
        "descricao": document.get("descricao") or "",
        "posicao": int(document["posicao"]),
        "data_documento": document.get("data_documento"),
    }
    values.update(changes)
    return values


def _replace_document(service, record, kind, upload):
    try:
        updated = service.replace(record["id"], kind, _upload_tuple(upload))
    except Exception as exc:
        _error(exc)
        return
    st.session_state.pop(f"minutas_download_{record['id']}_{kind}", None)
    if updated.get("cleanup_pending"):
        _finish_action(
            record["id"],
            "warning",
            "Arquivo substituído, mas a limpeza da versão anterior ficou pendente.",
        )
    _finish_action(record["id"], "success", "Arquivo substituído com sucesso.")


def _main_document_actions(service, record, kind, accepted):
    download_key = f"minutas_download_{record['id']}_{kind}"
    prepared = st.session_state.get(download_key)
    if prepared:
        st.download_button(
            "Baixar arquivo",
            prepared["content"],
            prepared["name"],
            prepared["mime"],
            key=f"minutas_download_button_{record['id']}_{kind}",
            icon=":material/download:",
            width="stretch",
        )
    elif st.button(
        "Preparar download",
        key=f"minutas_prepare_{record['id']}_{kind}",
        icon=":material/download:",
        width="stretch",
    ):
        if _prepare_download(service, record, kind):
            _keep_reference_open(record["id"])
            st.rerun()
    replacement = st.file_uploader(
        "Substituir arquivo",
        type=accepted,
        key=f"minutas_replace_file_{record['id']}_{kind}",
        max_upload_size=10,
    )
    if st.button(
        "Confirmar substituição",
        key=f"minutas_replace_{record['id']}_{kind}",
        disabled=replacement is None,
        width="stretch",
    ):
        _replace_document(service, record, kind, replacement)


def _main_document_row(service, record, kind, position_label, type_label):
    with st.container(border=True):
        order, details, actions = st.columns(
            [1.1, 4.8, 1.35], vertical_alignment="center"
        )
        order.caption(position_label)
        details.markdown(f"**{record[f'{kind}_nome']}**")
        details.caption(
            f"{type_label} · {record[f'{kind}_tamanho']:,} bytes"
        )
        with actions.popover("Ações", icon=":material/more_vert:"):
            accepted = ["pdf"] if kind == "auditoria" else ["pdf", "docx"]
            _main_document_actions(service, record, kind, accepted)


def _add_document_panel(service, record, documents):
    with st.container(border=True):
        st.markdown("**Adicionar documento intermediário**")
        document_type = st.selectbox(
            "Tipo do documento",
            COMPLEMENT_TYPES,
            key=f"minutas_add_type_{record['id']}",
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        st.caption(f"Classificação selecionada: **{document_type}**")
        description = ""
        if document_type == OTHER_COMPLEMENT_TYPE:
            description = st.text_input(
                "Descrição *",
                key=f"minutas_add_description_{record['id']}",
                on_change=_keep_reference_open,
                args=(record["id"],),
            )
        upload = st.file_uploader(
            "Documento — PDF ou DOCX, até 10 MB",
            type=["pdf", "docx"],
            key=f"minutas_add_file_{record['id']}",
            max_upload_size=10,
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        known_date = st.checkbox(
            "Data conhecida",
            key=f"minutas_add_known_date_{record['id']}",
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        document_date = None
        if known_date:
            document_date = st.date_input(
                "Data do documento",
                value=None,
                key=f"minutas_add_date_{record['id']}",
                on_change=_keep_reference_open,
                args=(record["id"],),
            )
        save, cancel = st.columns(2)
        if save.button(
            "Salvar documento",
            key=f"minutas_add_save_{record['id']}",
            type="primary",
            disabled=upload is None,
            width="stretch",
        ):
            try:
                service.add_document(
                    record["id"],
                    {
                        "tipo": document_type,
                        "descricao": description,
                        "posicao": max(
                            (int(item["posicao"]) for item in documents), default=0
                        )
                        + 1,
                        "data_documento": document_date,
                    },
                    _upload_tuple(upload),
                )
            except Exception as exc:
                _error(exc)
            else:
                _finish_action(
                    record["id"],
                    "success",
                    "Documento complementar adicionado com sucesso.",
                )
        if cancel.button(
            "Cancelar",
            key=f"minutas_add_cancel_{record['id']}",
            width="stretch",
        ):
            _hide_panel(record["id"])
            st.rerun()


def _reordered_values(documents, index, offset):
    other_index = index + offset
    if not 0 <= other_index < len(documents):
        return None
    values = [_metadata_values(document) for document in documents]
    values[index]["posicao"], values[other_index]["posicao"] = (
        values[other_index]["posicao"],
        values[index]["posicao"],
    )
    return values


def _move_document(service, record, documents, index, offset):
    values = _reordered_values(documents, index, offset)
    if values is None:
        return
    try:
        service.update_documents(record["id"], values)
    except Exception as exc:
        _error(exc)
    else:
        _finish_action(record["id"], "success", "Ordem processual atualizada.")


def _edit_document_panel(service, record, documents, document):
    current_date = _document_date(document.get("data_documento"))
    with st.container(border=True):
        st.markdown(f"**Editar classificação — {document['nome']}**")
        document_type = st.selectbox(
            "Tipo do documento",
            COMPLEMENT_TYPES,
            index=COMPLEMENT_TYPES.index(document["tipo"]),
            key=f"minutas_edit_type_{document['id']}",
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        st.caption(f"Classificação selecionada: **{document_type}**")
        description = ""
        if document_type == OTHER_COMPLEMENT_TYPE:
            description = st.text_input(
                "Descrição *",
                value=document.get("descricao") or "",
                key=f"minutas_edit_description_{document['id']}",
                on_change=_keep_reference_open,
                args=(record["id"],),
            )
        known_date = st.checkbox(
            "Data conhecida",
            value=current_date is not None,
            key=f"minutas_edit_known_date_{document['id']}",
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        document_date = None
        if known_date:
            document_date = st.date_input(
                "Data do documento",
                value=current_date,
                key=f"minutas_edit_date_{document['id']}",
                on_change=_keep_reference_open,
                args=(record["id"],),
            )
        save, cancel = st.columns(2)
        if save.button(
            "Salvar alterações",
            key=f"minutas_edit_save_{document['id']}",
            type="primary",
            width="stretch",
        ):
            values = [
                _metadata_values(
                    item,
                    tipo=document_type,
                    descricao=description,
                    data_documento=document_date,
                )
                if item["id"] == document["id"]
                else _metadata_values(item)
                for item in documents
            ]
            try:
                service.update_documents(record["id"], values)
            except Exception as exc:
                _error(exc)
            else:
                _finish_action(
                    record["id"], "success", "Classificação atualizada."
                )
        if cancel.button(
            "Cancelar",
            key=f"minutas_edit_cancel_{document['id']}",
            width="stretch",
        ):
            _hide_panel(record["id"])
            st.rerun()


def _delete_document_panel(service, record, document):
    with st.container(border=True):
        st.warning(f"Excluir permanentemente {document['nome']}?")
        confirmed = st.checkbox(
            "Confirmo a exclusão deste documento.",
            key=f"minutas_delete_confirm_{document['id']}",
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        confirm, cancel = st.columns(2)
        if confirm.button(
            "Excluir documento",
            key=f"minutas_delete_confirm_button_{document['id']}",
            disabled=not confirmed,
            width="stretch",
        ):
            try:
                service.delete_document(
                    record["id"], document["id"], confirmed=confirmed
                )
            except Exception as exc:
                _error(exc)
            else:
                st.session_state.pop(
                    f"minutas_download_complement_{document['id']}", None
                )
                _finish_action(
                    record["id"],
                    "success",
                    "Documento complementar excluído com sucesso.",
                )
        if cancel.button(
            "Cancelar",
            key=f"minutas_delete_cancel_{document['id']}",
            width="stretch",
        ):
            _hide_panel(record["id"])
            st.rerun()


def _promote_document_panel(service, record, document):
    with st.container(border=True):
        st.markdown(f"**Definir como relatório principal — {document['nome']}**")
        st.caption(
            "O relatório principal atual será preservado como documento "
            "intermediário na mesma posição processual deste documento."
        )
        displaced_type = st.selectbox(
            "Classificação do relatório principal atual",
            COMPLEMENT_TYPES,
            index=COMPLEMENT_TYPES.index(
                "Relatório de Complementação de Instrução"
            ),
            key=f"minutas_promote_type_{document['id']}",
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        st.caption(f"Classificação selecionada: **{displaced_type}**")
        description = ""
        if displaced_type == OTHER_COMPLEMENT_TYPE:
            description = st.text_input(
                "Descrição *",
                key=f"minutas_promote_description_{document['id']}",
                on_change=_keep_reference_open,
                args=(record["id"],),
            )
        known_date = st.checkbox(
            "Data do relatório principal atual conhecida",
            key=f"minutas_promote_known_date_{document['id']}",
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        document_date = None
        if known_date:
            document_date = st.date_input(
                "Data do relatório principal atual",
                value=None,
                key=f"minutas_promote_date_{document['id']}",
                on_change=_keep_reference_open,
                args=(record["id"],),
            )
        confirmed = st.checkbox(
            "Confirmo a troca, preservando ambos os arquivos.",
            key=f"minutas_promote_confirm_{document['id']}",
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        confirm, cancel = st.columns(2)
        if confirm.button(
            "Definir como principal",
            key=f"minutas_promote_save_{document['id']}",
            type="primary",
            disabled=not confirmed,
            width="stretch",
        ):
            try:
                service.promote_document_to_primary(
                    record["id"],
                    document["id"],
                    {
                        "tipo": displaced_type,
                        "descricao": description,
                        "posicao": int(document["posicao"]),
                        "data_documento": document_date,
                    },
                    confirmed=confirmed,
                )
            except Exception as exc:
                _error(exc)
            else:
                st.session_state.pop(
                    f"minutas_download_{record['id']}_auditoria", None
                )
                st.session_state.pop(
                    f"minutas_download_complement_{document['id']}", None
                )
                _finish_action(
                    record["id"],
                    "success",
                    "Relatório principal reorganizado sem movimentar os arquivos.",
                )
        if cancel.button(
            "Cancelar",
            key=f"minutas_promote_cancel_{document['id']}",
            width="stretch",
        ):
            _hide_panel(record["id"])
            st.rerun()


def _complement_actions(service, record, documents, document, index):
    download_key = f"minutas_download_complement_{document['id']}"
    prepared = st.session_state.get(download_key)
    if prepared:
        st.download_button(
            "Baixar arquivo",
            prepared["content"],
            prepared["name"],
            prepared["mime"],
            key=f"minutas_complement_download_button_{document['id']}",
            icon=":material/download:",
            width="stretch",
        )
    elif st.button(
        "Preparar download",
        key=f"minutas_complement_prepare_{document['id']}",
        icon=":material/download:",
        width="stretch",
    ):
        if _prepare_complement_download(service, record, document):
            _keep_reference_open(record["id"])
            st.rerun()
    up, down = st.columns(2)
    if up.button(
        "Subir",
        key=f"minutas_move_up_{document['id']}",
        disabled=index == 0,
        icon=":material/arrow_upward:",
        width="stretch",
    ):
        _move_document(service, record, documents, index, -1)
    if down.button(
        "Descer",
        key=f"minutas_move_down_{document['id']}",
        disabled=index == len(documents) - 1,
        icon=":material/arrow_downward:",
        width="stretch",
    ):
        _move_document(service, record, documents, index, 1)
    if st.button(
        "Editar classificação",
        key=f"minutas_edit_open_{document['id']}",
        width="stretch",
    ):
        _show_panel("edit", document["id"], record["id"])
    if (
        document["tipo"] == "Relatório de Análise de Defesa"
        and document["mime"] == "application/pdf"
        and st.button(
            "Definir como relatório principal",
            key=f"minutas_promote_open_{document['id']}",
            width="stretch",
        )
    ):
        _show_panel("promote", document["id"], record["id"])
    if st.button(
        "Excluir documento",
        key=f"minutas_delete_open_{document['id']}",
        icon=":material/delete:",
        width="stretch",
    ):
        _show_panel("delete", document["id"], record["id"])


def _complement_document_row(service, record, documents, document, index):
    document_date = _document_date(document.get("data_documento"))
    date_text = (
        f"Data informada: {document_date.strftime('%d/%m/%Y')}"
        if document_date
        else "Data não informada"
    )
    with st.container(border=True):
        order, details, actions = st.columns(
            [1.1, 4.8, 1.35], vertical_alignment="center"
        )
        order.caption(f"Etapa {index + 1}")
        details.markdown(f"**{document['nome']}**")
        details.caption(
            f"{document['tipo']} · Ordem processual definida pelo administrador · "
            f"{date_text}"
        )
        if document.get("descricao"):
            details.caption(document["descricao"])
        with actions.popover("Ações", icon=":material/more_vert:"):
            _complement_actions(service, record, documents, document, index)
    if _panel_is("edit", document["id"]):
        _edit_document_panel(service, record, documents, document)
    elif _panel_is("promote", document["id"]):
        _promote_document_panel(service, record, document)
    elif _panel_is("delete", document["id"]):
        _delete_document_panel(service, record, document)


def _delete_reference_panel(service, record):
    with st.container(border=True):
        st.warning("Esta operação exclui a referência e todos os seus documentos.")
        confirmation = st.checkbox(
            "Confirmo a exclusão permanente desta referência.",
            key=f"minutas_reference_delete_confirm_{record['id']}",
            on_change=_keep_reference_open,
            args=(record["id"],),
        )
        confirm, cancel = st.columns(2)
        if confirm.button(
            "Excluir referência",
            key=f"minutas_reference_delete_save_{record['id']}",
            disabled=not confirmation,
            width="stretch",
        ):
            try:
                service.delete(record["id"], confirmed=confirmation)
            except Exception as exc:
                _error(exc)
            else:
                for kind in ("auditoria", "parecer"):
                    st.session_state.pop(
                        f"minutas_download_{record['id']}_{kind}", None
                    )
                _finish_action(
                    record["id"], "success", "Referência excluída com sucesso."
                )
        if cancel.button(
            "Cancelar",
            key=f"minutas_reference_delete_cancel_{record['id']}",
            width="stretch",
        ):
            _hide_panel(record["id"])
            st.rerun()


def _reference_card(service, record, expanded=False):
    title = f"Processo {record['numero_processo']} · Exercício {record['exercicio']}"
    with st.expander(title, expanded=expanded):
        header, reference_actions = st.columns(
            [5, 1.3], vertical_alignment="center"
        )
        header.caption(f"{record['subtipo']} · Procuradora {record['procuradora']}")
        with reference_actions.popover("Processo", icon=":material/settings:"):
            if st.button(
                "Excluir referência",
                key=f"minutas_reference_delete_open_{record['id']}",
                icon=":material/delete:",
                width="stretch",
            ):
                _show_panel("delete_reference", record["id"], record["id"])
        section_label("Documentos do Processo")
        try:
            documents = service.list_documents(record["id"])
        except Exception as exc:
            _error(exc)
            return
        if st.button(
            "+ Adicionar documento",
            key=f"minutas_add_open_{record['id']}",
        ):
            _show_panel("add", record["id"], record["id"])
        if _panel_is("add", record["id"]):
            _add_document_panel(service, record, documents)
        _main_document_row(
            service,
            record,
            "auditoria",
            "Início",
            "Relatório da Auditoria · Relatório principal",
        )
        for index, document in enumerate(documents):
            _complement_document_row(
                service, record, documents, document, index
            )
        _main_document_row(
            service,
            record,
            "parecer",
            "Conclusão",
            "Parecer definitivo",
        )
        if _panel_is("delete_reference", record["id"]):
            _delete_reference_panel(service, record)


def _library_tab(store, identity):
    st.caption(
        "Administração centralizada de relatórios da Auditoria e pareceres definitivos."
    )
    service = _service(store, identity)
    if service is None:
        return
    if message := st.session_state.pop("minutas_message", None):
        getattr(st, message[0])(message[1])
    st.subheader("Prestações de Contas Anuais (PCA)")
    _create_form(service)
    section_label("Referências cadastradas")
    try:
        records = service.list()
    except Exception as exc:
        _error(exc)
        return
    if not records:
        empty_state("Nenhuma referência cadastrada.")
        return
    keep_open = st.session_state.pop("minutas_keep_open", None)
    for record in records:
        _reference_card(
            service, record, expanded=str(record["id"]) == str(keep_open)
        )


def render(store, identity):
    st.title("Gerador de Minutas de Pareceres — Projeto-Piloto")
    render_html(badge("AMBIENTE EXPERIMENTAL", "warning"))
    st.caption("Projeto-piloto privado do MPC-PB · Procuradora Elvira Samara")
    library, generator = st.tabs(["Biblioteca de Referências", "Gerar Minuta"])
    with library:
        _library_tab(store, identity)
    with generator:
        empty_state("A geração de minutas será implementada na Fase 3.")
