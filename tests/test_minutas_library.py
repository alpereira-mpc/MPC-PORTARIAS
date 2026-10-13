from copy import deepcopy
from io import BytesIO
import re

import pytest
from docx import Document
from pypdf import PdfWriter

from database.minutas_referencias import COLUMNS
from database.store import ROOT
from services.minutas_library import (
    MinutasAccessDenied,
    MinutasDuplicateError,
    MinutasLibraryService,
    MinutasValidationError,
    PROCURADORA,
)
from services.oficios import MAX_FILE
from tests.access_testing import TEST_IDENTITY


SECRETS = {
    "MINUTAS_PILOTO_ENABLED": True,
    "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]],
}


def _pdf(label=b""):
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.add_metadata({"/Subject": label.decode("ascii", errors="ignore")})
    writer.write(output)
    return output.getvalue()


def _docx():
    output = BytesIO()
    document = Document()
    document.add_paragraph("Parecer definitivo")
    document.save(output)
    return output.getvalue()


class FakeRepository:
    def __init__(self, events=None):
        self.records = {}
        self.events = events if events is not None else []
        self.fail_create = False
        self.fail_replace = False
        self.fail_delete = False

    def get_by_process(self, procuradora_key, process_key):
        return next(
            (
                deepcopy(record)
                for record in self.records.values()
                if record["procuradora_chave"] == procuradora_key
                and record["processo_chave"] == process_key
            ),
            None,
        )

    def create(self, record):
        self.events.append(("db_create", record["id"]))
        if self.fail_create:
            raise RuntimeError("database unavailable")
        self.records[record["id"]] = deepcopy(record)
        return deepcopy(record)

    def get(self, identifier):
        record = self.records.get(identifier)
        return deepcopy(record) if record else None

    def list(self):
        return [deepcopy(record) for record in self.records.values()]

    def replace_document(self, identifier, kind, document, actor):
        self.events.append(("db_replace", document["path"]))
        if self.fail_replace:
            raise RuntimeError("database unavailable")
        record = self.records[identifier]
        for field in ("name", "mime", "size", "path"):
            column = {"name": "nome", "size": "tamanho"}.get(field, field)
            record[f"{kind}_{column}"] = document[field]
        record["atualizado_por"] = actor
        return deepcopy(record)

    def delete(self, identifier):
        self.events.append(("db_delete", identifier))
        if self.fail_delete:
            raise RuntimeError("database unavailable")
        del self.records[identifier]


class FakeStorage:
    def __init__(self, events=None):
        self.objects = {}
        self.events = events if events is not None else []
        self.upload_count = 0
        self.fail_upload_at = None
        self.delete_count = 0
        self.fail_delete_at = None

    def upload(self, path, content, mime):
        self.upload_count += 1
        self.events.append(("upload", path))
        if self.upload_count == self.fail_upload_at:
            raise RuntimeError("storage unavailable")
        if path in self.objects:
            raise RuntimeError("object exists")
        self.objects[path] = (content, mime)

    def download(self, path):
        self.events.append(("download", path))
        return self.objects[path][0]

    def delete(self, path):
        self.delete_count += 1
        self.events.append(("delete", path))
        if self.delete_count == self.fail_delete_at:
            raise RuntimeError("storage unavailable")
        del self.objects[path]


def _values(process="TC 00001/24", subtype="Prefeitura"):
    return {
        "procuradora": PROCURADORA,
        "numero_processo": process,
        "exercicio": 2024,
        "subtipo": subtype,
    }


def _service(repository=None, storage=None, identity=None, secrets=None):
    return MinutasLibraryService(
        repository or FakeRepository(),
        storage or FakeStorage(),
        identity or TEST_IDENTITY,
        secrets or SECRETS,
    )


def _create(service, process="TC 00001/24"):
    return service.create(
        _values(process),
        ("auditoria.pdf", _pdf(b"audit")),
        ("parecer.pdf", _pdf(b"opinion")),
    )


def test_repository_columns_match_idempotent_postgresql_schema():
    sql = (ROOT / "database" / "minutas_referencias.sql").read_text(
        encoding="utf-8"
    )
    table = sql.split("CREATE TABLE IF NOT EXISTS", 1)[1].split(
        "CREATE INDEX", 1
    )[0]
    declared = re.findall(
        r"^    ([a-z_]+)\s+(?:UUID|TEXT|INTEGER|BIGINT)\b", table, re.MULTILINE
    )

    assert declared == COLUMNS.split(",")
    assert "criado_em TEXT NOT NULL" in table
    assert "atualizado_em TEXT NOT NULL" in table


def test_complete_registration_listing_and_document_recovery():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)

    created = _create(service)

    assert service.list() == [created]
    audit = service.download(created["id"], "auditoria")
    opinion = service.download(created["id"], "parecer")
    assert audit.name == "auditoria.pdf"
    assert audit.mime == "application/pdf"
    assert audit.content.startswith(b"%PDF-")
    assert opinion.content.startswith(b"%PDF-")
    assert len(storage.objects) == 2


def test_duplicate_and_invalid_files_are_rejected_before_upload():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    _create(service)

    with pytest.raises(MinutasDuplicateError):
        _create(service, "tc-00001 24")
    with pytest.raises(MinutasValidationError, match="PDF"):
        service.create(
            _values("TC 00002/24"),
            ("auditoria.pdf", b"not a pdf"),
            ("parecer.pdf", _pdf()),
        )
    with pytest.raises(MinutasValidationError, match="Auditoria"):
        service.create(
            _values("TC 00003/24"),
            ("auditoria.docx", _docx()),
            ("parecer.pdf", _pdf()),
        )
    assert len(storage.objects) == 2


@pytest.mark.parametrize(
    "kind,upload,message",
    (
        ("auditoria", ("vazio.pdf", b""), "1 byte"),
        (
            "auditoria",
            ("grande.pdf", b"%PDF-" + b"x" * MAX_FILE),
            "10 MB",
        ),
        ("auditoria", ("relatorio.docx", _docx()), "Auditoria"),
        ("parecer", ("parecer.txt", b"texto"), "PDF e DOCX"),
        ("parecer", ("parecer.pdf", b"invalid content"), "PDF inválido"),
    ),
)
def test_invalid_or_oversized_documents_never_reach_storage(kind, upload, message):
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    audit = upload if kind == "auditoria" else ("auditoria.pdf", _pdf())
    opinion = upload if kind == "parecer" else ("parecer.pdf", _pdf())

    with pytest.raises(MinutasValidationError, match=message):
        service.create(_values(), audit, opinion)

    assert storage.events == []
    assert storage.objects == {}


def test_replacement_commits_new_metadata_before_removing_previous_file():
    events = []
    repository = FakeRepository(events)
    storage = FakeStorage(events)
    service = _service(repository, storage)
    created = _create(service)
    old_path = created["auditoria_path"]
    events.clear()

    updated = service.replace(
        created["id"], "auditoria", ("auditoria-nova.pdf", _pdf(b"new"))
    )

    new_path = updated["auditoria_path"]
    assert old_path not in storage.objects
    assert new_path in storage.objects
    assert [event[0] for event in events] == ["upload", "db_replace", "delete"]


def test_delete_requires_confirmation_and_removes_record_and_documents():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service)

    with pytest.raises(MinutasValidationError, match="Confirme"):
        service.delete(created["id"])
    service.delete(created["id"], confirmed=True)

    assert repository.records == {}
    assert storage.objects == {}


def test_partial_upload_and_database_failure_clean_new_objects():
    repository = FakeRepository()
    storage = FakeStorage()
    storage.fail_upload_at = 2
    service = _service(repository, storage)

    with pytest.raises(RuntimeError, match="storage"):
        _create(service)
    assert storage.objects == {}
    assert repository.records == {}

    storage.fail_upload_at = None
    repository.fail_create = True
    with pytest.raises(RuntimeError, match="database"):
        _create(service, "TC 00002/24")
    assert storage.objects == {}
    assert repository.records == {}


def test_failed_replacement_preserves_previous_file_and_metadata():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service)
    before = deepcopy(storage.objects)
    repository.fail_replace = True

    with pytest.raises(RuntimeError, match="database"):
        service.replace(created["id"], "parecer", ("novo.pdf", _pdf(b"new")))

    assert storage.objects == before
    assert repository.get(created["id"])["parecer_path"] == created["parecer_path"]


def test_failed_database_delete_restores_private_objects():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service)
    before = deepcopy(storage.objects)
    repository.fail_delete = True

    with pytest.raises(RuntimeError, match="database"):
        service.delete(created["id"], confirmed=True)

    assert storage.objects == before
    assert repository.get(created["id"]) == created


def test_partial_storage_delete_restores_already_removed_object():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service)
    before = deepcopy(storage.objects)
    storage.delete_count = 0
    storage.fail_delete_at = 2

    with pytest.raises(RuntimeError, match="storage"):
        service.delete(created["id"], confirmed=True)

    assert storage.objects == before
    assert repository.get(created["id"]) == created


@pytest.mark.parametrize("operation", ("list", "get", "download", "replace", "delete"))
def test_direct_service_calls_deny_unauthorized_identity(operation):
    repository = FakeRepository()
    storage = FakeStorage()
    identity = {**TEST_IDENTITY, "email": "outro@test.local"}
    service = _service(repository, storage, identity=identity)
    arguments = {
        "list": (),
        "get": ("record",),
        "download": ("record", "auditoria"),
        "replace": ("record", "auditoria", ("file.pdf", _pdf())),
        "delete": ("record", True),
    }

    with pytest.raises(MinutasAccessDenied):
        getattr(service, operation)(*arguments[operation])
    assert repository.events == []
    assert storage.events == []


def test_direct_create_denies_disabled_feature_before_validation():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(
        repository,
        storage,
        secrets={**SECRETS, "MINUTAS_PILOTO_ENABLED": False},
    )

    with pytest.raises(MinutasAccessDenied):
        _create(service)
    assert repository.events == []
    assert storage.events == []
