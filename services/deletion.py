"""Transactional administrative removal with backups and recoverable file quarantine."""

from pathlib import Path
import hashlib
import json
import re
import uuid
from database.store import now, encode

REASONS = (
    "Portaria criada para teste",
    "Finalização realizada por engano",
    "Erro de lançamento",
    "Ato não emitido oficialmente",
    "Outro",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def stage_file(row, remove, operation):
    source = Path(row["caminho"])
    result = dict(
        caminho=str(source),
        quarentena="",
        sha256=row["sha256"],
        estado="MANTIDO",
        detalhe="Opção de excluir arquivos desmarcada.",
    )
    if not remove:
        return result
    result["detalhe"] = ""
    if not source.exists():
        result.update(
            estado="AUSENTE",
            detalhe="Arquivo vinculado não encontrado; nenhuma remoção necessária.",
        )
        return result
    try:
        if (
            source.is_symlink()
            or source.resolve() != source
            or digest(source) != row["sha256"]
        ):
            result.update(
                estado="MANTIDO_ALTERADO",
                detalhe="Caminho ou conteúdo alterado desde a exportação. Arquivo preservado.",
            )
            return result
        target = source.parent / "excluidos" / operation / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as inp, target.open("xb") as out:
            import shutil

            shutil.copyfileobj(inp, out)
        if digest(target) != row["sha256"]:
            raise OSError("Cópia de quarentena não corresponde ao arquivo registrado.")
        result.update(quarentena=str(target), estado="PENDENTE_REMOCAO")
    except OSError as exc:
        result.update(estado="MANTIDO_ERRO", detalhe=str(exc))
    return result


def finish_files(store, audit_id):
    # The database commit comes first. A crash leaves the original intact and a
    # PENDENTE_REMOCAO entry with a verified quarantine copy, never lost data.
    with store.connection() as c:
        rows = c.execute(
            "SELECT * FROM audit_arquivos WHERE audit_id=? AND estado='PENDENTE_REMOCAO'",
            (audit_id,),
        ).fetchall()
    for row in rows:
        state, detail = "QUARENTENA", ""
        source, target = Path(row["caminho"]), Path(row["quarentena"])
        try:
            if source.exists():
                if (
                    source.is_symlink()
                    or source.resolve() != source
                    or digest(source) != row["sha256"]
                    or digest(target) != row["sha256"]
                ):
                    state, detail = (
                        "MANTIDO_ALTERADO",
                        "Arquivo alterado; original preservado.",
                    )
                else:
                    source.unlink()
        except OSError as exc:
            state, detail = "MANTIDO_ERRO", str(exc)
        with store.connection() as c:
            c.execute(
                "UPDATE audit_arquivos SET estado=?,detalhe=? WHERE id=?",
                (state, detail, row["id"]),
            )


def recalculate_sequence(c, year, removed_number):
    row = c.execute("SELECT ultimo FROM sequencias WHERE ano=?", (year,)).fetchone()
    before = row[0] if row else 0
    if removed_number is None:
        return before, before, False
    maximum = c.execute(
        "SELECT COALESCE(MAX(numero),0) FROM portarias WHERE ano=? AND status IN ('Finalizada','Cancelada')",
        (year,),
    ).fetchone()[0]
    baseline = c.execute(
        "SELECT baseline FROM sequencia_baselines WHERE ano=?", (year,)
    ).fetchone()
    floor = baseline[0] if baseline else 0
    later = maximum > removed_number
    # Never reduce on deletion of an intermediate act, nor cross an external baseline.
    after = max(before, maximum, floor) if later else max(maximum, floor)
    c.execute(
        "INSERT INTO sequencias VALUES(?,?) ON CONFLICT(ano) DO UPDATE SET ultimo=excluded.ultimo",
        (year, after),
    )
    return before, after, later


def delete_portaria(
    store, identifier, reason, confirmed, confirmation, delete_files, draft_only=False
):
    if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
        raise ValueError("Identificador de Portaria inválido.")
    if not confirmed or (not draft_only and confirmation != "EXCLUIR"):
        raise ValueError(
            "Confirme a exclusão e digite EXCLUIR exatamente quando solicitado."
        )
    if not isinstance(reason, str) or not reason.strip() or reason.strip() == "Outro":
        raise ValueError("Informe o motivo da exclusão.")
    from services.placeholders import reject_placeholders

    reject_placeholders(reason)
    staged = []
    operation = uuid.uuid4().hex
    with store.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM portarias WHERE id=?", (identifier,)).fetchone()
        if row is None:
            previous = c.execute(
                "SELECT * FROM audit_log WHERE portaria_id_original=?", (identifier,)
            ).fetchone()
            if previous:
                return {
                    "already_deleted": True,
                    "message": "Esta Portaria já foi excluída.",
                    "audit_id": previous["id"],
                }
            raise ValueError("Portaria não encontrada. Nenhum registro foi alterado.")
        if draft_only != (row["status"] == "Rascunho"):
            raise ValueError(
                "O status mudou. Atualize o Histórico e use a ação correspondente."
            )
        try:
            backup = store.automatic_backup(
                f"antes_exclusao_{row['numero'] or 'rascunho'}_{row['ano']}"
            )
        except Exception as exc:
            raise ValueError(
                "Não foi possível criar o backup de segurança. A Portaria não foi excluída."
            ) from exc
        files = [
            dict(r)
            for r in c.execute(
                "SELECT * FROM exportacoes WHERE portaria_id=?", (identifier,)
            )
        ]
        seq = c.execute(
            "SELECT ultimo FROM sequencias WHERE ano=?", (row["ano"],)
        ).fetchone()
        before = seq[0] if seq else 0
        audit_id = c.execute(
            "INSERT INTO audit_log(data_hora,acao,portaria_id_original,numero,ano,status_anterior,motivo,dados_resumidos,arquivo_docx,arquivo_pdf,backup,sequencia_antes,sequencia_depois) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                now(),
                "EXCLUSAO_RASCUNHO" if draft_only else "EXCLUSAO_DEFINITIVA",
                identifier,
                row["numero"],
                row["ano"],
                row["status"],
                reason.strip(),
                row["payload"],
                encode([r["caminho"] for r in files if r["formato"] == "docx"]),
                encode([r["caminho"] for r in files if r["formato"] == "pdf"]),
                str(backup),
                before,
                before,
            ),
        ).lastrowid
        for f in files:
            staged.append(stage_file(f, delete_files, operation))
        for f in staged:
            c.execute(
                "INSERT INTO audit_arquivos(audit_id,caminho,quarentena,sha256,estado,detalhe) VALUES(?,?,?,?,?,?)",
                (
                    audit_id,
                    f["caminho"],
                    f["quarentena"],
                    f["sha256"],
                    f["estado"],
                    f["detalhe"],
                ),
            )
        c.execute("DELETE FROM exportacoes WHERE portaria_id=?", (identifier,))
        c.execute("DELETE FROM substituicoes WHERE portaria_id=?", (identifier,))
        store.authorize_delete(c, identifier)
        c.execute("DELETE FROM portarias WHERE id=?", (identifier,))
        before, after, later = recalculate_sequence(c, row["ano"], row["numero"])
        c.execute(
            "UPDATE audit_log SET sequencia_depois=? WHERE id=?", (after, audit_id)
        )
        store.event(
            c,
            "exclusao_administrativa",
            {
                "audit_id": audit_id,
                "id": identifier,
                "backup": str(backup),
                "arquivos_vinculados": len(files),
            },
        )
    try:
        finish_files(store, audit_id)
    except Exception:
        import logging

        logging.exception(
            "Exclusão confirmada no banco; confira arquivos pendentes no registro de exclusões."
        )
    if draft_only:
        message = "Rascunho excluído. A sequência não foi alterada."
    elif later:
        message = f"Portaria {row['numero']}/{row['ano']} excluída. A sequência atual não foi reduzida porque existem atos posteriores."
    else:
        message = f"Portaria {row['numero']}/{row['ano']} excluída definitivamente. A próxima numeração prevista é {after+1}/{row['ano']}."
    return dict(
        already_deleted=False,
        audit_id=audit_id,
        backup=str(backup),
        previous=before,
        last=after,
        next_number=after + 1,
        message=message,
    )
