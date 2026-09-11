"""Cached display reads only. All writes and authoritative reads use Store."""

import json
from pathlib import Path

import streamlit as st


@st.cache_data(ttl=30, max_entries=256, show_spinner=False)
def _read(key, operation, args, _store):
    if operation in ("catalog", "settings", "next_number", "baseline"):
        return getattr(_store, operation)(*args)
    with _store.connection(read_only=True) as c:
        if operation == "years":
            return [
                r[0]
                for r in c.execute(
                    "SELECT DISTINCT ano FROM portarias ORDER BY ano DESC"
                )
            ]
        if operation == "warnings":
            return [
                json.loads(r[0])
                for r in c.execute(
                    "SELECT payload FROM portarias WHERE status='Finalizada'"
                )
            ]
        if operation == "history_page":
            year, person, search, offset = args
            clauses, values = [], []
            if year != "Todos":
                clauses.append("ano=?")
                values.append(year)
            if person:
                clauses.append("""(payload::jsonb->'signatario'->>'id' = ? OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements(payload::jsonb->'substituicoes') s
                    WHERE s->'titular'->>'id'=? OR s->'substituto'->>'id'=?))""")
                values.extend([str(person)] * 3)
            if search:
                clauses.append("strpos(lower(payload), lower(?)) > 0")
                values.append(search)
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            rows = c.execute(
                "SELECT id,numero,ano,status,payload,criada,atualizada,cancelamento FROM portarias"
                + where
                + " ORDER BY criada DESC,id DESC LIMIT 51 OFFSET ?",
                [*values, offset],
            ).fetchall()
            result = [dict(row) for row in rows]
            for row in result:
                row["payload"] = json.loads(row["payload"])
            return result
        if operation == "summary":
            row = c.execute(
                "SELECT id,numero,ano,status FROM portarias WHERE id=?", args
            ).fetchone()
            return dict(row) if row else None
    raise ValueError("Leitura de interface inválida")


class DisplayStore:
    def __init__(self, store):
        self._store = store
        # A fragment can outlive a global rerun. Only the TTL cache owns reads.
        store._read_cache = None

    @property
    def schema_key(self):
        return self._store.schema_key

    @property
    def backend(self):
        return self._store.backend

    def __getattr__(self, name):
        return getattr(self._store, name)

    def _cached(self, operation, tables, *args):
        return _read(self._store.read_cache_key(tables), operation, args, self._store)

    def catalog(self, table):
        return self._cached("catalog", (table,), table)

    def settings(self):
        return self._cached("settings", ("configuracoes",))

    def display_number(self, year):
        # Estimate for UI only; finalization always re-reads under its writer lock.
        return self._cached("next_number", ("sequencias",), year)

    def display_baseline(self, year):
        return self._cached("baseline", ("sequencia_baselines",), year)

    def history_years(self):
        return self._cached("years", ("portarias",))

    def history_page(self, year="Todos", person=0, search="", offset=0):
        return self._cached(
            "history_page", ("portarias",), year, person, search, offset
        )

    def warning_payloads(self):
        return self._cached("warnings", ("portarias",))

    def record_summary(self, identifier):
        return self._cached("summary", ("portarias",), identifier)


def display_store(store):
    store = persistence_store(store)
    return DisplayStore(store) if store.backend == "postgresql" else store


def persistence_store(store):
    """Unwrap a display decorator so modules share the same Store instance."""
    if store is None:
        return None
    inner = getattr(store, "_store", None)
    if inner is not None and type(store).__name__ == "DisplayStore":
        return inner
    return store


@st.cache_data(max_entries=8, show_spinner=False)
def _asset(path, modified, size):
    return Path(path).read_bytes()


def asset(path):
    path = Path(path)
    info = path.stat()
    return _asset(str(path), info.st_mtime_ns, info.st_size)
