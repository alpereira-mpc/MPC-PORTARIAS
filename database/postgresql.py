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
import os
import re
import time
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from services.performance import active as performance_active
from services.performance import record_database_call, record_pool_wait

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
    "usuarios_acesso",
    "usuario_gabinetes",
    "access_requests",
    "servidores",
    "servidores_importacoes",
    "memorandos",
    "memorandos_substituicao",
    "memorandos_substituicao_etapas",
    "memorandos_arquivos",
    "auditoria_eventos",
    "tarefas",
    "tarefas_checklist",
    "tarefas_lembretes",
    "registros_seguidos",
    "avisos_usuario",
    "tramita_importacoes",
    "tramita_movimentacoes",
    "tramita_estoque",
    "relatorios_institucionais",
    "relatorios_institucionais_pdf",
    "relatorios_institucionais_envios",
    "relatorios_institucionais_envio_destinatarios",
    "representacoes",
    "representacao_integrantes",
    "representacao_andamentos",
    "representacao_documentos",
    "peticoes",
    "peticoes_signatarios",
    "peticoes_pedidos",
    "peticoes_andamentos",
    "peticoes_documentos",
    "ouvidoria_sequencias",
    "ouvidoria_manifestacoes",
    "ouvidoria_integrantes",
    "ouvidoria_andamentos",
    "ouvidoria_providencias",
    "ouvidoria_documentos",
    "notas_internas",
    "encaminhamentos_internos",
)
IDENTITY_TABLES = {
    "procuradores",
    "motivos_afastamento",
    "substituicoes",
    "eventos",
    "audit_log",
    "exportacoes",
    "audit_arquivos",
    "usuarios_acesso",
    "access_requests",
    "servidores",
    "servidores_importacoes",
    "memorandos_substituicao_etapas",
    "auditoria_eventos",
    "tarefas",
    "tarefas_checklist",
    "tarefas_lembretes",
    "registros_seguidos",
    "avisos_usuario",
    "tramita_importacoes",
    "tramita_movimentacoes",
    "tramita_estoque",
    "relatorios_institucionais",
    "relatorios_institucionais_envios",
    "relatorios_institucionais_envio_destinatarios",
    "representacoes",
    "peticoes",
    "peticoes_pedidos",
    "peticoes_andamentos",
    "representacao_integrantes",
    "representacao_andamentos",
    "ouvidoria_manifestacoes",
    "ouvidoria_integrantes",
    "ouvidoria_andamentos",
    "ouvidoria_providencias",
    "notas_internas",
    "encaminhamentos_internos",
    "notificacoes_email",
    "notificacao_destinatarios",
}
HISTORY_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS portarias_recent_idx "
    "ON portarias(criada DESC, id DESC)"
)
TRAMITA_HISTORY_MIGRATION_SQL = (
    "ALTER TABLE tramita_importacoes "
    "ADD COLUMN IF NOT EXISTS quantidade_inserida INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tramita_importacoes "
    "ADD COLUMN IF NOT EXISTS quantidade_duplicada INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE tramita_importacoes "
    "ADD COLUMN IF NOT EXISTS origem_historica TEXT NOT NULL DEFAULT 'LEGADO'",
    "ALTER TABLE tramita_movimentacoes "
    "ADD COLUMN IF NOT EXISTS procurador_original TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE tramita_movimentacoes ADD COLUMN IF NOT EXISTS data_evento TEXT",
    "ALTER TABLE tramita_movimentacoes "
    "ADD COLUMN IF NOT EXISTS classificacao_producao TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE tramita_movimentacoes "
    "ADD COLUMN IF NOT EXISTS chave_evento TEXT NOT NULL DEFAULT ''",
    "CREATE INDEX IF NOT EXISTS tramita_movimentacoes_evento_idx "
    "ON tramita_movimentacoes(data_evento, tipo_movimentacao, procurador)",
    "CREATE UNIQUE INDEX IF NOT EXISTS tramita_movimentacoes_chave_unica "
    "ON tramita_movimentacoes(chave_evento) WHERE chave_evento<>''",
    "CREATE INDEX IF NOT EXISTS tramita_importacoes_origem_historica_idx "
    "ON tramita_importacoes(origem_historica)",
)
TRAMITA_STOCK_SNAPSHOT_MIGRATION_SQL = (
    """DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM tramita_importacoes
        WHERE tipo='ESTOQUE' AND data_snapshot IS NOT NULL
        GROUP BY data_snapshot HAVING COUNT(*)>1
    ) THEN
        RAISE EXCEPTION 'Migração do estoque interrompida: há mais de uma fotografia por data';
    END IF;
END $$""",
    "ALTER TABLE tramita_importacoes "
    "DROP CONSTRAINT IF EXISTS tramita_importacoes_hash_arquivo_key",
    "CREATE UNIQUE INDEX IF NOT EXISTS tramita_importacoes_estoque_snapshot_unico "
    "ON tramita_importacoes(data_snapshot) "
    "WHERE tipo='ESTOQUE' AND data_snapshot IS NOT NULL",
)
INSTITUTIONAL_REPORTS_MIGRATION_SQL = (
    "CREATE TABLE IF NOT EXISTS relatorios_institucionais ("
    "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
    "tipo TEXT NOT NULL CHECK(tipo IN ('TRIMESTRAL','ANUAL')), ano INTEGER NOT NULL, "
    "trimestre INTEGER, data_inicio TEXT NOT NULL, data_fim TEXT NOT NULL, "
    "periodo_parcial INTEGER NOT NULL DEFAULT 0 CHECK(periodo_parcial IN (0,1)), "
    "descricao_periodo TEXT NOT NULL DEFAULT '', "
    "status TEXT NOT NULL DEFAULT 'RASCUNHO' CHECK(status IN ('RASCUNHO','EM_REVISAO','FINALIZADO','ENVIADO')), "
    "versao INTEGER NOT NULL CHECK(versao>=1), data_corte TEXT NOT NULL, "
    "snapshot_dados JSONB NOT NULL, conteudo_estruturado JSONB NOT NULL, "
    "criado_por TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL, atualizado_por TEXT, "
    "atualizado_em TEXT, finalizado_por TEXT, finalizado_em TEXT, "
    "ativo INTEGER NOT NULL DEFAULT 1 CHECK(ativo IN (0,1)), "
    "CHECK((tipo='TRIMESTRAL' AND trimestre BETWEEN 1 AND 4) OR "
    "(tipo='ANUAL' AND trimestre IS NULL)))",
    "CREATE UNIQUE INDEX IF NOT EXISTS relatorios_institucionais_periodo_versao_uq "
    "ON relatorios_institucionais(tipo,ano,COALESCE(trimestre,0),versao)",
    "CREATE INDEX IF NOT EXISTS relatorios_institucionais_consulta_idx "
    "ON relatorios_institucionais(tipo,ano,trimestre,status,versao)",
    "CREATE TABLE IF NOT EXISTS relatorios_institucionais_pdf ("
    "relatorio_id BIGINT PRIMARY KEY REFERENCES relatorios_institucionais(id), "
    "nome_arquivo TEXT NOT NULL, conteudo BYTEA NOT NULL, sha256 TEXT NOT NULL, "
    "tamanho INTEGER NOT NULL, gerado_por TEXT NOT NULL DEFAULT '', "
    "gerado_em TEXT NOT NULL)",
)
INSTITUTIONAL_DISTRIBUTION_MIGRATION_SQL = (
    "CREATE TABLE IF NOT EXISTS relatorios_institucionais_envios ("
    "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
    "relatorio_id BIGINT NOT NULL REFERENCES relatorios_institucionais(id), "
    "origem_envio_id BIGINT, tipo TEXT NOT NULL, ano INTEGER NOT NULL, "
    "trimestre INTEGER, versao INTEGER NOT NULL, "
    "status_envio TEXT NOT NULL CHECK(status_envio IN "
    "('PREPARADO','ENVIANDO','ENVIADO','PARCIAL','FALHA')), "
    "remetente TEXT NOT NULL, assunto TEXT NOT NULL, corpo TEXT NOT NULL, "
    "pdf_nome TEXT NOT NULL, pdf_sha256 TEXT NOT NULL, pdf_tamanho INTEGER NOT NULL, "
    "criado_por TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL, "
    "enviado_por TEXT NOT NULL DEFAULT '', enviado_em TEXT, "
    "provedor_mensagem_id TEXT NOT NULL DEFAULT '', "
    "erro_resumo TEXT NOT NULL DEFAULT '', chave_idempotencia TEXT NOT NULL UNIQUE)",
    "CREATE INDEX IF NOT EXISTS relatorios_institucionais_envios_relatorio_idx "
    "ON relatorios_institucionais_envios(relatorio_id,id)",
    "CREATE TABLE IF NOT EXISTS relatorios_institucionais_envio_destinatarios ("
    "id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY, "
    "envio_id BIGINT NOT NULL REFERENCES relatorios_institucionais_envios(id), "
    "procurador_id BIGINT, procurador TEXT NOT NULL, email TEXT NOT NULL, "
    "status TEXT NOT NULL CHECK(status IN ('PENDENTE','ENVIADO','FALHA')), "
    "provedor_mensagem_id TEXT NOT NULL DEFAULT '', enviado_em TEXT, "
    "erro TEXT NOT NULL DEFAULT '')",
    "CREATE INDEX IF NOT EXISTS "
    "relatorios_institucionais_envio_destinatarios_envio_idx "
    "ON relatorios_institucionais_envio_destinatarios(envio_id,id)",
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
        # psycopg exposes rowcount; engagement follow/conclude rely on it.
        self.rowcount = cursor.rowcount
        if returning_id:
            row = cursor.fetchone()
            self.lastrowid = row[0] if row is not None else None
        else:
            self.lastrowid = None

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
    def __init__(
        self,
        raw,
        invalidate=None,
        *,
        read_only=False,
        isolation="READ COMMITTED",
        statement_timeout="60s",
    ):
        self.raw = raw
        self.invalidate = invalidate
        self.read_only = read_only
        self.isolation = isolation
        self.statement_timeout = statement_timeout
        self.changed = set()

    def can_share(self, *, read_only, isolation, statement_timeout):
        """Whether a nested operation can use this transaction unchanged."""
        return (
            self.isolation == isolation
            and self.statement_timeout == statement_timeout
            and (read_only or not self.read_only)
        )

    def execute(self, statement, values=None):
        measured = performance_active()
        started = time.perf_counter() if measured else None
        if statement.strip().upper() == "BEGIN IMMEDIATE":
            # Transaction and equivalent writer lock are already held.
            try:
                return Cursor(self.raw.execute("SELECT 1"))
            finally:
                if measured:
                    record_database_call(
                        "postgres.transaction",
                        (time.perf_counter() - started) * 1000,
                        query=False,
                    )
        returning_id = False
        insert = re.match(r"\s*INSERT\s+INTO\s+(\w+)", statement, re.I)
        if (
            insert
            and insert[1].lower() in IDENTITY_TABLES
            and "RETURNING" not in statement.upper()
        ):
            statement += " RETURNING id"
            returning_id = True
        try:
            cursor = Cursor(
                self.raw.execute(parameters(statement), values), returning_id
            )
        finally:
            if measured:
                record_database_call(
                    "postgres.execute", (time.perf_counter() - started) * 1000
                )
        mutation = re.match(
            r"\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)", statement, re.I
        )
        if mutation:
            self.changed.add(mutation[1].lower())
        return cursor

    def executemany(self, statement, rows):
        # No caller consumes generated IDs from executemany. Let psycopg send
        # the batch instead of waiting for one INSERT/RETURNING per row.
        measured = performance_active()
        started = time.perf_counter() if measured else None
        try:
            with self.raw.cursor() as cursor:
                cursor.executemany(parameters(statement), rows)
        finally:
            if measured:
                record_database_call(
                    "postgres.executemany", (time.perf_counter() - started) * 1000
                )
        mutation = re.match(
            r"\s*(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)", statement, re.I
        )
        if mutation:
            self.changed.add(mutation[1].lower())

    def commit(self):
        measured = performance_active()
        started = time.perf_counter() if measured else None
        try:
            self.raw.commit()
        finally:
            if measured:
                record_database_call(
                    "postgres.commit",
                    (time.perf_counter() - started) * 1000,
                    query=False,
                )
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
        if self._is_disposable_test_connection(url) and mode == "disable":
            self._options["sslmode"] = "disable"
        else:
            self._options["sslmode"] = (
                mode if mode in ("require", "verify-ca", "verify-full") else "require"
            )
        self._options["connect_timeout"] = 10
        self.schema = schema
        self._active = ContextVar("mpc_postgres_connection", default=None)

    @staticmethod
    def _is_disposable_test_connection(url):
        """Allow plaintext only for the exact loopback test URL from the environment."""
        test_url = os.environ.get("MPC_TEST_POSTGRES_URL")
        if not test_url or url != test_url:
            return False
        options = conninfo_to_dict(test_url)
        return (
            options.get("host") in ("127.0.0.1", "localhost")
            and options.get("dbname") == "mpc_disposable_tests"
        )

    @contextmanager
    def connection(self, *, read_only=False, isolation=None, statement_timeout=None):
        raw = None
        token = None

        levels = {"READ COMMITTED", "REPEATABLE READ"}
        level = isolation or "READ COMMITTED"
        if level not in levels:
            raise ValueError("Isolamento de transação inválido.")
        timeout = statement_timeout or "60s"
        if timeout not in ("60s", "120s", "300s"):
            raise ValueError("Tempo máximo de instrução inválido.")
        active = self._active.get()
        if active is not None and active.can_share(
            read_only=read_only,
            isolation=level,
            statement_timeout=timeout,
        ):
            # The outer scope owns commit/rollback and pool return. A writer is
            # never reused by a read-only outer transaction, and transaction
            # settings are never silently weakened for a nested caller.
            yield active
            return
        from database.pool import mark_connection_idle, resource

        pool = resource(self._options).pool
        try:
            measured = performance_active()
            started = time.perf_counter() if measured else None
            raw = pool.getconn()
            if measured:
                record_pool_wait((time.perf_counter() - started) * 1000)
            # One round trip for transaction setup, including transaction-pooler safety.
            started = time.perf_counter() if measured else None
            try:
                raw.execute(
                    sql.SQL(
                        "SET TRANSACTION ISOLATION LEVEL {} {}; "
                        "SET LOCAL statement_timeout = {}; "
                        "SET LOCAL lock_timeout = '30s'; "
                        "SET LOCAL search_path TO {}, pg_catalog"
                    ).format(
                        sql.SQL(level),
                        sql.SQL("READ ONLY" if read_only else "READ WRITE"),
                        sql.Literal(timeout),
                        sql.Identifier(self.schema),
                    ),
                    prepare=False,
                )
            finally:
                if measured:
                    record_database_call(
                        "postgres.transaction_setup",
                        (time.perf_counter() - started) * 1000,
                        query=False,
                    )
            if not read_only:
                # Keep the same lock for all mutations, numbering and bootstrap.
                started = time.perf_counter() if measured else None
                try:
                    raw.execute(
                        "SELECT pg_advisory_xact_lock(64712026, hashtext(%s))",
                        (self.schema,),
                    )
                finally:
                    if measured:
                        record_database_call(
                            "postgres.writer_lock",
                            (time.perf_counter() - started) * 1000,
                            query=False,
                        )
            connection = Connection(
                raw,
                self.invalidate,
                read_only=read_only,
                isolation=level,
                statement_timeout=timeout,
            )
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
                if not getattr(raw, "closed", False):
                    mark_connection_idle(raw)
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
            versions = sorted(
                r[0] for r in c.execute("SELECT version FROM schema_migrations")
            )
            if versions:
                if versions not in (
                    [1],
                    [1, 2],
                    [1, 2, 3],
                    [1, 2, 3, 4],
                    [1, 2, 3, 4, 5],
                ):
                    raise DatabaseUnavailable(
                        "Versão PostgreSQL incompatível. Atualize o aplicativo."
                    )
                c.raw.execute(HISTORY_INDEX_SQL)
                # Additive migrations remain safe for Cloud databases already at v1.
                # The complete initializer only runs for a new schema.
                # Agenda was historically lazy-created, so establish the parent first.
                c.raw.execute(
                    """CREATE TABLE IF NOT EXISTS agenda_afastamentos (
                    id TEXT PRIMARY KEY, procurador_id INTEGER NOT NULL REFERENCES procuradores(id),
                    motivo TEXT NOT NULL, motivo_outro TEXT, data_inicio TEXT NOT NULL, data_fim TEXT NOT NULL,
                    substituto_id INTEGER REFERENCES procuradores(id), observacao TEXT,
                    cancelado INTEGER NOT NULL DEFAULT 0, criado_por TEXT,
                    criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);""",
                    prepare=False,
                )
                c.raw.execute(
                    """CREATE TABLE IF NOT EXISTS agenda_afastamentos_viagens (
                    afastamento_id TEXT PRIMARY KEY REFERENCES agenda_afastamentos(id) ON DELETE CASCADE,
                    aeroporto TEXT NOT NULL, aeroporto_outro TEXT, ida_data TEXT, ida_hora TEXT,
                    aeroporto_ida TEXT, aeroporto_ida_outro TEXT, aeroporto_volta TEXT, aeroporto_volta_outro TEXT,
                    ida_companhia TEXT, ida_voo TEXT, ida_motorista_hora TEXT,
                    volta_data TEXT, volta_chegada_hora TEXT, volta_companhia TEXT, volta_voo TEXT,
                    volta_motorista_hora TEXT, motorista_informado INTEGER NOT NULL DEFAULT 0,
                    informado_em TEXT, informado_por TEXT, observacao TEXT,
                    criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL);
                    CREATE INDEX IF NOT EXISTS agenda_viagens_ida_idx ON agenda_afastamentos_viagens(ida_data);
                    CREATE INDEX IF NOT EXISTS agenda_viagens_volta_idx ON agenda_afastamentos_viagens(volta_data);""",
                    prepare=False,
                )
                c.raw.execute(
                    """ALTER TABLE agenda_afastamentos_viagens ADD COLUMN IF NOT EXISTS aeroporto_ida TEXT;
                    ALTER TABLE agenda_afastamentos_viagens ADD COLUMN IF NOT EXISTS aeroporto_ida_outro TEXT;
                    ALTER TABLE agenda_afastamentos_viagens ADD COLUMN IF NOT EXISTS aeroporto_volta TEXT;
                    ALTER TABLE agenda_afastamentos_viagens ADD COLUMN IF NOT EXISTS aeroporto_volta_outro TEXT;
                    UPDATE agenda_afastamentos_viagens SET
                    aeroporto_ida=COALESCE(aeroporto_ida,aeroporto),
                    aeroporto_ida_outro=COALESCE(aeroporto_ida_outro,aeroporto_outro),
                    aeroporto_volta=COALESCE(aeroporto_volta,aeroporto),
                    aeroporto_volta_outro=COALESCE(aeroporto_volta_outro,aeroporto_outro)
                    WHERE aeroporto_ida IS NULL OR aeroporto_volta IS NULL;""",
                    prepare=False,
                )
                self._migrate_tramita_history(c)
                self._migrate_institutional_reports(c)
                self._migrate_institutional_distribution(c)
                self._migrate_tramita_stock_snapshot(c)
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
            # Add-on tables are created only after this bootstrap completes.
            # ``configuracoes`` belongs to the base schema and is populated by
            # every initialized application database, so it is the safe seed
            # marker here.
            occupied = c.execute("SELECT 1 FROM configuracoes LIMIT 1").fetchone()
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
            self._migrate_tramita_history(c)
            self._migrate_institutional_reports(c)
            self._migrate_institutional_distribution(c)
            self._migrate_tramita_stock_snapshot(c)

    @staticmethod
    def _migrate_tramita_history(connection):
        """Upgrade existing PostgreSQL schemas for historical Tramita reports."""
        for statement in TRAMITA_HISTORY_MIGRATION_SQL:
            connection.raw.execute(statement, prepare=False)
        connection.execute(
            "INSERT INTO schema_migrations VALUES(2) ON CONFLICT DO NOTHING"
        )

    @staticmethod
    def _migrate_institutional_reports(connection):
        """Add immutable institutional report snapshots without touching Tramita."""
        for statement in INSTITUTIONAL_REPORTS_MIGRATION_SQL:
            connection.raw.execute(statement, prepare=False)
        connection.execute(
            "INSERT INTO schema_migrations VALUES(3) ON CONFLICT DO NOTHING"
        )

    @staticmethod
    def _migrate_institutional_distribution(connection):
        """Add distribution history after the report migration already applied."""
        for statement in INSTITUTIONAL_DISTRIBUTION_MIGRATION_SQL:
            connection.raw.execute(statement, prepare=False)
        connection.execute(
            "INSERT INTO schema_migrations VALUES(4) ON CONFLICT DO NOTHING"
        )

    @staticmethod
    def _migrate_tramita_stock_snapshot(connection):
        """Make a stock date, rather than a file hash, the operational identity."""
        for statement in TRAMITA_STOCK_SNAPSHOT_MIGRATION_SQL:
            connection.raw.execute(statement, prepare=False)
        connection.execute(
            "INSERT INTO schema_migrations VALUES(5) ON CONFLICT DO NOTHING"
        )

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
        existing = {
            row[0]
            for row in c.raw.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname=current_schema()"
            ).fetchall()
        }
        tables = tuple(table for table in TABLES if table in existing)
        with file, gzip.open(file, "wt", encoding="utf-8") as out:
            out.write('{"format":"mpc-postgresql-v1","tables":{')
            for index, table in enumerate(tables):
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
