"""Agenda persistence through the existing Store connection/pool contract."""

from datetime import datetime
import json
import re
import uuid
from database.store import now, schema_key_of, unwrap_store
from services.agenda import RULES, normalized, validate, institutional, conflicts
from services.afastamentos import validate as validate_leave, status as leave_status

_READY = set()


class AgendaStore:
    def __init__(self, store, *, load_bindings=True):
        self.store = unwrap_store(store)
        self.initialize(load_bindings=load_bindings)

    def initialize(self, *, load_bindings=True):
        key = schema_key_of(self.store)
        if key in _READY:
            if not load_bindings:
                return
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
            # Additive migration: travel logistics belongs to the institutional
            # commitment and its participating procurador, never to a leave.
            c.execute("""CREATE TABLE IF NOT EXISTS agenda_compromissos_viagens (
                id TEXT PRIMARY KEY,
                compromisso_id TEXT NOT NULL REFERENCES agenda_compromissos(id) ON DELETE CASCADE,
                procurador_id INTEGER NOT NULL REFERENCES procuradores(id),
                aeroporto_ida TEXT, aeroporto_ida_outro TEXT, ida_data TEXT, ida_hora TEXT,
                ida_motorista_hora TEXT, aeroporto_volta TEXT, aeroporto_volta_outro TEXT,
                volta_data TEXT, volta_chegada_hora TEXT, volta_motorista_hora TEXT,
                motorista_informado INTEGER NOT NULL DEFAULT 0,
                informado_em TEXT, informado_por TEXT, observacao TEXT,
                criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL,
                UNIQUE(compromisso_id, procurador_id))""")
            c.execute("CREATE INDEX IF NOT EXISTS agenda_compromissos_viagens_ida_idx ON agenda_compromissos_viagens(ida_data)")
            c.execute("CREATE INDEX IF NOT EXISTS agenda_compromissos_viagens_volta_idx ON agenda_compromissos_viagens(volta_data)")
            c.execute(
                """CREATE TABLE IF NOT EXISTS agenda_afastamentos (
                id TEXT PRIMARY KEY, procurador_id INTEGER NOT NULL REFERENCES procuradores(id),
                motivo TEXT NOT NULL, motivo_outro TEXT, data_inicio TEXT NOT NULL, data_fim TEXT NOT NULL,
                substituto_id INTEGER REFERENCES procuradores(id), observacao TEXT, cancelado INTEGER NOT NULL DEFAULT 0,
                criado_por TEXT, criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL)"""
            )
            c.execute("CREATE INDEX IF NOT EXISTS agenda_afastamentos_procurador_idx ON agenda_afastamentos(procurador_id,data_inicio,data_fim)")
            c.execute("CREATE INDEX IF NOT EXISTS agenda_afastamentos_periodo_idx ON agenda_afastamentos(data_inicio,data_fim)")
            c.execute("""CREATE TABLE IF NOT EXISTS agenda_afastamentos_viagens (
                afastamento_id TEXT PRIMARY KEY REFERENCES agenda_afastamentos(id) ON DELETE CASCADE,
                aeroporto TEXT NOT NULL, aeroporto_outro TEXT, ida_data TEXT, ida_hora TEXT,
                aeroporto_ida TEXT, aeroporto_ida_outro TEXT, aeroporto_volta TEXT, aeroporto_volta_outro TEXT,
                ida_companhia TEXT, ida_voo TEXT, ida_motorista_hora TEXT,
                volta_data TEXT, volta_chegada_hora TEXT, volta_companhia TEXT, volta_voo TEXT,
                volta_motorista_hora TEXT, motorista_informado INTEGER NOT NULL DEFAULT 0,
                informado_em TEXT, informado_por TEXT, observacao TEXT,
                criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL)""")
            c.execute("CREATE INDEX IF NOT EXISTS agenda_viagens_ida_idx ON agenda_afastamentos_viagens(ida_data)")
            c.execute("CREATE INDEX IF NOT EXISTS agenda_viagens_volta_idx ON agenda_afastamentos_viagens(volta_data)")
            if self.store.backend == "postgresql":
                columns = {
                    row[0]
                    for row in c.execute(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema=current_schema() AND table_name='agenda_afastamentos_viagens'"
                    )
                }
            else:
                columns = {row[1] for row in c.execute("PRAGMA table_info(agenda_afastamentos_viagens)")}
            for column in ("aeroporto_ida", "aeroporto_ida_outro", "aeroporto_volta", "aeroporto_volta_outro"):
                if column not in columns:
                    c.execute(
                        "ALTER TABLE agenda_afastamentos_viagens ADD COLUMN "
                        + ("IF NOT EXISTS " if self.store.backend == "postgresql" else "")
                        + column + " TEXT"
                    )
            c.execute("""UPDATE agenda_afastamentos_viagens SET
                aeroporto_ida=COALESCE(aeroporto_ida,aeroporto),
                aeroporto_ida_outro=COALESCE(aeroporto_ida_outro,aeroporto_outro),
                aeroporto_volta=COALESCE(aeroporto_volta,aeroporto),
                aeroporto_volta_outro=COALESCE(aeroporto_volta_outro,aeroporto_outro)
                WHERE aeroporto_ida IS NULL OR aeroporto_volta IS NULL""")
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

    def _list(self, c, start, end, member=None, kind=None, status=None, *, offset=None, active_only=None):
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
        if active_only is True:
            clauses.append("a.situacao NOT IN ('Realizado','Cancelado')")
        elif active_only is False:
            clauses.append("a.situacao IN ('Realizado','Cancelado')")
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

    def get(self, identifier):
        with self.store.connection(read_only=True) as c:
            row = c.execute(
                "SELECT * FROM agenda_compromissos WHERE id=?", (identifier,)
            ).fetchone()
            if not row:
                return None
            people = [
                r[0]
                for r in c.execute(
                    "SELECT procurador_id FROM agenda_compromisso_procuradores "
                    "WHERE compromisso_id=? ORDER BY procurador_id",
                    (identifier,),
                )
            ]
            return {
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
                "procuradores": people,
            }

    def upcoming(self, start, member=None, kind=None, status=None, offset=0):
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("Página inválida.")
        with self.store.connection(read_only=True) as c:
            return self._list(c, start, None, member, kind, status, offset=offset)

    def active(self, start, end, member=None, kind=None, status=None, *, offset=None):
        """Operational appointments only; old dates remain active until closed."""
        with self.store.connection(read_only=True) as c:
            return self._list(c, start, end, member, kind, status, offset=offset, active_only=True)

    def active_upcoming(self, start, member=None, kind=None, status=None, offset=0):
        return self.active(start, None, member, kind, status, offset=offset)

    def history(self, *, member=None, kind=None, status=None, start=None, end=None, search=None, limit=30, offset=0):
        """Fetch one historical page in SQL, newest first, without moving records."""
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 1 or offset < 0:
            raise ValueError("Página inválida.")
        with self.store.connection(read_only=True) as c:
            clauses = ["a.situacao IN ('Realizado','Cancelado')"]
            values = []
            if member:
                clauses.append("EXISTS (SELECT 1 FROM agenda_compromisso_procuradores p WHERE p.compromisso_id=a.id AND p.procurador_id=?)")
                values.append(member)
            if kind:
                clauses.append("a.tipo=?"); values.append(kind)
            if status:
                clauses.append("a.situacao=?"); values.append(status)
            if start:
                clauses.append("a.inicio>=?"); values.append(start)
            if end:
                clauses.append("a.inicio<?"); values.append(end)
            if search:
                clauses.append("a.payload LIKE ?"); values.append("%" + search + "%")
            rows = c.execute("SELECT a.* FROM agenda_compromissos a WHERE " + " AND ".join(clauses) + " ORDER BY a.inicio DESC,a.id DESC LIMIT ? OFFSET ?", [*values, limit + 1, offset]).fetchall()
            participants = {}
            if rows:
                identifiers = [row["id"] for row in rows]
                for participant in c.execute(
                    "SELECT compromisso_id,procurador_id FROM agenda_compromisso_procuradores "
                    "WHERE compromisso_id IN (" + ",".join("?" for _ in identifiers)
                    + ") ORDER BY procurador_id", identifiers,
                ):
                    participants.setdefault(participant[0], []).append(participant[1])
            records = []
            for row in rows:
                people = participants.get(row["id"], [])
                records.append({**json.loads(row["payload"]), **{k: row[k] for k in ("id", "tipo", "inicio", "fim", "situacao", "criada", "atualizada")}, "procuradores": people})
            return records

    def save(self, record, *, institutional_confirmed=False, conflict_confirmed=False, remove_trip_members=()):
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
            removed_with_trip = c.execute(
                "SELECT procurador_id FROM agenda_compromissos_viagens WHERE compromisso_id=? "
                "AND procurador_id NOT IN (" + ",".join("?" for _ in record["procuradores"]) + ")",
                (identifier, *record["procuradores"]),
            ).fetchall() if record["procuradores"] else []
            removed_ids = {row[0] for row in removed_with_trip}
            if removed_ids and not removed_ids <= set(remove_trip_members):
                raise ValueError("Há logística de viagem de membro removido; remova-a explicitamente antes de salvar.")
            if removed_ids:
                c.execute(
                    "DELETE FROM agenda_compromissos_viagens WHERE compromisso_id=? AND procurador_id IN (" + ",".join("?" for _ in removed_ids) + ")",
                    (identifier, *removed_ids),
                )
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

    def _leaves(self, c, start, end, member=None, *, upcoming=False, limit=None, offset=0):
        clauses = ["cancelado=0", "data_inicio>=?" if upcoming else "data_inicio<=? AND data_fim>=?"]
        values = [start] if upcoming else [end, start]
        if member:
            clauses.append("procurador_id=?")
            values.append(member)
        pagination = ""
        if limit is not None:
            if not isinstance(limit, int) or limit < 1 or not isinstance(offset, int) or offset < 0:
                raise ValueError("Página inválida.")
            pagination = " LIMIT ? OFFSET ?"
            values.extend((limit + 1, offset))
        return [dict(r) | {"status": leave_status(dict(r))} for r in c.execute(
            "SELECT id,procurador_id,motivo,motivo_outro,data_inicio,data_fim,substituto_id,observacao,cancelado,criado_por,criado_em,atualizado_em FROM agenda_afastamentos WHERE " + " AND ".join(clauses) + " ORDER BY data_inicio,id" + pagination, values)]

    def leaves(self, start, end, member=None, *, upcoming=False, limit=None, offset=0):
        with self.store.connection(read_only=True) as c:
            return self._leaves(c, start, end, member, upcoming=upcoming, limit=limit, offset=offset)

    def active_leaves(self, start, end, member=None, *, upcoming=False, limit=None, offset=0):
        return [row for row in self.leaves(start, end, member, upcoming=upcoming, limit=limit, offset=offset) if row["status"] in ("AGENDADO", "EM ANDAMENTO")]

    def history_leaves(self, *, member=None, status=None, start=None, end=None, limit=30, offset=0):
        """Historical leave page. Ended status is deliberately derived, never persisted."""
        if not isinstance(limit, int) or not isinstance(offset, int) or limit < 1 or offset < 0:
            raise ValueError("Página inválida.")
        today = datetime.now().date().isoformat()
        with self.store.connection(read_only=True) as c:
            clauses, values = [], []
            clauses.append("(cancelado=1 OR (cancelado=0 AND data_fim<?))"); values.append(today)
            if member:
                clauses.append("procurador_id=?"); values.append(member)
            if start:
                clauses.append("data_fim>=?"); values.append(start)
            if end:
                clauses.append("data_inicio<?"); values.append(end)
            where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
            if status == "CANCELADO":
                clauses.append("cancelado=1")
            elif status == "ENCERRADO":
                clauses.append("cancelado=0 AND data_fim<?"); values.append(today)
            where = " WHERE " + " AND ".join(clauses)
            rows = [dict(r) | {"status": leave_status(dict(r))} for r in c.execute("SELECT * FROM agenda_afastamentos" + where + " ORDER BY data_fim DESC,id DESC LIMIT ? OFFSET ?", [*values, limit + 1, offset])]
        return rows

    def get_leave(self, identifier):
        with self.store.connection(read_only=True) as c:
            row = c.execute("SELECT * FROM agenda_afastamentos WHERE id=?", (identifier,)).fetchone()
            return dict(row) | {"status": leave_status(dict(row))} if row else None

    def leave_substitute_warning(self, substitute_id, start, end, identifier=None):
        """A concurrent substitution is advisory in the first release."""
        if not substitute_id:
            return False
        with self.store.connection(read_only=True) as c:
            return bool(c.execute(
                "SELECT 1 FROM agenda_afastamentos WHERE substituto_id=? AND cancelado=0 AND id<>? AND data_inicio<=? AND data_fim>=?",
                (substitute_id, identifier or "", end, start),
            ).fetchone())

    def save_leave(self, record, *, created_by=None):
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            people = [dict(r) for r in c.execute("SELECT id,nome,funcao,ativo FROM procuradores")]
            identifier = record.get("id") or uuid.uuid4().hex
            conflict = c.execute("SELECT 1 FROM agenda_afastamentos WHERE procurador_id=? AND cancelado=0 AND id<>? AND data_inicio<=? AND data_fim>=?", (record.get("procurador_id"), identifier, record.get("data_fim"), record.get("data_inicio"))).fetchone()
            absent = record.get("substituto_id") and c.execute("SELECT 1 FROM agenda_afastamentos WHERE procurador_id=? AND cancelado=0 AND id<>? AND data_inicio<=? AND data_fim>=?", (record["substituto_id"], identifier, record.get("data_fim"), record.get("data_inicio"))).fetchone()
            validate_leave(record, people, holder_conflict=bool(conflict), substitute_absent=bool(absent))
            stamp = now(); old = c.execute("SELECT criado_em FROM agenda_afastamentos WHERE id=?", (identifier,)).fetchone()
            if record.get("id") and not old: raise ValueError("Afastamento não encontrado.")
            c.execute("""INSERT INTO agenda_afastamentos(id,procurador_id,motivo,motivo_outro,data_inicio,data_fim,substituto_id,observacao,cancelado,criado_por,criado_em,atualizado_em)
            VALUES(?,?,?,?,?,?,?,?,0,?,?,?) ON CONFLICT(id) DO UPDATE SET procurador_id=excluded.procurador_id,motivo=excluded.motivo,motivo_outro=excluded.motivo_outro,data_inicio=excluded.data_inicio,data_fim=excluded.data_fim,substituto_id=excluded.substituto_id,observacao=excluded.observacao,atualizado_em=excluded.atualizado_em""", (identifier,record["procurador_id"],record["motivo"],record.get("motivo_outro") or None,record["data_inicio"],record["data_fim"],record.get("substituto_id"),record.get("observacao") or None,created_by,old[0] if old else stamp,stamp))
            return identifier

    def cancel_leave(self, identifier):
        with self.store.connection() as c:
            c.execute("UPDATE agenda_afastamentos SET cancelado=1,atualizado_em=? WHERE id=?", (now(), identifier))

    def get_commitment_trip(self, compromisso_id, procurador_id):
        with self.store.connection(read_only=True) as c:
            row = c.execute(
                "SELECT * FROM agenda_compromissos_viagens WHERE compromisso_id=? AND procurador_id=?",
                (compromisso_id, procurador_id),
            ).fetchone()
            return dict(row) if row else None

    def trips_for_commitments(self, compromisso_ids):
        identifiers = tuple(dict.fromkeys(compromisso_ids))
        if not identifiers:
            return {}
        placeholders = ",".join("?" for _ in identifiers)
        with self.store.connection(read_only=True) as c:
            rows = c.execute(
                "SELECT * FROM agenda_compromissos_viagens WHERE compromisso_id IN (" + placeholders + ")",
                identifiers,
            )
            result = {}
            for row in rows:
                trip = dict(row)
                result.setdefault(trip["compromisso_id"], {})[trip["procurador_id"]] = trip
            return result

    def upsert_commitment_trip(self, compromisso_id, procurador_id, trip, *, informed_by=None):
        trip = dict(trip)
        self.validate_trip(trip)
        with self.store.connection() as c:
            member = c.execute(
                "SELECT 1 FROM agenda_compromisso_procuradores WHERE compromisso_id=? AND procurador_id=?",
                (compromisso_id, procurador_id),
            ).fetchone()
            if not member:
                raise ValueError("O procurador deve ser membro do compromisso para ter logística de viagem.")
            stamp = now()
            old = c.execute(
                "SELECT motorista_informado,informado_em,informado_por FROM agenda_compromissos_viagens WHERE compromisso_id=? AND procurador_id=?",
                (compromisso_id, procurador_id),
            ).fetchone()
            informed = bool(trip.get("motorista_informado"))
            metadata = (stamp, informed_by) if informed and (not old or not old[0]) else ((old[1], old[2]) if informed else (None, None))
            columns = ("aeroporto_ida", "aeroporto_ida_outro", "ida_data", "ida_hora", "ida_motorista_hora", "aeroporto_volta", "aeroporto_volta_outro", "volta_data", "volta_chegada_hora", "volta_motorista_hora", "observacao")
            values = [trip.get(key) or None for key in columns]
            c.execute("""INSERT INTO agenda_compromissos_viagens(id,compromisso_id,procurador_id,aeroporto_ida,aeroporto_ida_outro,ida_data,ida_hora,ida_motorista_hora,aeroporto_volta,aeroporto_volta_outro,volta_data,volta_chegada_hora,volta_motorista_hora,motorista_informado,informado_em,informado_por,observacao,criado_em,atualizado_em)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(compromisso_id,procurador_id) DO UPDATE SET aeroporto_ida=excluded.aeroporto_ida,aeroporto_ida_outro=excluded.aeroporto_ida_outro,ida_data=excluded.ida_data,ida_hora=excluded.ida_hora,ida_motorista_hora=excluded.ida_motorista_hora,aeroporto_volta=excluded.aeroporto_volta,aeroporto_volta_outro=excluded.aeroporto_volta_outro,volta_data=excluded.volta_data,volta_chegada_hora=excluded.volta_chegada_hora,volta_motorista_hora=excluded.volta_motorista_hora,motorista_informado=excluded.motorista_informado,informado_em=excluded.informado_em,informado_por=excluded.informado_por,observacao=excluded.observacao,atualizado_em=excluded.atualizado_em""", [uuid.uuid4().hex, compromisso_id, procurador_id, *values[:5], *values[5:10], int(informed), *metadata, values[10], stamp, stamp])

    def delete_commitment_trip(self, compromisso_id, procurador_id):
        with self.store.connection() as c:
            c.execute("DELETE FROM agenda_compromissos_viagens WHERE compromisso_id=? AND procurador_id=?", (compromisso_id, procurador_id))

    def get_trip(self, leave_id):
        with self.store.connection(read_only=True) as c:
            row = c.execute("SELECT * FROM agenda_afastamentos_viagens WHERE afastamento_id=?", (leave_id,)).fetchone()
            return self._compatible_trip(dict(row)) if row else None

    @staticmethod
    def _compatible_trip(trip):
        """Expose per-leg airports for pre-migration records as well."""
        for leg in ("ida", "volta"):
            trip.setdefault("aeroporto_" + leg, trip.get("aeroporto"))
            trip.setdefault("aeroporto_" + leg + "_outro", trip.get("aeroporto_outro"))
            if not trip.get("aeroporto_" + leg):
                trip["aeroporto_" + leg] = trip.get("aeroporto")
            if not trip.get("aeroporto_" + leg + "_outro"):
                trip["aeroporto_" + leg + "_outro"] = trip.get("aeroporto_outro")
        return trip

    def trips_for_leaves(self, leave_ids):
        """Fetch optional trips in one query for an Agenda page."""
        identifiers = tuple(dict.fromkeys(leave_ids))
        if not identifiers:
            return {}
        placeholders = ",".join("?" for _ in identifiers)
        with self.store.connection(read_only=True) as c:
            return {
                row["afastamento_id"]: self._compatible_trip(dict(row))
                for row in c.execute(
                    "SELECT * FROM agenda_afastamentos_viagens WHERE afastamento_id IN ("
                    + placeholders + ")",
                    identifiers,
                )
            }

    @staticmethod
    def validate_trip(trip):
        for leg in ("ida", "volta"):
            if not trip.get(leg + "_data"):
                continue
            airport = trip.get("aeroporto_" + leg) or trip.get("aeroporto")
            if airport not in ("João Pessoa", "Recife", "Outro"):
                raise ValueError("Selecione um aeroporto válido.")
            other = trip.get("aeroporto_" + leg + "_outro") or trip.get("aeroporto_outro")
            if airport == "Outro" and not (other or "").strip():
                raise ValueError("Informe o outro aeroporto.")
        for field in ("ida_data", "volta_data"):
            if trip.get(field):
                try:
                    datetime.fromisoformat(trip[field]).date()
                except (TypeError, ValueError):
                    raise ValueError("Informe uma data de viagem válida.") from None
        if trip.get("volta_data") and trip.get("ida_data") and trip["volta_data"] < trip["ida_data"]:
            raise ValueError("A data de volta não pode ser anterior à data de ida.")
        for field in ("ida_hora", "ida_motorista_hora", "volta_chegada_hora", "volta_motorista_hora"):
            value = trip.get(field)
            if value and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise ValueError("Informe horários no formato HH:MM, entre 00:00 e 23:59.")

    def upsert_trip(self, leave_id, trip, *, informed_by=None):
        """Create or update the optional 1:1 air-travel logistics record."""
        trip = dict(trip)
        # Retain the legacy columns as a compatible summary, without using them in UI.
        trip["aeroporto"] = trip.get("aeroporto_ida") or trip.get("aeroporto_volta") or trip.get("aeroporto") or "João Pessoa"
        trip["aeroporto_outro"] = trip.get("aeroporto_ida_outro") or trip.get("aeroporto_volta_outro") or trip.get("aeroporto_outro")
        self.validate_trip(trip)
        with self.store.connection() as c:
            if not c.execute("SELECT 1 FROM agenda_afastamentos WHERE id=?", (leave_id,)).fetchone():
                raise ValueError("Afastamento não encontrado.")
            stamp = now(); old = c.execute("SELECT motorista_informado,informado_em,informado_por FROM agenda_afastamentos_viagens WHERE afastamento_id=?", (leave_id,)).fetchone()
            informed = bool(trip.get("motorista_informado"))
            metadata = (stamp, informed_by) if informed and (not old or not old[0]) else ((old[1], old[2]) if informed else (None, None))
            columns = ("aeroporto","aeroporto_outro","aeroporto_ida","aeroporto_ida_outro","aeroporto_volta","aeroporto_volta_outro","ida_data","ida_hora","ida_companhia","ida_voo","ida_motorista_hora","volta_data","volta_chegada_hora","volta_companhia","volta_voo","volta_motorista_hora","observacao")
            values = [trip.get(key) or None for key in columns]
            c.execute("""INSERT INTO agenda_afastamentos_viagens(afastamento_id,aeroporto,aeroporto_outro,aeroporto_ida,aeroporto_ida_outro,aeroporto_volta,aeroporto_volta_outro,ida_data,ida_hora,ida_companhia,ida_voo,ida_motorista_hora,volta_data,volta_chegada_hora,volta_companhia,volta_voo,volta_motorista_hora,motorista_informado,informado_em,informado_por,observacao,criado_em,atualizado_em)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(afastamento_id) DO UPDATE SET aeroporto=excluded.aeroporto,aeroporto_outro=excluded.aeroporto_outro,aeroporto_ida=excluded.aeroporto_ida,aeroporto_ida_outro=excluded.aeroporto_ida_outro,aeroporto_volta=excluded.aeroporto_volta,aeroporto_volta_outro=excluded.aeroporto_volta_outro,ida_data=excluded.ida_data,ida_hora=excluded.ida_hora,ida_companhia=excluded.ida_companhia,ida_voo=excluded.ida_voo,ida_motorista_hora=excluded.ida_motorista_hora,volta_data=excluded.volta_data,volta_chegada_hora=excluded.volta_chegada_hora,volta_companhia=excluded.volta_companhia,volta_voo=excluded.volta_voo,volta_motorista_hora=excluded.volta_motorista_hora,motorista_informado=excluded.motorista_informado,informado_em=excluded.informado_em,informado_por=excluded.informado_por,observacao=excluded.observacao,atualizado_em=excluded.atualizado_em""", [leave_id, *values[:16], int(informed), *metadata, values[16], stamp, stamp])

    def delete_trip(self, leave_id):
        with self.store.connection() as c:
            c.execute("DELETE FROM agenda_afastamentos_viagens WHERE afastamento_id=?", (leave_id,))

    def trip_alert_window(self, tomorrow):
        """Only tomorrow's non-cancelled, non-ended air-travel legs for the bell."""
        with self.store.connection(read_only=True) as c:
            return [dict(row) for row in c.execute(
                """SELECT v.*,a.id AS compromisso_id,a.payload,p.nome
                FROM agenda_compromissos_viagens v JOIN agenda_compromissos a ON a.id=v.compromisso_id
                JOIN procuradores p ON p.id=v.procurador_id
                WHERE a.situacao NOT IN ('Realizado','Cancelado') AND (v.ida_data=? OR v.volta_data=?)""",
                (tomorrow, tomorrow),
            )]
