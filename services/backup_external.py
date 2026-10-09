"""Opt-in external storage. No credentials, provisioning, deletion or scheduler."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import base64
import json
import os
import uuid

from services.backup import _require_admin
from services.backup_format import file_hash, json_bytes
from services.restore import validate_backup, INVALID, LEGACY, MAX_ARCHIVE


RECEIPT_KIND = "mpcpb-external-receipt-v1"
MONITOR_KIND = "mpcpb-external-monitor-v1"


def _validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Configuração externa inválida.")
    required = (
        "approved_by",
        "approval_reference",
        "bucket",
        "prefix",
        "kms_key_id",
        "object_lock_mode",
        "receipt_path",
        "monitor_path",
        "retention_days",
        "max_age_hours",
    )
    if config.get("provider") != "s3" or any(not config.get(k) for k in required):
        raise ValueError("Configuração externa incompleta ou não aprovada.")
    if (
        not 1 <= int(config["retention_days"]) <= 36500
        or not 1 <= int(config["max_age_hours"]) <= 8760
    ):
        raise ValueError("Retenção ou prazo inválido.")
    if not config["prefix"].strip("/"):
        raise ValueError("Prefixo externo vazio.")
    if config["object_lock_mode"] not in ("GOVERNANCE", "COMPLIANCE"):
        raise ValueError("Modo Object Lock inválido.")
    if config["object_lock_mode"] == "COMPLIANCE" and not config.get("compliance_approval_reference"):
        raise ValueError("Object Lock COMPLIANCE exige aprovação institucional específica.")
    path = Path(config["receipt_path"])
    monitor = Path(config["monitor_path"])
    if monitor.suffix != ".json" or monitor.is_symlink():
        raise ValueError("Monitor exige arquivo JSON próprio, sem link simbólico.")
    if path.suffix != ".json" or path.is_symlink():
        raise ValueError("Recibo exige arquivo JSON próprio, sem link simbólico.")
    if path.exists():
        try:
            if (
                path.stat().st_size > 65536
                or json.loads(path.read_text(encoding="utf-8")).get("kind")
                != RECEIPT_KIND
            ):
                raise ValueError("Arquivo preexistente não é um recibo deste serviço.")
        except (UnicodeError, AttributeError):
            raise ValueError(
                "Recibo não pode substituir arquivos preexistentes."
            ) from None
    return config


def load_config(path=None):
    configured = path or os.environ.get("MPC_BACKUP_EXTERNAL_CONFIG")
    if not configured:
        return None
    config = json.loads(Path(configured).read_text(encoding="utf-8-sig"))
    return _validate_config(config)



def _read_record(path, kind):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 65536:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value.get("kind") == kind else None
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _write_record(path, value, kind):
    path = Path(path)
    if path.suffix != ".json" or path.is_symlink():
        raise ValueError("Registro externo exige arquivo JSON próprio, sem link simbólico.")
    if path.exists() and _read_record(path, kind) is None:
        raise ValueError("Registro externo não pode substituir arquivo desconhecido.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(json_bytes(value))
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _verified_object(client, receipt):
    stored = client.get_object(
        Bucket=receipt["bucket"], Key=receipt["key"], VersionId=receipt["version_id"]
    )
    import hashlib

    body = stored["Body"]
    try:
        calculated = hashlib.sha256()
        size = 0
        while chunk := body.read(1024 * 1024):
            calculated.update(chunk)
            size += len(chunk)
    finally:
        body.close()
    if calculated.hexdigest() != receipt["sha256"] or size != receipt["bytes"]:
        raise ValueError("Objeto externo diverge do recibo.")
    if (
        stored.get("ServerSideEncryption") != "aws:kms"
        or stored.get("ObjectLockMode") not in ("GOVERNANCE", "COMPLIANCE")
        or not stored.get("ObjectLockRetainUntilDate")
    ):
        raise ValueError("Proteções do objeto externo não foram comprovadas.")
    return stored


def verify_external_backup(config, *, client=None):
    """Controlled remote read-back; writes durable evidence for the UI/monitor."""
    config = _validate_config(config)
    receipt = _read_record(config["receipt_path"], RECEIPT_KIND)
    if receipt is None:
        raise ValueError("Não existe recibo externo verificável.")
    result = {"kind": MONITOR_KIND, "checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        client = client or _client(config)
        if client.get_bucket_versioning(Bucket=config["bucket"]).get("Status") != "Enabled":
            raise ValueError("Versionamento obrigatório no destino.")
        lock = client.get_object_lock_configuration(Bucket=config["bucket"])["ObjectLockConfiguration"]
        if lock.get("ObjectLockEnabled") != "Enabled":
            raise ValueError("Object Lock obrigatório no destino.")
        stored = _verified_object(client, receipt)
        if stored.get("SSEKMSKeyId") != config["kms_key_id"]:
            raise ValueError("Chave KMS do objeto diverge da configuração aprovada.")
        result.update({"result": "ok", "bucket": receipt["bucket"], "key": receipt["key"], "version_id": receipt["version_id"], "sha256": receipt["sha256"], "bytes": receipt["bytes"]})
    except Exception as exc:
        result.update({"result": "failed", "error_type": type(exc).__name__})
        _write_record(config["monitor_path"], result, MONITOR_KIND)
        raise
    _write_record(config["monitor_path"], result, MONITOR_KIND)
    return result


def download_external_backup(config, principal, destination, *, client=None):
    """Download one receipt-pinned version into a new isolated file and validate V2."""
    _require_admin(principal)
    config = _validate_config(config)
    receipt = _read_record(config["receipt_path"], RECEIPT_KIND)
    target = Path(destination).resolve()
    if receipt is None or target.suffix != ".zip" or target.exists() or target.is_symlink():
        raise ValueError("Destino de recuperação deve ser um ZIP novo e recibo deve ser válido.")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        client = client or _client(config)
        stored = _verified_object(client, receipt)
        if (
            stored.get("SSEKMSKeyId") != config["kms_key_id"]
            or stored.get("ObjectLockMode") != config["object_lock_mode"]
        ):
            raise ValueError("Proteções da versão externa divergem da configuração aprovada.")
        # Read a second pinned version only after remote integrity was checked.
        stored = client.get_object(
            Bucket=receipt["bucket"], Key=receipt["key"], VersionId=receipt["version_id"]
        )
        body = stored["Body"]
        import hashlib
        digest = hashlib.sha256()
        size = 0
        try:
            with temporary.open("xb") as output:
                while chunk := body.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_ARCHIVE:
                        raise ValueError("Objeto externo excede o limite permitido.")
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        finally:
            body.close()
        if size != receipt["bytes"] or digest.hexdigest() != receipt["sha256"]:
            raise ValueError("Download externo diverge do recibo.")
        report = validate_backup(temporary, principal, reconstruct=False)
        if not report.get("integrity_ok") or report["status"] in (INVALID, LEGACY):
            raise ValueError("Pacote externo não passou na validação V2.")
        temporary.replace(target)
        return {"path": str(target), "sha256": receipt["sha256"], "bytes": size, "validation": report["status"]}
    finally:
        temporary.unlink(missing_ok=True)
def protection_status(config=None):
    if config is None:
        return {"status": "Não configurado", "last_verified": None, "monitor": None}
    receipt = _read_record(config["receipt_path"], RECEIPT_KIND)
    if receipt is None:
        return {"status": "Atenção: nenhuma cópia externa comprovada", "last_verified": None, "monitor": None}
    try:
        stamp = datetime.fromisoformat(receipt["verified_at"])
        same_target = receipt["bucket"] == config["bucket"] and receipt["key"].startswith(config["prefix"].strip("/") + "/")
        if not receipt.get("version_id") or receipt.get("encryption") != "aws:kms" or stamp.tzinfo is None or stamp > datetime.now(timezone.utc) or not same_target:
            raise ValueError("Recibo inválido")
    except (ValueError, KeyError, TypeError):
        return {"status": "Falha: recibo externo inválido", "last_verified": None, "monitor": None}
    monitor = _read_record(config["monitor_path"], MONITOR_KIND)
    if monitor is None:
        return {"status": "Atenção: cópia aguarda verificação remota", "last_verified": receipt, "monitor": None}
    try:
        checked = datetime.fromisoformat(monitor["checked_at"])
        current = datetime.now(timezone.utc)
        overdue = current - stamp > timedelta(hours=int(config["max_age_hours"]))
        monitor_late = checked.tzinfo is None or checked > current or current - checked > timedelta(hours=int(config["max_age_hours"]))
        matches = all(monitor.get(key) == receipt.get(key) for key in ("bucket", "key", "version_id", "sha256", "bytes"))
        if monitor.get("result") != "ok" or not matches:
            return {"status": "Falha: verificação remota não comprovada", "last_verified": receipt, "monitor": monitor}
        if overdue or monitor_late:
            return {"status": "Atenção: backup ou verificação remota atrasados", "last_verified": receipt, "monitor": monitor}
        return {"status": "Protegido", "last_verified": receipt, "monitor": monitor}
    except (ValueError, KeyError, TypeError):
        return {"status": "Falha: registro de monitoramento inválido", "last_verified": receipt, "monitor": None}
def _client(config):
    try:
        import boto3
    except ImportError:
        raise ValueError(
            "Instale boto3 no executor externo aprovado; adaptador está inativo."
        ) from None
    # Standard credential chain; do not accept keys or arbitrary HTTP endpoints.
    return boto3.client("s3", region_name=config.get("region"), use_ssl=True)


def publish_backup(path, principal, config, *, client=None):
    _require_admin(principal)
    with TemporaryDirectory(prefix="mpc-publish-") as workspace:
        snapshot = Path(workspace) / "snapshot.zip"
        with open(path, "rb") as source, snapshot.open("xb") as output:
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE:
                    raise ValueError("ZIP excede limite.")
                output.write(chunk)
        return _publish_snapshot(snapshot, principal, config, client=client)


def _publish_snapshot(path, principal, config, *, client=None):
    _require_admin(principal)
    if (
        not config
        or not config.get("approval_reference")
        or not config.get("approved_by")
    ):
        raise ValueError("Backup externo não configurado")
    config = _validate_config(config)
    report = validate_backup(path, principal, reconstruct=False)
    if not report.get("integrity_ok") or report["status"] in (INVALID, LEGACY):
        raise ValueError("Somente backups V2 íntegros podem ser armazenados.")
    client = client or _client(config)
    bucket = config["bucket"]
    if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
        raise ValueError("Versionamento obrigatório no destino.")
    lock = client.get_object_lock_configuration(Bucket=bucket)[
        "ObjectLockConfiguration"
    ]
    if lock.get("ObjectLockEnabled") != "Enabled":
        raise ValueError("Object Lock obrigatório no destino.")
    now = datetime.now(timezone.utc)
    retain_until = now + timedelta(days=int(config["retention_days"]))
    key = config["prefix"].strip("/") + f"/{now:%Y/%m/%d}/{uuid.uuid4().hex}.zip"
    digest = file_hash(path)
    with open(path, "rb") as source:
        result = client.put_object(
            Bucket=bucket,
            Key=key,
            Body=source,
            ContentLength=Path(path).stat().st_size,
            ContentType="application/zip",
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=config["kms_key_id"],
            ObjectLockMode=config["object_lock_mode"],
            ObjectLockRetainUntilDate=retain_until,
            ChecksumAlgorithm="SHA256",
            ChecksumSHA256=base64.b64encode(bytes.fromhex(digest)).decode("ascii"),
            IfNoneMatch="*",
        )
    version = result.get("VersionId")
    if not version or version == "null":
        raise ValueError("Destino não comprovou versão imutável.")
    stored = client.get_object(Bucket=bucket, Key=key, VersionId=version)
    import hashlib

    body = stored["Body"]
    try:
        calculated = hashlib.sha256()
        size = 0
        while chunk := body.read(1024 * 1024):
            calculated.update(chunk)
            size += len(chunk)
    finally:
        body.close()
    if calculated.hexdigest() != digest or size != Path(path).stat().st_size:
        raise ValueError("Verificação após armazenamento falhou.")
    if (
        stored.get("ServerSideEncryption") != "aws:kms"
        or stored.get("SSEKMSKeyId") != config["kms_key_id"]
        or stored.get("ObjectLockMode") != config["object_lock_mode"]
        or stored.get("ObjectLockRetainUntilDate", now)
        < retain_until.replace(microsecond=0)
    ):
        raise ValueError("Criptografia ou retenção não comprovada.")
    receipt = {
        "kind": RECEIPT_KIND,
        "verified_at": now.isoformat(),
        "bucket": bucket,
        "key": key,
        "version_id": version,
        "sha256": digest,
        "bytes": size,
        "retain_until": retain_until.isoformat(),
        "encryption": "aws:kms",
        "approval_reference": config["approval_reference"],
    }
    path = Path(config["receipt_path"])
    monitor = Path(config["monitor_path"])
    if monitor.suffix != ".json" or monitor.is_symlink():
        raise ValueError("Monitor exige arquivo JSON próprio, sem link simbólico.")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as output:
            output.write(json_bytes(receipt))
            output.flush()
            os.fsync(output.fileno())
        _validate_config(config)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return receipt
