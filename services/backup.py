"""Logical administrative backup of Ferramentas MPC-PB (SQLite and PostgreSQL)."""

from datetime import datetime, timezone
from pathlib import Path
from tempfile import gettempdir
from zipfile import ZIP_DEFLATED, ZipFile
import csv
import hashlib
import logging
import uuid

from database.inventory import (
    BLOB_COLUMNS,
    DOCUMENT_FOLDERS,
    backup_tables,
)
from database.store import unwrap_store
from services.access import has_permission
from services.audit import INSTITUTIONAL_TZ, registrar_evento
from services.branding import APP_NAME, APP_SUBTITLE
from services.system_health import (
    app_version,
    git_build,
    list_tables,
)

LOGGER = logging.getLogger("mpc.backup")
FORMAT_VERSION = 2


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


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _notify(progress, message):
    if progress:
        progress(message)


def _require_admin(principal):
    if not getattr(principal, "ativo", False) or not has_permission(principal, "admin"):
        raise ValueError("Acesso não autorizado a este módulo.")


def backup_filename(moment=None):
    moment = moment or datetime.now(INSTITUTIONAL_TZ)
    return f"backup_ferramentas_mpcpb_{moment:%Y-%m-%d_%H%M}.zip"


def unique_backup_path(directory=None):
    """Internal ZIP path. Unique per call; the file is not created here."""
    folder = Path(directory) if directory is not None else Path(gettempdir())
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"backup_{uuid.uuid4().hex}.zip"


def generate_backup(store, principal, destination, progress=None, *, audit=True):
    """Stream a consistent V2 snapshot, then validate before publication.

    CSV is a human-readable companion only. Typed JSONL is authoritative.
    """
    from tempfile import TemporaryDirectory
    from services.backup_format import (
        FORMAT_ID,
        encode_cell,
        file_hash,
        json_bytes,
        table_schema,
        iter_rows,
    )
    from database.inventory import check_backup_coverage, EXCLUSION_REASONS

    _require_admin(principal)
    store = unwrap_store(store)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise ValueError("O arquivo de backup já existe.")
    utc = datetime.now(timezone.utc)
    local = utc.astimezone(INSTITUTIONAL_TZ)
    counts, schemas, files, documents = {}, {}, {}, []
    owned = False
    try:
        # Exclusive creation also protects against a competing generation.
        with (
            destination.open("xb") as output,
            TemporaryDirectory(prefix="mpc-export-") as spool,
        ):
            owned = True
            with store.connection(
                read_only=True,
                isolation="REPEATABLE READ" if store.backend == "postgresql" else None,
                statement_timeout="300s",
            ) as connection:
                if store.backend == "sqlite":
                    connection.execute("BEGIN")
                existing = list_tables(connection, store.backend)
                markers = []
                if "configuracoes" in existing:
                    markers = sorted(
                        row[0]
                        for row in connection.execute(
                            "SELECT chave FROM configuracoes WHERE chave LIKE '%schema_v%' OR chave LIKE 'agenda_member_%'"
                        )
                    )
                absent = check_backup_coverage(store.backend, existing, markers)
                tables = backup_tables(store.backend, existing)
                origin_schema = (
                    connection.execute("SELECT current_schema()").fetchone()[0]
                    if store.backend == "postgresql"
                    else "main"
                )
                if store.backend == "postgresql" and origin_schema in (
                    "public",
                    "auth",
                    "storage",
                    "pg_catalog",
                    "information_schema",
                ):
                    raise ValueError("Backup exige schema exclusivo da aplicação.")
                sequences = []
                if store.backend == "sqlite":
                    has_sequences = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
                    ).fetchone()
                    if has_sequences:
                        sequences = [
                            list(row)
                            for row in connection.execute(
                                "SELECT name,seq FROM sqlite_sequence"
                            )
                        ]
                else:
                    for row in connection.execute(
                        "SELECT sequencename,last_value,increment_by,start_value,"
                        "min_value,max_value,cycle,cache_size FROM pg_sequences "
                        "WHERE schemaname=current_schema()"
                    ):
                        state = dict(
                            zip(
                                (
                                    "name",
                                    "last_value",
                                    "increment",
                                    "start",
                                    "minimum",
                                    "maximum",
                                    "cycle",
                                    "cache",
                                ),
                                tuple(row),
                            )
                        )
                        sequence_name = state["name"]
                        sequence_row = connection.raw.execute(
                            'SELECT last_value,is_called FROM "{}"."{}"'.format(
                                origin_schema.replace('"', '""'),
                                sequence_name.replace('"', '""'),
                            )
                        ).fetchone()
                        state["is_called"] = bool(sequence_row[1])
                        sequences.append(state)
                with ZipFile(
                    output, "w", compression=ZIP_DEFLATED, allowZip64=True
                ) as archive:

                    def write_bytes(name, data):
                        if name in files:
                            raise ValueError("Componente duplicado no backup.")
                        archive.writestr(name, data)
                        files[name] = {"sha256": _sha256(data), "bytes": len(data)}

                    for table in tables:
                        _notify(progress, f"Exportando {table}…")
                        metadata = table_schema(connection, store.backend, table)
                        schemas[table] = metadata
                        columns = [column["name"] for column in metadata["columns"]]
                        # Detect new binary columns instead of silently discarding them.
                        binary = {
                            column["name"]
                            for column in metadata["columns"]
                            if column["type"].lower() in ("blob", "bytea")
                        }
                        if binary != set(BLOB_COLUMNS.get(table, ())):
                            raise ValueError(
                                "Cobertura de colunas binárias divergente: " + table
                            )
                        data_path = Path(spool) / "rows.jsonl"
                        csv_path = Path(spool) / "rows.csv"
                        count = 0
                        header = [c for c in columns if c not in binary]
                        with (
                            data_path.open("wb") as data_out,
                            csv_path.open("w", encoding="utf-8", newline="") as csv_out,
                        ):
                            writer = csv.writer(csv_out, lineterminator="\n")
                            writer.writerow(
                                header
                                + [
                                    extra
                                    for c in columns
                                    if c in binary
                                    for extra in (c + "_arquivo", c + "_sha256")
                                ]
                            )
                            for raw in iter_rows(
                                connection, store.backend, table, columns
                            ):
                                row = tuple(raw)
                                refs = {}
                                encoded = []
                                for column, value in zip(columns, row):

                                    def write_blob(data, column=column):
                                        folder = DOCUMENT_FOLDERS.get(table)
                                        if not folder or column not in binary:
                                            raise ValueError(
                                                "Documento sem inventário: "
                                                + table
                                                + "."
                                                + column
                                            )
                                        name = f"{folder}/{count}_{column}.bin"
                                        write_bytes(name, data)
                                        ref = {"arquivo": name, **files[name]}
                                        refs[column] = ref
                                        documents.append(
                                            {
                                                "tabela": table,
                                                "id": (
                                                    str(row[columns.index("id")])
                                                    if "id" in columns
                                                    else None
                                                ),
                                                "linha": count,
                                                "coluna": column,
                                                **ref,
                                            }
                                        )
                                        return ref

                                    encoded.append(encode_cell(value, write_blob))
                                data_out.write(json_bytes(encoded) + b"\n")
                                line = [
                                    _csv_cell(row[columns.index(c)]) for c in header
                                ]
                                for column in columns:
                                    if column in binary:
                                        ref = refs.get(column, {})
                                        line.extend(
                                            (
                                                ref.get("arquivo", ""),
                                                ref.get("sha256", ""),
                                            )
                                        )
                                writer.writerow(line)
                                count += 1
                        for name, path in (
                            (f"dados/{table}.jsonl", data_path),
                            (f"dados/{table}.csv", csv_path),
                        ):
                            archive.write(path, name)
                            files[name] = {
                                "sha256": file_hash(path),
                                "bytes": path.stat().st_size,
                            }
                        counts[table] = count
                    schema = {
                        "engine": store.backend,
                        "schema": origin_schema,
                        "tables": schemas,
                        "sequences": sequences,
                        "sqlite_user_version": (
                            connection.execute("PRAGMA user_version").fetchone()[0]
                            if store.backend == "sqlite"
                            else None
                        ),
                        "not_initialized": absent,
                        "initialization_markers": markers,
                        "exclusions": {
                            name: reason
                            for name, reason in EXCLUSION_REASONS.items()
                            if name == "sqlite_sequence" or name in existing
                        },
                    }
                    write_bytes("schema/schema_manifest.json", json_bytes(schema))
                    write_bytes("schema/documentos.json", json_bytes(documents))
                    manifest = {
                        "identificador": FORMAT_ID,
                        "aplicacao": APP_NAME,
                        "formato": FORMAT_VERSION,
                        "gerado_em_utc": utc.isoformat(),
                        "gerado_em_local": local.strftime("%d/%m/%Y %H:%M:%S"),
                        "engine": (
                            "PostgreSQL" if store.backend == "postgresql" else "SQLite"
                        ),
                        "schema": origin_schema,
                        "tabelas": list(tables),
                        "quantidade_tabelas": len(tables),
                        "registros_por_tabela": counts,
                        "quantidade_registros": sum(counts.values()),
                        "quantidade_documentos": len(documents),
                        "versao_aplicativo": app_version(),
                        "build": git_build(),
                        "exclusoes": schema["exclusions"],
                        "modulos_nao_inicializados": absent,
                        "escopo": "Banco lógico da aplicação; arquivos externos e infraestrutura exigem cópia independente.",
                        "autenticidade": "SHA-256 verifica integridade, não autentica a origem.",
                    }
                    write_bytes(
                        "README.txt",
                        (
                            "Backup lógico V2 — "
                            + APP_NAME
                            + "\n"
                            + APP_SUBTITLE
                            + "\nJSONL tipado para restauração; CSV apenas para consulta.\nValidar com scripts/backup_admin.py. SHA-256 não autentica a origem.\nSnapshots administrativos nativos e arquivos externos exigem cópia independente.\n"
                        ).encode("utf-8"),
                    )
                    manifest["arquivos"] = files
                    archive.writestr("manifest.json", json_bytes(manifest))
        from services.restore import validate_backup

        validation = validate_backup(destination, principal, reconstruct=False)
        if validation["status"] not in (
            "Válido para restauração",
            "Incompatível",
        ) or not validation.get("integrity_ok"):
            raise ValueError(
                "Falha na verificação do pacote gerado: "
                + "; ".join(validation["errors"])
            )
        result = {
            **manifest,
            "arquivo": backup_filename(local),
            "caminho": str(destination),
            "tamanho": destination.stat().st_size,
            "sha256": file_hash(destination),
            "validation": validation,
            "engine": store.backend,
        }
        if audit:
            registrar_evento(
                store,
                evento="BACKUP_GERADO",
                modulo="admin",
                acao="BACKUP",
                resultado="OK",
                principal=principal,
                entidade_tipo="backup",
                entidade_id=result["arquivo"],
                detalhes={
                    key: result[key]
                    for key in (
                        "quantidade_tabelas",
                        "quantidade_registros",
                        "quantidade_documentos",
                        "tamanho",
                        "sha256",
                    )
                },
            )
        _notify(progress, "Backup V2 verificado e concluído.")
        return result
    except Exception as exc:
        if audit:
            registrar_evento(
                store,
                evento="BACKUP_FALHOU",
                modulo="admin",
                acao="BACKUP",
                resultado="ERRO",
                principal=principal,
                detalhes={"tipo_erro": type(exc).__name__},
            )
        if owned:
            destination.unlink(missing_ok=True)
        # Never log row data, SQL parameters or connection strings.
        LOGGER.error("Falha de backup (%s)", type(exc).__name__)
        if isinstance(exc, ValueError):
            raise
        raise ValueError("Não foi possível gerar o backup.") from None
