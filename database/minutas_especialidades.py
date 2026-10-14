"""Persistência aditiva de especialidades; nenhuma migração automática."""

import json
import uuid
from database.store import now, unwrap_store
from services.minutas_specialties import PROSECUTOR_KEY, STATES, scope, fingerprint


def scoped_clause(alias="r"):
    return (f"{alias}.procuradora_chave=? AND EXISTS (SELECT 1 FROM minutas_classificacoes mc "
            f"JOIN minutas_acervo_vinculos mv ON mv.referencia_id=mc.referencia_id "
            "JOIN minutas_especialidades ms ON ms.id=mv.especialidade_id "
            f"WHERE mc.referencia_id={alias}.id AND mc.estado='CLASSIFICADO' "
            f"AND ms.procuradora_chave={alias}.procuradora_chave AND (ms.grupo='TEMATICO' OR mc.classe=ms.grupo) "
            "AND mv.especialidade_id=?)")


class MinutasSpecialtiesStore:
    def __init__(self, store):
        self.store = unwrap_store(store)

    def catalog(self):
        with self.store.connection(read_only=True) as c:
            return [dict(row) for row in c.execute(
                "SELECT * FROM minutas_especialidades WHERE procuradora_chave=? ORDER BY grupo,nome",
                (PROSECUTOR_KEY,),
            ).fetchall()]

    def classification(self, identifier):
        with self.store.connection(read_only=True) as c:
            row = c.execute("SELECT * FROM minutas_classificacoes WHERE referencia_id=?", (identifier,)).fetchone()
        return dict(row) if row else None

    def list_records(self, specialty=None, *, pending=False, query="", role=None, page=1, limit=25):
        if specialty: scope(specialty)
        page, limit = int(page), int(limit)
        if not 1 <= limit <= 100 or page < 1: raise ValueError("Paginação inválida.")
        where = "r.procuradora_chave=?"
        args = [PROSECUTOR_KEY]
        if specialty:
            where = scoped_clause()
            args.append(specialty)
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
            return [dict(r) for r in c.execute(
                "SELECT r.*,mc.especialidade_id,mc.estado AS classificacao_estado,mc.classe,"
                "mc.entidade AS origem,mc.motivo,mc.revisao AS classificacao_revisao "
                "FROM minutas_referencias r LEFT JOIN minutas_classificacoes mc ON mc.referencia_id=r.id "
                "WHERE " + where + " ORDER BY r.numero_processo,r.id LIMIT ? OFFSET ?",
                (*args, limit, (page - 1) * limit),
            ).fetchall()]

    def dashboard(self):
        from services.minutas_extraction import EXTRACTOR_VERSION
        with self.store.connection(read_only=True) as c:
            rows = c.execute(
                "SELECT s.id,COUNT(DISTINCT r.id) AS processos,"
                "COUNT(DISTINCT CASE WHEN d.papel='parecer' THEN d.path END) AS pareceres,"
                "COUNT(DISTINCT CASE WHEN d.papel IN ('auditoria','complementar') THEN d.path END) AS relatorios,"
                "COUNT(DISTINCT CASE WHEN e.status='PROCESSADO' AND e.extrator_versao=? "
                "THEN d.path END) AS indexados,MAX(mc.atualizado_em) AS ultima_atualizacao "
                "FROM minutas_especialidades s LEFT JOIN minutas_acervo_vinculos v ON v.especialidade_id=s.id "
                "LEFT JOIN minutas_classificacoes mc ON mc.referencia_id=v.referencia_id AND mc.estado='CLASSIFICADO' "
                "LEFT JOIN minutas_referencias r ON r.id=mc.referencia_id AND r.procuradora_chave=s.procuradora_chave "
                "LEFT JOIN minutas_documentos_catalogo d ON d.referencia_id=r.id "
                "LEFT JOIN minutas_extracoes e ON e.arquivo_path=d.path AND e.referencia_id=r.id "
                "WHERE s.procuradora_chave=? GROUP BY s.id", (EXTRACTOR_VERSION, PROSECUTOR_KEY),
            ).fetchall()
            pending = c.execute(
                "SELECT COUNT(*) AS total FROM minutas_referencias r LEFT JOIN minutas_classificacoes mc "
                "ON mc.referencia_id=r.id WHERE r.procuradora_chave=? "
                "AND (mc.estado IS NULL OR mc.estado<>'CLASSIFICADO')", (PROSECUTOR_KEY,),
            ).fetchone()["total"]
        return {"especialidades": [dict(r) for r in rows], "pendentes": pending}

    def snapshot(self):
        """Inventário explícito: três leituras; texto carregado só no dry-run administrativo."""
        with self.store.connection(read_only=True, isolation="REPEATABLE READ") as c:
            records = [dict(r) for r in c.execute(
                "SELECT * FROM minutas_referencias WHERE procuradora_chave=? ORDER BY id", (PROSECUTOR_KEY,)).fetchall()]
            documents = [dict(r) for r in c.execute(
                "SELECT d.* FROM minutas_documentos_catalogo d JOIN minutas_referencias r ON r.id=d.referencia_id "
                "WHERE r.procuradora_chave=? ORDER BY d.path", (PROSECUTOR_KEY,)).fetchall()]
            extractions = [dict(r) for r in c.execute(
                "SELECT e.* FROM minutas_extracoes e WHERE e.procuradora_chave=? ORDER BY e.arquivo_path",
                (PROSECUTOR_KEY,)).fetchall()]
            classifications = [dict(r) for r in c.execute(
                "SELECT mc.* FROM minutas_classificacoes mc JOIN minutas_referencias r ON r.id=mc.referencia_id "
                "WHERE r.procuradora_chave=? ORDER BY mc.referencia_id", (PROSECUTOR_KEY,)).fetchall()]
        return {"processos": records, "documentos": documents, "extracoes": extractions, "classificacoes": classifications}

    def legacy_snapshot(self):
        """Inventário anterior ao DDL; usa somente tabelas Minutas já existentes."""
        with self.store.connection(read_only=True) as c:
            records = [dict(r) for r in c.execute("SELECT * FROM minutas_referencias WHERE procuradora_chave=? ORDER BY id",
                                                (PROSECUTOR_KEY,)).fetchall()]
            complements = [dict(r) for r in c.execute("SELECT d.* FROM minutas_documentos_complementares d "
                "JOIN minutas_referencias r ON r.id=d.referencia_id WHERE r.procuradora_chave=? ORDER BY d.path",
                (PROSECUTOR_KEY,)).fetchall()]
            extractions = [dict(r) for r in c.execute("SELECT * FROM minutas_extracoes WHERE procuradora_chave=? ORDER BY arquivo_path",
                                                    (PROSECUTOR_KEY,)).fetchall()]
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
        if not row or row["procuradora_chave"] != PROSECUTOR_KEY: raise ValueError("Processo fora do escopo autorizado.")
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
            rows = [dict(r) for r in c.execute("SELECT * FROM minutas_referencias WHERE procuradora_chave=? ORDER BY id", (PROSECUTOR_KEY,)).fetchall()]
            if fingerprint(rows) != plan["registros_hash"]: raise ValueError("Inventário alterado; simule novamente.")
            docs = [dict(r) for r in c.execute("SELECT d.* FROM minutas_documentos_catalogo d JOIN minutas_referencias r ON r.id=d.referencia_id WHERE r.procuradora_chave=? ORDER BY d.path", (PROSECUTOR_KEY,)).fetchall()]
            ext = [dict(r) for r in c.execute("SELECT * FROM minutas_extracoes WHERE procuradora_chave=? ORDER BY arquivo_path", (PROSECUTOR_KEY,)).fetchall()]
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
                (identifier, PROSECUTOR_KEY)).fetchone()
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
                "ORDER BY e.criado_em DESC,e.id DESC LIMIT 20", (identifier, PROSECUTOR_KEY)).fetchall()]
