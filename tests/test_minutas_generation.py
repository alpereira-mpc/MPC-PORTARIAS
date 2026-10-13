import json
import re

import pytest
from streamlit.testing.v1 import AppTest

from services import ai_service
from services.minutas_generation import (
    HISTORICAL_TEST_PROCESS_KEY,
    MinutasGenerationError,
    MinutasGenerationService,
    _canonical_process_key,
    _clean_text,
)
from services.minutas_extraction import EXTRACTOR_VERSION
from services.minutas_library import MinutasAccessDenied


TARGET_ID = "target-taperoa"
_UI_HOLD = {}


def _record(
    identifier,
    process,
    key,
    *,
    procuradora="Elvira Samara",
    subtype="Instituto/Autarquia",
):
    return {
        "id": identifier,
        "procuradora": procuradora,
        "procuradora_chave": procuradora.casefold(),
        "numero_processo": process,
        "processo_chave": key,
        "exercicio": 2026,
        "subtipo": subtype,
        "auditoria_nome": "auditoria.pdf",
        "auditoria_mime": "application/pdf",
        "auditoria_tamanho": 100,
        "auditoria_path": f"{identifier}/auditoria-0.pdf",
        "parecer_nome": "parecer.pdf",
        "parecer_mime": "application/pdf",
        "parecer_tamanho": 100,
        "parecer_path": f"{identifier}/parecer.pdf",
        "atualizado_em": "2026-10-13T10:00:00+00:00",
    }


def _row(reference, role, text, *, path=None, file_hash=None, position=0):
    file_path = path or f"{reference['id']}/{role}-{position}.pdf"
    return {
        "referencia_id": reference["id"],
        "papel": role,
        "tipo": {
            "auditoria": "Relatório da Auditoria",
            "parecer": "Parecer definitivo",
            "complementar": "Relatório de Análise de Defesa",
        }[role],
        "posicao": position,
        "procuradora": reference["procuradora"],
        "procuradora_chave": reference["procuradora_chave"],
        "arquivo_nome": file_path.rsplit("/", 1)[-1],
        "arquivo_tamanho": 100,
        "arquivo_path": file_path,
        "arquivo_sha256": file_hash or (reference["id"] + role + str(position)),
        "texto_integral": text,
        "unidades_json": json.dumps([{"pagina": 1, "texto": text}], ensure_ascii=False),
        "secoes_json": "[]",
        "status": "PROCESSADO",
        "extrator_versao": EXTRACTOR_VERSION,
        "erro": "",
        "atualizado_em": "2026-10-13T10:00:00+00:00",
    }


class Repository:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    def list_extractions_by_procuradora(self, key):
        self.queries.append(key)
        return list(self.rows)

    def list_extraction_inventory_by_procuradora(self, key):
        return [row for row in self.rows if row["procuradora_chave"] == key]

    def list_documents(self, reference_id):
        return [
            {
                "id": f"document-{index}",
                "referencia_id": row["referencia_id"],
                "tipo": row["tipo"],
                "posicao": row["posicao"],
                "nome": row["arquivo_nome"],
                "mime": "application/pdf",
                "tamanho": row["arquivo_tamanho"],
                "path": row["arquivo_path"],
            }
            for index, row in enumerate(self.rows)
            if row["referencia_id"] == reference_id
            and row["papel"] == "complementar"
        ]

    def get_extraction(self, path):
        return next((row for row in self.rows if row["arquivo_path"] == path), None)


class Library:
    def __init__(self, records, rows, *, authorized=True):
        self.records = {record["id"]: record for record in records}
        self.repository = Repository(rows)
        self.storage = object()
        self.authorized = authorized

    def _authorize(self):
        if not self.authorized:
            raise MinutasAccessDenied("Acesso não autorizado a este módulo.")
        return "admin@test.local"

    def get(self, identifier):
        self._authorize()
        return self.records[identifier]

    def list(self):
        self._authorize()
        return list(self.records.values())


def _scenario():
    target = _record(TARGET_ID, "TC 01653/26", HISTORICAL_TEST_PROCESS_KEY)
    history = _record("history-rio", "TC 02720/25", "tc0272025", subtype="Prefeitura")
    other = _record(
        "other-office",
        "TC 99999/26",
        "tc9999926",
        procuradora="Outra Procuradora",
    )
    target_opinion = _row(
        target,
        "parecer",
        "SEGREDO DO PARECER-ALVO",
        path=target["parecer_path"],
        file_hash="target-opinion-hash",
    )
    rows = [
        _row(
            target,
            "auditoria",
            "Irregularidade previdenciária na página original.\n"
            "TRAMITA - Sistema de Tramitação de Processos e Documentos",
        ),
        _row(target, "complementar", "Defesa apresentada pelo gestor.", position=1),
        target_opinion,
        _row(history, "parecer", "Parecer histórico com fundamentação previdenciária."),
        _row(history, "auditoria", "Auditoria histórica sobre contribuições."),
        _row(
            history,
            "auditoria",
            "SEGREDO DO PARECER-ALVO",
            path="history-rio/duplicata.pdf",
            file_hash="target-opinion-hash",
            position=2,
        ),
        _row(other, "parecer", "CONTEÚDO DE OUTRA PROCURADORA"),
    ]
    return target, history, other, rows


@pytest.mark.parametrize(
    ("display", "stored_key"),
    (
        ("01653/26", "0165326"),
        ("TC 01653/26", "tc0165326"),
        ("TC-01653/26", "tc0165326"),
        ("Processo TC 01653/26", "processotc0165326"),
    ),
)
def test_taperoa_equivalent_formats_are_found_and_accepted(display, stored_key):
    target, history, other, rows = _scenario()
    target.update(numero_processo=display, processo_chave=stored_key)
    service = MinutasGenerationService(
        Library([target, history, other], rows), generate=lambda _context: {}
    )

    assert service.targets() == [target]
    assert service.prepare_context(target["id"])["contexto"]["processo_alvo"][
        "numero"
    ] == display


@pytest.mark.parametrize(
    "value",
    (
        "01654/26",
        "01653/25",
        "11653/26",
        "01653260",
        "TC 01653/26 adicional",
    ),
)
def test_other_processes_are_strictly_rejected(value):
    assert _canonical_process_key(value) != HISTORICAL_TEST_PROCESS_KEY
    target = _record(TARGET_ID, value, re.sub(r"[^0-9a-z]", "", value.casefold()))
    service = MinutasGenerationService(Library([target], []))

    assert service.targets() == []
    with pytest.raises(MinutasGenerationError, match="somente o processo histórico"):
        service.prepare_context(target["id"])


def test_clean_text_removes_only_specific_validation_lines():
    text = "\n".join(
        (
            "A tramitação processual deve observar o contraditório.",
            "O ato administrativo foi registrado no TRAMITA em 10/01/2026.",
            "A validação jurídica do ato compete ao gestor.",
            "Verifique a autenticidade deste documento em https://validar.exemplo/abc",
            "Código verificador: ABC123",
            "Documento assinado eletronicamente por Servidor Exemplo",
            "TRAMITA - Sistema de Tramitação de Processos e Documentos",
        )
    )

    cleaned = _clean_text(text)

    assert "tramitação processual" in cleaned
    assert "registrado no TRAMITA" in cleaned
    assert "validação jurídica" in cleaned
    assert "Verifique a autenticidade" not in cleaned
    assert "Código verificador" not in cleaned
    assert "assinado eletronicamente" not in cleaned
    assert "Sistema de Tramitação" not in cleaned


def test_context_uses_target_instruction_and_excludes_contamination():
    target, history, other, rows = _scenario()
    library = Library([target, history, other], rows)
    service = MinutasGenerationService(library, generate=lambda _context: {})

    prepared = service.prepare_context(target["id"])
    context = prepared["contexto"]
    serialized = json.dumps(context, ensure_ascii=False)

    assert context["processo_alvo"]["parecer_definitivo_excluido"] is True
    assert [source["papel"] for source in context["documentos_processo_alvo"]] == [
        "auditoria",
        "complementar",
    ]
    assert context["documentos_processo_alvo"][0]["conteudo"][0]["pagina"] == 1
    assert "Irregularidade previdenciária" in serialized
    assert "TRAMITA" not in serialized
    assert "SEGREDO DO PARECER-ALVO" not in serialized
    assert "CONTEÚDO DE OUTRA PROCURADORA" not in serialized
    assert [item["processo"] for item in context["referencias_historicas"]] == [
        history["numero_processo"]
    ]
    assert library.repository.queries == [target["procuradora_chave"]]


def test_generation_calls_shared_ai_once_and_returns_only_metrics_not_context():
    target, history, other, rows = _scenario()
    calls = []

    def generate(context):
        calls.append(context)
        return {
            "texto": "# PARECER\n\nMinuta estruturada.",
            "modelo": "modelo-configurado",
            "duracao_ms": 25,
            "tokens_entrada": 100,
            "tokens_saida": 40,
            "tokens_total": 140,
        }

    service = MinutasGenerationService(
        Library([target, history, other], rows), generate=generate
    )
    result = service.generate(target["id"])

    assert len(calls) == 1
    assert result["texto"].startswith("# PARECER")
    assert result["modelo"] == "modelo-configurado"
    assert "contexto" not in result
    assert result["fontes_alvo"] == 2
    assert any("TC 01653/26" in source for source in result["fontes_utilizadas"])


def test_context_limit_fails_without_cutting_target_or_calling_ai(monkeypatch):
    from services import minutas_generation

    target, history, other, rows = _scenario()
    calls = []
    monkeypatch.setattr(minutas_generation, "MAX_CONTEXT_BYTES", 100)
    service = MinutasGenerationService(
        Library([target, history, other], rows), generate=calls.append
    )

    with pytest.raises(MinutasGenerationError, match="essenciais"):
        service.generate(target["id"])

    assert calls == []


def test_missing_processed_audit_and_unauthorized_access_fail_before_ai():
    target, history, other, rows = _scenario()
    calls = []
    without_target = [row for row in rows if row["referencia_id"] != target["id"]]
    service = MinutasGenerationService(
        Library([target, history, other], without_target), generate=calls.append
    )
    with pytest.raises(MinutasGenerationError, match="extração pendente"):
        service.generate(target["id"])

    denied = MinutasGenerationService(
        Library([target, history], rows, authorized=False), generate=calls.append
    )
    with pytest.raises(MinutasAccessDenied):
        denied.generate(target["id"])
    assert calls == []


@pytest.mark.parametrize(
    ("status", "version", "message"),
    (
        ("ATENCAO", EXTRACTOR_VERSION, "necessita atenção"),
        ("PROCESSADO", "extrator-antigo", "desatualizada"),
    ),
)
def test_invalid_or_outdated_instruction_blocks_generation(status, version, message):
    target, history, other, rows = _scenario()
    target_audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    target_audit.update(status=status, extrator_versao=version)
    calls = []
    service = MinutasGenerationService(
        Library([target, history, other], rows), generate=calls.append
    )

    with pytest.raises(MinutasGenerationError, match=message):
        service.generate(target["id"])

    assert calls == []


def test_shared_gemini_request_uses_models_limits_and_usage(monkeypatch):
    seen = {}
    raw = json.dumps(
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "Minuta experimental."}]},
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 8,
                "totalTokenCount": 28,
            },
        }
    ).encode()

    def execute(builder, label, operacao):
        request = builder("test-key", "modelo-em-uso")
        seen.update(
            payload=json.loads(request.data.decode()), label=label, operacao=operacao
        )
        return raw, "modelo-em-uso"

    monkeypatch.setattr(ai_service, "_executar", execute)
    result = ai_service.gerar_minuta_parecer({"fontes": ["conteúdo"]})

    assert seen["operacao"] == "minutas_geracao"
    assert seen["payload"]["generationConfig"]["maxOutputTokens"] == 8192
    sent = seen["payload"]["contents"][0]["parts"][0]["text"]
    assert ai_service.PROMPT_MINUTA_PARECER in sent
    assert "conteúdo" in sent
    assert result["modelo"] == "modelo-em-uso"
    assert result["tokens_total"] == 28


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (
            {
                "candidates": [
                    {
                        "finishReason": "MAX_TOKENS",
                        "content": {"parts": [{"text": "Minuta parcial"}]},
                    }
                ]
            },
            "incompleta",
        ),
        (
            {"candidates": []},
            "não retornou conteúdo",
        ),
        (
            {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}},
            "bloqueada",
        ),
        (
            {
                "candidates": [
                    {
                        "finishReason": "SAFETY",
                        "content": {"parts": [{"text": "Texto bloqueado"}]},
                    }
                ]
            },
            "filtros de segurança",
        ),
    ),
)
def test_minuta_rejects_incomplete_empty_or_blocked_response(
    monkeypatch, payload, message
):
    monkeypatch.setattr(
        ai_service,
        "_executar",
        lambda *_args, **_kwargs: (json.dumps(payload).encode(), "modelo-em-uso"),
    )

    with pytest.raises(ai_service.GeminiErro, match=message):
        ai_service.gerar_minuta_parecer({"fontes": ["conteúdo"]})


def test_generation_propagates_controlled_gemini_failure():
    target, history, other, rows = _scenario()

    def unavailable(_context):
        raise ai_service.GeminiErro("O serviço de IA está indisponível no momento.")

    service = MinutasGenerationService(
        Library([target, history, other], rows), generate=unavailable
    )

    with pytest.raises(ai_service.GeminiErro, match="indisponível"):
        service.generate(target["id"])


def test_ui_calls_generation_only_on_click_and_keeps_result_on_rerun(monkeypatch):
    from services import minutas_ia_ui

    target = _record(TARGET_ID, "TC 01653/26", HISTORICAL_TEST_PROCESS_KEY)
    calls = []
    revision = {"value": "revision-1"}
    summary_calls = []

    class Generator:
        def __init__(self, _library):
            pass

        def targets(self):
            return [target]

        def source_summary(self, identifier):
            summary_calls.append(identifier)
            return {
                "fontes_alvo_previstas": 1,
                "referencias_historicas_disponiveis": 3,
                "issues": [],
                "fontes_versao": revision["value"],
            }

        def generate(self, identifier):
            calls.append(identifier)
            return {
                "texto": "# MINUTA\n\nConteúdo para revisão.",
                "modelo": "modelo-teste",
                "duracao_ms": 100,
                "contexto_bytes": 1000,
                "fontes_alvo": 1,
                "referencias_historicas": 3,
                "tokens_total": None,
                "fontes_versao": revision["value"],
                "fontes_utilizadas": ["TC 01653/26 — Relatório da Auditoria"],
                "omitidas_por_limite": [],
            }

    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args: object())
    monkeypatch.setattr(minutas_ia_ui, "MinutasGenerationService", Generator)
    _UI_HOLD.clear()

    def page():
        from services.minutas_ia_ui import _generator_tab

        _generator_tab(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    assert calls == []
    assert summary_calls == [TARGET_ID]

    app.button(key=f"minutas_generation_submit_{TARGET_ID}").click().run()
    assert calls == [TARGET_ID]
    assert any("Conteúdo para revisão" in item.value for item in app.markdown)
    assert any("Conteúdo para revisão" in item.value for item in app.code)

    app.run()
    assert calls == [TARGET_ID]
    assert any("Conteúdo para revisão" in item.value for item in app.markdown)

    revision["value"] = "revision-2"
    app.run()
    assert calls == [TARGET_ID]
    assert not any("Conteúdo para revisão" in item.value for item in app.markdown)
    assert any("fontes foram alteradas" in item.value for item in app.info)
