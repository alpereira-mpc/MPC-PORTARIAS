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


def test_reports_are_administrator_only():
    base = dict(id=1, nome="Pessoa", email="pessoa@tce.pb.gov.br", ativo=True, pode_portarias=True, pode_agenda=True, pode_oficios=True, pode_admin=False, gabinetes=())
    assert not has_permission(Principal(perfil="USUARIO", **base), "relatorios")
    assert has_permission(Principal(perfil="ADMINISTRADOR", **{**base, "pode_admin": True}), "relatorios")
