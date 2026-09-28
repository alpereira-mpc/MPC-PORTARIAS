"""Retry, single 503 fallback and technical telemetry of the shared IA layer."""

import json

import pytest

import services.ai_service as ai_service
from database.ia_telemetria import resumo
from services.ai_service import (
    GEMINI_FALLBACK_MODEL,
    GEMINI_MODEL,
    GeminiErro,
    analisar_periodo_agenda,
    analisar_tarefas_ativas,
    extrair_dados_oficio_pdf,
    resumir_documento_pdf,
)
from tests.test_ai_service import (
    OFICIO,
    PDF,
    RPD_DETAILS,
    RPM_DETAILS,
    _gemini_json,
    _http_error,
    _install,
    _key,
    _no_sleep,
    _primary,
    _summary,
)

PROMPT_MARK = "Não faça inferências jurídicas além do conteúdo apresentado."
RESERVE = ai_service._endpoint(GEMINI_FALLBACK_MODEL)


def _fail_times(code, status, successes_after):
    def post(request, index):
        if index <= successes_after:
            raise _http_error(request.full_url, code, status)
        return _summary()

    return post


def _rows(store):
    with store.connection(read_only=True) as connection:
        return [
            dict(row)
            for row in connection.execute("SELECT * FROM ia_telemetria ORDER BY id")
        ]


def _persist(monkeypatch, store):
    def gravar(evento):
        from database.ia_telemetria import registrar

        registrar(store, evento)

    monkeypatch.setattr(ai_service, "_gravar_telemetria", gravar)


def test_success_on_the_first_attempt(monkeypatch):
    calls, sleeps = _install(monkeypatch, lambda request, index: _summary())
    assert resumir_documento_pdf(PDF) == "Objeto: exemplo."
    assert calls == [ai_service.GEMINI_ENDPOINT]
    assert sleeps == []


def test_503_succeeds_on_the_first_retry(monkeypatch):
    calls, sleeps = _install(monkeypatch, _fail_times(503, "UNAVAILABLE", 1))
    assert resumir_documento_pdf(PDF) == "Objeto: exemplo."
    assert calls == [ai_service.GEMINI_ENDPOINT, ai_service.GEMINI_ENDPOINT]
    assert sleeps == [1]
    assert GEMINI_FALLBACK_MODEL not in "".join(calls)


def test_two_503_then_success(monkeypatch):
    calls, sleeps = _install(monkeypatch, _fail_times(503, "UNAVAILABLE", 2))
    assert resumir_documento_pdf(PDF) == "Objeto: exemplo."
    assert len(calls) == 3
    assert sleeps == [1, 2]
    assert all(_primary(url) for url in calls)


def test_three_503_then_success_on_the_fourth_attempt(monkeypatch):
    calls, sleeps = _install(monkeypatch, _fail_times(503, "UNAVAILABLE", 3))
    assert resumir_documento_pdf(PDF) == "Objeto: exemplo."
    assert len(calls) == 4
    assert sleeps == [1, 2, 4]
    assert all(_primary(url) for url in calls)


def test_four_503_then_success_on_the_single_reserve_call(monkeypatch):
    calls, sleeps = _install(monkeypatch, _fail_times(503, "UNAVAILABLE", 4))
    summary = resumir_documento_pdf(PDF)
    assert summary == "Objeto: exemplo."
    assert summary.modelo == GEMINI_FALLBACK_MODEL
    assert calls == [ai_service.GEMINI_ENDPOINT] * 4 + [RESERVE]
    assert sleeps == [1, 2, 4]


def test_four_503_and_reserve_503_stop(monkeypatch):
    calls, sleeps = _install(monkeypatch, _fail_times(503, "UNAVAILABLE", 9))
    with pytest.raises(GeminiErro, match="sobrecarregado"):
        resumir_documento_pdf(PDF)
    assert calls == [ai_service.GEMINI_ENDPOINT] * 4 + [RESERVE]
    assert sleeps == [1, 2, 4]
    assert calls.count(RESERVE) == 1


def test_reserve_is_not_called_again_and_does_not_return_to_the_primary(monkeypatch):
    calls, _sleeps = _install(monkeypatch, _fail_times(503, "UNAVAILABLE", 9))
    with pytest.raises(GeminiErro):
        resumir_documento_pdf(PDF)
    assert calls[-1] == RESERVE
    assert calls.count(RESERVE) == 1
    assert GEMINI_MODEL not in calls[-1] or GEMINI_FALLBACK_MODEL in calls[-1]


def test_400_does_not_retry_or_fall_back(monkeypatch):
    calls, sleeps = _install(monkeypatch, _fail_times(400, "INVALID_ARGUMENT", 3))
    with pytest.raises(GeminiErro, match="não conseguiu ler o PDF"):
        resumir_documento_pdf(PDF)
    assert calls == [ai_service.GEMINI_ENDPOINT]
    assert sleeps == []


def test_authentication_error_does_not_retry_or_fall_back(monkeypatch):
    calls, sleeps = _install(monkeypatch, _fail_times(401, "UNAUTHENTICATED", 3))
    with pytest.raises(GeminiErro, match="recusou a credencial"):
        resumir_documento_pdf(PDF)
    assert calls == [ai_service.GEMINI_ENDPOINT]
    assert sleeps == []


def test_429_stays_on_three_attempts_without_the_reserve(monkeypatch):
    calls, sleeps = _install(monkeypatch, _fail_times(429, "RESOURCE_EXHAUSTED", 9))
    with pytest.raises(GeminiErro, match="muitas solicitações"):
        resumir_documento_pdf(PDF)
    assert calls == [ai_service.GEMINI_ENDPOINT] * 3
    assert sleeps == [1, 2]


def test_daily_quota_fallback_still_switches_once(monkeypatch):
    def post(request, index):
        if _primary(request.full_url):
            raise _http_error(
                request.full_url, 429, "RESOURCE_EXHAUSTED", details=RPD_DETAILS
            )
        return _summary()

    calls, sleeps = _install(monkeypatch, post)
    summary = resumir_documento_pdf(PDF)
    assert summary.modelo == GEMINI_FALLBACK_MODEL
    assert len(calls) == 2
    assert sleeps == []


def test_quota_reserve_still_retries_a_temporary_429(monkeypatch):
    def post(request, index):
        if _primary(request.full_url):
            raise _http_error(
                request.full_url, 429, "RESOURCE_EXHAUSTED", details=RPD_DETAILS
            )
        raise _http_error(
            request.full_url, 429, "RESOURCE_EXHAUSTED", details=RPM_DETAILS
        )

    calls, sleeps = _install(monkeypatch, post)
    with pytest.raises(GeminiErro, match="muitas solicitações"):
        resumir_documento_pdf(PDF)
    assert len(calls) == 4
    assert _primary(calls[0])
    assert all(GEMINI_FALLBACK_MODEL in url for url in calls[1:])
    assert sleeps == [1, 2]


def test_server_backoff_jitter_is_added_and_bounded(monkeypatch):
    monkeypatch.setattr(ai_service.random, "uniform", lambda start, end: 0.2)
    assert ai_service._espera(1, None, servidor=True) == pytest.approx(1.2)
    assert ai_service._espera(2, None, servidor=True) == pytest.approx(2.2)
    assert ai_service._espera(3, None, servidor=True) == pytest.approx(4.2)
    assert ai_service.JITTER_MAX_SECONDS <= 1


def test_jitter_zero_keeps_the_backoff_schedule(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    calls = []

    def post(request):
        calls.append(1)
        raise _http_error(request.full_url, 503, "UNAVAILABLE")

    monkeypatch.setattr(ai_service, "_post", post)
    _key(monkeypatch)
    with pytest.raises(GeminiErro):
        resumir_documento_pdf(PDF)
    assert sleeps == [1, 2, 4]
    assert len(calls) == 5


def test_telemetry_records_first_try_retry_fallback_and_final_failure(
    store, monkeypatch
):
    _persist(monkeypatch, store)
    _install(monkeypatch, lambda request, index: _summary())
    resumir_documento_pdf(PDF)

    _install(monkeypatch, _fail_times(503, "UNAVAILABLE", 1))
    resumir_documento_pdf(PDF)

    _install(monkeypatch, _fail_times(503, "UNAVAILABLE", 4))
    resumir_documento_pdf(PDF)

    _install(monkeypatch, _fail_times(503, "UNAVAILABLE", 9))
    with pytest.raises(GeminiErro):
        resumir_documento_pdf(PDF)

    rows = _rows(store)
    assert [row["sucesso"] for row in rows] == [1, 1, 1, 0]
    assert rows[0]["tentativas"] == 1
    assert rows[0]["retry"] == 0
    assert rows[0]["fallback"] == 0
    assert rows[0]["http_status"] == 200
    assert rows[1]["tentativas"] == 2
    assert rows[1]["retry"] == 1
    assert rows[1]["viu_503"] == 1
    assert rows[1]["fallback"] == 0
    assert rows[2]["tentativas"] == 5
    assert rows[2]["fallback"] == 1
    assert rows[2]["modelo_final"] == GEMINI_FALLBACK_MODEL
    assert rows[3]["sucesso"] == 0
    assert rows[3]["fallback"] == 1
    assert rows[3]["http_status"] == 503
    assert rows[3]["categoria_erro"] == "http_503"
    window = resumo(store, 24)
    assert window["total"] == 4
    assert window["primeira_tentativa"] == 1
    assert window["retry"] == 3
    assert window["fallback"] == 2
    assert window["falhas"] == 1
    assert window["viu_503"] == 3


def test_telemetry_does_not_store_prompt_or_user_content(store, monkeypatch):
    _persist(monkeypatch, store)
    _install(monkeypatch, lambda request, index: _summary())
    resumir_documento_pdf(PDF)
    stored = json.dumps(_rows(store), ensure_ascii=False)
    assert PROMPT_MARK not in stored
    assert "1 0 obj" not in stored
    assert "chave-de-teste" not in stored


def test_telemetry_failure_does_not_hide_the_answer(monkeypatch):
    def broken(evento):
        raise RuntimeError("banco indisponível " + PROMPT_MARK)

    monkeypatch.setattr(ai_service, "_gravar_telemetria", broken)
    calls, _sleeps = _install(monkeypatch, lambda request, index: _summary())
    assert resumir_documento_pdf(PDF) == "Objeto: exemplo."
    assert len(calls) == 1


def test_agenda_tarefas_oficios_and_representacoes_keep_the_central_path(
    store, monkeypatch
):
    _persist(monkeypatch, store)

    def post(request, index):
        text = json.loads(request.data.decode("utf-8"))["contents"][0]["parts"][0][
            "text"
        ]
        if "numero_externo" in text:
            return _gemini_json(OFICIO)
        return _summary()

    _install(monkeypatch, post)
    assert analisar_periodo_agenda({"total_compromissos": 1}) == "Objeto: exemplo."
    assert (
        analisar_tarefas_ativas({"tarefas": [{"titulo": "Item"}]}) == "Objeto: exemplo."
    )
    assert extrair_dados_oficio_pdf(PDF)["numero_externo"] == OFICIO["numero_externo"]
    resumir_documento_pdf(PDF, operacao="representacao_resumo")
    operations = [row["operacao"] for row in _rows(store)]
    assert operations == [
        "agenda_analise",
        "tarefas_analise",
        "oficio_extracao",
        "representacao_resumo",
    ]
    assert [row["modulo"] for row in _rows(store)] == [
        "agenda",
        "tarefas",
        "oficios",
        "representacoes",
    ]


def test_telemetry_schema_failure_does_not_block_initialization(tmp_path, monkeypatch):
    import database.ia_telemetria as telemetria
    from database.store import Store

    monkeypatch.setattr(
        telemetria,
        "ensure_schema",
        lambda store: (_ for _ in ()).throw(RuntimeError("ddl")),
    )
    store = Store(tmp_path / "sem_telemetria.db")
    with store.connection(read_only=True) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == 2


def test_500_retries_four_times_without_the_reserve(monkeypatch):
    calls, sleeps = _install(monkeypatch, _fail_times(500, "INTERNAL", 9))
    with pytest.raises(GeminiErro, match="indisponível"):
        resumir_documento_pdf(PDF)
    assert calls == [ai_service.GEMINI_ENDPOINT] * 4
    assert sleeps == [1, 2, 4]
