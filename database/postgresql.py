"""PostgreSQL adapter for the existing Store SQL contract.

Connections are pooled; write transactions remain serialized per app schema.
No credentials are included in public exceptions, logs or backup contents.
"""

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import base64
import gzip
import hashlib
import json
import re
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

TABLES = (
    "procuradores",
    "funcoes",
    "assentos",
    "motivos_afastamento",
    "bases_legais",
    "configuracoes",
    "sequencias",
    "sequencia_baselines",
    "portarias",
    "substituicoes",
    "eventos",
    "audit_log",
    "exportacoes",
    "audit_arquivos",
)
IDENTITY_TABLES = {
    "procuradores",
    "motivos_afastamento",
    "substituicoes",
    "eventos",
    "audit_log",
    "exportacoes",
    "audit_arquivos",
}
HISTORY_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS portarias_recent_idx "
    "ON portarias(criada DESC, id DESC)"
)


class DatabaseUnavailable(ValueError):
    pass


class Row:
    """sqlite.Row-compatible named and positional reads, without changing callers."""

    def __init__(self, names, values):
        self._data = dict(zip(names, values))
        self._values = values

    def keys(self):
        return self._data.keys()

    def __getitem__(self, key):
        return self._values[key] if isinstance(key, int) else self._data[key]

    def __iter__(self):
        return iter(self._values)


class Cursor:
    def __init__(self, cursor, returning_id=False):
        self.cursor = cursor
        self.lastrowid = cursor.fetchone()[0] if returning_id else None

    def fetchone(self):
        values = self.cursor.fetchone()
        if values is None:
            return None
        return Row([column.name for column in self.cursor.description], values)

    def fetchall(self):
        return list(self)

    def __iter__(self):
        while (row := self.fetchone()) is not None:
            yield row


def parameters(statement):
    # Existing SQL is static; translate only placeholders outside quoted literals.
    parts = re.split(r"('(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")", statement)
    return "".join(
        part if i % 2 else part.replace("?", "%s") for i, part in enumerate(parts)
    )


class Connection:
    def __init__(self, raw, invalidate=None):
        self.raw = raw
        self.invalidate = invalidate
        self.changed = set()

    def execute(self, statement, values=None):
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            # Transaction and equivalent writer lock are already held.
            return Cursor(self.raw.execute("SELECT 1"))
        returning_id = False
        insert = re.match(r"\s*INSERT\s+INTO\s+(\w+)", statement, re.I)
        if (
            insert
            and insert[1].lower() in IDENTITY_TABLES
            and "RETURNING" not in statement.upper()
        ):
            statement += " RETURNING id"
            returning_id = True
        cursor = Cursor(self.raw.execute(parameters(statement), values), returning_id)
        mutation = re.match(
            r"\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)", statement, re.I
        )
        if mutation:
            self.changed.add(mutation[1].lower())
        return cursor

    def executemany(self, statement, rows):
        for row in rows:
            self.execute(statement, row)

    def commit(self):
        self.raw.commit()
        if self.changed and self.invalidate:
            self.invalidate(self.changed)
        self.changed.clear()


class PostgresBackend:
    def __init__(self, url, schema="mpc_portarias"):
        if not isinstance(url, str) or not url.startswith(
            ("postgresql://", "postgres://")
        ):
            raise DatabaseUnavailable(
                "DATABASE_URL deve ser uma URL PostgreSQL válida."
            )
        try:
            self._options = conninfo_to_dict(url)
        except psycopg.Error:
            raise DatabaseUnavailable(
                "DATABASE_URL inválida. Confira os Secrets."
            ) from None
        mode = self._options.get("sslmode", "require")
        self._options["sslmode"] = (
            mode if mode in ("require", "verify-ca", "verify-full") else "require"
        )
        self._options["connect_timeout"] = 10
        self.schema = schema
        self._active = ContextVar("mpc_postgres_connection", default=None)

    @contextmanager
    def connection(self, *, read_only=False):
        raw = None
        token = None
        from database.pool import resource

        pool = resource(self._options).pool
        try:
            raw = pool.getconn()
            # One round trip for transaction setup, including transaction-pooler safety.
            raw.execute(
                sql.SQL(
                    "SET TRANSACTION ISOLATION LEVEL READ COMMITTED {}; "
                    "SET LOCAL statement_timeout = '60s'; "
                    "SET LOCAL lock_timeout = '30s'; "
                    "SET LOCAL search_path TO {}, pg_catalog"
                ).format(
                    sql.SQL("READ ONLY" if read_only else "READ WRITE"),
                    sql.Identifier(self.schema),
                ),
                prepare=False,
            )
            if not read_only:
                # Keep the same lock for all mutations, numbering and bootstrap.
                raw.execute(
                    "SELECT pg_advisory_xact_lock(64712026, hashtext(%s))",
                    (self.schema,),
                )
            connection = Connection(raw, self.invalidate)
            token = self._active.set(connection)
            yield connection
            connection.commit()
        except psycopg.Error as exc:
            self._rollback(raw)
            raise DatabaseUnavailable(
                "Operação PostgreSQL não concluída. Verifique conexão, permissões e integridade. "
                f"Código: {exc.sqlstate or 'conexão'}."
            ) from None
        except BaseException:
            self._rollback(raw)
            raise
        finally:
            if token is not None:
                self._active.reset(token)
            if raw is not None:
                pool.putconn(raw)

    @staticmethod
    def _rollback(raw):
        if raw is not None:
            try:
                raw.rollback()
            except psycopg.Error:
                # A lost socket must not expose the original server/DSN message.
                raw.close()

    def cache_key(self, tables):
        from database.pool import resource

        return resource(self._options).cache_key(self.schema, tables)

    def invalidate(self, tables):
        from database.pool import resource

        resource(self._options).invalidate(self.schema, tables)

    def initialize(self, root, *, force=False):
        from database.pool import resource

        shared = resource(self._options)
        with shared.lock:
            if not force and self.schema in shared.initialized:
                return
            shared.initialized.discard(self.schema)
            self._initialize(root)
            # Only a committed initialization may be remembered; failures are retried.
            shared.initialized.add(self.schema)

    def _initialize(self, root):
        with self.connection() as c:
            c.raw.execute(
                sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                    sql.Identifier(self.schema)
                )
            )
            c.raw.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY)"
            )
            versions = [
                r[0] for r in c.execute("SELECT version FROM schema_migrations")
            ]
            if versions:
                if versions != [1]:
                    raise DatabaseUnavailable(
                        "Versão PostgreSQL incompatível. Atualize o aplicativo."
                    )
                c.raw.execute(HISTORY_INDEX_SQL)
                return
            # Do not silently start a second numbering history over legacy public data.
            legacy = c.raw.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' AND tablename=ANY(%s)",
                (list(TABLES),),
            ).fetchall()
            for (table,) in legacy:
                if c.raw.execute(
                    sql.SQL("SELECT 1 FROM public.{} LIMIT 1").format(
                        sql.Identifier(table)
                    )
                ).fetchone():
                    raise DatabaseUnavailable(
                        "Há dados do aplicativo no schema public. Inicialização interrompida; "
                        "é necessária revisão explícita antes de usar outro schema."
                    )
            c.raw.execute(
                (root / "database/postgresql_schema.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
            c.raw.execute(HISTORY_INDEX_SQL)
            occupied = any(
                c.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                for table in (*TABLES, "backup_snapshots")
            )
            if not occupied:
                seed = json.loads(
                    (root / "database/seed.json").read_text(encoding="utf-8")
                )
                c.executemany(
                    "INSERT INTO procuradores(nome,genero,cargo_base,funcao,assento) VALUES(?,?,?,?,?)",
                    seed["procuradores"],
                )
                for table in ("funcoes", "assentos"):
                    c.executemany(
                        f"INSERT INTO {table} VALUES(?)", [(v,) for v in seed[table]]
                    )
                c.executemany(
                    "INSERT INTO motivos_afastamento(nome,texto) VALUES(?,?)",
                    seed["motivos"],
                )
                c.executemany("INSERT INTO bases_legais VALUES(?,?,?)", seed["bases"])
                c.executemany(
                    "INSERT INTO configuracoes VALUES(?,?)",
                    [
                        ("seeded", "1"),
                        ("pdf_engine", "auto"),
                        ("export_dir", str(root / "exports")),
                        ("admin_number", "0"),
                        ("sequence_confirmed", "1"),
                    ],
                )
                c.execute("INSERT INTO sequencias VALUES(2026,8)")
                c.execute("INSERT INTO sequencia_baselines VALUES(2026,8)")
            c.execute("INSERT INTO schema_migrations VALUES(1)")

    def backup(self, destination):
        active = self._active.get()
        if active is not None:
            return self._backup(active, destination)
        with self.connection() as c:
            return self._backup(c, destination)

    def _backup(self, c, destination):
        from database.store import now

        def binary(value):
            if isinstance(value, bytes):
                return {"base64": base64.b64encode(value).decode("ascii")}
            raise TypeError("Tipo de backup inválido")

        destination = Path(destination).with_suffix(".json.gz")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Stream rows to disk; do not load all DOCX/PDF blobs into Python at once.
        # Exclude past backup payloads to avoid recursive/exponential growth.
        try:
            file = destination.open("xb")
        except FileExistsError:
            raise ValueError("O arquivo de backup já existe.") from None
        with file, gzip.open(file, "wt", encoding="utf-8") as out:
            out.write('{"format":"mpc-postgresql-v1","tables":{')
            for index, table in enumerate(TABLES):
                if index:
                    out.write(",")
                out.write(json.dumps(table) + ":[")
                with c.raw.cursor(
                    name="backup_" + uuid.uuid4().hex, row_factory=dict_row
                ) as rows:
                    rows.itersize = 1
                    rows.execute(
                        sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))
                    )
                    for row_index, row in enumerate(rows):
                        if row_index:
                            out.write(",")
                        json.dump(row, out, ensure_ascii=False, default=binary)
                out.write("]")
            out.write("}}")
        compressed = destination.read_bytes()
        # Durable copy survives Cloud filesystem replacement; same transaction as deletion.
        c.execute(
            "INSERT INTO backup_snapshots VALUES(?,?,?,?)",
            (
                str(destination),
                now(),
                compressed,
                hashlib.sha256(compressed).hexdigest(),
            ),
        )
        return destination
