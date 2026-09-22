"""Central audit API. Failures never break the calling operation."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
import csv
import io
import json
import logging
import uuid

from database.audit import AuditStore, EXPORT_LIMIT
from database.store import now as utc_now, unwrap_store

INSTITUTIONAL_TZ = ZoneInfo("America/Recife")
LOGGER = logging.getLogger("mpc.audit")
FORBIDDEN_KEYS = {
    "password",
    "senha",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
    "cookie",
    "cookies",
    "secret",
    "secrets",
    "client_secret",
    "cookie_secret",
    "oauth",
    "authorization",
    "payload",
    "docx",
    "pdf",
    "xlsx",
    "bytes",
    "content",
    "conteudo",
    "corpo",
    "arquivo_bytes",
    "texto",
    "texto_extraido",
    "assunto",
    "observacoes",
    "conclusao",
    "conclusao_analise",
    "stack",
    "traceback",
    "trace",
    "exception",
    "manifestante_nome",
    "manifestante_email",
    "manifestante_telefone",
}
MAX_STRING = 200
MAX_KEYS = 24
MAX_SEARCH = 80
DASHBOARD_CACHE_SECONDS = 20
MODULE_LABELS = {
    "portarias": "Portarias",
    "agenda": "Agenda e Afastamentos",
    "oficios": "Ofícios",
    "memorandos": "Memorandos",
    "admin": "Administração",
    "pendencias": "Pendências",
    "alertas": "Alertas",
    "tarefas": "Tarefas",
    "relatorios": "Relatórios e Indicadores",
    "representacoes": "Representações",
    "ouvidoria": "Ouvidoria",
}
MODULE_KEYS = {
    "Portarias": "portarias",
    "Agenda": "agenda",
    "Ofícios": "oficios",
    "Memorandos": "memorandos",
    "Administração": "admin",
    "Pendências": "pendencias",
    "Alertas": "alertas",
    "Tarefas": "tarefas",
    "Relatórios e Indicadores": "relatorios",
    "Representações": "representacoes",
    "Ouvidoria": "ouvidoria",
}
ACTION_LABELS = {
    "ACESSO": "Login",
    "AUTORIZACAO": "Autorização",
    "ENTRAR": "Acesso ao módulo",
    "SAIR": "Logout",
    "CRIAR": "Criação",
    "CADASTRAR": "Cadastro",
    "EDITAR": "Alteração",
    "ALTERAR": "Alteração",
    "EXCLUIR": "Exclusão",
    "REMOVER": "Remoção",
    "CANCELAR": "Cancelamento",
    "FINALIZAR": "Finalização",
    "EXPORTAR": "Download",
    "IMPORTAR": "Importação",
    "BACKUP": "Backup",
    "PERMISSOES": "Alteração de permissão",
    "GABINETES": "Alteração de gabinete",
    "ATIVAR": "Reativação",
    "DESATIVAR": "Desativação",
    "PROMOVER": "Alteração de perfil",
    "REBAIXAR": "Alteração de perfil",
    "BLOQUEADO": "Operação recusada",
    "PREVIA": "Geração",
    "MOVIMENTAR": "Alteração de status",
    "ACESSAR": "Acesso",
    "REGISTRAR": "Registro",
    "CORRIGIR": "Alteração",
    "REABRIR": "Reabertura",
}
EVENT_LABELS = {
    "SESSAO_INICIADA": "Início de sessão",
    "ACESSO_AUTORIZADO": "Acesso autorizado",
    "ACESSO_NEGADO": "Acesso recusado",
    "USUARIO_INATIVO": "Acesso recusado (usuário inativo)",
    "LOGOUT": "Logout",
    "MODULO_ACESSADO": "Acesso ao módulo",
    "CENTRAL_PENDENCIAS_ACESSADA": "Acesso às Pendências",
    "ALERTAS_ACESSADOS": "Acesso aos Alertas",
    "PERMISSAO_NEGADA": "Permissão recusada",
    "USUARIO_CRIADO": "Usuário criado",
    "USUARIO_EDITADO": "Usuário alterado",
    "USUARIO_ATIVADO": "Usuário reativado",
    "USUARIO_DESATIVADO": "Usuário desativado",
    "USUARIO_PROMOVIDO": "Perfil alterado",
    "ADMINISTRADOR_REBAIXADO": "Perfil alterado",
    "PERMISSOES_ALTERADAS": "Permissões alteradas",
    "GABINETES_ALTERADOS": "Gabinetes alterados",
    "USUARIO_EXCLUIDO": "Usuário excluído",
    "ADMIN_PROTEGIDO_BLOQUEADO": "Alteração bloqueada (administrador protegido)",
    "ULTIMO_ADMIN_BLOQUEADO": "Alteração bloqueada (último administrador)",
    "FUNCAO_INSTITUCIONAL_ALTERADA": "Função institucional alterada",
    "SOLICITACAO_APROVADA": "Solicitação de acesso aprovada",
    "SOLICITACAO_RECUSADA": "Solicitação de acesso recusada",
    "SOLICITACAO_EXCLUIDA": "Solicitação de acesso excluída",
    "BACKUP_GERADO": "Backup gerado",
    "BACKUP_DISPONIBILIZADO": "Backup disponibilizado para download",
    "DOCUMENTO_BAIXADO": "Download de documento",
    "DOCUMENTO_ANEXADO": "Documento anexado",
    "ERRO_OPERACIONAL": "Falha operacional",
    "PORTARIA_FINALIZADA": "Portaria finalizada",
    "PORTARIA_CANCELADA": "Portaria cancelada",
    "PORTARIA_EXCLUIDA": "Portaria excluída",
    "RASCUNHO_EXCLUIDO": "Rascunho excluído",
    "PREVIA_GERADA": "Prévia gerada",
    "OFICIO_FINALIZADO": "Ofício finalizado",
    "OFICIO_RECEBIDO": "Ofício recebido cadastrado",
    "OFICIO_EXCLUIDO": "Ofício excluído",
    "NUMERO_LIBERADO": "Exclusão definitiva com número liberado",
    "MEMORANDO_FINALIZADO": "Memorando finalizado",
    "MEMORANDO_EXCLUIDO_DEFINITIVAMENTE": "Memorando excluído definitivamente",
    "REPRESENTACAO_CRIADA": "Representação criada",
    "REPRESENTACAO_ALTERADA": "Representação alterada",
    "REPRESENTACAO_EXCLUIDA": "Representação excluída",
    "REPRESENTACAO_PROTOCOLADA": "Representação protocolada",
    "REPRESENTACAO_STATUS_ALTERADO": "Situação da representação alterada",
    "REPRESENTACAO_FASE_ALTERADA": "Fase processual alterada",
    "NOTICIA_FATO_CRIADA": "Notícia de fato criada",
    "NOTICIA_FATO_ALTERADA": "Notícia de fato alterada",
    "NOTICIA_FATO_EXCLUIDA": "Notícia de fato excluída",
    "NOTICIA_FATO_ENCERRADA": "Notícia de fato encerrada",
    "NOTICIA_FATO_ARQUIVADA": "Notícia de fato arquivada",
    "NOTICIA_VINCULADA_REPRESENTACAO": "Projeto de Representação vinculado",
    "COMPROMISSO_CRIADO": "Compromisso criado",
    "COMPROMISSO_ALTERADO": "Compromisso alterado",
    "COMPROMISSO_CANCELADO": "Compromisso cancelado",
    "COMPROMISSO_EXCLUIDO": "Compromisso excluído",
    "AFASTAMENTO_CRIADO": "Afastamento criado",
    "AFASTAMENTO_EDITADO": "Afastamento alterado",
    "AFASTAMENTO_CANCELADO": "Afastamento cancelado",
}
ENTITY_LABELS = {
    "portaria": "Portaria",
    "oficio": "Ofício",
    "memorando": "Memorando",
    "usuario": "Usuário",
    "backup": "Backup",
    "representacao": "Representação",
    "noticia_fato": "Notícia de fato",
    "compromisso": "Compromisso",
    "afastamento": "Afastamento",
    "tarefa": "Tarefa",
    "funcao_institucional": "Função institucional",
    "solicitacao_acesso": "Solicitação de acesso",
    "documento": "Documento",
}
RESULT_LABELS = {
    "OK": "Sucesso",
    "ERRO": "Falha",
    "NEGADO": "Recusado",
    "FALHA": "Falha",
}
ACTION_TYPE_OPTIONS = (
    ("", "Todas"),
    ("login", "Login"),
    ("criacao", "Criação"),
    ("alteracao", "Alteração"),
    ("exclusao", "Exclusão"),
    ("finalizacao", "Finalização"),
    ("download", "Download"),
    ("upload", "Upload"),
    ("geracao", "Geração"),
    ("permissao", "Alteração de permissão"),
    ("backup", "Backup"),
    ("falha", "Falha"),
)
ACTION_TYPE_FILTERS = {
    "login": {
        "eventos": [
            "SESSAO_INICIADA",
            "ACESSO_AUTORIZADO",
            "ACESSO_NEGADO",
            "USUARIO_INATIVO",
            "LOGOUT",
        ]
    },
    "criacao": {"acoes": ["CRIAR", "CADASTRAR"]},
    "alteracao": {
        "acoes": [
            "ALTERAR",
            "EDITAR",
            "MOVIMENTAR",
            "PERMISSOES",
            "GABINETES",
            "ATIVAR",
            "DESATIVAR",
            "PROMOVER",
            "REBAIXAR",
            "CORRIGIR",
            "REGISTRAR",
            "REABRIR",
        ]
    },
    "exclusao": {"acoes": ["EXCLUIR", "REMOVER"]},
    "finalizacao": {"acoes": ["FINALIZAR"]},
    "download": {
        "eventos": ["DOCUMENTO_BAIXADO", "BACKUP_DISPONIBILIZADO"],
        "acoes": ["EXPORTAR"],
    },
    "upload": {"acoes": ["IMPORTAR"], "eventos": ["DOCUMENTO_ANEXADO"]},
    "geracao": {"acoes": ["PREVIA", "GERAR"], "eventos": ["PREVIA_GERADA", "BACKUP_GERADO"]},
    "permissao": {
        "eventos": [
            "PERMISSOES_ALTERADAS",
            "USUARIO_PROMOVIDO",
            "ADMINISTRADOR_REBAIXADO",
            "GABINETES_ALTERADOS",
            "FUNCAO_INSTITUCIONAL_ALTERADA",
        ]
    },
    "backup": {"acoes": ["BACKUP"], "eventos": ["BACKUP_GERADO", "BACKUP_DISPONIBILIZADO"]},
    "falha": {"resultados": ["ERRO", "NEGADO", "FALHA"]},
}
USER_FIELD_LABELS = {
    "ativo": "Situação",
    "perfil": "Perfil",
    "nome": "Nome",
    "pode_portarias": "Portarias",
    "pode_agenda": "Agenda",
    "pode_oficios": "Ofícios",
    "pode_memorandos": "Memorandos",
    "pode_admin": "Administração",
    "pode_representacoes": "Representações",
    "pode_ouvidoria": "Ouvidoria",
    "pode_relatorios": "Relatórios",
}


def format_local(value):
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return str(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(INSTITUTIONAL_TZ).strftime("%d/%m/%Y %H:%M:%S")


def format_local_short(value):
    if not value:
        return "—"
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return str(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(INSTITUTIONAL_TZ).strftime("%d/%m/%Y · %H:%M")


def module_label(code):
    return MODULE_LABELS.get(code, code or "—")


def action_label(code):
    return ACTION_LABELS.get(code, EVENT_LABELS.get(code, code or "—"))


def event_label(code):
    return EVENT_LABELS.get(code, action_label(code) if code else "—")


def result_label(code):
    return RESULT_LABELS.get(code, code or "—")


def entity_label(code):
    return ENTITY_LABELS.get(code, code or "—")


def _display_value(value):
    if value is True:
        return "Sim"
    if value is False:
        return "Não"
    if value is None or value == "":
        return "—"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value) or "—"
    return str(value)


def format_changes(before, after, fields):
    lines = []
    before = before or {}
    after = after or {}
    for key, label in fields.items():
        old, new = before.get(key), after.get(key)
        if old != new:
            lines.append(f"{label}: {_display_value(old)} → {_display_value(new)}")
    return lines


def apply_action_type(filters, tipo):
    data = dict(filters or {})
    extra = ACTION_TYPE_FILTERS.get(tipo) or {}
    data.update(extra)
    return data


def period_bounds(kind, start=None, end=None):
    local_now = datetime.now(INSTITUTIONAL_TZ)
    today = local_now.date()
    if kind == "hoje":
        begin = datetime.combine(today, time.min, INSTITUTIONAL_TZ)
        finish = datetime.combine(today, time.max, INSTITUTIONAL_TZ)
    elif kind == "7d":
        begin = datetime.combine(today - timedelta(days=6), time.min, INSTITUTIONAL_TZ)
        finish = datetime.combine(today, time.max, INSTITUTIONAL_TZ)
    elif kind == "30d":
        begin = datetime.combine(today - timedelta(days=29), time.min, INSTITUTIONAL_TZ)
        finish = datetime.combine(today, time.max, INSTITUTIONAL_TZ)
    elif kind == "personalizado":
        if start is None or end is None:
            raise ValueError("Informe o intervalo personalizado.")
        begin = datetime.combine(start, time.min, INSTITUTIONAL_TZ)
        finish = datetime.combine(end, time.max, INSTITUTIONAL_TZ)
        if finish < begin:
            raise ValueError("A data final deve ser igual ou posterior à inicial.")
    else:
        raise ValueError("Período inválido.")
    return begin.astimezone(timezone.utc).isoformat(), finish.astimezone(
        timezone.utc
    ).isoformat()


def sanitize_details(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    clean = {}
    for key, item in list(value.items())[:MAX_KEYS]:
        name = str(key)
        lowered = name.casefold()
        if lowered in FORBIDDEN_KEYS or any(
            part in lowered
            for part in (
                "token",
                "secret",
                "senha",
                "password",
                "cookie",
                "manifestante",
                "traceback",
            )
        ):
            continue
        if item is None or isinstance(item, bool):
            clean[name] = item
        elif isinstance(item, (int, float)) and not isinstance(item, bool):
            clean[name] = item
        elif isinstance(item, str):
            text = item.strip()
            if len(text) > MAX_STRING:
                text = text[:MAX_STRING]
            clean[name] = text
        elif isinstance(item, (list, tuple)):
            values = []
            for entry in item[:20]:
                if isinstance(entry, (int, float, bool)) or entry is None:
                    values.append(entry)
                elif isinstance(entry, str):
                    values.append(entry.strip()[:MAX_STRING])
            clean[name] = values
        else:
            continue
    return clean or None


def _session_map():
    try:
        import streamlit as st

        return st.session_state
    except Exception:
        return None


def session_id(state=None):
    state = state if state is not None else _session_map()
    if state is None:
        return uuid.uuid4().hex
    try:
        current = state.get("audit_sessao_id")
    except Exception:
        current = None
    if current:
        return current
    generated = uuid.uuid4().hex
    try:
        state["audit_sessao_id"] = generated
    except Exception:
        return generated
    return generated


def _identity_fields(principal=None, identity=None, state=None):
    if principal is None:
        state = state if state is not None else _session_map()
        if state is not None:
            try:
                principal = state.get("_audit_actor")
            except Exception:
                principal = None
    if principal is not None:
        return {
            "usuario_id": getattr(principal, "id", None),
            "usuario_email": getattr(principal, "email", "") or "",
            "usuario_nome": getattr(principal, "nome", "") or "",
            "perfil_ator": getattr(principal, "perfil", None),
            "gabinetes_ator": list(getattr(principal, "gabinetes", ()) or ())[:12],
        }
    identity = identity or {}
    return {
        "usuario_id": None,
        "usuario_email": (identity.get("email") or "").strip().lower(),
        "usuario_nome": identity.get("name") or identity.get("email") or "",
        "perfil_ator": None,
        "gabinetes_ator": [],
    }


def _default_summary(evento, acao, modulo, entidade_tipo, entidade_id, details):
    details = details or {}
    if details.get("resumo"):
        return details["resumo"]
    title = details.get("titulo") or details.get("numero") or details.get("arquivo")
    target = details.get("usuario_alvo") or details.get("nome_alvo")
    entity = entity_label(entidade_tipo) if entidade_tipo else module_label(modulo)
    action = event_label(evento) or action_label(acao)
    if evento == "DOCUMENTO_BAIXADO":
        who = entity if entity and entity != "—" else "documento"
        name = title or ""
        return f"Baixou {who}" + (f" {name}" if name else "")
    if evento == "BACKUP_GERADO":
        return "Gerou backup administrativo"
    if evento == "BACKUP_DISPONIBILIZADO":
        return "Disponibilizou backup administrativo para download"
    if target:
        return f"{action}: {target}"
    if title:
        return f"{action}: {title}"
    return action


def registrar_evento(
    store,
    *,
    evento,
    modulo="",
    acao="",
    resultado="OK",
    principal=None,
    identity=None,
    entidade_tipo=None,
    entidade_id=None,
    detalhes=None,
    sessao_id=None,
    state=None,
):
    if store is None:
        return None
    try:
        store = unwrap_store(store)
        state = state if state is not None else _session_map()
        fields = _identity_fields(principal, identity, state)
        stamp = utc_now()
        payload = dict(sanitize_details(detalhes) or {})
        if fields.get("perfil_ator") and "perfil_ator" not in payload:
            payload["perfil_ator"] = fields["perfil_ator"]
        gabinetes = fields.get("gabinetes_ator")
        if gabinetes and "gabinetes_ator" not in payload:
            payload["gabinetes_ator"] = gabinetes
        if "resumo" not in payload:
            built = _default_summary(evento, acao, modulo, entidade_tipo, entidade_id, payload)
            if built:
                payload["resumo"] = built[:MAX_STRING]
        row = {
            **fields,
            "sessao_id": sessao_id if sessao_id is not None else session_id(state),
            "evento": evento,
            "modulo": modulo or "",
            "acao": acao or evento,
            "entidade_tipo": entidade_tipo,
            "entidade_id": entidade_id,
            "resultado": resultado or "OK",
            "detalhes_json": json.dumps(payload, ensure_ascii=False) if payload else None,
            "criado_em": stamp,
        }
        identifier = AuditStore(store).insert(row)
        if state is not None:
            try:
                if "audit_sessao_inicio" not in state:
                    state["audit_sessao_inicio"] = stamp
                state["audit_ultima_atividade"] = stamp
                if modulo:
                    state["audit_ultimo_modulo"] = modulo
            except Exception:
                pass
        return identifier
    except Exception:
        LOGGER.exception("Falha ao registrar auditoria; operação principal preservada.")
        return None


def iniciar_sessao_autorizada(store, principal, state=None):
    state = state if state is not None else _session_map()
    if state is not None:
        try:
            if state.get("audit_sessao_registrada"):
                return session_id(state)
        except Exception:
            pass
    sid = session_id(state)
    registrar_evento(
        store,
        evento="SESSAO_INICIADA",
        modulo="",
        acao="ACESSO",
        resultado="OK",
        principal=principal,
        sessao_id=sid,
        state=state,
        detalhes={"ultimo_modulo": None},
    )
    registrar_evento(
        store,
        evento="ACESSO_AUTORIZADO",
        modulo="",
        acao="AUTORIZACAO",
        resultado="OK",
        principal=principal,
        sessao_id=sid,
        state=state,
    )
    if state is not None:
        try:
            state["audit_sessao_registrada"] = True
        except Exception:
            pass
    return sid


def registrar_acesso_negado(store, identity, *, inativo=False, state=None):
    state = state if state is not None else _session_map()
    if state is not None:
        try:
            if state.get("audit_negado_registrado"):
                return
        except Exception:
            pass
    registrar_evento(
        store,
        evento="USUARIO_INATIVO" if inativo else "ACESSO_NEGADO",
        modulo="",
        acao="AUTORIZACAO",
        resultado="NEGADO",
        identity=identity,
        state=state,
        detalhes={"motivo": "usuario_inativo" if inativo else "nao_autorizado"},
    )
    if state is not None:
        try:
            state["audit_negado_registrado"] = True
        except Exception:
            pass


def registrar_modulo(store, principal, selected, state=None):
    modulo = MODULE_KEYS.get(selected)
    if not modulo:
        return
    state = state if state is not None else _session_map()
    if state is not None:
        try:
            if state.get("audit_modulo_atual") == modulo:
                return
        except Exception:
            pass
    registrar_evento(
        store,
        evento=(
            "CENTRAL_PENDENCIAS_ACESSADA"
            if modulo == "pendencias"
            else "ALERTAS_ACESSADOS"
            if modulo == "alertas"
            else "MODULO_ACESSADO"
        ),
        modulo=modulo,
        acao="ENTRAR",
        resultado="OK",
        principal=principal,
        state=state,
        detalhes={"modulo": modulo},
    )
    if state is not None:
        try:
            state["audit_modulo_atual"] = modulo
        except Exception:
            pass


def registrar_logout(store, principal=None, identity=None, state=None):
    state = state if state is not None else _session_map()
    detalhes = {}
    if state is not None:
        try:
            started = state.get("audit_sessao_inicio")
            last = state.get("audit_ultima_atividade") or utc_now()
            if started:
                begin = datetime.fromisoformat(started)
                finish = datetime.fromisoformat(last)
                if begin.tzinfo is None:
                    begin = begin.replace(tzinfo=timezone.utc)
                if finish.tzinfo is None:
                    finish = finish.replace(tzinfo=timezone.utc)
                detalhes["duracao_segundos_aprox"] = max(
                    0, int((finish - begin).total_seconds())
                )
            detalhes["ultimo_modulo"] = state.get("audit_ultimo_modulo")
        except Exception:
            pass
    registrar_evento(
        store,
        evento="LOGOUT",
        modulo="",
        acao="SAIR",
        resultado="OK",
        principal=principal,
        identity=identity,
        state=state,
        detalhes=detalhes or None,
    )


def registrar_erro(store, *, modulo, acao, erro, principal=None):
    registrar_evento(
        store,
        evento="ERRO_OPERACIONAL",
        modulo=modulo,
        acao=acao,
        resultado="ERRO",
        principal=principal,
        detalhes={"tipo": type(erro).__name__, "mensagem": str(erro)[:MAX_STRING]},
    )


def registrar_alteracao_usuario(store, principal, antes, depois, criado=False):
    alvo = (depois or antes or {}).get("email")
    nome = (depois or antes or {}).get("nome")
    identifier = (depois or antes or {}).get("id")
    base = {"usuario_alvo": alvo, "nome_alvo": nome}

    def emit(evento, acao, extra=None):
        detalhes = dict(base)
        if extra:
            detalhes.update(extra)
        registrar_evento(
            store,
            evento=evento,
            modulo="admin",
            acao=acao,
            resultado="OK",
            principal=principal,
            entidade_tipo="usuario",
            entidade_id=identifier,
            detalhes=detalhes,
        )

    if criado:
        emit(
            "USUARIO_CRIADO",
            "CRIAR",
            {
                "perfil": depois.get("perfil"),
                "ativo": depois.get("ativo"),
                "resumo": f"Criou o usuário {alvo or nome or identifier}",
            },
        )
        return
    changes = format_changes(
        antes,
        depois,
        {
            "nome": "Nome",
            "ativo": "Situação",
            "perfil": "Perfil",
            "pode_portarias": "Portarias",
            "pode_agenda": "Agenda",
            "pode_oficios": "Ofícios",
            "pode_memorandos": "Memorandos",
            "pode_admin": "Administração",
            "pode_representacoes": "Representações",
            "pode_ouvidoria": "Ouvidoria",
            "pode_relatorios": "Relatórios",
        },
    )
    extra_edit = {"alteracoes": changes} if changes else None
    emit("USUARIO_EDITADO", "EDITAR", extra_edit)
    if antes and depois:
        if bool(antes.get("ativo")) != bool(depois.get("ativo")):
            emit(
                "USUARIO_ATIVADO" if depois.get("ativo") else "USUARIO_DESATIVADO",
                "ATIVAR" if depois.get("ativo") else "DESATIVAR",
                {"de": bool(antes.get("ativo")), "para": bool(depois.get("ativo")), "campo": "ativo"},
            )
        if antes.get("perfil") != depois.get("perfil"):
            if depois.get("perfil") == "ADMINISTRADOR":
                emit(
                    "USUARIO_PROMOVIDO",
                    "PROMOVER",
                    {"de": antes.get("perfil"), "para": depois.get("perfil"), "campo": "perfil"},
                )
            elif antes.get("perfil") == "ADMINISTRADOR":
                emit(
                    "ADMINISTRADOR_REBAIXADO",
                    "REBAIXAR",
                    {"de": antes.get("perfil"), "para": depois.get("perfil"), "campo": "perfil"},
                )
        permission_keys = (
            "pode_portarias",
            "pode_agenda",
            "pode_oficios",
            "pode_memorandos",
            "pode_admin",
            "pode_representacoes",
            "pode_ouvidoria",
        )
        changed = [
            key
            for key in permission_keys
            if bool(antes.get(key)) != bool(depois.get(key))
        ]
        if changed:
            emit(
                "PERMISSOES_ALTERADAS",
                "PERMISSOES",
                {
                    "campos": changed,
                    "alteracoes": [
                        f"{USER_FIELD_LABELS.get(key, key)}: "
                        f"{_display_value(bool(antes.get(key)))} → "
                        f"{_display_value(bool(depois.get(key)))}"
                        for key in changed
                    ],
                    "de": {k: bool(antes.get(k)) for k in changed},
                    "para": {k: bool(depois.get(k)) for k in changed},
                },
            )
        before_offices = list(antes.get("gabinetes") or [])
        after_offices = list(depois.get("gabinetes") or [])
        if before_offices != after_offices:
            emit(
                "GABINETES_ALTERADOS",
                "GABINETES",
                {
                    "de": before_offices,
                    "para": after_offices,
                    "alteracoes": [
                        "Gabinetes: "
                        + _display_value(before_offices)
                        + " → "
                        + _display_value(after_offices)
                    ],
                },
            )


def registrar_bloqueio_admin(store, principal, alvo, motivo, evento):
    registrar_evento(
        store,
        evento=evento,
        modulo="admin",
        acao="BLOQUEADO",
        resultado="NEGADO",
        principal=principal,
        entidade_tipo="usuario",
        entidade_id=(alvo or {}).get("id"),
        detalhes={
            "usuario_alvo": (alvo or {}).get("email"),
            "motivo": motivo[:MAX_STRING],
        },
    )


def aplicar_usuario(store, principal, payload, identifier=None):
    from database.access import AccessStore, LAST_ADMIN_MESSAGE, PROTECTED_ADMIN_MESSAGE

    access = AccessStore(store)
    antes = access.get(identifier) if identifier else None
    try:
        new_id = access.save_user(payload, identifier)
    except ValueError as exc:
        message = str(exc)
        alvo = antes or payload
        if PROTECTED_ADMIN_MESSAGE in message or "protegido" in message:
            registrar_bloqueio_admin(
                store, principal, alvo, message, "ADMIN_PROTEGIDO_BLOQUEADO"
            )
        elif LAST_ADMIN_MESSAGE in message or "administrador ativo" in message:
            registrar_bloqueio_admin(
                store, principal, alvo, message, "ULTIMO_ADMIN_BLOQUEADO"
            )
        raise
    depois = access.get(new_id)
    registrar_alteracao_usuario(store, principal, antes, depois, criado=identifier is None)
    return new_id


def aplicar_exclusao_usuario(store, principal, identifier):
    from database.access import AccessStore, LAST_ADMIN_MESSAGE, PROTECTED_ADMIN_MESSAGE

    access = AccessStore(store)
    alvo = access.get(identifier)
    try:
        snapshot = access.delete_user(identifier, actor_id=principal.id)
    except ValueError as exc:
        message = str(exc)
        if PROTECTED_ADMIN_MESSAGE in message or "protegido" in message:
            registrar_bloqueio_admin(
                store, principal, alvo, message, "ADMIN_PROTEGIDO_BLOQUEADO"
            )
        elif "administrador ativo" in message or LAST_ADMIN_MESSAGE in message:
            registrar_bloqueio_admin(
                store, principal, alvo, message, "ULTIMO_ADMIN_BLOQUEADO"
            )
        raise
    registrar_evento(
        store,
        evento="USUARIO_EXCLUIDO",
        modulo="admin",
        acao="EXCLUIR",
        resultado="OK",
        principal=principal,
        entidade_tipo="usuario",
        entidade_id=snapshot["id"],
        detalhes={"usuario_alvo": snapshot["email"], "nome_alvo": snapshot["nome"]},
    )
    return snapshot


def registrar_download(
    store,
    *,
    modulo,
    entidade_tipo,
    entidade_id=None,
    arquivo=None,
    formato=None,
    rotulo=None,
    principal=None,
):
    return registrar_evento(
        store,
        evento="DOCUMENTO_BAIXADO",
        modulo=modulo,
        acao="EXPORTAR",
        resultado="OK",
        principal=principal,
        entidade_tipo=entidade_tipo,
        entidade_id=entidade_id,
        detalhes={
            "arquivo": (arquivo or "")[:80] or None,
            "formato": formato,
            "titulo": rotulo,
        },
    )


def _require_audit_reader(principal):
    from services.access import require_permission

    require_permission(principal, "admin")


def overview(store, principal):
    _require_audit_reader(principal)
    audit = AuditStore(store)
    today = period_bounds("hoje")
    week = period_bounds("7d")
    month = period_bounds("30d")
    from database.access import AccessStore

    users = AccessStore(store).list_users()
    ativos = sum(1 for u in users if u["ativo"])
    painel = audit.dashboard(*today)
    return {
        "hoje": audit.period_summary(*today),
        "semana": audit.period_summary(*week),
        "mes": audit.period_summary(*month),
        "ultimo_acesso": audit.latest_access(),
        "modulo_mais_usado": audit.top_module(*month),
        "usuarios_ativos": ativos,
        "usuarios": users,
        "painel": painel,
    }


def dashboard_hoje(store, principal):
    _require_audit_reader(principal)
    state = _session_map()
    now = datetime.now(timezone.utc).timestamp()
    if state is not None:
        try:
            cached = state.get("_audit_dashboard")
            if (
                cached
                and now - cached.get("t", 0) < DASHBOARD_CACHE_SECONDS
            ):
                return cached["data"]
        except Exception:
            pass
    data = AuditStore(store).dashboard(*period_bounds("hoje"))
    if state is not None:
        try:
            state["_audit_dashboard"] = {"t": now, "data": data}
        except Exception:
            pass
    return data


def user_overview(store, principal, filters=None):
    _require_audit_reader(principal)
    from database.access import AccessStore

    listed = {u["email"]: u for u in AccessStore(store).list_users()}
    rows = []
    for row in AuditStore(store).user_rows(filters):
        cadastro = listed.get(row["usuario_email"], {})
        rows.append(
            {
                **row,
                "nome": cadastro.get("nome") or row.get("usuario_nome") or "—",
                "email": row["usuario_email"],
                "perfil": cadastro.get("perfil") or "—",
                "ativo": cadastro.get("ativo"),
            }
        )
    return rows


def export_csv(store, principal, filters=None):
    _require_audit_reader(principal)
    audit = AuditStore(store)
    total = audit.count(filters)
    truncated = total > EXPORT_LIMIT
    rows = audit.list_events(filters, limit=EXPORT_LIMIT, offset=0)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "data_hora_local",
            "nome",
            "email",
            "modulo",
            "evento",
            "acao",
            "resultado",
            "entidade_tipo",
            "entidade_id",
            "detalhes",
        ]
    )
    for row in rows:
        writer.writerow(
            [
                format_local(row["criado_em"]),
                row.get("usuario_nome") or "",
                row.get("usuario_email") or "",
                module_label(row.get("modulo")),
                event_label(row.get("evento")),
                action_label(row.get("acao") or row.get("evento")),
                result_label(row.get("resultado")),
                entity_label(row.get("entidade_tipo"))
                if row.get("entidade_tipo")
                else "",
                row.get("entidade_id") or "",
                row.get("detalhes_json") or "",
            ]
        )
    return buffer.getvalue(), total, truncated


def details_dict(row):
    raw = (row or {}).get("detalhes_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return sanitize_details(parsed) or {}


def objeto_humano(row):
    details = details_dict(row)
    title = details.get("titulo") or details.get("numero") or details.get("arquivo")
    if title:
        return str(title)
    if row.get("entidade_tipo"):
        label = entity_label(row["entidade_tipo"])
        identifier = row.get("entidade_id")
        return label if not identifier else label
    return "—"


def resumo_humano(row):
    details = details_dict(row)
    if details.get("resumo"):
        return details["resumo"]
    changes = details.get("alteracoes")
    if isinstance(changes, list) and changes:
        return "; ".join(str(item) for item in changes[:4])
    return _default_summary(
        row.get("evento"),
        row.get("acao"),
        row.get("modulo"),
        row.get("entidade_tipo"),
        row.get("entidade_id"),
        details,
    )
