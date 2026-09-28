from datetime import datetime, timedelta
import json

from streamlit.testing.v1 import AppTest

import services.ai_service as ai_service
from database.tarefas import TarefasStore
from services.access import Principal
from services.audit import INSTITUTIONAL_TZ
from services.tarefas import contexto_analise_tarefas


NOW = datetime(2026, 9, 28, 10, 0, tzinfo=INSTITUTIONAL_TZ)
_AI_HOLD = {}


def _principal(identifier=1):
    return Principal(
        identifier,
        f"Usuário {identifier}",
        f"usuario{identifier}@test.local",
        "USUARIO",
        True,
        False,
        False,
        False,
        False,
        (),
    )


def _row(title, status, *, due=None, hour=None, priority="NORMAL", owner=1):
    return {
        "id": 999,
        "owner_user_id": owner,
        "titulo": title,
        "descricao": "Descrição da tarefa",
        "categoria": "",
        "prioridade": priority,
        "status": status,
        "prazo_data": due,
        "prazo_hora": hour,
        "origem_modulo": "oficios",
        "origem_id": "interno",
        "lembretes": [],
    }


def test_task_analysis_context_excludes_every_closed_status():
    context, _ = contexto_analise_tarefas(
        [
            _row("Ativa", "A_FAZER", due="2026-09-28", priority="ALTA"),
            _row("Em andamento", "EM_ANDAMENTO"),
            _row("Aguardando", "AGUARDANDO"),
            _row("Concluída", "CONCLUIDA"),
            _row("Cancelada", "CANCELADA"),
        ],
        NOW,
    )

    assert [task["titulo"] for task in context["tarefas"]] == [
        "Ativa",
        "Em andamento",
        "Aguardando",
    ]
    assert context["contagens"]["total_ativas"] == 3
    assert "id" not in context["tarefas"][0]
    assert "origem_id" not in context["tarefas"][0]


def test_task_analysis_context_classifies_deadlines_deterministically():
    context, _ = contexto_analise_tarefas(
        [
            _row("Vencida", "A_FAZER", due="2026-09-27"),
            _row("Hoje com hora", "A_FAZER", due="2026-09-28", hour="11:00"),
            _row("Próxima", "A_FAZER", due="2026-10-05"),
            _row("Futura", "A_FAZER", due="2026-10-06"),
            _row("Sem prazo", "A_FAZER"),
        ],
        NOW,
    )

    assert [task["situacao_prazo"] for task in context["tarefas"]] == [
        "VENCIDA",
        "HOJE",
        "PROXIMOS_7_DIAS",
        "FUTURA",
        "SEM_PRAZO",
    ]
    assert context["contagens"] == {
        "total_ativas": 5,
        "vencidas": 1,
        "hoje": 1,
        "proximos_7_dias": 1,
        "sem_prazo": 1,
        "por_prioridade": {"Baixa": 0, "Normal": 5, "Alta": 0, "Urgente": 0},
        "por_status": {"A fazer": 5, "Em andamento": 0, "Aguardando": 0},
    }


def test_analysis_query_is_private_and_excludes_history(store):
    repo = TarefasStore(store)
    own = repo.create(1, {"titulo": "Ativa própria"})
    closed = repo.create(1, {"titulo": "Concluída própria"})
    other = repo.create(2, {"titulo": "Ativa alheia"})
    repo.change_status(closed["id"], 1, "CONCLUIDA")

    rows = repo.list_active_for_analysis(1)

    assert [row["id"] for row in rows] == [own["id"]]
    assert other["id"] not in [row["id"] for row in rows]


def test_task_analysis_request_uses_central_gemini_and_active_context(monkeypatch):
    seen = {}

    def post(request):
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return json.dumps(
            {"candidates": [{"content": {"parts": [{"text": "Briefing seguro."}]}}]}
        ).encode()

    monkeypatch.setattr(ai_service, "_post", post)
    import streamlit as st

    monkeypatch.setattr(st, "secrets", {"GEMINI_API_KEY": "chave-teste"})
    context, _ = contexto_analise_tarefas(
        [
            _row("Ativa enviada", "A_FAZER"),
            _row("Concluída excluída", "CONCLUIDA"),
            _row("Cancelada excluída", "CANCELADA"),
        ],
        NOW,
    )

    assert ai_service.analisar_tarefas_ativas(context) == "Briefing seguro."
    body = seen["body"]["contents"][0]["parts"][0]["text"]
    assert ai_service.PROMPT_ANALISE_TAREFAS in body
    assert "Ativa enviada" in body
    assert "Concluída excluída" not in body
    assert "Cancelada excluída" not in body


def test_empty_active_list_does_not_call_gemini(store, monkeypatch):
    calls = []
    repo = TarefasStore(store)
    principal = _principal()
    monkeypatch.setattr(
        ai_service, "analisar_tarefas_ativas", lambda context: calls.append(context)
    )
    _AI_HOLD.clear()
    _AI_HOLD.update(repo=repo, principal=principal)

    def page():
        from services.tarefas_ui import apresentar_analise_tarefas
        from tests.test_tarefas_analise_ia import _AI_HOLD

        apresentar_analise_tarefas(_AI_HOLD["repo"], _AI_HOLD["principal"])

    app = AppTest.from_function(page, default_timeout=30).run()
    assert calls == []
    assert any("Não há tarefas ativas" in item.value for item in app.info)
    assert not any(button.key == "tarefas_analise_ia_btn" for button in app.button)


def test_successful_analysis_is_explicit_and_survives_reruns(store, monkeypatch):
    repo = TarefasStore(store)
    principal = _principal()
    repo.create(
        principal.id,
        {
            "titulo": "Providência ativa",
            "prazo_data": (NOW.date() + timedelta(days=1)).isoformat(),
            "prioridade": "ALTA",
        },
    )
    calls = []

    def analyze(context):
        calls.append(context)
        return "Briefing das tarefas ativas."

    monkeypatch.setattr(ai_service, "analisar_tarefas_ativas", analyze)
    _AI_HOLD.clear()
    _AI_HOLD.update(repo=repo, principal=principal)

    def page():
        from services.tarefas_ui import apresentar_analise_tarefas
        from tests.test_tarefas_analise_ia import _AI_HOLD

        apresentar_analise_tarefas(_AI_HOLD["repo"], _AI_HOLD["principal"])

    app = AppTest.from_function(page, default_timeout=30).run()
    assert calls == []
    app.button(key="tarefas_analise_ia_btn").click().run()
    assert len(calls) == 1
    assert any("Briefing das tarefas ativas." in item.value for item in app.markdown)
    app.run()
    assert len(calls) == 1
    assert app.button(key="tarefas_analise_ia_btn").label == "↻ Atualizar análise"


def test_ai_failure_never_writes_tasks(store, monkeypatch):
    from services.ai_service import GeminiErro

    repo = TarefasStore(store)
    principal = _principal()
    task = repo.create(principal.id, {"titulo": "Permanece inalterada"})
    before = repo.get(task["id"], principal.id)
    monkeypatch.setattr(
        ai_service,
        "analisar_tarefas_ativas",
        lambda _context: (_ for _ in ()).throw(GeminiErro("IA indisponível.")),
    )
    _AI_HOLD.clear()
    _AI_HOLD.update(repo=repo, principal=principal)

    def page():
        from services.tarefas_ui import apresentar_analise_tarefas
        from tests.test_tarefas_analise_ia import _AI_HOLD

        apresentar_analise_tarefas(_AI_HOLD["repo"], _AI_HOLD["principal"])

    app = AppTest.from_function(page, default_timeout=30).run()
    app.button(key="tarefas_analise_ia_btn").click().run()

    assert any("IA indisponível." in item.value for item in app.error)
    assert repo.get(task["id"], principal.id) == before
