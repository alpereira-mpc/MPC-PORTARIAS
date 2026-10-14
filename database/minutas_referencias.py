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
EXTRACTION_COLUMNS = (
    "id,referencia_id,documento_id,papel,tipo,posicao,procuradora,"
    "procuradora_chave,arquivo_nome,arquivo_mime,arquivo_tamanho,arquivo_path,"
    "arquivo_sha256,extrator_versao,status,texto_integral,unidades_json,"
    "secoes_json,erro,processado_em,processado_por,atualizado_em"
)
EXTRACTION_INVENTORY_COLUMNS = (
    "referencia_id,papel,tipo,posicao,arquivo_nome,arquivo_tamanho,arquivo_path,"
    "arquivo_sha256,extrator_versao,status,erro,atualizado_em"
)


class MinutasReferenciasStore:
    """Operate only on the manually provisioned PostgreSQL table."""

    def __init__(self, store, specialty_id=None, *, include_acervo=False):
        self.store = unwrap_store(store)
        self.specialty_id = specialty_id
        self.include_acervo = include_acervo or bool(specialty_id)
        if specialty_id:
            from services.minutas_specialties import scope
            scope(specialty_id)
        if getattr(self.store, "backend", None) != "postgresql":
            raise RuntimeError("A biblioteca de referências exige PostgreSQL.")

    def _active(self, connection, alias="r"):
        from database.minutas_especialidades import active_clause, separation_enabled
        return active_clause(separation_enabled(connection, getattr(self.store, "backend", "")), alias)

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

    def profile(self):
        from services.minutas_specialties import scope
        from copy import deepcopy
        import json
        result = deepcopy(scope(self.specialty_id))
        with self.store.connection(read_only=True) as c:
            row = c.execute("SELECT * FROM minutas_especialidades WHERE id=? AND procuradora_chave=?",
                            (self.specialty_id, result["procuradora_chave"])).fetchone()
        if not row:
            raise ValueError("Especialidade ainda não provisionada.")
        result.update(dict(row))
        result["config"].update(json.loads(result.pop("config_json")))
        return result

    def scope_revision(self):
        from database.minutas_especialidades import scoped_clause
        from services.minutas_specialties import fingerprint, PROSECUTOR_KEY
        profile = self.profile()
        with self.store.connection(read_only=True) as c:
            row = c.execute("SELECT COUNT(e.id) AS total,MAX(e.atualizado_em) AS atualizado "
                "FROM minutas_extracoes e JOIN minutas_referencias r ON r.id=e.referencia_id WHERE " + scoped_clause()
                + self._active(c),
                (PROSECUTOR_KEY, self.specialty_id)).fetchone()
        return fingerprint([profile, dict(row)])

    def get(self, identifier):
        if self.specialty_id:
            from database.minutas_especialidades import scoped_clause
            from services.minutas_specialties import PROSECUTOR_KEY
            with self.store.connection(read_only=True) as connection:
                row = connection.execute(
                    "SELECT r.*,mc.especialidade_id,mc.classe,mc.estado AS classificacao_estado,"
                    "mc.entidade AS origem,mc.revisao AS classificacao_revisao "
                    "FROM minutas_referencias r JOIN minutas_classificacoes mc ON mc.referencia_id=r.id "
                    "WHERE r.id=? AND " + scoped_clause() + self._active(connection),
                    (identifier, PROSECUTOR_KEY, self.specialty_id),
                ).fetchone()
            if row:
                result = dict(row)
                result["especialidade_id"] = self.specialty_id
                return result
            return None
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
                "WHERE procuradora_chave=? AND processo_chave=?" + self._active(connection, ""),
                (procuradora_key, process_key),
            ).fetchone()
        return dict(row) if row else None

    def list(self):
        if self.specialty_id:
            from database.minutas_especialidades import scoped_clause
            from services.minutas_specialties import PROSECUTOR_KEY
            with self.store.connection(read_only=True) as connection:
                rows = connection.execute(
                    "SELECT r.*,mc.classe,mc.estado AS classificacao_estado,mc.entidade AS origem,"
                    "mc.revisao AS classificacao_revisao FROM minutas_referencias r "
                    "JOIN minutas_classificacoes mc ON mc.referencia_id=r.id WHERE " + scoped_clause()
                    + self._active(connection)
                    + " ORDER BY r.exercicio DESC,r.numero_processo,r.id",
                    (PROSECUTOR_KEY, self.specialty_id),
                ).fetchall()
            return [dict(row, especialidade_id=self.specialty_id) for row in rows]
        with self.store.connection(read_only=True) as connection:
            active = self._active(connection, "")
            rows = connection.execute(
                f"SELECT {COLUMNS} FROM minutas_referencias"
                + (" WHERE acervo='ativo'" if active else "")
                + " ORDER BY exercicio DESC, numero_processo, id"
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
        if self.specialty_id and self.get(reference_id) is None:
            return []
        with self.store.connection(read_only=True) as connection:
            rows = connection.execute(
                f"SELECT {COMPLEMENT_COLUMNS} "
                "FROM minutas_documentos_complementares "
                "WHERE referencia_id=? ORDER BY posicao,id",
                (reference_id,),
            ).fetchall()
            extra = []
            if self.include_acervo:
                extra = connection.execute(
                    "SELECT * FROM minutas_acervo_documentos WHERE referencia_id=? ORDER BY posicao,id",
                    (reference_id,),
                ).fetchall()
        return [dict(row) for row in (*rows, *extra)]

    def get_extraction(self, path):
        if self.specialty_id:
            from database.minutas_especialidades import scoped_clause
            from services.minutas_specialties import PROSECUTOR_KEY
            with self.store.connection(read_only=True) as c:
                row = c.execute("SELECT e.* FROM minutas_extracoes e JOIN minutas_referencias r "
                    "ON r.id=e.referencia_id WHERE e.arquivo_path=? AND " + scoped_clause() + self._active(c),
                    (path, PROSECUTOR_KEY, self.specialty_id)).fetchone()
            return dict(row) if row else None
        with self.store.connection(read_only=True) as connection:
            row = connection.execute(
                f"SELECT {EXTRACTION_COLUMNS} FROM minutas_extracoes "
                "WHERE arquivo_path=?",
                (path,),
            ).fetchone()
        return dict(row) if row else None

    def list_extractions(self, reference_id):
        if self.specialty_id and self.get(reference_id) is None:
            return []
        with self.store.connection(read_only=True) as connection:
            rows = connection.execute(
                f"SELECT {EXTRACTION_COLUMNS} FROM minutas_extracoes "
                "WHERE referencia_id=? ORDER BY papel,posicao,arquivo_path",
                (reference_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_extractions_by_procuradora(self, procuradora_key):
        if self.specialty_id:
            return self._scoped_extractions(procuradora_key, inventory=False)
        with self.store.connection(read_only=True) as connection:
            if self._active(connection):
                columns = ",".join("e." + name for name in EXTRACTION_COLUMNS.split(","))
                rows = connection.execute(
                    "SELECT " + columns + " FROM minutas_extracoes e "
                    "JOIN minutas_referencias r ON r.id=e.referencia_id "
                    "WHERE e.procuradora_chave=? AND e.status='PROCESSADO' AND r.acervo='ativo' "
                    "ORDER BY e.referencia_id,e.papel,e.posicao,e.arquivo_path",
                    (procuradora_key,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {EXTRACTION_COLUMNS} FROM minutas_extracoes "
                    "WHERE procuradora_chave=? AND status='PROCESSADO' "
                    "ORDER BY referencia_id,papel,posicao,arquivo_path",
                    (procuradora_key,),
                ).fetchall()
        return [dict(row) for row in rows]

    def list_extraction_inventory_by_procuradora(self, procuradora_key):
        """Return generation metadata without loading extracted legal text."""
        if self.specialty_id:
            return self._scoped_extractions(procuradora_key, inventory=True)
        with self.store.connection(read_only=True) as connection:
            if self._active(connection):
                columns = ",".join("e." + name for name in EXTRACTION_INVENTORY_COLUMNS.split(","))
                rows = connection.execute(
                    "SELECT " + columns + " FROM minutas_extracoes e "
                    "JOIN minutas_referencias r ON r.id=e.referencia_id "
                    "WHERE e.procuradora_chave=? AND r.acervo='ativo' "
                    "ORDER BY e.referencia_id,e.papel,e.posicao,e.arquivo_path",
                    (procuradora_key,),
                ).fetchall()
            else:
                rows = connection.execute(
                    f"SELECT {EXTRACTION_INVENTORY_COLUMNS} FROM minutas_extracoes "
                    "WHERE procuradora_chave=? "
                    "ORDER BY referencia_id,papel,posicao,arquivo_path",
                    (procuradora_key,),
                ).fetchall()
        return [dict(row) for row in rows]

    def _scoped_extractions(self, procuradora_key, *, inventory):
        from database.minutas_especialidades import scoped_clause
        from services.minutas_specialties import scope
        if procuradora_key != scope(self.specialty_id)["procuradora_chave"]:
            raise ValueError("Procuradora incompatível com a especialidade.")
        columns = EXTRACTION_INVENTORY_COLUMNS if inventory else EXTRACTION_COLUMNS
        columns = ",".join("e." + name for name in columns.split(","))
        with self.store.connection(read_only=True) as connection:
            rows = connection.execute(
                "SELECT " + columns + " FROM minutas_extracoes e JOIN minutas_referencias r ON r.id=e.referencia_id "
                "WHERE e.procuradora_chave=r.procuradora_chave AND " + scoped_clause()
                + self._active(connection)
                + ("" if inventory else " AND e.status='PROCESSADO'")
                + " ORDER BY e.referencia_id,e.papel,e.posicao,e.arquivo_path",
                (procuradora_key, self.specialty_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def search_opinions(
        self,
        query,
        procuradora_key,
        extractor_version,
        *,
        year=None,
        subtype=None,
        exclude_reference_id=None,
        exclude_process_key=None,
        exclude_paths=(),
        exclude_hashes=(),
        limit=10,
        offset=0,
        specialty_id=None,
    ):
        """Search current, linked opinion pages with PostgreSQL Portuguese FTS."""
        from services.minutas_specialties import scope
        if procuradora_key != scope(specialty_id)["procuradora_chave"]:
            raise ValueError("Procuradora incompatível com a especialidade.")
        if self.specialty_id and specialty_id != self.specialty_id:
            raise ValueError("Especialidade incompatível com o acervo selecionado.")
        clauses = [
            "e.papel='parecer'",
            "e.status='PROCESSADO'",
            "e.extrator_versao=?",
            "e.procuradora_chave=?",
            "r.procuradora_chave=?",
            "e.procuradora_chave=r.procuradora_chave",
            "e.referencia_id=r.id",
            "EXISTS (SELECT 1 FROM minutas_documentos_catalogo doc WHERE doc.referencia_id=r.id "
            "AND doc.papel='parecer' AND doc.path=e.arquivo_path AND doc.nome=e.arquivo_nome "
            "AND doc.mime=e.arquivo_mime AND doc.tamanho=e.arquivo_tamanho)",
            "EXISTS (SELECT 1 FROM minutas_classificacoes mc JOIN minutas_acervo_vinculos mv "
            "ON mv.referencia_id=mc.referencia_id JOIN minutas_especialidades ms ON ms.id=mv.especialidade_id "
            "WHERE mc.referencia_id=r.id AND ms.procuradora_chave=r.procuradora_chave "
            "AND (ms.grupo='TEMATICO' OR mc.classe=ms.grupo) "
            "AND mc.estado='CLASSIFICADO' AND mv.especialidade_id=?)",
            # Validate only the recorded identifier format; search never downloads
            # Storage bytes and therefore does not attest cryptographic integrity.
            "length(e.arquivo_sha256)=64",
            "e.arquivo_sha256<>repeat('0',64)",
            "to_tsvector('portuguese',COALESCE(e.texto_integral,'')) @@ q.terms",
        ]
        with self.store.connection(read_only=True) as connection:
            if self._active(connection):
                clauses.append("r.acervo='ativo'")
        parameters = [
            query,
            query,
            extractor_version,
            procuradora_key,
            procuradora_key,
            specialty_id,
        ]
        if year is not None:
            clauses.append("r.exercicio=?")
            parameters.append(year)
        if subtype is not None:
            clauses.append("r.subtipo=?")
            parameters.append(subtype)
        if exclude_reference_id is not None:
            clauses.append("e.referencia_id<>?")
            parameters.append(exclude_reference_id)
        if exclude_process_key is not None:
            clauses.append("r.processo_chave<>?")
            parameters.append(exclude_process_key)
        for path in sorted(set(exclude_paths)):
            clauses.append("e.arquivo_path<>?")
            parameters.append(path)
        for digest in sorted(set(exclude_hashes)):
            clauses.append("e.arquivo_sha256<>?")
            parameters.append(digest)
        sql = (
            "WITH search_query AS ("
            "SELECT ?::text AS raw,websearch_to_tsquery('portuguese',?) AS terms"
            "),eligible AS ("
            "SELECT e.id,e.referencia_id,e.arquivo_nome,e.arquivo_path,"
            "e.arquivo_sha256,e.extrator_versao,e.atualizado_em,e.unidades_json,"
            "r.procuradora,r.numero_processo,r.exercicio,r.subtipo "
            "FROM minutas_extracoes e JOIN minutas_referencias r ON r.id=e.referencia_id "
            "CROSS JOIN search_query q WHERE "
            + " AND ".join(clauses)
            + ") SELECT d.referencia_id,d.id AS documento_id,d.procuradora,"
            "d.numero_processo,d.exercicio,d.subtipo,d.arquivo_nome,d.arquivo_path,"
            "d.arquivo_sha256,d.extrator_versao,d.atualizado_em,"
            "CASE WHEN COALESCE(unit->>'pagina','')~'^[0-9]+$' "
            "THEN (unit->>'pagina')::integer ELSE NULL END AS pagina,"
            "COALESCE(unit->>'ordem','') AS localizacao,"
            "ts_headline('portuguese',unit->>'texto',q.terms,"
            "'StartSel=【,StopSel=】,MaxWords=55,MinWords=25,ShortWord=3') AS trecho,"
            "ts_headline('portuguese',unit->>'texto',q.terms,"
            "'StartSel=【,StopSel=】,MaxWords=180,MinWords=80,ShortWord=3,"
            "MaxFragments=2') AS contexto,"
            "(ts_rank_cd(to_tsvector('portuguese',unit->>'texto'),q.terms)+"
            "CASE WHEN POSITION(LOWER(q.raw) IN LOWER(unit->>'texto'))>0 "
            "THEN 1.0 ELSE 0.0 END) AS relevancia "
            "FROM eligible d CROSS JOIN search_query q CROSS JOIN LATERAL "
            "jsonb_array_elements(CASE WHEN jsonb_typeof(d.unidades_json::jsonb)='array' "
            "THEN d.unidades_json::jsonb ELSE '[]'::jsonb END) unit "
            "WHERE COALESCE(unit->>'texto','')<>'' AND "
            "to_tsvector('portuguese',unit->>'texto')@@q.terms "
            "ORDER BY relevancia DESC,d.exercicio DESC,d.numero_processo,"
            "pagina NULLS LAST,d.id LIMIT ? OFFSET ?"
        )
        parameters.extend((limit + 1, offset))
        with self.store.connection(read_only=True) as connection:
            rows = connection.execute(sql, tuple(parameters)).fetchall()
        return [dict(row) for row in rows]

    def upsert_extraction(self, record):
        columns = EXTRACTION_COLUMNS.split(",")
        assignments = ",".join(
            f"{column}=excluded.{column}" for column in columns if column != "id"
        )
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO minutas_extracoes("
                + EXTRACTION_COLUMNS
                + ") VALUES("
                + ",".join("?" for _ in columns)
                + ") ON CONFLICT(arquivo_path) DO UPDATE SET "
                + assignments,
                tuple(record[column] for column in columns),
            )
        return self.get_extraction(record["arquivo_path"])

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
                connection.execute(
                    "UPDATE minutas_extracoes SET tipo=?,posicao=?,documento_id=?,"
                    "atualizado_em=? WHERE arquivo_path=(SELECT path FROM "
                    "minutas_documentos_complementares WHERE id=? AND referencia_id=?)",
                    (
                        document["tipo"],
                        document["posicao"],
                        document["id"],
                        stamp,
                        document["id"],
                        reference_id,
                    ),
                )
        return self.list_documents(reference_id)

    def delete_document(self, identifier, reference_id):
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM minutas_extracoes WHERE arquivo_path=(SELECT path FROM "
                "minutas_documentos_complementares WHERE id=? AND referencia_id=?)",
                (identifier, reference_id),
            )
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
            connection.execute(
                "UPDATE minutas_extracoes SET documento_id=NULL,papel='auditoria',"
                "tipo='Relatório da Auditoria',posicao=0,atualizado_em=? "
                "WHERE arquivo_path=?",
                (stamp, selected["path"]),
            )
            connection.execute(
                "UPDATE minutas_extracoes SET documento_id=?,papel='complementar',"
                "tipo=?,posicao=?,atualizado_em=? WHERE arquivo_path=?",
                (
                    displaced["id"],
                    displaced["tipo"],
                    displaced["posicao"],
                    stamp,
                    reference["auditoria_path"],
                ),
            )
        return self.get(reference_id), self.list_documents(reference_id)

    def replace_document(self, identifier, kind, document, actor):
        if kind not in {"auditoria", "parecer"}:
            raise ValueError("Tipo de documento inválido.")
        stamp = now()
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                f"SELECT {kind}_path FROM minutas_referencias WHERE id=?",
                (identifier,),
            ).fetchone()
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
            if previous:
                connection.execute(
                    "DELETE FROM minutas_extracoes WHERE arquivo_path=?",
                    (previous[f"{kind}_path"],),
                )
        return self.get(identifier)

    def delete(self, identifier):
        with self.store.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            result = connection.execute(
                "DELETE FROM minutas_referencias WHERE id=?", (identifier,)
            )
            if not getattr(result, "rowcount", 1):
                raise ValueError("Referência não encontrada.")
