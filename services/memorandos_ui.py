"""Lazy Streamlit UI for substitution memoranda."""
from datetime import date
import hashlib
import streamlit as st
from services.access import require_permission
from services.memorandos import MOTIVOS, NATUREZAS, TIPO_SUBSTITUICAO, cabinet_text, display_sector, fingerprint, open_service, read_server_xlsx, safe_filename


def _label(r): return f"{r['nome']} — {r['matricula_original'] or 'sem matrícula'} — {r['setor']}"


def _snapshot(row, prefix):
    known = {"masculino": "Masculino", "feminino": "Feminino", "Masculino": "Masculino", "Feminino": "Feminino"}.get(row.get("genero"))
    gender = known or st.selectbox("Gênero gramatical", ("Masculino", "Feminino"), key=prefix+"_gender")
    return {"servidor_id":row["id"],"nome":row["nome"],"matricula":row["matricula_original"],"genero":gender,
            "cargo":st.text_input("Cargo/função documental",row["cargo"],key=prefix+"_cargo"),
            "lotacao":st.text_input("Lotação documental",display_sector(row["setor"]),key=prefix+"_lotacao")}


def _chain(people):
    stages = st.session_state.setdefault("memorando_stages", 1)
    away = st.selectbox("Servidor afastado", people, format_func=_label, key="memorando_away")
    replacement = st.selectbox("Primeiro substituto", people, format_func=_label, key="memorando_replacement")
    with st.expander("Etapa 1", expanded=True):
        left, right = _snapshot(away,"memorando_left_0"), _snapshot(replacement,"memorando_right_0")
    steps=[{"substituido":left,"substituto":right}]; used={left["servidor_id"],right["servidor_id"]}; previous=right
    for order in range(1, stages):
        available=[p for p in people if p["id"] not in used]
        with st.expander(f"Etapa {order+1} — cascata", expanded=True):
            st.caption("Substituído automaticamente: "+previous["nome"])
            if not available: st.error("Não há servidor disponível sem criar ciclo."); continue
            candidate=st.selectbox("Novo substituto",available,format_func=_label,key=f"memorando_cascade_{order}")
            right=_snapshot(candidate,f"memorando_right_{order}")
        steps.append({"substituido":dict(previous),"substituto":right}); used.add(right["servidor_id"]); previous=right
    add, remove=st.columns(2)
    if add.button("Adicionar substituição em cascata"):
        st.session_state["memorando_stages"]+=1; st.rerun()
    if stages>1 and remove.button("Remover última etapa"):
        st.session_state["memorando_stages"]-=1; st.rerun()
    return steps


def _editor(service, store, principal):
    people=service.servers(limit=600)
    if len(people)<2: st.info("Importe ao menos dois servidores ativos."); return
    st.caption("Preencher → Pré-visualizar PDF → Conferir → Finalizar → Baixar PDF")
    steps=_chain(people); members=[p for p in store.catalog("procuradores") if p["ativo"]]
    cabinet=st.selectbox("Gabinete do servidor substituído",members,format_func=lambda p:p["nome"])
    default=next((i for i,p in enumerate(members) if "elvira samara" in p["nome"].casefold()),0)
    signer=st.selectbox("Signatário",members,index=default,format_func=lambda p:p["nome"])
    a,b,c=st.columns(3); nature=a.selectbox("Natureza da função",NATUREZAS); motive=b.selectbox("Motivo",MOTIVOS); custom=c.text_input("Texto do motivo",disabled=motive!="Outro")
    start,end=st.date_input("Período",value=(date.today(),date.today()),format="DD/MM/YYYY")
    if end<start: st.error("A data final deve ser igual ou posterior à inicial."); return
    record={"tipo":TIPO_SUBSTITUICAO,"data_inicio":start.isoformat(),"data_fim":end.isoformat(),"natureza_funcao":nature,"motivo":motive,"motivo_texto":custom,"gabinete_procurador_id":cabinet["id"],"gabinete_procurador":cabinet,"gabinete_snapshot":cabinet_text(cabinet,steps[0]["substituido"]["genero"]),"signatario_id":signer["id"],"signatario_nome":signer["nome"],"signatario_cargo":signer["funcao"],"etapas":steps}
    st.caption(f"Duração calculada: {(end-start).days+1} dias.")
    current=fingerprint(record); preview=st.session_state.get("memorando_preview")
    if preview and preview["fingerprint"]!=current:
        st.session_state.pop("memorando_preview",None); preview=None; st.warning("Dados alterados: gere uma nova prévia.")
    identifier=st.session_state.get("memorando_id")
    if st.button("Salvar rascunho",type="primary"):
        try:
            identifier=service.save_draft(record,actor_email=principal.email,identifier=identifier); st.session_state["memorando_id"]=identifier
            if service.overlaps(record,exclude_id=identifier): st.warning("Há sobreposição de período envolvendo participante. É um aviso, não bloqueio.")
            st.success("Rascunho salvo.")
        except ValueError as exc: st.error(str(exc))
    if identifier and st.button("Pré-visualizar PDF"):
        try:
            from document_generator.memorandos import pdf
            st.session_state["memorando_preview"]={"fingerprint":current,"content":pdf(record),"name":safe_filename(record)}; preview=st.session_state["memorando_preview"]; st.success("Prévia gerada.")
        except RuntimeError as exc: st.error(str(exc))
    if identifier and preview and preview["fingerprint"]==current:
        st.download_button("Baixar prévia PDF",preview["content"],preview["name"],"application/pdf")
        if st.button("Finalizar memorando",type="primary"):
            try:
                service.save_draft(record,actor_email=principal.email,identifier=identifier); service.finalize(identifier,preview_hash=current,pdf_bytes=preview["content"],filename=preview["name"]); st.success("Memorando finalizado.")
            except ValueError as exc: st.error(str(exc))


def _details(service,row,principal):
    r=service.get(row["id"]); st.write("Cadeia: "+row["cadeia"]); st.write(f"Período: {r['data_inicio']} a {r['data_fim']} · {r['situacao']}")
    st.write("Gabinete: "+r["gabinete_snapshot"]); st.write("Signatário: "+r["signatario_nome"]+" — "+r["signatario_cargo"])
    for index,step in enumerate(r["etapas"],1): st.caption(f"Etapa {index}: {step['substituido']['nome']} ({step['substituido']['cargo']}, {step['substituido'].get('lotacao','')}) → {step['substituto']['nome']} ({step['substituto']['cargo']}, {step['substituto'].get('lotacao','')})")
    st.caption(f"Criado por {r['criado_por']} em {r['criado_em']}. Finalizado: {r.get('finalizado_em') or '—'}")
    file=service.file(row["id"])
    if file: st.download_button("Baixar PDF",file[1],file[0],"application/pdf",key="memo_file_"+row["id"])
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
    upload=st.file_uploader("Nova planilha XLSX",type=["xlsx"])
    if upload and st.button("Ler e validar planilha"):
        try:
            content=upload.getvalue(); rows=read_server_xlsx(content); st.session_state["memo_import"]=(upload.name,hashlib.sha256(content).hexdigest(),rows,service.preview_import(rows))
        except (ValueError,RuntimeError) as exc: st.error(str(exc))
    pending=st.session_state.get("memo_import")
    if pending:
        name,digest,rows,report=pending; st.dataframe([{"Total":report["total"],"Novos":report["novos"],"Atualizados":report["atualizados"],"Matrícula vazia":len(report["sem_matricula"]),"Matrícula zero":len(report["matriculas_zero"]),"Duplicidades":len(report["duplicidades"]),"Inconsistentes":len(report["inconsistentes"])}],hide_index=True)
        if st.checkbox("Confirmo a importação desta prévia") and st.button("Importar base transacionalmente"):
            result=service.import_servers(rows,actor_email=principal.email,filename=name,content_hash=digest,administrator=True); st.session_state.pop("memo_import",None); st.success(f"{result['incluidos']} incluídos; {result['atualizados']} atualizados.")
    st.divider(); servers=service.all_servers(include_inactive=True)
    if servers:
        current=st.selectbox("Corrigir servidor",servers,format_func=_label)
        with st.form("memo_server_edit"):
            name=st.text_input("Nome",current["nome"]); cargo=st.text_input("Cargo",current["cargo"]); sector=st.text_input("Setor",current["setor"]); gender=st.selectbox("Gênero",[None,"Masculino","Feminino"],format_func=lambda x:x or "Não informado"); active=st.checkbox("Ativo",bool(current["ativo"]))
            if st.form_submit_button("Salvar correção"): service.update_server(current["id"],{"nome":name,"cargo":cargo,"setor":sector,"genero":gender,"ativo":active},actor_email=principal.email,administrator=True); st.success("Cadastro atualizado.")


def render(store,principal):
    require_permission(principal,"memorandos"); service=open_service(store); st.subheader("MEMORANDOS DE SUBSTITUIÇÃO")
    pages=["Visão Geral","Novo Memorando","Em andamento","Histórico"] + (["Base de Servidores"] if principal.administrator else []); page=st.radio("Memorandos",pages,horizontal=True)
    if page=="Novo Memorando": _editor(service,store,principal)
    elif page=="Em andamento": _listing(service,principal,True)
    elif page=="Histórico": _listing(service,principal)
    elif page=="Base de Servidores": _base(service,principal)
    else:
        rows=service.list(limit=200)
        for col,status in zip(st.columns(3),("EM ANDAMENTO","AGENDADA","ENCERRADA")): col.metric(status,sum(r["situacao"]==status for r in rows))
