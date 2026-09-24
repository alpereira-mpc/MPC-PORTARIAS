"""Business rules for Ouvidoria. No Streamlit and no BLOB listing."""

from datetime import date
import logging

from database.institutional import INITIAL
from database.ouvidoria import OuvidoriaStore
from services.access import require_permission
from services.oficios import validate_upload
from services.representacoes import (
    assessores as representacao_assessores,
    create as create_representacao,
    procuradores,
)

LOGGER = logging.getLogger("mpc.ouvidoria")

OUVIDOR_NOME = INITIAL["OUVIDOR"]
TIPOS = {
    "DENUNCIA": "Denúncia",
    "NOTICIA_FATO": "Notícia de fato",
    "OFICIO": "Ofício",
    "COMUNICACAO": "Comunicação",
    "OUTRO": "Outro",
}
FORMAS = {
    "EMAIL": "E-mail",
    "PRESENCIAL": "Presencial",
    "TRAMITA": "TRAMITA",
    "OFICIO": "Ofício",
    "CORRESPONDENCIA": "Correspondência",
    "OUTRO": "Outro",
}
CLASSIFICACOES = {"RESTRITA": "Restrita", "PUBLICA": "Pública"}
PRIORIDADES = {
    "BAIXA": "Baixa",
    "NORMAL": "Normal",
    "ALTA": "Alta",
    "URGENTE": "Urgente",
}
SITUACOES = {
    "RECEBIDA": "Recebida",
    "TRIAGEM": "Em triagem",
    "ANALISE": "Em análise",
    "AGUARDANDO_PROVIDENCIA": "Aguardando providência",
    "PROVIDENCIA_ANDAMENTO": "Providência em andamento",
    "ENCERRADA": "Encerrada",
    "ARQUIVADA": "Arquivada",
}
RESULTADOS = {
    "ARQUIVAMENTO": "Arquivamento",
    "PETICAO_PRESIDENCIA": "Petição à Presidência",
    "PROJETO_REPRESENTACAO": "Projeto de Representação",
    "ENCAMINHAMENTO_INTERNO": "Encaminhamento interno",
    "SOLICITACAO_INFORMACAO": "Solicitação de informação",
    "OUTRA": "Outra providência",
}
PAPEIS = {
    "OUVIDOR": "Ouvidor",
    "PROCURADOR_RESPONSAVEL": "Procurador responsável",
    "PROCURADOR_PARTICIPANTE": "Procurador participante",
    "ASSESSOR": "Assessor",
}
ANDAMENTOS = {
    "CRIADA": "Notícia de fato recebida pela Ouvidoria",
    "ENCAMINHADA_OUVIDOR": "Encaminhada ao Ouvidor",
    "PROCURADOR_INCLUIDO": "Procurador incluído",
    "ASSESSOR_INCLUIDO": "Assessor incluído",
    "RESPONSAVEL_ALTERADO": "Responsável alterado",
    "PROVIDENCIA": "Providência registrada",
    "PROJETO_REPRESENTACAO": "Projeto de Representação criado",
    "PROJETO_REPRESENTACAO_EXCLUIDO": "Projeto de Representação excluído.",
    "ARQUIVADA": "Notícia de fato arquivada",
    "ENCERRADA": "Notícia de fato encerrada",
    "LIVRE": "Andamento livre",
}
DELETE_ALLOWED_ANDAMENTOS = frozenset(
    {
        "CRIADA",
        "ENCAMINHADA_OUVIDOR",
        "PROJETO_REPRESENTACAO",
        "PROJETO_REPRESENTACAO_EXCLUIDO",
    }
)
PROVIDENCIAS = {
    "ARQUIVAMENTO": "Arquivamento",
    "PETICAO_PRESIDENCIA": "Petição à Presidência",
    "SOLICITACAO_INFORMACAO": "Solicitação de informação",
    "ENCAMINHAMENTO_INTERNO": "Encaminhamento interno",
    "PROJETO_REPRESENTACAO": "Criação de Projeto de Representação",
    "OUTRA": "Outra",
}
DOCUMENTOS_RECEBIDOS = {
    "DOCUMENTO_RECEBIDO": "Documento recebido",
    "DOCUMENTO_PRINCIPAL": "Documento principal",
    "DENUNCIA": "Denúncia",
    "OFICIO": "Ofício",
    "EMAIL_PDF": "E-mail",
    "ANEXO_MANIFESTANTE": "Anexo",
    "OUTROS": "Outro",
}
DOCUMENTOS = {
    "DOCUMENTO_RECEBIDO": "Documento recebido",
    "DOCUMENTO_PRINCIPAL": "Documento principal",
    "DENUNCIA": "Denúncia",
    "NOTICIA_FATO": "Notícia de fato",
    "OFICIO": "Ofício",
    "EMAIL_PDF": "E-mail convertido em PDF",
    "DOCUMENTO_ORIGINAL": "Documento original",
    "ANEXO_MANIFESTANTE": "Anexos do manifestante",
    "PESQUISA": "Pesquisa",
    "NOTA": "Nota",
    "COMPLEMENTAR": "Documento complementar",
    "INFORMACAO_TECNICA": "Informação técnica",
    "PETICAO": "Petição à Presidência",
    "SOLICITACAO": "Solicitação",
    "RESPOSTA": "Resposta",
    "DESPACHO": "Despacho",
    "ARQUIVAMENTO": "Decisão de arquivamento",
    "PREPARATORIO": "Documento preparatório",
    "OUTROS": "Outros",
}


def label(mapping, key, fallback="—"):
    if not key:
        return fallback
    return mapping.get(key, key)


def tipo_visivel(tipo):
    if tipo == "NOTICIA_FATO":
        return None
    return label(TIPOS, tipo)


def actor_of(principal):
    return getattr(principal, "email", None) or str(principal)


def _audit(store, principal, evento, acao, record=None, extra=None, entidade_id=None):
    from services.audit import registrar_evento

    detalhes = dict(extra or {})
    identifier = entidade_id
    if record:
        identifier = record.get("id") if identifier is None else identifier
        if record.get("numero_interno"):
            detalhes.setdefault("titulo", record["numero_interno"])
        elif record.get("titulo"):
            detalhes.setdefault("titulo", str(record["titulo"])[:80])
        if record.get("situacao"):
            detalhes.setdefault("situacao", record["situacao"])
    registrar_evento(
        store,
        evento=evento,
        modulo="ouvidoria",
        acao=acao,
        principal=principal,
        entidade_tipo="noticia_fato",
        entidade_id=identifier,
        detalhes=detalhes or None,
    )


def open_store(store):
    return OuvidoriaStore(store)


def assessores(store):
    return representacao_assessores(store)


def default_ouvidor_id(store):
    wanted = " ".join(OUVIDOR_NOME.casefold().split())
    for row in procuradores(store):
        if " ".join((row.get("nome") or "").casefold().split()) == wanted:
            return row["id"]
    return None


def andamento_display(item):
    heading = label(ANDAMENTOS, item.get("tipo"), item.get("tipo") or "")
    descricao = (item.get("descricao") or "").strip()
    if not descricao or descricao.rstrip(".") == heading.rstrip("."):
        return heading, ""
    return heading, descricao


def representation_open_label(representation):
    from services.representacoes import is_protocolled

    if representation and is_protocolled(representation):
        return "Abrir Representação"
    return "Abrir Projeto de Representação"


def grouped_members(record):
    groups = {papel: [] for papel in PAPEIS}
    for member in record.get("integrantes") or []:
        groups.setdefault(member["papel"], []).append(member)
    return groups


def _members(values):
    ouvidor = values.get("ouvidor_id")
    responsible = values.get("procurador_responsavel_id")
    if not ouvidor:
        raise ValueError("Informe o Ouvidor.")
    if not responsible:
        raise ValueError("Informe o Procurador responsável.")
    members = [
        {"membro_tipo": "PROCURADOR", "membro_id": int(ouvidor), "papel": "OUVIDOR"},
        {
            "membro_tipo": "PROCURADOR",
            "membro_id": int(responsible),
            "papel": "PROCURADOR_RESPONSAVEL",
        },
    ]
    seen = {int(ouvidor), int(responsible)}
    for identifier in values.get("procuradores_participantes") or []:
        identifier = int(identifier)
        if identifier in seen:
            continue
        seen.add(identifier)
        members.append(
            {
                "membro_tipo": "PROCURADOR",
                "membro_id": identifier,
                "papel": "PROCURADOR_PARTICIPANTE",
            }
        )
    helpers = set()
    for identifier in values.get("assessores") or []:
        identifier = int(identifier)
        if identifier in helpers:
            continue
        helpers.add(identifier)
        members.append(
            {"membro_tipo": "SERVIDOR", "membro_id": identifier, "papel": "ASSESSOR"}
        )
    return members


def _validate_people(store, members):
    active = {row["id"] for row in procuradores(store)}
    servers = {row["id"] for row in assessores(store)}
    for member in members:
        if member["papel"] == "ASSESSOR":
            if member["membro_id"] not in servers:
                raise ValueError("Assessor inválido ou inativo.")
        elif member["membro_id"] not in active:
            raise ValueError("Procurador inválido ou inativo.")


def _payload(values, *, creating=False):
    title = (values.get("titulo") or "").strip()
    if not title:
        raise ValueError("Informe o título/assunto.")
    tipo = values.get("tipo") or "DENUNCIA"
    if tipo not in TIPOS:
        raise ValueError("Tipo inválido.")
    forma = values.get("forma_recebimento") or "EMAIL"
    if forma not in FORMAS:
        raise ValueError("Forma de recebimento inválida.")
    classificacao = values.get("classificacao_acesso") or "RESTRITA"
    if classificacao not in CLASSIFICACOES:
        raise ValueError("Classificação de acesso inválida.")
    prioridade = values.get("prioridade") or "NORMAL"
    if prioridade not in PRIORIDADES:
        raise ValueError("Prioridade inválida.")
    situacao = values.get("situacao") or ("RECEBIDA" if creating else None)
    if situacao and situacao not in SITUACOES:
        raise ValueError("Situação inválida.")
    resultado = values.get("resultado") or None
    if resultado and resultado not in RESULTADOS:
        raise ValueError("Resultado inválido.")
    identified = bool(values.get("manifestante_identificado"))
    record = {
        "tipo": tipo,
        "forma_recebimento": forma,
        "data_recebimento": values.get("data_recebimento") or date.today().isoformat(),
        "titulo": title,
        "resumo": (values.get("resumo") or "").strip(),
        "representado": (values.get("representado") or "").strip(),
        "tema": (values.get("tema") or "").strip(),
        "classificacao_acesso": classificacao,
        "prioridade": prioridade,
        "conclusao_analise": (values.get("conclusao_analise") or "").strip(),
        "manifestante_identificado": identified,
        "manifestante_nome": (values.get("manifestante_nome") or "").strip() if identified else "",
        "manifestante_email": (values.get("manifestante_email") or "").strip() if identified else "",
        "manifestante_telefone": (values.get("manifestante_telefone") or "").strip() if identified else "",
        "observacoes": (values.get("observacoes") or "").strip(),
        "procurador_responsavel_id": int(values["procurador_responsavel_id"]),
    }
    if situacao:
        record["situacao"] = situacao
    if resultado:
        record["resultado"] = resultado
    elif "resultado" in values:
        record["resultado"] = None
    return record


def _prepare_uploads(uploads, *, tipo=None, descricao="", data_documento=None):
    prepared = []
    for item in uploads or []:
        if isinstance(item, dict):
            name = item.get("nome")
            content = item.get("conteudo")
            kind = item.get("tipo_documento") or tipo or "DOCUMENTO_RECEBIDO"
            note = item.get("descricao") if item.get("descricao") is not None else descricao
            day = item.get("data_documento") or data_documento
        else:
            name, content = item
            kind = tipo or "DOCUMENTO_RECEBIDO"
            note = descricao
            day = data_documento
        if kind not in DOCUMENTOS:
            raise ValueError("Tipo de documento inválido.")
        safe, mime = validate_upload(name, content)
        prepared.append(
            (
                {
                    "tipo_documento": kind,
                    "descricao": (note or "").strip(),
                    "data_documento": day or date.today().isoformat(),
                    "nome_arquivo": safe,
                    "mime_type": mime,
                },
                content,
            )
        )
    return prepared


def create(store, values, principal, uploads=None):
    values = dict(values)
    if not values.get("ouvidor_id"):
        values["ouvidor_id"] = default_ouvidor_id(store) or values.get("procurador_responsavel_id")
    record = _payload(values, creating=True)
    members = _members(values)
    _validate_people(store, members)
    files = _prepare_uploads(
        uploads,
        tipo=values.get("documento_tipo") or "DOCUMENTO_RECEBIDO",
        data_documento=record["data_recebimento"],
    )
    created = open_store(store).create(record, members, actor_of(principal), files)
    _audit(store, principal, "NOTICIA_FATO_CRIADA", "CRIAR", created)
    return created


def update(store, identifier, values, principal, *, notify_status=True):
    current = get(store, identifier)
    if current is None:
        raise ValueError("Notícia de fato não encontrada.")
    if not values.get("ouvidor_id"):
        values = dict(values)
        values["ouvidor_id"] = default_ouvidor_id(store) or values.get("procurador_responsavel_id")
    record = _payload(values)
    members = _members(values)
    _validate_people(store, members)
    note = None
    old = current.get("procurador_responsavel_id")
    new = record.get("procurador_responsavel_id")
    if old and new and int(old) != int(new):
        names = {row["id"]: row["nome"] for row in procuradores(store)}
        note = (
            "Responsabilidade transferida de "
            + (names.get(int(old)) or "—")
            + " para "
            + (names.get(int(new)) or "—")
            + "."
        )
    updated = open_store(store).update(identifier, record, members, actor_of(principal), note)
    from services.audit import format_changes

    changes = format_changes(
        current,
        updated,
        {
            "titulo": "Título",
            "situacao": "Situação",
            "prioridade": "Prioridade",
            "classificacao_acesso": "Classificação",
            "procurador_responsavel_id": "Responsável",
            "resultado": "Resultado",
        },
    )
    _audit(
        store,
        principal,
        "NOTICIA_FATO_ALTERADA",
        "EDITAR",
        updated,
        {"alteracoes": changes} if changes else None,
    )
    if old and new and int(old) != int(new):
        from database.record_engagement import RecordEngagementStore

        try:
            RecordEngagementStore(store).emit(
                "ouvidoria",
                identifier,
                principal.id,
                f"ouvidoria:{identifier}:responsavel:{updated['atualizado_em']}",
                "A Notícia de Fato seguida mudou de responsável.",
            )
        except Exception:
            LOGGER.exception("Falha ao avisar seguidores sobre mudança de responsável.")
    if notify_status and current.get("situacao") != updated.get("situacao"):
        from database.record_engagement import RecordEngagementStore

        notices = {
            "ENCERRADA": "A Notícia de Fato seguida foi encerrada.",
            "ARQUIVADA": "A Notícia de Fato seguida foi arquivada.",
        }
        try:
            RecordEngagementStore(store).emit(
                "ouvidoria",
                identifier,
                principal.id,
                f"ouvidoria:{identifier}:situacao:{updated['atualizado_em']}",
                notices.get(
                    updated.get("situacao"),
                    "A Notícia de Fato seguida teve a situação atualizada.",
                ),
            )
        except Exception:
            LOGGER.exception("Falha ao avisar seguidores sobre mudança de situação.")
    return updated


def get(store, identifier):
    return open_store(store).get(identifier)


def by_representation(store, representacao_id):
    if not representacao_id:
        return None
    return open_store(store).by_representation(representacao_id)


def list_records(store, filters=None, limit=50, offset=0):
    return open_store(store).list(filters, limit=limit, offset=offset)


def overview(store):
    return open_store(store).overview()


def progress(store, identifier):
    return open_store(store).progress(identifier)


def actions(store, identifier):
    return open_store(store).actions(identifier)


def documents(store, identifier):
    return open_store(store).documents(identifier)


def download(store, file_id, principal=None):
    if principal is not None:
        require_permission(principal, "ouvidoria")
    return open_store(store).download(file_id)


def add_progress(store, identifier, values, principal):
    if get(store, identifier) is None:
        raise ValueError("Notícia de fato não encontrada.")
    tipo = values.get("tipo") or "LIVRE"
    if tipo not in ANDAMENTOS:
        raise ValueError("Tipo de andamento inválido.")
    descricao = (values.get("descricao") or "").strip()
    if tipo == "LIVRE" and not descricao:
        raise ValueError("Descreva o andamento.")
    day = values.get("data") or date.today().isoformat()
    return open_store(store).add_progress(
        identifier, day, tipo, descricao, actor_of(principal)
    )


def add_action(store, identifier, values, principal):
    current = get(store, identifier)
    if current is None:
        raise ValueError("Notícia de fato não encontrada.")
    tipo = values.get("tipo")
    if tipo not in PROVIDENCIAS:
        raise ValueError("Tipo de providência inválido.")
    return open_store(store).add_action(
        identifier,
        {
            "tipo": tipo,
            "data": values.get("data") or date.today().isoformat(),
            "descricao": (values.get("descricao") or "").strip(),
            "responsavel_id": values.get("responsavel_id") or current.get("procurador_responsavel_id"),
            "status": values.get("status") or "REGISTRADA",
        },
        actor_of(principal),
    )


def update_action(store, identifier, action_id, values, principal):
    current = get(store, identifier)
    if current is None:
        raise ValueError("Notícia de fato não encontrada.")
    tipo = values.get("tipo")
    if tipo not in PROVIDENCIAS:
        raise ValueError("Tipo de providência inválido.")
    return open_store(store).update_action(
        identifier,
        action_id,
        {
            "tipo": tipo,
            "data": values.get("data") or date.today().isoformat(),
            "descricao": (values.get("descricao") or "").strip(),
            "responsavel_id": values.get("responsavel_id") or current.get("procurador_responsavel_id"),
            "status": values.get("status") or "REGISTRADA",
        },
        actor_of(principal),
    )


def add_document(store, identifier, values, name, content, principal):
    if get(store, identifier) is None:
        raise ValueError("Notícia de fato não encontrada.")
    tipo = values.get("tipo_documento") or "OUTROS"
    if tipo not in DOCUMENTOS:
        raise ValueError("Tipo de documento inválido.")
    safe, mime = validate_upload(name, content)
    saved = open_store(store).add_document(
        identifier,
        {
            "providencia_id": values.get("providencia_id"),
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
    _audit(
        store,
        principal,
        "DOCUMENTO_ANEXADO",
        "IMPORTAR",
        get(store, identifier),
        {"arquivo": safe, "formato": mime},
    )
    return saved


def close(store, identifier, situacao, principal, resultado=None, conclusao=""):
    current = get(store, identifier)
    if current is None:
        raise ValueError("Notícia de fato não encontrada.")
    if situacao not in ("ENCERRADA", "ARQUIVADA"):
        raise ValueError("Situação de encerramento inválida.")
    values = {
        "tipo": current["tipo"],
        "forma_recebimento": current["forma_recebimento"],
        "data_recebimento": current["data_recebimento"],
        "titulo": current["titulo"],
        "resumo": current.get("resumo"),
        "representado": current.get("representado"),
        "tema": current.get("tema"),
        "classificacao_acesso": current["classificacao_acesso"],
        "prioridade": current["prioridade"],
        "situacao": situacao,
        "resultado": resultado or current.get("resultado") or ("ARQUIVAMENTO" if situacao == "ARQUIVADA" else None),
        "conclusao_analise": conclusao or current.get("conclusao_analise"),
        "manifestante_identificado": current.get("manifestante_identificado"),
        "manifestante_nome": current.get("manifestante_nome"),
        "manifestante_email": current.get("manifestante_email"),
        "manifestante_telefone": current.get("manifestante_telefone"),
        "observacoes": current.get("observacoes"),
        "procurador_responsavel_id": current.get("procurador_responsavel_id"),
        "ouvidor_id": next(
            (m["membro_id"] for m in current.get("integrantes") or [] if m["papel"] == "OUVIDOR"),
            current.get("procurador_responsavel_id"),
        ),
        "procuradores_participantes": [
            m["membro_id"]
            for m in current.get("integrantes") or []
            if m["papel"] == "PROCURADOR_PARTICIPANTE"
        ],
        "assessores": [
            m["membro_id"] for m in current.get("integrantes") or [] if m["papel"] == "ASSESSOR"
        ],
    }
    updated = update(store, identifier, values, principal, notify_status=False)
    tipo = "ARQUIVADA" if situacao == "ARQUIVADA" else "ENCERRADA"
    add_progress(store, identifier, {"tipo": tipo, "data": date.today().isoformat()}, principal)
    _audit(
        store,
        principal,
        "NOTICIA_FATO_ARQUIVADA" if situacao == "ARQUIVADA" else "NOTICIA_FATO_ENCERRADA",
        "FINALIZAR",
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


def create_projeto_representacao(store, identifier, principal, overrides=None):
    require_permission(principal, "ouvidoria")
    require_permission(principal, "representacoes")
    current = get(store, identifier)
    if current is None:
        raise ValueError("Notícia de fato não encontrada.")
    if current.get("representacao_id"):
        raise ValueError("Já existe Projeto de Representação vinculado a esta notícia de fato.")
    extras = overrides or {}
    participants = [
        m["membro_id"]
        for m in current.get("integrantes") or []
        if m["papel"] in ("PROCURADOR_PARTICIPANTE", "OUVIDOR")
        and m["membro_id"] != current.get("procurador_responsavel_id")
    ]
    helpers = [m["membro_id"] for m in current.get("integrantes") or [] if m["papel"] == "ASSESSOR"]
    created = create_representacao(
        store,
        {
            "titulo": extras.get("titulo") or current["titulo"],
            "objeto": extras.get("objeto") or current.get("resumo") or "",
            "origem": "OUVIDORIA",
            "data_abertura": extras.get("data_abertura") or date.today().isoformat(),
            "representado": extras.get("representado") or current.get("representado") or "",
            "tema": extras.get("tema") or current.get("tema") or "",
            "prioridade": extras.get("prioridade") or current.get("prioridade") or "NORMAL",
            "observacoes": extras.get("observacoes") or ("Origem: " + current["numero_interno"]),
            "procurador_responsavel": extras.get("procurador_responsavel")
            or current.get("procurador_responsavel_id"),
            "procuradores_signatarios": extras.get("procuradores_signatarios") or participants,
            "assessores": extras.get("assessores") or helpers,
        },
        principal,
    )
    linked = open_store(store).link_representation(
        identifier, created["id"], actor_of(principal)
    )
    _audit(
        store,
        principal,
        "NOTICIA_VINCULADA_REPRESENTACAO",
        "ALTERAR",
        get(store, identifier),
        {"representacao_id": created["id"]},
    )
    return linked


def delete(store, identifier, principal=None):
    current = get(store, identifier)
    if current is None:
        raise ValueError("Notícia de fato não encontrada.")
    removed = open_store(store).delete(identifier)
    _audit(
        store,
        principal,
        "NOTICIA_FATO_EXCLUIDA",
        "EXCLUIR",
        current,
    )
    return removed


def can_delete(record, *, providencias=(), documentos=(), andamentos=()):
    return delete_blocked_reason(
        record,
        providencias=providencias,
        documentos=documentos,
        andamentos=andamentos,
    ) is None


def delete_blocked_reason(record, *, providencias=(), documentos=(), andamentos=()):
    if record.get("representacao_id"):
        return (
            "Esta notícia de fato não pode ser excluída definitivamente porque possui "
            "Representação vinculada."
        )
    if record.get("situacao") in ("ENCERRADA", "ARQUIVADA"):
        return (
            "Esta notícia de fato não pode ser excluída definitivamente porque já foi "
            "encerrada ou arquivada."
        )
    if providencias:
        return (
            "Esta notícia de fato não pode ser excluída definitivamente porque já possui "
            "providências registradas."
        )
    if documentos:
        return (
            "Esta notícia de fato não pode ser excluída definitivamente porque já possui "
            "documentos anexados."
        )
    if any((item.get("tipo") or "") not in DELETE_ALLOWED_ANDAMENTOS for item in andamentos):
        return (
            "Esta notícia de fato não pode ser excluída definitivamente porque já possui "
            "histórico de andamentos que deve ser preservado."
        )
    return None
