import pytest

from services.afastamentos import substitution_pending, validate


PEOPLE = [
    {"id": 1, "nome": "PG", "funcao": "Procurador-Geral", "ativo": True},
    {"id": 2, "nome": "Sub", "funcao": "Subprocurador-Geral", "ativo": True},
    {"id": 3, "nome": "Procurador", "funcao": "Procurador", "ativo": True},
]


def leave(holder, substitute=None):
    return {"procurador_id": holder, "substituto_id": substitute, "motivo": "Férias", "data_inicio": "2026-09-20", "data_fim": "2026-09-22"}


@pytest.mark.parametrize("holder", [1, 2])
def test_leadership_leave_can_be_saved_without_substitute(holder):
    record = leave(holder)
    validate(record, PEOPLE)
    assert substitution_pending(record, PEOPLE)


def test_later_eligible_substitute_clears_pending_state():
    record = leave(1, 2)
    validate(record, PEOPLE)
    assert not substitution_pending(record, PEOPLE)


def test_pg_still_rejects_common_prosecutor_as_substitute():
    with pytest.raises(ValueError, match="elegível"):
        validate(leave(1, 3), PEOPLE)


def test_sub_pg_rejects_pg_as_ineligible_substitute():
    with pytest.raises(ValueError, match="elegível"):
        validate(leave(2, 1), PEOPLE)


def test_prosecutor_cannot_substitute_themself():
    with pytest.raises(ValueError, match="não pode substituir a si mesmo"):
        validate(leave(2, 2), PEOPLE)


def test_common_prosecutor_never_has_pending_substitution():
    record = leave(3)
    validate(record, PEOPLE)
    assert not substitution_pending(record, PEOPLE)


def test_period_conflicts_continue_to_block():
    with pytest.raises(ValueError, match="Já existe afastamento"):
        validate(leave(1), PEOPLE, holder_conflict=True)
