from services.afastamentos import (
    accepted_substitute,
    eligible_substitutes,
    portaria_substitute_ids,
)
from services.wording import preview_text, validate


PEOPLE = [
    {"id": 1, "nome": "PG", "funcao": "Procurador-Geral", "ativo": True},
    {"id": 2, "nome": "Sub A", "funcao": "Subprocurador-Geral", "ativo": True},
    {"id": 3, "nome": "Sub B", "funcao": "Subprocurador-Geral", "ativo": True},
    {"id": 4, "nome": "Ouvidor", "funcao": "Ouvidor", "ativo": True},
    {"id": 5, "nome": "Procurador", "funcao": "Procurador", "ativo": True},
    {"id": 6, "nome": "Inativo", "funcao": "Procurador", "ativo": False},
]


def test_pg_holder_accepts_only_subprocuradores():
    holder = PEOPLE[0]
    eligible = portaria_substitute_ids(holder, PEOPLE)
    assert eligible == [2, 3]
    assert eligible == [person["id"] for person in eligible_substitutes(holder, PEOPLE)]


def test_sub_pg_a_excludes_leadership_and_self():
    eligible = portaria_substitute_ids(PEOPLE[1], PEOPLE)
    assert eligible == [4, 5]
    assert {1, 2, 3, 6}.isdisjoint(eligible)


def test_sub_pg_b_excludes_leadership_and_self():
    eligible = portaria_substitute_ids(PEOPLE[2], PEOPLE)
    assert eligible == [4, 5]
    assert {1, 2, 3, 6}.isdisjoint(eligible)


def test_common_prosecutor_keeps_current_portaria_list():
    eligible = portaria_substitute_ids(PEOPLE[4], PEOPLE)
    assert eligible == [1, 2, 3, 4]
    assert 5 not in eligible
    assert 6 not in eligible


def test_changing_holder_drops_invalid_substitute():
    selected = 4
    kept = portaria_substitute_ids(PEOPLE[4], PEOPLE)
    cleared = portaria_substitute_ids(PEOPLE[0], PEOPLE)
    assert accepted_substitute(selected, kept) == 4
    assert accepted_substitute(selected, cleared) is None


def test_form_recalculates_substitutes_when_holder_changes(tmp_path, monkeypatch):
    import database.store as persistence
    from database.store import ROOT
    from streamlit.testing.v1 import AppTest
    from tests.access_testing import enable_login

    original = persistence.Store
    path = tmp_path / "subs.db"
    store = original(path)
    monkeypatch.setattr(persistence, "Store", lambda: original(path))
    enable_login(monkeypatch, store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_portarias").click().run()
    assert not app.exception

    def box(label):
        return next(item for item in app.selectbox if item.label == label)

    box("Procurador titular").set_value(1).run()
    assert not app.exception
    assert list(box("Procurador substituto").options) == [
        "Isabella Barbosa Marinho Falcão",
        "Bradson Tibério Luna Camelo",
    ]
    box("Procurador substituto").set_value(2).run()
    box("Procurador titular").set_value(2).run()
    assert not app.exception
    substitute = box("Procurador substituto")
    assert substitute.value is None
    labels = list(substitute.options)
    assert "Elvira Samara Pereira de Oliveira" not in labels
    assert "Isabella Barbosa Marinho Falcão" not in labels
    assert "Bradson Tibério Luna Camelo" not in labels
    assert "Sheyla Barreto Braga de Queiroz" in labels
    box("Procurador titular").set_value(7).run()
    options = list(box("Procurador substituto").options)
    assert "Sheyla Barreto Braga de Queiroz" not in options
    assert "Elvira Samara Pereira de Oliveira" in options
    assert "Isabella Barbosa Marinho Falcão" in options
    assert "Bradson Tibério Luna Camelo" in options


def test_ordinary_portaria_generation_still_composes(store):
    from tests.cases import sample

    payload = sample(store)
    holder = payload["substituicoes"][0]["titular"]
    substitute = payload["substituicoes"][0]["substituto"]
    people = store.catalog("procuradores")
    assert substitute["id"] in portaria_substitute_ids(holder, people)
    validate(payload)
    text = preview_text(payload, 9)
    assert substitute["nome"] in text
    assert payload["signatario"]["nome"].upper() in text
