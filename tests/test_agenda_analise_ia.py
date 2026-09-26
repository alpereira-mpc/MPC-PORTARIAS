"""Period briefing for the agenda. Facts stay in Python; Gemini is mocked."""

from datetime import date, timedelta
import json
import logging
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import services.ai_service as ai_service
from services.agenda import (
    contexto_periodo,
    leitura_analise_salva,
    validar_intervalo_analise,
)
from services.ai_service import GeminiErro

START = date(2026, 9, 28)
END = date(2026, 10, 4)
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


def test_period_facts_keep_overlap_rules_and_count_records():
    context, signature = contexto_periodo(START, END, _week(), _leaves(), NAMES)
    assert context["periodo"] == {"inicio": "2026-09-28", "fim": "2026-10-04"}
    assert context["total_compromissos"] == 11
    assert context["total_afastamentos"] == 2
    assert context["compromissos_sem_horario"] == 1
    assert context["quantidade_por_dia"]["2026-09-28"] == 2
    assert context["quantidade_por_dia"]["2026-09-30"] == 4
    assert context["quantidade_por_dia"]["2026-10-01"] == 2
    assert context["quantidade_por_dia"]["2026-10-02"] == 3
    assert context["dias_maior_concentracao"] == ["2026-09-30"]
    assert "dia_mais_carregado" not in context
    titles = [item["titulo"] for item in context["compromissos"]]
    assert "Fora da semana" not in titles
    assert "Cancelado" not in titles
    overlaps = context["sobreposicoes_compromissos"]
    assert len(overlaps) == 1
    assert {item["titulo"] for item in overlaps[0]["itens"]} == {
        "Sessão do Pleno",
        "Reunião sobreposta",
    }
    coincidences = context["coincidencias_afastamento_compromisso"]
    assert len(coincidences) == 1
    assert "conflito" not in coincidences[0]
    assert coincidences[0]["compromisso"]["titulo"] == "Durante o afastamento"
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
    _, same = contexto_periodo(START, END, _week(), _leaves(), NAMES)
    assert same == signature
    changed = _week()
    changed[0]["titulo"] = "Abertura alterada"
    _, other = contexto_periodo(START, END, changed, _leaves(), NAMES)
    assert other != signature


def test_concentration_tie_lists_every_day_and_a_spanning_leave_is_clipped():
    rows = [
        _item("2026-09-28", "09:00", "10:00", [1], "A"),
        _item("2026-09-28", "14:00", "15:00", [1], "B"),
        _item("2026-09-29", "09:00", "10:00", [1], "C"),
        _item("2026-09-30", "09:00", "10:00", [1], "D"),
        _item("2026-09-30", "14:00", "15:00", [1], "E"),
    ]
    context, _ = contexto_periodo(START, date(2026, 9, 30), rows, [], NAMES)
    assert context["dias_maior_concentracao"] == ["2026-09-28", "2026-09-30"]
    leave = {
        "procurador_id": 1,
        "motivo": "Férias",
        "data_inicio": "2026-09-20",
        "data_fim": "2026-10-10",
        "cancelado": 0,
    }
    spanned, _ = contexto_periodo(
        date(2026, 9, 26),
        date(2026, 10, 2),
        [_item("2026-09-26", "09:00", "10:00", [1], "Evento institucional")],
        [leave],
        NAMES,
    )
    assert spanned["total_afastamentos"] == 1
    assert spanned["afastamentos"][0]["inicio"] == "2026-09-20"
    assert spanned["afastamentos"][0]["fim"] == "2026-10-10"
    assert "2026-09-20" not in spanned["quantidade_por_dia"]
    assert "2026-10-10" not in spanned["quantidade_por_dia"]
    assert spanned["quantidade_por_dia"]["2026-09-26"] == 2
    assert spanned["quantidade_por_dia"]["2026-09-27"] == 1
    assert len(spanned["coincidencias_afastamento_compromisso"]) == 1


def test_interval_rejects_inverted_dates_and_more_than_31_days():
    validar_intervalo_analise(START, START)
    validar_intervalo_analise(START, START + timedelta(days=30))
    with pytest.raises(ValueError, match="anterior"):
        validar_intervalo_analise(date(2026, 10, 10), date(2026, 10, 1))
    with pytest.raises(ValueError, match="31 dias"):
        validar_intervalo_analise(date(2026, 9, 1), date(2026, 10, 2))


def test_saved_briefing_follows_the_period_and_the_signature():
    stored = {
        "inicio": "2026-09-28",
        "fim": "2026-10-04",
        "assinatura": "abc",
        "texto": "Panorama do período.",
    }
    assert leitura_analise_salva(stored, "2026-09-28", "2026-10-04", "abc") == (
        "Panorama do período.",
        False,
    )
    text, stale = leitura_analise_salva(stored, "2026-09-28", "2026-10-04", "nova")
    assert text == "Panorama do período." and stale is True
    assert leitura_analise_salva(stored, "2026-10-05", "2026-10-11", "abc") == (
        None,
        False,
    )


def test_period_prompt_treats_coincidence_as_information():
    prompt = ai_service.PROMPT_ANALISE_AGENDA
    for phrase in (
        "Não invente",
        "apenas os dados fornecidos",
        "Não recalcule",
        "maior concentração de registros da Agenda",
        "Não use a expressão dia mais carregado",
        "mencione todos",
        "coincidência temporal",
        "pode decorrer do próprio compromisso",
        "Não chame essa coincidência de conflito",
        "merece conferência sem elemento concreto",
        "Não invente incompatibilidade",
        "Não crie obrigação",
        "Não decida cancelamento",
        "Não indique substituto",
        "Não gere tarefa",
    ):
        assert phrase in prompt
    assert "pedido cautelar" not in prompt


def test_period_analysis_sends_text_facts_and_hides_them_from_logs(monkeypatch, caplog):
    seen = {}

    def post(request):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return json.dumps(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": "Quarta-feira concentra os registros da Agenda."
                                }
                            ]
                        }
                    }
                ]
            }
        ).encode()

    monkeypatch.setattr(ai_service, "_post", post)
    import streamlit as st

    monkeypatch.setattr(st, "secrets", {"GEMINI_API_KEY": "chave-teste"})
    context, _ = contexto_periodo(START, END, _week(), _leaves(), NAMES)
    with caplog.at_level(logging.DEBUG):
        text = ai_service.analisar_periodo_agenda(context)
    assert text == "Quarta-feira concentra os registros da Agenda."
    part = seen["body"]["contents"][0]["parts"][0]["text"]
    assert "inlineData" not in json.dumps(seen["body"])
    assert ai_service.PROMPT_ANALISE_AGENDA in part
    assert "Sessão do Pleno" in part
    assert "Ana Lima" not in caplog.text
    assert "chave-teste" not in caplog.text


def test_period_button_calls_gemini_once_and_hides_another_interval(monkeypatch):
    calls = []

    def fake(context):
        calls.append(context["periodo"])
        return "Panorama de teste."

    monkeypatch.setattr(ai_service, "analisar_periodo_agenda", fake)
    rows = _week()
    _HOLD.clear()
    _HOLD.update(start=START, end=END, rows=rows, leaves=_leaves(), names=NAMES)

    def page():
        from services.agenda_ui import apresentar_analise_periodo
        from tests.test_agenda_analise_ia import _HOLD

        apresentar_analise_periodo(
            _HOLD["start"],
            _HOLD["end"],
            _HOLD["rows"],
            _HOLD["leaves"],
            _HOLD["names"],
        )

    app = AppTest.from_function(page, default_timeout=30).run()
    assert calls == []
    assert app.button(key="agenda_analise_ia_btn").label == "✨ Analisar período com IA"
    app.button(key="agenda_analise_ia_btn").click().run()
    assert calls == [{"inicio": "2026-09-28", "fim": "2026-10-04"}]
    assert any("Panorama Executivo" in item.value for item in app.markdown)
    assert any("Panorama de teste." in item.value for item in app.markdown)
    app.run()
    assert len(calls) == 1
    assert app.button(key="agenda_analise_ia_btn").label == "↻ Atualizar análise"
    rows[0]["titulo"] = "Abertura alterada"
    app.run()
    assert len(calls) == 1
    assert any("foi alterada" in item.value for item in app.caption)
    _HOLD["start"] = date(2026, 10, 5)
    _HOLD["end"] = date(2026, 10, 11)
    app.run()
    assert len(calls) == 1
    assert not any("Panorama de teste." in item.value for item in app.markdown)
    assert app.button(key="agenda_analise_ia_btn").label == "✨ Analisar período com IA"


def test_invalid_interval_does_not_call_gemini(monkeypatch):
    calls = []
    monkeypatch.setattr(
        ai_service, "analisar_periodo_agenda", lambda context: calls.append(context)
    )

    def page():
        import streamlit as st
        from datetime import date
        from services.agenda_ui import render_period_analysis

        st.session_state["agenda_analise_inicio"] = date(2026, 10, 10)
        st.session_state["agenda_analise_fim"] = date(2026, 10, 1)
        render_period_analysis(None, {}, None, None, None, True, True, "Todos")

    app = AppTest.from_function(page, default_timeout=30).run()
    assert calls == []
    assert any("anterior" in item.value for item in app.warning)
    assert app.button == [] or all(
        button.label != "✨ Analisar período com IA" for button in app.button
    )


def test_period_error_keeps_the_screen(monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "analisar_periodo_agenda",
        lambda _context: (_ for _ in ()).throw(
            GeminiErro("O serviço de IA está indisponível no momento.")
        ),
    )
    _HOLD.clear()
    _HOLD.update(start=START, end=END, rows=_week(), leaves=[], names=NAMES)

    def page():
        from services.agenda_ui import apresentar_analise_periodo
        from tests.test_agenda_analise_ia import _HOLD

        apresentar_analise_periodo(
            _HOLD["start"],
            _HOLD["end"],
            _HOLD["rows"],
            _HOLD["leaves"],
            _HOLD["names"],
        )

    app = AppTest.from_function(page, default_timeout=30).run()
    app.button(key="agenda_analise_ia_btn").click().run()
    assert any("indisponível" in item.value for item in app.error)
    assert app.exception == []
    assert "agenda_analise_ia" not in app.session_state


def test_analysis_view_leaves_the_week_listing_and_does_not_write():
    ui = Path("services/agenda_ui.py").read_text(encoding="utf-8")
    agenda = Path("services/agenda.py").read_text(encoding="utf-8")
    assert '"Análise com IA"' in ui
    assert "Analisar semana com IA" not in ui
    assert "render_week_analysis" not in ui
    assert "dia_mais_carregado" not in agenda
    screen = ui.split("def render(", 1)[1]
    assert screen.index("require_permission") < screen.index("render_period_analysis(")
    week = screen.split('if view == "Semana":', 1)[1].split('elif view == "Mês":', 1)[0]
    assert "apresentar_analise_periodo" not in week
    handler = ui.split("def apresentar_analise_periodo", 1)[1].split(
        "\ndef render_period_analysis", 1
    )[0]
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
