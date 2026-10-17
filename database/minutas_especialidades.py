"""Persistência aditiva de especialidades; nenhuma migração automática."""

import json
import uuid
from database.store import now, unwrap_store
from services.minutas_specialties import (
    PREFEITURAS, PREFEITURAS_LEGACY, PROSECUTOR_KEY, STATES, canonical_specialty_id,
    fingerprint, scope,
)


REQUIRED_RELATIONS = (
    "minutas_especialidades",
    "minutas_classificacoes",
    "minutas_acervo_vinculos",
    "minutas_especialidades_eventos",
    "minutas_acervo_documentos",
    "minutas_documentos_catalogo",
)


def _first(result):
    if hasattr(result, "fetchone"):
        return result.fetchone()
    rows = result.fetchall()
    return rows[0] if rows else None


def separation_enabled(connection, backend):
    """Coluna aditiva. Ausente até o SQL manual: o aplicativo segue no comportamento atual."""
    if backend == "postgresql":
        result = connection.execute(
            "SELECT 1 AS ok FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name='minutas_referencias' "
            "AND column_name='acervo'"
        )
    else:
        result = connection.execute(
            "SELECT 1 AS ok FROM pragma_table_info('minutas_referencias') WHERE name='acervo'"
        )
    return _first(result) is not None


def active_clause(enabled, alias="r"):
    if not enabled:
        return ""
    column = f"{alias}.acervo" if alias else "acervo"
    return f" AND {column}='ativo'"


def specialty_membership_clause(column):
    """Compatibilidade temporária para vínculos legados de Prefeituras."""
    return (
        "(" + column + "=? OR (?='" + PREFEITURAS + "' AND " + column
        + " IN ('" + "','".join(PREFEITURAS_LEGACY) + "')))"
    )


def scoped_parameters(specialty):
    canonical = canonical_specialty_id(specialty)
    return (canonical, canonical)


def scoped_clause(alias="r"):
    """Documento do Procurador da operação e catálogo técnico central.

    A segunda chave é a do catálogo compartilhado (piloto ESPO), não a do acervo.
    """
    return (f"{alias}.procuradora_chave=? AND EXISTS (SELECT 1 FROM minutas_classificacoes mc "
            f"JOIN minutas_acervo_vinculos mv ON mv.referencia_id=mc.referencia_id "
            "JOIN minutas_especialidades ms ON ms.id=mv.especialidade_id "
            f"WHERE mc.referencia_id={alias}.id AND mc.estado='CLASSIFICADO' "
            f"AND ms.procuradora_chave=? AND (ms.grupo='TEMATICO' OR mc.classe=ms.grupo) "
            "AND " + specialty_membership_clause("mv.especialidade_id") + ")")


def scoped_arguments(document_key, specialty):
    from services.minutas_procuradores import CHAVE_CATALOGO

    return (document_key, CHAVE_CATALOGO, *scoped_parameters(specialty))


class MinutasSpecialtiesStore:
    def __init__(self, store, procuradora_chave=None):
        self.store = unwrap_store(store)
        if procuradora_chave:
            from services.minutas_procuradores import exigir_procurador

            self.procuradora_chave = exigir_procurador(procuradora_chave).chave

    def _escopo(self):
        from services.minutas_procuradores import chave_efetiva

        return chave_efetiva(self)

    def missing_relations(self):
        """Relações invisíveis no search_path atual. Não cria nem altera nada."""
        names = REQUIRED_RELATIONS
        with self.store.connection(read_only=True) as c:
            if getattr(self.store, "backend", "") == "postgresql":
                row = c.execute(
                    "SELECT " + ",".join(f"to_regclass('{name}') AS {name}" for name in names)
                ).fetchone()
                return [name for name in names if row[name] is None]
            found = {row["name"] for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name IN ("
                + ",".join("?" for _ in names) + ")", names).fetchall()}
        return [name for name in names if name not in found]

    def catalog(self):
        with self.store.connection(read_only=True) as c:
            rows = [dict(row) for row in c.execute(
                "SELECT * FROM minutas_especialidades WHERE procuradora_chave=? ORDER BY grupo,nome",
                (PROSECUTOR_KEY,),
            ).fetchall()]
        canonical = next((row for row in rows if row["id"] == PREFEITURAS), None)
        legacy = [row for row in rows if row["id"] in PREFEITURAS_LEGACY]
        rows = [row for row in rows if row["id"] not in PREFEITURAS_LEGACY]
        if canonical is None and legacy:
            canonical = dict(legacy[0])
            canonical.update(scope(PREFEITURAS))
            canonical["id"] = PREFEITURAS
        if canonical is not None and not any(row["id"] == PREFEITURAS for row in rows):
            rows.append(canonical)
        return sorted(rows, key=lambda row: (row["grupo"], row["nome"]))

    def classification(self, identifier):
        with self.store.connection(read_only=True) as c:
            row = c.execute("SELECT * FROM minutas_classificacoes WHERE referencia_id=?", (identifier,)).fetchone()
        return dict(row) if row else None

    def separation_enabled(self):
        with self.store.connection(read_only=True) as connection:
            return separation_enabled(connection, getattr(self.store, "backend", ""))

    def list_records(self, specialty=None, *, pending=False, query="", role=None, page=1, limit=25, legacy=False):
        if specialty: scope(specialty)
        page, limit = int(page), int(limit)
        if not 1 <= limit <= 100 or page < 1: raise ValueError("Paginação inválida.")
        chave = self._escopo()
        where = "r.procuradora_chave=?"
        args = [chave]
        if specialty:
            where = scoped_clause()
            args = list(scoped_arguments(chave, specialty))
        if pending:
            where += " AND (mc.estado IS NULL OR mc.estado<>'CLASSIFICADO')"
        if query:
            where += " AND (LOWER(r.numero_processo) LIKE ? OR LOWER(mc.entidade) LIKE ?)"
            args += ["%" + query.lower() + "%"] * 2
        if role:
            if role not in ("auditoria", "parecer", "complementar", "modelo"): raise ValueError("Finalidade inválida.")
            where += " AND EXISTS (SELECT 1 FROM minutas_documentos_catalogo dc WHERE dc.referencia_id=r.id AND dc.papel=?)"
            args.append(role)
        with self.store.connection(read_only=True) as c:
            enabled = separation_enabled(c, getattr(self.store, "backend", ""))
            if legacy:
                if not enabled:
                    return []
                where += " AND r.acervo='legado'"
            else:
                where += active_clause(enabled)
            return [dict(r) for r in c.execute(
                "SELECT r.*,mc.especialidade_id,mc.estado AS classificacao_estado,mc.classe,"
                "mc.entidade AS origem,mc.motivo,mc.revisao AS classificacao_revisao "
                "FROM minutas_referencias r LEFT JOIN minutas_classificacoes mc ON mc.referencia_id=r.id "
                "WHERE " + where + " ORDER BY r.numero_processo,r.id LIMIT ? OFFSET ?",
                (*args, limit, (page - 1) * limit),
            ).fetchall()]

    def dashboard(self):
        from services.minutas_extraction import EXTRACTOR_VERSION
        catalog = self.catalog()
        canonical_ids = tuple(row["id"] for row in catalog)
        membership_ids = (*canonical_ids, *PREFEITURAS_LEGACY)
        membership_placeholders = ",".join("?" for _ in membership_ids)
        legacy_placeholders = ",".join("?" for _ in PREFEITURAS_LEGACY)
        with self.store.connection(read_only=True) as c:
            active = active_clause(separation_enabled(c, getattr(self.store, "backend", "")))
            rows = c.execute(
                "WITH escopo AS ("
                "SELECT CASE WHEN v.especialidade_id IN (" + legacy_placeholders + ") THEN ? "
                "ELSE v.especialidade_id END AS especialidade_id,r.id AS referencia_id,d.id AS documento_id,"
                "d.papel,e.status AS extracao_status,e.extrator_versao,mc.atualizado_em "
                "FROM minutas_acervo_vinculos v "
                "JOIN minutas_classificacoes mc ON mc.referencia_id=v.referencia_id AND mc.estado='CLASSIFICADO' "
                "JOIN minutas_referencias r ON r.id=mc.referencia_id AND r.procuradora_chave=?" + active + " "
                "LEFT JOIN minutas_documentos_catalogo d ON d.referencia_id=r.id "
                "LEFT JOIN minutas_extracoes e ON e.arquivo_path=d.path AND e.referencia_id=r.id "
                "WHERE v.especialidade_id IN (" + membership_placeholders + ")"
                ") SELECT especialidade_id,COUNT(DISTINCT referencia_id) AS processos,"
                "COUNT(DISTINCT CASE WHEN papel='parecer' THEN documento_id END) AS pareceres,"
                "COUNT(DISTINCT CASE WHEN papel IN ('auditoria','complementar') THEN documento_id END) AS relatorios,"
                "COUNT(DISTINCT CASE WHEN extracao_status='PROCESSADO' AND extrator_versao=? "
                "THEN documento_id END) AS indexados,MAX(atualizado_em) AS ultima_atualizacao "
                "FROM escopo GROUP BY especialidade_id",
                (*PREFEITURAS_LEGACY, PREFEITURAS, self._escopo(), *membership_ids, EXTRACTOR_VERSION),
            ).fetchall()
            pending = c.execute(
                "SELECT COUNT(*) AS total FROM minutas_referencias r LEFT JOIN minutas_classificacoes mc "
                "ON mc.referencia_id=r.id WHERE r.procuradora_chave=? "
                "AND (mc.estado IS NULL OR mc.estado<>'CLASSIFICADO')" + active, (self._escopo(),),
            ).fetchone()["total"]
        metrics = {row["especialidade_id"]: dict(row) for row in rows}
        specialties = []
        for profile in catalog:
            row = metrics.get(profile["id"], {})
            specialties.append({
                "id": profile["id"],
                "processos": row.get("processos", 0),
                "pareceres": row.get("pareceres", 0),
                "relatorios": row.get("relatorios", 0),
                "indexados": row.get("indexados", 0),
                "ultima_atualizacao": row.get("ultima_atualizacao"),
            })
        return {"especialidades": specialties, "pendentes": pending}

    def snapshot(self):
        """Inventário explícito: três leituras; texto carregado só no dry-run administrativo."""
        with self.store.connection(read_only=True, isolation="REPEATABLE READ") as c:
            enabled = separation_enabled(c, getattr(self.store, "backend", ""))
            bare, scoped = active_clause(enabled, ""), active_clause(enabled)
            records = [dict(r) for r in c.execute(
                "SELECT * FROM minutas_referencias WHERE procuradora_chave=?" + bare + " ORDER BY id", (self._escopo(),)).fetchall()]
            documents = [dict(r) for r in c.execute(
                "SELECT d.* FROM minutas_documentos_catalogo d JOIN minutas_referencias r ON r.id=d.referencia_id "
                "WHERE r.procuradora_chave=?" + scoped + " ORDER BY d.path", (self._escopo(),)).fetchall()]
            if enabled:
                extractions = [dict(r) for r in c.execute(
                    "SELECT e.* FROM minutas_extracoes e JOIN minutas_referencias r ON r.id=e.referencia_id "
                    "WHERE e.procuradora_chave=?" + scoped + " ORDER BY e.arquivo_path", (self._escopo(),)).fetchall()]
            else:
                extractions = [dict(r) for r in c.execute(
                    "SELECT e.* FROM minutas_extracoes e WHERE e.procuradora_chave=? ORDER BY e.arquivo_path",
                    (self._escopo(),)).fetchall()]
            classifications = [dict(r) for r in c.execute(
                "SELECT mc.* FROM minutas_classificacoes mc JOIN minutas_referencias r ON r.id=mc.referencia_id "
                "WHERE r.procuradora_chave=?" + scoped + " ORDER BY mc.referencia_id", (self._escopo(),)).fetchall()]
        return {"processos": records, "documentos": documents, "extracoes": extractions, "classificacoes": classifications}

    def legacy_snapshot(self):
        """Inventário anterior ao DDL; usa somente tabelas Minutas já existentes."""
        with self.store.connection(read_only=True) as c:
            records = [dict(r) for r in c.execute("SELECT * FROM minutas_referencias WHERE procuradora_chave=? ORDER BY id",
                                                (self._escopo(),)).fetchall()]
            complements = [dict(r) for r in c.execute("SELECT d.* FROM minutas_documentos_complementares d "
                "JOIN minutas_referencias r ON r.id=d.referencia_id WHERE r.procuradora_chave=? ORDER BY d.path",
                (self._escopo(),)).fetchall()]
            extractions = [dict(r) for r in c.execute("SELECT * FROM minutas_extracoes WHERE procuradora_chave=? ORDER BY arquivo_path",
                                                    (self._escopo(),)).fetchall()]
        documents = []
        for record in records:
            for role, kind in (("auditoria", "Relatório da Auditoria"), ("parecer", "Parecer definitivo")):
                if record.get(role + "_path"):
                    documents.append({"id": str(record["id"]) + ":" + role, "referencia_id": record["id"],
                        "papel": role, "tipo": kind,
                        **{key: record[role + "_" + key] for key in ("nome", "mime", "tamanho", "path")}})
        documents.extend({"id": str(d["id"]), "referencia_id": d["referencia_id"], "papel": "complementar",
                          **{key: d[key] for key in ("tipo", "nome", "mime", "tamanho", "path")}} for d in complements)
        return {"processos": records, "documentos": sorted(documents, key=lambda d: d["path"]),
                "extracoes": extractions, "classificacoes": []}

    @staticmethod
    def _event(c, identifier, specialty, action, before, after, actor, batch=""):
        c.execute("INSERT INTO minutas_especialidades_eventos "
                  "(id,referencia_id,especialidade_id,acao,anterior_json,posterior_json,lote,criado_em,criado_por) "
                  "VALUES(?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), identifier, specialty, action,
                  json.dumps(before, ensure_ascii=False, default=str), json.dumps(after, ensure_ascii=False, default=str),
                  batch, now(), actor))

    def _classify(self, c, item, actor, *, automatic=False, batch=""):
        if getattr(self.store, "backend", "") == "postgresql":
            c.execute("SELECT id FROM minutas_referencias WHERE id=? FOR UPDATE", (item["referencia_id"],))
        row = c.execute("SELECT procuradora_chave FROM minutas_referencias WHERE id=?", (item["referencia_id"],)).fetchone()
        if not row or row["procuradora_chave"] != self._escopo(): raise ValueError("Processo fora do escopo autorizado.")
        previous = c.execute("SELECT * FROM minutas_classificacoes WHERE referencia_id=?", (item["referencia_id"],)).fetchone()
        previous = dict(previous) if previous else None
        if previous:
            previous["referencia_id"] = str(previous["referencia_id"])
        links = [dict(r) for r in c.execute("SELECT * FROM minutas_acervo_vinculos WHERE referencia_id=?",
                                          (item["referencia_id"],)).fetchall()]
        if automatic and previous and previous["estado"] == "CLASSIFICADO": return False
        specialty = item.get("especialidade_id")
        if specialty: scope(specialty)
        after = {"referencia_id": item["referencia_id"], "especialidade_id": specialty,
                 "estado": item["estado"], "classe": item.get("classe", ""),
                 "modalidade": item.get("modalidade", ""), "perfil": item.get("perfil", ""),
                 "entidade": item.get("entidade", ""), "temas_json": json.dumps(item.get("temas", []), ensure_ascii=False),
                 "evidencia_json": json.dumps(item.get("evidencia", []), ensure_ascii=False),
                 "motivo": item.get("motivo", ""), "origem": "AUTOMATICA" if automatic else "MANUAL"}
        if previous and all(previous.get(k) == v for k, v in after.items()): return False
        after.update(revisao=(previous or {}).get("revisao", 0) + 1, atualizado_em=now(), atualizado_por=actor)
        columns = list(after)
        c.execute("INSERT INTO minutas_classificacoes (" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ") "
                  "ON CONFLICT(referencia_id) DO UPDATE SET " + ",".join(f"{k}=excluded.{k}" for k in columns if k != "referencia_id"), tuple(after.values()))
        # Corrigir a classificação invalida vínculos secundários; devem ser reconfirmados.
        c.execute("DELETE FROM minutas_acervo_vinculos WHERE referencia_id=?", (item["referencia_id"],))
        if after["estado"] == "CLASSIFICADO":
            c.execute("INSERT INTO minutas_acervo_vinculos(referencia_id,especialidade_id,justificativa) VALUES(?,?,?)",
                      (item["referencia_id"], specialty, after["motivo"]))
        for affected in {specialty, *(link["especialidade_id"] for link in links)} - {None}:
            c.execute("UPDATE minutas_especialidades SET revisao=revisao+1 WHERE id=?", (affected,))
        if previous: previous["vinculos"] = links
        self._event(c, item["referencia_id"], specialty, "CLASSIFICAR", previous, after, actor, batch)
        return True

    def classify(self, item, actor):
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            return self._classify(c, item, actor)

    def apply_plan(self, plan, actor, backup):
        if not backup.get("restauracao_verificada") or not backup.get("identificador"):
            raise ValueError("Informe backup recuperável com restauração verificada.")
        if plan["contagens"]["extracoes_sem_vinculo"]:
            raise ValueError("Há extrações sem vínculo válido; confira a integridade antes de aplicar.")
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            # Lock prevents replacement/reclassification between comparison and write.
            if getattr(self.store, "backend", "") == "postgresql":
                c.execute("LOCK TABLE minutas_referencias,minutas_documentos_complementares,minutas_acervo_documentos,"
                          "minutas_extracoes,minutas_classificacoes IN SHARE ROW EXCLUSIVE MODE")
            enabled = separation_enabled(c, getattr(self.store, "backend", ""))
            bare, scoped = active_clause(enabled, ""), active_clause(enabled)
            rows = [dict(r) for r in c.execute("SELECT * FROM minutas_referencias WHERE procuradora_chave=?" + bare + " ORDER BY id", (self._escopo(),)).fetchall()]
            if fingerprint(rows) != plan["registros_hash"]: raise ValueError("Inventário alterado; simule novamente.")
            docs = [dict(r) for r in c.execute("SELECT d.* FROM minutas_documentos_catalogo d JOIN minutas_referencias r ON r.id=d.referencia_id WHERE r.procuradora_chave=?" + scoped + " ORDER BY d.path", (self._escopo(),)).fetchall()]
            if enabled:
                ext = [dict(r) for r in c.execute(
                    "SELECT e.* FROM minutas_extracoes e JOIN minutas_referencias r ON r.id=e.referencia_id "
                    "WHERE e.procuradora_chave=?" + scoped + " ORDER BY e.arquivo_path", (self._escopo(),)).fetchall()]
            else:
                ext = [dict(r) for r in c.execute("SELECT * FROM minutas_extracoes WHERE procuradora_chave=? ORDER BY arquivo_path", (self._escopo(),)).fetchall()]
            if fingerprint(docs) != plan["documentos_hash"] or fingerprint(ext) != plan["extracoes_hash"]:
                raise ValueError("Documentos ou extrações alterados; simule novamente.")
            changed = sum(self._classify(c, item, actor, automatic=True, batch=plan["lote"]) for item in plan["itens"])
            self._event(c, None, None, "MIGRAR", plan["contagens"], {"alterados": changed, "backup": backup,
                        "contagens_reconciliadas": plan["contagens"]}, actor, plan["lote"])
        return changed

    def update_state(self, specialty, values, actor):
        scope(specialty)
        if values["estado"] not in STATES: raise ValueError("Estado inválido.")
        if values["estado"] == "Validada" and not (values.get("revisao_humana") and values.get("resultados_testes")):
            raise ValueError("Validação exige revisão humana e resultados de testes.")
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            before = dict(c.execute("SELECT * FROM minutas_especialidades WHERE id=?", (specialty,)).fetchone())
            c.execute("UPDATE minutas_especialidades SET estado=?,casos_testados=?,pendencias=?,resultados_testes=?,"
                      "ultima_validacao=?,revisao=revisao+1,atualizado_em=?,atualizado_por=? WHERE id=?",
                      (values["estado"], int(values.get("casos_testados", 0)), values.get("pendencias", ""),
                       values.get("resultados_testes", ""), now() if values["estado"] == "Validada" else before["ultima_validacao"], now(), actor, specialty))
            self._event(c, None, specialty, "EVOLUCAO", before, values, actor)

    def restore_classification(self, event_id, actor):
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            event = c.execute("SELECT * FROM minutas_especialidades_eventos WHERE id=? AND acao='CLASSIFICAR'", (event_id,)).fetchone()
            if not event: raise ValueError("Evento de classificação não encontrado.")
            after = json.loads(event["posterior_json"])
            current = c.execute("SELECT * FROM minutas_classificacoes WHERE referencia_id=?", (event["referencia_id"],)).fetchone()
            if not current or current["revisao"] != after["revisao"]: raise ValueError("Há classificação posterior; restauração recusada.")
            before = json.loads(event["anterior_json"]) or {"referencia_id": event["referencia_id"], "estado": "NAO_CLASSIFICADO"}
            before["temas"] = json.loads(before.get("temas_json", "[]"))
            before["evidencia"] = json.loads(before.get("evidencia_json", "[]"))
            before["motivo"] = "Restauração do evento " + event_id
            self._classify(c, before, actor)
            for link in before.get("vinculos", []):
                c.execute("INSERT INTO minutas_acervo_vinculos(referencia_id,especialidade_id,justificativa) "
                    "VALUES(?,?,?) ON CONFLICT(referencia_id,especialidade_id) DO NOTHING",
                    (event["referencia_id"], link["especialidade_id"], link["justificativa"]))
                c.execute("UPDATE minutas_especialidades SET revisao=revisao+1 WHERE id=?", (link["especialidade_id"],))

    def link(self, identifier, specialty, reason, actor):
        """Explicit secondary membership; no copying of documents or extractions."""
        selected = scope(specialty)
        if not reason.strip(): raise ValueError("Justifique a pertinência jurídica do vínculo.")
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT mc.* FROM minutas_classificacoes mc JOIN minutas_referencias r "
                "ON r.id=mc.referencia_id WHERE r.id=? AND r.procuradora_chave=? AND mc.estado='CLASSIFICADO'",
                (identifier, self._escopo())).fetchone()
            if not row: raise ValueError("Processo não classificado no escopo autorizado.")
            if selected["grupo"] != "TEMATICO" and row["classe"] != selected["grupo"]:
                raise ValueError("Vínculo incompatível com a classe efetiva.")
            existing = c.execute("SELECT * FROM minutas_acervo_vinculos WHERE referencia_id=? AND especialidade_id=?",
                                 (identifier, specialty)).fetchone()
            if existing: return False
            c.execute("INSERT INTO minutas_acervo_vinculos VALUES(?,?,?)", (identifier, specialty, reason))
            c.execute("UPDATE minutas_especialidades SET revisao=revisao+1 WHERE id=?", (specialty,))
            c.execute("UPDATE minutas_classificacoes SET revisao=revisao+1,atualizado_em=?,atualizado_por=? WHERE referencia_id=?",
                      (now(), actor, identifier))
            self._event(c, identifier, specialty, "VINCULAR", None, {"justificativa": reason}, actor)
        return True

    def events(self, identifier):
        with self.store.connection(read_only=True) as c:
            return [dict(r) for r in c.execute("SELECT e.* FROM minutas_especialidades_eventos e "
                "JOIN minutas_referencias r ON r.id=e.referencia_id WHERE e.referencia_id=? AND r.procuradora_chave=? "
                "ORDER BY e.criado_em DESC,e.id DESC LIMIT 20", (identifier, self._escopo())).fetchall()]
