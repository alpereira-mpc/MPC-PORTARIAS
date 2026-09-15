"""Versioned institutional assignments; never mutate prosecutor catalog roles."""

from datetime import date
import uuid

from database.store import now, unwrap_store


FUNCTIONS = {
    "PROCURADOR_GERAL": "Procurador(a)-Geral do MPC-PB",
    "SUBPROCURADOR_1_CAMARA": "Subprocurador(a)-Geral — 1ª Câmara",
    "SUBPROCURADOR_2_CAMARA": "Subprocurador(a)-Geral — 2ª Câmara",
    "CORREGEDOR": "Corregedor(a) do MPC-PB",
    "OUVIDOR": "Ouvidor(a) do MPC-PB",
}
INITIAL = {
    "PROCURADOR_GERAL": "Elvira Samara Pereira de Oliveira",
    "SUBPROCURADOR_1_CAMARA": "Isabella Barbosa Marinho Falcão",
    "SUBPROCURADOR_2_CAMARA": "Bradson Tibério Luna Camelo",
    "CORREGEDOR": "Manoel Antônio dos Santos Neto",
    "OUVIDOR": "Marcílio Toscano Franca Filho",
}


class InstitutionalFunctions:
    def __init__(self, store):
        self.store = unwrap_store(store)
        self.initialize()

    def initialize(self):
        with self.store.connection() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS funcoes_institucionais (
                id TEXT PRIMARY KEY, funcao TEXT NOT NULL, procurador_id INTEGER NOT NULL REFERENCES procuradores(id),
                data_inicio TEXT NOT NULL, data_fim TEXT, alterado_por TEXT,
                criado_em TEXT NOT NULL, atualizado_em TEXT NOT NULL)""")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS funcao_institucional_vigente_idx ON funcoes_institucionais(funcao) WHERE data_fim IS NULL")
            c.execute("CREATE INDEX IF NOT EXISTS funcao_institucional_hist_idx ON funcoes_institucionais(funcao,data_inicio DESC)")
            for code, name in INITIAL.items():
                if c.execute("SELECT 1 FROM funcoes_institucionais WHERE funcao=?", (code,)).fetchone():
                    continue
                person = c.execute("SELECT id FROM procuradores WHERE nome=?", (name,)).fetchone()
                if person:
                    stamp = now()
                    c.execute("INSERT INTO funcoes_institucionais VALUES(?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, code, person[0], "2026-01-01", None, "migração inicial", stamp, stamp))

    def current(self, function):
        if function not in FUNCTIONS:
            raise ValueError("Função institucional inválida.")
        with self.store.connection(read_only=True) as c:
            row = c.execute("SELECT f.*,p.nome,p.genero FROM funcoes_institucionais f JOIN procuradores p ON p.id=f.procurador_id WHERE f.funcao=? AND f.data_fim IS NULL", (function,)).fetchone()
            return dict(row) if row else None

    def current_all(self):
        return {code: self.current(code) for code in FUNCTIONS}

    def history(self):
        with self.store.connection(read_only=True) as c:
            return [dict(row) for row in c.execute("SELECT f.*,p.nome,p.genero FROM funcoes_institucionais f JOIN procuradores p ON p.id=f.procurador_id ORDER BY f.data_inicio DESC,f.criado_em DESC")]

    def change(self, function, procurador_id, start, actor):
        if function not in FUNCTIONS:
            raise ValueError("Função institucional inválida.")
        start = date.fromisoformat(str(start)).isoformat()
        with self.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            if not c.execute("SELECT 1 FROM procuradores WHERE id=? AND ativo=1", (procurador_id,)).fetchone():
                raise ValueError("Selecione um procurador ativo.")
            old = c.execute("SELECT * FROM funcoes_institucionais WHERE funcao=? AND data_fim IS NULL", (function,)).fetchone()
            if old and old["procurador_id"] == procurador_id:
                raise ValueError("Este procurador já é o titular vigente.")
            stamp = now()
            if old:
                c.execute("UPDATE funcoes_institucionais SET data_fim=?,atualizado_em=? WHERE id=?", (start, stamp, old["id"]))
            identifier = uuid.uuid4().hex
            c.execute("INSERT INTO funcoes_institucionais VALUES(?,?,?,?,?,?,?,?)", (identifier, function, procurador_id, start, None, actor, stamp, stamp))
            return dict(old) if old else None, identifier


def get_current_holder(store, function):
    return InstitutionalFunctions(store).current(function)
