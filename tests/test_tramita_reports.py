from datetime import datetime
import sys
import types

import pytest

from database.store import Store
from database.tramita_reports import TramitaReportsStore
from services.tramita_reports import (
    MOVEMENT_HEADERS,
    STOCK_HEADERS,
    aging_band,
    file_hash,
    is_result,
    official_procurador,
    parse_movements,
    parse_stock,
    turnaround_days,
)
from services.access import Principal, has_permission


def movement_file(*rows):
    return ("\t".join(MOVEMENT_HEADERS) + "\n" + "\n".join("\t".join(row) for row in rows)).encode("cp1252")


def test_tsv_movements_use_cp1252_and_ignore_grouping_rows():
    content = movement_file(
        ("Manoel Antônio dos Santos Neto", "", "", "", "", "", "", "", ""),
        ("04124/26", "Processo", "Denúncia", "Prefeitura Municipal de Mãe d'Água", "03/08/2026 09:25", "Manoel Antônio dos Santos Neto", "Ao Procurador", "06/08/2026 11:29", "Analisado Com Cota"),
    )
    rows, unknown = parse_movements(content)
    assert unknown == [] and len(rows) == 1
    assert rows[0]["protocolo"] == "04124/26"
    assert rows[0]["procurador"] == "Manoel Antônio dos Santos Neto"


def test_binary_xls_stock_headers_and_accented_procurador(monkeypatch):
    class Sheet:
        ncols, nrows = len(STOCK_HEADERS), 2
        def cell_value(self, row, col):
            values = [*STOCK_HEADERS] if row == 0 else ["Processo", "09137/18", "", "Representação", "Prefeitura Municipal de Cabedelo", "Recurso", "Bradson Tiberio Luna Camelo", 168, "Assistente", 2, 170, "30 meses"]
            return values[col]
    monkeypatch.setitem(sys.modules, "xlrd", types.SimpleNamespace(open_workbook=lambda **kwargs: types.SimpleNamespace(sheet_by_index=lambda _: Sheet())))
    rows, unknown = parse_stock(b"\xd0\xcf\x11\xe0fake")
    assert unknown == []
    assert rows[0]["procurador"] == "Bradson Tibério Luna Camelo"
    assert rows[0]["dias_com_procurador"] == 168


def test_binary_xls_stock_locates_real_tramita_header_after_title(monkeypatch):
    actual_header = ["TIPO", "PROTOCOLO", "DIGITAL", "SUBCATEGORIA", "JURISDICIONADO", "FASE", "PROCURADOR(A)", "DIAS COM PROCURADOR(A)", "ASSISTENTE", "DIAS COM ASSISTENTE", "DIAS NA PROGE", "PRESCRIÇÃO"]
    process = ["Processo", "09137/18", "Sim", "Representação", "Prefeitura Municipal de Cabedelo", "Recurso", "Isabella Barbosa Marinho Falcão", 168, "Agda", 112, 168, "30 meses"]
    class Sheet:
        ncols, nrows = 12, 6
        def cell_value(self, row, col):
            rows = [[""] * len(actual_header), ["Processos da Proge"] + [""] * 11, [""] * len(actual_header), [""] * len(actual_header), actual_header, process]
            return rows[row][col]
    monkeypatch.setitem(sys.modules, "xlrd", types.SimpleNamespace(open_workbook=lambda **kwargs: types.SimpleNamespace(sheet_by_index=lambda _: Sheet())))
    rows, unknown = parse_stock(b"\xd0\xcf\x11\xe0real")
    assert unknown == []
    assert rows == [{"tipo": "Processo", "protocolo": "09137/18", "digital": "Sim", "subcategoria": "Representação", "jurisdicionado": "Prefeitura Municipal de Cabedelo", "fase": "Recurso", "procurador": "Isabella Barbosa Marinho Falcão", "dias_com_procurador": 168.0, "assistente": "Agda", "dias_com_assistente": 112.0, "dias_no_mpc": 168.0, "prescricao": "30 meses"}]


def test_stock_header_aliases_and_clear_missing_columns_error(monkeypatch):
    headers = [" protocolo ", "Subcategoria", "JURISDICIONADO", "Procurador (A)", "Dias Com Procurador (A)"]
    class Sheet:
        ncols, nrows = 5, 2
        def cell_value(self, row, col):
            return (headers if row == 0 else ["01000/26", "Denúncia", "Origem", "Sheyla Barreto Braga de Queiroz", 8])[col]
    monkeypatch.setitem(sys.modules, "xlrd", types.SimpleNamespace(open_workbook=lambda **kwargs: types.SimpleNamespace(sheet_by_index=lambda _: Sheet())))
    rows, _ = parse_stock(b"\xd0\xcf\x11\xe0aliases")
    assert rows[0]["dias_no_mpc"] is None
    assert rows[0]["procurador"] == "Sheyla Barreto Braga de Queiroz"
    class InvalidSheet:
        ncols, nrows = 2, 1
        def cell_value(self, row, col): return ["PROTOCOLO", "SUBCATEGORIA"][col]
    monkeypatch.setitem(sys.modules, "xlrd", types.SimpleNamespace(open_workbook=lambda **kwargs: types.SimpleNamespace(sheet_by_index=lambda _: InvalidSheet())))
    with pytest.raises(ValueError, match="Colunas obrigatórias ausentes"):
        parse_stock(b"\xd0\xcf\x11\xe0invalid")


def test_same_protocol_can_be_entry_and_exit_and_hash_blocks_repeat(tmp_path):
    store = Store(tmp_path / "tramita.db")
    reports = TramitaReportsStore(store)
    row = {"protocolo": "04124/26", "tipo": "Processo", "subcategoria": "Denúncia", "origem": "Origem", "data_realizacao": "2026-08-03 09:25", "procurador": "Manoel Antônio dos Santos Neto", "motivo_distribuicao": "Ao Procurador", "data_devolucao": "2026-08-06 11:29", "motivo_devolucao": "Analisado Com Cota"}
    reports.import_rows(kind="ENTRADAS", file_name="entrada.xls", file_hash="a", competence="2026-08", actor="admin", rows=[row])
    reports.import_rows(kind="SAIDAS", file_name="saida.xls", file_hash="b", competence="2026-08", actor="admin", rows=[row])
    with store.connection(read_only=True) as c:
        assert c.execute("SELECT COUNT(*) FROM tramita_movimentacoes WHERE protocolo='04124/26'").fetchone()[0] == 2
    with pytest.raises(ValueError, match="já foi importado"):
        reports.import_rows(kind="SAIDAS", file_name="saida.xls", file_hash="b", competence="2026-08", actor="admin", rows=[row])


def test_indicators_and_aging_rules():
    assert official_procurador("Bradson Tiberio Luna Camelo") == "Bradson Tibério Luna Camelo"
    assert is_result("  analisado com parecer ", "Analisado Com Parecer")
    assert turnaround_days({"data_realizacao": "2026-08-03 09:25", "data_devolucao": "2026-08-06 11:29"}) == pytest.approx(3.0861, rel=1e-3)
    assert [aging_band(value) for value in (7, 8, 16, 31, 61, 91)] == ["0–7 dias", "8–15 dias", "16–30 dias", "31–60 dias", "61–90 dias", "Mais de 90 dias"]


def test_reports_use_module_permission_and_imports_remain_administrator_only():
    from services.relatorios_ui import imports

    base = dict(id=1, nome="Pessoa", email="pessoa@tce.pb.gov.br", ativo=True, pode_portarias=True, pode_agenda=True, pode_oficios=True, pode_admin=False, gabinetes=())
    unauthorized = Principal(perfil="USUARIO", **base)
    authorized = Principal(perfil="USUARIO", **base, pode_relatorios=True)
    administrator = Principal(perfil="ADMINISTRADOR", **{**base, "pode_admin": True})
    assert not has_permission(unauthorized, "relatorios")
    assert has_permission(authorized, "relatorios")
    assert has_permission(administrator, "relatorios")
    with pytest.raises(ValueError, match="administradores"):
        imports(None, authorized)
