"""Reusable institutional business-day calculations.

The caller supplies officially configured non-working days; this module never
invents a holiday calendar.
"""

from datetime import date, timedelta


DIAS_UTEIS = "DIAS_UTEIS"
DIAS_CORRIDOS = "DIAS_CORRIDOS"
TIPOS_PRAZO = (DIAS_UTEIS, DIAS_CORRIDOS)


def calcular_vencimento(data_inicio, quantidade, tipo_prazo, dias_nao_uteis=None):
    if not isinstance(data_inicio, date):
        data_inicio = date.fromisoformat(data_inicio)
    if not isinstance(quantidade, int) or quantidade < 1:
        raise ValueError("O prazo deve ser um número inteiro positivo.")
    if tipo_prazo not in TIPOS_PRAZO:
        raise ValueError("Tipo de prazo inválido.")
    if tipo_prazo == DIAS_CORRIDOS:
        return data_inicio + timedelta(days=quantidade)
    blocked = {
        value if isinstance(value, date) else date.fromisoformat(value)
        for value in (dias_nao_uteis or ())
    }
    current = data_inicio
    counted = 0
    while counted < quantidade:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in blocked:
            counted += 1
    return current


def dias_restantes(data_referencia, vencimento, tipo_prazo, dias_nao_uteis=None):
    """Return the remaining days using the same counting rule as a deadline.

    The reference day is not counted, matching ``calcular_vencimento``.  A
    negative result denotes an already expired deadline.
    """
    if not isinstance(data_referencia, date):
        data_referencia = date.fromisoformat(data_referencia)
    if not isinstance(vencimento, date):
        vencimento = date.fromisoformat(vencimento)
    if tipo_prazo not in TIPOS_PRAZO:
        raise ValueError("Tipo de prazo inválido.")
    if tipo_prazo == DIAS_CORRIDOS:
        return (vencimento - data_referencia).days
    blocked = {
        value if isinstance(value, date) else date.fromisoformat(value)
        for value in (dias_nao_uteis or ())
    }
    if vencimento < data_referencia:
        return -dias_restantes(vencimento, data_referencia, tipo_prazo, blocked)
    current = data_referencia
    remaining = 0
    while current < vencimento:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in blocked:
            remaining += 1
    return remaining
