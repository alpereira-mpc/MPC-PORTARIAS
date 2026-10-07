from datetime import date

import pytest

from database.estagiarios import EstagiariosStore, limite_padrao
from database.memorandos import MemorandosStore


def _people(store):
    base = MemorandosStore(store)
    base.import_servers(
        [
            {"nome": "Ana Estágio", "matricula": "101", "cargo": "Estagiária", "setor": "PROGE"},
            {"nome": "Bruno Estágio", "matricula": "102", "cargo": "Estagiário", "setor": "PROGE"},
            {"nome": "Carla Estágio", "matricula": "103", "cargo": "Estagiária", "setor": "PROGE"},
            {"nome": "Davi Analista", "matricula": "104", "cargo": "Analista", "setor": "PROGE"},
        ],
        actor_email="admin@test", filename="base.xlsx", content_hash="servers", administrator=True,
    )
    return {row["nome"]: row["id"] for row in base.all_servers()}


def test_limit_two_active_positions_and_admin_only(store):
    people = _people(store)
    service = EstagiariosStore(store)
    start = date(2026, 3, 10)
    with pytest.raises(ValueError, match="administradores"):
        service.create(people["Ana Estágio"], "PROGE", start, administrator=False)
    service.create(people["Ana Estágio"], "PROGE", start, administrator=True)
    service.create(people["Bruno Estágio"], "PROGE", start, administrator=True)
    with pytest.raises(ValueError, match="duas posições"):
        service.create(people["Carla Estágio"], "PROGE", start, administrator=True)
    with pytest.raises(ValueError, match="classificada como estagiária"):
        service.create(people["Davi Analista"], "MTTF", start, administrator=True)


def test_two_year_limit_close_and_replace_preserve_history(store):
    people = _people(store)
    service = EstagiariosStore(store)
    start = date(2026, 2, 28)
    assert limite_padrao(start) == date(2028, 2, 28)
    identifier = service.create(people["Ana Estágio"], "LAF", start, administrator=True)
    row = service.list(include_inactive=False)[0]
    assert row["data_limite"] == "2028-02-28"
    replacement = service.replace(identifier, people["Bruno Estágio"], date(2026, 6, 1), administrator=True)
    rows = service.list()
    old = next(item for item in rows if item["id"] == identifier)
    new = next(item for item in rows if item["id"] == replacement)
    assert not old["ativo"] and old["data_encerramento"] == "2026-06-01"
    assert new["ativo"] and new["pessoa_id"] == people["Bruno Estágio"]
    service.close(replacement, date(2026, 9, 1), administrator=True)
    assert not service.list(include_inactive=False)
