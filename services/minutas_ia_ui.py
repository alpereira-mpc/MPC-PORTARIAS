"""Private UI for the Minutas IA reference library."""

from datetime import datetime

import streamlit as st

from database.minutas_referencias import MinutasReferenciasStore
from services.minutas_library import (
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
        confirmation = st.checkbox(
            "Confirmo a exclusão permanente desta referência e dos dois documentos.",
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
