"""Navegação e administração por especialidade, sem IA implícita."""

import json
from datetime import date

import streamlit as st

from services.minutas_acervo import MinutasAcervoService
from services.minutas_extraction import MinutasExtractionService
from services.minutas_specialties import CLASSES, RPPS, STATES, fingerprint, scope
from services.ui_theme import badges, render_record


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
    group = st.selectbox("Classe processual / grupo temático", groups,
        format_func=lambda value: CLASSES.get(value, "Temas na classe efetiva do processo"), key="minutas_class_group")
    available = {item["id"]: item for item in catalog if item["grupo"] == group}
    options = sorted(available, key=lambda value: (value != RPPS, available[value]["nome"]))
    if st.session_state.get("minutas_specialty") not in available:
        st.session_state["minutas_specialty"] = options[0]
    selected = st.selectbox("Especialidade", options,
        format_func=lambda value: available[value]["nome"], key="minutas_specialty")
    profile = available[selected]
    st.caption(f"{profile['estado']} · Configuração {profile['config_versao']}")
    if selected != RPPS or profile["motor"] != "pca-rpps-v1":
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
        prefixes = ("minutas_generation_analysis_", "minutas_generation_result_", "minutas_generation_docx_cache_", "minutas_search_download_",
                    "minutas_result_", "minutas_download_", "minutas_complement_download_")
        for key in list(st.session_state):
            if key.startswith(prefixes) or key in {"minutas_legal_search", "minutas_active_panel", "minutas_open_reference", "minutas_generation_target"}:
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
    role = st.selectbox("Finalidade do lote", ("parecer", "auditoria", "complementar", "modelo"),
        format_func=lambda value: {"parecer": "Pareceres definitivos", "auditoria": "Relatórios da Auditoria",
        "complementar": "Instrução complementar", "modelo": "Modelos visuais (fora do RAG)"}[value],
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
    if saved.get("signature") != signature:
        st.info("Identifique os dados antes de confirmar o lote.")
        return
    with st.form("minutas_batch_" + profile["id"]):
        formal = profile["grupo"]
        if formal == "TEMATICO":
            formal = st.selectbox("Classe processual efetiva do lote", list(CLASSES), format_func=CLASSES.get)
        values = []
        existing = []
        if role in ("auditoria", "complementar"):
            existing = admin.repository.list_records(profile["id"], limit=100)
            existing_by_id = {str(row["id"]): row for row in existing}
        else:
            existing_by_id = {}
        for index, item in enumerate(saved["previews"]):
            st.write(item["nome"])
            if item.get("erro"):
                st.warning(item["erro"])
            for warning in item.get("advertencias") or []:
                st.warning(warning)
            if item.get("recuperado"):
                st.caption("Processo já cadastrado — dados recuperados do acervo")
            else:
                st.caption("Identificação documental: " + item.get("sugestao", "Necessita conferência"))
            linked = ""
            if existing:
                options = [""] + list(existing_by_id)
                initial = str(item.get("referencia_id") or "")
                linked = st.selectbox(
                    "Processo já cadastrado na especialidade",
                    options,
                    index=options.index(initial) if initial in options else 0,
                    format_func=lambda key: (
                        "Usar a identificação acima"
                        if not key
                        else f"{existing_by_id[key]['numero_processo']} · exercício {existing_by_id[key].get('exercicio') or '—'} · {existing_by_id[key].get('origem') or 'origem a conferir'}"
                    ),
                    key=f"minutas_batch_link_{signature}_{index}",
                )
            fields = st.columns(3)
            process = fields[0].text_input("Processo TC", value=item.get("processo", ""), key=f"minutas_batch_process_{signature}_{index}")
            year = fields[1].number_input("Exercício das contas", 1900, date.today().year + 1, item.get("exercicio") or date.today().year,
                                         key=f"minutas_batch_year_{signature}_{index}")
            entity = fields[2].text_input("Origem", value=item.get("origem", ""), key=f"minutas_batch_origin_{signature}_{index}")
            if linked:
                chosen = existing_by_id[linked]
                process = chosen.get("numero_processo") or process
                year = chosen.get("exercicio") or year
                entity = chosen.get("origem") or entity
                formal_row = chosen.get("classe") or formal
            else:
                formal_row = item.get("classe") or formal
            values.append({"numero_processo": process, "exercicio": year, "origem": entity, "classe": formal_row})
        reason = st.text_input("Fundamento / conferência da classificação do lote")
        versions = st.checkbox("Preservar como nova versão quando o nome já existir")
        confirmed = st.form_submit_button("Confirmar cadastro e processamento local", type="primary")
    if confirmed:
        progress = st.progress(0)
        details = []
        for index, (upload, metadata) in enumerate(zip(uploads, values)):
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


def acervo(library, administrative_library, profile):
    admin = MinutasAcervoService(administrative_library)
    admin.library._authorize()
    mode = st.radio("Operação do acervo", ("Consultar", "Adicionar documentos", "Administração"),
                    horizontal=True, key="minutas_acervo_mode")
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
    identifier = st.selectbox("Processo", list(options), index=None, placeholder="Selecione para abrir os detalhes",
        format_func=lambda key: f"{options[key]['numero_processo']} · {options[key].get('origem') or 'Origem a conferir'}",
        key="minutas_record_" + page_key)
    if identifier:
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
        _record_summary(options[identifier])
        _documents(library, library.get(identifier))


def _administration(admin, profile):
    operations = ["Painel de especialidades", "Inventário e reclassificação", "Evolução da especialidade"]
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
