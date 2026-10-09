"""Structured AI suggestions for a directly registered Representação."""

from io import BytesIO
import json

import pytest
from pypdf import PdfWriter

from services import ai_service


def _pdf():
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _response(data):
    return json.dumps(
        {"candidates": [{"content": {"parts": [{"text": json.dumps(data)}]}}]}
    ).encode()


def _suggestion(**changes):
    data = {
        "titulo": "Fiscalização de contratação pública",
        "objeto": "Apuração de possível irregularidade na contratação.",
        "origem": "DE_OFICIO",
        "data_abertura": "2026-09-10",
        "representado": "Município de Exemplo",
        "tema": "Licitações",
        "procurador_responsavel": "Marcílio Toscano Franca Filho",
        "prioridade": "",
        "procuradores_signatarios": ["Marcílio Toscano Franca Filho"],
        "assessores": [],
        "observacoes_internas": "",
        "numero_processo": "TC 012345/26",
        "data_protocolo": "2026-09-20",
        "relator": "André Carlo Torres Pontes",
        "fase_processual": "INSTRUCAO",
        "possui_medida_cautelar": "SIM",
        "observacoes_protocolo": "",
    }
    data.update(changes)
    return data


def test_representation_extraction_uses_structured_service_and_validates_fields(
    monkeypatch,
):
    calls = []
    payload = _suggestion(
        data_abertura="10/09/2026",
        data_protocolo="20/09/2026",
        procuradores_signatarios=["  MARCILIO TOSCANO FRANCA FILHO  "],
        extra="ignored",
    )

    def consultar(document, prompt, label, schema=None, *, operacao=None):
        calls.append((document, prompt, label, schema, operacao))
        return _response(payload), "mock"

    monkeypatch.setattr(ai_service, "_consultar", consultar)
    pdf = _pdf()
    result = ai_service.extrair_dados_representacao_pdf(pdf)

    assert len(calls) == 1
    assert calls[0][0] == pdf
    assert calls[0][3]["type"] == "OBJECT"
    assert "represent" in calls[0][4]
    assert result["titulo"] == payload["titulo"]
    assert result["data_abertura"] == "2026-09-10"
    assert result["data_protocolo"] == "2026-09-20"
    assert result["procuradores_signatarios"] == ["MARCILIO TOSCANO FRANCA FILHO"]
    assert result["possui_medida_cautelar"] == "SIM"
    assert "extra" not in result


@pytest.mark.parametrize(
    "field,value,expected",
    [
        ("origem", "ORIGEM_INVENTADA", ""),
        ("prioridade", "MUITO_ALTA", ""),
        ("fase_processual", "FASE_INVENTADA", ""),
        ("data_abertura", "31/02/2026", ""),
        ("data_protocolo", "2026-99-30", ""),
        ("possui_medida_cautelar", "TALVEZ", "NAO_IDENTIFICADO"),
    ],
)
def test_representation_extraction_discards_invalid_widget_values(
    monkeypatch, field, value, expected
):
    monkeypatch.setattr(
        ai_service,
        "_consultar",
        lambda *_args, **_kwargs: (_response(_suggestion(**{field: value})), "mock"),
    )
    assert ai_service.extrair_dados_representacao_pdf(_pdf())[field] == expected


def test_representation_extraction_keeps_caution_unknown_and_missing_fields(
    monkeypatch,
):
    payload = {
        "titulo": "Representação sem protocolo visível",
        "possui_medida_cautelar": "NAO_IDENTIFICADO",
    }
    monkeypatch.setattr(
        ai_service,
        "_consultar",
        lambda *_args, **_kwargs: (_response(payload), "mock"),
    )
    result = ai_service.extrair_dados_representacao_pdf(_pdf())
    assert result["possui_medida_cautelar"] == "NAO_IDENTIFICADO"
    assert result["numero_processo"] == ""
    assert result["relator"] == ""
    assert result["procuradores_signatarios"] == []


def test_representation_extraction_rejects_malformed_json(monkeypatch):
    monkeypatch.setattr(
        ai_service,
        "_consultar",
        lambda *_args, **_kwargs: (
            b'{"candidates":[{"content":{"parts":[{"text":"not-json"}]}}]}',
            "mock",
        ),
    )
    with pytest.raises(ai_service.GeminiErro, match="interpretar"):
        ai_service.extrair_dados_representacao_pdf(_pdf())
