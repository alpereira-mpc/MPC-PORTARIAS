"""Operação explícita; nunca lê Secrets nem inicializa/migra o Store compartilhado.

Use --help. A conexão é fornecida exclusivamente por MINUTAS_MIGRATION_DATABASE_URL.
Não executar contra produção sem autorização, inventário e restauração de backup.
"""

import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database.postgresql import PostgresBackend
from database.minutas_especialidades import MinutasSpecialtiesStore
from services.minutas_acervo import migration_plan, storage_inventory


class OperationalConnection:
    backend = "postgresql"

    def __init__(self, url, schema):
        self.backend_connection = PostgresBackend(url, schema)

    def connection(self, **kwargs):
        return self.backend_connection.connection(**kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inventario-legado", "simular", "aplicar"))
    parser.add_argument("--schema", default="mpc_portarias")
    parser.add_argument("--report", type=Path, required=True, help="Novo JSON; nunca sobrescreve arquivo existente")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--backup-evidence", help="Identificador do backup banco+Storage e prova de restauração")
    parser.add_argument("--restore-verified", action="store_true")
    parser.add_argument("--actor", help="Identidade do operador autorizado")
    parser.add_argument("--storage-bucket", help="Inventaria também storage.objects no bucket privado informado, sem baixar PDFs")
    args = parser.parse_args(argv)
    if args.report.exists(): parser.error("O relatório já existe; escolha um novo caminho.")
    url = os.environ.get("MINUTAS_MIGRATION_DATABASE_URL")
    if not url: parser.error("Defina explicitamente MINUTAS_MIGRATION_DATABASE_URL no ambiente seguro.")
    repository = MinutasSpecialtiesStore(OperationalConnection(url, args.schema))
    snapshot = repository.legacy_snapshot() if args.mode == "inventario-legado" else repository.snapshot()
    current = migration_plan(snapshot)
    if args.storage_bucket:
        with repository.store.connection(read_only=True) as c:
            objects = [dict(row) for row in c.execute("SELECT name,metadata FROM storage.objects WHERE bucket_id=? ORDER BY name",
                                                    (args.storage_bucket,)).fetchall()]
        current["storage"] = storage_inventory(snapshot, objects)
    if args.mode == "inventario-legado":
        report = current
    else:
        if args.mode == "simular":
            report = current
        else:
            if not (args.plan and args.actor and args.backup_evidence and args.restore_verified):
                parser.error("Aplicação exige --plan, --actor, --backup-evidence e --restore-verified.")
            reviewed = json.loads(args.plan.read_text(encoding="utf-8"))
            if reviewed != current: parser.error("Inventário alterado; gere e confira nova simulação.")
            if args.storage_bucket and current["storage"]["vinculos_sem_objeto"]:
                parser.error("Há vínculos sem arquivo no Storage; confira a integridade antes de aplicar.")
            changed = repository.apply_plan(current, args.actor,
                {"identificador": args.backup_evidence, "restauracao_verificada": True})
            after = migration_plan(repository.snapshot())["contagens"]
            report = {"alterados": changed, "antes": current["contagens"], "depois": after,
                      "reconciliado": current["contagens"] == after}
    with args.report.open("x", encoding="utf-8") as output:
        json.dump(report, output, ensure_ascii=False, indent=2, default=str)
    print("Relatório gravado. Nenhum arquivo original foi movido ou copiado.")


if __name__ == "__main__":
    main()
