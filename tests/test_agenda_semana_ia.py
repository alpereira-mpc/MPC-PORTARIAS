"""Weekly agenda briefing. Facts stay in Python; Gemini is mocked."""

from datetime import date
import json
import logging
from pathlib import Path

from streamlit.testing.v1 import AppTest

import services.ai_service as ai_service
from services.agenda import contexto_semana, leitura_semana_salva
from services.ai_service import GeminiErro

START = date(2026, 9, 28)
END = date(2026, 10, 5)
NAMES = {1: "Ana Lima", 2: "Bruno Souza"}
_HOLD = {}


def _item(day, start, finish, people, title, **extra):
    untimed = extra.pop("sem_hora", False)
    return {
        "id": extra.pop("id", title),
        "tipo": extra.pop("tipo", "REUNIAO"),
        "inicio": f"{day}T00:00:00" if untimed else f"{day}T{start}:00",
        "fim": (
            f"{day}T00:00:00" if untimed else (f"{day}T{finish}:00" if finish else None)
        ),
        "sem_hora": untimed,
        "situacao": extra.pop("situacao", "Agendado"),
        "titulo": title,
        "procuradores": people,
        "observacoes": "texto interno que não pode sair",
        "processo": "0001/2026",
        **extra,
    }


def _week():
    return [
        _item("2026-09-28", "09:00", "10:00", [1], "Abertura"),
        _item("2026-09-28", "14:00", "15:00", [1], "Tarde"),
        _item("2026-09-29", "09:00", "10:00", [1], "Manhã"),
        _item("2026-09-29", "10:00", "11:00", [1], "Em seguida"),
        _item("2026-09-30", "08:00", "09:00", [1], "Cedo"),
        _item("2026-09-30", "10:00", "11:00", [1], "Sessão do Pleno"),
        _item("2026-09-30", "10:30", "12:00", [1], "Reunião sobreposta"),
        _item("2026-09-30", "10:00", "11:00", [2], "Outro gabinete"),
        _item("2026-10-01", "09:00", "10:00", [1], "Durante o afastamento"),
        _item("2026-10-02", "10:00", "11:00", [1], "Com horário"),
        _item("2026-10-02", "00:00", "00:00", [1], "Sem horário", sem_hora=True),
        _item("2026-10-06", "09:00", "10:00", [1], "Fora da semana"),
        _item("2026-09-30", "16:00", "17:00", [1], "Cancelado", situacao="Cancelado"),
    ]


def _leaves():
    return [
        {
            "id": "af-1",
            "procurador_id": 1,
            "motivo": "Férias",
            "motivo_outro": "detalhe reservado",
            "data_inicio": "2026-10-01",
            "data_fim": "2026-10-01",
            "substituto_id": 2,
            "observacao": "observação interna",
            "cancelado": 0,
        },
        {
            "id": "af-2",
            "procurador_id": 2,
            "motivo": "Licença especial",
            "data_inicio": "2026-10-02",
            "data_fim": "2026-10-02",
            "substituto_id": None,
            "observacao": "outro texto",
            "cancelado": 0,
        },
    ]


def test_week_facts_are_calculated_without_inventing_conflicts():
    context, signature = contexto_semana(START, END, _week(), _leaves(), NAMES)
    assert context["periodo"] == {"inicio": "2026-09-28", "fim": "2026-10-04"}
    assert context["total_compromissos"] == 11
    assert context["total_afastamentos"] == 2
    assert context["compromissos_sem_horario"] == 1
    assert context["quantidade_por_dia"]["2026-09-28"] == 2
    assert context["quantidade_por_dia"]["2026-09-29"] == 2
    assert context["quantidade_por_dia"]["2026-09-30"] == 4
    assert context["quantidade_por_dia"]["2026-10-01"] == 1
    assert context["dia_mais_carregado"] == "2026-09-30"
    titles = [item["titulo"] for item in context["compromissos"]]
    assert "Fora da semana" not in titles
    assert "Cancelado" not in titles
    overlaps = context["sobreposicoes_compromissos"]
    assert len(overlaps) == 1
    assert overlaps[0]["data"] == "2026-09-30"
    assert {item["titulo"] for item in overlaps[0]["itens"]} == {
        "Sessão do Pleno",
        "Reunião sobreposta",
    }
    coincidences = context["coincidencias_afastamento_compromisso"]
    assert len(coincidences) == 1
    assert coincidences[0]["compromisso"]["titulo"] == "Durante o afastamento"
    assert coincidences[0]["afastamento"]["responsavel"] == "Ana Lima"
    dumped = json.dumps(context, ensure_ascii=False)
    for secret in (
        "texto interno",
        "0001/2026",
        "detalhe reservado",
        "observação interna",
        "substituto_id",
        '"id"',
    ):
        assert secret not in dumped
    again, same = contexto_semana(START, END, _week(), _leaves(), NAMES)
    assert same == signature
    changed = _week()
    changed[0]["titulo"] = "Abertura alterada"
    _, other = contexto_semana(START, END, changed, _leaves(), NAMES)
    assert other != signature
    assert again["total_compromissos"] == 11


def test_saved_briefing_follows_the_period_and_the_signature():
    stored = {
        "inicio": "2026-09-28",
        "fim": "2026-10-04",
        "assinatura": "abc",
        "texto": "Panorama da semana.",
    }
    assert leitura_semana_salva(stored, "2026-09-28", "2026-10-04", "abc") == (
        "Panorama da semana.",
        False,
    )
    text, stale = leitura_semana_salva(stored, "2026-09-28", "2026-10-04", "nova")
    assert text == "Panorama da semana." and stale is True
    assert leitura_semana_salva(stored, "2026-10-05", "2026-10-11", "abc") == (
        None,
        False,
    )


def test_week_prompt_explains_facts_and_does_not_decide():
    prompt = ai_service.PROMPT_SEMANA_AGENDA
    for phrase in (
        "Não invente",
        "apenas os dados fornecidos",
        "Não recalcule",
        "panorama",
        "concentração",
        "sobreposições",
        "afastamentos",
        "Não decida cancelamento",
        "Não indique substituto",
        "Não crie obrigação",
        "Não gere tarefa",
    ):
        assert phrase in prompt
    assert "pedido cautelar" not in prompt


def test_week_analysis_sends_text_facts_and_hides_them_from_logs(monkeypatch, caplog):
    seen = {}

    def post(request):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Quarta-feira concentra as atividades."}]
                        }
                    }
                ]
            }
        ).encode()

    monkeypatch.setattr(ai_service, "_post", post)
    import streamlit as st

    monkeypatch.setattr(st, "secrets", {"GEMINI_API_KEY": "chave-teste"})
    context, _ = contexto_semana(START, END, _week(), _leaves(), NAMES)
    with caplog.at_level(logging.DEBUG):
        text = ai_service.analisar_semana_agenda(context)
    assert text == "Quarta-feira concentra as atividades."
    part = seen["body"]["contents"][0]["parts"][0]["text"]
    assert "inlineData" not in json.dumps(seen["body"])
    assert ai_service.PROMPT_SEMANA_AGENDA in part
    assert "Sessão do Pleno" in part
    assert "Ana Lima" not in caplog.text
    assert "Sessão do Pleno" not in caplog.text
    assert "chave-teste" not in caplog.text


def test_week_button_calls_gemini_once_and_keeps_the_period(monkeypatch):
    calls = []

    def fake(context):
        calls.append(context["periodo"])
        return "Panorama de teste."

    monkeypatch.setattr(ai_service, "analisar_semana_agenda", fake)
    rows = _week()
    _HOLD.clear()
    _HOLD.update(
        start=START,
        end=END,
        rows=rows,
        leaves=_leaves(),
        names=NAMES,
    )

    def page():
        from services.agenda_ui import render_week_analysis
        from tests.test_agenda_semana_ia import _HOLD

        render_week_analysis(
            _HOLD["start"], _HOLD["end"], _HOLD["rows"], _HOLD["leaves"], _HOLD["names"]
        )

    app = AppTest.from_function(page, default_timeout=30).run()
    assert calls == []
    assert app.button(key="agenda_semana_analisar").label == "✨ Analisar semana com IA"
    app.button(key="agenda_semana_analisar").click().run()
    assert calls == [{"inicio": "2026-09-28", "fim": "2026-10-04"}]
    assert any("Panorama de teste." in item.value for item in app.markdown)
    app.run()
    assert len(calls) == 1
    assert app.button(key="agenda_semana_analisar").label == "↻ Atualizar análise"
    rows[0]["titulo"] = "Abertura alterada"
    app.run()
    assert len(calls) == 1
    assert any("foi alterada" in item.value for item in app.caption)
    _HOLD["start"] = date(2026, 10, 5)
    _HOLD["end"] = date(2026, 10, 12)
    app.run()
    assert len(calls) == 1
    assert not any("Panorama de teste." in item.value for item in app.markdown)
    assert app.button(key="agenda_semana_analisar").label == "✨ Analisar semana com IA"


def test_week_analysis_error_keeps_the_screen(monkeypatch):
    def fail(_context):
        raise GeminiErro("O serviço de IA está indisponível no momento.")

    monkeypatch.setattr(ai_service, "analisar_semana_agenda", fail)
    _HOLD.clear()
    _HOLD.update(start=START, end=END, rows=_week(), leaves=[], names=NAMES)

    def page():
        from services.agenda_ui import render_week_analysis
        from tests.test_agenda_semana_ia import _HOLD

        render_week_analysis(
            _HOLD["start"], _HOLD["end"], _HOLD["rows"], _HOLD["leaves"], _HOLD["names"]
        )

    app = AppTest.from_function(page, default_timeout=30).run()
    app.button(key="agenda_semana_analisar").click().run()
    assert any("indisponível" in item.value for item in app.error)
    assert app.exception == []
    assert "agenda_semana_ia" not in app.session_state


def test_week_analysis_stays_inside_the_week_view_and_does_not_write():
    ui = Path("services/agenda_ui.py").read_text(encoding="utf-8")
    agenda = Path("services/agenda.py").read_text(encoding="utf-8")
    assert ui.count("render_week_analysis(") == 2
    screen = ui.split("def render(", 1)[1]
    assert screen.index("require_permission") < screen.index("render_week_analysis(")
    call = ui.split('if view == "Semana":', 1)[1]
    assert "render_week_analysis(start, end, rows, leaves, names)" in call
    handler = ui.split("def render_week_analysis", 1)[1].split("\ndef render(", 1)[0]
    for needle in (
        "agenda.save",
        "AgendaStore",
        "tarefa",
        "pendenc",
        "oficio",
        ".execute(",
    ):
        assert needle not in handler.lower()
    assert "ai_service" not in agenda
    assert "CREATE TABLE" not in ui
