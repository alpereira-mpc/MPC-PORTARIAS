"""Business rules for Petições. No dependency on Representações."""
from database.peticoes import PeticoesStore
from services.audit import registrar_evento
from services.oficios import validate_upload

NATUREZAS={"PROVIDENCIAS":"Pedido de providências","FISCALIZACAO":"Pedido de fiscalização","NOTA_RECOMENDATORIA":"Pedido de Nota Recomendatória","REPRESENTACAO":"Representação apresentada como Petição","INCIDENTAL":"Petição incidental","INSTITUCIONAL":"Manifestação institucional","OUTROS":"Outros"}
RESULTADOS={"PENDENTE":"Pendente","ACOLHIDO":"Acolhido","ACOLHIDO_PARCIALMENTE":"Acolhido parcialmente","INDEFERIDO":"Indeferido","PREJUDICADO":"Prejudicado","OUTRO":"Outro"}
SITUACOES={"PROTOCOLADA":"Protocolada","EM_ACOMPANHAMENTO":"Em acompanhamento","CONCLUIDA":"Concluída"}
def _actor(principal): return getattr(principal,"email",None) or str(principal)
def _audit(store,principal,event,action,identifier,extra=None): registrar_evento(store,evento=event,modulo="peticoes",acao=action,principal=principal,entidade_tipo="peticao",entidade_id=identifier,detalhes=extra)
def validate(data,upload=None):
    for key in ("numero_tramita","data_protocolo","destinatario","natureza","assunto","objeto"):
        if not str(data.get(key) or "").strip(): raise ValueError("Preencha os campos obrigatórios da Petição.")
    if not data.get("signatarios"): raise ValueError("Selecione ao menos um Procurador signatário.")
    if "pedidos" in data and (not data["pedidos"] or not all(str(x).strip() for x in data["pedidos"])): raise ValueError("Informe ao menos um pedido válido.")
    if upload: validate_upload(upload[0],upload[2])
def create(store,data,principal,upload):
    if not upload: raise ValueError("Anexe o PDF protocolado.")
    validate(data,upload); record=PeticoesStore(store).create(data,_actor(principal),upload); _audit(store,principal,"PETICAO_CRIADA","CRIAR",record["id"],{"numero":record["numero_tramita"]}); return record
def update(store,identifier,data,principal,upload=None):
    validate(data,upload); record=PeticoesStore(store).update(identifier,data,_actor(principal),upload); _audit(store,principal,"PETICAO_EDITADA","EDITAR",identifier,{"numero":record["numero_tramita"]})
    if upload: _audit(store,principal,"PETICAO_PDF_SUBSTITUIDO","EDITAR",identifier,{"arquivo":upload[0]})
    return record
def add_progress(store,identifier,data,description,principal):
    if not description.strip(): raise ValueError("Informe a descrição do andamento.")
    PeticoesStore(store).add_progress(identifier,data,description,_actor(principal)); _audit(store,principal,"PETICAO_ANDAMENTO_CRIADO","CRIAR",identifier)
def remove_progress(store,progress_id,reason,principal):
    identifier=PeticoesStore(store).remove_progress(progress_id,_actor(principal),reason); _audit(store,principal,"PETICAO_ANDAMENTO_EXCLUIDO_LOGICAMENTE","EXCLUIR",identifier,{"motivo":reason}); return identifier
def update_request_result(store,request_id,status,result,result_date,principal):
    if status not in RESULTADOS: raise ValueError("Situação de resultado inválida.")
    identifier=PeticoesStore(store).update_request_result(request_id,status,result,result_date); _audit(store,principal,"PETICAO_RESULTADO_REGISTRADO","EDITAR",identifier,{"pedido_id":request_id,"situacao":status}); return identifier
def save_requests(store,identifier,requests,principal):
    if not requests or not all((item.get("descricao") or "").strip() for item in requests): raise ValueError("Informe ao menos um pedido válido.")
    removed=PeticoesStore(store).save_requests(identifier,requests,_actor(principal)); _audit(store,principal,"PETICAO_PEDIDOS_EDITADOS","EDITAR",identifier)
    for request_id in removed: _audit(store,principal,"PETICAO_PEDIDO_EXCLUIDO_LOGICAMENTE","EXCLUIR",identifier,{"pedido_id":request_id})
def conclude(store,identifier,result,principal):
    PeticoesStore(store).conclude(identifier,result,_actor(principal)); _audit(store,principal,"PETICAO_CONCLUIDA","CONCLUIR",identifier)
