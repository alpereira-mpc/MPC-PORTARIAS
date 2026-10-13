"""Private UI for the Minutas IA reference library."""

from datetime import date, datetime
import re

import streamlit as st

from database.minutas_referencias import MinutasReferenciasStore
from services.minutas_library import (
    COMPLEMENT_TYPES,
    MinutasAccessDenied,
    MinutasDuplicateError,
    MinutasLibraryService,
    MinutasValidationError,
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
        )
        opinion = st.file_uploader(
            "Parecer definitivo — PDF ou DOCX, até 10 MB *",
            type=["pdf", "docx"],
            key="minutas_create_opinion",
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


def _complement_create_form(service, record, documents):
    with st.form(f"minutas_complement_create_{record['id']}", border=True):
        st.markdown("**Adicionar documento**")
        document_type = st.selectbox(
            "Tipo *", COMPLEMENT_TYPES, key=f"minutas_complement_type_{record['id']}"
        )
        description = st.text_input(
            "Descrição",
            help="Obrigatória para outros relatórios ou manifestações processuais.",
            key=f"minutas_complement_description_{record['id']}",
        )
        first, second = st.columns(2)
        position = first.number_input(
            "Posição cronológica *",
            min_value=1,
            value=max((int(item["posicao"]) for item in documents), default=0) + 1,
            step=1,
            key=f"minutas_complement_position_{record['id']}",
        )
        known_date = second.checkbox(
            "Data conhecida", key=f"minutas_complement_known_date_{record['id']}"
        )
        entered_date = second.date_input(
            "Data do documento",
            key=f"minutas_complement_date_{record['id']}",
        )
        document_date = entered_date if known_date else None
        upload = st.file_uploader(
            "Documento — PDF ou DOCX, até 10 MB *",
            type=["pdf", "docx"],
            key=f"minutas_complement_file_{record['id']}",
        )
        submitted = st.form_submit_button(
            "Adicionar documento", icon=":material/add:"
        )
    if not submitted:
        return
    try:
        service.add_document(
            record["id"],
            {
                "tipo": document_type,
                "descricao": description,
                "posicao": position,
                "data_documento": document_date,
            },
            _upload_tuple(upload),
        )
    except Exception as exc:
        _error(exc)
    else:
        st.session_state["minutas_message"] = (
            "success",
            "Documento complementar adicionado com sucesso.",
        )
        st.rerun()


def _complement_list(service, record, documents):
    if not documents:
        empty_state("Nenhum documento complementar cadastrado.")
        return
    for document in documents:
        date_text = (
            f" · {_document_date(document['data_documento']).strftime('%d/%m/%Y')}"
            if document.get("data_documento")
            else ""
        )
        st.markdown(f"**{document['posicao']}. {document['tipo']}**")
        st.caption(f"{document['nome']} · {document['tamanho']:,} bytes{date_text}")
        if document.get("descricao"):
            st.write(document["descricao"])
        download_key = f"minutas_download_complement_{document['id']}"
        prepared = st.session_state.get(download_key)
        left, right = st.columns(2)
        if prepared:
            left.download_button(
                "Baixar",
                prepared["content"],
                prepared["name"],
                prepared["mime"],
                key=f"minutas_complement_download_button_{document['id']}",
                icon=":material/download:",
            )
        elif left.button(
            "Preparar download",
            key=f"minutas_complement_prepare_{document['id']}",
            icon=":material/download:",
        ):
            if _prepare_complement_download(service, record, document):
                st.rerun()
        confirmed = right.checkbox(
            "Confirmar exclusão", key=f"minutas_complement_confirm_{document['id']}"
        )
        if right.button(
            "Excluir documento",
            key=f"minutas_complement_delete_{document['id']}",
            disabled=not confirmed,
            icon=":material/delete:",
        ):
            try:
                service.delete_document(
                    record["id"], document["id"], confirmed=confirmed
                )
            except Exception as exc:
                _error(exc)
            else:
                st.session_state.pop(download_key, None)
                st.session_state["minutas_message"] = (
                    "success",
                    "Documento complementar excluído com sucesso.",
                )
                st.rerun()


def _complement_organize_form(service, record, documents):
    if not documents:
        return
    with st.form(f"minutas_complement_organize_{record['id']}", border=True):
        st.markdown("**Corrigir classificação e sequência**")
        values = []
        for document in documents:
            st.caption(document["nome"])
            first, second = st.columns(2)
            document_type = first.selectbox(
                "Tipo",
                COMPLEMENT_TYPES,
                index=COMPLEMENT_TYPES.index(document["tipo"]),
                key=f"minutas_organize_type_{document['id']}",
            )
            position = second.number_input(
                "Posição cronológica",
                min_value=1,
                value=int(document["posicao"]),
                step=1,
                key=f"minutas_organize_position_{document['id']}",
            )
            description = st.text_input(
                "Descrição",
                value=document.get("descricao") or "",
                key=f"minutas_organize_description_{document['id']}",
            )
            current_date = _document_date(document.get("data_documento"))
            known_date = st.checkbox(
                "Data conhecida",
                value=current_date is not None,
                key=f"minutas_organize_known_date_{document['id']}",
            )
            entered_date = st.date_input(
                "Data do documento",
                value=current_date or date.today(),
                key=f"minutas_organize_date_{document['id']}",
            )
            document_date = entered_date if known_date else None
            values.append(
                {
                    "id": document["id"],
                    "tipo": document_type,
                    "descricao": description,
                    "posicao": position,
                    "data_documento": document_date,
                }
            )
        submitted = st.form_submit_button("Salvar classificação e sequência")
    if submitted:
        try:
            service.update_documents(record["id"], values)
        except Exception as exc:
            _error(exc)
        else:
            st.session_state["minutas_message"] = (
                "success",
                "Classificação e sequência atualizadas.",
            )
            st.rerun()


def _sedh_reorganization(service, record, documents):
    process_key = re.sub(r"\D", "", str(record["numero_processo"]))
    candidates = [
        item
        for item in documents
        if item["mime"] == "application/pdf"
        and item["tipo"] == "Relatório de Análise de Defesa"
    ]
    if process_key != "0265025" or record["procuradora"] != PROCURADORA:
        return
    with st.container(border=True):
        st.markdown("**Reorganizar relatório principal — caso SEDH**")
        st.caption(
            "Troca apenas os vínculos de metadados em uma transação. "
            "Os arquivos permanecem no bucket privado."
        )
        if not candidates:
            st.info("Adicione primeiro o Relatório de Análise de Defesa em PDF.")
            return
        labels = {
            item["id"]: f"{item['posicao']}. {item['tipo']} — {item['nome']}"
            for item in candidates
        }
        with st.form(f"minutas_sedh_reorganize_{record['id']}", border=True):
            selected_id = st.selectbox(
                "Novo Relatório da Auditoria",
                tuple(labels),
                format_func=labels.get,
            )
            st.text_input(
                "Arquivo atualmente no campo principal",
                value=record["auditoria_nome"],
                disabled=True,
            )
            position = st.number_input(
                "Posição do Relatório de Complementação",
                min_value=1,
                value=3,
                step=1,
            )
            known_date = st.checkbox("Data do relatório de complementação conhecida")
            entered_date = st.date_input("Data do relatório de complementação")
            document_date = entered_date if known_date else None
            confirmation = st.checkbox(
                "Confirmo a troca do relatório principal, preservando ambos os arquivos."
            )
            submitted = st.form_submit_button("Reorganizar documentos")
        if submitted:
            try:
                service.promote_document_to_primary(
                    record["id"],
                    selected_id,
                    {
                        "tipo": "Relatório de Complementação de Instrução",
                        "descricao": "",
                        "posicao": position,
                        "data_documento": document_date,
                    },
                    confirmed=confirmation,
                )
            except Exception as exc:
                _error(exc)
            else:
                st.session_state.pop(
                    f"minutas_download_{record['id']}_auditoria", None
                )
                st.session_state["minutas_message"] = (
                    "success",
                    "Relatório principal reorganizado sem movimentar os arquivos.",
                )
                st.rerun()


def _document_actions(service, record, kind, label, accepted):
    st.markdown(f"**{label}**")
    st.caption(
        f"{record[f'{kind}_nome']} · {record[f'{kind}_tamanho']:,} bytes"
    )
    download_key = f"minutas_download_{record['id']}_{kind}"
    prepared = st.session_state.get(download_key)
    left, right = st.columns(2)
    if prepared:
        left.download_button(
            "Baixar",
            prepared["content"],
            prepared["name"],
            prepared["mime"],
            key=f"minutas_download_button_{record['id']}_{kind}",
            icon=":material/download:",
        )
    elif left.button(
        "Preparar download",
        key=f"minutas_prepare_{record['id']}_{kind}",
        icon=":material/download:",
    ):
        if _prepare_download(service, record, kind):
            st.rerun()

    replacement = right.file_uploader(
        "Substituir arquivo",
        type=accepted,
        key=f"minutas_replace_file_{record['id']}_{kind}",
        label_visibility="collapsed",
    )
    if st.button(
        "Confirmar substituição",
        key=f"minutas_replace_{record['id']}_{kind}",
        disabled=replacement is None,
    ):
        try:
            updated = service.replace(record["id"], kind, _upload_tuple(replacement))
        except Exception as exc:
            _error(exc)
        else:
            st.session_state.pop(download_key, None)
            if updated.get("cleanup_pending"):
                st.session_state["minutas_message"] = (
                    "warning",
                    "Arquivo substituído, mas a limpeza da versão anterior "
                    "ficou pendente.",
                )
            else:
                st.session_state["minutas_message"] = (
                    "success",
                    "Arquivo substituído com sucesso.",
                )
            st.rerun()


def _reference_card(service, record):
    title = f"Processo {record['numero_processo']} · Exercício {record['exercicio']}"
    with st.expander(title):
        st.caption(f"{record['subtipo']} · Procuradora {record['procuradora']}")
        audit_column, opinion_column = st.columns(2)
        with audit_column:
            _document_actions(
                service, record, "auditoria", "Relatório da Auditoria", ["pdf"]
            )
        with opinion_column:
            _document_actions(
                service,
                record,
                "parecer",
                "Parecer definitivo",
                ["pdf", "docx"],
            )
        section_label("Documentos Complementares")
        try:
            documents = service.list_documents(record["id"])
        except Exception as exc:
            _error(exc)
            return
        _complement_create_form(service, record, documents)
        _complement_list(service, record, documents)
        _complement_organize_form(service, record, documents)
        _sedh_reorganization(service, record, documents)
        confirmation = st.checkbox(
            "Confirmo a exclusão permanente desta referência e de todos os documentos.",
            key=f"minutas_delete_confirm_{record['id']}",
        )
        if st.button(
            "Excluir referência",
            key=f"minutas_delete_{record['id']}",
            disabled=not confirmation,
            icon=":material/delete:",
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
                st.session_state["minutas_message"] = (
                    "success",
                    "Referência excluída com sucesso.",
                )
                st.rerun()


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
    for record in records:
        _reference_card(service, record)


def render(store, identity):
    st.title("Gerador de Minutas de Pareceres — Projeto-Piloto")
    render_html(badge("AMBIENTE EXPERIMENTAL", "warning"))
    st.caption("Projeto-piloto privado do MPC-PB · Procuradora Elvira Samara")
    library, generator = st.tabs(["Biblioteca de Referências", "Gerar Minuta"])
    with library:
        _library_tab(store, identity)
    with generator:
        empty_state("A geração de minutas será implementada na Fase 3.")
