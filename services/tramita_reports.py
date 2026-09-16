"""Leitura e regras analíticas dos relatórios Tramita, sem Streamlit."""

import csv
import hashlib
import io
import re
import unicodedata
from datetime import datetime

MOVEMENT_HEADERS = ("NUMERO PROTOCOLO", "TIPO", "SUBCATEGORIA", "ORIGEM", "DATA REALIZAÇÃO", "USUÁRIO DESTINO", "MOTIVO DISTRIBUIÇÃO", "DATA DEVOLUÇÃO", "MOTIVO DEVOLUÇÃO")
STOCK_HEADERS = ("TIPO", "PROTOCOLO", "DIGITAL", "SUBCATEGORIA", "JURISDICIONADO", "FASE", "PROCURADOR(A)", "DIAS COM PROCURADOR(A)", "ASSISTENTE", "DIAS COM ASSISTENTE", "DIAS NA PROGE", "PRESCRIÇÃO")
PROCURADORES = {"Elvira Samara Pereira de Oliveira": "PROGE", "Isabella Barbosa Marinho Falcão": "IBMF", "Bradson Tibério Luna Camelo": "BTLC", "Marcílio Toscano Franca Filho": "MTFF", "Manoel Antônio dos Santos Neto": "MASN", "Luciano Andrade Farias": "LAF", "Sheyla Barreto Braga de Queiroz": "SBBQ"}
PROTOCOL_PATTERN = re.compile(r"^\d+/\d{2,4}$")


def normalized(value):
    return " ".join(unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode().casefold().split())


_NAMES = {normalized(name): name for name in PROCURADORES}


def normalized_header(value):
    """Normalize only presentation differences used by Tramita column labels."""
    value = normalized(value).upper().replace("/", " ")
    return re.sub(r"[^A-Z0-9 ]", "", value).strip()


STOCK_HEADER_ALIASES = {
    "tipo": {"TIPO"},
    "protocolo": {"PROTOCOLO"},
    "digital": {"DIGITAL"},
    "subcategoria": {"SUBCATEGORIA"},
    "jurisdicionado": {"JURISDICIONADO"},
    "fase": {"FASE"},
    "procurador": {"PROCURADORA", "PROCURADOR A", "PROCURADOR"},
    "dias_com_procurador": {
        "DIAS COM PROCURADORA", "DIAS COM PROCURADOR A", "DIAS COM PROCURADOR",
    },
    "assistente": {"ASSISTENTE"},
    "dias_com_assistente": {
        "DIAS COM ASSISTENTE A", "DIAS COM ASSISTENTE",
    },
    "dias_no_mpc": {"DIAS NA PROGE"},
    "prescricao": {"PRESCRICAO"},
}
REQUIRED_STOCK_FIELDS = {"protocolo", "subcategoria", "jurisdicionado", "procurador", "dias_com_procurador"}


def official_procurador(value):
    return _NAMES.get(normalized(value))


def _text(value):
    return "" if value is None else str(value).strip()


def _date(value):
    value = _text(value)
    if not value:
        return None
    for pattern in ("%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, pattern).isoformat(sep=" ", timespec="minutes")
        except ValueError:
            pass
    return None


def _number(value):
    try:
        return float(value) if _text(value) else None
    except (TypeError, ValueError):
        return None


def file_hash(content):
    return hashlib.sha256(content).hexdigest()


def parse_movements(content):
    """Lê TSV Tramita salvo como .xls em cp1252/latin-1."""
    try:
        decoded = content.decode("cp1252")
    except UnicodeDecodeError:
        decoded = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(decoded), delimiter="\t")
    if tuple(reader.fieldnames or ()) != MOVEMENT_HEADERS:
        raise ValueError("Cabeçalhos do relatório de movimentação do Tramita não reconhecidos.")
    rows, unknown = [], set()
    for source in reader:
        protocol = _text(source["NUMERO PROTOCOLO"])
        if not PROTOCOL_PATTERN.fullmatch(protocol):
            # O Tramita grava o nome do grupo nesta primeira coluna; não é processo.
            continue
        procurador = official_procurador(source["USUÁRIO DESTINO"])
        if not procurador:
            unknown.add(_text(source["USUÁRIO DESTINO"]))
        rows.append({"protocolo": protocol, "tipo": _text(source["TIPO"]), "subcategoria": _text(source["SUBCATEGORIA"]), "origem": _text(source["ORIGEM"]), "data_realizacao": _date(source["DATA REALIZAÇÃO"]), "procurador": procurador or _text(source["USUÁRIO DESTINO"]), "motivo_distribuicao": _text(source["MOTIVO DISTRIBUIÇÃO"]), "data_devolucao": _date(source["DATA DEVOLUÇÃO"]), "motivo_devolucao": _text(source["MOTIVO DEVOLUÇÃO"])})
    return rows, sorted(value for value in unknown if value)


def parse_stock(content):
    """Lê XLS binário legado; não depende da extensão do arquivo."""
    if not content.startswith(b"\xd0\xcf\x11\xe0"):
        raise ValueError("O relatório de estoque deve ser um XLS binário legado.")
    try:
        import xlrd
    except ImportError as exc:
        raise ValueError("A leitura do XLS de estoque requer a dependência xlrd.") from exc
    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_index(0)
    header_row, field_columns, found_headers = _find_stock_header(sheet)
    if header_row is None:
        missing = ", ".join(sorted(REQUIRED_STOCK_FIELDS - set(field_columns)))
        columns = ", ".join(found_headers[:12]) or "nenhuma"
        raise ValueError("O arquivo não corresponde ao formato esperado do relatório de estoque do Tramita. Colunas identificadas: " + columns + ". Colunas obrigatórias ausentes: " + missing + ".")
    rows, unknown = [], set()
    for index in range(header_row + 1, sheet.nrows):
        values = {field: _text(sheet.cell_value(index, column)) for field, column in field_columns.items()}
        protocol = values["protocolo"]
        if not PROTOCOL_PATTERN.fullmatch(protocol):
            continue
        procurador = official_procurador(values["procurador"])
        if not procurador:
            unknown.add(values["procurador"])
        rows.append({"tipo": values.get("tipo", ""), "protocolo": protocol, "digital": values.get("digital", ""), "subcategoria": values["subcategoria"], "jurisdicionado": values["jurisdicionado"], "fase": values.get("fase", ""), "procurador": procurador or values["procurador"], "dias_com_procurador": _number(values["dias_com_procurador"]), "assistente": values.get("assistente", ""), "dias_com_assistente": _number(values.get("dias_com_assistente")), "dias_no_mpc": _number(values.get("dias_no_mpc")), "prescricao": values.get("prescricao", "")})
    return rows, sorted(value for value in unknown if value)


def _find_stock_header(sheet, limit=20):
    """Locate one safe header row near the top of Tramita's binary XLS export."""
    best_headers, best_columns, best_score = [], {}, 0
    for row_index in range(min(limit, sheet.nrows)):
        raw_headers = [_text(sheet.cell_value(row_index, col)) for col in range(sheet.ncols)]
        columns = {}
        for column, header in enumerate(raw_headers):
            normalized_value = normalized_header(header)
            for field, aliases in STOCK_HEADER_ALIASES.items():
                if normalized_value in aliases and field not in columns:
                    columns[field] = column
                    break
        if len(columns) > best_score:
            best_score = len(columns)
            best_columns = columns
            best_headers = [header for header in raw_headers if header]
        if REQUIRED_STOCK_FIELDS.issubset(columns):
            return row_index, columns, [header for header in raw_headers if header]
    return None, best_columns, best_headers


def is_result(value, expected):
    return normalized(value) == normalized(expected)


def turnaround_days(row):
    if not row.get("data_realizacao") or not row.get("data_devolucao"):
        return None
    return (datetime.fromisoformat(row["data_devolucao"]) - datetime.fromisoformat(row["data_realizacao"])).total_seconds() / 86400


def aging_band(days):
    if days is None:
        return None
    if days <= 7: return "0–7 dias"
    if days <= 15: return "8–15 dias"
    if days <= 30: return "16–30 dias"
    if days <= 60: return "31–60 dias"
    if days <= 90: return "61–90 dias"
    return "Mais de 90 dias"
