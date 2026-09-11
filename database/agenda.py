"""Agenda persistence through the existing Store connection/pool contract."""

from datetime import datetime
import json
import uuid
from database.store import now
from services.agenda import RULES, normalized, validate, institutional, conflicts

_READY = set()


class AgendaStore:
    def __init__(self, store):
        self.store = store
        self.initialize()

    def initialize(self):
        key = self.store.schema_key
        if key in _READY:
            with self.store.connection(read_only=True) as c:
                self.bindings = {
                    r["chave"].removeprefix("agenda_member_"): int(r["valor"])
                    for r in c.execute(
                        "SELECT chave,valor FROM configuracoes WHERE chave LIKE 'agenda_member_%'"
                    )
                }
            return
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                """CREATE TABLE IF NOT EXISTS agenda_compromissos (
                id TEXT PRIMARY KEY, tipo TEXT NOT NULL CHECK(tipo IN ('EVENTO','REUNIAO','DESPACHO')),
                inicio TEXT NOT NULL, fim TEXT, situacao TEXT NOT NULL,
                payload TEXT NOT NULL, criada TEXT NOT NULL, atualizada TEXT NOT NULL)"""
            )
            c.execute(
                """CREATE TABLE IF NOT EXISTS agenda_compromisso_procuradores (
                compromisso_id TEXT NOT NULL REFERENCES agenda_compromissos(id) ON DELETE CASCADE,
                procurador_id INTEGER NOT NULL REFERENCES procuradores(id),
                PRIMARY KEY(compromisso_id, procurador_id))"""
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS agenda_periodo_idx ON agenda_compromissos(inicio, fim)"
            )
            c.execute(
                "CREATE INDEX IF NOT EXISTS agenda_membro_idx ON agenda_compromisso_procuradores(procurador_id, compromisso_id)"
            )
            people = [dict(r) for r in c.execute("SELECT id,nome FROM procuradores")]
            # Resolve once against the existing catalog, then persist stable IDs, never seed positions.
            for rule in RULES:
                matches = [
                    p
                    for p in people
                    if normalized(p["nome"]) == normalized(rule["name"])
                ]
                if len(matches) == 1:
                    c.execute(
                        "INSERT INTO configuracoes(chave,valor) VALUES(?,?) ON CONFLICT(chave) DO NOTHING",
                        ("agenda_member_" + rule["key"], str(matches[0]["id"])),
                    )
            self.bindings = {
                r["chave"].removeprefix("agenda_member_"): int(r["valor"])
                for r in c.execute(
                    "SELECT chave,valor FROM configuracoes WHERE chave LIKE 'agenda_member_%'"
                )
            }
        _READY.add(key)

    def _list(self, c, start, end, member=None, kind=None, status=None, *, offset=None):
        if offset is None:
            clauses = ["a.inicio < ?", "COALESCE(a.fim,a.inicio) >= ?"]
            values = [end, start]
        else:
            clauses = ["a.inicio >= ?"]
            values = [start]
        for column, value in (("tipo", kind), ("situacao", status)):
            if value:
                clauses.append(f"a.{column}=?")
                values.append(value)
        if member:
            clauses.append(
                "EXISTS (SELECT 1 FROM agenda_compromisso_procuradores p WHERE p.compromisso_id=a.id AND p.procurador_id=?)"
            )
            values.append(member)
        source = "agenda_compromissos a"
        where = " WHERE " + " AND ".join(clauses)
        if offset is not None:
            # Limit appointments before joining participants: 30 plus one lookahead.
            source = (
                "(SELECT a.* FROM agenda_compromissos a"
                + where
                + " ORDER BY a.inicio,a.id LIMIT 31 OFFSET ?) a"
            )
            values.append(offset)
            where = ""
        rows = c.execute(
            "SELECT a.*,p.procurador_id FROM "
            + source
            + " JOIN agenda_compromisso_procuradores p ON p.compromisso_id=a.id"
            + where
            + " ORDER BY a.inicio,a.id,p.procurador_id",
            values,
        )
        records = {}
        for row in rows:
            if row["id"] not in records:
                records[row["id"]] = {
                    **json.loads(row["payload"]),
                    **{
                        k: row[k]
                        for k in (
                            "id",
                            "tipo",
                            "inicio",
                            "fim",
                            "situacao",
                            "criada",
                            "atualizada",
                        )
                    },
                    "procuradores": [],
                }
            records[row["id"]]["procuradores"].append(row["procurador_id"])
        return list(records.values())

    def list(self, start, end, member=None, kind=None, status=None):
        with self.store.connection(read_only=True) as c:
            return self._list(c, start, end, member, kind, status)

    def upcoming(self, start, member=None, kind=None, status=None, offset=0):
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Página inválida.")
        with self.store.connection(read_only=True) as c:
            return self._list(c, start, None, member, kind, status, offset=offset)

    def save(self, record, *, institutional_confirmed=False, conflict_confirmed=False):
        validate(record)
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            people = {
                r["id"] for r in c.execute("SELECT id FROM procuradores WHERE ativo=1")
            }
            if not set(record["procuradores"]) <= people:
                raise ValueError("Selecione procuradores ativos da base existente.")
            alerts = institutional(
                record["procuradores"],
                record["tipo"],
                datetime.fromisoformat(record["inicio"]).date(),
                self.bindings,
            )
            if (
                alerts
                and record["situacao"] != "Cancelado"
                and not institutional_confirmed
            ):
                raise ValueError(
                    "Confirme a disponibilidade institucional antes de salvar."
                )
            others = self._list(
                c,
                record["inicio"][:10],
                (record.get("fim") or record["inicio"])[:10] + "T23:59:59",
            )
            if conflicts(record, others) and not conflict_confirmed:
                raise ValueError(
                    "Confirme a ciência do conflito de horário antes de salvar."
                )
            identifier = record.get("id") or uuid.uuid4().hex
            old = c.execute(
                "SELECT criada FROM agenda_compromissos WHERE id=?", (identifier,)
            ).fetchone()
            if record.get("id") and not old:
                raise ValueError("Compromisso não encontrado; pode ter sido excluído.")
            stamp = now()
            payload = {
                k: record.get(k, "")
                for k in (
                    "titulo",
                    "categoria",
                    "reuniao_com",
                    "processo",
                    "local",
                    "observacoes",
                    "sem_hora",
                )
            }
            payload.update(
                confirmacao_institucional=bool(institutional_confirmed),
                confirmacao_conflito=bool(conflict_confirmed),
            )
            c.execute(
                """INSERT INTO agenda_compromissos(id,tipo,inicio,fim,situacao,payload,criada,atualizada)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET tipo=excluded.tipo,inicio=excluded.inicio,
                fim=excluded.fim,situacao=excluded.situacao,payload=excluded.payload,atualizada=excluded.atualizada""",
                (
                    identifier,
                    record["tipo"],
                    record["inicio"],
                    record.get("fim"),
                    record["situacao"],
                    json.dumps(payload, ensure_ascii=False),
                    old[0] if old else stamp,
                    stamp,
                ),
            )
            c.execute(
                "DELETE FROM agenda_compromisso_procuradores WHERE compromisso_id=?",
                (identifier,),
            )
            c.executemany(
                "INSERT INTO agenda_compromisso_procuradores VALUES(?,?)",
                [(identifier, p) for p in record["procuradores"]],
            )
            return identifier

    def cancel(self, identifier):
        with self.store.connection() as c:
            c.execute(
                "UPDATE agenda_compromissos SET situacao='Cancelado',atualizada=? WHERE id=?",
                (now(), identifier),
            )

    def delete(self, identifier, *, confirmed=False):
        if not confirmed:
            raise ValueError("Confirme explicitamente a exclusão.")
        with self.store.connection() as c:
            c.execute("DELETE FROM agenda_compromissos WHERE id=?", (identifier,))
