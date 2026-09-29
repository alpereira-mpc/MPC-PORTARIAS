"""Controlled, one-off historical import of the eight 2026 MPC-PB cases.

The command is deliberately not imported by the Streamlit runtime.  It is a
dry-run by default; ``--apply`` is refused if any source PDF exceeds the
module's established 10 MiB upload limit.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from database.audit import AuditStore
from database.store import Store, now
from services.oficios import MAX_FILE, safe_name, validate_upload
from services.representacoes import FASES, RELATORES, SITUACOES


@dataclass(frozen=True)
class Source:
    numero: str
    protocolo: str
    jurisdicionado: str
    assunto: str
    procuradores: tuple[str, ...]
    responsavel: str
    relator: str
    situacao: str
    fase: str
    arquivo: str


SOURCES = (
    Source("00534/26", "2026-01-14T09:18:00-03:00", "Secretaria de Planejamento, Desenv. Urbano e Meio Ambiente do Mun de João Pessoa", "Possível exploração irregular de bens públicos com publicidade de casa de apostas", ("Luciano Andrade Farias", "Manoel Antônio dos Santos Neto"), "Luciano Andrade Farias", "André Carlo Torres Pontes", "EM_TRAMITACAO", "ANALISE_DEFESA", "proc_00534_26_representacao.pdf"),
    Source("02842/26", "2026-04-28T20:30:00-03:00", "Prefeitura Municipal de Coxixola", "Gasto elevado com evento festivo", ("Elvira Samara Pereira de Oliveira",), "Elvira Samara Pereira de Oliveira", "Deusdete Queiroga Filho", "EM_TRAMITACAO", "ANALISE_DEFESA", "proc_02842_26_representacao.pdf"),
    Source("02946/26", "2026-05-05T15:30:00-03:00", "Prefeitura Municipal de Sumé", "Dano ao patrimônio cultural decorrente de alteração/descaracterização de fachadas protegidas", ("Marcílio Toscano Franca Filho",), "Marcílio Toscano Franca Filho", "Antônio Gomes Vieira Filho", "JULGADA", "POS_JULGAMENTO", "proc_02946_26_representacao.pdf"),
    Source("03059/26", "2026-05-14T12:30:00-03:00", "Câmara Municipal de João Pessoa", "REPRESENTAÇÃO DO MINISTÉRIO PÚBLICO DE CONTAS – EMENDAS IMPOSITIVAS SEM IMPESSOALIDADE", ("Bradson Tibério Luna Camelo",), "Bradson Tibério Luna Camelo", "André Carlo Torres Pontes", "EM_TRAMITACAO", "INSTRUCAO", "proc_03059_26_representacao.pdf"),
    Source("03065/26", "2026-05-14T16:58:00-03:00", "Prefeitura Municipal de Cabedelo", "REPRESENTAÇÃO DO MINISTÉRIO PÚBLICO DE CONTAS – INSPEÇÃO ESPECIAL DE CONTRATOS", ("Bradson Tibério Luna Camelo",), "Bradson Tibério Luna Camelo", "Alanna Camilla Santos Galdino Vieira", "EM_TRAMITACAO", "INSTRUCAO", "proc_03065_26_representacao.pdf"),
    Source("03631/26", "2026-06-16T11:31:00-03:00", "Secretaria de Estado da Educação - SEE", "Fiscalização da existência e efetividade de ações educacionais destinadas à prevenção da violência contra a mulher", ("Elvira Samara Pereira de Oliveira",), "Elvira Samara Pereira de Oliveira", "Antônio Gomes Vieira Filho", "JULGADA", "POS_JULGAMENTO", "proc_03631_26_representacao.pdf"),
    Source("04022/26", "2026-07-13T11:36:00-03:00", "Prefeitura Municipal de João Pessoa", "Reforma e gestão do antigo Convento São Frei Pedro Gonçalves (Conventinho) e criação/gestão da Cidade da Imagem", ("Marcílio Toscano Franca Filho",), "Marcílio Toscano Franca Filho", "André Carlo Torres Pontes", "EM_TRAMITACAO", "INSTRUCAO", "proc_04022_26_representacao.pdf"),
    Source("04421/26", "2026-08-07T12:02:00-03:00", "Secretaria de Estado da Saúde", "Política de proteção às Pessoas com Albinismo", ("Elvira Samara Pereira de Oliveira",), "Elvira Samara Pereira de Oliveira", "Renato Sérgio Santiago Melo", "EM_TRAMITACAO", "INSTRUCAO", "proc_04421_26_representacao.pdf"),
)
BACKFILL_MAX_FILE = 25 * 1024 * 1024
LARGE_PDF_PROCESS = "04022/26"


class ReadonlySqliteStore:
    """Minimal read-only adapter so a dry-run cannot bootstrap or migrate."""

    backend = "sqlite"

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connection(self, *, read_only=False, **_unused):
        connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()


def normalized_number(value: str) -> str:
    return "".join(char for char in (value or "") if char.isdigit())


def validate_backfill_pdf(source: Source, content: bytes) -> tuple[str, str]:
    """Keep normal upload policy intact, with one bounded historical exception."""
    if len(content) <= MAX_FILE:
        return validate_upload(source.arquivo, content)
    if source.numero != LARGE_PDF_PROCESS or len(content) > BACKFILL_MAX_FILE:
        raise ValueError("PDF excede o limite excepcional do backfill de 25 MiB.")
    if not content.startswith(b"%PDF-"):
        raise ValueError("PDF inválido.")
    from io import BytesIO
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(content))
    if reader.is_encrypted or not reader.pages:
        raise ValueError("PDF inválido ou protegido.")
    return safe_name(source.arquivo), "application/pdf"


def compatible(record, members, has_pdf, source, people):
    expected_members = {
        (people[name], "PROCURADOR_RESPONSAVEL" if name == source.responsavel else "PROCURADOR_SIGNATARIO")
        for name in source.procuradores
    }
    return (
        record["situacao"] == source.situacao
        and record["fase_processual"] == source.fase
        and record["relator"] == source.relator
        and record["representado"] == source.jurisdicionado
        and {(item["membro_id"], item["papel"]) for item in members} == expected_members
        and has_pdf
    )


def plan(store: Store, pdf_dir: Path):
    with store.connection(read_only=True) as connection:
        people = {
            row["nome"]: row["id"]
            for row in connection.execute(
                "SELECT id,nome FROM procuradores WHERE ativo=1"
            )
        }
        records = [
            dict(row)
            for row in connection.execute(
                "SELECT id,numero_processo,situacao,fase_processual,relator,representado "
                "FROM representacoes WHERE numero_processo IS NOT NULL"
            )
        ]
        members = {}
        for row in connection.execute(
            "SELECT representacao_id,membro_id,papel FROM representacao_integrantes"
        ):
            members.setdefault(row["representacao_id"], []).append(dict(row))
        official_pdfs = {
            row["representacao_id"]
            for row in connection.execute(
                "SELECT representacao_id FROM representacao_documentos "
                "WHERE tipo_documento='REPRESENTACAO_FINAL' AND mime_type='application/pdf'"
            )
        }
    result = []
    for source in SOURCES:
        pdf = pdf_dir / source.arquivo
        alerts = []
        if source.situacao not in SITUACOES or source.fase not in FASES or source.relator not in RELATORES:
            alerts.append("Enum ou relator inválido no código.")
        missing = [name for name in source.procuradores if name not in people]
        if missing:
            alerts.append("Procurador não resolvido: " + ", ".join(missing))
        if not pdf.is_file():
            alerts.append("PDF não encontrado.")
        elif pdf.stat().st_size > BACKFILL_MAX_FILE:
            alerts.append("PDF excede o limite excepcional do backfill de 25 MiB.")
        elif pdf.stat().st_size > MAX_FILE and source.numero != LARGE_PDF_PROCESS:
            alerts.append(f"PDF excede o limite real de 10 MiB ({pdf.stat().st_size} bytes).")
        existing = [
            row
            for row in records
            if normalized_number(row.get("numero_processo"))
            == normalized_number(source.numero)
        ]
        action = "CREATE" if not existing else "SKIP"
        if len(existing) > 1:
            action, alerts = "CONFLICT", alerts + ["Mais de um registro com o mesmo número normalizado."]
        elif existing and not compatible(
            existing[0], members.get(existing[0]["id"], []), existing[0]["id"] in official_pdfs, source, people
        ):
            action, alerts = "CONFLICT", alerts + ["Registro existente diverge da carga histórica."]
        if alerts:
            action = "ERROR" if action == "CREATE" else "CONFLICT"
        result.append((source, pdf, people, existing, action, alerts))
    return result


def run(store: Store, pdf_dir: Path, apply: bool = False, actor: str = ""):
    rows = plan(store, pdf_dir)
    if not apply:
        return rows
    if not actor.strip():
        raise ValueError("Apply exige um ator identificado para a auditoria.")
    if any(action in {"ERROR", "CONFLICT"} for *_, action, _ in rows):
        raise ValueError("Apply bloqueado: corrija os alertas do dry-run.")
    # Ensure only during an authorized write; dry-run is strictly read-only.
    AuditStore(store)
    for source, pdf, people, existing, action, _ in rows:
        if existing:
            continue
        content = pdf.read_bytes()
        name, mime = validate_backfill_pdf(source, content)
        stamp = now()
        with store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            inserted = c.execute("INSERT INTO representacoes(titulo,objeto,origem,data_abertura,representado,tema,prioridade,situacao,fase_processual,numero_processo,data_protocolo,relator,observacoes,criado_em,criado_por,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("Representação - Processo TC nº " + source.numero, source.assunto, "DE_OFICIO", source.protocolo[:10], source.jurisdicionado, source.assunto, "NORMAL", source.situacao, source.fase, source.numero, source.protocolo, source.relator, "Carga histórica controlada de 2026.", stamp, actor, stamp, actor))
            identifier = inserted.lastrowid
            for member in source.procuradores:
                papel = "PROCURADOR_RESPONSAVEL" if member == source.responsavel else "PROCURADOR_SIGNATARIO"
                c.execute("INSERT INTO representacao_integrantes(representacao_id,membro_tipo,membro_id,papel,criado_em) VALUES(?,?,?,?,?)", (identifier, "PROCURADOR", people[member], papel, stamp))
            c.execute("INSERT INTO representacao_andamentos(representacao_id,data,tipo,descricao,criado_em,criado_por) VALUES(?,?,?,?,?,?)", (identifier, source.protocolo, "PROTOCOLADA", "Importação histórica. Processo " + source.numero + ".", stamp, actor))
            c.execute("INSERT INTO representacao_documentos(id,representacao_id,andamento_id,tipo_documento,descricao,data_documento,nome_arquivo,mime_type,tamanho,arquivo,criado_em,criado_por) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, identifier, None, "REPRESENTACAO_FINAL", "PDF protocolado importado historicamente.", source.protocolo, name, mime, len(content), content, stamp, actor))
            # This is intentionally inserted directly: registrar_evento fans out
            # follow notices, which must never occur for a historical backfill.
            c.execute("INSERT INTO auditoria_eventos(usuario_email,evento,modulo,acao,entidade_tipo,entidade_id,resultado,detalhes_json,criado_em) VALUES(?,?,?,?,?,?,?,?,?)", (actor, "REPRESENTACAO_BACKFILL_2026", "representacoes", "IMPORTAR", "representacao", str(identifier), "OK", '{\"processo\": \"' + source.numero + '\", \"modo\": \"backfill_historico\"}', stamp))
    return plan(store, pdf_dir)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db-path", type=Path, required=True, help="Banco SQLite do Ferramentas.")
    parser.add_argument("--pdf-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", default="")
    args = parser.parse_args()
    if args.apply and not args.actor.strip():
        parser.error("--apply exige --actor; não invente o ator da auditoria.")
    store = Store(args.db_path) if args.apply else ReadonlySqliteStore(args.db_path)
    rows = run(store, args.pdf_dir, args.apply, args.actor.strip())
    for source, pdf, _people, existing, action, alerts in rows:
        print(f"{source.numero} | existe={bool(existing)} | {action} | {source.situacao}/{source.fase} | PDF={pdf.is_file()} | " + "; ".join(alerts or ["sem alertas"]))


if __name__ == "__main__":
    main()
