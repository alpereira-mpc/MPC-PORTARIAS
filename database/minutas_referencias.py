"""Metadata persistence for the isolated Minutas IA reference library."""

from database.store import now, unwrap_store


COLUMNS = (
    "id,procuradora,procuradora_chave,numero_processo,processo_chave,exercicio,"
    "subtipo,auditoria_nome,auditoria_mime,auditoria_tamanho,auditoria_path,"
    "parecer_nome,parecer_mime,parecer_tamanho,parecer_path,criado_em,criado_por,"
    "atualizado_em,atualizado_por"
)
COMPLEMENT_COLUMNS = (
    "id,referencia_id,tipo,descricao,posicao,data_documento,nome,mime,tamanho,path,"
    "criado_em,criado_por,atualizado_em,atualizado_por"
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

    def create_document(self, record):
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO minutas_documentos_complementares("
                + COMPLEMENT_COLUMNS
                + ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                tuple(record[column] for column in COMPLEMENT_COLUMNS.split(",")),
            )
        return self.get_document(record["id"])

    def get_document(self, identifier):
        with self.store.connection(read_only=True) as connection:
            row = connection.execute(
                f"SELECT {COMPLEMENT_COLUMNS} "
                "FROM minutas_documentos_complementares WHERE id=?",
                (identifier,),
            ).fetchone()
        return dict(row) if row else None

    def list_documents(self, reference_id):
        with self.store.connection(read_only=True) as connection:
            rows = connection.execute(
                f"SELECT {COMPLEMENT_COLUMNS} "
                "FROM minutas_documentos_complementares "
                "WHERE referencia_id=? ORDER BY posicao,id",
                (reference_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_documents(self, reference_id, documents, actor):
        stamp = now()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for document in documents:
                result = connection.execute(
                    "UPDATE minutas_documentos_complementares SET tipo=?,descricao=?,"
                    "posicao=?,data_documento=?,atualizado_em=?,atualizado_por=? "
                    "WHERE id=? AND referencia_id=?",
                    (
                        document["tipo"],
                        document["descricao"],
                        document["posicao"],
                        document["data_documento"],
                        stamp,
                        actor,
                        document["id"],
                        reference_id,
                    ),
                )
                if not getattr(result, "rowcount", 1):
                    raise ValueError("Documento complementar não encontrado.")
        return self.list_documents(reference_id)

    def delete_document(self, identifier, reference_id):
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "DELETE FROM minutas_documentos_complementares "
                "WHERE id=? AND referencia_id=?",
                (identifier, reference_id),
            )
            if not getattr(result, "rowcount", 1):
                raise ValueError("Documento complementar não encontrado.")

    def promote_document(self, reference_id, document_id, displaced, actor):
        stamp = now()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            reference = connection.execute(
                "SELECT auditoria_nome,auditoria_mime,auditoria_tamanho,auditoria_path "
                "FROM minutas_referencias WHERE id=?",
                (reference_id,),
            ).fetchone()
            selected = connection.execute(
                f"SELECT {COMPLEMENT_COLUMNS} "
                "FROM minutas_documentos_complementares "
                "WHERE id=? AND referencia_id=?",
                (document_id, reference_id),
            ).fetchone()
            if not reference or not selected:
                raise ValueError("Referência ou documento não encontrado.")
            selected = dict(selected)
            connection.execute(
                "DELETE FROM minutas_documentos_complementares "
                "WHERE id=? AND referencia_id=?",
                (document_id, reference_id),
            )
            connection.execute(
                "INSERT INTO minutas_documentos_complementares("
                + COMPLEMENT_COLUMNS
                + ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    displaced["id"],
                    reference_id,
                    displaced["tipo"],
                    displaced["descricao"],
                    displaced["posicao"],
                    displaced["data_documento"],
                    reference["auditoria_nome"],
                    reference["auditoria_mime"],
                    reference["auditoria_tamanho"],
                    reference["auditoria_path"],
                    stamp,
                    actor,
                    stamp,
                    actor,
                ),
            )
            connection.execute(
                "UPDATE minutas_referencias SET auditoria_nome=?,auditoria_mime=?,"
                "auditoria_tamanho=?,auditoria_path=?,atualizado_em=?,atualizado_por=? "
                "WHERE id=?",
                (
                    selected["nome"],
                    selected["mime"],
                    selected["tamanho"],
                    selected["path"],
                    stamp,
                    actor,
                    reference_id,
                ),
            )
        return self.get(reference_id), self.list_documents(reference_id)

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
