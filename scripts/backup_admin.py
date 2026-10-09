"""Offline recovery / read-only scheduled backup runner.

Run with python -m scripts.backup_admin. Never initializes the source database.
"""

import argparse
from contextlib import contextmanager, closing
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
import sys

from services.access import principal_from_record
from services.backup import generate_backup, _require_admin
from services.backup_format import json_bytes
from services.restore import validate_backup, restore_isolated, CONFIRMATION, VALID
from services.backup_external import (
    download_external_backup,
    load_config,
    publish_backup,
    protection_status,
    verify_external_backup,
)


class ExistingSource:
    """No Store constructor: no migrations, seeds, schema preparation or writes."""

    def __init__(self, *, sqlite_path=None, postgres_schema=None):
        if bool(sqlite_path) == bool(postgres_schema):
            raise ValueError("Escolha exatamente uma origem existente.")
        self.backend = "sqlite" if sqlite_path else "postgresql"
        if sqlite_path:
            self.path = Path(sqlite_path).resolve(strict=True)
            if not self.path.is_file():
                raise ValueError("Origem SQLite inexistente.")
        else:
            from database.postgresql import PostgresBackend

            if postgres_schema in (
                "public",
                "auth",
                "storage",
                "pg_catalog",
                "information_schema",
            ):
                raise ValueError("Informe o schema exclusivo da aplicação.")
            url = os.environ.get("MPC_BACKUP_DATABASE_URL")
            if not url:
                raise ValueError("MPC_BACKUP_DATABASE_URL não configurada no executor.")
            self._postgres = PostgresBackend(url, postgres_schema)

    @contextmanager
    def connection(self, *, read_only=True, isolation=None, statement_timeout=None):
        if not read_only:
            raise ValueError("Executor permite somente leitura da origem.")
        if self.backend == "postgresql":
            with self._postgres.connection(
                read_only=True, isolation=isolation, statement_timeout=statement_timeout
            ) as connection:
                yield connection
        else:
            with closing(
                sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)
            ) as connection:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA query_only=ON")
                yield connection

    def principal(self, email):
        with self.connection() as connection:
            record = connection.execute(
                "SELECT * FROM usuarios_acesso WHERE email=?", (email,)
            ).fetchone()
        if record is None:
            raise ValueError("Administrador não autorizado na origem.")
        principal = principal_from_record(dict(record))
        _require_admin(principal)
        return principal


def offline_principal(authorization_path, *, scope="isolated-sqlite-recovery"):
    """OS-protected recovery authorization; independent of the unavailable source."""
    path = Path(authorization_path).resolve(strict=True)
    if os.name != "nt" and path.stat().st_mode & 0o022:
        raise ValueError("Autorização não pode ser gravável por grupo/outros.")
    record = json.loads(path.read_text(encoding="utf-8-sig"))
    expires = datetime.fromisoformat(record["expires_at"])
    if (
        not record.get("approval_reference")
        or not record.get("actor")
        or expires.tzinfo is None
        or expires <= datetime.now(timezone.utc)
    ):
        raise ValueError("Autorização formal ausente ou expirada.")
    if record.get("scope") != scope:
        raise ValueError("Autorização não permite esta operação isolada.")
    return principal_from_record(
        {
            "id": 0,
            "nome": "Operador de recuperação autorizado",
            "email": record["actor"],
            "perfil": "ADMINISTRADOR",
            "ativo": True,
        }
    )


@contextmanager
def execution_lock(path):
    """Atomic, local single-run lock for the independent scheduler."""
    lock = Path(path).resolve()
    lock.parent.mkdir(parents=True, exist_ok=True)
    descriptor = None
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(descriptor, json_bytes({"started_at": datetime.now(timezone.utc).isoformat(), "pid": os.getpid()}))
        yield
    except FileExistsError:
        raise ValueError("Já existe uma execução de backup externo em andamento.") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
            Path(lock).unlink(missing_ok=True)


def scheduled_backup(args):
    """Generate, certify and publish once under a scheduler-owned lock."""
    config = load_config(args.external_config)
    if config is None:
        raise ValueError("Execução agendada exige configuração externa aprovada.")
    root = Path(args.work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with execution_lock(args.lock_file):
        source = ExistingSource(sqlite_path=args.sqlite, postgres_schema=args.postgres_schema)
        principal = source.principal(args.actor)
        last_error = None
        package = None
        for _ in range(args.prepublish_attempts):
            package = root / ("backup-v2-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + os.urandom(8).hex() + ".zip")
            try:
                generated = generate_backup(source, principal, package, audit=False)
                checked = validate_backup(package, principal, reconstruct=False)
                if not checked.get("integrity_ok") or checked["status"] in ("Inválido", "Legado não certificado"):
                    raise ValueError("Validação prévia à publicação falhou.")
                break
            except Exception as exc:
                last_error = exc
                package.unlink(missing_ok=True)
                package = None
        if package is None:
            raise last_error
        try:
            receipt = publish_backup(package, principal, config)
            return {"status": "publicado", "sha256": generated["sha256"], "receipt": receipt}
        finally:
            # The local artifact is discarded only after publish_backup verified read-back and wrote receipt.
            if package is not None and 'receipt' in locals():
                package.unlink(missing_ok=True)
def parser():
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument(
        "--audit-log", required=True, help="Log local protegido, fora do repositório"
    )
    subs = root.add_subparsers(dest="command", required=True)
    generate = subs.add_parser("generate")
    source = generate.add_mutually_exclusive_group(required=True)
    source.add_argument("--sqlite")
    source.add_argument("--postgres-schema")
    generate.add_argument("--actor", required=True)
    generate.add_argument("--output", required=True)
    generate.add_argument("--external-config")
    scheduled = subs.add_parser("scheduled")
    scheduled_source = scheduled.add_mutually_exclusive_group(required=True)
    scheduled_source.add_argument("--sqlite")
    scheduled_source.add_argument("--postgres-schema")
    scheduled.add_argument("--actor", required=True)
    scheduled.add_argument("--work-dir", required=True)
    scheduled.add_argument("--lock-file", required=True)
    scheduled.add_argument("--external-config", required=True)
    scheduled.add_argument("--prepublish-attempts", type=int, choices=(1, 2, 3), default=2)
    verify = subs.add_parser("verify")
    verify.add_argument("--external-config", required=True)
    download = subs.add_parser("download")
    download.add_argument("--external-config", required=True)
    download.add_argument("--authorization", required=True)
    download.add_argument("--destination", required=True)
    for action in ("validate", "restore", "publish"):
        command = subs.add_parser(action)
        command.add_argument("archive")
        command.add_argument("--authorization", required=True)
        if action == "restore":
            command.add_argument("--workspace", required=True)
            command.add_argument("--confirm", required=True, choices=[CONFIRMATION])
        if action == "publish":
            command.add_argument("--external-config", required=True)
    status = subs.add_parser("status")
    status.add_argument("--external-config")
    return root


def _open_audit(log):
    """Never append audit bytes to a preexisting database or unrelated file."""
    if log.suffix not in (".jsonl", ".log") or log.is_symlink():
        raise ValueError("Log exige arquivo próprio .jsonl/.log, sem link simbólico.")
    try:
        return log.open("xb")
    except FileExistsError:
        stream = log.open("r+b")
        try:
            first = stream.readline(8192)
            if json.loads(first).get("format") != "mpcpb-backup-audit-v1":
                raise ValueError("Arquivo preexistente não é log deste executor.")
            stream.seek(0, 2)
            return stream
        except Exception:
            stream.close()
            raise ValueError(
                "Log não pode alterar arquivo preexistente desconhecido."
            ) from None


def main(argv=None):
    args = parser().parse_args(argv)
    log = Path(args.audit_log)
    log.parent.mkdir(parents=True, exist_ok=True)
    # Verify the audit destination is writable before any backup/restore action.
    with _open_audit(log) as audit:
        start = {
            "time": datetime.now(timezone.utc).isoformat(),
            "operation": args.command,
            "result": "started",
        }
        start["format"] = "mpcpb-backup-audit-v1"
        audit.write(json_bytes(start) + b"\n")
        audit.flush()
        os.fsync(audit.fileno())
        code = 0
        try:
            if args.command == "generate":
                source = ExistingSource(
                    sqlite_path=args.sqlite, postgres_schema=args.postgres_schema
                )
                principal = source.principal(args.actor)
                report = generate_backup(source, principal, args.output, audit=False)
                # Reconstruct SQLite on the runner as part of certification.
                validation = validate_backup(args.output, principal)
                if source.backend == "sqlite" and validation["status"] != VALID:
                    raise ValueError(
                        "Reconstrução de verificação falhou; cópia externa bloqueada."
                    )
                if args.external_config:
                    report = publish_backup(
                        args.output, principal, load_config(args.external_config)
                    )
                else:
                    report = {
                        "sha256": report["sha256"],
                        "status": validation["status"],
                        "external": "Backup externo não configurado",
                    }
            elif args.command == "scheduled":
                report = scheduled_backup(args)
            elif args.command == "verify":
                report = verify_external_backup(load_config(args.external_config))
            elif args.command == "download":
                principal = offline_principal(args.authorization, scope="external-backup-download")
                report = download_external_backup(load_config(args.external_config), principal, args.destination)
            elif args.command == "status":
                report = protection_status(load_config(args.external_config))
                if report["status"] != "Protegido":
                    code = 2
            else:
                principal = offline_principal(args.authorization)
                if args.command == "validate":
                    report = validate_backup(args.archive, principal)
                    if report["status"] != VALID:
                        code = 2
                elif args.command == "restore":
                    report = restore_isolated(
                        args.archive,
                        principal,
                        args.workspace,
                        confirmation=args.confirm,
                    )
                else:
                    report = publish_backup(
                        args.archive, principal, load_config(args.external_config)
                    )
            # No rows, document names, credentials, SQL or connection strings in logs.
            event = {
                "time": datetime.now(timezone.utc).isoformat(),
                "operation": args.command,
                "result": "ok" if code == 0 else "attention",
                "status": report.get("status"),
                "sha256": report.get("sha256"),
            }
            print(json.dumps(report, ensure_ascii=False, indent=2))
        except Exception as exc:
            event = {
                "time": datetime.now(timezone.utc).isoformat(),
                "operation": args.command,
                "result": "failed",
                "error_type": type(exc).__name__,
            }
            print(
                "Operação recusada ou falhou. Confira autorização, cobertura, integridade e configuração; a origem não foi alterada.",
                file=sys.stderr,
            )
            code = 1
        audit.write(json_bytes(event) + b"\n")
        audit.flush()
        os.fsync(audit.fileno())
    return code


if __name__ == "__main__":
    raise SystemExit(main())
