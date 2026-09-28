"""Recent IA operations shown on Administração → Sistema → Saúde da IA."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from streamlit.testing.v1 import AppTest

from database.ia_telemetria import LIMITE_RECENTES, ensure_schema, recentes
from services.access import Principal
from services.audit import INSTITUTIONAL_TZ
from services.system_ui import (
    linha_operacao_recente,
    rotulo_duracao,
    rotulo_modelo,
    rotulo_operacao,
)

_HOLD = {}
AGORA = datetime(2026, 9, 28, 17, 42, tzinfo=INSTITUTIONAL_TZ)


def _admin():
    return Principal(
        1,
        "Administrador",
        "admin@test.local",
        "ADMINISTRADOR",
        True,
        True,
        True,
        True,
        True,
        (),
    )


def _usuario():
    return Principal(
        2,
        "Usuário",
        "usuario@test.local",
        "USUARIO",
        True,
        False,
        False,
        False,
        False,
        (),
    )


def _inserir(store, criado_em, **campos):
    ensure_schema(store)
    base = {
        "modulo": "agenda",
        "operacao": "agenda_analise",
        "modelo_principal": "gemini-3.5-flash-lite",
        "modelo_final": "gemini-3.5-flash-lite",
        "tentativas": 1,
        "retry": 0,
        "fallback": 0,
        "sucesso": 1,
        "http_status": 200,
        "categoria_erro": "",
        "duracao_ms": 820,
        "viu_429": 0,
        "viu_500": 0,
        "viu_503": 0,
    }
    base.update(campos)
    with store.connection() as connection:
        connection.execute(
            "INSERT INTO ia_telemetria ("
            "criado_em, modulo, operacao, modelo_principal, modelo_final, "
            "tentativas, retry, fallback, sucesso, http_status, categoria_erro, "
            "duracao_ms, viu_429, viu_500, viu_503) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                criado_em,
                base["modulo"],
                base["operacao"],
                base["modelo_principal"],
                base["modelo_final"],
                base["tentativas"],
                base["retry"],
                base["fallback"],
                base["sucesso"],
                base["http_status"],
                base["categoria_erro"],
                base["duracao_ms"],
                base["viu_429"],
                base["viu_500"],
                base["viu_503"],
            ),
        )


def _momento(offset):
    return (
        datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc) + timedelta(seconds=offset)
    ).isoformat()


def test_recent_operations_are_newest_first_and_capped_at_twenty(store):
    for offset in range(25):
        _inserir(store, _momento(offset), operacao=f"op_{offset:02d}")
    rows = recentes(store)
    assert len(rows) == LIMITE_RECENTES == 20
    assert [row["operacao"] for row in rows] == [
        f"op_{offset:02d}" for offset in range(24, 4, -1)
    ]
    assert rows[0]["criado_em"] > rows[-1]["criado_em"]


def test_recent_query_is_one_indexed_select(store):
    _inserir(store, _momento(1))
    calls = []
    original = store.connection

    @contextmanager
    def wrapped(*args, **kwargs):
        with original(*args, **kwargs) as connection:
            connection.set_trace_callback(
                lambda sql: calls.append(" ".join(sql.split()))
            )
            yield connection

    store.connection = wrapped
    recentes(store)
    selects = [sql for sql in calls if sql.upper().startswith("SELECT")]
    assert len(selects) == 1
    assert "ORDER BY criado_em DESC" in selects[0]
    assert "LIMIT 20" in selects[0]
    with store.connection(read_only=True) as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT criado_em FROM ia_telemetria "
            "ORDER BY criado_em DESC LIMIT 20"
        ).fetchall()
    assert any("ia_telemetria_criado_em_idx" in str(tuple(row)) for row in plan)


def test_labels_duration_result_and_models():
    assert rotulo_operacao("agenda_analise") == "Agenda"
    assert rotulo_operacao("tarefas_analise") == "Tarefas"
    assert rotulo_operacao("oficio_extracao") == "Ofícios"
    assert rotulo_operacao("representacao_resumo") == "Representações"
    assert rotulo_operacao("laboratorio_resumo") == "Laboratório"
    assert rotulo_operacao("operacao_nova") == "operacao_nova"
    assert rotulo_operacao("Maria Aparecida") == "—"
    assert rotulo_duracao(820) == "820 ms"
    assert rotulo_duracao(2400) == "2,4 s"
    assert rotulo_duracao(27346) == "27,3 s"
    assert rotulo_modelo("gemini-3.5-flash-lite") == "Gemini 3.5 Flash Lite"
    assert rotulo_modelo("gemini-3.1-flash-lite") == "Gemini 3.1 Flash Lite"
    sucesso = linha_operacao_recente(
        {
            "criado_em": "2026-09-28T20:42:18+00:00",
            "operacao": "tarefas_analise",
            "sucesso": 1,
            "tentativas": 1,
            "retry": 0,
            "fallback": 0,
            "modelo_final": "gemini-3.5-flash-lite",
            "duracao_ms": 4200,
            "http_status": 200,
        },
        AGORA,
    )
    assert sucesso["Resultado"] == "Sucesso"
    assert sucesso["Retry"] == "Não"
    assert sucesso["Fallback"] == "Não"
    assert sucesso["Modelo final"] == "Gemini 3.5 Flash Lite"
    assert sucesso["Tempo"] == "4,2 s"
    assert sucesso["HTTP"] == "200"
    assert sucesso["Operação"] == "Tarefas"
    falha = linha_operacao_recente(
        {
            "criado_em": "2026-09-27T18:00:00+00:00",
            "operacao": "representacao_resumo",
            "sucesso": 0,
            "tentativas": 5,
            "retry": 1,
            "fallback": 1,
            "modelo_final": "gemini-3.1-flash-lite",
            "duracao_ms": 15100,
            "http_status": None,
        },
        AGORA,
    )
    assert falha["Resultado"] == "Falha"
    assert falha["Retry"] == "Sim"
    assert falha["Fallback"] == "Sim"
    assert falha["Modelo final"] == "Gemini 3.1 Flash Lite"
    assert falha["HTTP"] == "—"
    assert falha["Horário"] == "27/09/2026 15:00"
    assert "prompt" not in str(falha).lower()


def test_unknown_operation_and_missing_http_do_not_break_the_row():
    row = linha_operacao_recente(
        {
            "criado_em": "nao-e-data",
            "operacao": "texto livre com nome",
            "sucesso": 0,
            "tentativas": 2,
            "retry": 1,
            "fallback": 0,
            "modelo_final": "resposta secreta da IA",
            "duracao_ms": None,
            "http_status": None,
        },
        AGORA,
    )
    assert row["Operação"] == "—"
    assert row["Modelo final"] == "—"
    assert row["HTTP"] == "—"
    assert row["Horário"] == "—"
    assert row["Tempo"] == "—"
    assert "secreta" not in str(row)


def test_recent_failure_keeps_the_aggregate_cards(store, monkeypatch):
    import database.ia_telemetria as telemetria

    def fail(store, limite=20):
        raise RuntimeError("prompt secreto e stack trace")

    monkeypatch.setattr(telemetria, "recentes", fail)
    _HOLD.clear()
    _HOLD.update(store=store, principal=_admin())

    def page():
        from services.system_ui import render_ai_health
        from tests.test_ia_saude import _HOLD

        render_ai_health(_HOLD["store"], _HOLD["principal"])

    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception
    visible = " ".join(item.value for item in list(app.markdown) + list(app.caption))
    assert "Últimas 24 horas" in visible
    assert "Últimos 30 dias" in visible
    assert "não puderam ser carregadas" in visible
    assert "prompt secreto" not in visible
    assert "Traceback" not in visible


def _pagina_saude():
    from services.system_ui import render_ai_health
    from tests.test_ia_saude import _HOLD

    render_ai_health(_HOLD["store"], _HOLD["principal"])


def test_average_time_uses_the_recent_operations_format(store):
    _HOLD.clear()
    _HOLD.update(store=store, principal=_admin())
    for milissegundos, esperado in ((27346, "27,3 s"), (820, "820 ms")):
        with store.connection() as connection:
            connection.execute("DELETE FROM ia_telemetria")
        _inserir(store, _momento(1), duracao_ms=milissegundos)
        app = AppTest.from_function(_pagina_saude, default_timeout=30).run()
        assert not app.exception
        tempos = [
            metric.value for metric in app.metric if metric.label == "Tempo médio"
        ]
        assert tempos == [esperado, esperado, esperado]


def test_recent_table_stays_behind_the_admin_permission(store):
    from services.system_ui import render_ai_health

    with pytest.raises(ValueError, match="não autorizado"):
        render_ai_health(store, _usuario())
