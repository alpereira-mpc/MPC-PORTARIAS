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


def _validate_config(config):
    if not isinstance(config, dict):
        raise ValueError("Configuração externa inválida.")
    required = (
        "approved_by",
        "approval_reference",
        "bucket",
        "prefix",
        "kms_key_id",
        "receipt_path",
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
    path = Path(config["receipt_path"])
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


def protection_status(config=None):
    if config is None:
        return {"status": "Backup externo não configurado", "last_verified": None}
    receipt = Path(config["receipt_path"])
    if not receipt.is_file():
        return {"status": "Nenhuma cópia externa comprovada", "last_verified": None}
    try:
        value = json.loads(receipt.read_text(encoding="utf-8"))
        if (
            value.get("kind") != RECEIPT_KIND
            or not value.get("version_id")
            or value.get("encryption") != "aws:kms"
        ):
            raise ValueError("Recibo incompleto")
        stamp = datetime.fromisoformat(value["verified_at"])
        same_target = value["bucket"] == config["bucket"] and value["key"].startswith(
            config["prefix"].strip("/") + "/"
        )
        if (
            not same_target
            or stamp.tzinfo is None
            or stamp > datetime.now(timezone.utc)
        ):
            raise ValueError("Recibo inválido")
        overdue = datetime.now(timezone.utc) - stamp > timedelta(
            hours=int(config["max_age_hours"])
        )
        return {
            "status": (
                "Cópia externa atrasada"
                if overdue
                else "Cópia externa verificada no horário registrado"
            ),
            "last_verified": value,
        }
    except (ValueError, KeyError, TypeError):
        return {
            "status": "Registro externo inválido; verificar execução",
            "last_verified": None,
        }


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
            ObjectLockMode="COMPLIANCE",
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
        or stored.get("ObjectLockMode") != "COMPLIANCE"
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
