"""SQLite persistence with immutable finalized snapshots and transactional numbering."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
import uuid

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = Path(os.environ.get("MPC_DB_PATH", ROOT / "data" / "mpc.db"))


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False)


class Store:
    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        # Authorization is connection-local and cannot leak to ordinary SQL callers.
        c.create_function("mpc_delete_authorized", 1, lambda identifier: 0)
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def initialize(self):
        with self.connection() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version == 2:
                return
            if version > 2:
                raise ValueError(
                    "Banco criado por versão mais recente. Atualize o aplicativo."
                )
        if version == 1:
            self.migrate()
            return
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise ValueError(
                    "Banco criado por versão mais recente. Atualize o aplicativo."
                )
            c.executescript(
                """
            CREATE TABLE IF NOT EXISTS procuradores (
                id INTEGER PRIMARY KEY, nome TEXT NOT NULL, genero TEXT NOT NULL,
                cargo_base TEXT NOT NULL, funcao TEXT NOT NULL, assento TEXT NOT NULL,
                ativo INTEGER NOT NULL DEFAULT 1, observacoes TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS funcoes (nome TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS assentos (nome TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS motivos_afastamento (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, texto TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS bases_legais (funcao TEXT PRIMARY KEY, texto TEXT NOT NULL, nota TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS configuracoes (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sequencias (ano INTEGER PRIMARY KEY CHECK(ano BETWEEN 1000 AND 9999), ultimo INTEGER NOT NULL CHECK(ultimo>=0));
            CREATE TABLE IF NOT EXISTS portarias (
                id TEXT PRIMARY KEY, numero INTEGER, ano INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('Rascunho','Finalizada','Cancelada')),
                payload TEXT NOT NULL, docx BLOB, pdf BLOB, criada TEXT NOT NULL,
                atualizada TEXT NOT NULL, cancelamento TEXT NOT NULL DEFAULT '',
                UNIQUE(ano,numero), CHECK(numero IS NULL OR numero>0),
                CHECK((status='Rascunho' AND numero IS NULL) OR (status!='Rascunho' AND numero IS NOT NULL)));
            CREATE TABLE IF NOT EXISTS substituicoes (id INTEGER PRIMARY KEY, portaria_id TEXT NOT NULL REFERENCES portarias(id), ordem INTEGER NOT NULL, payload TEXT NOT NULL, UNIQUE(portaria_id,ordem));
            CREATE TABLE IF NOT EXISTS eventos (id INTEGER PRIMARY KEY, instante TEXT NOT NULL, acao TEXT NOT NULL, detalhes TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS manter_portaria BEFORE DELETE ON portarias WHEN OLD.status != 'Rascunho'
                BEGIN SELECT RAISE(ABORT,'Portaria finalizada não pode ser apagada'); END;
            CREATE TRIGGER IF NOT EXISTS manter_ato BEFORE UPDATE OF numero,ano,payload,docx,status ON portarias
                WHEN OLD.status != 'Rascunho' AND (NEW.numero IS NOT OLD.numero OR NEW.ano IS NOT OLD.ano OR NEW.payload IS NOT OLD.payload OR NEW.docx IS NOT OLD.docx OR NEW.status='Rascunho' OR (OLD.status='Cancelada' AND NEW.status!='Cancelada'))
                BEGIN SELECT RAISE(ABORT,'Conteúdo de ato finalizado é imutável'); END;
            """
            )
            c.execute("BEGIN IMMEDIATE")
            if not c.execute(
                "SELECT 1 FROM configuracoes WHERE chave='seeded'"
            ).fetchone():
                seed = json.loads(
                    (ROOT / "database" / "seed.json").read_text(encoding="utf-8")
                )
                c.executemany(
                    "INSERT INTO procuradores(nome,genero,cargo_base,funcao,assento) VALUES(?,?,?,?,?)",
                    seed["procuradores"],
                )
                c.executemany(
                    "INSERT INTO funcoes VALUES(?)", [(x,) for x in seed["funcoes"]]
                )
                c.executemany(
                    "INSERT INTO assentos VALUES(?)", [(x,) for x in seed["assentos"]]
                )
                c.executemany(
                    "INSERT INTO motivos_afastamento(nome,texto) VALUES(?,?)",
                    seed["motivos"],
                )
                c.executemany("INSERT INTO bases_legais VALUES(?,?,?)", seed["bases"])
                c.executemany(
                    "INSERT INTO configuracoes VALUES(?,?)",
                    [
                        ("seeded", "1"),
                        ("pdf_engine", "auto"),
                        ("export_dir", str(ROOT / "exports")),
                        ("admin_number", "0"),
                    ],
                )
            c.execute("PRAGMA user_version=1")
        self.migrate(backup_required=False)

    def migrate(self, backup_required=True):
        from database.migration import migrate_v2

        migrate_v2(self, backup_required)

    def settings(self):
        with self.connection() as c:
            return dict(c.execute("SELECT chave,valor FROM configuracoes").fetchall())

    def configure(self, **values):
        with self.connection() as c:
            c.executemany(
                "INSERT INTO configuracoes VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
                [(k, str(v)) for k, v in values.items()],
            )
            self.event(c, "configurar", values)

    def catalog(self, table):
        if table not in (
            "procuradores",
            "funcoes",
            "assentos",
            "motivos_afastamento",
            "bases_legais",
        ):
            raise ValueError("Catálogo inválido.")
        with self.connection() as c:
            return [dict(r) for r in c.execute(f"SELECT * FROM {table}")]

    def save_member(self, member):
        fields = (
            "nome",
            "genero",
            "cargo_base",
            "funcao",
            "assento",
            "ativo",
            "observacoes",
        )
        if (
            not member["nome"].strip()
            or not member["cargo_base"].strip()
            or not member["funcao"].strip()
        ):
            raise ValueError("Nome, cargo e função são obrigatórios.")
        if member["genero"] not in ("feminino", "masculino"):
            raise ValueError("Gênero inválido.")
        with self.connection() as c:
            values = [member[k] for k in fields]
            if member.get("id"):
                c.execute(
                    "UPDATE procuradores SET nome=?,genero=?,cargo_base=?,funcao=?,assento=?,ativo=?,observacoes=? WHERE id=?",
                    values + [member["id"]],
                )
            else:
                c.execute(
                    "INSERT INTO procuradores(nome,genero,cargo_base,funcao,assento,ativo,observacoes) VALUES(?,?,?,?,?,?,?)",
                    values,
                )
            self.event(c, "cadastro_procurador", member)

    def assign_roles(self, assignments):
        chosen = [item[2] for item in assignments]
        if len(chosen) != len(set(chosen)):
            raise ValueError("Escolha membros distintos para as funções atuais.")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            for function, seat, identifier in assignments:
                if not c.execute(
                    "SELECT 1 FROM procuradores WHERE id=? AND ativo=1", (identifier,)
                ).fetchone():
                    raise ValueError("Escolha um procurador ativo para cada função.")
            for function, seat, identifier in assignments:
                c.execute(
                    "UPDATE procuradores SET funcao='Procurador',assento='Não se aplica' WHERE funcao=? AND assento=?",
                    (function, seat),
                )
            for function, seat, identifier in assignments:
                c.execute(
                    "UPDATE procuradores SET funcao=?,assento=? WHERE id=?",
                    (function, seat, identifier),
                )
            self.event(c, "atribuir_funcoes", assignments)

    def save_reason(self, name, text, identifier=None):
        from services.placeholders import reject_placeholders

        reject_placeholders([name, text])
        if not name.strip() or not text.strip():
            raise ValueError("Nome e redação obrigatórios.")
        with self.connection() as c:
            if identifier:
                c.execute(
                    "UPDATE motivos_afastamento SET nome=?,texto=? WHERE id=?",
                    (name, text, identifier),
                )
            else:
                c.execute(
                    "INSERT INTO motivos_afastamento(nome,texto) VALUES(?,?)",
                    (name, text),
                )

    def save_basis(self, function, text, note):
        from services.placeholders import reject_placeholders

        reject_placeholders([function, text, note])
        if not function.strip() or not text.strip():
            raise ValueError("Função e base legal obrigatórias.")
        with self.connection() as c:
            c.execute(
                "INSERT INTO bases_legais VALUES(?,?,?) ON CONFLICT(funcao) DO UPDATE SET texto=excluded.texto,nota=excluded.nota",
                (function, text, note),
            )

    def next_number(self, year):
        with self.connection() as c:
            row = c.execute(
                "SELECT ultimo FROM sequencias WHERE ano=?", (year,)
            ).fetchone()
            return (row[0] if row else 0) + 1

    def set_sequence(self, year, last, confirmed=False):
        if not confirmed:
            raise ValueError("Confirme o ajuste de numeração.")
        if not 1000 <= year <= 9999 or last < 0:
            raise ValueError("Ano ou número inválido.")
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            maximum = c.execute(
                "SELECT COALESCE(MAX(numero),0) FROM portarias WHERE ano=?", (year,)
            ).fetchone()[0]
            if last < maximum:
                raise ValueError(
                    "O último número não pode ser inferior a uma Portaria já registrada."
                )
            existing_baseline = c.execute(
                "SELECT baseline FROM sequencia_baselines WHERE ano=?", (year,)
            ).fetchone()
            if existing_baseline and last < existing_baseline[0]:
                raise ValueError(
                    "O ajuste não pode apagar a sequência administrativa anterior à implantação."
                )
            c.execute(
                "INSERT INTO sequencias VALUES(?,?) ON CONFLICT(ano) DO UPDATE SET ultimo=excluded.ultimo",
                (year, last),
            )
            # A later administrative advance also reserves externally issued numbers.
            baseline = c.execute(
                "SELECT baseline FROM sequencia_baselines WHERE ano=?", (year,)
            ).fetchone()
            if baseline is None or last > maximum:
                c.execute(
                    "INSERT INTO sequencia_baselines VALUES(?,?) ON CONFLICT(ano) DO UPDATE SET baseline=excluded.baseline",
                    (year, max(last, baseline[0] if baseline else 0)),
                )
            c.execute(
                "INSERT INTO configuracoes VALUES('sequence_confirmed','1') ON CONFLICT(chave) DO UPDATE SET valor='1'"
            )
            self.event(c, "ajustar_sequencia", {"ano": year, "ultimo": last})

    @staticmethod
    def event(c, action, details):
        c.execute(
            "INSERT INTO eventos(instante,acao,detalhes) VALUES(?,?,?)",
            (now(), action, encode(details)),
        )

    def save_draft(self, payload, identifier=None):
        from services.wording import parsed, normalized_payload

        payload = normalized_payload(payload)
        identifier = identifier or uuid.uuid4().hex
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            old = c.execute(
                "SELECT status FROM portarias WHERE id=?", (identifier,)
            ).fetchone()
            if old and old["status"] != "Rascunho":
                raise ValueError(
                    "Este ato já foi finalizado. Duplique para criar um rascunho."
                )
            if (
                not old
                and c.execute(
                    "SELECT 1 FROM audit_log WHERE portaria_id_original=?",
                    (identifier,),
                ).fetchone()
            ):
                raise ValueError(
                    "Esta Portaria já foi excluída. Inicie um novo rascunho."
                )
            c.execute(
                "INSERT INTO portarias(id,ano,status,payload,criada,atualizada) VALUES(?,?,'Rascunho',?,?,?) ON CONFLICT(id) DO UPDATE SET ano=excluded.ano,payload=excluded.payload,atualizada=excluded.atualizada",
                (
                    identifier,
                    parsed(payload["data"]).year,
                    encode(payload),
                    now(),
                    now(),
                ),
            )
            self.replace_substitutions(c, identifier, payload)
        return identifier

    @staticmethod
    def replace_substitutions(c, identifier, payload):
        c.execute("DELETE FROM substituicoes WHERE portaria_id=?", (identifier,))
        c.executemany(
            "INSERT INTO substituicoes(portaria_id,ordem,payload) VALUES(?,?,?)",
            [
                (identifier, i, encode(s))
                for i, s in enumerate(payload["substituicoes"])
            ],
        )

    def get(self, identifier):
        with self.connection() as c:
            row = c.execute(
                "SELECT * FROM portarias WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise ValueError("Portaria não encontrada.")
            result = dict(row)
            result["payload"] = json.loads(result["payload"])
            return result

    def history(self):
        with self.connection() as c:
            rows = c.execute(
                "SELECT id,numero,ano,status,payload,criada,atualizada,cancelamento FROM portarias ORDER BY criada DESC"
            ).fetchall()
            return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def duplicate(self, identifier):
        payload = deepcopy(self.get(identifier)["payload"])
        payload.pop("manual", None)
        return self.save_draft(payload)

    def finalize(self, identifier, number=None, confirmed_warnings=False):
        from services.wording import validate, parsed
        from services.validation import warnings_for
        from document_generator.docx import generate

        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM portarias WHERE id=?", (identifier,)
            ).fetchone()
            if row is None:
                raise ValueError("Salve a prévia antes de finalizar.")
            if row["status"] != "Rascunho":
                return identifier  # Idempotent double-click/retry; never consumes another number.
            if not c.execute(
                "SELECT 1 FROM configuracoes WHERE chave='sequence_confirmed' AND valor='1'"
            ).fetchone():
                raise ValueError("Confirme a última Portaria emitida em Configurações.")
            payload = json.loads(row["payload"])
            validate(payload)
            people = [payload["signatario"]] + [
                s[k]
                for s in payload["substituicoes"]
                for k in ("titular", "substituto")
            ]
            for person in people:
                active = c.execute(
                    "SELECT ativo FROM procuradores WHERE id=?", (person["id"],)
                ).fetchone()
                if not active or not active[0]:
                    raise ValueError(
                        "Há procurador inativo ou excluído. Atualize os dados do rascunho."
                    )
            others = [
                json.loads(r[0])
                for r in c.execute(
                    "SELECT payload FROM portarias WHERE status='Finalizada' AND id!=?",
                    (identifier,),
                )
            ]
            warnings = warnings_for(payload, others)
            if warnings and not confirmed_warnings:
                raise ValueError(
                    "Confirme os avisos antes de finalizar: " + " ".join(warnings)
                )
            year = parsed(payload["data"]).year
            seq = c.execute(
                "SELECT ultimo FROM sequencias WHERE ano=?", (year,)
            ).fetchone()
            next_value = (seq[0] if seq else 0) + 1
            if number is not None:
                admin = c.execute(
                    "SELECT valor FROM configuracoes WHERE chave='admin_number'"
                ).fetchone()
                if not admin or admin[0] != "1" or number < next_value:
                    raise ValueError(
                        "Número administrativo deve estar habilitado e ser igual ou superior ao próximo número."
                    )
            else:
                number = next_value
            docx = generate(payload, number)
            c.execute(
                "UPDATE portarias SET numero=?,ano=?,status='Finalizada',docx=?,atualizada=? WHERE id=?",
                (number, year, docx, now(), identifier),
            )
            c.execute(
                "INSERT INTO sequencias VALUES(?,?) ON CONFLICT(ano) DO UPDATE SET ultimo=excluded.ultimo",
                (year, number),
            )
            self.event(
                c,
                "finalizar",
                {"id": identifier, "numero": number, "ano": year, "avisos": warnings},
            )
        return identifier

    def cache_pdf(self, identifier, content):
        if not content.startswith(b"%PDF-"):
            raise ValueError("PDF inválido.")
        with self.connection() as c:
            c.execute(
                "UPDATE portarias SET pdf=? WHERE id=? AND status!='Rascunho'",
                (content, identifier),
            )

    def cancel(self, identifier, reason, confirmed=False):
        if not confirmed or not reason.strip():
            raise ValueError("Confirme o cancelamento e informe o motivo.")
        with self.connection() as c:
            row = c.execute(
                "SELECT status FROM portarias WHERE id=?", (identifier,)
            ).fetchone()
            if not row or row["status"] != "Finalizada":
                raise ValueError("Somente uma Portaria finalizada pode ser cancelada.")
            c.execute(
                "UPDATE portarias SET status='Cancelada',cancelamento=?,atualizada=? WHERE id=?",
                (reason, now(), identifier),
            )
            self.event(c, "cancelar", {"id": identifier, "motivo": reason})

    def backup(self, destination):
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.touch(exist_ok=False)
        except FileExistsError:
            raise ValueError("O arquivo de backup já existe.")
        try:
            with (
                sqlite3.connect(
                    self.path.resolve().as_uri() + "?mode=ro", uri=True
                ) as source,
                sqlite3.connect(destination) as target,
            ):
                source.backup(target)
                if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Backup não passou na verificação de integridade.")
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def automatic_backup(self, label):
        path = (
            self.path.parent
            / "backups"
            / f"mpc_{datetime.now():%Y%m%d_%H%M%S_%f}_{label}_{uuid.uuid4().hex[:8]}.db"
        )
        self.backup(path)
        return path

    def delete_portaria(
        self, identifier, reason, confirmed=False, confirmation="", delete_files=True
    ):
        from services.deletion import delete_portaria

        return delete_portaria(
            self, identifier, reason, confirmed, confirmation, delete_files
        )

    def delete_draft(self, identifier, confirmed=False, delete_files=True):
        from services.deletion import delete_portaria

        return delete_portaria(
            self,
            identifier,
            "Rascunho descartado pelo usuário",
            confirmed,
            "",
            delete_files,
            draft_only=True,
        )

    def deletion_history(self):
        with self.connection() as c:
            return [
                dict(r) for r in c.execute("SELECT * FROM audit_log ORDER BY id DESC")
            ]

    def baseline(self, year):
        with self.connection() as c:
            row = c.execute(
                "SELECT baseline FROM sequencia_baselines WHERE ano=?", (year,)
            ).fetchone()
            return row[0] if row else 0
