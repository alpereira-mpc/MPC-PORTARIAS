"""Pure preparation of private active tasks for the IA briefing."""

from datetime import date, datetime, timedelta
import hashlib
import json

from database.tarefas import ACTIVE, effective_deadline
from database.record_engagement import ORIGIN_LABELS
from services.audit import INSTITUTIONAL_TZ


PRIORITIES = ("BAIXA", "NORMAL", "ALTA", "URGENTE")
STATUS_LABELS = {
    "A_FAZER": "A fazer",
    "EM_ANDAMENTO": "Em andamento",
    "AGUARDANDO": "Aguardando",
}
PRIORITY_LABELS = {
    "BAIXA": "Baixa",
    "NORMAL": "Normal",
    "ALTA": "Alta",
    "URGENTE": "Urgente",
}
PROXIMOS_PRAZOS_DIAS = 7


def contexto_analise_tarefas(tarefas, agora=None):
    """Build deterministic, non-sensitive facts for active private tasks only."""
    agora = _agora(agora)
    active = [row for row in tarefas or () if row.get("status") in ACTIVE]
    prepared = [_tarefa_resumo(row, agora) for row in active]
    counts = {
        "total_ativas": len(prepared),
        "vencidas": sum(row["situacao_prazo"] == "VENCIDA" for row in prepared),
        "hoje": sum(row["situacao_prazo"] == "HOJE" for row in prepared),
        "proximos_7_dias": sum(
            row["situacao_prazo"] == "PROXIMOS_7_DIAS" for row in prepared
        ),
        "sem_prazo": sum(row["situacao_prazo"] == "SEM_PRAZO" for row in prepared),
        "por_prioridade": {
            PRIORITY_LABELS[priority]: sum(
                row["prioridade"] == PRIORITY_LABELS[priority] for row in prepared
            )
            for priority in PRIORITIES
        },
        "por_status": {
            STATUS_LABELS[status]: sum(
                row["status"] == STATUS_LABELS[status] for row in prepared
            )
            for status in ACTIVE
        },
    }
    context = {
        "data_atual": agora.date().isoformat(),
        "janela_prazo_proximo_dias": PROXIMOS_PRAZOS_DIAS,
        "contagens": counts,
        "tarefas": prepared,
    }
    signature = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return context, signature


def leitura_analise_tarefas_salva(stored, owner_user_id, signature):
    """Keep a successful briefing visible without another Gemini request."""
    if not isinstance(stored, dict) or stored.get("owner_user_id") != owner_user_id:
        return None, False
    text = str(stored.get("texto") or "").strip()
    if not text:
        return None, False
    return text, stored.get("assinatura") != signature


def _agora(value):
    if value is None:
        return datetime.now(INSTITUTIONAL_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=INSTITUTIONAL_TZ)
    return value.astimezone(INSTITUTIONAL_TZ)


def _tarefa_resumo(row, agora):
    deadline = _prazo_efetivo(row)
    priority = row.get("prioridade") if row.get("prioridade") in PRIORITIES else "NORMAL"
    status = row["status"]
    return {
        "titulo": _texto(row.get("titulo"), 200),
        "descricao": _texto(row.get("descricao"), 800),
        "categoria": _texto(row.get("categoria"), 100),
        "status": STATUS_LABELS[status],
        "prioridade": PRIORITY_LABELS[priority],
        "prazo": _prazo_publico(row, deadline),
        "situacao_prazo": _situacao_prazo(deadline, agora),
        "origem_modulo": _rotulo_origem(row.get("origem_modulo")),
        "lembretes": _lembretes_publicos(row.get("lembretes"), agora),
    }


def _prazo_efetivo(row):
    try:
        return effective_deadline(row, INSTITUTIONAL_TZ)
    except (TypeError, ValueError):
        return None


def _prazo_publico(row, deadline):
    if deadline is None:
        return None
    return {
        "data": row.get("prazo_data"),
        "hora": row.get("prazo_hora") or None,
    }


def _situacao_prazo(deadline, agora):
    if deadline is None:
        return "SEM_PRAZO"
    if deadline < agora:
        return "VENCIDA"
    if deadline.date() == agora.date():
        return "HOJE"
    if deadline.date() <= agora.date() + timedelta(days=PROXIMOS_PRAZOS_DIAS):
        return "PROXIMOS_7_DIAS"
    return "FUTURA"


def _lembretes_publicos(values, agora):
    reminders = []
    for value in values or ():
        try:
            moment = datetime.fromisoformat(str(value))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=INSTITUTIONAL_TZ)
        else:
            moment = moment.astimezone(INSTITUTIONAL_TZ)
        reminders.append(
            {
                "momento": moment.isoformat(),
                "atingido": moment <= agora,
            }
        )
    return reminders[:3]


def _rotulo_origem(value):
    """Friendly module name for the briefing. Stored slugs stay unchanged."""
    text = _texto(value, 80)
    if not text:
        return ""
    label = ORIGIN_LABELS.get(text)
    if label:
        return label
    readable = " ".join(
        part.capitalize() for part in text.replace("-", " ").split("_") if part
    )
    return readable


def _texto(value, limit):
    return " ".join(str(value or "").split())[:limit]
