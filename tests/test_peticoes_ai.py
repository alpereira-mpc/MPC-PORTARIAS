from io import BytesIO
import json

import pytest
from pypdf import PdfWriter

from services import ai_service


def _pdf():
    writer=PdfWriter(); writer.add_blank_page(width=72,height=72)
    buffer=BytesIO(); writer.write(buffer); return buffer.getvalue()


def _raw(data):
    return json.dumps({"candidates":[{"content":{"parts":[{"text":json.dumps(data)}]}}]}).encode()


def test_analisar_peticao_pdf_returns_validated_structured_fields(monkeypatch):
    response={"numero_tramita":"116435/26","data_protocolo":"29/09/2026","destinatario":"Presidente do TCE-PB","natureza":"PROVIDENCIAS","assunto":"Assunto","objeto":"Objeto","origem":None,"processo_tc":None,"signatarios":["  ELVIRA SAMARA PEREIRA DE OLIVEIRA  "],"pedidos":[{"descricao":"Pedido A"},{"descricao":""},{"descricao":"Pedido B"}]}
    monkeypatch.setattr(ai_service,"_consultar",lambda *_args,**_kwargs: (_raw(response),"mock"))
    data=ai_service.analisar_peticao_pdf(_pdf())
    assert data["numero_tramita"] == "116435/26" and data["data_protocolo"] == "2026-09-29"
    assert data["origem"] == data["processo_tc"] == ""
    assert data["signatarios"] == ["ELVIRA SAMARA PEREIRA DE OLIVEIRA"]
    assert [item["descricao"] for item in data["pedidos"]] == ["Pedido A","Pedido B"]


@pytest.mark.parametrize("field,value",[("natureza","CATEGORIA_INVENTADA"),("data_protocolo","31/99/2026")])
def test_analisar_peticao_pdf_discards_invalid_widget_values(monkeypatch,field,value):
    response={**ai_service.PETICAO_EXTRAIDA_VAZIA,field:value}
    monkeypatch.setattr(ai_service,"_consultar",lambda *_args,**_kwargs: (_raw(response),"mock"))
    assert ai_service.analisar_peticao_pdf(_pdf())[field] == ""


def test_analisar_peticao_pdf_rejects_malformed_json(monkeypatch):
    monkeypatch.setattr(ai_service,"_consultar",lambda *_args,**_kwargs: (b'{"candidates":[{"content":{"parts":[{"text":"not-json"}]}}]}',"mock"))
    with pytest.raises(ai_service.GeminiErro,match="interpretar"):
        ai_service.analisar_peticao_pdf(_pdf())
