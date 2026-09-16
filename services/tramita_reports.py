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
    headers = tuple(_text(sheet.cell_value(0, col)) for col in range(sheet.ncols))
    if headers != STOCK_HEADERS:
        raise ValueError("Cabeçalhos do relatório de estoque do Tramita não reconhecidos.")
    rows, unknown = [], set()
    for index in range(1, sheet.nrows):
        values = [_text(sheet.cell_value(index, col)) for col in range(sheet.ncols)]
        protocol = values[1]
        if not PROTOCOL_PATTERN.fullmatch(protocol):
            continue
        procurador = official_procurador(values[6])
        if not procurador:
            unknown.add(values[6])
        rows.append({"tipo": values[0], "protocolo": protocol, "digital": values[2], "subcategoria": values[3], "jurisdicionado": values[4], "fase": values[5], "procurador": procurador or values[6], "dias_com_procurador": _number(values[7]), "assistente": values[8], "dias_com_assistente": _number(values[9]), "dias_no_mpc": _number(values[10]), "prescricao": values[11]})
    return rows, sorted(value for value in unknown if value)


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
