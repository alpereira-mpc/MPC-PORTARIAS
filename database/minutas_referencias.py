"""Metadata persistence for the isolated Minutas IA reference library."""

from database.store import now, unwrap_store


COLUMNS = (
    "id,procuradora,procuradora_chave,numero_processo,processo_chave,exercicio,"
    "subtipo,auditoria_nome,auditoria_mime,auditoria_tamanho,auditoria_path,"
    "parecer_nome,parecer_mime,parecer_tamanho,parecer_path,criado_em,criado_por,"
    "atualizado_em,atualizado_por"
)


class MinutasReferenciasStore:
    """Operate only on the manually provisioned PostgreSQL table."""

    def __init__(self, store):
        self.store = unwrap_store(store)
        if getattr(self.store, "backend", None) != "postgresql":
            raise RuntimeError("A biblioteca de referências exige PostgreSQL.")

    def create(self, record):
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO minutas_referencias("
                + COLUMNS
                + ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(record[column] for column in COLUMNS.split(",")),
            )
        return self.get(record["id"])

    def get(self, identifier):
        with self.store.connection(read_only=True) as connection:
            row = connection.execute(
                f"SELECT {COLUMNS} FROM minutas_referencias WHERE id=?",
                (identifier,),
            ).fetchone()
        return dict(row) if row else None

    def get_by_process(self, procuradora_key, process_key):
        with self.store.connection(read_only=True) as connection:
            row = connection.execute(
                f"SELECT {COLUMNS} FROM minutas_referencias "
                "WHERE procuradora_chave=? AND processo_chave=?",
                (procuradora_key, process_key),
            ).fetchone()
        return dict(row) if row else None

    def list(self):
        with self.store.connection(read_only=True) as connection:
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM minutas_referencias "
                "ORDER BY exercicio DESC, numero_processo, id"
            ).fetchall()
        return [dict(row) for row in rows]

    def replace_document(self, identifier, kind, document, actor):
        if kind not in {"auditoria", "parecer"}:
            raise ValueError("Tipo de documento inválido.")
        stamp = now()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                f"UPDATE minutas_referencias SET {kind}_nome=?,{kind}_mime=?,"
                f"{kind}_tamanho=?,{kind}_path=?,atualizado_em=?,atualizado_por=? "
                "WHERE id=?",
                (
                    document["name"],
                    document["mime"],
                    document["size"],
                    document["path"],
                    stamp,
                    actor,
                    identifier,
                ),
            )
            if not getattr(result, "rowcount", 1):
                raise ValueError("Referência não encontrada.")
        return self.get(identifier)

    def delete(self, identifier):
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "DELETE FROM minutas_referencias WHERE id=?", (identifier,)
            )
            if not getattr(result, "rowcount", 1):
                raise ValueError("Referência não encontrada.")
