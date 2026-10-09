"""Versioned data codec and trusted schema metadata. No SQL is read from archives."""

from datetime import date, datetime, time
from decimal import Decimal
import hashlib
import json
import math
import re
import uuid

from database.inventory import APPLICATION_TABLES

FORMAT_ID = "mpcpb-logical-backup"
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def quoted(name):
    if not isinstance(name, str) or not IDENT.fullmatch(name):
        raise ValueError("Identificador inválido.")
    return '"' + name + '"'


def json_bytes(value):
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def file_hash(path):
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def encode_cell(value, binary_writer):
    if value is None:
        return ["null", None]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return ["blob", binary_writer(bytes(value))]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, str):
        return ["str", value]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat()]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, time):
        return ["time", value.isoformat()]
    if isinstance(value, uuid.UUID):
        return ["uuid", str(value)]
    if isinstance(value, (dict, list)):
        return ["json", value]
    raise ValueError("Tipo de dado não suportado pelo backup V2.")


def decode_cell(cell, binary_reader):
    if not isinstance(cell, list) or len(cell) != 2:
        raise ValueError("Célula tipada inválida.")
    tag, value = cell
    if tag == "null" and value is None:
        return None
    if tag == "str" and isinstance(value, str):
        return value
    if tag == "bool" and type(value) is bool:
        return value
    if tag == "int" and isinstance(value, str) and re.fullmatch(r"-?\d{1,100}", value):
        return int(value)
    if tag == "float" and isinstance(value, str):
        number = float.fromhex(value)
        if math.isfinite(number):
            return number
    if tag == "decimal" and isinstance(value, str):
        return Decimal(value)
    if tag in ("datetime", "date", "time") and isinstance(value, str):
        return {"datetime": datetime, "date": date, "time": time}[tag].fromisoformat(
            value
        )
    if tag == "uuid" and isinstance(value, str):
        return uuid.UUID(value)
    if tag == "json" and isinstance(value, (dict, list)):
        json_bytes(value)
        return value
    if (
        tag == "blob"
        and isinstance(value, dict)
        and set(value) == {"arquivo", "sha256", "bytes"}
    ):
        return binary_reader(value)
    raise ValueError("Tipo ou valor de célula inválido.")


def table_schema(connection, backend, table):
    if table not in APPLICATION_TABLES:
        raise ValueError("Tabela não inventariada.")
    if backend == "sqlite":
        columns = [
            dict(
                zip(
                    ("position", "name", "type", "notnull", "default", "pk"), tuple(row)
                )
            )
            for row in connection.execute(f"PRAGMA table_info({quoted(table)})")
        ]
        ddl = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()[0]
        constraints = " ".join(ddl.split())
        fks = [
            list(row)
            for row in connection.execute(f"PRAGMA foreign_key_list({quoted(table)})")
        ]
        return {
            "columns": columns,
            "foreign_keys": fks,
            "definition_sha256": hashlib.sha256(constraints.encode()).hexdigest(),
        }
    rows = connection.execute(
        "SELECT ordinal_position,column_name,data_type,is_nullable,column_default,udt_name,"
        "is_identity FROM information_schema.columns WHERE table_schema=current_schema() "
        "AND table_name=? ORDER BY ordinal_position",
        (table,),
    )
    return {
        "columns": [
            dict(
                zip(
                    (
                        "position",
                        "name",
                        "type",
                        "nullable",
                        "default",
                        "udt",
                        "identity",
                    ),
                    tuple(row),
                )
            )
            for row in rows
        ]
    }


def iter_rows(connection, backend, table, columns):
    statement = "SELECT " + ",".join(map(quoted, columns)) + " FROM " + quoted(table)
    if backend == "postgresql":
        # Named cursor prevents psycopg from buffering a whole attachment table.
        with connection.raw.cursor(name="mpc_backup_" + uuid.uuid4().hex) as cursor:
            cursor.itersize = 1
            cursor.execute(statement)
            yield from cursor
    else:
        yield from connection.execute(statement)
