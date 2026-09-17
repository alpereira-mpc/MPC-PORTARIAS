"""Lazy Streamlit UI for substitution memoranda."""
from datetime import date
import hashlib
import streamlit as st
from services.access import require_permission
from services.branding import module_title
from services.ui_theme import badges, filter_mark, render_record, status_tone
from services.memorandos import (
    MIME_DOCX,
    MIME_PDF,
    MOTIVOS,
    NATUREZAS,
    SOURCE_SYSTEM,
    SOURCE_UPLOAD,
    TIPO_SUBSTITUICAO,
    cabinet_text,
    digest,
    display_sector,
    fingerprint,
    open_service,
    preview_is_valid,
    read_server_xlsx,
    safe_filename,
    signature_role,
    upload_fingerprint,
    validate_docx,
)

LABEL_AWAY = "Servidor afastado"
LABEL_REPLACEMENT = "Substituto(a)"
PLACEHOLDER_AWAY = "Selecione o servidor afastado"
PLACEHOLDER_REPLACEMENT = "Selecione o substituto(a)"
NAV_KEY = "memorandos_nav"
NAV_OVERVIEW = "Visão Geral"
NAV_NEW = "Novo Memorando"
NAV_HISTORY = "Histórico"
NAV_BASE = "Base de Servidores"
NAV_ONGOING_LEGACY = "Em andamento"


def _audit_memo(evento, acao, entidade_id=None, extra=None):
    from services.audit import registrar_evento

    registrar_evento(
        st.session_state.get("_mpc_store"),
        evento=evento,
        modulo="memorandos",
        acao=acao,
        entidade_tipo="memorando",
        entidade_id=entidade_id,
        detalhes=extra,
    )


def _nav_pages(principal):
    pages = [NAV_OVERVIEW, NAV_NEW, NAV_HISTORY]
    if principal.administrator:
        pages.append(NAV_BASE)
    return pages


def consume_pending_open_memorando(pages):
    pending = st.session_state.pop("pending_open_memorando", None)
    if not pending:
        return None
    if not isinstance(pending, dict):
        pending = {"id": pending}
    page = pending.get("page")
    if page == NAV_ONGOING_LEGACY:
        page = NAV_HISTORY
    if page in pages:
        st.session_state[NAV_KEY] = page
    elif pending.get("id"):
        st.session_state[NAV_KEY] = NAV_HISTORY
    source_id = pending.get("id")
    if source_id:
        st.session_state["pending_focus_memorando"] = source_id
    return pending


def _current_page(pages):
    consume_pending_open_memorando(pages)
    current = st.session_state.get(NAV_KEY)
    if current == NAV_ONGOING_LEGACY:
        st.session_state[NAV_KEY] = NAV_HISTORY
    elif current not in pages:
        st.session_state[NAV_KEY] = NAV_OVERVIEW
    return st.radio("Memorandos", pages, horizontal=True, key=NAV_KEY)


def _label(r):
    if not r:
        return ""
    return f"{r['nome']} — {r['matricula_original'] or 'sem matrícula'} — {r['setor']}"


def _reset_new_form():
    for key in list(st.session_state.keys()):
        if key in (NAV_KEY, "memorando_form_active"):
            continue
        if key == "memorando_stages" or str(key).startswith("memorando_"):
            st.session_state.pop(key, None)
    st.session_state["memorando_stages"] = 1
    st.session_state["memorando_away"] = None
    st.session_state["memorando_replacement"] = None


def _snapshot(row, prefix):
    if not row:
        return None
    known = {"masculino": "Masculino", "feminino": "Feminino", "Masculino": "Masculino", "Feminino": "Feminino"}.get(row.get("genero"))
    gender = known or st.selectbox("Gênero gramatical", ("Masculino", "Feminino"), key=prefix+"_gender")
    return {"servidor_id":row["id"],"nome":row["nome"],"matricula":row["matricula_original"],"genero":gender,
            "cargo":st.text_input("Cargo/função documental",row["cargo"],key=prefix+"_cargo"),
            "lotacao":st.text_input("Lotação documental",display_sector(row["setor"]),key=prefix+"_lotacao")}


def _pick(label, people, *, key, placeholder):
    by_id = {person["id"]: person for person in people}
    options = list(by_id)
    current = st.session_state.get(key)
    if current is not None and current not in by_id:
        st.session_state.pop(key, None)
    chosen = st.selectbox(
        label,
        options,
        index=None,
        placeholder=placeholder,
        format_func=lambda identifier: _label(by_id.get(identifier)) if identifier is not None else "",
        filter_mode="prefix",
        key=key,
    )
    if chosen is None:
        return None
    return by_id.get(chosen)


def _show_locked_snapshot(row):
    st.caption("Gênero gramatical: " + (row.get("genero") or "—"))
    st.caption("Cargo/função documental: " + (row.get("cargo") or "—"))
    st.caption("Lotação documental: " + (row.get("lotacao") or "—"))


def _chain(people):
    stages = st.session_state.setdefault("memorando_stages", 1)
    with st.expander("Etapa 1", expanded=True):
        st.markdown(f"**{LABEL_AWAY}**")
        away = _pick(LABEL_AWAY, people, key="memorando_away", placeholder=PLACEHOLDER_AWAY)
        left = _snapshot(away, "memorando_left_0")
        st.markdown(f"**{LABEL_REPLACEMENT}**")
        replacement = _pick(LABEL_REPLACEMENT, people, key="memorando_replacement", placeholder=PLACEHOLDER_REPLACEMENT)
        right = _snapshot(replacement, "memorando_right_0")
    same_person = bool(away and replacement and away["id"] == replacement["id"])
    if same_person:
        st.error("O servidor afastado e o substituto não podem ser a mesma pessoa.")
    complete = bool(left and right) and not same_person
    steps = []
    if complete:
        steps = [{"substituido": left, "substituto": right}]
        used = {left["servidor_id"], right["servidor_id"]}
        previous = right
        for order in range(1, stages):
            available = [p for p in people if p["id"] not in used]
            with st.expander(f"Etapa {order+1} — cascata", expanded=True):
                st.markdown(f"**{LABEL_AWAY}**")
                st.caption("Substituído automaticamente: " + previous["nome"])
                _show_locked_snapshot(previous)
                if not available:
                    st.error("Não há servidor disponível sem criar ciclo.")
                    continue
                st.markdown(f"**{LABEL_REPLACEMENT}**")
                candidate = _pick(
                    LABEL_REPLACEMENT,
                    available,
                    key=f"memorando_cascade_{order}",
                    placeholder=PLACEHOLDER_REPLACEMENT,
                )
                extra = _snapshot(candidate, f"memorando_right_{order}") if candidate else None
            if extra:
                steps.append({"substituido": dict(previous), "substituto": extra})
                used.add(extra["servidor_id"])
                previous = extra
    add, remove = st.columns(2)
    if add.button("Adicionar substituição em cascata", disabled=not complete):
        st.session_state["memorando_stages"] += 1
        st.rerun()
    if stages > 1 and remove.button("Remover última etapa"):
        st.session_state["memorando_stages"] -= 1
        st.rerun()
    return steps, complete


def _finalize_active(service, principal, record, identifier, preview, key):
    valid = preview_is_valid(preview, record)
    if st.button("Finalizar memorando", type="primary", disabled=not valid, key=key):
        if not valid or not preview:
            st.error("Gere novamente a prévia antes de finalizar.")
            return
        try:
            identifier = service.save_draft(record, actor_email=principal.email, identifier=identifier)
            st.session_state["memorando_id"] = identifier
            service.finalize(
                identifier,
                preview_hash=preview["fingerprint"],
                pdf_bytes=preview["pdf"],
                filename=preview["pdf_name"],
                docx_bytes=preview.get("docx"),
                docx_filename=preview.get("docx_name"),
                uploaded=preview.get("source") == SOURCE_UPLOAD,
            )
            _audit_memo("MEMORANDO_FINALIZADO", "FINALIZAR", identifier)
            st.success("Memorando finalizado.")
            from services.alerts import invalidate_alert_summary

            invalidate_alert_summary()
        except ValueError as exc:
            st.error(str(exc))


def _documents(service, principal, record, identifier):
    from document_generator.memorandos import preview_documents
    from document_generator.pdf import convert

    preview = st.session_state.get("memorando_preview")
    if preview and not preview_is_valid(preview, record):
        st.session_state.pop("memorando_preview", None)
        preview = None
        st.warning("Os dados foram alterados. Gere novamente a prévia antes de finalizar.")
    st.markdown("**Documento**")
    if st.button("Gerar prévia"):
        try:
            docx, pdf_bytes = preview_documents(record)
            st.session_state["memorando_preview"] = {
                "source": SOURCE_SYSTEM,
                "fingerprint": fingerprint(record),
                "docx": docx,
                "pdf": pdf_bytes,
                "docx_name": safe_filename(record, "docx"),
                "pdf_name": safe_filename(record, "pdf"),
            }
            preview = st.session_state["memorando_preview"]
            st.success("Prévia atualizada com sucesso.")
        except RuntimeError as exc:
            st.error(str(exc))
    system_ready = preview_is_valid(preview, record) and preview.get("source") == SOURCE_SYSTEM
    if system_ready:
        a, b = st.columns(2)
        a.download_button("Baixar DOCX", preview["docx"], preview["docx_name"], MIME_DOCX, key="memo_dl_sys_docx")
        b.download_button("Baixar PDF", preview["pdf"], preview["pdf_name"], MIME_PDF, key="memo_dl_sys_pdf")
        st.caption("Prévia pronta para conferência.")
        _finalize_active(service, principal, record, identifier, preview, "memo_finalize_system")
    st.markdown("**Usar DOCX editado ou existente (opcional)**")
    st.caption("Opcional: envie um DOCX caso queira substituir o documento gerado pelo sistema ou utilizar um documento já existente.")
    uploaded = st.file_uploader("Selecionar arquivo .docx", type=["docx"], key="memorando_user_docx")
    upload_bytes = None
    upload_name = None
    if uploaded is not None:
        try:
            upload_name, _ = validate_docx(uploaded.name, uploaded.getvalue())
            upload_bytes = uploaded.getvalue()
        except ValueError as exc:
            st.error(str(exc))
    if preview and preview.get("source") == SOURCE_UPLOAD and upload_bytes and digest(upload_bytes) != digest(preview["docx"]):
        st.session_state.pop("memorando_preview", None)
        preview = None
        st.warning("O arquivo DOCX foi alterado. Gere o PDF novamente.")
    if st.button("Gerar PDF deste DOCX", disabled=not upload_bytes):
        if not upload_bytes:
            st.error("Selecione um arquivo DOCX apenas se quiser substituir a prévia gerada pelo sistema.")
        else:
            try:
                pdf_bytes, _ = convert(upload_bytes)
                st.session_state["memorando_preview"] = {
                    "source": SOURCE_UPLOAD,
                    "fingerprint": upload_fingerprint(record, upload_bytes, pdf_bytes),
                    "docx": upload_bytes,
                    "pdf": pdf_bytes,
                    "docx_name": upload_name or safe_filename(record, "docx"),
                    "pdf_name": safe_filename(record, "pdf"),
                }
                preview = st.session_state["memorando_preview"]
                st.success("DOCX convertido para PDF com sucesso.")
            except (RuntimeError, ValueError) as exc:
                st.error(str(exc))
    upload_ready = preview_is_valid(preview, record) and preview.get("source") == SOURCE_UPLOAD
    if upload_ready:
        st.download_button("Baixar PDF convertido", preview["pdf"], preview["pdf_name"], MIME_PDF, key="memo_dl_up_pdf")
        _finalize_active(service, principal, record, identifier, preview, "memo_finalize_upload")


def _editor(service, store, principal):
    people=service.servers(limit=600)
    if len(people)<2: st.info("Importe ao menos dois servidores ativos."); return
    st.caption("Preencher → Gerar prévia → Baixar DOCX/PDF → Conferir → Finalizar")
    steps, complete = _chain(people)
    members=[p for p in store.catalog("procuradores") if p["ativo"]]
    cabinet=st.selectbox("Gabinete do servidor substituído",members,format_func=lambda p:p["nome"])
    default=next((i for i,p in enumerate(members) if "elvira samara" in p["nome"].casefold()),0)
    signer=st.selectbox("Signatário",members,index=default,format_func=lambda p:p["nome"])
    a,b,c=st.columns(3); nature=a.selectbox("Natureza da função",NATUREZAS); motive=b.selectbox("Motivo",MOTIVOS); custom=c.text_input("Texto do motivo",disabled=motive!="Outro")
    period=st.date_input("Período",value=(date.today(),date.today()),format="DD/MM/YYYY")
    start,end=period if isinstance(period,tuple) else (period,period)
    if end<start: st.error("A data final deve ser igual ou posterior à inicial."); return
    if not complete:
        st.info("Selecione o servidor afastado e o substituto(a) para continuar.")
        return
    record={"tipo":TIPO_SUBSTITUICAO,"data_inicio":start.isoformat(),"data_fim":end.isoformat(),"natureza_funcao":nature,"motivo":motive,"motivo_texto":custom,"gabinete_procurador_id":cabinet["id"],"gabinete_snapshot":cabinet_text(cabinet,steps[0]["substituido"]["genero"]),"signatario_id":signer["id"],"signatario_nome":signer["nome"],"signatario_cargo":signature_role(signer),"etapas":steps}
    st.caption(f"Duração calculada: {(end-start).days+1} dias.")
    identifier=st.session_state.get("memorando_id")
    if st.button("Salvar rascunho"):
        try:
            identifier=service.save_draft(record,actor_email=principal.email,identifier=identifier); st.session_state["memorando_id"]=identifier
            if service.overlaps(record,exclude_id=identifier): st.warning("Há sobreposição de período envolvendo participante. É um aviso, não bloqueio.")
            st.success("Rascunho salvo.")
            from services.alerts import invalidate_alert_summary

            invalidate_alert_summary()
        except ValueError as exc: st.error(str(exc))
    _documents(service, principal, record, identifier)


def _details(service,row,principal):
    r=service.get(row["id"]); st.write("Cadeia: "+row["cadeia"]); st.write(f"Período: {r['data_inicio']} a {r['data_fim']} · {r['situacao']}")
    st.write("Gabinete: "+r["gabinete_snapshot"]); st.write("Signatário: "+r["signatario_nome"]+" — "+r["signatario_cargo"])
    for index,step in enumerate(r["etapas"],1): st.caption(f"Etapa {index}: {step['substituido']['nome']} ({step['substituido']['cargo']}, {step['substituido'].get('lotacao','')}) → {step['substituto']['nome']} ({step['substituto']['cargo']}, {step['substituto'].get('lotacao','')})")
    st.caption(f"Criado por {r['criado_por']} em {r['criado_em']}. Finalizado: {r.get('finalizado_em') or '—'}")
    file=service.file(row["id"])
    source=service.file(row["id"], MIME_DOCX)
    if source: st.download_button("Baixar DOCX",source[1],source[0],MIME_DOCX,key="memo_docx_"+row["id"])
    if file: st.download_button("Baixar PDF",file[1],file[0],MIME_PDF,key="memo_file_"+row["id"])
    if r["status"]=="RASCUNHO" and st.button("Excluir rascunho",key="memo_del_"+row["id"]):
        try:
            service.delete_draft(row["id"]); _audit_memo("RASCUNHO_EXCLUIDO","EXCLUIR",row["id"]); st.success("Rascunho excluído.");
            from services.alerts import invalidate_alert_summary

            invalidate_alert_summary()
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if r["status"]=="FINALIZADO":
        number=st.text_input("Número oficial",value=r["numero_oficial"],key="memo_number_"+row["id"])
        if st.button("Salvar número oficial",key="memo_number_save_"+row["id"]):
            service.set_official_number(row["id"],number,principal.email)
            _audit_memo("NUMERO_OFICIAL","REGISTRAR",row["id"],{"numero_oficial": (number or "")[:40]})
            st.rerun()
    if principal.administrator and r["status"] != "RASCUNHO":
        _hard_delete_controls(service, row, principal, r)


def _clear_memo_ui_state(identifier=None):
    st.session_state.pop("pending_focus_memorando", None)
    st.session_state.pop("pending_open_memorando", None)
    st.session_state.pop("memo_hard_delete_id", None)
    if not identifier:
        return
    for prefix in (
        "memo_hard_delete_typed_",
        "memo_number_",
        "memo_docx_",
        "memo_file_",
        "memo_del_",
        "memo_number_save_",
        "memo_hard_open_",
        "memo_hard_cancel_",
        "memo_hard_confirm_",
    ):
        st.session_state.pop(prefix + identifier, None)


def _hard_delete_controls(service, row, principal, record):
    identifier = row["id"]
    st.divider()
    st.caption("Ações administrativas")
    if st.session_state.get("memo_hard_delete_id") != identifier:
        if st.button("Excluir definitivamente", key="memo_hard_open_" + identifier):
            st.session_state["memo_hard_delete_id"] = identifier
            st.rerun()
        return
    etapas = record.get("etapas") or []
    away = (etapas[0]["substituido"]["nome"] if etapas else "—")
    replacement = (etapas[0]["substituto"]["nome"] if etapas else "—")
    st.warning("A exclusão removerá o registro e os documentos associados do sistema.")
    if (record.get("numero_oficial") or "").strip():
        st.warning(
            "Este memorando possui número oficial registrado. A exclusão removerá o registro e os documentos associados do sistema."
        )
    st.write(f"Servidor afastado: {away}")
    st.write(f"Substituto: {replacement}")
    st.write(f"Período: {record.get('data_inicio')} a {record.get('data_fim')}")
    st.write(f"Gabinete: {record.get('gabinete_snapshot') or '—'}")
    st.write(f"Status: {record.get('situacao') or record.get('status')}")
    official = (record.get("numero_oficial") or "").strip()
    st.write("Número oficial: " + (official or "—"))
    typed = st.text_input(
        "Digite EXCLUIR para confirmar",
        key="memo_hard_delete_typed_" + identifier,
    )
    cancel, confirm = st.columns(2)
    if cancel.button("Cancelar", key="memo_hard_cancel_" + identifier):
        st.session_state.pop("memo_hard_delete_id", None)
        st.rerun()
    if confirm.button(
        "Confirmar exclusão definitiva",
        disabled=(typed or "") != "EXCLUIR",
        key="memo_hard_confirm_" + identifier,
    ):
        try:
            snapshot = service.delete_finalized(
                identifier,
                administrator=principal.administrator,
                confirmation=typed,
            )
            _audit_memo(
                "MEMORANDO_EXCLUIDO_DEFINITIVAMENTE",
                "EXCLUIR",
                identifier,
                {
                    "gabinete": (snapshot.get("gabinete") or "")[:80],
                    "status": snapshot.get("status"),
                    "numero_oficial": (snapshot.get("numero_oficial") or "")[:40] or None,
                },
            )
            from services.alerts import invalidate_alert_summary

            invalidate_alert_summary()
            _clear_memo_ui_state(identifier)
            st.session_state["memo_hard_delete_flash"] = True
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))


def _listing(service, principal):
    if st.session_state.pop("memo_hard_delete_flash", None):
        st.success("Memorando excluído definitivamente.")
    focus = st.session_state.pop("pending_focus_memorando", None)
    focused = None
    if focus:
        try:
            focused = service.get(focus)
            etapas = focused.get("etapas") or []
            focused["cadeia"] = (
                " → ".join(
                    [etapas[0]["substituido"]["nome"]]
                    + [x["substituto"]["nome"] for x in etapas]
                )
                if etapas
                else focused.get("id")
            )
        except ValueError:
            focused = None
    with st.expander("Filtros", expanded=True):
        filter_mark()
        a, b, c = st.columns(3)
        search = a.text_input("Busca textual")
        server = a.text_input("Servidor")
        cabinet = b.text_input("Gabinete")
        status = b.selectbox(
            "Status documental",
            [None, "RASCUNHO", "FINALIZADO", "CANCELADO"],
            format_func=lambda x: x or "Todos",
        )
        situacao = c.selectbox(
            "Situação",
            [None, "AGENDADA", "EM ANDAMENTO", "ENCERRADA", "CANCELADA"],
            format_func=lambda x: x or "Todas",
        )
        official = c.text_input("Número oficial")
        start = a.date_input("Período a partir de", value=None, format="DD/MM/YYYY")
        end = b.date_input("Período até", value=None, format="DD/MM/YYYY")
    page = st.number_input("Página", min_value=1, value=1)
    rows = service.list(
        status=status,
        situacao=situacao,
        search=search,
        server=server,
        cabinet=cabinet,
        official_number=official,
        start=start.isoformat() if start else None,
        end=end.isoformat() if end else None,
        offset=(page - 1) * 30,
        limit=30,
    )
    shown = set()
    if focused:
        shown.add(focused["id"])
        with st.expander(focused["cadeia"] + " · " + focused["situacao"], expanded=True):
            render_record(
                focused["cadeia"],
                badges_html=badges(
                    (focused.get("status") or focused["situacao"], status_tone(focused.get("status") or focused["situacao"])),
                    (focused["situacao"], status_tone(focused["situacao"])),
                ),
                secondary=f"{focused['data_inicio']} a {focused['data_fim']} · {focused['gabinete_snapshot']} · {focused['motivo']}",
                meta=("Número oficial: " + focused["numero_oficial"]) if focused.get("numero_oficial") else "",
                accent=status_tone(focused.get("status") or focused["situacao"]),
            )
            _details(service, focused, principal)
    if not rows and not focused:
        st.info("Nenhum memorando encontrado.")
    for row in rows:
        if row["id"] in shown:
            continue
        with st.expander(row["cadeia"] + " · " + row["situacao"], expanded=False):
            accent = "muted" if (row.get("status") == "CANCELADO" or row["situacao"] in ("ENCERRADA", "CANCELADA")) else status_tone(row["situacao"])
            render_record(
                row["cadeia"],
                badges_html=badges(
                    (row.get("status"), status_tone(row.get("status"))) if row.get("status") else None,
                    (row["situacao"], status_tone(row["situacao"])),
                ),
                secondary=f"{row['data_inicio']} a {row['data_fim']} · {row['gabinete_snapshot']} · {row['motivo']}",
                meta=("Número oficial: " + row["numero_oficial"]) if row.get("numero_oficial") else "",
                accent=accent,
            )
            _details(service, row, principal)


def _base(service,principal):
    if not principal.administrator:
        st.error("Acesso não autorizado à base de servidores.")
        return
    upload=st.file_uploader("Nova planilha XLSX",type=["xlsx"])
    if upload and st.button("Ler e validar planilha"):
        try:
            content=upload.getvalue(); rows=read_server_xlsx(content); st.session_state["memo_import"]=(upload.name,hashlib.sha256(content).hexdigest(),rows,service.preview_import(rows))
        except (ValueError,RuntimeError) as exc: st.error(str(exc))
    pending=st.session_state.get("memo_import")
    if pending:
        name,digest,rows,report=pending; st.dataframe([{"Total":report["total"],"Novos":report["novos"],"Atualizados":report["atualizados"],"Matrícula vazia":len(report["sem_matricula"]),"Matrícula zero":len(report["matriculas_zero"]),"Duplicidades":len(report["duplicidades"]),"Inconsistentes":len(report["inconsistentes"])}],hide_index=True)
        if st.checkbox("Confirmo a importação desta prévia") and st.button("Importar base transacionalmente"):
            result=service.import_servers(rows,actor_email=principal.email,filename=name,content_hash=digest,administrator=principal.administrator)
            _audit_memo("BASE_IMPORTADA","IMPORTAR",None,{"registros_novos":result.get("incluidos"),"registros_atualizados":result.get("atualizados")})
            st.session_state.pop("memo_import", None)
            _reset_new_form()
            st.session_state["memorando_form_active"] = False
            st.success(f"{result['incluidos']} incluídos; {result['atualizados']} atualizados.")
    st.divider(); servers=service.all_servers(include_inactive=True)
    if st.session_state.pop("memo_server_flash", None):
        st.success("Cadastro atualizado com sucesso.")
    if not servers:
        return
    if not st.session_state.get("memo_server_edit_open"):
        if st.button("Corrigir servidor"):
            st.session_state["memo_server_edit_open"] = True
            st.session_state["memo_server_pick"] = None
            st.rerun()
        return
    by_id = {person["id"]: person for person in servers}
    options = list(by_id)
    current_id = st.session_state.get("memo_server_pick")
    if current_id is not None and current_id not in by_id:
        st.session_state.pop("memo_server_pick", None)
    chosen = st.selectbox(
        "Servidor",
        options,
        index=None,
        placeholder="Selecione um servidor",
        format_func=lambda identifier: _label(by_id.get(identifier)) if identifier is not None else "",
        key="memo_server_pick",
    )
    if chosen is None:
        return
    current = by_id[chosen]
    with st.form("memo_server_edit_" + str(chosen)):
        name=st.text_input("Nome", current["nome"], key="memo_server_name_"+str(chosen))
        cargo=st.text_input("Cargo", current["cargo"], key="memo_server_cargo_"+str(chosen))
        sector=st.text_input("Setor", current["setor"], key="memo_server_setor_"+str(chosen))
        gender_options = (None, "Masculino", "Feminino")
        gender=st.selectbox(
            "Gênero",
            gender_options,
            format_func=lambda x: x or "Não informado",
            key="memo_server_gender_"+str(chosen),
            index=gender_options.index(current["genero"]) if current["genero"] in ("Masculino", "Feminino") else 0,
        )
        active=st.checkbox("Ativo", bool(current["ativo"]), key="memo_server_ativo_"+str(chosen))
        if st.form_submit_button("Salvar correção"):
            try:
                service.update_server(current["id"], {"nome":name,"cargo":cargo,"setor":sector,"genero":gender,"ativo":active}, actor_email=principal.email, administrator=principal.administrator)
                _audit_memo("SERVIDOR_CORRIGIDO","CORRIGIR",current["id"])
            except ValueError as exc:
                st.error(str(exc))
            else:
                for key in list(st.session_state.keys()):
                    if str(key).startswith("memo_server_"):
                        st.session_state.pop(key, None)
                st.session_state["memo_server_edit_open"] = False
                st.session_state["memo_server_flash"] = True
                st.rerun()


def render(store,principal):
    require_permission(principal,"memorandos"); service=open_service(store); st.subheader(module_title("memorandos", "MEMORANDOS DE SUBSTITUIÇÃO"))
    pages=_nav_pages(principal)
    page=_current_page(pages)
    if page != NAV_BASE:
        st.session_state["memo_server_edit_open"] = False
    if page==NAV_NEW:
        if not st.session_state.get("memorando_form_active"):
            _reset_new_form()
            st.session_state["memorando_form_active"] = True
        _editor(service,store,principal)
    else:
        st.session_state["memorando_form_active"] = False
        if page==NAV_HISTORY: _listing(service,principal)
        elif page==NAV_BASE and principal.administrator:
            _base(service,principal)
        else:
            counts = service.situacao_counts()
            for col, status in zip(st.columns(3), ("EM ANDAMENTO", "AGENDADA", "ENCERRADA")):
                col.metric(status, counts.get(status, 0))
