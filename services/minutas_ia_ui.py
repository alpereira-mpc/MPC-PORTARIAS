"""Private UI for the Minutas IA reference library."""

from datetime import date, datetime
from hashlib import sha256
import json
import logging
from time import perf_counter

import streamlit as st

from database.minutas_referencias import MinutasReferenciasStore
from services.ai_service import GeminiErro, GeminiNaoConfigurada
from services.minutas_generation import (
    RAG_RECOVERY_VERSION,
    VALIDATION_POLICY_VERSION,
    MinutasGenerationService,
)
from services.minutas_docx import MinutasDocxError, MinutasDocxService
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
from services.minutas_search import MinutasSearchService, SEARCH_NATURE
from services.minutas_extraction import (
    MinutasExtractionError,
    MinutasExtractionService,
)
from services.minutas_storage import (
    StorageConfigurationError,
    StorageOperationError,
    SupabaseMinutasStorage,
)
from services.ui_theme import badge, empty_state, render_html, section_label


LOGGER = logging.getLogger(__name__)


def _clear_generated_result(result_key, docx_cache_key):
    st.session_state.pop(result_key, None)
    st.session_state.pop(docx_cache_key, None)


def _generated_review_items(result):
    items = []
    for warning in result.get("advertencias_qualidade") or ():
        text = str(warning or "").strip()
        if text:
            items.append(("Advertência de qualidade", text))
    for issue in result.get("advertencias_documentais") or ():
        if not isinstance(issue, dict):
            continue
        location = str(issue.get("documento") or "Documento não informado")
        if issue.get("pagina") is not None:
            location += f", p. {issue['pagina']}"
        items.append(
            (
                "Conferência documental",
                f"{issue.get('irregularidade') or '—'} · "
                f"{issue.get('categoria') or 'Campo não informado'} · {location}: "
                f"{issue.get('natureza') or 'conferência necessária'}",
            )
        )
    return items


def _generated_review_panel(result):
    items = _generated_review_items(result)
    if not items:
        return
    st.caption(
        f"{len(items)} advertência(s) disponível(is) para conferência facultativa do Assessor."
    )
    with st.expander(f"Conferência facultativa ({len(items)})", expanded=False):
        for category, text in items:
            st.markdown(f"- **{category}:** {text}")


def _docx_signature(target_id, analysis, result):
    payload = {
        "target_id": str(target_id),
        "fontes_versao": result.get("fontes_versao"),
        "validation_policy_version": result.get("validation_policy_version"),
        "analysis_policy_version": analysis.get("validation_policy_version"),
        "estrutura": result.get("estrutura"),
        "texto": result.get("texto"),
        "metadados_cabecalho": result.get("metadados_cabecalho"),
        "advertencias_documentais": result.get("advertencias_documentais"),
        "advertencias_qualidade": result.get("advertencias_qualidade"),
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _cached_docx(service, generator, target_id, analysis, result, cache_key):
    signature = _docx_signature(target_id, analysis, result)
    cached = st.session_state.get(cache_key)
    if isinstance(cached, dict) and cached.get("signature") == signature:
        LOGGER.info("MINUTAS_PERF etapa=docx cache=hit duracao_ms=0")
        return cached["word"]
    started = perf_counter()
    word = MinutasDocxService(
        service, source_summary=generator.source_summary
    ).export(target_id, analysis, result)
    st.session_state[cache_key] = {"signature": signature, "word": word}
    LOGGER.info(
        "MINUTAS_PERF etapa=docx cache=miss duracao_ms=%d bytes=%d",
        round((perf_counter() - started) * 1000),
        len(word.content),
    )
    return word


def _service(store, identity, *, administrative=False):
    try:
        storage = SupabaseMinutasStorage.from_secrets(st.secrets)
        repository = MinutasReferenciasStore(store,
            specialty_id=None if administrative else st.session_state.get("minutas_specialty"),
            include_acervo=True)
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
            MinutasDocxError,
            MinutasExtractionError,
            StorageOperationError,
            GeminiErro,
            GeminiNaoConfigurada,
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


def _extraction_label(extraction):
    if not extraction:
        return "Extração: Pendente"
    if extraction["status"] == "PROCESSADO":
        return "Extração: Processado"
    return "Extração: Necessita atenção"


def _extraction_actions(extractor, record, path, document_key, extraction):
    if extraction and st.button(
        "Visualizar texto extraído",
        key=f"minutas_extract_view_open_{document_key}",
        width="stretch",
    ):
        _show_panel("extraction", document_key, record["id"])
    if extraction and st.button(
        "Reprocessar extração",
        key=f"minutas_extract_retry_{document_key}",
        width="stretch",
    ):
        try:
            extractor.reprocess_document(record["id"], path)
        except Exception as exc:
            _error(exc)
        else:
            _finish_action(record["id"], "success", "Documento reprocessado.")


def _extraction_panel(extractor, record, path, document_key):
    try:
        extraction = extractor.view(record["id"], path)
    except Exception as exc:
        _error(exc)
        return
    with st.container(border=True):
        st.markdown(f"**Texto extraído — {extraction['arquivo_nome']}**")
        st.caption(
            f"Origem: {extraction['tipo']} · {_extraction_label(extraction)}"
        )
        if extraction["erro"]:
            st.warning(extraction["erro"])
        if extraction["arquivo_mime"] == "application/pdf":
            units = extraction["unidades"]
            if units:
                pages = [unit["pagina"] for unit in units]
                page = st.selectbox(
                    "Página de origem",
                    pages,
                    key=f"minutas_extract_page_{document_key}",
                    on_change=_keep_reference_open,
                    args=(record["id"],),
                )
                text = next(
                    unit["texto"] for unit in units if unit["pagina"] == page
                )
                st.markdown(f"**Conteúdo da página {page}**")
                st.code(
                    text,
                    language=None,
                    wrap_lines=True,
                    height=280,
                )
        else:
            st.caption("Documento DOCX: a origem é textual e não possui paginação.")
            st.markdown("**Conteúdo extraído**")
            st.code(
                extraction["texto_integral"],
                language=None,
                wrap_lines=True,
                height=280,
            )
        if extraction["secoes"]:
            names = ", ".join(
                section["titulo_original"] for section in extraction["secoes"]
            )
            st.caption(f"Seções explicitamente reconhecidas: {names}")
        if st.button(
            "Fechar texto",
            key=f"minutas_extract_view_close_{document_key}",
        ):
            _hide_panel(record["id"])
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


def _main_document_actions(
    service, extractor, record, kind, accepted, document_key, extraction
):
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
    _extraction_actions(
        extractor,
        record,
        record[f"{kind}_path"],
        document_key,
        extraction,
    )


def _main_document_row(
    service, extractor, record, kind, position_label, type_label, extraction
):
    document_key = f"{record['id']}_{kind}"
    with st.container(border=True):
        order, details, actions = st.columns(
            [1.1, 4.8, 1.35], vertical_alignment="center"
        )
        order.caption(position_label)
        details.markdown(f"**{record[f'{kind}_nome']}**")
        details.caption(
            f"{type_label} · {record[f'{kind}_tamanho']:,} bytes · "
            f"{_extraction_label(extraction)}"
        )
        with actions.popover("Ações", icon=":material/more_vert:"):
            accepted = ["pdf"] if kind == "auditoria" else ["pdf", "docx"]
            _main_document_actions(
                service,
                extractor,
                record,
                kind,
                accepted,
                document_key,
                extraction,
            )
    if _panel_is("extraction", document_key):
        _extraction_panel(
            extractor, record, record[f"{kind}_path"], document_key
        )


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


def _complement_actions(
    service, extractor, record, documents, document, index, extraction
):
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
    _extraction_actions(
        extractor,
        record,
        document["path"],
        document["id"],
        extraction,
    )


def _complement_document_row(
    service, extractor, record, documents, document, index, extraction
):
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
            f"{date_text} · {_extraction_label(extraction)}"
        )
        if document.get("descricao"):
            details.caption(document["descricao"])
        with actions.popover("Ações", icon=":material/more_vert:"):
            _complement_actions(
                service,
                extractor,
                record,
                documents,
                document,
                index,
                extraction,
            )
    if _panel_is("edit", document["id"]):
        _edit_document_panel(service, record, documents, document)
    elif _panel_is("promote", document["id"]):
        _promote_document_panel(service, record, document)
    elif _panel_is("delete", document["id"]):
        _delete_document_panel(service, record, document)
    elif _panel_is("extraction", document["id"]):
        _extraction_panel(extractor, record, document["path"], document["id"])


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


def _reference_card(service, extractor, record, expanded=False):
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
            extractions = extractor.statuses(record["id"])
        except Exception as exc:
            _error(exc)
            return
        add_column, process_column = st.columns(2)
        if add_column.button(
            "+ Adicionar documento", key=f"minutas_add_open_{record['id']}"
        ):
            _show_panel("add", record["id"], record["id"])
        if process_column.button(
            "Processar documentos",
            key=f"minutas_extract_process_{record['id']}",
            icon=":material/document_search:",
        ):
            try:
                with st.spinner("Extraindo os documentos localmente..."):
                    results = extractor.process_reference(record["id"])
            except Exception as exc:
                _error(exc)
            else:
                attention = sum(
                    result["status"] == "ATENCAO" for result in results
                )
                message = "Documentos processados."
                level = "success"
                if attention:
                    message = (
                        f"Processamento concluído; {attention} documento(s) "
                        "necessita(m) atenção."
                    )
                    level = "warning"
                _finish_action(record["id"], level, message)
        if _panel_is("add", record["id"]):
            _add_document_panel(service, record, documents)
        _main_document_row(
            service,
            extractor,
            record,
            "auditoria",
            "Início",
            "Relatório da Auditoria · Relatório principal",
            extractions.get(record["auditoria_path"]),
        )
        for index, document in enumerate(documents):
            _complement_document_row(
                service,
                extractor,
                record,
                documents,
                document,
                index,
                extractions.get(document["path"]),
            )
        _main_document_row(
            service,
            extractor,
            record,
            "parecer",
            "Conclusão",
            "Parecer definitivo",
            extractions.get(record["parecer_path"]),
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
    records_by_id = {str(record["id"]): record for record in records}
    selector_key = "minutas_library_reference"
    if str(keep_open or "") in records_by_id:
        st.session_state[selector_key] = str(keep_open)
    elif str(st.session_state.get(selector_key) or "") not in records_by_id:
        st.session_state[selector_key] = next(iter(records_by_id))
    selected_id = st.selectbox(
        "Processo para administrar",
        tuple(records_by_id),
        format_func=lambda identifier: (
            f"{records_by_id[identifier]['numero_processo']} · "
            f"{records_by_id[identifier]['subtipo']} · "
            f"Exercício {records_by_id[identifier]['exercicio']}"
        ),
        key=selector_key,
    )
    extractor = MinutasExtractionService(service)
    _reference_card(
        service,
        extractor,
        records_by_id[selected_id],
        expanded=True,
    )


def _analysis_panel(analysis_result, *, show_details=True):
    def source_suffix(item):
        sources = item.get("fontes") or ()
        if not sources:
            return ""
        labels = [
            f"fonte {source['fonte_id']}, p. {source['pagina']}"
            + (" — não verificado" if source.get("verificado") is False else "")
            for source in sources
        ]
        return " _(" + "; ".join(labels) + ")_"

    section_label("Etapa 1 — Análise probatória e jurídica")
    analysis = analysis_result["analise"]
    if analysis.get("resumo_processual"):
        st.caption(analysis["resumo_processual"])
    irregularities = analysis.get("irregularidades", ()) if show_details else ()
    for index, irregularity in enumerate(irregularities, 1):
        title = irregularity.get("apontamento") or f"Irregularidade {index}"
        with st.expander(f"{index}. {title}"):
            facts = irregularity.get("fatos_documentados") or ()
            defenses = irregularity.get("argumentos_defesa") or ()
            audit = irregularity.get("avaliacao_auditoria") or {}
            if facts:
                st.markdown("**Fatos documentados**")
                st.markdown(
                    "\n".join(
                        f"- {item['descricao']}{source_suffix(item)}" for item in facts
                    )
                )
            if defenses:
                st.markdown("**Alegações da defesa**")
                st.markdown(
                    "\n".join(
                        f"- {item['alegacao']} — comprovação: "
                        f"{item['comprovacao_documental']}{source_suffix(item)}"
                        for item in defenses
                    )
                )
            st.markdown("**Conclusão da Auditoria após a defesa**")
            st.markdown(
                (audit.get("descricao") or "Não identificada nas fontes.")
                + source_suffix(audit)
            )
            st.caption(
                "Tratamento da defesa: "
                + str(audit.get("tratamento_defesa") or "Não identificado")
            )
            attention = irregularity.get("pontos_controvertidos") or ()
            if attention:
                st.markdown("**Pontos que exigem atenção**")
                st.markdown("\n".join(f"- {item}" for item in attention))
    general_attention = (
        analysis.get("pontos_atencao_gerais") or () if show_details else ()
    )
    if general_attention:
        st.markdown("**Pontos gerais que exigem atenção**")
        st.markdown("\n".join(f"- {item}" for item in general_attention))
    pending = analysis_result.get("pendencias_conferencia") or ()
    if pending:
        affected = sorted(
            {str(issue.get("irregularidade") or "—") for issue in pending}
        )
        st.warning(
            "Análise concluída com pontos para conferência documental. A minuta "
            "pode ser gerada e deverá ser revisada antes de sua utilização."
        )
        st.caption(
            f"{len(pending)} advertência(s) em "
            f"{len(affected)} irregularidade(s): {', '.join(affected)}."
        )
        with st.expander(f"Pontos para conferência documental ({len(pending)})"):
            for issue in pending:
                location = issue.get("documento") or "Documento não informado"
                if issue.get("pagina") is not None:
                    location += f", p. {issue['pagina']}"
                st.markdown(
                    f"- **{issue.get('irregularidade') or '—'} · "
                    f"{issue.get('categoria') or 'Campo não informado'}** · "
                    f"{location}: {issue.get('natureza') or 'conferência necessária'}"
                )
    st.caption(
        f"Modelo: {analysis_result.get('modelo') or '—'} · "
        f"Tokens: {analysis_result.get('tokens_total') or 'não informado'} · "
        f"Duração: {analysis_result.get('duracao_ms', 0) / 1000:.1f} s"
    )


def _legal_references_panel(analysis_result):
    recovery = analysis_result.get("recuperacao_juridica") or {}
    section_label("Referências jurídicas recuperadas")
    if recovery.get("estado") == "INDISPONIVEL":
        st.warning(
            recovery.get("aviso")
            or "A pesquisa jurídica ficou indisponível; a geração poderá continuar."
        )
        return
    passages = recovery.get("trechos") or ()
    if not passages:
        st.info(
            "Nenhuma referência histórica pertinente foi localizada. A geração "
            "poderá continuar sem recuperação jurídica."
        )
        return
    st.caption(
        f"{recovery.get('pareceres', 0)} parecer(es) · "
        f"{len(passages)} trecho(s) recuperado(s). A inclusão no Gemini depende "
        "da opção escolhida na etapa de geração."
    )
    for index, item in enumerate(passages, 1):
        pages = (
            f"p. {item['pagina']}"
            if item.get("pagina") is not None
            else "página não identificada"
        )
        matters = "; ".join(item.get("materias") or ())
        with st.expander(
            f"{index}. Processo {item['processo']} · {pages} · {matters}"
        ):
            st.caption(
                f"{item['documento']} · manifestação histórica não vinculante"
            )
            st.code(
                item["trecho_literal"], language=None, wrap_lines=True, height=220
            )


def _technical_diagnostics_panel(
    diagnostics,
    target_id,
    *,
    generator=None,
    analysis=None,
    analysis_key=None,
    result_key=None,
    docx_cache_key=None,
):
    with st.expander("Diagnóstico técnico do piloto"):
        if not diagnostics or not diagnostics.get("disponivel"):
            st.info(
                "O diagnóstico não está disponível nesta sessão. Execute novamente "
                "Analisar fundamentos."
            )
            return

        st.caption(
            "Informações efêmeras da execução atual. Este painel não altera nem "
            "dispensa as verificações documentais."
        )
        conference = diagnostics.get("conferencia_documental") or ()
        st.markdown("**Conferência documental**")
        if not conference:
            st.success("Nenhuma advertência documental foi registrada nesta análise.")
        for index, issue in enumerate(conference, 1):
            page = (
                f"página {issue['pagina']}"
                if issue.get("pagina") is not None
                else "página não informada"
            )
            st.markdown(
                f"**{issue.get('irregularidade') or '—'} — "
                f"{issue.get('campo') or 'Campo não informado'}**"
            )
            st.caption(
                f"{issue.get('documento') or 'Documento não informado'} · {page} · "
                f"{issue.get('motivo') or 'motivo não informado'}"
            )
            if issue.get("pagina_corrigida_automaticamente"):
                st.success(
                    "Página corrigida automaticamente de "
                    f"{issue.get('pagina_original')} para "
                    f"{issue.get('pagina_corrigida')} após correspondência literal "
                    "única no mesmo documento."
                )
            st.markdown("Trecho literal fornecido pelo Gemini:")
            st.code(
                issue.get("trecho_suporte") or "(trecho não informado)",
                language=None,
                wrap_lines=True,
            )
            other_pages = issue.get("correspondencias_em_outras_paginas") or ()
            if other_pages:
                st.warning(
                    "Correspondência textual localizada também na(s) página(s) "
                    + ", ".join(str(item) for item in other_pages)
                    + ". A página indicada não foi corrigida automaticamente."
                )
            page_text = str(issue.get("texto_pagina") or "")
            st.markdown(
                "Texto extraído da página validada:"
                if issue.get("pagina_corrigida_automaticamente")
                else "Texto extraído da página indicada:"
            )
            preview = page_text[:4000]
            st.code(
                preview or "(texto da página indisponível)",
                language=None,
                wrap_lines=True,
                height=260,
            )
            if len(page_text) > len(preview) and st.checkbox(
                "Mostrar conteúdo integral desta página",
                key=f"minutas_diagnostic_full_page_{target_id}_{index}",
            ):
                st.code(page_text, language=None, wrap_lines=True, height=420)

            candidates = issue.get("candidatos") or ()
            reference_key = issue.get("referencia_chave")
            can_review = bool(
                reference_key
                and generator is not None
                and analysis is not None
                and analysis_key
                and result_key
            )
            review_key = (
                f"minutas_evidence_review_{target_id}_{reference_key}"
            )
            if can_review and st.button(
                "Conferir evidência",
                key=f"{review_key}_open",
                type="tertiary",
            ):
                st.session_state[review_key] = True
            if can_review and st.session_state.get(review_key):
                st.markdown("**Afirmação associada**")
                st.write(issue.get("afirmacao_associada") or "Não informada.")
                if not candidates:
                    st.info(
                        "Nenhuma passagem candidata foi localizada com os "
                        "critérios objetivos. A advertência permanece registrada "
                        "para revisão humana."
                    )
                    continue
                candidate_by_id = {item["id"]: item for item in candidates}
                selected_id = st.selectbox(
                    "Passagem candidata do documento autorizado",
                    tuple(candidate_by_id),
                    format_func=lambda identifier: (
                        f"p. {candidate_by_id[identifier]['pagina']} · "
                        + (
                            "compatível com os critérios objetivos"
                            if candidate_by_id[identifier]["substituicao_segura"]
                            else "exige nova análise jurídica"
                        )
                    ),
                    key=f"{review_key}_candidate",
                )
                selected = candidate_by_id[selected_id]
                st.code(
                    selected["trecho_literal"],
                    language=None,
                    wrap_lines=True,
                )
                if selected["substituicao_segura"]:
                    st.caption(
                        "A seleção será novamente comparada à extração atual no "
                        "servidor e ficará sinalizada para revisão humana."
                    )
                else:
                    st.warning(
                        selected.get("motivo_seguranca")
                        or "A passagem não sustenta a afirmação original."
                    )
                if st.button(
                    "Aplicar trecho e revalidar",
                    key=f"{review_key}_apply",
                    disabled=not selected["substituicao_segura"],
                ):
                    try:
                        corrected = generator.correct_evidence_reference(
                            target_id,
                            analysis,
                            reference_key,
                            selected_id,
                        )
                    except Exception as exc:
                        _error(exc)
                    else:
                        st.session_state[analysis_key] = corrected
                        _clear_generated_result(result_key, docx_cache_key)
                        st.session_state.pop(review_key, None)
                        st.success(
                            "Evidência corrigida e análise completa revalidada."
                        )
                        st.rerun()

        recovery = diagnostics.get("recuperacao_juridica") or {}
        st.markdown("**Recuperação RAG**")
        queries = recovery.get("consultas") or ()
        if not queries:
            st.info("Nenhuma consulta lexical foi construída nesta execução.")
        for index, query in enumerate(queries, 1):
            primary = query.get("consulta_principal") or query.get("consulta") or "—"
            alternative = query.get("consulta_alternativa") or ""
            st.markdown(f"{index}. Principal: `{primary}`")
            if alternative:
                st.markdown(f"   Alternativa: `{alternative}`")
            st.caption(
                f"Origem: irregularidade {query.get('irregularidade_id') or '—'} · "
                f"matéria: {query.get('materia') or '—'} · resultados retornados: "
                f"{query.get('resultados_retornados', 'não registrado')}"
            )
            for attempt in query.get("tentativas") or ():
                st.caption(
                    f"Tentativa {attempt.get('tipo') or '—'}: "
                    f"{attempt.get('resultados_retornados', 0)} resultado(s) · "
                    f"consulta `{attempt.get('consulta') or '—'}`."
                )
            discarded = query.get("descartados") or {}
            if discarded:
                st.caption(
                    "Descartados — barreira documental: "
                    f"{discarded.get('barreira_documental', 0)}; duplicação: "
                    f"{discarded.get('duplicacao', 0)}; sem pertinência temática: "
                    f"{discarded.get('sem_pertinencia', 0)}; sem trecho utilizável: "
                    f"{discarded.get('sem_trecho', 0)}."
                )
        omitted = recovery.get("materias_nao_pesquisadas") or ()
        if omitted:
            st.warning(
                "Matérias não pesquisadas: "
                + "; ".join(
                    f"{item.get('irregularidade_id') or '—'} — "
                    f"{item.get('materia') or 'sem descrição'} "
                    f"({item.get('motivo') or 'limite'})"
                    for item in omitted
                )
            )
        if recovery.get("motivo_ausencia"):
            st.info(recovery["motivo_ausencia"])
        selected = recovery.get("trechos") or ()
        st.caption(
            f"Resultados retornados: {recovery.get('resultados_retornados', 0)} · "
            f"consultas executadas: {recovery.get('consultas_executadas', 0)} · "
            f"trechos selecionados: {len(selected)}. A pertinência temática é "
            "conferida no trecho efetivamente selecionado."
        )
        for index, item in enumerate(selected, 1):
            st.markdown(
                f"Trecho selecionado {index}: processo {item.get('processo') or '—'}, "
                f"{item.get('documento') or 'documento não informado'}, "
                f"p. {item.get('pagina') or 'não identificada'}"
            )
            st.code(
                item.get("trecho_literal") or "(trecho indisponível)",
                language=None,
                wrap_lines=True,
                height=180,
            )


def _generator_tab(store, identity):
    st.subheader("Teste de Geração — Processo Histórico")
    st.caption(
        "Primeiro motor experimental para Prestação de Contas Anual, "
        "com revisão humana obrigatória."
    )
    service = _service(store, identity)
    if service is None:
        return
    generator = MinutasGenerationService(service)
    try:
        targets = generator.targets()
    except Exception as exc:
        _error(exc)
        return
    if not targets:
        empty_state(
            "Nenhum processo classificado nesta especialidade está disponível. "
            "Confira o acervo e as pendências de classificação."
        )
        return

    st.text_input("Procuradora", value=PROCURADORA, disabled=True)
    target = st.selectbox(
        "Processo histórico",
        targets,
        format_func=lambda item: (
            f"{item['numero_processo']} · {item['subtipo']} · "
            f"Exercício {item['exercicio']}"
        ),
        key="minutas_generation_target",
    )
    try:
        summary = generator.source_summary(target["id"])
    except Exception as exc:
        _error(exc)
        return

    with st.container(border=True):
        st.markdown("**Fontes disponíveis para esta geração**")
        st.caption(
            f"Processo-alvo: {summary['fontes_alvo_previstas']} documento(s) "
            f"instrutório(s) · Referências históricas processadas: "
            f"{summary['referencias_historicas_disponiveis']}"
        )
        st.success(
            "Barreira de contaminação ativa: o parecer definitivo do processo-alvo, "
            "seus hashes e duplicatas integrais foram excluídos no backend."
        )
        if summary["issues"]:
            st.warning(
                "Confira ou reprocesse antes de gerar: "
                + "; ".join(summary["issues"])
                + "."
            )

    analysis_key = f"minutas_generation_analysis_{target['id']}"
    result_key = f"minutas_generation_result_{target['id']}"
    docx_cache_key = f"minutas_generation_docx_cache_{target['id']}"
    analysis = st.session_state.get(analysis_key)
    result = st.session_state.get(result_key)
    sources_changed = any(
        item and item.get("fontes_versao") != summary["fontes_versao"]
        for item in (analysis, result)
    )
    if sources_changed or (result and not analysis):
        st.session_state.pop(analysis_key, None)
        _clear_generated_result(result_key, docx_cache_key)
        analysis = None
        result = None
        st.info(
            "As fontes foram alteradas. A "
            "análise e a minuta anteriores foram retiradas desta sessão."
        )

    if analysis and analysis.get("validation_policy_version") != (
        VALIDATION_POLICY_VERSION
    ):
        _clear_generated_result(result_key, docx_cache_key)
        result = None
        try:
            analysis = generator.migrate_validation_policy(target["id"], analysis)
        except Exception as exc:
            st.session_state.pop(analysis_key, None)
            analysis = None
            _error(exc)
        else:
            st.session_state[analysis_key] = analysis
            st.info(
                "A análise anterior foi reclassificada pela política atual, sem "
                "nova chamada ao Gemini. As conferências documentais passaram a "
                "ser advertências não bloqueantes."
            )

    recovery_version = (
        (analysis.get("recuperacao_juridica") or {}).get("versao")
        if analysis
        else None
    )
    rag_stale = bool(analysis) and (
        analysis.get("rag_recovery_version") != RAG_RECOVERY_VERSION
        or recovery_version != RAG_RECOVERY_VERSION
    )
    if rag_stale:
        st.caption(
            "A recuperação jurídica será atualizada somente se você gerar com "
            "referências jurídicas; navegar nesta tela não executa nova pesquisa."
        )

    section_label("Etapa 1 — Analisar processo")
    if st.button(
        "Analisar fundamentos",
        type="primary",
        icon=":material/fact_check:",
        key=f"minutas_analysis_submit_{target['id']}",
        disabled=bool(summary["issues"]),
    ):
        st.session_state.pop(analysis_key, None)
        _clear_generated_result(result_key, docx_cache_key)
        try:
            with st.spinner("Analisando fatos, defesa e conclusões da Auditoria..."):
                st.session_state[analysis_key] = generator.analyze(target["id"])
        except Exception as exc:
            _error(exc)
    analysis = st.session_state.get(analysis_key)
    if analysis:
        show_details = st.toggle(
            "Mostrar análise detalhada e precedentes",
            value=False,
            key=f"minutas_generation_details_{target['id']}",
        )
        _analysis_panel(analysis, show_details=show_details)
        if show_details:
            _legal_references_panel(analysis)
        show_diagnostics = st.toggle(
            "Mostrar diagnóstico técnico",
            value=False,
            key=f"minutas_generation_diagnostics_{target['id']}",
        )
        if show_diagnostics:
            try:
                diagnostics = generator.pilot_diagnostics(target["id"], analysis)
            except Exception as exc:
                _error(exc)
            else:
                _technical_diagnostics_panel(
                    diagnostics,
                    target["id"],
                    generator=generator,
                    analysis=analysis,
                    analysis_key=analysis_key,
                    result_key=result_key,
                    docx_cache_key=docx_cache_key,
                )

    section_label("Etapa 2 — Gerar minuta")
    use_rag = st.checkbox(
        "Usar referências jurídicas recuperadas nesta geração",
        value=True,
        key=f"minutas_generation_use_rag_{target['id']}",
        on_change=_clear_generated_result,
        args=(result_key, docx_cache_key),
        help=(
            "Desative para comparar com o comportamento anterior. A análise "
            "probatória e os demais controles permanecem iguais."
        ),
    )
    analysis_ready = bool(
        analysis
        and isinstance(analysis.get("analise"), dict)
    )
    if analysis and (analysis.get("pendencias_conferencia") or ()):
        st.caption(
            "A conferência assistida é facultativa; as advertências permanecerão "
            "registradas para a revisão do Assessor."
        )
    if st.button(
        "Gerar minuta",
        type="primary",
        icon=":material/auto_awesome:",
        key=f"minutas_generation_submit_{target['id']}",
        disabled=not analysis_ready or bool(summary["issues"]),
    ):
        _clear_generated_result(result_key, docx_cache_key)
        generation_analysis = analysis
        if use_rag and rag_stale:
            try:
                with st.spinner("Atualizando referências jurídicas..."):
                    generation_analysis = generator.refresh_legal_recovery(
                        target["id"], analysis
                    )
            except Exception as exc:
                generation_analysis = None
                _error(exc)
                st.warning(
                    "A pesquisa jurídica não foi repetida automaticamente. "
                    "Tente gerar novamente quando o serviço estiver disponível "
                    "ou desative o uso de referências jurídicas."
                )
            else:
                st.session_state[analysis_key] = generation_analysis
                analysis = generation_analysis
        if generation_analysis is not None:
            try:
                with st.spinner(
                    "Redigindo a minuta com os pontos de revisão sinalizados..."
                ):
                    st.session_state[result_key] = generator.generate(
                        target["id"], generation_analysis, use_rag=use_rag
                    )
            except Exception as exc:
                _error(exc)

    result = st.session_state.get(result_key)
    if not result:
        return
    st.warning(
        "Documento experimental sujeito à revisão humana — "
        "sem caráter de manifestação oficial."
    )
    section_label("Minuta gerada")
    quality_warnings = result.get("advertencias_qualidade") or ()
    if quality_warnings:
        st.info("Geração concluída com pontos disponíveis para conferência facultativa.")
    else:
        st.success(
            "Geração concluída sem advertências determinísticas de insuficiência "
            "jurídica. A revisão humana permanece obrigatória."
        )
    _generated_review_panel(result)
    with st.container(border=True):
        st.markdown(result["texto"])
    with st.expander("Copiar texto da minuta"):
        st.code(
            result["texto"],
            language=None,
            wrap_lines=True,
            height=320,
        )
    try:
        word = _cached_docx(
            service,
            generator,
            target["id"],
            analysis,
            result,
            docx_cache_key,
        )
    except Exception as exc:
        _error(exc)
    else:
        st.download_button(
            "Baixar minuta em Word",
            data=word.content,
            file_name=word.name,
            mime=word.mime,
            key=f"minutas_generation_docx_{target['id']}",
            icon=":material/download:",
            on_click="ignore",
        )
    with st.expander("Validação e fontes utilizadas"):
        details = [
            f"Modelo: {result.get('modelo') or '—'}",
            f"Contexto: {result.get('contexto_bytes', 0):,} bytes".replace(",", "."),
            f"Duração: {result.get('duracao_ms', 0) / 1000:.1f} s",
            f"Documentos do processo-alvo: {result.get('fontes_alvo', 0)}",
            f"Pareceres históricos: {result.get('referencias_historicas', 0)}",
            "Recuperação jurídica: "
            + ("ativada" if result.get("rag_ativo") else "desativada"),
            f"Trechos recuperados: "
            f"{result.get('referencias_juridicas_recuperadas', 0)} · "
            f"enviados ao Gemini: {result.get('referencias_juridicas_enviadas', 0)}",
            "Fontes efetivamente utilizadas: "
            + "; ".join(result.get("fontes_utilizadas") or ()),
        ]
        if result.get("fontes_juridicas_enviadas"):
            details.append(
                "Referências jurídicas enviadas: "
                + "; ".join(result["fontes_juridicas_enviadas"])
            )
        if result.get("omitidas_por_limite"):
            details.append(
                "Fontes integrais omitidas por limite: "
                + "; ".join(result["omitidas_por_limite"])
            )
        if result.get("tokens_total") is not None:
            details.append(
                "Tokens informados pelo provedor: "
                f"{result.get('tokens_entrada', 0)} entrada · "
                f"{result.get('tokens_saida', 0)} saída · "
                f"{result['tokens_total']} total"
            )
        st.caption("\n\n".join(details))


def _search_tab(store, identity):
    st.subheader("Pesquisa Jurídica")
    st.caption(
        "Localize trechos de pareceres definitivos históricos. Os resultados são "
        "passagens documentais, não entendimentos consolidados."
    )
    library = _service(store, identity)
    if library is None:
        return
    search_service = MinutasSearchService(library)
    with st.form("minutas_legal_search_form"):
        query = st.text_input(
            "Pesquisar pareceres",
            placeholder="Ex.: contribuição previdenciária, art. 37, prescrição",
        )
        filters = st.columns(4)
        with filters[0]:
            st.text_input("Procuradora", value=PROCURADORA, disabled=True)
        with filters[1]:
            from services.minutas_specialties import scope, CLASSES
            selected = scope(library.specialty_id)
            st.text_input("Classe / grupo", value=CLASSES.get(selected["grupo"], "Temas na classe efetiva"), disabled=True)
        with filters[2]:
            year = st.text_input("Exercício", placeholder="Todos")
        with filters[3]:
            subtype = st.selectbox("Subtipo", ("Todos", *SUBTIPOS))
        submitted = st.form_submit_button(
            "Pesquisar", type="primary", icon=":material/search:"
        )

    def execute(page):
        criteria = {
            "query": query,
            "year": year or None,
            "subtype": None if subtype == "Todos" else subtype,
        }
        try:
            with st.spinner("Pesquisando pareceres processados..."):
                result = search_service.search(**criteria, page=page)
        except Exception as exc:
            _error(exc)
            return
        st.session_state["minutas_legal_search"] = {
            "criteria": criteria,
            "result": result,
        }

    if submitted:
        execute(1)

    stored = st.session_state.get("minutas_legal_search")
    if not stored:
        return
    result = stored["result"]
    results = result["resultados"]
    if not results:
        empty_state("Nenhum trecho de parecer definitivo corresponde à pesquisa.")
        return

    st.caption(
        f"Página {result['pagina']} · resultados ordenados por relevância textual"
    )
    for index, item in enumerate(results):
        location = (
            f"Página {item['pagina']}"
            if item.get("pagina") is not None
            else "Localização sem número de página"
        )
        with st.container(border=True):
            st.markdown(f"**Processo {item['processo']}** · {item['subtipo']}")
            st.caption(
                f"{item['procuradora']} · Exercício {item['exercicio']} · "
                f"{item['documento']} · {location}"
            )
            st.write(item["trecho"])
            st.caption(
                item["criterio_recuperacao"]
                + " · Trecho de parecer histórico; requer leitura no contexto."
            )
            with st.expander("Consultar mais contexto"):
                st.code(item["contexto"], language=None, wrap_lines=True, height=260)
            download_key = (
                f"minutas_search_download_{item['referencia_id']}_{index}"
            )
            if prepared := st.session_state.get(download_key):
                st.download_button(
                    "Baixar parecer original",
                    data=prepared["content"],
                    file_name=prepared["name"],
                    mime=prepared["mime"],
                    key=download_key + "_button",
                    icon=":material/download:",
                )
            elif st.button(
                "Preparar parecer original",
                key=download_key + "_prepare",
                icon=":material/description:",
            ):
                try:
                    document = search_service.download_opinion(item["referencia_id"], item["arquivo_path"])
                    st.session_state[download_key] = {
                        "name": document.name,
                        "mime": document.mime,
                        "content": document.content,
                    }
                    st.rerun()
                except Exception as exc:
                    _error(exc)

    navigation = st.columns(2)
    if navigation[0].button(
        "Anterior",
        disabled=not result["tem_anterior"],
        key="minutas_search_previous",
    ):
        criteria = stored["criteria"]
        query, year, subtype = (
            criteria["query"],
            criteria["year"] or "",
            criteria["subtype"] or "Todos",
        )
        execute(result["pagina"] - 1)
        st.rerun()
    if navigation[1].button(
        "Próxima",
        disabled=not result["tem_proxima"],
        key="minutas_search_next",
    ):
        criteria = stored["criteria"]
        query, year, subtype = (
            criteria["query"],
            criteria["year"] or "",
            criteria["subtype"] or "Todos",
        )
        execute(result["pagina"] + 1)
        st.rerun()


def render(store, identity):
    started = perf_counter()
    st.title("Gerador de Minutas de Pareceres — Projeto-Piloto")
    render_html(badge("AMBIENTE EXPERIMENTAL", "warning"))
    st.caption("Projeto-piloto privado do MPC-PB · Procuradora Elvira Samara")
    from services.minutas_specialties_ui import selection, synchronize_scope, acervo
    from services.minutas_specialties import RPPS
    administrative_library = _service(store, identity, administrative=True)
    if administrative_library is None:
        return
    try:
        profile = selection(administrative_library)
        if profile is None:
            return
        library = _service(store, identity)
        if library is None:
            return
        synchronize_scope(library, identity)
    except Exception:
        st.warning("Especialidades indisponíveis. Confira a implantação de database/minutas_especialidades.sql e o catálogo; o acesso especializado permanece bloqueado.")
        return
    sections = (
        "Acervo",
        "Pesquisa Jurídica",
        "Gerar Minuta",
    )
    if st.session_state.get("minutas_active_view") not in sections:
        st.session_state["minutas_active_view"] = sections[0]
    active = st.radio(
        "Área do módulo",
        sections,
        horizontal=True,
        label_visibility="collapsed",
        key="minutas_active_view",
    )
    try:
        if active == sections[0]:
            acervo(library, administrative_library, profile)
        elif active == sections[1]:
            _search_tab(store, identity)
        elif profile["id"] == RPPS and profile["motor"] == "pca-rpps-v1":
            _generator_tab(store, identity)
        else:
            st.info("Geração especializada ainda não habilitada. Alimente e pesquise o acervo desta especialidade.")
    except Exception as exc:
        _error(exc)
    LOGGER.info(
        "MINUTAS_PERF etapa=interface duracao_ms=%d area=%s",
        round((perf_counter() - started) * 1000),
        sections.index(active) + 1,
    )
