"""UI for Petições, using only peticoes-prefixed session keys."""
from datetime import date
import hashlib
import unicodedata
import streamlit as st
from database.peticoes import PeticoesStore
from services.access import has_permission, require_permission
from services.branding import module_title
from services.date_format import format_date_br
from services.peticoes import NATUREZAS, RESULTADOS, SITUACOES, add_progress, conclude, create, remove_progress, save_requests, update, update_request_result

def _apply_ai_prefill(store):
    data=st.session_state.pop('peticoes_ai_pending',None)
    if not data:return
    mapping={'numero_tramita':'peticoes_new_numero','destinatario':'peticoes_new_destinatario','natureza':'peticoes_new_natureza','assunto':'peticoes_new_assunto','objeto':'peticoes_new_objeto','origem':'peticoes_new_origem','processo_tc':'peticoes_new_processo'}
    for source,target in mapping.items():
        if source == 'natureza' and data.get(source) not in NATUREZAS: continue
        if data.get(source) and not st.session_state.get(target): st.session_state[target]=data[source]
    if data.get('data_protocolo') and not st.session_state.get('peticoes_new_data'):
        st.session_state['peticoes_new_data']=date.fromisoformat(data['data_protocolo'])
    def normalized(value):
        decomposed=unicodedata.normalize('NFKD', value or '')
        return " ".join(''.join(char for char in decomposed if not unicodedata.combining(char)).casefold().split())
    people={normalized(p['nome']):p['id'] for p in store.catalog('procuradores') if p.get('ativo')}
    matched=[people.get(normalized(name)) for name in data.get('signatarios',[])]; matched=[value for value in matched if value]
    if matched and not st.session_state.get('peticoes_new_signatarios'):st.session_state['peticoes_new_signatarios']=matched
    if data.get('pedidos') and not st.session_state.get('peticoes_new_pedidos'):st.session_state['peticoes_new_pedidos']='\n'.join(item['descricao'] for item in data['pedidos'])

def _data_form(store, record=None):
    record=record or {}; people=[p for p in store.catalog('procuradores') if p.get('ativo')]
    prefix='peticoes_edit_' if record else 'peticoes_new_'
    number=st.text_input('Número do documento Tramita *',record.get('numero_tramita',''),key=prefix+'numero')
    protocol=st.date_input('Data do protocolo *',date.fromisoformat(record['data_protocolo']) if record.get('data_protocolo') else date.today(),format='DD/MM/YYYY',key=prefix+'data')
    destination_type=st.selectbox('Tipo de destinatário',['Presidente do TCE-PB','Conselheiro Relator','Outro destinatário institucional'],index=['Presidente do TCE-PB','Conselheiro Relator','Outro destinatário institucional'].index(record.get('destinatario_tipo','Presidente do TCE-PB')),key=prefix+'tipo')
    destination=st.text_input('Destinatário *',record.get('destinatario',''),key=prefix+'destinatario')
    nature=st.selectbox('Natureza *',list(NATUREZAS),index=list(NATUREZAS).index(record.get('natureza','PROVIDENCIAS')),format_func=NATUREZAS.get,key=prefix+'natureza')
    subject=st.text_input('Assunto *',record.get('assunto',''),key=prefix+'assunto'); obj=st.text_area('Objeto *',record.get('objeto',''),key=prefix+'objeto')
    origin=st.text_input('Origem',record.get('origem',''),key=prefix+'origem'); process=st.text_input('Processo TC relacionado',record.get('processo_tc',''),key=prefix+'processo')
    choices={p['id']:p['nome'] for p in people}; signers=st.multiselect('Procuradores signatários *',list(choices),default=[x for x in record.get('signatarios',[]) if x in choices],format_func=choices.get,key=prefix+'signatarios')
    return {'numero_tramita':number,'data_protocolo':protocol.isoformat(),'destinatario_tipo':destination_type,'destinatario':destination,'natureza':nature,'assunto':subject,'objeto':obj,'origem':origin,'processo_tc':process,'signatarios':signers}

def _request_editor(store,principal,record):
    if not has_permission(principal,'peticoes_editar'): return
    st.subheader('Pedidos')
    draft=st.session_state.setdefault('peticoes_pedidos_draft_'+str(record['id']),[{'id':x['id'],'descricao':x['descricao']} for x in record['pedidos']])
    for index,item in enumerate(draft):
        item['descricao']=st.text_input('Pedido '+str(index+1),item.get('descricao',''),key='peticoes_pedido_desc_'+str(record['id'])+'_'+str(index))
        move_left,move_right=st.columns(2)
        if index and move_left.button('Subir',key='peticoes_pedido_up_'+str(record['id'])+'_'+str(index)):
            draft[index-1],draft[index]=draft[index],draft[index-1];st.rerun()
        if index < len(draft)-1 and move_right.button('Descer',key='peticoes_pedido_down_'+str(record['id'])+'_'+str(index)):
            draft[index+1],draft[index]=draft[index],draft[index+1];st.rerun()
    left,right=st.columns(2)
    if left.button('Adicionar pedido',key='peticoes_pedido_add_'+str(record['id'])): draft.append({'descricao':''}); st.rerun()
    if right.button('Salvar pedidos e ordem',key='peticoes_pedido_save_'+str(record['id'])):
        try: save_requests(store,record['id'],draft,principal); st.session_state.pop('peticoes_pedidos_draft_'+str(record['id']),None); st.rerun()
        except ValueError as exc: st.error(str(exc))

def render(store,principal):
    require_permission(principal,'peticoes'); db=PeticoesStore(store); st.title(module_title('peticoes','PETIÇÕES'))
    _apply_ai_prefill(store)
    if st.session_state.get('peticoes_edit_id'):
        if not has_permission(principal,'peticoes_editar'): raise ValueError('Acesso não autorizado a esta ação.')
        record=db.get(st.session_state['peticoes_edit_id']); st.subheader('Editar Petição')
        with st.form('peticoes_edit_form'):
            data=_data_form(store,record); upload=st.file_uploader('Substituir PDF protocolado (opcional)',type=['pdf'],key='peticoes_edit_pdf'); saved=st.form_submit_button('Salvar alterações',type='primary')
        if saved:
            try: update(store,record['id'],data,principal,(upload.name,upload.type or 'application/pdf',upload.getvalue()) if upload else None)
            except ValueError as exc: st.error(str(exc))
            else: st.session_state.pop('peticoes_edit_id'); st.success('Petição atualizada.'); st.rerun()
        if st.button('Cancelar',key='peticoes_edit_cancel'): st.session_state.pop('peticoes_edit_id');st.rerun()
        return
    section=st.radio('Seção',['Cadastrar Petição','Acompanhamento','Histórico'],horizontal=True,key='peticoes_secao')
    if section=='Cadastrar Petição':
        if not has_permission(principal,'peticoes_cadastrar'): st.info('Sua conta possui somente permissão de visualização.'); return
        with st.form('peticoes_form_open'):
            data=_data_form(store); requests=st.text_area('Pedidos * (um por linha)',key='peticoes_new_pedidos'); upload=st.file_uploader('PDF protocolado *',type=['pdf'],key='peticoes_new_pdf'); analyze=st.form_submit_button('✨ Preencher com IA'); submitted=st.form_submit_button('Cadastrar Petição',type='primary')
        if analyze:
            if not upload: st.warning('Selecione o PDF protocolado antes de solicitar a análise.')
            else:
                content=upload.getvalue(); file_hash=hashlib.sha256(content).hexdigest()
                try:
                    from services.ai_service import analisar_peticao_pdf
                    result=analisar_peticao_pdf(content)
                except RuntimeError as exc: st.error(str(exc))
                else:
                    st.session_state['peticoes_ai_result_'+file_hash]=result;st.session_state['peticoes_ai_pending']=result;st.success('Campos preenchidos com IA. Revise as informações antes de cadastrar a Petição.');st.rerun()
        if submitted:
            data['pedidos']=[x.strip() for x in requests.splitlines() if x.strip()]
            try:create(store,data,principal,(upload.name,upload.type or 'application/pdf',upload.getvalue()) if upload else None)
            except ValueError as exc:st.error(str(exc))
            else:st.success('Petição protocolada cadastrada.');st.rerun()
        return
    rows=db.list()
    if not rows: st.info('Nenhuma petição encontrada para os filtros selecionados.'); return
    selected=st.selectbox('Petição',rows,format_func=lambda x:f"{x['numero_tramita']} — {x['assunto']}",key='peticoes_selecionada'); record=db.get(selected['id'])
    st.subheader(record['assunto']);st.write(f"**Tramita:** {record['numero_tramita']} · **Situação:** {SITUACOES[record['situacao']]}");st.write(record['objeto'])
    if section=='Histórico':
        if has_permission(principal,'peticoes_editar') and st.button('Editar Petição',key='peticoes_edit_open'):st.session_state['peticoes_edit_id']=record['id'];st.rerun()
        if st.button('Preparar download do PDF protocolado',key='peticoes_pdf_download'):
            document=db.document(record['id']); st.download_button('Baixar PDF protocolado',document['arquivo'],document['nome'],document['mime_type'],key='peticoes_pdf_ready')
        _request_editor(store,principal,record); return
    if has_permission(principal,'peticoes_registrar_andamento'):
        with st.form('peticoes_andamento_form'):
            progress_date=st.date_input('Data',format='DD/MM/YYYY',key='peticoes_andamento_data');description=st.text_area('Andamento',key='peticoes_andamento_desc');submit=st.form_submit_button('Registrar andamento')
        if submit:
            try:add_progress(store,record['id'],progress_date.isoformat(),description,principal);st.rerun()
            except ValueError as exc:st.error(str(exc))
    for item in db.progress(record['id']):
        st.write(f"{format_date_br(item['data'])} — {item['descricao']}")
        if has_permission(principal,'peticoes_registrar_andamento') and st.button('Excluir logicamente',key='peticoes_remove_'+str(item['id'])):remove_progress(store,item['id'],'Exclusão registrada pelo usuário.',principal);st.rerun()
    if has_permission(principal,'peticoes_registrar_resultado'):
        for request in record['pedidos']:
            with st.expander('Resultado: '+request['descricao']):
                status=st.selectbox('Situação',list(RESULTADOS),index=list(RESULTADOS).index(request['situacao_resultado']),format_func=RESULTADOS.get,key='peticoes_request_status_'+str(request['id']));result=st.text_area('Resultado efetivo',request['resultado'],key='peticoes_request_result_'+str(request['id']));result_date=st.date_input('Data do resultado',date.fromisoformat(request['data_resultado']) if request['data_resultado'] else None,format='DD/MM/YYYY',key='peticoes_request_date_'+str(request['id']))
                if st.button('Salvar resultado',key='peticoes_request_save_'+str(request['id'])):update_request_result(store,request['id'],status,result,result_date.isoformat() if result_date else None,principal);st.rerun()
        result_global=st.text_area('Resultado global',record['resultado_global'],key='peticoes_resultado_global')
        pending=any(x['situacao_resultado']=='PENDENTE' for x in record['pedidos'])
        if pending:st.warning('Há pedidos pendentes. A conclusão continua possível em situações excepcionais.')
        if record['situacao']!='CONCLUIDA' and has_permission(principal,'peticoes_concluir') and st.button('Concluir Petição',key='peticoes_concluir'):conclude(store,record['id'],result_global,principal);st.rerun()
