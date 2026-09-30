from datetime import date

import services.peticoes_ui as ui


class _Store:
    def catalog(self, name):
        assert name == "procuradores"
        return [
            {"id": 1, "nome": "Elvira Samara Pereira de Oliveira", "ativo": True},
            {"id": 2, "nome": "Marcílio Toscano Franca Filho", "ativo": True},
        ]


def _suggestion(**changes):
    value = {"numero_tramita":"116435/26","data_protocolo":"2026-09-29","destinatario":"Presidência","natureza":"PROVIDENCIAS","assunto":"Assunto da IA","objeto":"Objeto da IA","origem":"Origem da IA","processo_tc":"","signatarios":[" elvira samara pereira de oliveira ","MARCILIO TOSCANO FRANCA FILHO","Nome inexistente"],"pedidos":[{"descricao":"Pedido A"},{"descricao":"Pedido B"},{"descricao":"Pedido C"}]}
    value.update(changes); return value


def test_ai_prefill_populates_only_empty_fields_and_never_persists(monkeypatch):
    state={"peticoes_ai_pending":_suggestion()}
    monkeypatch.setattr(ui.st,"session_state",state)
    ui._apply_ai_prefill(_Store())
    assert state["peticoes_new_numero"] == "116435/26"
    assert state["peticoes_new_data"] == date(2026,9,29)
    assert state["peticoes_new_signatarios"] == [1,2]
    assert state["peticoes_new_pedidos"].splitlines() == ["Pedido A","Pedido B","Pedido C"]
    assert "peticoes_ai_pending" not in state


def test_ai_prefill_preserves_manual_values_and_does_not_reapply(monkeypatch):
    state={"peticoes_new_assunto":"Assunto manual","peticoes_new_origem":"Origem manual","peticoes_ai_pending":_suggestion()}
    monkeypatch.setattr(ui.st,"session_state",state)
    ui._apply_ai_prefill(_Store())
    assert state["peticoes_new_assunto"] == "Assunto manual"
    assert state["peticoes_new_origem"] == "Origem manual"
    state["peticoes_new_assunto"]="Correção posterior"
    ui._apply_ai_prefill(_Store())
    assert state["peticoes_new_assunto"] == "Correção posterior"


def test_partial_or_invalid_ai_data_is_safe_for_widgets(monkeypatch):
    state={"peticoes_ai_pending":_suggestion(natureza="INVALIDA",data_protocolo="",origem="",pedidos=[])}
    monkeypatch.setattr(ui.st,"session_state",state)
    ui._apply_ai_prefill(_Store())
    assert "peticoes_new_natureza" not in state
    assert "peticoes_new_data" not in state
    assert "peticoes_new_origem" not in state
    assert "peticoes_new_pedidos" not in state
