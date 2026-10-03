from datetime import datetime
from pathlib import Path
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
    import_reference_reports,
    is_result,
    official_procurador,
    parse_movements,
    parse_stock,
    turnaround_days,
)
from services.access import Principal, has_permission


def movement_file(*rows):
    return (
        "\t".join(MOVEMENT_HEADERS) + "\n" + "\n".join("\t".join(row) for row in rows)
    ).encode("cp1252")


def test_tsv_movements_use_cp1252_and_ignore_grouping_rows():
    content = movement_file(
        ("Manoel Antônio dos Santos Neto", "", "", "", "", "", "", "", ""),
        (
            "04124/26",
            "Processo",
            "Denúncia",
            "Prefeitura Municipal de Mãe d'Água",
            "03/08/2026 09:25",
            "Manoel Antônio dos Santos Neto",
            "Ao Procurador",
            "06/08/2026 11:29",
            "Analisado Com Cota",
        ),
    )
    rows, unknown = parse_movements(content)
    assert unknown == [] and len(rows) == 1
    assert rows[0]["protocolo"] == "04124/26"
    assert rows[0]["procurador"] == "Manoel Antônio dos Santos Neto"


def test_binary_xls_stock_headers_and_accented_procurador(monkeypatch):
    class Sheet:
        ncols, nrows = len(STOCK_HEADERS), 2

        def cell_value(self, row, col):
            values = (
                [*STOCK_HEADERS]
                if row == 0
                else [
                    "Processo",
                    "09137/18",
                    "",
                    "Representação",
                    "Prefeitura Municipal de Cabedelo",
                    "Recurso",
                    "Bradson Tiberio Luna Camelo",
                    168,
                    "Assistente",
                    2,
                    170,
                    "30 meses",
                ]
            )
            return values[col]

    monkeypatch.setitem(
        sys.modules,
        "xlrd",
        types.SimpleNamespace(
            open_workbook=lambda **kwargs: types.SimpleNamespace(
                sheet_by_index=lambda _: Sheet()
            )
        ),
    )
    rows, unknown = parse_stock(b"\xd0\xcf\x11\xe0fake")
    assert unknown == []
    assert rows[0]["procurador"] == "Bradson Tibério Luna Camelo"
    assert rows[0]["dias_com_procurador"] == 168


def test_binary_xls_stock_locates_real_tramita_header_after_title(monkeypatch):
    actual_header = [
        "TIPO",
        "PROTOCOLO",
        "DIGITAL",
        "SUBCATEGORIA",
        "JURISDICIONADO",
        "FASE",
        "PROCURADOR(A)",
        "DIAS COM PROCURADOR(A)",
        "ASSISTENTE",
        "DIAS COM ASSISTENTE",
        "DIAS NA PROGE",
        "PRESCRIÇÃO",
    ]
    process = [
        "Processo",
        "09137/18",
        "Sim",
        "Representação",
        "Prefeitura Municipal de Cabedelo",
        "Recurso",
        "Isabella Barbosa Marinho Falcão",
        168,
        "Agda",
        112,
        168,
        "30 meses",
    ]

    class Sheet:
        ncols, nrows = 12, 6

        def cell_value(self, row, col):
            rows = [
                [""] * len(actual_header),
                ["Processos da Proge"] + [""] * 11,
                [""] * len(actual_header),
                [""] * len(actual_header),
                actual_header,
                process,
            ]
            return rows[row][col]

    monkeypatch.setitem(
        sys.modules,
        "xlrd",
        types.SimpleNamespace(
            open_workbook=lambda **kwargs: types.SimpleNamespace(
                sheet_by_index=lambda _: Sheet()
            )
        ),
    )
    rows, unknown = parse_stock(b"\xd0\xcf\x11\xe0real")
    assert unknown == []
    assert rows == [
        {
            "tipo": "Processo",
            "protocolo": "09137/18",
            "digital": "Sim",
            "subcategoria": "Representação",
            "jurisdicionado": "Prefeitura Municipal de Cabedelo",
            "fase": "Recurso",
            "procurador": "Isabella Barbosa Marinho Falcão",
            "dias_com_procurador": 168.0,
            "assistente": "Agda",
            "dias_com_assistente": 112.0,
            "dias_no_mpc": 168.0,
            "prescricao": "30 meses",
        }
    ]


def test_stock_header_aliases_and_clear_missing_columns_error(monkeypatch):
    headers = [
        " protocolo ",
        "Subcategoria",
        "JURISDICIONADO",
        "Procurador (A)",
        "Dias Com Procurador (A)",
    ]

    class Sheet:
        ncols, nrows = 5, 2

        def cell_value(self, row, col):
            return (
                headers
                if row == 0
                else [
                    "01000/26",
                    "Denúncia",
                    "Origem",
                    "Sheyla Barreto Braga de Queiroz",
                    8,
                ]
            )[col]

    monkeypatch.setitem(
        sys.modules,
        "xlrd",
        types.SimpleNamespace(
            open_workbook=lambda **kwargs: types.SimpleNamespace(
                sheet_by_index=lambda _: Sheet()
            )
        ),
    )
    rows, _ = parse_stock(b"\xd0\xcf\x11\xe0aliases")
    assert rows[0]["dias_no_mpc"] is None
    assert rows[0]["procurador"] == "Sheyla Barreto Braga de Queiroz"

    class InvalidSheet:
        ncols, nrows = 2, 1

        def cell_value(self, row, col):
            return ["PROTOCOLO", "SUBCATEGORIA"][col]

    monkeypatch.setitem(
        sys.modules,
        "xlrd",
        types.SimpleNamespace(
            open_workbook=lambda **kwargs: types.SimpleNamespace(
                sheet_by_index=lambda _: InvalidSheet()
            )
        ),
    )
    with pytest.raises(ValueError, match="Colunas obrigatórias ausentes"):
        parse_stock(b"\xd0\xcf\x11\xe0invalid")


def test_same_protocol_can_be_entry_and_exit_and_hash_blocks_repeat(tmp_path):
    store = Store(tmp_path / "tramita.db")
    reports = TramitaReportsStore(store)
    row = {
        "protocolo": "04124/26",
        "tipo": "Processo",
        "subcategoria": "Denúncia",
        "origem": "Origem",
        "data_realizacao": "2026-08-03 09:25",
        "procurador": "Manoel Antônio dos Santos Neto",
        "motivo_distribuicao": "Ao Procurador",
        "data_devolucao": "2026-08-06 11:29",
        "motivo_devolucao": "Analisado Com Cota",
    }
    reports.import_rows(
        kind="ENTRADAS",
        file_name="entrada.xls",
        file_hash="a",
        competence="2026-08",
        actor="admin",
        rows=[row],
    )
    reports.import_rows(
        kind="SAIDAS",
        file_name="saida.xls",
        file_hash="b",
        competence="2026-08",
        actor="admin",
        rows=[row],
    )
    with store.connection(read_only=True) as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM tramita_movimentacoes WHERE protocolo='04124/26'"
            ).fetchone()[0]
            == 2
        )
    with pytest.raises(ValueError, match="já foi importado"):
        reports.import_rows(
            kind="SAIDAS",
            file_name="saida.xls",
            file_hash="b",
            competence="2026-08",
            actor="admin",
            rows=[row],
        )


def test_historical_schema_upgrade_is_idempotent_for_existing_sqlite_database(tmp_path):
    store = Store(tmp_path / "tramita-legacy.db")
    with store.connection() as connection:
        connection.execute("DROP TABLE tramita_movimentacoes")
        connection.execute("DROP TABLE tramita_estoque")
        connection.execute("DROP TABLE tramita_importacoes")
        connection.execute(
            "CREATE TABLE tramita_importacoes ("
            "id INTEGER PRIMARY KEY,tipo TEXT NOT NULL,competencia TEXT,data_snapshot TEXT,"
            "nome_arquivo TEXT NOT NULL,hash_arquivo TEXT NOT NULL UNIQUE,"
            "quantidade_registros INTEGER NOT NULL,importado_em TEXT NOT NULL,"
            "importado_por TEXT NOT NULL DEFAULT '',status TEXT NOT NULL DEFAULT 'CONCLUIDA')"
        )
        connection.execute(
            "CREATE TABLE tramita_movimentacoes ("
            "id INTEGER PRIMARY KEY,importacao_id INTEGER NOT NULL,competencia TEXT NOT NULL,"
            "tipo_movimentacao TEXT NOT NULL,protocolo TEXT NOT NULL,tipo TEXT NOT NULL DEFAULT '',"
            "subcategoria TEXT NOT NULL DEFAULT '',origem TEXT NOT NULL DEFAULT '',"
            "data_realizacao TEXT,procurador TEXT NOT NULL DEFAULT '',"
            "motivo_distribuicao TEXT NOT NULL DEFAULT '',data_devolucao TEXT,"
            "motivo_devolucao TEXT NOT NULL DEFAULT '')"
        )
        connection.execute(
            "CREATE TABLE tramita_estoque ("
            "id INTEGER PRIMARY KEY,importacao_id INTEGER NOT NULL,data_snapshot TEXT NOT NULL,"
            "protocolo TEXT NOT NULL,tipo TEXT NOT NULL DEFAULT '',digital TEXT NOT NULL DEFAULT '',"
            "subcategoria TEXT NOT NULL DEFAULT '',jurisdicionado TEXT NOT NULL DEFAULT '',"
            "fase TEXT NOT NULL DEFAULT '',procurador TEXT NOT NULL DEFAULT '',"
            "dias_com_procurador REAL,assistente TEXT NOT NULL DEFAULT '',"
            "dias_com_assistente REAL,dias_no_mpc REAL,prescricao TEXT NOT NULL DEFAULT '')"
        )
    store.__dict__.pop("_tramita_schema_ready", None)
    TramitaReportsStore(store)
    store.__dict__.pop("_tramita_schema_ready", None)
    TramitaReportsStore(store)
    with store.connection(read_only=True) as connection:
        movement_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tramita_movimentacoes)")
        }
        import_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(tramita_importacoes)")
        }
        assert {
            "data_evento",
            "classificacao_producao",
            "procurador_original",
            "chave_evento",
        } <= movement_columns
    assert {
        "quantidade_inserida",
        "quantidade_duplicada",
        "origem_historica",
    } <= import_columns


def test_indicators_and_aging_rules():
    assert (
        official_procurador("Bradson Tiberio Luna Camelo")
        == "Bradson Tibério Luna Camelo"
    )
    assert is_result("  analisado com parecer ", "Analisado Com Parecer")
    assert turnaround_days(
        {"data_realizacao": "2026-08-03 09:25", "data_devolucao": "2026-08-06 11:29"}
    ) == pytest.approx(3.0861, rel=1e-3)
    assert [aging_band(value) for value in (7, 8, 16, 31, 61, 91)] == [
        "0–7 dias",
        "8–15 dias",
        "16–30 dias",
        "31–60 dias",
        "61–90 dias",
        "Mais de 90 dias",
    ]


def test_historical_import_is_idempotent_and_uses_event_dates(tmp_path):
    store = Store(tmp_path / "tramita.db")
    source = Path(__file__).resolve().parents[1] / "referencias"
    first = import_reference_reports(store, source, "admin@tce.pb.gov.br")
    assert len(first) == 18
    assert sum(item["inserted"] for item in first) == 3609
    assert sum(item["duplicates"] for item in first) == 0

    reports = TramitaReportsStore(store)
    january = reports.period_report("2026-01-01", "2026-02-01")["summary"]
    august = reports.period_report("2026-08-01", "2026-09-01")["summary"]
    september = reports.period_report("2026-09-01", "2026-10-01")["summary"]
    annual = reports.period_report("2026-01-01", "2027-01-01")["summary"]
    assert {
        key: january[key] for key in ("distributed", "production", "opinions", "quotas")
    } == {"distributed": 140, "production": 146, "opinions": 102, "quotas": 44}
    assert january["production_rate"] == pytest.approx(104.2857)
    assert round(january["median_days"], 1) == 19.1
    assert {
        key: august[key] for key in ("distributed", "production", "opinions", "quotas")
    } == {"distributed": 155, "production": 212, "opinions": 143, "quotas": 69}
    assert august["production_rate"] == pytest.approx(136.7742)
    assert round(august["median_days"], 1) == 7.9
    assert september["distributed"] == 169
    assert september["production"] == 155
    assert round(september["median_days"], 1) == 9.1
    assert annual["distributed"] == 1760
    assert annual["production"] == 1849
    assert annual["opinions"] == 1320
    assert annual["quotas"] == 529
    assert annual["nonproductive_returns"] == 0
    assert round(annual["median_days"], 1) == 11.0
    period_data = reports.period_data("2026-01-01", "2027-01-01")
    assert period_data["report"]["summary"] == annual
    assert len(period_data["events"]) == 3609
    from services.relatorios_ui import _chart_values, _monthly_rows

    chart_rows = _monthly_rows(reports.monthly_reports(2026, list(range(1, 10))))
    median_series = _chart_values(chart_rows, ["Mediana de permanência"])
    ratio_series = _chart_values(chart_rows, ["Produção/Distribuições"])
    assert [round(row["Valor"], 1) for row in median_series] == [
        19.1,
        10.0,
        11.0,
        8.1,
        15.2,
        15.9,
        7.1,
        7.9,
        9.1,
    ]
    assert len(ratio_series) == 9
    assert all(isinstance(row["Valor"], float) for row in ratio_series)
    quarters = [
        reports.period_report(start, end)["summary"]
        for start, end in (
            ("2026-01-01", "2026-04-01"),
            ("2026-04-01", "2026-07-01"),
            ("2026-07-01", "2026-10-01"),
        )
    ]
    assert [row["distributed"] for row in quarters] == [528, 687, 545]
    assert [row["production"] for row in quarters] == [555, 700, 594]
    assert [row["opinions"] for row in quarters] == [398, 498, 424]
    assert [row["quotas"] for row in quarters] == [157, 202, 170]
    assert [round(row["median_days"], 1) for row in quarters] == [11.2, 14.1, 7.8]
    assert reports.months_for_year(2026) == list(range(1, 10))
    assert reports.historical_coverage(2026)["complete_through_last_month"]
    grouped_quarters = reports.quarterly_reports(2026, (1, 2, 3))
    assert [row["summary"]["distributed"] for row in grouped_quarters] == [
        528,
        687,
        545,
    ]

    second = import_reference_reports(store, source, "admin@tce.pb.gov.br")
    assert all(item["already_imported"] for item in second)
    assert sum(item["inserted"] for item in second) == 0
    with store.connection(read_only=True) as c:
        assert (
            c.execute("SELECT COUNT(*) FROM tramita_movimentacoes").fetchone()[0]
            == 3609
        )


def test_period_production_ignores_non_productive_returns_and_october_return(tmp_path):
    store = Store(tmp_path / "tramita.db")
    reports = TramitaReportsStore(store)
    entry = {
        "protocolo": "01000/26",
        "tipo": "Processo",
        "subcategoria": "Teste",
        "origem": "Origem",
        "data_realizacao": "2026-09-30 09:00",
        "procurador": "Manoel Antônio dos Santos Neto",
        "motivo_distribuicao": "Ao Procurador",
        "data_devolucao": "2026-10-02 09:00",
        "motivo_devolucao": "Analisado Com Parecer",
    }
    non_productive = {
        **entry,
        "protocolo": "01001/26",
        "data_devolucao": "2026-09-30 11:00",
        "motivo_devolucao": "Devolvido Após Solicitação",
    }
    reports.import_historical_rows(
        kind="ENTRADAS",
        file_name="entrada.xls",
        file_hash="entry",
        actor="admin",
        source_competence="2026-09",
        rows=[entry],
    )
    reports.import_historical_rows(
        kind="SAIDAS",
        file_name="saida.xls",
        file_hash="exit",
        actor="admin",
        source_competence="2026-09",
        rows=[entry, non_productive],
    )
    september = reports.period_report("2026-09-01", "2026-10-01")["summary"]
    october = reports.period_report("2026-10-01", "2026-11-01")["summary"]
    assert september["distributed"] == 1
    assert september["production"] == 0
    assert september["nonproductive_returns"] == 1
    assert october["production"] == 1
    assert october["opinions"] == 1


def test_nonproductive_return_then_redistribution_and_opinion_are_distinct_events(
    tmp_path,
):
    store = Store(tmp_path / "tramita.db")
    reports = TramitaReportsStore(store)
    procurador_a = "Manoel Antônio dos Santos Neto"
    procurador_b = "Luciano Andrade Farias"
    base = {
        "protocolo": "01002/26",
        "tipo": "Processo",
        "subcategoria": "Teste",
        "origem": "Origem",
        "motivo_distribuicao": "Ao Procurador",
    }
    distribuicao_a = {
        **base,
        "procurador": procurador_a,
        "data_realizacao": "2026-09-01 09:00",
        "data_devolucao": None,
        "motivo_devolucao": "",
    }
    devolucao_nao_produtiva = {
        **base,
        "procurador": procurador_a,
        "data_realizacao": "2026-09-01 09:00",
        "data_devolucao": "2026-09-02 10:00",
        "motivo_devolucao": "Devolvido Após Solicitação",
    }
    distribuicao_b = {
        **base,
        "procurador": procurador_b,
        "data_realizacao": "2026-09-03 09:00",
        "data_devolucao": None,
        "motivo_devolucao": "",
    }
    parecer_b = {
        **base,
        "procurador": procurador_b,
        "data_realizacao": "2026-09-03 09:00",
        "data_devolucao": "2026-09-04 10:00",
        "motivo_devolucao": "Analisado Com Parecer",
    }
    entradas = reports.import_historical_rows(
        kind="ENTRADAS",
        file_name="entradas.xls",
        file_hash="nonproductive-entries",
        actor="admin",
        source_competence="2026-09",
        rows=[distribuicao_a, distribuicao_b],
    )
    saidas = reports.import_historical_rows(
        kind="SAIDAS",
        file_name="saidas.xls",
        file_hash="nonproductive-returns",
        actor="admin",
        source_competence="2026-09",
        rows=[devolucao_nao_produtiva, parecer_b],
    )
    report = reports.period_report("2026-09-01", "2026-10-01")
    by_person = {row["procurador"]: row for row in report["by_procurador"]}
    assert entradas["inserted"] == 2 and saidas["inserted"] == 2
    assert entradas["duplicates"] == 0 and saidas["duplicates"] == 0
    assert report["summary"]["distributed"] == 2
    assert report["summary"]["production"] == 1
    assert report["summary"]["opinions"] == 1
    assert report["summary"]["nonproductive_returns"] == 1
    assert by_person[procurador_a]["production"] == 0
    assert by_person[procurador_a]["opinions"] == 0
    assert by_person[procurador_b]["production"] == 1
    assert by_person[procurador_b]["opinions"] == 1
    with store.connection(read_only=True) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM tramita_movimentacoes").fetchone()[
                0
            ]
            == 4
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM tramita_movimentacoes "
                "WHERE motivo_devolucao='Devolvido Após Solicitação'"
            ).fetchone()[0]
            == 1
        )


def test_quota_then_redistribution_and_opinion_keep_two_productive_events(tmp_path):
    store = Store(tmp_path / "tramita.db")
    reports = TramitaReportsStore(store)
    procurador_a = "Manoel Antônio dos Santos Neto"
    procurador_b = "Luciano Andrade Farias"
    base = {
        "protocolo": "01003/26",
        "tipo": "Processo",
        "subcategoria": "Teste",
        "origem": "Origem",
        "motivo_distribuicao": "Ao Procurador",
    }
    distribuicao_a = {
        **base,
        "procurador": procurador_a,
        "data_realizacao": "2026-09-01 09:00",
        "data_devolucao": None,
        "motivo_devolucao": "",
    }
    cota_a = {
        **base,
        "procurador": procurador_a,
        "data_realizacao": "2026-09-01 09:00",
        "data_devolucao": "2026-09-02 10:00",
        "motivo_devolucao": "Analisado Com Cota",
    }
    distribuicao_b = {
        **base,
        "procurador": procurador_b,
        "data_realizacao": "2026-09-03 09:00",
        "data_devolucao": None,
        "motivo_devolucao": "",
    }
    parecer_b = {
        **base,
        "procurador": procurador_b,
        "data_realizacao": "2026-09-03 09:00",
        "data_devolucao": "2026-09-04 10:00",
        "motivo_devolucao": "Analisado Com Parecer",
    }
    entradas = reports.import_historical_rows(
        kind="ENTRADAS",
        file_name="entradas.xls",
        file_hash="quota-entries",
        actor="admin",
        source_competence="2026-09",
        rows=[distribuicao_a, distribuicao_b],
    )
    saidas = reports.import_historical_rows(
        kind="SAIDAS",
        file_name="saidas.xls",
        file_hash="quota-returns",
        actor="admin",
        source_competence="2026-09",
        rows=[cota_a, parecer_b],
    )
    report = reports.period_report("2026-09-01", "2026-10-01")
    by_person = {row["procurador"]: row for row in report["by_procurador"]}
    assert entradas["inserted"] == 2 and saidas["inserted"] == 2
    assert entradas["duplicates"] == 0 and saidas["duplicates"] == 0
    assert report["summary"]["distributed"] == 2
    assert report["summary"]["production"] == 2
    assert report["summary"]["opinions"] == 1
    assert report["summary"]["quotas"] == 1
    assert by_person[procurador_a]["production"] == 1
    assert by_person[procurador_a]["quotas"] == 1
    assert by_person[procurador_b]["production"] == 1
    assert by_person[procurador_b]["opinions"] == 1
    with store.connection(read_only=True) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM tramita_movimentacoes").fetchone()[
                0
            ]
            == 4
        )


def test_reports_use_module_permission_and_imports_remain_administrator_only():
    from services.relatorios_ui import imports

    base = dict(
        id=1,
        nome="Pessoa",
        email="pessoa@tce.pb.gov.br",
        ativo=True,
        pode_portarias=True,
        pode_agenda=True,
        pode_oficios=True,
        pode_admin=False,
        gabinetes=(),
    )
    unauthorized = Principal(perfil="USUARIO", **base)
    authorized = Principal(perfil="USUARIO", **base, pode_relatorios=True)
    administrator = Principal(perfil="ADMINISTRADOR", **{**base, "pode_admin": True})
    assert not has_permission(unauthorized, "relatorios")
    assert has_permission(authorized, "relatorios")
    assert has_permission(administrator, "relatorios")
    with pytest.raises(ValueError, match="administradores"):
        imports(None, authorized)


def test_reconciliation_repairs_events_missing_from_an_already_recorded_hash(tmp_path):
    reports = TramitaReportsStore(Store(tmp_path / "tramita.db"))
    row = {
        "protocolo": "123/2026",
        "procurador": "Procurador A",
        "data_realizacao": "2026-09-01 09:00",
        "motivo_distribuicao": "Ao Procurador",
        "data_devolucao": None,
        "motivo_devolucao": "",
    }
    first = reports.import_historical_rows(
        kind="ENTRADAS",
        file_name="distribuidosmes9.xls",
        file_hash="reconcile-missing-event",
        actor="admin",
        source_competence="2026-09",
        rows=[row],
    )
    assert first["inserted"] == 1
    with reports.store.connection() as connection:
        connection.execute("DELETE FROM tramita_movimentacoes")
    repaired = reports.import_historical_rows(
        kind="ENTRADAS",
        file_name="distribuidosmes9.xls",
        file_hash="reconcile-missing-event",
        actor="admin",
        source_competence="2026-09",
        rows=[row],
    )
    assert repaired["already_imported"]
    assert repaired["inserted"] == 1
    assert reports.months_for_year(2026) == [9]
    repeated = reports.import_historical_rows(
        kind="ENTRADAS",
        file_name="distribuidosmes9.xls",
        file_hash="reconcile-missing-event",
        actor="admin",
        source_competence="2026-09",
        rows=[row],
    )
    assert repeated["inserted"] == 0


def test_historical_indicators_exclude_unverified_legacy_imports(tmp_path):
    reports = TramitaReportsStore(Store(tmp_path / "tramita.db"))
    reference = {
        "protocolo": "referencia/2026",
        "procurador": "Procurador A",
        "data_realizacao": "2026-08-01 09:00",
        "motivo_distribuicao": "Ao Procurador",
        "data_devolucao": None,
        "motivo_devolucao": "",
    }
    reports.import_historical_rows(
        kind="ENTRADAS",
        file_name="distribuidosmes8.xls",
        file_hash="reference-entry",
        actor="admin",
        source_competence="2026-08",
        rows=[reference],
    )
    reports.import_rows(
        kind="ENTRADAS",
        file_name="legado.xls",
        file_hash="legacy-entry",
        actor="admin",
        competence="2026-08",
        rows=[
            {
                **reference,
                "protocolo": "legado/2026",
                "data_realizacao": "2026-08-02 09:00",
            }
        ],
    )
    assert reports.months_for_year(2026) == [8]
    assert (
        reports.period_report("2026-08-01", "2026-09-01")["summary"]["distributed"] == 1
    )
    audit = reports.historical_audit(2026)
    assert {row["origem_historica"] for row in audit} == {
        "REFERENCIA_TRAMITA_2026",
        "LEGADO",
    }


def test_historical_coverage_reports_gaps_between_january_and_latest_month(tmp_path):
    reports = TramitaReportsStore(Store(tmp_path / "tramita.db"))
    for month in (8, 9):
        reports.import_historical_rows(
            kind="ENTRADAS",
            file_name=f"distribuidosmes{month}.xls",
            file_hash=f"coverage-{month}",
            actor="admin",
            source_competence=f"2026-{month:02d}",
            rows=[
                {
                    "protocolo": f"{month}/2026",
                    "procurador": "Procurador A",
                    "data_realizacao": f"2026-{month:02d}-01 09:00",
                    "motivo_distribuicao": "Ao Procurador",
                    "data_devolucao": None,
                    "motivo_devolucao": "",
                }
            ],
        )
    coverage = reports.historical_coverage(2026)
    assert coverage["months"] == [8, 9]
    assert coverage["missing_months"] == list(range(1, 8))
    assert not coverage["complete_through_last_month"]
