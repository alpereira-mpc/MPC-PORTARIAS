"""Add administrative deletion without rewriting finalized snapshots or catalogs."""

import json


def migrate_v2(store, backup_required):
    with store.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        if c.execute("PRAGMA user_version").fetchone()[0] == 2:
            return
        backup = (
            str(store.automatic_backup("antes_migration_v2"))
            if backup_required
            else None
        )
        statements = [
            "CREATE TABLE sequencia_baselines (ano INTEGER PRIMARY KEY, baseline INTEGER NOT NULL CHECK(baseline>=0))",
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, data_hora TEXT NOT NULL, acao TEXT NOT NULL, portaria_id_original TEXT NOT NULL UNIQUE, numero INTEGER, ano INTEGER NOT NULL, status_anterior TEXT NOT NULL, motivo TEXT NOT NULL, dados_resumidos TEXT NOT NULL, arquivo_docx TEXT NOT NULL, arquivo_pdf TEXT NOT NULL, backup TEXT NOT NULL, sequencia_antes INTEGER NOT NULL, sequencia_depois INTEGER NOT NULL)",
            "CREATE TABLE exportacoes (id INTEGER PRIMARY KEY, portaria_id TEXT NOT NULL REFERENCES portarias(id), caminho TEXT NOT NULL UNIQUE, formato TEXT NOT NULL CHECK(formato IN ('docx','pdf')), sha256 TEXT NOT NULL)",
            "CREATE TABLE audit_arquivos (id INTEGER PRIMARY KEY, audit_id INTEGER NOT NULL REFERENCES audit_log(id), caminho TEXT NOT NULL, quarentena TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL, estado TEXT NOT NULL, detalhe TEXT NOT NULL DEFAULT '')",
            "DROP TRIGGER manter_portaria",
            "CREATE TRIGGER manter_portaria BEFORE DELETE ON portarias WHEN OLD.status != 'Rascunho' AND mpc_delete_authorized(OLD.id)!=1 BEGIN SELECT RAISE(ABORT,'Use a exclusão administrativa com backup e confirmação'); END",
        ]
        for sql in statements:
            c.execute(sql)
        adjustments = {}
        emitted = {}
        for row in c.execute(
            "SELECT acao,detalhes FROM eventos WHERE acao IN ('ajustar_sequencia','finalizar') ORDER BY id"
        ):
            data = json.loads(row[1])
            year = data["ano"]
            if row[0] == "finalizar":
                emitted[year] = max(emitted.get(year, 0), data["numero"])
            elif data["ultimo"] > emitted.get(year, 0) or year not in emitted:
                # Re-confirming the current app-issued number does not turn it
                # into a pre-implementation administrative reservation.
                adjustments[year] = max(adjustments.get(year, 0), data["ultimo"])
        for year, last in c.execute("SELECT ano,ultimo FROM sequencias").fetchall():
            # Prefer explicit historic administrative setup. If provenance is absent,
            # keep the high-water mark rather than guessing that external numbers are free.
            baseline = adjustments.get(year, last)
            c.execute("INSERT INTO sequencia_baselines VALUES(?,?)", (year, baseline))
        c.execute(
            "UPDATE motivos_afastamento SET texto=? WHERE nome=? AND texto=?",
            (
                "por motivo de gozo de licença especial",
                "licença especial",
                "por motivo de gozo de licença especial {do_titular}",
            ),
        )
        store.event(
            c,
            "migration_v2",
            {
                "backup": backup,
                "baselines": [
                    dict(r) for r in c.execute("SELECT * FROM sequencia_baselines")
                ],
            },
        )
        c.execute("PRAGMA user_version=2")
        if (
            c.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or c.execute("PRAGMA foreign_key_check").fetchone()
        ):
            raise ValueError("Migração revertida: falha de integridade.")
