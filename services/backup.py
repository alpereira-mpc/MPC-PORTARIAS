"""Logical administrative backup of Ferramentas MPC-PB (SQLite and PostgreSQL)."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import gettempdir
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import hashlib
import io
import json
import logging
import re
import sqlite3
import uuid

from database.inventory import (
    APPLICATION_TABLES,
    BACKUP_EXCLUDED_TABLES,
    BLOB_COLUMNS,
    DOCUMENT_FOLDERS,
    SCHEMA_MARKERS,
    backup_tables,
)
from database.store import unwrap_store
from services.access import has_permission
from services.audit import INSTITUTIONAL_TZ, registrar_erro, registrar_evento
from services.branding import APP_NAME, APP_SUBTITLE
from services.system_health import (
    app_version,
    git_build,
    list_columns,
    list_tables,
)

LOGGER = logging.getLogger("mpc.backup")
FORMAT_VERSION = 1
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _as_bytes(value):
    if value is None:
        return None
    if isinstance(value, memoryview):
        value = value.tobytes()
    if isinstance(value, bytearray):
        value = bytes(value)
    if not isinstance(value, bytes):
        return None
    return value or None


def _csv_cell(value):
    if value is None:
        return ""
    if isinstance(value, bytes):
        return ""
    if isinstance(value, memoryview):
        return ""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return str(value)
    return value


def _safe_filename(name, fallback="arquivo"):
    text = SAFE_NAME.sub("_", str(name or fallback)).strip("._")
    return (text or fallback)[:120]


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _notify(progress, message):
    if progress:
        progress(message)


def _require_admin(principal):
    if not has_permission(principal, "admin"):
        raise ValueError("Acesso não autorizado a este módulo.")


def backup_filename(moment=None):
    moment = moment or datetime.now(INSTITUTIONAL_TZ)
    return f"backup_ferramentas_mpcpb_{moment:%Y-%m-%d_%H%M}.zip"


def unique_backup_path(directory=None):
    """Internal ZIP path. Unique per call; the file is not created here."""
    folder = Path(directory) if directory is not None else Path(gettempdir())
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"backup_{uuid.uuid4().hex}.zip"


def _column_list(connection, backend, table):
    names = list_columns(connection, backend, table)
    ordered = []
    if backend == "postgresql":
        rows = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=? "
            "ORDER BY ordinal_position",
            (table,),
        ).fetchall()
        ordered = [r[0] for r in rows if r[0] in names]
    else:
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        ordered = [r[1] for r in rows]
    clean = [name for name in ordered if IDENT.match(name)]
    if table not in APPLICATION_TABLES or not IDENT.match(table):
        return []
    return clean


def _export_documents(archive, connection, table, row, columns):
    blobs = BLOB_COLUMNS.get(table, ())
    folder = DOCUMENT_FOLDERS.get(table)
    files = []
    if not blobs or not folder:
        return files
    row_id = row[columns.index("id")] if "id" in columns else None
    for column in blobs:
        if column not in columns:
            continue
        payload = _as_bytes(row[columns.index(column)])
        if not payload:
            continue
        if table == "portarias":
            name = f"{row_id}.{column}"
        elif table == "memorandos_arquivos":
            original = row[columns.index("nome")] if "nome" in columns else column
            name = f"{row_id}_{_safe_filename(original)}"
        elif table == "oficio_arquivos":
            original = row[columns.index("nome")] if "nome" in columns else column
            name = f"{row_id}_{_safe_filename(original)}"
        else:
            name = f"{row_id or column}.bin"
        path = f"{folder}/{name}"
        archive.writestr(path, payload)
        files.append(
            {
                "tabela": table,
                "id": None if row_id is None else str(row_id),
                "coluna": column,
                "arquivo": path,
                "sha256": _sha256(payload),
                "bytes": len(payload),
            }
        )
    return files


def _write_csv(archive, table, columns, rows):
    blobs = set(BLOB_COLUMNS.get(table, ()))
    header = [c for c in columns if c not in blobs]
    extras = []
    for column in columns:
        if column in blobs:
            extras.extend((f"{column}_arquivo", f"{column}_sha256"))
    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header + extras)
    for row, documents in rows:
        line = [_csv_cell(row[columns.index(c)]) for c in header]
        by_column = {item["coluna"]: item for item in documents}
        for column in columns:
            if column not in blobs:
                continue
            item = by_column.get(column)
            line.append(item["arquivo"] if item else "")
            line.append(item["sha256"] if item else "")
        writer.writerow(line)
    archive.writestr(f"dados/{table}.csv", output.getvalue().encode("utf-8"))


def _readme(manifest):
    tables = ", ".join(manifest["tabelas"]) or "(nenhuma)"
    return (
        f"Backup lógico gerado pelo {APP_NAME}.\n"
        f"{APP_SUBTITLE}\n\n"
        f"Data (America/Recife): {manifest['gerado_em_local']}\n"
        f"Data (UTC): {manifest['gerado_em_utc']}\n"
        f"Versão do formato: {manifest['formato']}\n"
        f"Versão do aplicativo: {manifest.get('versao_aplicativo') or 'não identificada'}\n"
        f"Build: {manifest.get('build') or 'não identificado'}\n"
        f"Banco de origem: {manifest['engine']}\n\n"
        "Conteúdo:\n"
        "- dados/: exportação CSV das tabelas da aplicação\n"
        "- documentos/: bytes originais de DOCX/PDF/BLOB extraídos do banco\n"
        "- schema/schema_manifest.json: marcadores e tabelas presentes\n"
        "- manifest.json: metadados deste arquivo\n\n"
        f"Tabelas exportadas: {tables}\n\n"
        "Este arquivo pode conter dados e documentos institucionais. "
        "Armazene-o em local seguro.\n\n"
        "A restauração deve ser realizada por procedimento administrativo e "
        "técnico apropriado. Este pacote não inclui restauração automática.\n"
        "Não substitui backup nativo de PostgreSQL/Supabase nem política "
        "institucional de infraestrutura.\n"
    )


def generate_backup(store, principal, destination, progress=None):
    """Write a logical ZIP. Never returns a partial file as a valid backup."""
    _require_admin(principal)
    store = unwrap_store(store)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("O arquivo de backup já existe.")
    utc = datetime.now(timezone.utc)
    local = utc.astimezone(INSTITUTIONAL_TZ)
    counts = {}
    documents = []
    exported = []
    isolation = "REPEATABLE READ" if store.backend == "postgresql" else None
    try:
        _notify(progress, "Lendo o banco em transação de leitura…")
        with store.connection(
            read_only=True, isolation=isolation, statement_timeout="300s"
        ) as connection:
            if store.backend == "sqlite":
                try:
                    connection.execute("BEGIN")
                except sqlite3.OperationalError:
                    pass
            existing = list_tables(connection, store.backend)
            tables = backup_tables(store.backend, existing)
            markers = []
            if "configuracoes" in existing:
                rows = connection.execute(
                    "SELECT chave,valor FROM configuracoes WHERE chave IN ({})".format(
                        ",".join("?" * len(SCHEMA_MARKERS))
                    ),
                    SCHEMA_MARKERS,
                ).fetchall()
                markers = [{"chave": r[0], "valor": r[1]} for r in rows]
            sqlite_version = None
            pg_versions = []
            if store.backend == "sqlite":
                sqlite_version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
            elif "schema_migrations" in existing:
                pg_versions = [
                    r[0]
                    for r in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
            with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
                for table in tables:
                    _notify(progress, f"Exportando {table}…")
                    columns = _column_list(connection, store.backend, table)
                    if not columns:
                        continue
                    quoted = ",".join(columns)
                    cursor = connection.execute(f"SELECT {quoted} FROM {table}")
                    collected = []
                    count = 0
                    for raw in cursor:
                        row = tuple(raw)
                        files = _export_documents(
                            archive, connection, table, row, columns
                        )
                        documents.extend(files)
                        collected.append((row, files))
                        count += 1
                    _write_csv(archive, table, columns, collected)
                    counts[table] = count
                    exported.append(table)
                schema = {
                    "engine": (
                        "postgresql" if store.backend == "postgresql" else "sqlite"
                    ),
                    "sqlite_user_version": sqlite_version,
                    "postgres_schema_versions": pg_versions,
                    "marcadores": markers,
                    "tabelas_aplicacao": list(APPLICATION_TABLES),
                    "tabelas_excluidas": sorted(BACKUP_EXCLUDED_TABLES),
                    "tabelas_exportadas": exported,
                    "contagem": counts,
                }
                archive.writestr(
                    "schema/schema_manifest.json",
                    json.dumps(schema, ensure_ascii=False, indent=2),
                )
                archive.writestr(
                    "schema/documentos.json",
                    json.dumps(documents, ensure_ascii=False, indent=2),
                )
                manifest = {
                    "aplicacao": APP_NAME,
                    "formato": FORMAT_VERSION,
                    "gerado_em_utc": utc.isoformat(),
                    "gerado_em_local": local.strftime("%d/%m/%Y %H:%M:%S"),
                    "fuso": "America/Recife",
                    "engine": (
                        "PostgreSQL" if store.backend == "postgresql" else "SQLite"
                    ),
                    "schema_markers": [m["chave"] for m in markers],
                    "tabelas": exported,
                    "quantidade_tabelas": len(exported),
                    "registros_por_tabela": counts,
                    "quantidade_registros": sum(counts.values()),
                    "quantidade_documentos": len(documents),
                    "versao_aplicativo": app_version() or None,
                    "build": git_build(),
                }
                archive.writestr("README.txt", _readme(manifest))
                archive.writestr(
                    "manifest.json",
                    json.dumps(manifest, ensure_ascii=False, indent=2),
                )
        size = destination.stat().st_size
        digest = _sha256(destination.read_bytes())
        download_name = backup_filename(local)
        payload = {
            "arquivo": download_name,
            "caminho": str(destination),
            "tamanho": size,
            "sha256": digest,
            "quantidade_tabelas": len(exported),
            "quantidade_registros": sum(counts.values()),
            "quantidade_documentos": len(documents),
            "registros_por_tabela": counts,
            "tabelas": exported,
            "engine": "postgresql" if store.backend == "postgresql" else "sqlite",
            "gerado_em_utc": utc.isoformat(),
            "gerado_em_local": local.strftime("%d/%m/%Y %H:%M:%S"),
        }
        registrar_evento(
            store,
            evento="BACKUP_GERADO",
            modulo="admin",
            acao="BACKUP",
            resultado="OK",
            principal=principal,
            entidade_tipo="backup",
            entidade_id=download_name,
            detalhes={
                "tabelas": len(exported),
                "registros": sum(counts.values()),
                "documentos": len(documents),
                "tamanho": size,
                "sha256": digest,
            },
        )
        _notify(progress, "Backup concluído.")
        return payload
    except ValueError:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        LOGGER.exception("Falha ao gerar backup administrativo")
        registrar_erro(
            store, modulo="admin", acao="BACKUP", erro=exc, principal=principal
        )
        raise ValueError("Não foi possível gerar o backup.") from None
