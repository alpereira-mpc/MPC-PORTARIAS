"""Additive correspondence repository using only the public Store connection API.

The existing PostgreSQL connection holds a schema advisory transaction lock;
SQLite BEGIN IMMEDIATE provides the equivalent serialized writer transaction.
"""

from datetime import date, datetime, timedelta
import json
import re
import uuid
from database.store import now, encode, unwrap_store
from services.oficios import (
    SERIES,
    BASELINES,
    SENT,
    RECEIVED,
    CLOSED,
    normalized,
    validate,
    validate_upload,
)

COLUMNS = (
    "id,direcao,serie,ano,numero,status,data,prazo,membro_id,assunto,destinatario,"
    "numero_externo,responde_a,payload,criada,atualizada,data_envio,cancelada,"
    "aguarda_resposta,data_esperada_resposta,prazo_resposta_quantidade,"
    "prazo_resposta_tipo,prazo_resposta_inicio,prazo_resposta_manual,"
    "prazo_resposta_motivo_ajuste"
)


class OficiosStore:
    def __init__(self, store):
        self.store = unwrap_store(store)
        self._series = None
        self.initialize()

    def initialize(self):
        if getattr(self, "_schema_ready", False):
            return
        self._series = None
        # Read-only fast path on subsequent reruns; no DDL/writer lock after migration.
        with self.store.connection(read_only=True) as c:
            if c.execute(
                "SELECT COUNT(*) FROM configuracoes "
                "WHERE chave IN ('oficios_schema_v2','oficios_schema_v3','oficios_schema_v4')"
            ).fetchone()[0] == 3:
                self._schema_ready = True
                return
        binary = "BYTEA" if self.store.backend == "postgresql" else "BLOB"
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            for statement in [
                "CREATE TABLE IF NOT EXISTS oficio_series (sigla TEXT PRIMARY KEY, membro_id INTEGER NOT NULL UNIQUE REFERENCES procuradores(id), modelo TEXT NOT NULL, cabecalho TEXT NOT NULL, digitos INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS oficio_sequencias (serie TEXT NOT NULL REFERENCES oficio_series(sigla), ano INTEGER NOT NULL CHECK(ano BETWEEN 1000 AND 9999), proximo INTEGER NOT NULL CHECK(proximo>0), confirmada TEXT NOT NULL, PRIMARY KEY(serie,ano))",
                "CREATE TABLE IF NOT EXISTS oficios (id TEXT PRIMARY KEY, direcao TEXT NOT NULL CHECK(direcao IN ('ENVIADO','RECEBIDO')), serie TEXT REFERENCES oficio_series(sigla), ano INTEGER NOT NULL, numero INTEGER CHECK(numero>0), status TEXT NOT NULL, data TEXT NOT NULL, prazo TEXT, membro_id INTEGER REFERENCES procuradores(id), assunto TEXT NOT NULL, destinatario TEXT NOT NULL, numero_externo TEXT NOT NULL, responde_a TEXT REFERENCES oficios(id), payload TEXT NOT NULL, criada TEXT NOT NULL, atualizada TEXT NOT NULL, data_envio TEXT, cancelada TEXT, aguarda_resposta INTEGER NOT NULL DEFAULT 0 CHECK(aguarda_resposta IN (0,1)), data_esperada_resposta TEXT, prazo_resposta_quantidade INTEGER, prazo_resposta_tipo TEXT, prazo_resposta_inicio TEXT, prazo_resposta_manual INTEGER NOT NULL DEFAULT 0, prazo_resposta_motivo_ajuste TEXT, UNIQUE(serie,ano,numero), CHECK(numero IS NULL OR (direcao='ENVIADO' AND serie IS NOT NULL AND status!='Rascunho')))",
                "CREATE TABLE IF NOT EXISTS oficio_destinatarios (oficio_id TEXT NOT NULL REFERENCES oficios(id) ON DELETE CASCADE, membro_id INTEGER NOT NULL REFERENCES procuradores(id), PRIMARY KEY(oficio_id,membro_id))",
                f"CREATE TABLE IF NOT EXISTS oficio_arquivos (id TEXT PRIMARY KEY, oficio_id TEXT NOT NULL REFERENCES oficios(id) ON DELETE CASCADE, nome TEXT NOT NULL, tipo TEXT NOT NULL, tamanho INTEGER NOT NULL, incluida TEXT NOT NULL, conteudo {binary} NOT NULL)",
                "CREATE TABLE IF NOT EXISTS oficio_movimentacoes (id TEXT PRIMARY KEY, oficio_id TEXT NOT NULL REFERENCES oficios(id) ON DELETE CASCADE, instante TEXT NOT NULL, anterior TEXT NOT NULL, novo TEXT NOT NULL, observacao TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS oficio_data_idx ON oficios(direcao,data DESC,id)",
                "CREATE INDEX IF NOT EXISTS oficio_prazo_idx ON oficios(status,prazo)",
                "CREATE INDEX IF NOT EXISTS oficio_arquivo_idx ON oficio_arquivos(oficio_id)",
                "CREATE INDEX IF NOT EXISTS oficio_movimento_idx ON oficio_movimentacoes(oficio_id,instante)",
                "CREATE INDEX IF NOT EXISTS oficio_membro_idx ON oficio_destinatarios(membro_id,oficio_id)",
                "CREATE INDEX IF NOT EXISTS oficio_resposta_idx ON oficios(responde_a)",
                "CREATE TABLE IF NOT EXISTS oficio_numeros_liberados (serie TEXT NOT NULL REFERENCES oficio_series(sigla), ano INTEGER NOT NULL, numero INTEGER NOT NULL CHECK(numero>0), PRIMARY KEY(serie,ano,numero))",
                f"CREATE TABLE IF NOT EXISTS oficio_quarentena (id TEXT PRIMARY KEY, instante TEXT NOT NULL, motivo TEXT NOT NULL, dados TEXT NOT NULL, arquivos {binary} NOT NULL)",
            ]:
                c.execute(statement)
            if self.store.backend == "postgresql":
                columns = {
                    row[0]
                    for row in c.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND table_name='oficios'"
                    )
                }
            else:
                columns = {row[1] for row in c.execute("PRAGMA table_info(oficios)")}
            if "aguarda_resposta" not in columns:
                c.execute(
                    "ALTER TABLE oficios ADD COLUMN aguarda_resposta INTEGER NOT NULL DEFAULT 0"
                )
            if "data_esperada_resposta" not in columns:
                c.execute("ALTER TABLE oficios ADD COLUMN data_esperada_resposta TEXT")
            for name, definition in (
                ("prazo_resposta_quantidade", "INTEGER"),
                ("prazo_resposta_tipo", "TEXT"),
                ("prazo_resposta_inicio", "TEXT"),
                ("prazo_resposta_manual", "INTEGER NOT NULL DEFAULT 0"),
                ("prazo_resposta_motivo_ajuste", "TEXT"),
            ):
                if name not in columns:
                    c.execute(f"ALTER TABLE oficios ADD COLUMN {name} {definition}")
            c.execute("CREATE INDEX IF NOT EXISTS oficio_resposta_prazo_idx ON oficios(aguarda_resposta,data_esperada_resposta,status)")
            if self.store.backend == "postgresql":
                file_columns = {
                    row[0]
                    for row in c.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND table_name='oficio_arquivos'"
                    )
                }
            else:
                file_columns = {
                    row[1] for row in c.execute("PRAGMA table_info(oficio_arquivos)")
                }
            if "papel" not in file_columns:
                c.execute(
                    "ALTER TABLE oficio_arquivos ADD COLUMN papel TEXT NOT NULL DEFAULT 'DOCUMENTO'"
                )
            if "nome_original" not in file_columns:
                c.execute("ALTER TABLE oficio_arquivos ADD COLUMN nome_original TEXT")
            people = [dict(r) for r in c.execute("SELECT id,nome FROM procuradores")]
            for name, (sigla, modelo, heading, digits) in SERIES.items():
                matches = [
                    p for p in people if normalized(p["nome"]) == normalized(name)
                ]
                if len(matches) == 1:
                    c.execute(
                        "INSERT INTO oficio_series VALUES(?,?,?,?,?) ON CONFLICT DO NOTHING",
                        (sigla, matches[0]["id"], modelo, heading, digits),
                    )
                    c.execute(
                        "UPDATE oficio_series SET modelo=?,cabecalho=?,digitos=? WHERE sigla=? AND modelo='' AND cabecalho='' AND NOT EXISTS (SELECT 1 FROM oficios WHERE serie=? AND numero IS NOT NULL)",
                        (modelo, heading, digits, sigla, sigla),
                    )
            c.execute(
                "INSERT INTO configuracoes VALUES('oficios_schema_v1','1') ON CONFLICT DO NOTHING"
            )
            c.execute(
                "INSERT INTO configuracoes VALUES('oficios_schema_v2','1') ON CONFLICT DO NOTHING"
            )
            c.execute(
                "INSERT INTO configuracoes VALUES('oficios_schema_v3','1') ON CONFLICT DO NOTHING"
            )
            c.execute(
                "INSERT INTO configuracoes VALUES('oficios_schema_v4','1') ON CONFLICT DO NOTHING"
            )
        self._schema_ready = True

    def series(self):
        if self._series is not None:
            return self._series
        with self.store.connection(read_only=True) as c:
            self._series = [
                dict(r)
                for r in c.execute(
                    "SELECT s.*,p.nome FROM oficio_series s JOIN procuradores p ON p.id=s.membro_id ORDER BY s.sigla"
                )
            ]
            return self._series

    def configure_series(self, member, sigla, model, heading, digits, confirmed=False):
        if not confirmed:
            raise ValueError("Confirme o padrão institucional antes de salvar.")
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9-]{1,15}", sigla)
            or model not in ("PROGE", "BTLC")
            or not 1 <= digits <= 6
        ):
            raise ValueError("Série/modelo inválido.")
        if "{numero}" not in heading or "{ano}" not in heading or len(heading) > 160:
            raise ValueError("Use {numero} e {ano} no cabeçalho.")
        try:
            heading.format(numero="001", ano=2026)
        except (ValueError, KeyError, IndexError) as exc:
            raise ValueError("Cabeçalho inválido.") from exc
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = c.execute(
                "SELECT sigla FROM oficio_series WHERE membro_id=?", (member,)
            ).fetchone()
            if previous and previous["sigla"] != sigla:
                raise ValueError("Uma série vinculada não pode ser renomeada.")
            if c.execute(
                "SELECT id FROM oficios WHERE serie=? AND numero IS NOT NULL LIMIT 1",
                (sigla,),
            ).fetchone():
                raise ValueError("O padrão de uma série já utilizada é imutável.")
            c.execute(
                "INSERT INTO oficio_series VALUES(?,?,?,?,?) ON CONFLICT(sigla) DO UPDATE SET modelo=excluded.modelo,cabecalho=excluded.cabecalho,digitos=excluded.digitos WHERE oficio_series.membro_id=excluded.membro_id",
                (sigla, member, model, heading, digits),
            )
            actual = c.execute(
                "SELECT membro_id FROM oficio_series WHERE sigla=?", (sigla,)
            ).fetchone()
            if actual["membro_id"] != member:
                raise ValueError("Série já vinculada a outro membro.")
            self.store.event(
                c, "oficio_configurar_serie", {"serie": sigla, "membro": member}
            )
        self._series = None

    def sequence(self, series, year):
        with self.store.connection(read_only=True) as c:
            row = c.execute(
                "SELECT proximo FROM oficio_sequencias WHERE serie=? AND ano=?",
                (series, year),
            ).fetchone()
            released = c.execute(
                "SELECT MIN(numero) FROM oficio_numeros_liberados WHERE serie=? AND ano=?",
                (series, year),
            ).fetchone()[0]
            return {
                "proximo": released
                or (row["proximo"] if row else BASELINES.get((series, year), 1)),
                "confirmada": bool(row),
            }

    def confirm_sequence(self, series, year, next_number, confirmed=False):
        if (
            not confirmed
            or not 1000 <= year <= 9999
            or not isinstance(next_number, int)
            or next_number < 1
        ):
            raise ValueError("Confirme ano e próximo número válido.")
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute(
                "SELECT proximo FROM oficio_sequencias WHERE serie=? AND ano=?",
                (series, year),
            ).fetchone()
            maximum = (
                c.execute(
                    "SELECT MAX(numero) FROM oficios WHERE serie=? AND ano=?",
                    (series, year),
                ).fetchone()[0]
                or 0
            )
            if next_number < max(
                maximum + 1,
                old["proximo"] if old else 1,
                BASELINES.get((series, year), 1),
            ):
                raise ValueError(
                    "A sequência não pode retroceder abaixo dos números conhecidos."
                )
            c.execute(
                "INSERT INTO oficio_sequencias VALUES(?,?,?,?) ON CONFLICT(serie,ano) DO UPDATE SET proximo=excluded.proximo,confirmada=excluded.confirmada",
                (series, year, next_number, now()),
            )
            self.store.event(
                c,
                "oficio_confirmar_sequencia",
                {"serie": series, "ano": year, "proximo": next_number},
            )

    def _movement(self, c, identifier, previous, status, note=""):
        stamp = now()
        last = c.execute(
            "SELECT MAX(instante) FROM oficio_movimentacoes WHERE oficio_id=?",
            (identifier,),
        ).fetchone()[0]
        if last and stamp <= last:
            stamp = (
                datetime.fromisoformat(last) + timedelta(microseconds=1)
            ).isoformat()
        c.execute(
            "INSERT INTO oficio_movimentacoes VALUES(?,?,?,?,?,?)",
            (uuid.uuid4().hex, identifier, stamp, previous, status, note),
        )

    def _get(self, c, identifier):
        row = c.execute(
            "SELECT " + COLUMNS + " FROM oficios WHERE id=?", (identifier,)
        ).fetchone()
        if not row:
            raise ValueError("Ofício não encontrado.")
        return {
            **json.loads(row["payload"]),
            **{k: row[k] for k in row.keys() if k != "payload"},
            "aguarda_resposta": bool(row["aguarda_resposta"]),
        }

    def get(self, identifier):
        with self.store.connection(read_only=True) as c:
            return self._get(c, identifier)

    def save(self, record, identifier=None, uploads=()):
        validate(record)
        waiting = bool(record.get("aguarda_resposta"))
        tracking = {
            "aguarda_resposta": 1 if waiting else 0,
            "data_esperada_resposta": record.get("data_esperada_resposta") if waiting else None,
            "prazo_resposta_quantidade": record.get("prazo_resposta_quantidade") if waiting else None,
            "prazo_resposta_tipo": record.get("prazo_resposta_tipo") if waiting else None,
            "prazo_resposta_inicio": record.get("prazo_resposta_inicio") if waiting else None,
            "prazo_resposta_manual": 1 if waiting and record.get("prazo_resposta_manual") else 0,
            "prazo_resposta_motivo_ajuste": record.get("prazo_resposta_motivo_ajuste", "").strip() if waiting and record.get("prazo_resposta_manual") else None,
        }
        if waiting:
            if not tracking["data_esperada_resposta"]:
                raise ValueError("Informe o vencimento para acompanhamento da resposta.")
            date.fromisoformat(tracking["data_esperada_resposta"])
            if tracking["prazo_resposta_quantidade"] is not None:
                if not isinstance(tracking["prazo_resposta_quantidade"], int) or tracking["prazo_resposta_quantidade"] < 1:
                    raise ValueError("O prazo deve ser um número inteiro positivo.")
                if tracking["prazo_resposta_tipo"] not in ("DIAS_UTEIS", "DIAS_CORRIDOS") or not tracking["prazo_resposta_inicio"]:
                    raise ValueError("Informe o tipo e o início da contagem.")
            if tracking["prazo_resposta_manual"] and not tracking["prazo_resposta_motivo_ajuste"]:
                raise ValueError("Informe o motivo do ajuste manual do vencimento.")
        files = [
            (*validate_upload(name, content), content) for name, content in uploads
        ]
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = self._get(c, identifier) if identifier else None
            if previous and record["direcao"] != previous["direcao"]:
                raise ValueError("Não é permitido alterar a direção do registro.")
            if previous and (
                previous["numero"] is not None
                or previous["direcao"] != "ENVIADO"
                or previous["status"] != "Rascunho"
            ):
                raise ValueError("Somente rascunhos podem ser editados.")
            identifier = identifier or uuid.uuid4().hex
            direction = record["direcao"]
            status = (
                "Rascunho"
                if direction == "ENVIADO"
                else record.get("status", "Recebido")
            )
            if status not in (SENT if direction == "ENVIADO" else RECEIVED):
                raise ValueError("Status inválido.")
            members = (
                [record.get("membro_id")]
                if direction == "ENVIADO"
                else record["membros"]
            )
            active = {
                r["id"] for r in c.execute("SELECT id FROM procuradores WHERE ativo=1")
            }
            if not set(members) <= active:
                raise ValueError("Selecione membros ativos.")
            series = (
                c.execute(
                    "SELECT sigla FROM oficio_series WHERE membro_id=?",
                    (record.get("membro_id"),),
                ).fetchone()
                if direction == "ENVIADO"
                else None
            )
            related = record.get("responde_a") or None
            if related and self._get(c, related)["direcao"] != "RECEBIDO":
                raise ValueError("A resposta deve apontar para um recebido.")
            stamp = now()
            values = (
                identifier,
                direction,
                series["sigla"] if series else None,
                int(record["data"][:4]),
                None,
                status,
                record["data"],
                record.get("prazo") or None,
                record.get("membro_id"),
                record["assunto"],
                record.get("destinatario", ""),
                record.get("numero_externo", ""),
                related,
                encode(record),
                previous["criada"] if previous else stamp,
                stamp,
                None,
                None,
            )
            if previous:
                c.execute(
                    "UPDATE oficios SET serie=?,ano=?,data=?,prazo=?,membro_id=?,assunto=?,destinatario=?,responde_a=?,payload=?,atualizada=? WHERE id=?",
                    (
                        values[2],
                        values[3],
                        values[6],
                        values[7],
                        values[8],
                        values[9],
                        values[10],
                        related,
                        encode(record),
                        stamp,
                        identifier,
                    ),
                )
                c.execute(
                    "UPDATE oficios SET aguarda_resposta=?,data_esperada_resposta=?,"
                    "prazo_resposta_quantidade=?,prazo_resposta_tipo=?,prazo_resposta_inicio=?,"
                    "prazo_resposta_manual=?,prazo_resposta_motivo_ajuste=? WHERE id=?",
                    (*tracking.values(), identifier),
                )
                c.execute(
                    "DELETE FROM oficio_destinatarios WHERE oficio_id=?", (identifier,)
                )
            else:
                c.execute(
                    "INSERT INTO oficios(id,direcao,serie,ano,numero,status,data,prazo,"
                    "membro_id,assunto,destinatario,numero_externo,responde_a,payload,"
                    "criada,atualizada,data_envio,cancelada,aguarda_resposta,data_esperada_resposta,"
                    "prazo_resposta_quantidade,prazo_resposta_tipo,prazo_resposta_inicio,"
                    "prazo_resposta_manual,prazo_resposta_motivo_ajuste) VALUES("
                    + ",".join("?" for _ in (*values, *tracking.values()))
                    + ")",
                    (*values, *tracking.values()),
                )
            for member in set(members):
                c.execute(
                    "INSERT INTO oficio_destinatarios VALUES(?,?)", (identifier, member)
                )
            for name, mime, content in files:
                self._file(c, identifier, name, mime, content)
            self._movement(
                c,
                identifier,
                previous["status"] if previous else "",
                status,
                "Rascunho atualizado" if previous else "Registro criado",
            )
            return identifier

    def edit_finalized(self, identifier, changes, *, administrator=False, actor_email=""):
        """Administratively update safe registration fields without touching files.

        Official identifiers, numbering and the emitted document are deliberately
        outside this operation.  The caller is responsible for the application
        audit; the durable event below is the minimal service-side audit trail.
        """
        if not administrator:
            raise ValueError("Acesso não autorizado à edição administrativa.")
        editable = {
            "tratamento", "destinatario", "cargo", "unidade", "instituicao",
            "assunto", "referencia", "vocativo", "corpo", "processo",
            "procedimento", "fechamento", "titulo_assinatura", "observacoes",
        }
        def audit_value(value):
            text = str(value or "")
            return text if len(text) <= 500 else text[:500] + "… [conteúdo resumido]"
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            previous = self._get(c, identifier)
            if previous["direcao"] != "ENVIADO" or previous["numero"] is None:
                raise ValueError("A edição administrativa exige Ofício enviado e numerado.")
            record = dict(previous)
            changed = {}
            for field in editable:
                if field not in changes:
                    continue
                value = changes[field]
                if value is None:
                    value = ""
                if previous.get(field, "") != value:
                    changed[field] = {
                        "anterior": audit_value(previous.get(field, "")),
                        "novo": audit_value(value),
                    }
                    record[field] = value
            if not changed:
                return {"record": previous, "changed": {}}
            validate(record, official=True)
            c.execute(
                "UPDATE oficios SET assunto=?,destinatario=?,payload=?,atualizada=? WHERE id=?",
                (record["assunto"], record.get("destinatario", ""), encode(record), now(), identifier),
            )
            self._movement(c, identifier, previous["status"], previous["status"], "Edição administrativa de dados cadastrais")
            self.store.event(
                c,
                "oficio_edicao_administrativa",
                {
                    "id": identifier,
                    "serie": previous["serie"],
                    "ano": previous["ano"],
                    "numero": previous["numero"],
                    "usuario": actor_email,
                    "campos": changed,
                },
            )
            return {"record": self._get(c, identifier), "changed": changed}

    def delete_finalized(
        self, identifier, reason, confirmation, *, administrator=False, actor_email=""
    ):
        """Delete a final record and exclusive relations in one transaction.

        The pre-send ``Gerado`` state keeps its stricter, full-quarantine flow.
        Officially used numbers are never returned to the available sequence.
        """
        if not administrator:
            raise ValueError("Acesso não autorizado à exclusão definitiva.")
        if not reason or not reason.strip():
            raise ValueError("Informe o motivo da exclusão definitiva.")
        with self.store.connection(read_only=True) as c:
            record = self._get(c, identifier)
        if record["status"] == "Rascunho" or (
            record["direcao"] == "ENVIADO" and record["numero"] is None
        ):
            raise ValueError("Use a exclusão de rascunho para documento não numerado.")
        reference = (
            f"{record['serie']} {record['numero']}/{record['ano']}"
            if record["direcao"] == "ENVIADO"
            else str(record.get("numero_externo") or record["id"])
        )
        if confirmation.strip() != reference:
            raise ValueError("Digite a identificação completa do Ofício para confirmar.")
        if record["direcao"] == "ENVIADO" and record["status"] == "Gerado" and not record.get("data_envio"):
            self.delete_generated(
                identifier,
                reason,
                True,
                "EXCLUIR",
                True,
                administrator=True,
                actor_email=actor_email,
            )
            return {"id": identifier, "identificacao": reference, "quarentena": True, "numero_liberado": True}
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            record = self._get(c, identifier)
            linked = [
                dict(row)
                for row in c.execute(
                    "SELECT serie,numero,ano,status FROM oficios WHERE responde_a=? ORDER BY ano,numero,id",
                    (identifier,),
                )
            ]
            if linked:
                raise ValueError("Há Ofício vinculado como resposta. Desvincule-o antes da exclusão.")
            snapshot = {
                "id": record["id"], "direcao": record["direcao"], "serie": record["serie"],
                "ano": record["ano"], "numero": record["numero"], "identificacao": reference,
                "status": record["status"], "usuario": actor_email, "motivo": reason.strip(),
                "tipo": "EXCLUSAO_DEFINITIVA", "instante": now(),
            }
            from database.internal_collaboration import InternalCollaborationStore

            InternalCollaborationStore.delete_origin(
                c, "oficio_enviado" if record["direcao"] == "ENVIADO" else "oficio_recebido", identifier
            )
            deleted = c.execute("DELETE FROM oficios WHERE id=?", (identifier,))
            if getattr(deleted, "rowcount", 1) == 0:
                raise ValueError("Não foi possível excluir o Ofício.")
            self.store.event(c, "oficio_excluir_definitivamente", snapshot)
            return snapshot

    def _file(self, c, identifier, name, mime, content, role="DOCUMENTO", original_name=None):
        c.execute(
            "INSERT INTO oficio_arquivos(id,oficio_id,nome,tipo,tamanho,incluida,conteudo,papel,nome_original) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,
                identifier,
                name,
                mime,
                len(content),
                now(),
                content,
                role,
                original_name or name,
            ),
        )

    def finalize_reviewed(self, identifier, document, series, preview_hash):
        from services.oficios import fingerprint

        if not preview_hash or preview_hash != fingerprint(
            document, series, [series["sigla"], identifier]
        ):
            raise ValueError("Gere uma nova prévia antes de finalizar.")
        return self.finalize(
            identifier, expected_record=document, expected_series=series
        )

    def finalize(
        self,
        identifier,
        generator=None,
        *,
        expected_record=None,
        expected_series=None,
        attached_files=None,
        complementary_files=(),
    ):
        from document_generator.oficios import official_documents
        from services.oficios import safe_name, validate_complementary_upload

        complementary = [
            (*validate_complementary_upload(name, content), name, content)
            for name, content in (complementary_files or ())
        ]

        generator = generator or official_documents
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            record = self._get(c, identifier)
            if record["direcao"] != "ENVIADO":
                raise ValueError("Apenas enviados podem ser gerados.")
            if record["numero"] is not None:
                return record
            if record["status"] != "Rascunho":
                raise ValueError("Rascunho inválido.")
            validate(record, official=True, attached=bool(attached_files))
            member = c.execute(
                "SELECT nome,cargo_base,ativo FROM procuradores WHERE id=?",
                (record["membro_id"],),
            ).fetchone()
            if not member or not member["ativo"]:
                raise ValueError("Signatário inativo.")
            series = c.execute(
                "SELECT * FROM oficio_series WHERE membro_id=?", (record["membro_id"],)
            ).fetchone()
            if not series:
                raise ValueError("Configure a série e o modelo institucional.")
            if attached_files is None and (
                not series["modelo"] or not series["cabecalho"]
            ):
                raise ValueError("Configure a série e o modelo institucional.")
            series = dict(series)
            record.update(signatario=member["nome"], cargo_base=member["cargo_base"])
            if expected_record is not None and any(
                record.get(k) != v for k, v in expected_record.items()
            ):
                raise ValueError(
                    "O rascunho foi alterado. Gere uma nova prévia antes de finalizar."
                )
            if expected_series is not None and any(
                series.get(k) != v for k, v in expected_series.items() if k != "nome"
            ):
                raise ValueError(
                    "O modelo foi alterado. Gere uma nova prévia antes de finalizar."
                )
            seq = c.execute(
                "SELECT proximo FROM oficio_sequencias WHERE serie=? AND ano=?",
                (series["sigla"], record["ano"]),
            ).fetchone()
            if not seq:
                raise ValueError(
                    "Confirme o próximo número da série/ano antes de finalizar."
                )
            record.update(signatario=member["nome"], cargo_base=member["cargo_base"])
            released = c.execute(
                "SELECT MIN(numero) FROM oficio_numeros_liberados WHERE serie=? AND ano=?",
                (series["sigla"], record["ano"]),
            ).fetchone()[0]
            number = released or seq["proximo"]
            if attached_files is not None:
                if len(attached_files) != 1:
                    raise ValueError("Anexe um único arquivo PDF ou DOCX.")
                ext, name, content = attached_files[0]
                if ext not in ("pdf", "docx") or not content:
                    raise ValueError("Aceitos apenas PDF e DOCX.")
                validate_upload(
                    name
                    if str(name).lower().endswith("." + ext)
                    else "oficio." + ext,
                    content,
                )
                stamped = safe_name(
                    f"{series['sigla']}-{str(number).zfill(int(series['digitos'] or 1))}-{record['ano']}.{ext}"
                )
                files = [(ext, stamped, content)]
                note = "Arquivo anexado persistido"
            else:
                files = generator(record, series, number)
                if {x[0] for x in files} != {"docx", "pdf"} or any(
                    not x[2] for x in files
                ):
                    raise ValueError("A geração deve produzir DOCX e PDF.")
                note = "DOCX e PDF persistidos"
            for ext, name, content in files:
                self._file(
                    c,
                    identifier,
                    name,
                    (
                        "application/pdf"
                        if ext == "pdf"
                        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                    ),
                    content,
                    "PRINCIPAL" if attached_files is not None else "DOCUMENTO",
                    name,
                )
            for safe, mime, original_name, content in complementary:
                self._file(c, identifier, safe, mime, content, "ANEXO", original_name)
            c.execute(
                "UPDATE oficios SET serie=?,numero=?,status='Gerado',payload=?,atualizada=? WHERE id=?",
                (series["sigla"], number, encode(record), now(), identifier),
            )
            c.execute(
                "UPDATE oficio_sequencias SET proximo=? WHERE serie=? AND ano=?",
                (max(number + 1, seq["proximo"]), series["sigla"], record["ano"]),
            )
            if released:
                c.execute(
                    "DELETE FROM oficio_numeros_liberados WHERE serie=? AND ano=? AND numero=?",
                    (series["sigla"], record["ano"], number),
                )
            self._movement(c, identifier, "Rascunho", "Gerado", note)
            return self._get(c, identifier)

    def update_status(self, identifier, status, note="", due=None, sent=None):
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            r = self._get(c, identifier)
            allowed = SENT if r["direcao"] == "ENVIADO" else RECEIVED
            if (
                status not in allowed
                or status in ("Rascunho", "Gerado")
                or r["status"] == "Cancelado"
                or (r["direcao"] == "ENVIADO" and r["numero"] is None)
            ):
                raise ValueError("Transição de status inválida.")
            if status == "Cancelado" and not note.strip():
                raise ValueError("Informe o motivo do cancelamento.")
            if status == "Enviado" and not sent:
                raise ValueError("Informe a data de envio.")
            for value in (due, sent):
                if value:
                    date.fromisoformat(value)
            # Document snapshot stays immutable; follow-up fields are columns/history.
            c.execute(
                "UPDATE oficios SET status=?,prazo=?,atualizada=?,data_envio=COALESCE(?,data_envio),cancelada=? WHERE id=?",
                (
                    status,
                    due or None,
                    now(),
                    sent,
                    now() if status == "Cancelado" else None,
                    identifier,
                ),
            )
            self._movement(
                c,
                identifier,
                r["status"],
                status,
                note + (f" | Envio: {sent}" if sent else ""),
            )

    def set_response_tracking(self, identifier, waiting, expected_date=None, *, quantidade=None, tipo=None, inicio=None, manual=False, motivo_ajuste=""):
        if expected_date:
            date.fromisoformat(expected_date)
        waiting = bool(waiting)
        if waiting and quantidade is not None:
            if not isinstance(quantidade, int) or quantidade < 1:
                raise ValueError("O prazo deve ser um número inteiro positivo.")
            if tipo not in ("DIAS_UTEIS", "DIAS_CORRIDOS"):
                raise ValueError("Tipo de prazo inválido.")
            if not inicio:
                raise ValueError("Informe o início da contagem.")
            date.fromisoformat(inicio)
        if manual and not motivo_ajuste.strip():
            raise ValueError("Informe o motivo do ajuste manual do vencimento.")
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            record = self._get(c, identifier)
            if (
                record["direcao"] != "ENVIADO"
                or record["numero"] is None
                or record["status"] == "Cancelado"
            ):
                raise ValueError("O acompanhamento exige um Ofício enviado e numerado.")
            if not waiting:
                expected_date = None
            status = record["status"]
            if not record.get("responde_a"):
                if waiting and status not in ("Concluído", "Respondido"):
                    status = "Aguardando resposta"
                elif not waiting and status == "Aguardando resposta":
                    status = "Enviado"
            stamp = now()
            c.execute(
                "UPDATE oficios SET aguarda_resposta=?,data_esperada_resposta=?,"
                "prazo_resposta_quantidade=?,prazo_resposta_tipo=?,prazo_resposta_inicio=?,"
                "prazo_resposta_manual=?,prazo_resposta_motivo_ajuste=?,status=?,atualizada=? WHERE id=?",
                (1 if waiting else 0, expected_date if waiting else None,
                 quantidade if waiting else None, tipo if waiting else None,
                 inicio if waiting else None, 1 if waiting and manual else 0,
                 motivo_ajuste.strip() if waiting and manual else None, status, stamp, identifier),
            )
            self._movement(
                c,
                identifier,
                record["status"],
                status,
                "Acompanhamento de resposta ativado"
                if waiting
                else "Acompanhamento de resposta desativado",
            )

    def response_candidates(self, identifier, member_id=None, limit=200):
        with self.store.connection(read_only=True) as c:
            record = self._get(c, identifier)
            if record["direcao"] != "ENVIADO":
                return []
            args = [identifier]
            member_clause = ""
            if member_id:
                member_clause = (
                    " AND EXISTS (SELECT 1 FROM oficio_destinatarios d "
                    "WHERE d.oficio_id=o.id AND d.membro_id=?)"
                )
                args.append(member_id)
            args.append(max(1, min(int(limit), 500)))
            return [
                dict(row)
                for row in c.execute(
                    "SELECT o.id,o.numero_externo,o.assunto,o.data,o.status "
                    "FROM oficios o WHERE o.direcao='RECEBIDO' AND NOT EXISTS ("
                    "SELECT 1 FROM oficios linked "
                    "WHERE linked.responde_a=o.id AND linked.id!=?)"
                    + member_clause
                    + " ORDER BY o.data DESC,o.id DESC LIMIT ?",
                    args,
                )
            ]

    def link_response(self, identifier, received_id):
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            sent = self._get(c, identifier)
            received = self._get(c, received_id)
            if sent["direcao"] != "ENVIADO" or sent["numero"] is None:
                raise ValueError("Selecione um Ofício enviado e numerado.")
            if received["direcao"] != "RECEBIDO":
                raise ValueError("A resposta deve ser um Ofício recebido.")
            if sent.get("responde_a"):
                raise ValueError("Este Ofício já possui resposta vinculada.")
            if c.execute(
                "SELECT id FROM oficios WHERE responde_a=? AND id!=? LIMIT 1",
                (received_id, identifier),
            ).fetchone():
                raise ValueError("Este Ofício recebido já está vinculado como resposta.")
            c.execute(
                "UPDATE oficios SET responde_a=?,status='Respondido',atualizada=? WHERE id=?",
                (received_id, now(), identifier),
            )
            self._movement(c, identifier, sent["status"], "Respondido", "Resposta vinculada")

    def unlink_response(self, identifier):
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            sent = self._get(c, identifier)
            if sent["direcao"] != "ENVIADO" or not sent.get("responde_a"):
                raise ValueError("Este Ofício não possui resposta vinculada.")
            status = "Aguardando resposta" if sent.get("aguarda_resposta") else "Enviado"
            c.execute(
                "UPDATE oficios SET responde_a=NULL,status=?,atualizada=? WHERE id=?",
                (status, now(), identifier),
            )
            self._movement(c, identifier, sent["status"], status, "Resposta desvinculada")

    def delete_draft(self, identifier, confirmed=False):
        if not confirmed:
            raise ValueError("Confirme a exclusão do rascunho.")
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            r = self._get(c, identifier)
            if r["numero"] is not None or r["status"] != "Rascunho":
                raise ValueError("Ofício numerado deve ser cancelado.")
            self.store.event(c, "oficio_excluir_rascunho", {"id": identifier})
            from database.internal_collaboration import InternalCollaborationStore

            InternalCollaborationStore.delete_origin(c, "oficio_enviado", identifier)
            c.execute("DELETE FROM oficios WHERE id=?", (identifier,))

    def delete_received(self, identifier, acknowledged=False, typed="", *, administrator=False):
        if not administrator:
            raise ValueError("Acesso não autorizado à exclusão definitiva.")
        if not acknowledged or typed != "EXCLUIR":
            raise ValueError(
                "Confirme a exclusão definitiva: marque a ciência e digite EXCLUIR."
            )
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            record = self._get(c, identifier)
            if record["direcao"] != "RECEBIDO":
                raise ValueError(
                    "Somente ofício recebido pode ser excluído por esta ação."
                )
            linked = [
                dict(r)
                for r in c.execute(
                    "SELECT serie,numero,ano,status FROM oficios WHERE responde_a=? ORDER BY ano,numero,id",
                    (identifier,),
                )
            ]
            if linked:
                refs = ", ".join(
                    f"{(r['serie'] or '').strip()} {r['numero'] or 'Rascunho'}/{r['ano']}".strip()
                    for r in linked
                )
                raise ValueError(
                    "Há Ofício enviado relacionado a este recebido ("
                    + refs
                    + "). Trate o vínculo antes da exclusão."
                )
            self.store.event(
                c,
                "oficio_excluir_recebido",
                {
                    "id": identifier,
                    "numero_externo": record.get("numero_externo"),
                    "assunto": record.get("assunto"),
                },
            )
            from database.internal_collaboration import InternalCollaborationStore

            InternalCollaborationStore.delete_origin(c, "oficio_recebido", identifier)
            c.execute("DELETE FROM oficios WHERE id=?", (identifier,))

    def delete_generated(
        self,
        identifier,
        reason,
        acknowledged=False,
        typed="",
        confirmed=False,
        *,
        administrator=False,
        actor_email="",
    ):
        """Quarantine and release atomically, retaining evidence independently of FKs."""
        import base64

        if not administrator:
            raise ValueError("Acesso não autorizado à exclusão definitiva.")
        if (
            not reason.strip()
            or not acknowledged
            or typed != "EXCLUIR"
            or not confirmed
        ):
            raise ValueError(
                "Informe motivo e todas as confirmações da exclusão definitiva."
            )
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            record = self._get(c, identifier)
            history = [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM oficio_movimentacoes WHERE oficio_id=?",
                    (identifier,),
                )
            ]
            if (
                record["direcao"] != "ENVIADO"
                or record["status"] != "Gerado"
                or record["numero"] is None
                or record.get("data_envio")
                or any(r["novo"] not in ("Rascunho", "Gerado") for r in history)
                or c.execute(
                    "SELECT id FROM oficios WHERE responde_a=? LIMIT 1", (identifier,)
                ).fetchone()
            ):
                raise ValueError(
                    "Há envio, tramitação ou vínculo impeditivo. Use cancelamento, mantendo o número."
                )
            files = []
            for row in c.execute(
                "SELECT * FROM oficio_arquivos WHERE oficio_id=?", (identifier,)
            ):
                item = dict(row)
                item["conteudo"] = base64.b64encode(bytes(item["conteudo"])).decode(
                    "ascii"
                )
                files.append(item)
            stamp = now()
            audit = {
                **record,
                "gabinete": record["serie"],
                "motivo": reason.strip(),
                "instante": stamp,
                "usuario": actor_email,
                "numero_liberado": True,
                "movimentacoes": history,
            }
            c.execute(
                "INSERT INTO oficio_quarentena VALUES(?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    stamp,
                    reason.strip(),
                    encode(audit),
                    encode(files).encode("utf-8"),
                ),
            )
            self.store.event(c, "oficio_excluir_definitivamente", audit)
            c.execute(
                "INSERT INTO oficio_numeros_liberados VALUES(?,?,?)",
                (record["serie"], record["ano"], record["numero"]),
            )
            from database.internal_collaboration import InternalCollaborationStore

            InternalCollaborationStore.delete_origin(c, "oficio_enviado", identifier)
            c.execute("DELETE FROM oficios WHERE id=?", (identifier,))

    def files(self, identifier):
        with self.store.connection(read_only=True) as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT id,COALESCE(nome_original,nome) AS nome,tipo,tamanho,incluida,"
                    "COALESCE(papel,'DOCUMENTO') AS papel FROM oficio_arquivos "
                    "WHERE oficio_id=? ORDER BY "
                    "CASE COALESCE(papel,'DOCUMENTO') WHEN 'PRINCIPAL' THEN 0 "
                    "WHEN 'DOCUMENTO' THEN 0 ELSE 1 END,incluida,id",
                    (identifier,),
                )
            ]

    def download(self, file_id):
        with self.store.connection(read_only=True) as c:
            row = c.execute(
                "SELECT conteudo FROM oficio_arquivos WHERE id=?", (file_id,)
            ).fetchone()
            if not row:
                raise ValueError("Arquivo não encontrado.")
            return bytes(row["conteudo"])

    def movements(self, identifier):
        with self.store.connection(read_only=True) as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT instante,anterior,novo,observacao FROM oficio_movimentacoes WHERE oficio_id=? ORDER BY instante,id",
                    (identifier,),
                )
            ]

    def list(
        self,
        *,
        direction=None,
        series=None,
        member=None,
        number=None,
        year=None,
        recipient="",
        subject="",
        status=None,
        start=None,
        end=None,
        search="",
        deadline=None,
        related=None,
        attention_only=False,
        offset=0,
        limit=50,
    ):
        clauses = []
        values = []
        effective_due = "(CASE WHEN o.direcao='ENVIADO' AND o.numero IS NOT NULL AND o.aguarda_resposta=1 AND o.responde_a IS NULL THEN o.data_esperada_resposta ELSE o.prazo END)"
        for column, value in [
            ("direcao", direction),
            ("serie", series),
            ("numero", number),
            ("ano", year),
            ("status", status),
            ("responde_a", related),
        ]:
            if value is not None:
                clauses.append("o." + column + "=?")
                values.append(value)
        for column, value in [("destinatario", recipient), ("assunto", subject)]:
            if value:
                clauses.append("LOWER(o." + column + ") LIKE LOWER(?)")
                values.append("%" + value + "%")
        if member:
            clauses.append(
                "EXISTS (SELECT 1 FROM oficio_destinatarios d WHERE d.oficio_id=o.id AND d.membro_id=?)"
            )
            values.append(member)
        for op, value in [(">=", start), ("<=", end)]:
            if value:
                clauses.append("o.data" + op + "?")
                values.append(value)
        if search:
            clauses.append(
                "(LOWER(o.payload) LIKE LOWER(?) OR LOWER(o.numero_externo) LIKE LOWER(?))"
            )
            values.extend(["%" + search + "%"] * 2)
        if deadline:
            today = date.today()
            clauses.append(
                "o.status NOT IN ('Respondido','Concluído','Cancelado','Arquivado')"
            )
            if deadline == "Vencido":
                clauses.append(effective_due + "<?")
                values.append(today.isoformat())
            elif deadline == "Próximos 7 dias":
                clauses.append(effective_due + ">=? AND " + effective_due + "<=?")
                values.extend(
                    [today.isoformat(), (today + timedelta(days=7)).isoformat()]
                )
            elif deadline == "Com prazo":
                clauses.append(effective_due + " IS NOT NULL")
        if attention_only:
            clauses.append(
                "(o.status NOT IN ('Respondido','Concluído','Cancelado','Arquivado') AND ((o.direcao='ENVIADO' AND o.status!='Rascunho') OR o.status IN ('Em análise','Aguardando providência') OR "
                + effective_due
                + "<=?))"
            )
            values.append((date.today() + timedelta(days=7)).isoformat())
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        if not 1 <= limit <= 200 or offset < 0:
            raise ValueError("Página inválida.")
        # No file joins and no BLOB/BYTEA; body payload is only fetched in details.
        cols = ",".join("o." + x for x in COLUMNS.split(",") if x != "payload")
        with self.store.connection(read_only=True) as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT "
                    + cols
                    + " FROM oficios o"
                    + where
                    + (
                        " ORDER BY CASE WHEN " + effective_due + "<'"
                        + date.today().isoformat()
                        + "' THEN 0 WHEN " + effective_due + "<='"
                        + (date.today() + timedelta(days=7)).isoformat()
                        + "' THEN 1 WHEN o.status='Aguardando providência' THEN 2 WHEN o.status='Aguardando resposta' THEN 3 WHEN o.status='Em análise' THEN 4 ELSE 5 END,o.prazo,o.data DESC,o.id LIMIT ? OFFSET ?"
                        if attention_only
                        else " ORDER BY o.atualizada DESC,o.criada DESC,o.id LIMIT ? OFFSET ?"
                    ),
                    values + [limit, offset],
                )
            ]

    def overview(self, year, member=None):
        today = date.today().isoformat()
        soon = (date.today() + timedelta(days=7)).isoformat()
        with self.store.connection(read_only=True) as c:
            return dict(
                c.execute(
                    """SELECT
    COALESCE(SUM(CASE WHEN direcao='ENVIADO' AND ano=? AND numero IS NOT NULL THEN 1 ELSE 0 END),0) AS enviados,
    COALESCE(SUM(CASE WHEN direcao='RECEBIDO' AND ano=? THEN 1 ELSE 0 END),0) AS recebidos,
    COALESCE(SUM(CASE WHEN direcao='ENVIADO' AND numero IS NOT NULL AND aguarda_resposta=1 AND responde_a IS NULL AND status NOT IN ('Respondido','Concluído','Cancelado','Arquivado') THEN 1 ELSE 0 END),0) AS aguardando_resposta,
    COALESCE(SUM(CASE WHEN status='Aguardando providência' THEN 1 ELSE 0 END),0) AS aguardando_providencia,
    COALESCE(SUM(CASE WHEN (CASE WHEN direcao='ENVIADO' AND numero IS NOT NULL AND aguarda_resposta=1 THEN data_esperada_resposta ELSE prazo END)<? AND status NOT IN ('Respondido','Concluído','Cancelado','Arquivado') THEN 1 ELSE 0 END),0) AS vencidos,
    COALESCE(SUM(CASE WHEN (CASE WHEN direcao='ENVIADO' AND numero IS NOT NULL AND aguarda_resposta=1 THEN data_esperada_resposta ELSE prazo END)>=? AND (CASE WHEN direcao='ENVIADO' AND numero IS NOT NULL AND aguarda_resposta=1 THEN data_esperada_resposta ELSE prazo END)<=? AND status NOT IN ('Respondido','Concluído','Cancelado','Arquivado') THEN 1 ELSE 0 END),0) AS proximos
    FROM oficios o"""
                    + (
                        " WHERE EXISTS (SELECT 1 FROM oficio_destinatarios d WHERE d.oficio_id=o.id AND d.membro_id=?)"
                        if member
                        else ""
                    ),
                    (year, year, today, today, soon) + ((member,) if member else ()),
                ).fetchone()
            )
