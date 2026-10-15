"""Navegação e administração por especialidade, sem IA implícita."""

import json
from datetime import date

import streamlit as st

from services.minutas_acervo import MinutasAcervoService
from services.minutas_extraction import MinutasExtractionService
from services.minutas_specialties import (
    CLASSES, RPPS, STATES, fingerprint, instruction_generation_allowed, scope,
)
from services.ui_theme import badges, render_record


def clear_specialty_session():
    """Retorna à entrada neutra, preservando somente dados já persistidos."""
    transient_prefixes = (
        "minutas_generation_analysis_", "minutas_generation_result_",
        "minutas_generation_docx_cache_", "minutas_search_download_",
        "minutas_result_", "minutas_download_", "minutas_complement_download_",
        "minutas_instrucao_", "minutas_upload_preview_", "minutas_uploads_",
        "minutas_batch_",
    )
    transient_keys = {
        "minutas_specialty", "minutas_class_group", "minutas_scope_token",
        "minutas_legal_search", "minutas_active_panel", "minutas_open_reference",
        "minutas_generation_target", "minutas_generation_mode", "minutas_active_view",
        "minutas_initial_administration",
    }
    for key in list(st.session_state):
        if key in transient_keys or key.startswith(transient_prefixes):
            del st.session_state[key]


def selection(library):
    """Catálogo pequeno; não abre PDFs, extrações nem consulta o RAG."""
    admin = MinutasAcervoService(library)
    catalog = admin.catalog()
    if not catalog:
        st.warning("Catálogo não provisionado. Execute o roteiro de implantação com backup.")
        return None
    st.caption("Procuradora: Elvira Samara Pereira de Oliveira")
    groups = list(dict.fromkeys(item["grupo"] for item in catalog))
    groups.sort(key=lambda group: (group != "PCA", group))
    group = st.selectbox("Classe processual / grupo temático", groups, index=None,
        placeholder="Selecione a classe ou grupo",
        format_func=lambda value: CLASSES.get(value, "Temas na classe efetiva do processo"), key="minutas_class_group")
    if not group:
        st.info("Escolha a classe processual e, em seguida, a especialidade para acessar o Acervo, a pesquisa e o gerador.")
        return None
    available = {item["id"]: item for item in catalog if item["grupo"] == group}
    options = sorted(available, key=lambda value: (value != RPPS, available[value]["nome"]))
    selected = st.selectbox("Especialidade", options, index=None,
        placeholder="Selecione a especialidade",
        format_func=lambda value: available[value]["nome"], key="minutas_specialty")
    if not selected:
        return None
    profile = available[selected]
    st.caption(f"{profile['estado']} · Configuração {profile['config_versao']}")
    if not instruction_generation_allowed(profile):
        st.info("Acervo e pesquisa disponíveis. Esta especialidade ainda não possui motor especializado habilitado.")
    return profile


def synchronize_scope(library, identity):
    """Descarta somente derivados transitórios; mantém seleção e filtros por acervo."""
    email = identity.get("email")
    if st.session_state.get("minutas_scope_identity") != email:
        for key in list(st.session_state):
            if key.startswith(("minutas_upload_preview_", "minutas_uploads_", "minutas_batch_")) or key == "minutas_migration_plan":
                del st.session_state[key]
        st.session_state["minutas_scope_identity"] = email
    token = fingerprint([email, library.specialty_id, library.repository.scope_revision()])
    if st.session_state.get("minutas_scope_token") != token:
        prefixes = (
            "minutas_generation_analysis_", "minutas_generation_result_",
            "minutas_generation_docx_cache_", "minutas_search_download_",
            "minutas_result_", "minutas_download_", "minutas_complement_download_",
            "minutas_instrucao_",
        )
        drop = {
            "minutas_legal_search", "minutas_active_panel", "minutas_open_reference",
            "minutas_generation_target", "minutas_generation_mode",
        }
        for key in list(st.session_state):
            if key.startswith(prefixes) or key in drop:
                del st.session_state[key]
        st.session_state["minutas_scope_token"] = token


def _classification(admin, record, catalog):
    identifier = str(record["id"])
    current = admin.repository.classification(identifier) or {}
    if current.get("motivo"):
        st.caption(current["motivo"])
    if current.get("evidencia_json"):
        st.write(json.loads(current["evidencia_json"]))
    options = {item["id"]: item["nome"] for item in catalog}
    with st.form("minutas_classify_" + identifier):
        initial = current.get("especialidade_id") or st.session_state["minutas_specialty"]
        selected = st.selectbox("Classificação confirmada", list(options),
            index=list(options).index(initial), format_func=options.get)
        initial_class = current.get("classe") or scope(initial)["grupo"]
        formal = st.selectbox("Classe efetiva", list(CLASSES), format_func=CLASSES.get,
                              index=list(CLASSES).index(initial_class) if initial_class in CLASSES else 0)
        entity = st.text_input("Entidade fiscalizada", value=current.get("entidade", ""))
        themes = st.text_input("Temas (separados por ponto e vírgula)", value="; ".join(json.loads(current.get("temas_json", "[]"))))
        reason = st.text_area("Fundamento documental da classificação", value="")
        save = st.form_submit_button("Confirmar classificação")
    if save:
        admin.classify(identifier, selected, formal, entity, reason,
                       [term.strip() for term in themes.split(";") if term.strip()])
        st.rerun()
    if current.get("estado") == "CLASSIFICADO":
        st.caption("O vínculo adicional inclui o processo e seus documentos no acervo escolhido. Confirme a pertinência jurídica de todo esse conjunto.")
        with st.form("minutas_link_" + identifier):
            secondary = st.selectbox("Vínculo adicional autorizado", list(options), format_func=options.get)
            justification = st.text_input("Justificativa da pertinência nesta especialidade")
            link = st.form_submit_button("Adicionar vínculo sem copiar arquivos")
        if link:
            admin.link(identifier, secondary, justification)
            st.rerun()
    events = admin.repository.events(identifier)
    reversible = {str(event["id"]): event for event in events if event["acao"] == "CLASSIFICAR"}
    if reversible:
        event = st.selectbox("Histórico de classificação", list(reversible),
            format_func=lambda key: f"{reversible[key]['criado_em']} · {reversible[key]['criado_por']}", key="minutas_event_" + identifier)
        if st.button("Restaurar classificação anterior", key="minutas_restore_" + identifier):
            admin.restore(event)
            st.rerun()


def _documents(library, record):
    identifier = str(record["id"])
    extractor = MinutasExtractionService(library)
    documents = extractor._documents(record, include_models=True)
    if not documents:
        st.info("Nenhum arquivo vinculado.")
        return
    choices = {item["path"]: item for item in documents}
    path = st.selectbox("Documento", list(choices), format_func=lambda key: f"{choices[key]['tipo']} · {choices[key]['nome']}",
                        key="minutas_document_" + identifier)
    item = choices[path]
    st.caption(f"{item['mime']} · {item['tamanho']} bytes · finalidade: {item['papel']}")
    # Bytes apenas do documento escolhido, apenas após ação explícita; não ficam em cache de sessão.
    if st.button("Preparar download", key="minutas_file_" + identifier):
        downloaded = library.download_path(identifier, path)
        st.download_button("Baixar documento", downloaded.content, downloaded.name, downloaded.mime,
                           key="minutas_download_current_" + identifier)
    if item["papel"] != "modelo":
        if st.button("Consultar extração", key="minutas_text_" + identifier):
            st.text(extractor.view(identifier, path)["texto_integral"])
        if st.button("Reprocessar este documento (local)", key="minutas_reindex_" + identifier):
            result = extractor.reprocess_document(identifier, path)
            st.write([{key: row.get(key) for key in ("arquivo_nome", "status", "erro")} for row in result])


def batch_processing_summary(details, role):
    """Consolida o lote para a interface; não altera cadastro nem extração."""
    cadastrados = sum(1 for item in details if item.get("estado") == "CADASTRADO")
    processadas = sum(1 for item in details if item.get("extracao") == "PROCESSADO")
    atencao = sum(1 for item in details if item.get("extracao") and item.get("extracao") != "PROCESSADO")
    duplicados = sum(1 for item in details if item.get("estado") == "DUPLICADO")
    falhas = sum(1 for item in details if item.get("falha"))
    complete = bool(details) and cadastrados == len(details) and not duplicados and not atencao and not falhas
    if complete and role != "modelo":
        complete = processadas == len(details)
    return {
        "cadastrados": cadastrados,
        "processadas": processadas,
        "atencao": atencao,
        "duplicados": duplicados,
        "falhas": falhas,
        "complete": complete,
    }


def _batch_counts_line(summary):
    line = (
        f"{summary['cadastrados']} documento(s) cadastrado(s) · "
        f"{summary['processadas']} extração(ões) PROCESSADA(S) · "
        f"{summary['falhas']} falha(s)"
    )
    if summary.get("duplicados") or summary.get("atencao"):
        line += (
            f" · {summary['duplicados']} duplicidade(s) · "
            f"{summary['atencao']} extração(ões) em ATENÇÃO"
        )
    return line + "."


def _batch_detail_line(item):
    if item.get("falha"):
        return f"{item['nome']}: falha — {item['falha']}"
    parts = [item["nome"], item.get("estado") or ""]
    if item.get("extracao"):
        parts.append("extração " + item["extracao"])
        if item.get("extracao_erro") and item["extracao"] != "PROCESSADO":
            parts.append(item["extracao_erro"])
    return " · ".join(part for part in parts if part)


def _render_batch_result(result):
    if not result:
        return
    summary = result["summary"]
    if summary["complete"]:
        render_record(
            "Cadastro e processamento concluídos!",
            badges_html=badges(
                (f"{summary['cadastrados']} cadastrado(s)", "success"),
                (f"{summary['processadas']} PROCESSADA(S)", "success"),
            ),
            secondary=_batch_counts_line(summary),
            accent="success",
            boxed=True,
        )
    elif summary["cadastrados"] or summary["processadas"]:
        render_record(
            "Cadastro e processamento concluídos com ressalvas",
            badges_html=badges(
                (f"{summary['cadastrados']} cadastrado(s)", "warning"),
                (f"{summary['falhas']} falha(s)", "danger" if summary["falhas"] else "warning"),
            ),
            secondary=_batch_counts_line(summary),
            accent="warning",
            boxed=True,
        )
    else:
        render_record(
            "Não foi possível concluir o cadastro",
            badges_html=badges((f"{summary['falhas']} falha(s)", "danger")),
            secondary=_batch_counts_line(summary),
            accent="danger",
            boxed=True,
        )
    with st.expander("Ver detalhes do processamento"):
        for item in result["details"]:
            st.caption(_batch_detail_line(item))


def _uploads(admin, library, profile):
    st.caption("Extração local. Nenhuma chamada Gemini será feita por este cadastro.")
    uploads = st.file_uploader("Adicionar documentos", type=["pdf", "docx"], accept_multiple_files=True,
                               key="minutas_uploads_" + profile["id"])
    role = st.selectbox("Finalidade do lote", ("parecer", "auditoria", "complementar"),
        format_func=lambda value: {"parecer": "Pareceres definitivos", "auditoria": "Relatórios da Auditoria",
        "complementar": "Instrução complementar"}[value],
        key="minutas_upload_role_" + profile["id"])
    result_key = "minutas_batch_result_" + profile["id"]
    preview_key = "minutas_upload_preview_" + profile["id"]
    if not uploads:
        st.session_state.pop(preview_key, None)
        _render_batch_result(st.session_state.get(result_key))
        return
    signature = fingerprint([(u.name, u.size, getattr(u, "file_id", "")) for u in uploads] + [profile["id"], role])
    stored = st.session_state.get(result_key)
    if stored and stored.get("signature") != signature:
        st.session_state.pop(result_key, None)
        stored = None
    saved = st.session_state.get(preview_key, {})
    if st.button("Identificar dados localmente", key="minutas_preview_" + profile["id"]):
        previews = []
        for upload in uploads:
            try:
                item = admin.preview_upload(upload.name, upload.getvalue(), role, specialty=profile["id"])
                previews.append(item)
            except Exception as exc:
                previews.append({"nome": upload.name, "erro": str(exc)})
        saved = {"signature": signature, "previews": previews}
        st.session_state[preview_key] = saved
        for index, item in enumerate(previews):
            link_key = f"minutas_batch_link_{signature}_{index}"
            default = "" if item.get("conflito") else str(item.get("referencia_id") or "")
            st.session_state[link_key] = default
            st.session_state[f"minutas_batch_process_{signature}_{index}"] = item.get("processo") or ""
            st.session_state[f"minutas_batch_origin_{signature}_{index}"] = item.get("origem") or ""
            year_key = f"minutas_batch_year_{signature}_{index}"
            if item.get("exercicio"):
                st.session_state[year_key] = int(item["exercicio"])
            else:
                st.session_state.pop(year_key, None)
    if saved.get("signature") != signature:
        st.info("Identifique os dados antes de confirmar o lote.")
        return
    linkable = role in ("auditoria", "complementar")
    existing_by_id = {str(row["id"]): row for row in admin.repository.list_records(profile["id"], limit=100)} if linkable else {}
    prepared = []
    for index, item in enumerate(saved["previews"]):
        st.write(item["nome"])
        if item.get("erro"):
            st.warning(item["erro"])
            prepared.append({"item": item, "chosen": None})
            continue
        for warning in item.get("advertencias") or []:
            st.warning(warning)
        linked = ""
        if existing_by_id:
            options = [""] + list(existing_by_id)
            link_key = f"minutas_batch_link_{signature}_{index}"
            if st.session_state.get(link_key) not in options:
                st.session_state[link_key] = ""
            linked = st.selectbox(
                "Vincular a processo já cadastrado",
                options,
                format_func=lambda key, rows=existing_by_id: (
                    "Informar manualmente"
                    if not key
                    else f"{rows[key]['numero_processo']} · exercício {rows[key].get('exercicio') or '—'} · {rows[key].get('origem') or 'origem a conferir'}"
                ),
                key=link_key,
            )
        chosen = existing_by_id.get(linked)
        if chosen:
            st.caption("Processo identificado no acervo — relatório será adicionado ao processo existente.")
            render_record(
                str(chosen.get("numero_processo") or "Processo"),
                secondary=chosen.get("origem") or "Origem a conferir",
                meta=f"Exercício das contas {chosen.get('exercicio') or '—'} · {CLASSES.get(chosen.get('classe') or '', chosen.get('classe') or 'Classe a conferir')}",
                accent="brand",
                boxed=True,
            )
        else:
            st.caption(item.get("sugestao") or "Preencha processo, exercício e origem.")
            if item.get("fonte") == "nome do arquivo":
                st.caption("A origem desta identificação é o nome do arquivo.")
            if item.get("classe"):
                st.caption("Classe processual: " + CLASSES.get(item["classe"], item["classe"]))
        prepared.append({"item": item, "chosen": chosen})
    with st.form("minutas_batch_" + profile["id"]):
        formal = profile["grupo"]
        if formal == "TEMATICO":
            formal = st.selectbox("Classe processual efetiva do lote", list(CLASSES), format_func=CLASSES.get)
        values = []
        for index, row in enumerate(prepared):
            item, chosen = row["item"], row["chosen"]
            if item.get("erro"):
                values.append(None)
                continue
            if chosen:
                values.append({"numero_processo": chosen.get("numero_processo") or "", "exercicio": chosen.get("exercicio"),
                    "origem": chosen.get("origem") or "", "classe": chosen.get("classe") or formal,
                    "referencia_id": str(chosen["id"])})
                continue
            st.write(item["nome"])
            fields = st.columns(3)
            process_key = f"minutas_batch_process_{signature}_{index}"
            year_key = f"minutas_batch_year_{signature}_{index}"
            origin_key = f"minutas_batch_origin_{signature}_{index}"
            process = fields[0].text_input("Processo TC", key=process_key)
            if year_key in st.session_state:
                year = fields[1].number_input("Exercício das contas", 1900, date.today().year + 1, step=1, key=year_key)
            else:
                year = fields[1].number_input("Exercício das contas", min_value=1900, max_value=date.today().year + 1, value=None, step=1, key=year_key)
            entity = fields[2].text_input("Origem", key=origin_key)
            values.append({"numero_processo": process, "exercicio": year, "origem": entity,
                "classe": item.get("classe") or formal, "referencia_id": ""})
        reason = st.text_input("Fundamento / conferência da classificação do lote")
        versions = st.checkbox("Preservar como nova versão quando o nome já existir")
        confirmed = st.form_submit_button("Confirmar cadastro e processamento local", type="primary")
    if confirmed:
        progress = st.progress(0)
        details = []
        for index, (upload, metadata) in enumerate(zip(uploads, values)):
            if not metadata:
                details.append({"nome": upload.name, "falha": "Identificação inválida."})
                progress.progress((index + 1) / len(uploads))
                continue
            metadata["fundamento"] = reason
            try:
                result = admin.add_file(profile["id"], metadata, (upload.name, upload.getvalue()), role=role, confirm_version=versions)
                item = {"nome": upload.name, "estado": result["estado"]}
                if result["estado"] == "CADASTRADO" and role != "modelo":
                    extracted = MinutasExtractionService(library).reprocess_document(result["referencia_id"], result["path"])
                    pending = [row for row in extracted if row.get("status") != "PROCESSADO"]
                    if extracted and not pending:
                        item["extracao"] = "PROCESSADO"
                    else:
                        row = pending[0] if pending else {}
                        item["extracao"] = row.get("status") or "ATENCAO"
                        item["extracao_erro"] = row.get("erro") or ""
                details.append(item)
            except Exception as exc:
                details.append({"nome": upload.name, "falha": str(exc)})
            progress.progress((index + 1) / len(uploads))
        stored = {"signature": signature, "summary": batch_processing_summary(details, role), "details": details}
        st.session_state[result_key] = stored
    _render_batch_result(st.session_state.get(result_key))


def _record_summary(record):
    st.markdown(f"**Processo {record.get('numero_processo') or '—'}**")
    st.caption(
        f"Exercício {record.get('exercicio') or '—'} · "
        f"{record.get('origem') or 'Origem a conferir'} · "
        f"{record.get('classe') or 'Classe a conferir'} · "
        f"{record.get('classificacao_estado') or 'Sem classificação'}"
    )


def _visual_model(library, profile):
    from services.minutas_modelos import MinutasModeloVisualService

    service = MinutasModeloVisualService(library)
    st.caption(
        "Modelo de formatação desta especialidade. Não se vincula a processo, "
        "não entra no acervo jurídico e não é usado como precedente."
    )
    if not service.enabled():
        st.info(
            "O cadastro ainda não está disponível neste banco. A exportação continua "
            "no modelo institucional padrão. A migração manual está em "
            "database/minutas_modelos_visuais.sql."
        )
        return
    upload = st.file_uploader(
        "Substituir modelo visual",
        type=["docx"],
        key="minutas_modelo_upload_" + profile["id"],
    )
    if st.button("Salvar modelo visual", key="minutas_modelo_save_" + profile["id"], disabled=upload is None):
        try:
            result = service.save(profile["id"], (upload.name, upload.getvalue()))
        except Exception as exc:
            st.warning(str(exc))
        else:
            st.caption(f"Modelo visual {result['estado'].lower()} · versão {result['versao']}.")
    current = service.active(profile["id"])
    if current:
        render_record(
            current["nome"],
            secondary=f"Versão {current['versao']} · modelo ativo",
            meta="A minuta usa a formatação deste arquivo. O texto dele não é copiado.",
            accent="brand",
            boxed=True,
        )
    else:
        st.caption("Nenhum modelo visual configurado. A exportação usa o modelo institucional padrão.")
    previous = [item for item in service.versions(profile["id"]) if not int(item["ativo"])]
    if previous:
        st.caption("Versões anteriores preservadas: " + ", ".join(
            f"{item['nome']} (versão {item['versao']})" for item in previous
        ))


def _back_to_acervo_consult():
    st.session_state["minutas_acervo_mode"] = "Consultar"


def acervo(library, administrative_library, profile):
    admin = MinutasAcervoService(administrative_library)
    admin.library._authorize()
    mode = st.radio(
        "Operação do acervo",
        ("Consultar", "Adicionar documentos", "Modelo visual", "Administração"),
        horizontal=True,
        key="minutas_acervo_mode",
    )
    if mode != "Consultar":
        st.button("← Voltar", key="minutas_acervo_back", on_click=_back_to_acervo_consult)
    if mode == "Modelo visual":
        _visual_model(library, profile)
        return
    if mode == "Adicionar documentos":
        if not admin.repository.separation_enabled():
            st.caption("A separação do acervo legado ainda não está aplicada. Não recadastre um número que já exista: o arquivo seria gravado no registro histórico.")
        _uploads(admin, library, profile)
        return
    if mode == "Administração":
        _administration(admin, profile)
        return
    query = st.text_input("Processo ou entidade", key="minutas_query_" + profile["id"])
    role = st.selectbox("Tipo documental", ("Todos", "parecer", "auditoria", "complementar", "modelo"))
    page_key = "minutas_page_" + fingerprint([profile["id"], query, role])
    page = st.number_input("Página", min_value=1, value=1, step=1, key=page_key)
    records = admin.repository.list_records(profile["id"], query=query,
                                           role=None if role == "Todos" else role, page=page)
    if not records:
        if query or role != "Todos" or page > 1:
            st.info("Nenhum processo encontrado com esses filtros.")
        else:
            st.info("Nenhum documento neste acervo. Em Adicionar documentos, cadastre o primeiro processo desta especialidade.")
        return
    options = {str(r["id"]): r for r in records}
    st.caption(f"{len(records)} processo(s) nesta página · até 25 por página")
    record_key = "minutas_record_" + page_key
    identifier = st.selectbox("Processo", list(options), index=None, placeholder="Selecione para abrir os detalhes",
        format_func=lambda key: f"{options[key]['numero_processo']} · {options[key].get('origem') or 'Origem a conferir'}",
        key=record_key)
    if identifier:
        st.button(
            "← Voltar",
            key="minutas_acervo_record_back",
            on_click=lambda key=record_key: st.session_state.pop(key, None),
        )
        record = library.get(identifier)
        _record_summary(options[identifier])
        detail = st.radio("Detalhes", ("Documentos", "Classificação"), horizontal=True, key="minutas_detail_" + identifier)
        if detail == "Documentos":
            _documents(library, record)
        else:
            _classification(admin, record, admin.catalog())


def _plan_summary(plan):
    counts = plan.get("contagens") or {}
    results = plan.get("resultados") or {}
    st.caption(
        f"Processos {counts.get('processos', 0)} · arquivos {counts.get('arquivos_vinculados', 0)} · "
        f"extrações {counts.get('extracoes', 0)}"
    )
    if results:
        st.caption(" · ".join(f"{key}: {value}" for key, value in sorted(results.items())))
    incomplete = counts.get("metadados_incompletos") or []
    if incomplete:
        st.caption(f"Metadados incompletos: {len(incomplete)}")


def _legacy_archive(admin, library):
    st.caption("Leitura do acervo anterior. Estes processos não entram na pesquisa, no RAG nem na geração.")
    records = admin.repository.list_records(legacy=True, limit=100)
    if not records:
        st.info("Nenhum processo legado encontrado.")
        return
    options = {str(row["id"]): row for row in records}
    st.caption(f"{len(records)} processo(s) legado(s)")
    identifier = st.selectbox("Processo legado", list(options), index=None,
        placeholder="Selecione para consultar os documentos",
        format_func=lambda key: f"{options[key]['numero_processo']} · exercício {options[key].get('exercicio') or '—'}",
        key="minutas_legacy_record")
    if identifier:
        st.button(
            "← Voltar",
            key="minutas_legacy_back",
            on_click=lambda: st.session_state.pop("minutas_legacy_record", None),
        )
        _record_summary(options[identifier])
        _documents(library, library.get(identifier))


def administration(library):
    """Acesso administrativo sem criar uma especialidade de trabalho implícita."""
    admin = MinutasAcervoService(library)
    admin.library._authorize()
    _administration(admin, None)


def _administration(admin, profile):
    operations = ["Painel de especialidades", "Inventário e reclassificação"]
    if profile is not None:
        operations.append("Evolução da especialidade")
    if admin.repository.separation_enabled():
        operations.append("Acervo legado")
    operation = st.selectbox("Administração", operations)
    if operation == "Painel de especialidades":
        data = admin.repository.dashboard()
        catalog = {row["id"]: row for row in admin.catalog()}
        rows = []
        for row in data["especialidades"]:
            item = catalog.get(row["id"], {})
            rows.append({"Especialidade": item.get("nome", row["id"]), "Estado": item.get("estado", ""),
                "Processos": row.get("processos", 0), "Pareceres": row.get("pareceres", 0),
                "Relatórios": row.get("relatorios", 0), "Indexados": row.get("indexados", 0)})
        st.dataframe(rows, hide_index=True)
        st.caption(f"Processos do acervo ativo sem classificação: {data['pendentes']}")
    elif operation == "Acervo legado":
        _legacy_archive(admin, admin.library)
    elif operation == "Inventário e reclassificação":
        st.warning("Operação administrativa explícita. Antes de aplicar, confira o inventário e comprove restauração do backup de banco e Storage.")
        if st.button("Simular reclassificação (sem alterações)"):
            st.session_state["minutas_migration_plan"] = admin.dry_run()
        plan = st.session_state.get("minutas_migration_plan")
        if plan:
            _plan_summary(plan)
            st.download_button("Baixar relatório da simulação", json.dumps(plan, ensure_ascii=False, indent=2), "minutas-dry-run.json", "application/json")
            backup = st.text_input("Identificador da evidência de backup e teste de restauração")
            verified = st.checkbox("Backup de banco e Storage restaurado e conferido em ambiente isolado")
            if st.button("Aplicar plano conferido", disabled=not (backup.strip() and verified)):
                result = admin.apply(plan, {"identificador": backup, "restauracao_verificada": verified})
                st.success(f"{result['alterados']} classificação(ões) aplicada(s); contagens reconciliadas.")
                del st.session_state["minutas_migration_plan"]
    else:
        with st.form("minutas_evolution_" + profile["id"]):
            state = st.selectbox("Estado", STATES, index=STATES.index(profile["estado"]))
            cases = st.number_input("Casos testados", 0, value=int(profile["casos_testados"]))
            pending = st.text_area("Pendências conhecidas", value=profile["pendencias"])
            results = st.text_area("Resultados dos testes comparativos", value=profile["resultados_testes"])
            human = st.checkbox("Testes e revisão humana concluídos")
            save = st.form_submit_button("Registrar evolução")
        if save:
            admin.update_state(profile["id"], {"estado": state, "casos_testados": cases, "pendencias": pending,
                "resultados_testes": results, "revisao_humana": human})
            st.rerun()
