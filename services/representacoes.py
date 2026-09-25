"""Business rules for Representações. No Streamlit and no BLOB listing."""

from datetime import date
import logging
import re

from database.memorandos import MemorandosStore
from database.representacoes import RepresentacoesStore
from services.oficios import validate_upload

LOGGER = logging.getLogger("mpc.representacoes")

ORIGENS = {
    "DE_OFICIO": "De ofício",
    "PROVOCACAO_EXTERNA": "Provocação externa",
    "PROVOCACAO_INTERNA": "Provocação interna",
    "OUVIDORIA": "Ouvidoria",
}
PRIORIDADES = {
    "BAIXA": "Baixa",
    "NORMAL": "Normal",
    "ALTA": "Alta",
    "URGENTE": "Urgente",
}
SITUACOES = {
    "IDEIA": "Ideia / avaliação inicial",
    "PESQUISA": "Em pesquisa",
    "ELABORACAO": "Em elaboração",
    "MINUTA_REVISAO": "Minuta em revisão",
    "APROVADA": "Aprovada para assinatura",
    "AGUARDANDO_PROTOCOLO": "Aguardando protocolo",
    "PROTOCOLADA": "Protocolada",
    "EM_TRAMITACAO": "Em tramitação",
    "JULGADA": "Julgada",
    "ENCERRADA": "Encerrada",
    "ARQUIVADA": "Arquivada sem protocolo",
    "SUSPENSA": "Suspensa",
    "CANCELADA": "Cancelada",
}
FASES = {
    "INSTRUCAO": "Instrução inicial",
    "AGUARDANDO_DEFESA": "Aguardando defesa",
    "DEFESA_APRESENTADA": "Defesa apresentada",
    "ANALISE_DEFESA": "Análise de defesa",
    "MPC": "MPC",
    "PAUTA": "Pauta",
    "JULGAMENTO": "Julgamento",
    "POS_JULGAMENTO": "Pós-julgamento",
}
PAPEIS = {
    "PROCURADOR_RESPONSAVEL": "Procurador responsável",
    "PROCURADOR_SIGNATARIO": "Procurador signatário",
    "ASSESSOR": "Assessor",
}
ANDAMENTOS = {
    "CRIADA": "Projeto de Representação criado",
    "PESQUISA_ATRIBUIDA": "Pesquisa atribuída",
    "MINUTA_PREPARADA": "Minuta preparada",
    "MINUTA_REVISADA": "Minuta revisada",
    "APROVADA": "Aprovada",
    "PROTOCOLADA": "Representação protocolada",
    "DEFESA_APRESENTADA": "Defesa apresentada",
    "RELATORIO_ANALISE_DEFESA": "Relatório de análise de defesa",
    "ENCAMINHAMENTO_MPC": "Encaminhamento ao MPC",
    "PARECER_EMITIDO": "Parecer emitido",
    "INCLUIDO_PAUTA": "Incluído em pauta",
    "JULGADO": "Julgado",
    "ACORDAO_PUBLICADO": "Acórdão publicado",
    "ENCERRADO": "Encerrado",
    "SITUACAO": "Situação atualizada",
    "LIVRE": "Andamento livre",
}
DOCUMENTOS = {
    "PROVOCACAO": "Provocação",
    "PESQUISA": "Pesquisa",
    "NOTA_TECNICA": "Nota técnica",
    "MINUTA": "Minuta",
    "REPRESENTACAO_FINAL": "Representação final",
    "DEFESA": "Defesa",
    "DOCUMENTOS_DEFESA": "Documentos de defesa",
    "RELATORIO_ANALISE_DEFESA": "Relatório de análise de defesa",
    "DESPACHO": "Despacho",
    "PARECER": "Parecer",
    "COTA": "Cota",
    "MANIFESTACAO": "Manifestação complementar",
    "VOTO": "Voto",
    "DECISAO": "Decisão",
    "ACORDAO": "Acórdão",
    "CERTIDAO": "Certidão",
    "OUTROS": "Outros documentos relevantes",
}
PREPARATION = (
    "IDEIA",
    "PESQUISA",
    "ELABORACAO",
    "MINUTA_REVISAO",
    "APROVADA",
    "AGUARDANDO_PROTOCOLO",
    "ARQUIVADA",
    "SUSPENSA",
    "CANCELADA",
)
PROTOCOLLED = ("PROTOCOLADA", "EM_TRAMITACAO", "JULGADA", "ENCERRADA")
KIND_PROJECT = "Projeto de Representação"
KIND_REPRESENTATION = "Representação"
RELATORES_TITULARES = (
    "Alanna Camilla Santos Galdino Vieira",
    "André Carlo Torres Pontes",
    "Antônio Gomes Vieira Filho",
    "Arnóbio Alves Viana",
    "Deusdete Queiroga Filho",
    "Fábio Túlio Filgueiras Nogueira",
    "Taciano Luis Barbosa Diniz",
)
RELATORES_SUBSTITUTOS = (
    "Marcus Vinícius Carvalho Farias",
    "Renato Sérgio Santiago Melo",
)
RELATORES = RELATORES_TITULARES + RELATORES_SUBSTITUTOS
# Lotação institucional em servidores.setor (códigos da planilha/cadastro).
LOTACOES_ASSESSORES = frozenset(
    {"PROGE", "ESPO", "BTLC", "LAF", "MTFF", "SBBQ", "MASN"}
)
_SETOR_TOKEN = re.compile(r"[A-Za-z]+")


def label(mapping, key, fallback="—"):
    if not key:
        return fallback
    return mapping.get(key, key)


def actor_of(principal):
    return getattr(principal, "email", None) or str(principal)


def _audit(store, principal, evento, acao, record=None, extra=None, entidade_id=None):
    from services.audit import registrar_evento

    detalhes = dict(extra or {})
    identifier = entidade_id
    if record:
        identifier = record.get("id") if identifier is None else identifier
        if record.get("titulo"):
            detalhes.setdefault("titulo", str(record["titulo"])[:80])
        if record.get("numero_processo"):
            detalhes.setdefault("numero_processo", record["numero_processo"])
        if record.get("situacao"):
            detalhes.setdefault("situacao", record["situacao"])
    registrar_evento(
        store,
        evento=evento,
        modulo="representacoes",
        acao=acao,
        principal=principal,
        entidade_tipo="representacao",
        entidade_id=identifier,
        detalhes=detalhes or None,
    )


def is_protocolled(record):
    return bool((record or {}).get("numero_processo"))


def kind_label(record):
    return KIND_REPRESENTATION if is_protocolled(record) else KIND_PROJECT


def kind_saved_message(record):
    if is_protocolled(record):
        return "Representação salva."
    return "Projeto de Representação salvo."


def relator_label(name):
    if name in RELATORES_SUBSTITUTOS:
        return name + " — Conselheiro Substituto"
    return name or ""


def andamento_display(item):
    heading = label(ANDAMENTOS, item.get("tipo"), item.get("tipo") or "")
    descricao = (item.get("descricao") or "").strip()
    if item.get("tipo") == "CRIADA" or not descricao:
        return heading, ""
    if descricao.rstrip(".") == heading.rstrip("."):
        return heading, ""
    return heading, descricao


def open_store(store):
    return RepresentacoesStore(store)


def procuradores(store):
    return [row for row in store.catalog("procuradores") if row.get("ativo")]


def setor_elegivel(value):
    tokens = {token.upper() for token in _SETOR_TOKEN.findall(value or "")}
    return bool(tokens & LOTACOES_ASSESSORES)


def assessores(store):
    return [
        row
        for row in MemorandosStore(store).servers()
        if setor_elegivel(row.get("setor"))
    ]


def signatory_options(procurador_ids, responsible_id):
    return [identifier for identifier in procurador_ids if identifier != responsible_id]


def reconcile_signatories(selected, allowed):
    allowed_set = set(allowed)
    return [identifier for identifier in (selected or []) if identifier in allowed_set]


def people_index(store):
    procuradores_map = {row["id"]: row["nome"] for row in store.catalog("procuradores")}
    assessores_map = {row["id"]: row["nome"] for row in MemorandosStore(store).all_servers(include_inactive=True)}
    return procuradores_map, assessores_map


def people_context(store):
    """Load active filter options and historical display names in two reads."""
    people = store.catalog("procuradores")
    server_rows = MemorandosStore(store).all_servers(include_inactive=True)
    active_servers = [row for row in server_rows if row.get("ativo")][:500]
    return (
        {row["id"]: row["nome"] for row in people if row.get("ativo")},
        {
            row["id"]: row["nome"]
            for row in active_servers
            if setor_elegivel(row.get("setor"))
        },
        {row["id"]: row["nome"] for row in people},
        {row["id"]: row["nome"] for row in server_rows},
    )


def member_name(member, procuradores_map, assessores_map):
    source = procuradores_map if member["membro_tipo"] == "PROCURADOR" else assessores_map
    return source.get(member["membro_id"]) or "—"


def grouped_members(record, procuradores_map, assessores_map):
    groups = {papel: [] for papel in PAPEIS}
    for member in record.get("integrantes") or []:
        groups.setdefault(member["papel"], []).append(
            member_name(member, procuradores_map, assessores_map)
        )
    return groups


def can_delete(record):
    return delete_blocked_reason(record) is None


def delete_blocked_reason(record):
    if is_protocolled(record) or record.get("numero_processo"):
        return (
            "Esta Representação não pode ser excluída definitivamente porque já foi "
            "protocolada. Utilize encerramento, cancelamento ou arquivamento."
        )
    if record.get("situacao") in PROTOCOLLED:
        return (
            "Esta Representação não pode ser excluída definitivamente porque já se "
            "encontra em tramitação. Utilize encerramento, cancelamento ou arquivamento."
        )
    return None


def _members(payload):
    responsible = payload.get("procurador_responsavel")
    if not responsible:
        raise ValueError("Informe o Procurador responsável.")
    members = [
        {
            "membro_tipo": "PROCURADOR",
            "membro_id": int(responsible),
            "papel": "PROCURADOR_RESPONSAVEL",
        }
    ]
    seen_signatories = set()
    responsible_id = int(responsible)
    for identifier in payload.get("procuradores_signatarios") or []:
        identifier = int(identifier)
        if identifier == responsible_id or identifier in seen_signatories:
            continue
        seen_signatories.add(identifier)
        members.append(
            {
                "membro_tipo": "PROCURADOR",
                "membro_id": identifier,
                "papel": "PROCURADOR_SIGNATARIO",
            }
        )
    seen_assessors = set()
    for identifier in payload.get("assessores") or []:
        identifier = int(identifier)
        if identifier in seen_assessors:
            continue
        seen_assessors.add(identifier)
        members.append(
            {
                "membro_tipo": "SERVIDOR",
                "membro_id": identifier,
                "papel": "ASSESSOR",
            }
        )
    return members


def _validate_people(store, members):
    active_procuradores = {row["id"] for row in procuradores(store)}
    active_assessores = {row["id"] for row in assessores(store)}
    for member in members:
        if member["papel"] == "ASSESSOR":
            if member["membro_id"] not in active_assessores:
                raise ValueError("Assessor inválido ou inativo.")
        elif member["membro_id"] not in active_procuradores:
            raise ValueError("Procurador inválido ou inativo.")


def _payload(values, *, creating=False):
    title = (values.get("titulo") or "").strip()
    if not title:
        raise ValueError("Informe o título.")
    origem = values.get("origem") or "DE_OFICIO"
    if origem not in ORIGENS:
        raise ValueError("Origem inválida.")
    prioridade = values.get("prioridade") or "NORMAL"
    if prioridade not in PRIORIDADES:
        raise ValueError("Prioridade inválida.")
    opening = values.get("data_abertura") or date.today().isoformat()
    situacao = values.get("situacao") or ("IDEIA" if creating else None)
    if situacao and situacao not in SITUACOES:
        raise ValueError("Situação inválida.")
    fase = values.get("fase_processual") or None
    if fase and fase not in FASES:
        raise ValueError("Fase processual inválida.")
    record = {
        "titulo": title,
        "objeto": (values.get("objeto") or "").strip(),
        "origem": origem,
        "data_abertura": opening,
        "representado": (values.get("representado") or "").strip(),
        "tema": (values.get("tema") or "").strip(),
        "prioridade": prioridade,
        "observacoes": (values.get("observacoes") or "").strip(),
    }
    if situacao:
        record["situacao"] = situacao
    if fase:
        record["fase_processual"] = fase
    return record


def create(store, values, principal):
    record = _payload(values, creating=True)
    members = _members(values)
    _validate_people(store, members)
    created = open_store(store).create(record, members, actor_of(principal))
    _audit(store, principal, "REPRESENTACAO_CRIADA", "CRIAR", created)
    return created


def update(store, identifier, values, principal):
    current = get(store, identifier)
    if current is None:
        raise ValueError("Representação não encontrada.")
    record = _payload(values)
    members = _members(values)
    _validate_people(store, members)
    updated = open_store(store).update(identifier, record, members, actor_of(principal))
    from services.audit import format_changes

    changes = format_changes(
        current,
        updated,
        {
            "titulo": "Título",
            "situacao": "Situação",
            "prioridade": "Prioridade",
            "fase_processual": "Fase",
            "numero_processo": "Número do processo",
            "origem": "Origem",
            "representado": "Representado",
            "tema": "Tema",
        },
    )
    _audit(
        store,
        principal,
        "REPRESENTACAO_ALTERADA",
        "EDITAR",
        updated,
        {"alteracoes": changes} if changes else None,
    )
    old_responsible = next(
        (
            member["membro_id"]
            for member in current.get("integrantes") or ()
            if member["papel"] == "PROCURADOR_RESPONSAVEL"
        ),
        None,
    )
    new_responsible = next(
        (
            member["membro_id"]
            for member in updated.get("integrantes") or ()
            if member["papel"] == "PROCURADOR_RESPONSAVEL"
        ),
        None,
    )
    if old_responsible != new_responsible:
        from database.record_engagement import RecordEngagementStore

        try:
            RecordEngagementStore(store).emit(
                "representacao",
                identifier,
                principal.id,
                f"representacao:{identifier}:responsavel:{updated['atualizado_em']}",
                "A Representação seguida mudou de responsável.",
            )
        except Exception:
            LOGGER.exception("Falha ao avisar seguidores sobre mudança de responsável.")
    return updated


def get(store, identifier):
    return open_store(store).get(identifier)


def list_records(store, filters=None, limit=50, offset=0):
    return open_store(store).list(filters, limit=limit, offset=offset)


def overview(store):
    return open_store(store).overview()


def progress(store, identifier):
    return open_store(store).progress(identifier)


def documents(store, identifier):
    return open_store(store).documents(identifier)


def download(store, file_id):
    return open_store(store).download(file_id)


def add_progress(store, identifier, values, principal):
    if get(store, identifier) is None:
        raise ValueError("Representação não encontrada.")
    tipo = values.get("tipo") or "LIVRE"
    if tipo not in ANDAMENTOS:
        raise ValueError("Tipo de andamento inválido.")
    descricao = (values.get("descricao") or "").strip()
    if tipo == "LIVRE" and not descricao:
        raise ValueError("Descreva o andamento.")
    if not descricao:
        descricao = ANDAMENTOS[tipo] + "."
    day = values.get("data") or date.today().isoformat()
    return open_store(store).add_progress(
        identifier, day, tipo, descricao, actor_of(principal)
    )


def add_document(store, identifier, values, name, content, principal):
    if get(store, identifier) is None:
        raise ValueError("Representação não encontrada.")
    tipo = values.get("tipo_documento") or "OUTROS"
    if tipo not in DOCUMENTOS:
        raise ValueError("Tipo de documento inválido.")
    safe, mime = validate_upload(name, content)
    saved = open_store(store).add_document(
        identifier,
        {
            "andamento_id": values.get("andamento_id"),
            "tipo_documento": tipo,
            "descricao": (values.get("descricao") or "").strip(),
            "data_documento": values.get("data_documento") or date.today().isoformat(),
            "nome_arquivo": safe,
            "mime_type": mime,
        },
        content,
        actor_of(principal),
    )
    record = get(store, identifier)
    _audit(
        store,
        principal,
        "DOCUMENTO_ANEXADO",
        "IMPORTAR",
        record,
        {"arquivo": safe, "formato": mime},
    )
    return saved


def register_protocol(store, identifier, values, principal, upload=None):
    from services.access import require_permission

    require_permission(principal, "representacoes_registrar_protocolo")
    current = get(store, identifier)
    if current is None:
        raise ValueError("Representação não encontrada.")
    if current.get("numero_processo"):
        raise ValueError("Esta representação já possui protocolo.")
    number = (values.get("numero_processo") or "").strip()
    relator = (values.get("relator") or "").strip()
    if not number:
        raise ValueError("Informe o número do processo atribuído pelo TRAMITA.")
    if relator not in RELATORES:
        raise ValueError("Informe o Relator atribuído pelo TRAMITA.")
    day = values.get("data_protocolo") or date.today().isoformat()
    fase = values.get("fase_processual") or "INSTRUCAO"
    if fase not in FASES:
        raise ValueError("Fase processual inválida.")
    cautelar = bool(values.get("possui_medida_cautelar"))
    file_tuple = None
    if upload:
        name, content = upload
        safe, mime = validate_upload(name, content)
        if mime != "application/pdf":
            raise ValueError("O documento final da Representação deve ser PDF.")
        file_tuple = (safe, mime, content)
    protocolled = open_store(store).register_protocol(
        identifier,
        {
            "numero_processo": number,
            "data_protocolo": day,
            "relator": relator,
            "fase_processual": fase,
            "observacao": (values.get("observacoes") or "").strip(),
            "possui_medida_cautelar": cautelar,
        },
        actor_of(principal),
        file_tuple,
    )
    _audit(
        store,
        principal,
        "REPRESENTACAO_PROTOCOLADA",
        "FINALIZAR",
        protocolled,
        {
            "alteracoes": [
                "Situação: "
                + label(SITUACOES, current.get("situacao"))
                + " → "
                + label(SITUACOES, protocolled.get("situacao"))
            ],
            "numero_processo": number,
        },
    )
    return protocolled


def set_status(store, identifier, situacao, principal, fase=None, note=""):
    current = get(store, identifier)
    if current is None:
        raise ValueError("Representação não encontrada.")
    if situacao not in SITUACOES:
        raise ValueError("Situação inválida.")
    if fase is None:
        fase = current.get("fase_processual")
    if fase and fase not in FASES:
        raise ValueError("Fase processual inválida.")
    if current.get("numero_processo") and situacao in (
        "IDEIA",
        "PESQUISA",
        "ELABORACAO",
        "MINUTA_REVISAO",
        "APROVADA",
        "AGUARDANDO_PROTOCOLO",
        "ARQUIVADA",
    ):
        raise ValueError("Após o protocolo, utilize a situação processual correspondente.")
    text = note or ("Situação alterada para " + SITUACOES[situacao] + ".")
    updated = open_store(store).set_status(
        identifier, situacao, fase, actor_of(principal), text
    )
    _audit(
        store,
        principal,
        "REPRESENTACAO_STATUS_ALTERADO",
        "MOVIMENTAR",
        updated,
        {
            "alteracoes": [
                "Situação: "
                + label(SITUACOES, current.get("situacao"))
                + " → "
                + label(SITUACOES, situacao)
            ]
        },
    )
    return updated


def set_phase(store, identifier, fase, principal):
    current = get(store, identifier)
    if current is None:
        raise ValueError("Representação não encontrada.")
    if not current.get("numero_processo"):
        raise ValueError("A fase processual só se aplica após o protocolo.")
    if fase not in FASES:
        raise ValueError("Fase processual inválida.")
    situacao = current["situacao"]
    if fase in ("JULGAMENTO", "POS_JULGAMENTO") and situacao == "PROTOCOLADA":
        situacao = "EM_TRAMITACAO"
    if fase == "JULGAMENTO":
        situacao = "EM_TRAMITACAO"
    note = "Fase processual: " + FASES[fase] + "."
    updated = open_store(store).set_status(
        identifier, situacao, fase, actor_of(principal), note
    )
    _audit(
        store,
        principal,
        "REPRESENTACAO_FASE_ALTERADA",
        "MOVIMENTAR",
        updated,
        {
            "alteracoes": [
                "Fase: "
                + label(FASES, current.get("fase_processual"))
                + " → "
                + label(FASES, fase)
            ]
        },
    )
    return updated


def delete(store, identifier, principal=None):
    current = get(store, identifier)
    if current is None:
        raise ValueError("Representação não encontrada.")
    if not can_delete(current):
        raise ValueError(
            "Representação protocolada não pode ser excluída. Utilize encerramento, cancelamento ou arquivamento."
        )
    removed = open_store(store).delete(identifier, actor_of(principal) if principal else "")
    _audit(
        store,
        principal,
        "REPRESENTACAO_EXCLUIDA",
        "EXCLUIR",
        current,
        {"titulo": (current.get("titulo") or "")[:80]},
    )
    return removed
