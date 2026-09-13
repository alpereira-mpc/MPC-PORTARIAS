"""Lazy Streamlit UI for substitution memoranda."""
from datetime import date
import hashlib
import streamlit as st
from services.access import require_permission
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
NAV_BASE = "Base de Servidores"


def _nav_pages(principal):
    pages = [NAV_OVERVIEW, NAV_NEW, "Em andamento", "Histórico"]
    if principal.administrator:
        pages.append(NAV_BASE)
    return pages


def _current_page(pages):
    if st.session_state.get(NAV_KEY) not in pages:
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
            st.success("Memorando finalizado.")
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
            service.delete_draft(row["id"]); st.success("Rascunho excluído."); st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if r["status"]=="FINALIZADO":
        number=st.text_input("Número oficial",value=r["numero_oficial"],key="memo_number_"+row["id"])
        if st.button("Salvar número oficial",key="memo_number_save_"+row["id"]): service.set_official_number(row["id"],number,principal.email); st.rerun()


def _listing(service,principal,ongoing=False):
    with st.expander("Filtros",expanded=not ongoing):
        a,b,c=st.columns(3); search=a.text_input("Busca textual"); server=a.text_input("Servidor"); cabinet=b.text_input("Gabinete"); status=b.selectbox("Status documental",[None,"RASCUNHO","FINALIZADO","CANCELADO"],format_func=lambda x:x or "Todos"); official=c.text_input("Número oficial"); start=c.date_input("Período a partir de",value=None,format="DD/MM/YYYY"); end=c.date_input("Período até",value=None,format="DD/MM/YYYY")
    page=st.number_input("Página",min_value=1,value=1); rows=service.list(status=status,search=search,server=server,cabinet=cabinet,official_number=official,start=start.isoformat() if start else None,end=end.isoformat() if end else None,offset=(page-1)*30,limit=30)
    if ongoing: rows=[r for r in rows if r["situacao"] in ("AGENDADA","EM ANDAMENTO")]
    if not rows: st.info("Nenhum memorando encontrado.")
    for row in rows:
        with st.expander(row["cadeia"]+" · "+row["situacao"]):
            st.write(f"{row['data_inicio']} a {row['data_fim']} · {row['gabinete_snapshot']} · {row['motivo']}")
            if row["numero_oficial"]: st.caption("Número oficial: "+row["numero_oficial"])
            _details(service,row,principal)


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
            st.session_state.pop("memo_import", None)
            _reset_new_form()
            st.session_state["memorando_form_active"] = False
            st.success(f"{result['incluidos']} incluídos; {result['atualizados']} atualizados.")
    st.divider(); servers=service.all_servers(include_inactive=True)
    if servers:
        current=st.selectbox("Corrigir servidor",servers,format_func=_label)
        with st.form("memo_server_edit"):
            name=st.text_input("Nome",current["nome"]); cargo=st.text_input("Cargo",current["cargo"]); sector=st.text_input("Setor",current["setor"]); gender=st.selectbox("Gênero",[None,"Masculino","Feminino"],format_func=lambda x:x or "Não informado"); active=st.checkbox("Ativo",bool(current["ativo"]))
            if st.form_submit_button("Salvar correção"): service.update_server(current["id"],{"nome":name,"cargo":cargo,"setor":sector,"genero":gender,"ativo":active},actor_email=principal.email,administrator=principal.administrator); st.success("Cadastro atualizado.")


def render(store,principal):
    require_permission(principal,"memorandos"); service=open_service(store); st.subheader("MEMORANDOS DE SUBSTITUIÇÃO")
    pages=_nav_pages(principal)
    page=_current_page(pages)
    if page==NAV_NEW:
        if not st.session_state.get("memorando_form_active"):
            _reset_new_form()
            st.session_state["memorando_form_active"] = True
        _editor(service,store,principal)
    else:
        st.session_state["memorando_form_active"] = False
        if page=="Em andamento": _listing(service,principal,True)
        elif page=="Histórico": _listing(service,principal)
        elif page==NAV_BASE and principal.administrator:
            _base(service,principal)
        else:
            rows=service.list(limit=200)
            for col,status in zip(st.columns(3),("EM ANDAMENTO","AGENDADA","ENCERRADA")): col.metric(status,sum(r["situacao"]==status for r in rows))
