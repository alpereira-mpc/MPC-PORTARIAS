"""Domain rules for prosecutor leave, kept separate from appointments."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

MOTIVOS = ("Férias", "Licença especial", "Licença para tratamento de saúde", "Outro")
RECIFE = ZoneInfo("America/Recife")


def status(record, today=None):
    if record.get("cancelado"):
        return "CANCELADO"
    today = today or datetime.now(RECIFE).date()
    start = date.fromisoformat(record["data_inicio"])
    end = date.fromisoformat(record["data_fim"])
    return "AGENDADO" if start > today else "ENCERRADO" if end < today else "EM ANDAMENTO"


def eligible_substitutes(holder, people):
    role = holder.get("funcao")
    if role == "Procurador-Geral":
        return [p for p in people if p.get("ativo") and p.get("funcao") == "Subprocurador-Geral"]
    if role == "Subprocurador-Geral":
        return [
            p for p in people
            if p.get("ativo") and p.get("funcao") not in ("Procurador-Geral", "Subprocurador-Geral")
        ]
    return []


def validate(record, people, *, holder_conflict=False, substitute_absent=False):
    if record.get("motivo") not in MOTIVOS:
        raise ValueError("Motivo de afastamento inválido.")
    if record.get("motivo") == "Outro" and not (record.get("motivo_outro") or "").strip():
        raise ValueError("Especifique o motivo do afastamento.")
    try:
        start = date.fromisoformat(record["data_inicio"])
        end = date.fromisoformat(record["data_fim"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Informe as datas inicial e final do afastamento.") from None
    if end < start:
        raise ValueError("A data final deve ser igual ou posterior à data inicial.")
    members = {p["id"]: p for p in people}
    holder = members.get(record.get("procurador_id"))
    if not holder or not holder.get("ativo"):
        raise ValueError("Selecione um procurador ativo da base existente.")
    substitute_id = record.get("substituto_id")
    needed = holder.get("funcao") in ("Procurador-Geral", "Subprocurador-Geral")
    if needed and not substitute_id:
        label = "Procurador-Geral" if holder.get("funcao") == "Procurador-Geral" else "Subprocurador-Geral"
        raise ValueError(f"O afastamento do {label} exige indicação de um substituto.")
    if substitute_id:
        if substitute_id == holder["id"]:
            raise ValueError("O procurador afastado não pode substituir a si mesmo.")
        if substitute_id not in {p["id"] for p in eligible_substitutes(holder, people)}:
            raise ValueError("O substituto selecionado não é elegível para o cargo institucional.")
    if holder_conflict:
        raise ValueError("Já existe afastamento deste procurador que coincide total ou parcialmente com o período informado.")
    if substitute_absent:
        raise ValueError("O procurador selecionado como substituto possui afastamento no período informado.")
