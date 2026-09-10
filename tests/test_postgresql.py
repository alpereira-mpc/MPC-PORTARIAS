"""Real PostgreSQL tests: only a guarded, loopback-only disposable database.

No DATABASE_URL or Streamlit Secret is read here. Set MPC_TEST_POSTGRES_URL
only for a cluster created specifically for tests; external hosts are rejected.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import gzip
import inspect
import json
import os
import subprocess
import sys
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import pytest

from database.postgresql import DatabaseUnavailable, TABLES
from database.store import Store
from services.exports import export_record
from tests.cases import sample
from tests import test_persistence as persistence
from tests import test_deletion as deletion


@pytest.fixture(scope="module")
def pg_url():
    url = os.environ.get("MPC_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("MPC_TEST_POSTGRES_URL não configurada para PostgreSQL descartável")
    options = conninfo_to_dict(url)
    assert options.get("host") in ("127.0.0.1", "localhost", "::1")
    assert options.get("dbname") == "mpc_disposable_tests"
    assert "hostaddr" not in options and "service" not in options
    with psycopg.connect(url) as c:
        assert (
            c.execute("SELECT tag FROM public.mpc_test_guard").fetchone()[0]
            == "disposable-mpc-tests-v1"
        )
    return url


@pytest.fixture
def pg_store(pg_url, tmp_path):
    store = Store(database_url=pg_url, postgres_schema="mpc_test_" + uuid.uuid4().hex)
    store.path = tmp_path / "runtime"
    store.configure(export_dir=str(tmp_path / "exports"))
    return store


@pytest.mark.parametrize(
    "scenario",
    [
        persistence.test_draft_preview_finalize_cancel_year,
        persistence.test_concurrent_numbering,
        persistence.test_same_draft_concurrent,
        persistence.test_generator_failure_rolls_back,
        persistence.test_export_never_overwrites,
        persistence.test_pdf_absent_is_recoverable,
        persistence.test_sequence_admin_safeguards,
        persistence.test_warning_confirmation,
        persistence.test_office_assignment_atomic,
        persistence.test_inactive_member_blocks_finalization,
        persistence.test_duplicate_drops_manual_template_override,
        deletion.test_cancelled_lower_number_occupied,
        deletion.test_successive_deletions,
        deletion.test_delete_cancelled,
        deletion.test_failed_backup_blocks_deletion,
        deletion.test_delete_draft_no_sequence_change,
        deletion.test_delete_repeat_does_not_touch_reused_number,
        deletion.test_invalid_id_and_empty_reason,
        deletion.test_file_original_intact_if_database_rollback,
        deletion.test_permission_error_after_commit_is_logged,
        deletion.test_admin_gaps_and_other_year,
    ],
    ids=lambda f: f.__name__,
)
def test_existing_contract_on_postgres(pg_store, monkeypatch, scenario):
    kwargs = {"store": pg_store}
    if "monkeypatch" in inspect.signature(scenario).parameters:
        kwargs["monkeypatch"] = monkeypatch
    scenario(**kwargs)


def test_bootstrap_idempotent(pg_store, pg_url):
    assert pg_store.next_number(2026) == 9 and pg_store.baseline(2026) == 8
    assert [p["nome"] for p in pg_store.catalog("procuradores")] == [
        "Elvira Samara Pereira de Oliveira",
        "Isabella Barbosa Marinho Falcão",
        "Bradson Tibério Luna Camelo",
        "Marcílio Toscano Franca Filho",
        "Manoel Antonio dos Santos Neto",
        "Luciano Andrade Farias",
        "Sheyla Barreto Braga de Queiroz",
    ]
    member = pg_store.catalog("procuradores")[0]
    member["nome"] = "Nome configurado"
    pg_store.save_member(member)
    pg_store.set_sequence(2026, 17, True)
    again = Store(database_url=pg_url, postgres_schema=pg_store._postgres.schema)
    assert again.catalog("procuradores")[0]["nome"] == "Nome configurado"
    assert again.next_number(2026) == 18 and again.baseline(2026) == 17


def test_concurrent_bootstrap(pg_url):
    schema = "mpc_test_" + uuid.uuid4().hex
    with ThreadPoolExecutor(max_workers=4) as pool:
        stores = list(
            pool.map(
                lambda _: Store(database_url=pg_url, postgres_schema=schema), range(4)
            )
        )
    assert all(len(s.catalog("procuradores")) == 7 for s in stores)
    assert all(s.next_number(2026) == 9 for s in stores)


def test_nonempty_database_is_not_seeded(pg_url):
    schema = "mpc_test_" + uuid.uuid4().hex
    with psycopg.connect(pg_url) as c:
        c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        c.execute(
            sql.SQL(
                "CREATE TABLE {}.configuracoes(chave TEXT PRIMARY KEY,valor TEXT NOT NULL)"
            ).format(sql.Identifier(schema))
        )
        c.execute(
            sql.SQL("INSERT INTO {}.configuracoes VALUES('custom','preserve')").format(
                sql.Identifier(schema)
            )
        )
    store = Store(database_url=pg_url, postgres_schema=schema)
    assert store.settings() == {"custom": "preserve"}
    assert store.catalog("procuradores") == []
    assert store.next_number(2026) == 1


def test_constraints_and_delete_authorization(pg_store):
    identifier = deletion.finalized(pg_store)[0]
    for query in (
        "DELETE FROM portarias WHERE id=?",
        "UPDATE portarias SET numero=99 WHERE id=?",
    ):
        with pytest.raises(DatabaseUnavailable, match="23514"):
            with pg_store.connection() as c:
                c.execute(query, (identifier,))
    draft = pg_store.save_draft(sample(pg_store))
    with pytest.raises(DatabaseUnavailable, match="23505"):
        with pg_store.connection() as c:
            c.execute(
                "UPDATE portarias SET numero=9,status='Finalizada' WHERE id=?", (draft,)
            )
    deletion.remove(pg_store, identifier)
    pg_store.finalize(draft)
    with pytest.raises(DatabaseUnavailable, match="23514"):
        with pg_store.connection() as c:
            c.execute("DELETE FROM portarias WHERE id=?", (draft,))


def test_audit_and_durable_backup(pg_store):
    identifier = deletion.finalized(pg_store)[0]
    before = pg_store.get(identifier)
    export_record(pg_store, identifier)
    result = deletion.remove(pg_store, identifier)
    audit = pg_store.deletion_history()[0]
    assert audit["portaria_id_original"] == identifier
    assert audit["sequencia_antes"] == 9 and audit["sequencia_depois"] == 8
    backup = Path(result["backup"])
    snapshot = json.loads(gzip.decompress(backup.read_bytes()))
    assert snapshot["tables"]["portarias"][0]["id"] == identifier
    import base64

    assert (
        base64.b64decode(snapshot["tables"]["portarias"][0]["docx"]["base64"])
        == before["docx"]
    )
    with pg_store.connection() as c:
        durable = c.execute(
            "SELECT conteudo FROM backup_snapshots WHERE referencia=?", (str(backup),)
        ).fetchone()[0]
        assert durable == backup.read_bytes()
        assert c.execute("SELECT COUNT(*) FROM substituicoes").fetchone()[0] == 0
        assert (
            c.execute("SELECT estado FROM audit_arquivos").fetchone()[0] == "QUARENTENA"
        )


def test_delete_rollback_including_backup(pg_store, monkeypatch):
    identifier = deletion.finalized(pg_store)[0]
    before = pg_store.get(identifier)

    def fail(*args):
        raise OSError("injected audit failure")

    monkeypatch.setattr(pg_store, "event", fail)
    with pytest.raises(OSError):
        deletion.remove(pg_store, identifier)
    assert pg_store.get(identifier) == before
    assert pg_store.next_number(2026) == 10 and not pg_store.deletion_history()
    with pg_store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM backup_snapshots").fetchone()[0] == 0


def test_restart_in_fresh_process(pg_store, pg_url):
    identifier = deletion.finalized(pg_store)[0]
    code = "from database.store import Store; import os,json; s=Store(database_url=os.environ['MPC_TEST_POSTGRES_URL'],postgres_schema=os.environ['MPC_TEST_SCHEMA']); print(json.dumps([s.next_number(2026),len(s.catalog('procuradores'))]))"
    env = dict(
        os.environ,
        MPC_TEST_POSTGRES_URL=pg_url,
        MPC_TEST_SCHEMA=pg_store._postgres.schema,
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout) == [10, 7]
    assert pg_store.get(identifier)["docx"]


def test_concurrent_delete_and_finalize(pg_store):
    old = deletion.finalized(pg_store)[0]
    draft = pg_store.save_draft(sample(pg_store))
    with ThreadPoolExecutor(max_workers=2) as pool:
        deleting = pool.submit(deletion.remove, pg_store, old)
        finalizing = pool.submit(pg_store.finalize, draft, confirmed_warnings=True)
        deleting.result()
        finalizing.result()
    record = pg_store.get(draft)
    assert record["numero"] in (9, 10)
    assert pg_store.next_number(2026) == record["numero"] + 1
    assert len(pg_store.deletion_history()) == 1


def test_catalogs_and_binary_snapshot(pg_store):
    pg_store.save_reason("Novo", "por motivo de teste")
    pg_store.save_basis("Outro", "Base configurada", "Nota")
    member = dict(
        pg_store.catalog("procuradores")[0], id=None, nome="Novo membro", ativo=True
    )
    pg_store.save_member(member)
    assert len(pg_store.catalog("procuradores")) == 8
    identifier = deletion.finalized(pg_store)[0]
    pg_store.cache_pdf(identifier, b"%PDF-1.4 test")
    assert pg_store.get(identifier)["pdf"] == b"%PDF-1.4 test"


def test_schema_ssl_foreign_keys_and_indexes(pg_store):
    with pg_store.connection() as c:
        names = {
            r[0]
            for r in c.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname=current_schema()"
            )
        }
        assert names == set(TABLES) | {"schema_migrations", "backup_snapshots"}
        assert c.execute(
            "SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid=pg_backend_pid()"
        ).fetchone()[0]
        assert c.execute("SHOW transaction_isolation").fetchone()[0] == "read committed"
        indexes = {
            r[0]
            for r in c.execute(
                "SELECT indexname FROM pg_catalog.pg_indexes WHERE schemaname=current_schema()"
            )
        }
        assert {"exportacoes_portaria_idx", "audit_arquivos_audit_idx"} <= indexes
    with pytest.raises(DatabaseUnavailable, match="23503"):
        with pg_store.connection() as c:
            c.execute(
                "INSERT INTO substituicoes(portaria_id,ordem,payload) VALUES('missing',0,'{}')"
            )
    with pytest.raises(DatabaseUnavailable, match="23514"):
        with pg_store.connection() as c:
            c.execute("INSERT INTO sequencias VALUES(999,0)")


@pytest.mark.parametrize("count,index,expected", [(1, 0, 9), (2, 1, 10), (3, 0, 12)])
def test_delete_sequence_cases(pg_store, count, index, expected):
    identifiers = deletion.finalized(pg_store, count)
    deletion.remove(pg_store, identifiers[index])
    assert pg_store.next_number(2026) == expected
    assert pg_store.baseline(2026) == 8
    for i, identifier in enumerate(identifiers):
        if i != index:
            assert pg_store.get(identifier)["numero"] == 9 + i


def test_new_year_concurrent_numbering(pg_store):
    payload = sample(pg_store)
    payload["data"] = "2027-01-01"
    payload["substituicoes"][0].update(inicio="2027-01-02", fim="2027-01-10")
    identifiers = [pg_store.save_draft(payload) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda i: pg_store.finalize(i, confirmed_warnings=True), identifiers
            )
        )
    assert sorted(pg_store.get(i)["numero"] for i in identifiers) == [1, 2, 3, 4]
    assert pg_store.next_number(2026) == 9


def test_numbering_across_processes(pg_store, pg_url):
    identifiers = [pg_store.save_draft(sample(pg_store)) for _ in range(3)]
    code = "from database.store import Store; import os,sys; s=Store(database_url=os.environ['MPC_TEST_POSTGRES_URL'],postgres_schema=os.environ['MPC_TEST_SCHEMA']); s.finalize(sys.argv[1],confirmed_warnings=True)"
    env = dict(
        os.environ,
        MPC_TEST_POSTGRES_URL=pg_url,
        MPC_TEST_SCHEMA=pg_store._postgres.schema,
    )

    def worker(identifier):
        subprocess.run(
            [sys.executable, "-c", code, identifier],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(worker, identifiers))
    assert sorted(pg_store.get(i)["numero"] for i in identifiers) == [9, 10, 11]


def test_backup_survives_reconnection_and_lost_local_copy(pg_store, pg_url):
    identifier = deletion.finalized(pg_store)[0]
    result = deletion.remove(pg_store, identifier)
    backup = Path(result["backup"])
    expected = backup.read_bytes()
    backup.unlink()  # Only this fixture's temporary backup.
    reopened = Store(database_url=pg_url, postgres_schema=pg_store._postgres.schema)
    assert len(reopened.deletion_history()) == 1
    assert reopened.next_number(2026) == 9
    with reopened.connection() as c:
        assert (
            c.execute(
                "SELECT conteudo FROM backup_snapshots WHERE referencia=?",
                (str(backup),),
            ).fetchone()[0]
            == expected
        )


def test_bootstrap_rollback_and_retry(pg_url, monkeypatch):
    from database.postgresql import Connection

    schema = "mpc_test_" + uuid.uuid4().hex
    original = Connection.executemany

    def fail(self, statement, values):
        if "procuradores" in statement:
            raise OSError("bootstrap interrupted")
        return original(self, statement, values)

    with monkeypatch.context() as scoped:
        scoped.setattr(Connection, "executemany", fail)
        with pytest.raises(OSError):
            Store(database_url=pg_url, postgres_schema=schema)
    restored = Store(database_url=pg_url, postgres_schema=schema)
    assert len(restored.catalog("procuradores")) == 7
    assert restored.next_number(2026) == 9


def test_legacy_public_data_blocks_new_bootstrap(pg_url):
    # This fixture is guarded against non-loopback/non-disposable databases.
    with psycopg.connect(pg_url) as c:
        c.execute("CREATE TABLE public.portarias (id TEXT)")
        c.execute("INSERT INTO public.portarias VALUES('legacy')")
    try:
        with pytest.raises(DatabaseUnavailable, match="schema public"):
            Store(database_url=pg_url, postgres_schema="mpc_test_" + uuid.uuid4().hex)
        with psycopg.connect(pg_url) as c:
            assert (
                c.execute("SELECT id FROM public.portarias").fetchone()[0] == "legacy"
            )
    finally:
        with psycopg.connect(pg_url) as c:
            c.execute("DROP TABLE public.portarias")  # Disposable test fixture only.


def test_postgres_streamlit_screens_and_backup(pg_store, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT

    monkeypatch.setattr("database.store.Store", lambda: pg_store)
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    app.button(key="open_portarias").click().run()
    assert not app.exception and not app.error
    for screen in ("Histórico", "Procuradores", "Configurações"):
        app.sidebar.radio(key="nav").set_value(screen).run()
        assert not app.exception and not app.error
    next(x for x in app.button if x.label == "Criar backup do banco").click().run()
    assert not app.exception and not app.error
    with pg_store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM backup_snapshots").fetchone()[0] == 1


def test_manual_backup_never_overwrites_and_migration_is_idempotent(pg_store, tmp_path):
    destination = tmp_path / "manual.db"
    backup = pg_store.backup(destination)
    before = backup.read_bytes()
    with pytest.raises(ValueError, match="já existe"):
        pg_store.backup(destination)
    assert backup.read_bytes() == before
    pg_store.migrate()
    assert pg_store.baseline(2026) == 8 and pg_store.next_number(2026) == 9
