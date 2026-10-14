"""DDL e FTS reais, exclusivamente em PostgreSQL descartável protegido."""

from pathlib import Path
import re
import uuid

import psycopg
from psycopg import sql
import pytest

from database.minutas_referencias import MinutasReferenciasStore
from scripts.minutas_reclassificar import OperationalConnection
from services.minutas_acervo import MinutasAcervoService
from services.minutas_library import MinutasLibraryService
from services.minutas_search import MinutasSearchService
from services.minutas_specialties import RPPS
from tests.test_postgresql import pg_url  # loopback + database name + guard table
from tests.test_minutas_specialties import Storage, seed
from tests.access_testing import TEST_IDENTITY


@pytest.fixture
def specialized_pg(pg_url):
    root = Path(__file__).resolve().parents[1]
    schema = "mpc_test_minutas_" + uuid.uuid4().hex
    scripts = []
    for name in ("minutas_referencias.sql", "minutas_documentos_complementares.sql", "minutas_extracoes.sql",
                 "minutas_pesquisa_juridica.sql", "minutas_especialidades.sql"):
        source = (root / "database" / name).read_text(encoding="utf-8")
        source = re.sub(r"--[^\n]*", "", source)
        source = source.split("INSERT INTO storage.buckets")[0]
        # Disposable PostgreSQL need not have Supabase global roles. PUBLIC
        # revocations and every RLS/table/index statement are still executed.
        source = re.sub(r"FROM\s+(?:PUBLIC\s*,\s*)?anon\s*,\s*authenticated", "FROM PUBLIC", source, flags=re.I)
        scripts.append(source.replace("mpc_portarias.", schema + "."))
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            for source in scripts: c.execute(source)
            yield OperationalConnection(pg_url, schema), c, scripts[-1], schema
        finally:
            assert re.fullmatch(r"mpc_test_minutas_[0-9a-f]{32}", schema)
            c.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_real_postgresql_migration_uuid_idempotence_fts_and_rls(specialized_pg):
    store, raw, migration_sql, schema = specialized_pg
    target = seed(store)
    chamber = seed(store, process="01499/26", subtype="Câmara Municipal", entity="Câmara Municipal de Exemplo")
    seed(store, process="02720/25", subtype="Prefeitura", entity="Prefeitura Municipal")
    seed(store, process="99999/26", prosecutor="outra")
    library = MinutasLibraryService(MinutasReferenciasStore(store, RPPS), Storage(), TEST_IDENTITY,
        {"MINUTAS_PILOTO_ENABLED": True, "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]]})
    admin = MinutasAcervoService(library)
    first = admin.apply(admin.dry_run(), {"identificador": "backup-sintetico", "restauracao_verificada": True})
    assert first["alterados"] == 3
    assert first["antes"] == first["depois"]
    assert admin.apply(admin.dry_run(), {"identificador": "backup-sintetico", "restauracao_verificada": True})["alterados"] == 0
    results = MinutasSearchService(library).search("Conteúdo sintético")["resultados"]
    assert results and {row["referencia_id"] for row in results} == {target["id"]}
    assert library.repository.get(chamber["id"]) is None
    admin.update_state(RPPS, {"estado": "Em testes"})
    raw.execute(migration_sql)
    assert next(row for row in admin.catalog() if row["id"] == RPPS)["estado"] == "Em testes"
    private = raw.execute("SELECT relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                          "WHERE n.nspname=%s AND c.relname='minutas_classificacoes'", (schema,)).fetchone()
    assert private == (True,)
