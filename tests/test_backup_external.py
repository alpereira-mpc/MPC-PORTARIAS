from datetime import datetime, timedelta, timezone
from pathlib import Path
import io
import json
import pytest

from services.backup_external import publish_backup, protection_status
from services.backup import generate_backup
from scripts.backup_admin import ExistingSource, main
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
        assert kwargs["ObjectLockMode"] == "COMPLIANCE"
        assert kwargs["IfNoneMatch"] == "*"
        self.data = kwargs["Body"].read()
        self.retention = kwargs["ObjectLockRetainUntilDate"]
        self.kms_key = kwargs["SSEKMSKeyId"]
        return {"VersionId": "synthetic-version"}

    def get_object(self, **kwargs):
        assert kwargs["VersionId"] == "synthetic-version"
        return {
            "Body": io.BytesIO(b"bad" if self.corrupt else self.data),
            "ServerSideEncryption": "aws:kms",
            "SSEKMSKeyId": self.kms_key,
            "ObjectLockMode": "COMPLIANCE",
            "ObjectLockRetainUntilDate": self.retention,
        }


def config(tmp_path):
    return {
        "provider": "s3",
        "bucket": "institutional-test",
        "prefix": "mpc",
        "kms_key_id": "test-kms",
        "approved_by": "synthetic",
        "approval_reference": "TEST-ONLY",
        "retention_days": 30,
        "max_age_hours": 25,
        "receipt_path": str(tmp_path / "receipt.json"),
    }


def test_external_disabled_receipt_verification_and_overdue(store, tmp_path):
    assert protection_status()["status"] == "Backup externo não configurado"
    _prepare(store)
    path = tmp_path / "backup.zip"
    generate_backup(store, _principal(store), path)
    cfg = config(tmp_path)
    assert protection_status(cfg)["last_verified"] is None
    storage = Storage()
    receipt = publish_backup(path, _principal(store), cfg, client=storage)
    assert receipt["version_id"] == "synthetic-version"
    assert storage.puts == 1
    assert protection_status(cfg)["last_verified"]["sha256"] == receipt["sha256"]
    receipt["verified_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    Path(cfg["receipt_path"]).write_text(json.dumps(receipt))
    assert protection_status(cfg)["status"] == "Cópia externa atrasada"


@pytest.mark.parametrize("corrupt,locked", [(True, True), (False, False)])
def test_external_failure_never_records_success(store, tmp_path, corrupt, locked):
    _prepare(store)
    path = tmp_path / "backup.zip"
    generate_backup(store, _principal(store), path)
    cfg = config(tmp_path)
    with pytest.raises(ValueError):
        publish_backup(path, _principal(store), cfg, client=Storage(corrupt, locked))
    assert not Path(cfg["receipt_path"]).exists()


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
