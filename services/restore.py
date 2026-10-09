"""Independent V2 validator and SQLite-only isolated recovery.

Never accepts a Store/URL/schema as destination and never executes archive SQL.
"""

from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import os
import re
from tempfile import TemporaryDirectory, mkdtemp
from zipfile import ZipFile, BadZipFile, ZIP_STORED, ZIP_DEFLATED
import hashlib
import json
import shutil
import sqlite3
import zlib

from database.inventory import (
    APPLICATION_TABLES,
    BACKUP_EXCLUDED_TABLES,
    BLOB_COLUMNS,
    check_backup_coverage,
)
from services.backup import _require_admin
from services.backup_format import (
    FORMAT_ID,
    decode_cell,
    encode_cell,
    file_hash,
    json_bytes,
    quoted,
    table_schema,
)

VALID = "Válido para restauração"
INVALID = "Inválido"
INCOMPATIBLE = "Incompatível"
LEGACY = "Legado não certificado"
CONFIRMATION = "RESTAURAR EM SQLITE ISOLADO"
MAX_ARCHIVE = 2 * 1024**3
MAX_TOTAL = 4 * 1024**3
MAX_ENTRY = 256 * 1024**2
MAX_METADATA = 32 * 1024**2
MAX_LINE = 16 * 1024**2
MAX_ENTRIES = 100000
PG_TEST_MODE_ENV = "MPC_BACKUP_PG_TEST_MODE"
PG_TEST_URL_ENV = "MPC_TEST_POSTGRES_URL"
PG_TEST_DATABASE = "mpc_disposable_tests"
PG_TEST_GUARD = "disposable-mpc-tests-v1"
PG_TEST_SCHEMA = re.compile(r"mpc_test_[0-9a-f]{32}\Z")


def _test_schema_name(store):
    backend = getattr(store, "_postgres", None)
    schema = getattr(backend, "schema", "")
    if not PG_TEST_SCHEMA.fullmatch(schema):
        raise Incompatible("Restauração PostgreSQL exige schema descartável mpc_test_<uuid>.")
    return schema


def _require_disposable_postgres(store):
    """Authorize a database only when every test-only guard matches."""
    if os.environ.get(PG_TEST_MODE_ENV) != "1":
        raise Incompatible("Restauração PostgreSQL de teste não foi autorizada.")
    expected_url = os.environ.get(PG_TEST_URL_ENV)
    if not expected_url:
        raise Incompatible("URL PostgreSQL descartável não configurada.")
    from psycopg.conninfo import conninfo_to_dict

    expected = conninfo_to_dict(expected_url)
    backend = getattr(store, "_postgres", None)
    options = getattr(backend, "_options", {})
    if getattr(store, "backend", None) != "postgresql":
        raise Incompatible("Destino de teste deve ser PostgreSQL.")
    if expected.get("host") not in ("127.0.0.1", "localhost", "::1"):
        raise Incompatible("URL de teste não aponta para loopback.")
    if expected.get("dbname") != PG_TEST_DATABASE:
        raise Incompatible("Banco de teste não é o banco descartável autorizado.")
    for field in ("host", "port", "dbname", "user"):
        if expected.get(field) and options.get(field) != expected[field]:
            raise Incompatible("Conexão não corresponde à URL descartável autorizada.")
    schema = _test_schema_name(store)
    with store.connection(read_only=True) as connection:
        if connection.execute("SELECT current_database()").fetchone()[0] != PG_TEST_DATABASE:
            raise Incompatible("Banco de destino não é descartável.")
        marker = connection.raw.execute(
            "SELECT tag FROM public.mpc_test_guard"
        ).fetchone()
        if marker is None or marker[0] != PG_TEST_GUARD:
            raise Incompatible("Marcador de guarda do banco descartável ausente.")
    return schema


def _postgres_signature(connection):
    constraints = [
        tuple(row)
        for row in connection.execute(
            "SELECT c.relname,con.contype,pg_get_constraintdef(con.oid,true) "
            "FROM pg_constraint con JOIN pg_class c ON c.oid=con.conrelid "
            "WHERE con.connamespace=current_schema()::regnamespace ORDER BY c.relname,con.conname"
        )
    ]
    triggers = [
        tuple(row)
        for row in connection.execute(
            "SELECT c.relname,pg_get_triggerdef(t.oid,true) FROM pg_trigger t "
            "JOIN pg_class c ON c.oid=t.tgrelid WHERE t.tgfoid <> 0 "
            "AND NOT t.tgisinternal AND c.relnamespace=current_schema()::regnamespace "
            "ORDER BY c.relname,t.tgname"
        )
    ]
    return _sha256_json({"constraints": constraints, "triggers": triggers})


def _sha256_json(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def _prepare_postgres_target(target_store, tables, schema):
    """Build trusted local DDL, then only truncate the guarded disposable schema."""
    from database.agenda import AgendaStore
    from database.audit import AuditStore
    from database.oficios import OficiosStore
    from database.peticoes import PeticoesStore
    for constructor in (AgendaStore, OficiosStore, PeticoesStore, AuditStore):
        constructor(target_store)
    with target_store.connection() as connection:
        existing = {
            row[0]
            for row in connection.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname=current_schema()"
            )
        }
        if set(tables) - existing:
            raise Incompatible("Schema PostgreSQL de destino não contém todas as tabelas confiáveis.")
        return _postgres_signature(connection)


def _restore_postgres_disposable(archive, manifest, schema, source_store, target_store):
    if schema["engine"] != "postgresql":
        raise Incompatible("Pacote não foi gerado por PostgreSQL.")
    source_schema = _require_disposable_postgres(source_store)
    target_schema = _require_disposable_postgres(target_store)
    if source_schema == target_schema:
        raise Incompatible("Origem e destino PostgreSQL devem usar schemas descartáveis distintos.")
    tables = manifest["tabelas"]
    _prepare_postgres_target(target_store, tables, target_schema)
    with source_store.connection(read_only=True) as source, target_store.connection() as target:
        for table in tables:
            if table_schema(source, "postgresql", table) != schema["tables"][table]:
                raise Incompatible("Schema da origem diverge do pacote: " + table)
            if table_schema(target, "postgresql", table) != schema["tables"][table]:
                raise Incompatible("Schema confiável de destino diverge: " + table)
        source_signature = _postgres_signature(source)
        target_signature = _postgres_signature(target)
        if source_signature != target_signature:
            raise Incompatible("Constraints ou gatilhos do destino divergem da origem confiável.")
        from psycopg import sql

        truncate = sql.SQL("TRUNCATE TABLE {} RESTART IDENTITY CASCADE").format(
            sql.SQL(",").join(sql.Identifier(table) for table in tables)
        )
        # A limpeza e toda a importação fazem parte da mesma transação: qualquer
        # erro restaura o estado anterior do schema descartável.
        target.raw.execute(truncate)
        verified = {}
        for table in tables:
            columns = [column["name"] for column in schema["tables"][table]["columns"]]
            statement = (
                "INSERT INTO "
                + quoted(table)
                + " ("
                + ",".join(map(quoted, columns))
                + ") VALUES("
                + ",".join("?" for _ in columns)
                + ")"
            )
            expected = Counter()
            for row in _rows(archive, table):
                values = [
                    decode_cell(cell, lambda ref: archive.read(ref["arquivo"]))
                    for cell in row
                ]
                target.execute(statement, values)
                expected[_row_digest(values)] += 1
            actual = Counter(
                _row_digest(tuple(row))
                for row in target.execute(
                    "SELECT " + ",".join(map(quoted, columns)) + " FROM " + quoted(table)
                )
            )
            if actual != expected:
                raise InvalidBackup("Dados restaurados divergentes: " + table)
            verified[table] = sum(actual.values())
        for state in schema["sequences"]:
            name = state.get("name")
            quoted(name)
            value = state.get("last_value")
            if value is None:
                continue
            sequence = '"{}"."{}"'.format(
                target_schema.replace('"', '""'), name.replace('"', '""')
            )
            target.execute(
                "SELECT setval(?::regclass, ?, ?)",
                (sequence, value, bool(state.get("is_called", True))),
            )
        if _postgres_signature(target) != source_signature:
            raise InvalidBackup("Constraints ou gatilhos foram alterados na restauração.")
    with target_store.connection(read_only=True) as target:
        for table, count in verified.items():
            if target.execute("SELECT COUNT(*) FROM " + quoted(table)).fetchone()[0] != count:
                raise InvalidBackup("Contagem pós-restauração divergente: " + table)
        if _postgres_signature(target) != source_signature:
            raise InvalidBackup("Integridade de constraints ou gatilhos pós-reabertura falhou.")
    return {
        "source_schema": source_schema,
        "target_schema": target_schema,
        "counts": verified,
        "relationships": "constraints e chaves estrangeiras aceitas",
        "data_and_document_hashes": "ok",
        "sequences": "estado PostgreSQL restaurado",
        "constraints_and_triggers": "ok",
        "functional_reads": _functional_reads_postgres(target_store),
    }

class Incompatible(ValueError):
    pass


class InvalidBackup(ValueError):
    """Safe diagnostic generated by this validator, never database exception text."""


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidBackup("Chave JSON duplicada.")
        result[key] = value
    return result


def _json(data):
    return json.loads(
        data,
        object_pairs_hook=_object,
        parse_constant=lambda _: (_ for _ in ()).throw(
            ValueError("Número JSON inválido.")
        ),
    )


def _metadata(archive, name):
    if archive.getinfo(name).file_size > MAX_METADATA:
        raise InvalidBackup("Metadados excedem o limite permitido.")
    return _json(archive.read(name))


def _open_checked(path):
    if Path(path).stat().st_size > MAX_ARCHIVE:
        raise InvalidBackup("ZIP excede o limite permitido.")
    archive = ZipFile(path)
    try:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) > MAX_ENTRIES or len(names) != len(set(names)):
            raise InvalidBackup("ZIP com entradas duplicadas ou excessivas.")
        if sum(entry.file_size for entry in entries) > MAX_TOTAL:
            raise InvalidBackup("ZIP descompactado excede o limite permitido.")
        for entry in entries:
            name = entry.filename
            parts = PurePosixPath(name).parts
            if (
                not parts
                or name.startswith("/")
                or "\\" in name
                or ":" in name
                or any(part in ("..", ".") for part in name.split("/"))
                or any(ord(c) < 32 for c in name)
                or entry.is_dir()
                or ((entry.external_attr >> 16) & 0o170000) == 0o120000
                or entry.flag_bits & 1
                or entry.compress_type not in (ZIP_STORED, ZIP_DEFLATED)
            ):
                raise InvalidBackup("Entrada ZIP não permitida.")
            if (
                entry.file_size > MAX_ENTRY
                or entry.file_size > max(1, entry.compress_size) * 2000
            ):
                raise InvalidBackup("Expansão de ZIP excede o limite permitido.")
        return archive
    except Exception:
        archive.close()
        raise


def _rows(archive, table):
    with archive.open(f"dados/{table}.jsonl") as source:
        while line := source.readline(MAX_LINE + 1):
            if len(line) > MAX_LINE:
                raise InvalidBackup("Registro excede o limite permitido.")
            yield _json(line)


def _inspect(archive):
    manifest = _metadata(archive, "manifest.json")
    if not isinstance(manifest, dict):
        raise InvalidBackup("Manifesto inválido.")
    if manifest.get("formato") == 1:
        return manifest, None
    if manifest.get("identificador") != FORMAT_ID or manifest.get("formato") != 2:
        raise Incompatible("Identificação ou versão de formato não suportada.")
    for key in ("quantidade_tabelas", "quantidade_registros", "quantidade_documentos"):
        if type(manifest.get(key)) is not int or manifest[key] < 0:
            raise InvalidBackup("Total declarado inválido: " + key)
    if not isinstance(manifest.get("registros_por_tabela"), dict) or any(
        type(n) is not int or n < 0 for n in manifest["registros_por_tabela"].values()
    ):
        raise InvalidBackup("Contagens de registros inválidas.")
    files = manifest["arquivos"]
    if not isinstance(files, dict) or set(archive.namelist()) != set(files) | {
        "manifest.json"
    }:
        raise InvalidBackup("Componentes ausentes ou não declarados no manifesto.")
    for name, expected in files.items():
        if set(expected) != {"sha256", "bytes"} or type(expected["bytes"]) is not int:
            raise InvalidBackup("Descrição de componente inválida.")
        if archive.getinfo(name).file_size != expected["bytes"]:
            raise InvalidBackup("Tamanho divergente de componente.")
        with archive.open(name) as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected["sha256"]:
                raise InvalidBackup("Hash divergente de componente.")
    schema = _metadata(archive, "schema/schema_manifest.json")
    if schema["engine"] not in ("sqlite", "postgresql"):
        raise Incompatible("Engine não suportada.")
    if (
        manifest["engine"].lower() != schema["engine"]
        or manifest["schema"] != schema["schema"]
    ):
        raise InvalidBackup("Origem inconsistente.")
    tables = manifest["tabelas"]
    if (
        not isinstance(tables, list)
        or len(tables) != len(set(tables))
        or set(tables) != set(schema["tables"])
    ):
        raise InvalidBackup("Inventário inconsistente.")
    if set(tables) - set(APPLICATION_TABLES) or set(tables) & BACKUP_EXCLUDED_TABLES:
        raise Incompatible("Tabela não reconhecida neste aplicativo.")
    technical = {"backup_snapshots"} if schema["engine"] == "postgresql" else set()
    absent = check_backup_coverage(
        schema["engine"], set(tables) | technical, schema["initialization_markers"]
    )
    if (
        absent != schema["not_initialized"]
        or absent != manifest["modulos_nao_inicializados"]
    ):
        raise InvalidBackup("Declaração de módulos ausentes divergente.")
    if manifest["quantidade_tabelas"] != len(tables) or set(
        manifest["registros_por_tabela"]
    ) != set(tables):
        raise InvalidBackup("Contagem de tabelas divergente.")
    documents = _metadata(archive, "schema/documentos.json")
    references = []
    configuration_markers = []
    counts = {}
    for table in tables:
        columns = schema["tables"][table]["columns"]
        names = [c["name"] for c in columns]
        if len(names) != len(set(names)) or not names:
            raise InvalidBackup("Colunas duplicadas ou ausentes.")
        for name in names:
            quoted(name)
        counts[table] = 0
        for index, row in enumerate(_rows(archive, table)):
            if not isinstance(row, list) or len(row) != len(names):
                raise InvalidBackup("Quantidade de colunas divergente.")
            for column, cell in zip(names, row):
                if column in BLOB_COLUMNS.get(table, ()) and (
                    not isinstance(cell, list) or cell[0] not in ("null", "blob")
                ):
                    raise InvalidBackup(
                        "Coluna binária descartada ou substituída por texto."
                    )

                def read_blob(ref):
                    name = ref["arquivo"]
                    if column not in BLOB_COLUMNS.get(table, ()) or not name.startswith(
                        "documentos/"
                    ):
                        raise InvalidBackup("Referência binária não inventariada.")
                    if files.get(name) != {
                        "sha256": ref["sha256"],
                        "bytes": ref["bytes"],
                    }:
                        raise InvalidBackup("Documento ausente ou divergente.")
                    identifier = None
                    if "id" in names:
                        identifier = str(
                            decode_cell(row[names.index("id")], lambda _: None)
                        )
                    references.append(
                        {
                            "tabela": table,
                            "id": identifier,
                            "linha": index,
                            "coluna": column,
                            **ref,
                        }
                    )
                    return b""  # Bytes already hashed by streaming above.

                decode_cell(cell, read_blob)
            if table == "configuracoes":
                key = decode_cell(row[names.index("chave")], lambda _: None)
                if isinstance(key, str) and (
                    "schema_v" in key or key.startswith("agenda_member_")
                ):
                    configuration_markers.append(key)
            counts[table] += 1
        if counts[table] != manifest["registros_por_tabela"][table]:
            raise InvalidBackup("Contagem de registros divergente.")
    if sorted(configuration_markers) != schema["initialization_markers"]:
        raise InvalidBackup("Marcadores de inicialização divergentes.")
    if documents != references or manifest["quantidade_documentos"] != len(references):
        raise InvalidBackup("Inventário de documentos divergente.")
    document_names = [r["arquivo"] for r in references]
    if len(document_names) != len(set(document_names)):
        raise InvalidBackup("Documento referenciado mais de uma vez.")
    expected_names = {
        "README.txt",
        "schema/schema_manifest.json",
        "schema/documentos.json",
    }
    expected_names.update(
        f"dados/{t}.{ext}" for t in tables for ext in ("jsonl", "csv")
    )
    expected_names.update(document_names)
    if set(files) != expected_names or manifest["quantidade_registros"] != sum(
        counts.values()
    ):
        raise InvalidBackup("Arquivos ou totais não correspondem aos dados.")
    return manifest, schema


def _trusted_template(path):
    # Explicit path bypasses DATABASE_URL/Streamlit Secrets even in production.
    from database.store import Store
    from database.agenda import AgendaStore
    from database.oficios import OficiosStore
    from database.peticoes import PeticoesStore
    from database.audit import AuditStore

    store = Store(path)
    for constructor in (AgendaStore, OficiosStore, PeticoesStore, AuditStore):
        constructor(store)
    return store


def _row_digest(values):
    def blob(value):
        return {"sha256": hashlib.sha256(value).hexdigest(), "bytes": len(value)}

    return hashlib.sha256(json_bytes([encode_cell(v, blob) for v in values])).digest()


def _functional_reads(target):
    from database.store import Store
    from database.representacoes import RepresentacoesStore
    from database.institutional_reports import InstitutionalReportsStore

    class ReadOnlyRecovered(Store):
        def __init__(self):
            self.path = target
            self.backend = "sqlite"
            self._postgres = None
            self._read_cache = None

        @contextmanager
        def connection(self, *, read_only=True, **kwargs):
            if not read_only:
                raise InvalidBackup("Verificação funcional permite somente leitura.")
            with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as c:
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA query_only=ON")
                yield c

    reader = ReadOnlyRecovered()
    reader.catalog("procuradores")
    result = {"catalogo_procuradores": "ok"}
    # Bind only the existing read methods, bypassing constructors that prepare schema.
    for table, repository_type, label in (
        ("portarias", None, "portaria"),
        ("representacoes", RepresentacoesStore, "representacao"),
        (
            "relatorios_institucionais",
            InstitutionalReportsStore,
            "relatorio_institucional",
        ),
    ):
        with reader.connection() as c:
            row = c.execute(
                f"SELECT id FROM {quoted(table)} ORDER BY id LIMIT 1"
            ).fetchone()
        if row is None:
            result[label] = "sem registros para amostragem"
            continue
        if repository_type is None:
            value = reader.get(row[0])
        else:
            repository = object.__new__(repository_type)
            repository.store = reader
            value = repository.get(row[0])
        if value is None:
            raise InvalidBackup("Leitura funcional de registro restaurado falhou.")
        result[label] = "ok"
    return result



def _functional_reads_postgres(store):
    """Read restored records through existing repository APIs without schema writes."""
    from database.institutional_reports import InstitutionalReportsStore
    from database.representacoes import RepresentacoesStore

    store.catalog("procuradores")
    result = {"catalogo_procuradores": "ok"}
    for table, repository_type, label in (
        ("portarias", None, "portaria"),
        ("representacoes", RepresentacoesStore, "representacao"),
        ("relatorios_institucionais", InstitutionalReportsStore, "relatorio_institucional"),
    ):
        with store.connection(read_only=True) as connection:
            row = connection.execute(
                "SELECT id FROM " + quoted(table) + " ORDER BY id LIMIT 1"
            ).fetchone()
        if row is None:
            result[label] = "sem registros para amostragem"
            continue
        if repository_type is None:
            value = store.get(row[0])
        else:
            repository = object.__new__(repository_type)
            repository.store = store
            value = repository.get(row[0])
        if value is None:
            raise InvalidBackup("Leitura funcional PostgreSQL restaurada falhou.")
        result[label] = "ok"
    return result
def _reconstruct(archive, manifest, schema, workdir):
    if schema["engine"] != "sqlite":
        raise Incompatible(
            "Restauração PostgreSQL bloqueada: exige ambiente segregado, sequências e procedimento nativo comprovados."
        )
    template = _trusted_template(Path(workdir) / "trusted-template.sqlite")
    with template.connection(read_only=True) as source:
        for table in manifest["tabelas"]:
            if table_schema(source, "sqlite", table) != schema["tables"][table]:
                raise Incompatible("Schema instalado incompatível: " + table)
        if (
            source.execute("PRAGMA user_version").fetchone()[0]
            != schema["sqlite_user_version"]
        ):
            raise Incompatible("Versão SQLite incompatível.")
        # DDL is obtained ONLY from this freshly built trusted local template.
        definitions = [
            tuple(row)
            for row in source.execute(
                "SELECT type,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY CASE type WHEN 'table' THEN 0 ELSE 1 END"
            )
        ]
    target = Path(workdir) / "restored.sqlite"
    with target.open("xb"):
        pass
    connection = sqlite3.connect(target)
    try:
        connection.create_function("mpc_delete_authorized", 1, lambda _: 0)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        connection.execute("PRAGMA defer_foreign_keys=ON")
        for _, table, ddl in definitions:
            if table in manifest["tabelas"]:
                connection.execute(ddl)
        verified = {}
        for table in manifest["tabelas"]:
            columns = [c["name"] for c in schema["tables"][table]["columns"]]
            sql = (
                f"INSERT INTO {quoted(table)} ("
                + ",".join(map(quoted, columns))
                + ") VALUES("
                + ",".join("?" for _ in columns)
                + ")"
            )
            expected = Counter()
            for row in _rows(archive, table):
                values = [
                    decode_cell(cell, lambda ref: archive.read(ref["arquivo"]))
                    for cell in row
                ]
                connection.execute(sql, values)
                expected[_row_digest(values)] += 1
            actual = Counter(
                _row_digest(tuple(row))
                for row in connection.execute(f"SELECT * FROM {quoted(table)}")
            )
            if actual != expected:
                raise InvalidBackup("Dados restaurados divergentes: " + table)
            verified[table] = sum(actual.values())
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
        ).fetchone():
            for name, seq in schema["sequences"]:
                if name not in manifest["tabelas"] or type(seq) is not int or seq < 0:
                    raise InvalidBackup("Sequência SQLite inválida.")
                current = connection.execute(
                    "SELECT seq FROM sqlite_sequence WHERE name=?", (name,)
                ).fetchone()
                if current and seq < current[0]:
                    raise InvalidBackup("Sequência inferior aos IDs restaurados.")
                if current:
                    connection.execute(
                        "UPDATE sqlite_sequence SET seq=? WHERE name=?", (seq, name)
                    )
                else:
                    connection.execute(
                        "INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)", (name, seq)
                    )
        elif schema["sequences"]:
            raise InvalidBackup("Sequências incompatíveis com schema.")
        if connection.execute("PRAGMA foreign_key_check").fetchone():
            raise InvalidBackup("Relacionamentos inconsistentes.")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise InvalidBackup("Integridade SQLite inválida.")
        connection.execute("PRAGMA user_version=2")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    # Reopen independently after commit. Do not initialize or migrate the result.
    with closing(sqlite3.connect(f"{target.as_uri()}?mode=ro", uri=True)) as reopened:
        if (
            reopened.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or reopened.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise InvalidBackup("Verificação pós-abertura falhou.")
        for table, count in verified.items():
            if (
                reopened.execute(f"SELECT COUNT(*) FROM {quoted(table)}").fetchone()[0]
                != count
            ):
                raise InvalidBackup("Contagem pós-abertura divergente.")
    return {
        "destination": str(target),
        "counts": verified,
        "relationships": "ok",
        "data_and_document_hashes": "ok",
        "reopened": True,
        "functional_reads": _functional_reads(target),
        "sequences": "SQLite e controles de numeração conferidos",
    }


def _report(path, principal, reconstruct, workdir):
    _require_admin(principal)
    report = {
        "status": INVALID,
        "integrity_ok": False,
        "errors": [],
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "destination_engine": "SQLite isolado",
    }
    try:
        with _open_checked(path) as archive:
            manifest, schema = _inspect(archive)
            report["manifest"] = {
                key: value for key, value in manifest.items() if key != "arquivos"
            }
            report["sha256"] = file_hash(path)
            if schema is None:
                report["status"] = LEGACY
                report["errors"] = [
                    "CSV V1 não comprova recuperação integral; restauração automática bloqueada."
                ]
                return report
            report["integrity_ok"] = True
            if not reconstruct:
                report["status"] = INCOMPATIBLE
                report["errors"] = [
                    "Integridade do pacote verificada; reconstrução isolada ainda não executada."
                ]
                return report
            report["restore"] = _reconstruct(archive, manifest, schema, workdir)
            report["status"] = VALID
    except InvalidBackup as exc:
        report["status"] = INVALID
        report["errors"] = [str(exc)]
    except Incompatible as exc:
        report["status"] = INCOMPATIBLE
        report["errors"] = [str(exc)]
    except (
        ValueError,
        AttributeError,
        zlib.error,
        KeyError,
        TypeError,
        IndexError,
        BadZipFile,
        OSError,
        RuntimeError,
        sqlite3.Error,
        RecursionError,
        ArithmeticError,
    ):
        report["status"] = INVALID
        report["errors"] = [
            "Arquivo inválido, cobertura incompleta, dados incompatíveis ou falha na reconstrução. Nenhum banco operacional foi alterado."
        ]
    return report


def validate_backup(path, principal, *, reconstruct=True):
    """Read-only preview relative to operational databases. Uses disposable local DBs."""
    _require_admin(principal)
    with TemporaryDirectory(prefix="mpc-validate-") as workspace:
        report = _report(path, principal, reconstruct, workspace)
        if "restore" in report:
            report["restore"].pop("destination", None)
            report["restore"]["temporary_database_discarded"] = True
        return report


def restore_postgresql_disposable_test(path, principal, source_store, target_store):
    """Restore only between two guarded GitHub/local disposable PostgreSQL schemas.

    This API is intentionally separate from ``restore_isolated`` and never accepts
    a URL, schema name, production Store, or Streamlit input as a destination.
    """
    _require_admin(principal)
    with _open_checked(path) as archive:
        manifest, schema = _inspect(archive)
        if schema is None:
            raise Incompatible("Backup V1 não pode ser restaurado em PostgreSQL.")
        result = _restore_postgres_disposable(
            archive, manifest, schema, source_store, target_store
        )
    return {
        "status": VALID,
        "integrity_ok": True,
        "restore": result,
        "sha256": file_hash(path),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "destination_engine": "PostgreSQL descartável",
    }

def restore_isolated(path, principal, workspace, *, confirmation):
    """Only creates a new generated child directory. Existing DBs/URLs are unsupported."""
    _require_admin(principal)
    if confirmation != CONFIRMATION:
        raise InvalidBackup("Confirmação específica de restauração isolada necessária.")
    workspace = Path(workspace).resolve(strict=True)
    if not workspace.is_dir():
        raise InvalidBackup(
            "Informe somente um diretório autorizado para testes isolados."
        )
    directory = Path(mkdtemp(prefix="mpc-recovery-", dir=workspace))
    try:
        # Freeze the uploaded package before validation to avoid replacement races.
        snapshot = directory / "input.zip"
        with open(path, "rb") as source, snapshot.open("xb") as output:
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise InvalidBackup("ZIP excede limite.")
                output.write(chunk)
        report = _report(snapshot, principal, True, directory)
        if report["status"] != VALID:
            raise InvalidBackup("Restauração recusada: " + "; ".join(report["errors"]))
        report["operation"] = "isolated_restore"
        (directory / "report.json").write_bytes(json_bytes(report))
        snapshot.unlink()
        return report
    except Exception:
        # Only our mkdtemp child, never caller-selected targets, is removed.
        shutil.rmtree(directory)
        raise
