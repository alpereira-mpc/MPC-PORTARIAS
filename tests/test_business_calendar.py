from datetime import date

from services.business_calendar import (
    DIAS_CORRIDOS,
    DIAS_UTEIS,
    calcular_vencimento,
    dias_restantes,
)


def test_business_deadline_excludes_start_and_weekend():
    assert calcular_vencimento(date(2026, 10, 1), 5, DIAS_UTEIS) == date(2026, 10, 8)
    assert calcular_vencimento(date(2026, 10, 1), 5, DIAS_CORRIDOS) == date(2026, 10, 6)


def test_remaining_days_uses_same_business_calendar():
    holiday = date(2026, 10, 5)
    assert dias_restantes(date(2026, 10, 2), date(2026, 10, 8), DIAS_UTEIS, [holiday]) == 3
    assert dias_restantes(date(2026, 10, 2), date(2026, 10, 8), DIAS_CORRIDOS) == 6
