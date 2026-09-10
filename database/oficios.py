"""Additive correspondence repository using only the public Store connection API.

The existing PostgreSQL connection holds a schema advisory transaction lock;
SQLite BEGIN IMMEDIATE provides the equivalent serialized writer transaction.
"""

from datetime import date, datetime, timedelta
import json
import re
import uuid
from database.store import now, encode
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

COLUMNS = "id,direcao,serie,ano,numero,status,data,prazo,membro_id,assunto,destinatario,numero_externo,responde_a,payload,criada,atualizada,data_envio,cancelada"


class OficiosStore:
    def __init__(self, store):
        self.store = store
        self.initialize()

    def initialize(self):
        # Read-only fast path on subsequent reruns; no DDL/writer lock after migration.
        with self.store.connection(read_only=True) as c:
            if c.execute(
                "SELECT valor FROM configuracoes WHERE chave='oficios_schema_v2'"
            ).fetchone():
                return
        binary = "BYTEA" if self.store.backend == "postgresql" else "BLOB"
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            for statement in [
                "CREATE TABLE IF NOT EXISTS oficio_series (sigla TEXT PRIMARY KEY, membro_id INTEGER NOT NULL UNIQUE REFERENCES procuradores(id), modelo TEXT NOT NULL, cabecalho TEXT NOT NULL, digitos INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS oficio_sequencias (serie TEXT NOT NULL REFERENCES oficio_series(sigla), ano INTEGER NOT NULL CHECK(ano BETWEEN 1000 AND 9999), proximo INTEGER NOT NULL CHECK(proximo>0), confirmada TEXT NOT NULL, PRIMARY KEY(serie,ano))",
                "CREATE TABLE IF NOT EXISTS oficios (id TEXT PRIMARY KEY, direcao TEXT NOT NULL CHECK(direcao IN ('ENVIADO','RECEBIDO')), serie TEXT REFERENCES oficio_series(sigla), ano INTEGER NOT NULL, numero INTEGER CHECK(numero>0), status TEXT NOT NULL, data TEXT NOT NULL, prazo TEXT, membro_id INTEGER REFERENCES procuradores(id), assunto TEXT NOT NULL, destinatario TEXT NOT NULL, numero_externo TEXT NOT NULL, responde_a TEXT REFERENCES oficios(id), payload TEXT NOT NULL, criada TEXT NOT NULL, atualizada TEXT NOT NULL, data_envio TEXT, cancelada TEXT, UNIQUE(serie,ano,numero), CHECK(numero IS NULL OR (direcao='ENVIADO' AND serie IS NOT NULL AND status!='Rascunho')))",
                "CREATE TABLE IF NOT EXISTS oficio_destinatarios (oficio_id TEXT NOT NULL REFERENCES oficios(id) ON DELETE CASCADE, membro_id INTEGER NOT NULL REFERENCES procuradores(id), PRIMARY KEY(oficio_id,membro_id))",
                f"CREATE TABLE IF NOT EXISTS oficio_arquivos (id TEXT PRIMARY KEY, oficio_id TEXT NOT NULL REFERENCES oficios(id) ON DELETE CASCADE, nome TEXT NOT NULL, tipo TEXT NOT NULL, tamanho INTEGER NOT NULL, incluida TEXT NOT NULL, conteudo {binary} NOT NULL)",
                "CREATE TABLE IF NOT EXISTS oficio_movimentacoes (id TEXT PRIMARY KEY, oficio_id TEXT NOT NULL REFERENCES oficios(id) ON DELETE CASCADE, instante TEXT NOT NULL, anterior TEXT NOT NULL, novo TEXT NOT NULL, observacao TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS oficio_data_idx ON oficios(direcao,data DESC,id)",
                "CREATE INDEX IF NOT EXISTS oficio_prazo_idx ON oficios(status,prazo)",
                "CREATE INDEX IF NOT EXISTS oficio_arquivo_idx ON oficio_arquivos(oficio_id)",
                "CREATE INDEX IF NOT EXISTS oficio_movimento_idx ON oficio_movimentacoes(oficio_id,instante)",
                "CREATE INDEX IF NOT EXISTS oficio_membro_idx ON oficio_destinatarios(membro_id,oficio_id)",
                "CREATE TABLE IF NOT EXISTS oficio_numeros_liberados (serie TEXT NOT NULL REFERENCES oficio_series(sigla), ano INTEGER NOT NULL, numero INTEGER NOT NULL CHECK(numero>0), PRIMARY KEY(serie,ano,numero))",
                f"CREATE TABLE IF NOT EXISTS oficio_quarentena (id TEXT PRIMARY KEY, instante TEXT NOT NULL, motivo TEXT NOT NULL, dados TEXT NOT NULL, arquivos {binary} NOT NULL)",
            ]:
                c.execute(statement)
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

    def series(self):
        with self.store.connection(read_only=True) as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT s.*,p.nome FROM oficio_series s JOIN procuradores p ON p.id=s.membro_id ORDER BY s.sigla"
                )
            ]

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
        }

    def get(self, identifier):
        with self.store.connection(read_only=True) as c:
            return self._get(c, identifier)

    def save(self, record, identifier=None, uploads=()):
        validate(record)
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
                    "DELETE FROM oficio_destinatarios WHERE oficio_id=?", (identifier,)
                )
            else:
                c.execute(
                    "INSERT INTO oficios VALUES(" + ",".join("?" for _ in values) + ")",
                    values,
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

    def _file(self, c, identifier, name, mime, content):
        c.execute(
            "INSERT INTO oficio_arquivos VALUES(?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, identifier, name, mime, len(content), now(), content),
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
        self, identifier, generator=None, *, expected_record=None, expected_series=None
    ):
        from document_generator.oficios import official_documents

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
            validate(record, official=True)
            member = c.execute(
                "SELECT nome,cargo_base,ativo FROM procuradores WHERE id=?",
                (record["membro_id"],),
            ).fetchone()
            if not member or not member["ativo"]:
                raise ValueError("Signatário inativo.")
            series = c.execute(
                "SELECT * FROM oficio_series WHERE membro_id=?", (record["membro_id"],)
            ).fetchone()
            if not series or not series["modelo"] or not series["cabecalho"]:
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
            files = generator(record, series, number)
            if {x[0] for x in files} != {"docx", "pdf"} or any(not x[2] for x in files):
                raise ValueError("A geração deve produzir DOCX e PDF.")
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
                )
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
            self._movement(
                c, identifier, "Rascunho", "Gerado", "DOCX e PDF persistidos"
            )
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

    def delete_draft(self, identifier, confirmed=False):
        if not confirmed:
            raise ValueError("Confirme a exclusão do rascunho.")
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            r = self._get(c, identifier)
            if r["numero"] is not None or r["status"] != "Rascunho":
                raise ValueError("Ofício numerado deve ser cancelado.")
            self.store.event(c, "oficio_excluir_rascunho", {"id": identifier})
            c.execute("DELETE FROM oficios WHERE id=?", (identifier,))

    def delete_received(self, identifier, acknowledged=False, typed=""):
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
            c.execute("DELETE FROM oficios WHERE id=?", (identifier,))

    def delete_generated(
        self, identifier, reason, acknowledged=False, typed="", confirmed=False
    ):
        """Quarantine and release atomically, retaining evidence independently of FKs."""
        import base64

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
            c.execute("DELETE FROM oficios WHERE id=?", (identifier,))

    def files(self, identifier):
        with self.store.connection(read_only=True) as c:
            return [
                dict(r)
                for r in c.execute(
                    "SELECT id,nome,tipo,tamanho,incluida FROM oficio_arquivos WHERE oficio_id=? ORDER BY incluida,id",
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
                clauses.append("o.prazo<?")
                values.append(today.isoformat())
            elif deadline == "Próximos 7 dias":
                clauses.append("o.prazo>=? AND o.prazo<=?")
                values.extend(
                    [today.isoformat(), (today + timedelta(days=7)).isoformat()]
                )
            elif deadline == "Com prazo":
                clauses.append("o.prazo IS NOT NULL")
        if attention_only:
            clauses.append(
                "(o.status NOT IN ('Respondido','Concluído','Cancelado','Arquivado') AND ((o.direcao='ENVIADO' AND o.status!='Rascunho') OR o.status IN ('Em análise','Aguardando providência') OR o.prazo<=?))"
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
                        " ORDER BY CASE WHEN o.prazo<'"
                        + date.today().isoformat()
                        + "' THEN 0 WHEN o.prazo<='"
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
    COALESCE(SUM(CASE WHEN status='Aguardando resposta' THEN 1 ELSE 0 END),0) AS aguardando_resposta,
    COALESCE(SUM(CASE WHEN status='Aguardando providência' THEN 1 ELSE 0 END),0) AS aguardando_providencia,
    COALESCE(SUM(CASE WHEN prazo<? AND status NOT IN ('Respondido','Concluído','Cancelado','Arquivado') THEN 1 ELSE 0 END),0) AS vencidos,
    COALESCE(SUM(CASE WHEN prazo>=? AND prazo<=? AND status NOT IN ('Respondido','Concluído','Cancelado','Arquivado') THEN 1 ELSE 0 END),0) AS proximos
    FROM oficios o"""
                    + (
                        " WHERE EXISTS (SELECT 1 FROM oficio_destinatarios d WHERE d.oficio_id=o.id AND d.membro_id=?)"
                        if member
                        else ""
                    ),
                    (year, year, today, today, soon) + ((member,) if member else ()),
                ).fetchone()
            )
