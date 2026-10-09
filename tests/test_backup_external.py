from datetime import datetime, timedelta, timezone
from pathlib import Path
import io
import json
import pytest

from services.backup_external import (
    download_external_backup,
    publish_backup,
    protection_status,
    verify_external_backup,
)
from services.backup import generate_backup
from scripts.backup_admin import ExistingSource, execution_lock, main, scheduled_backup
from tests.test_backup import _prepare, _principal


class Storage:
    def __init__(self, corrupt=False, locked=True):
        self.corrupt, self.locked = corrupt, locked
        self.puts = 0

    def get_bucket_versioning(self, **kwargs):
        return {"Status": "Enabled"}

    def get_object_lock_configuration(self, **kwargs):
        return {
            "ObjectLockConfiguration": {
                "ObjectLockEnabled": "Enabled" if self.locked else "Disabled"
            }
        }

    def put_object(self, **kwargs):
        self.puts += 1
        assert kwargs["ServerSideEncryption"] == "aws:kms"
        assert kwargs["ObjectLockMode"] == "GOVERNANCE"
        assert kwargs["IfNoneMatch"] == "*"
        self.data = kwargs["Body"].read()
        self.retention = kwargs["ObjectLockRetainUntilDate"]
        self.kms_key = kwargs["SSEKMSKeyId"]
        self.lock_mode = kwargs["ObjectLockMode"]
        return {"VersionId": "synthetic-version"}

    def get_object(self, **kwargs):
        assert kwargs["VersionId"] == "synthetic-version"
        return {
            "Body": io.BytesIO(b"bad" if self.corrupt else self.data),
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self.kms_key,
            "ObjectLockMode": self.lock_mode,
            "ObjectLockRetainUntilDate": self.retention,
        }


def config(tmp_path):
    return {
        "provider": "s3",
        "bucket": "institutional-test",
        "prefix": "mpc",
        "kms_key_id": "test-kms",
        "object_lock_mode": "GOVERNANCE",
        "approved_by": "synthetic",
        "approval_reference": "TEST-ONLY",
        "retention_days": 30,
        "max_age_hours": 25,
        "receipt_path": str(tmp_path / "receipt.json"),
        "monitor_path": str(tmp_path / "monitor.json"),
    }


def test_external_disabled_receipt_verification_and_overdue(store, tmp_path):
    assert protection_status()["status"] == "Não configurado"
    _prepare(store)
    path = tmp_path / "backup.zip"
    generate_backup(store, _principal(store), path)
    cfg = config(tmp_path)
    assert protection_status(cfg)["last_verified"] is None
    storage = Storage()
    receipt = publish_backup(path, _principal(store), cfg, client=storage)
    assert receipt["version_id"] == "synthetic-version"
    assert storage.puts == 1
    assert protection_status(cfg)["status"] == "Atenção: cópia aguarda verificação remota"
    monitored = verify_external_backup(cfg, client=storage)
    assert monitored["result"] == "ok"
    assert protection_status(cfg)["status"] == "Protegido"
    receipt["verified_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    Path(cfg["receipt_path"]).write_text(json.dumps(receipt))
    assert protection_status(cfg)["status"] == "Atenção: backup ou verificação remota atrasados"


@pytest.mark.parametrize("corrupt,locked", [(True, True), (False, False)])
def test_external_failure_never_records_success(store, tmp_path, corrupt, locked):
    _prepare(store)
    path = tmp_path / "backup.zip"
    generate_backup(store, _principal(store), path)
    cfg = config(tmp_path)
    with pytest.raises(ValueError):
        publish_backup(path, _principal(store), cfg, client=Storage(corrupt, locked))
    assert not Path(cfg["receipt_path"]).exists()



def test_execution_lock_rejects_concurrency_and_releases(tmp_path):
    lock = tmp_path / "scheduled.lock"
    with execution_lock(lock):
        with pytest.raises(ValueError, match="em andamento"):
            with execution_lock(lock):
                pass
    assert not lock.exists()


def test_compliance_requires_specific_institutional_approval(tmp_path):
    from services.backup_external import _validate_config

    cfg = config(tmp_path)
    cfg["object_lock_mode"] = "COMPLIANCE"
    with pytest.raises(ValueError, match="aprovação institucional específica"):
        _validate_config(cfg)
    cfg["compliance_approval_reference"] = "ATO-ESPECIFICO"
    assert _validate_config(cfg)["object_lock_mode"] == "COMPLIANCE"

def test_external_download_is_pinned_validated_and_never_overwrites(store, tmp_path):
    _prepare(store)
    package = tmp_path / "backup.zip"
    generate_backup(store, _principal(store), package)
    cfg = config(tmp_path)
    storage = Storage()
    publish_backup(package, _principal(store), cfg, client=storage)
    destination = tmp_path / "isolated-download.zip"
    downloaded = download_external_backup(
        cfg, _principal(store), destination, client=storage
    )
    assert destination.is_file()
    assert downloaded["sha256"]
    with pytest.raises(ValueError):
        download_external_backup(cfg, _principal(store), destination, client=storage)

def test_remote_monitor_failure_is_recorded_after_a_valid_publication(store, tmp_path):
    _prepare(store)
    package = tmp_path / "backup.zip"
    generate_backup(store, _principal(store), package)
    cfg = config(tmp_path)
    storage = Storage()
    publish_backup(package, _principal(store), cfg, client=storage)
    storage.corrupt = True
    with pytest.raises(ValueError):
        verify_external_backup(cfg, client=storage)
    assert protection_status(cfg)["status"] == "Falha: verificação remota não comprovada"
    assert Path(cfg["monitor_path"]).is_file()

def test_scheduled_backup_uses_lock_unique_staging_and_publishes_once(store, tmp_path, monkeypatch):
    _prepare(store)
    cfg_path = tmp_path / "external.json"
    cfg_path.write_text(json.dumps(config(tmp_path)), encoding="utf-8")
    import scripts.backup_admin as runner

    published = []
    def publish(path, principal, cfg):
        published.append(Path(path))
        assert Path(path).is_file()
        return {"version_id": "synthetic", "sha256": "0" * 64}
    monkeypatch.setattr(runner, "publish_backup", publish)
    args = type("Args", (), {
        "external_config": str(cfg_path), "work_dir": str(tmp_path / "staging"),
        "lock_file": str(tmp_path / "runner.lock"), "sqlite": str(store.path),
        "postgres_schema": None, "actor": _principal(store).email,
        "prepublish_attempts": 1,
    })()
    result = scheduled_backup(args)
    assert result["status"] == "publicado"
    assert len(published) == 1
    assert not list((tmp_path / "staging").glob("*.zip"))
    assert not Path(args.lock_file).exists()

def test_cli_existing_source_is_read_only_and_no_migrations(
    store, tmp_path, monkeypatch
):
    source = ExistingSource(sqlite_path=store.path)
    with source.connection() as c:
        with pytest.raises(Exception):
            c.execute("CREATE TABLE forbidden(id INTEGER)")
    with pytest.raises(ValueError):
        with source.connection(read_only=False):
            pass
    with pytest.raises(FileNotFoundError):
        ExistingSource(sqlite_path=tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()
    _prepare(store)
    import database.store

    monkeypatch.setattr(
        database.store,
        "Store",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("No initialization")),
    )
    generate_backup(
        source,
        source.principal(_principal(store).email),
        tmp_path / "readonly.zip",
        audit=False,
    )


def test_offline_cli_recovery_without_original_database(store, tmp_path, capsys):
    _prepare(store)
    package = tmp_path / "backup.zip"
    generate_backup(store, _principal(store), package)
    auth = tmp_path / "authorization.json"
    auth.write_text(
        json.dumps(
            {
                "actor": "operator@test",
                "approval_reference": "TEST-ONLY",
                "scope": "isolated-sqlite-recovery",
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=1)
                ).isoformat(),
            }
        )
    )
    log = tmp_path / "recovery.log"
    args = [
        "--audit-log",
        str(log),
        "restore",
        str(package),
        "--authorization",
        str(auth),
        "--workspace",
        str(tmp_path),
        "--confirm",
        "RESTAURAR EM SQLITE ISOLADO",
    ]
    assert main(args) == 0
    report = json.loads(capsys.readouterr().out)
    assert Path(report["restore"]["destination"]).exists()
    events = [json.loads(line) for line in log.read_text().splitlines()]
    assert events[-1]["result"] == "ok"
    auth.write_text("{}")
    assert main(args) == 1
    assert "failed" in log.read_text()


def test_log_and_receipt_never_modify_unknown_existing_files(store, tmp_path):
    from scripts.backup_admin import _open_audit
    from services.backup_external import load_config

    for name in ("operational.log", "receipt.json"):
        target = tmp_path / name
        payload = b"SQLite format 3\x00synthetic database"
        target.write_bytes(payload)
        if name.endswith(".log"):
            with pytest.raises(ValueError):
                _open_audit(target)
        else:
            cfg = config(tmp_path)
            config_path = tmp_path / "config.json"
            config_path.write_text(json.dumps(cfg))
            with pytest.raises(ValueError):
                load_config(config_path)
        assert target.read_bytes() == payload
