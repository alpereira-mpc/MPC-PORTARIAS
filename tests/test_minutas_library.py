from copy import deepcopy
from io import BytesIO
import re

import pytest
from docx import Document
from pypdf import PdfWriter

from database.minutas_referencias import COLUMNS, COMPLEMENT_COLUMNS
from database.store import ROOT
from services.minutas_library import (
    COMPLEMENT_TYPES,
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
        self.documents = {}
        self.events = events if events is not None else []
        self.fail_create = False
        self.fail_replace = False
        self.fail_delete = False
        self.fail_document_create = False
        self.fail_document_delete = False
        self.fail_promote = False

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

    def create_document(self, record):
        self.events.append(("db_document_create", record["id"]))
        if self.fail_document_create:
            raise RuntimeError("database unavailable")
        if any(
            item["referencia_id"] == record["referencia_id"]
            and item["posicao"] == record["posicao"]
            for item in self.documents.values()
        ):
            raise RuntimeError("unique position")
        self.documents[record["id"]] = deepcopy(record)
        return deepcopy(record)

    def get_document(self, identifier):
        document = self.documents.get(identifier)
        return deepcopy(document) if document else None

    def list_documents(self, reference_id):
        return sorted(
            (
                deepcopy(document)
                for document in self.documents.values()
                if document["referencia_id"] == reference_id
            ),
            key=lambda document: (document["posicao"], document["id"]),
        )

    def update_documents(self, reference_id, documents, actor):
        positions = [item["posicao"] for item in documents]
        if len(positions) != len(set(positions)):
            raise RuntimeError("unique position")
        for values in documents:
            document = self.documents[values["id"]]
            if document["referencia_id"] != reference_id:
                raise RuntimeError("document unavailable")
            document.update(deepcopy(values))
            document["atualizado_por"] = actor
        return self.list_documents(reference_id)

    def delete_document(self, identifier, reference_id):
        self.events.append(("db_document_delete", identifier))
        if self.fail_document_delete:
            raise RuntimeError("database unavailable")
        if self.documents[identifier]["referencia_id"] != reference_id:
            raise RuntimeError("document unavailable")
        del self.documents[identifier]

    def promote_document(self, reference_id, document_id, displaced, actor):
        self.events.append(("db_promote", document_id))
        if self.fail_promote:
            raise RuntimeError("database unavailable")
        selected = deepcopy(self.documents[document_id])
        reference = self.records[reference_id]
        old_audit = {
            "nome": reference["auditoria_nome"],
            "mime": reference["auditoria_mime"],
            "tamanho": reference["auditoria_tamanho"],
            "path": reference["auditoria_path"],
        }
        del self.documents[document_id]
        self.documents[displaced["id"]] = {
            **deepcopy(displaced),
            "referencia_id": reference_id,
            **old_audit,
            "criado_em": "now",
            "criado_por": actor,
            "atualizado_em": "now",
            "atualizado_por": actor,
        }
        for field in ("nome", "mime", "tamanho", "path"):
            reference[f"auditoria_{field}"] = selected[field]
        reference["atualizado_por"] = actor
        return deepcopy(reference), self.list_documents(reference_id)

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
        self.documents = {
            key: value
            for key, value in self.documents.items()
            if value["referencia_id"] != identifier
        }


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


def _add_complement(
    service,
    reference_id,
    position=1,
    document_type="Relatório de Análise de Defesa",
    name="complementar.pdf",
    content=None,
    description="",
):
    return service.add_document(
        reference_id,
        {
            "tipo": document_type,
            "descricao": description,
            "posicao": position,
            "data_documento": None,
        },
        (name, content or _pdf(name.encode("ascii", errors="ignore"))),
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


def test_complement_repository_columns_match_additive_schema():
    sql = (ROOT / "database" / "minutas_documentos_complementares.sql").read_text(
        encoding="utf-8"
    )
    table = sql.split("CREATE TABLE IF NOT EXISTS", 1)[1].split(
        "CREATE INDEX", 1
    )[0]
    declared = re.findall(
        r"^    ([a-z_]+)\s+(?:UUID|TEXT|INTEGER|BIGINT|DATE)\b",
        table,
        re.MULTILINE,
    )

    assert declared == COMPLEMENT_COLUMNS.split(",")
    assert "REFERENCES mpc_portarias.minutas_referencias(id)" in table
    assert "DEFERRABLE INITIALLY DEFERRED" in table
    assert re.search(
        r"ALTER TABLE mpc_portarias\.minutas_documentos_complementares\s+"
        r"ENABLE ROW LEVEL SECURITY;",
        sql,
        re.IGNORECASE,
    )
    assert re.search(
        r"REVOKE ALL ON mpc_portarias\.minutas_documentos_complementares\s+"
        r"FROM PUBLIC, anon, authenticated;",
        sql,
        re.IGNORECASE,
    )
    assert not re.search(
        r"^\s*(?:UPDATE|DELETE|INSERT)\s", sql, re.IGNORECASE | re.MULTILINE
    )


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


def test_multiple_complements_are_ordered_downloadable_and_reclassified():
    service = _service()
    created = _create(service)
    second = _add_complement(
        service,
        created["id"],
        position=2,
        document_type="Cota Ministerial",
        name="cota.docx",
        content=_docx(),
    )
    first = _add_complement(service, created["id"], position=1)

    assert [item["id"] for item in service.list_documents(created["id"])] == [
        first["id"],
        second["id"],
    ]
    assert service.download_document(created["id"], second["id"]).content.startswith(
        b"PK"
    )

    updated = service.update_documents(
        created["id"],
        [
            {
                "id": first["id"],
                "tipo": "Cota Ministerial",
                "descricao": "",
                "posicao": 2,
                "data_documento": "2025-03-02",
            },
            {
                "id": second["id"],
                "tipo": COMPLEMENT_TYPES[-1],
                "descricao": "Manifestação da unidade técnica",
                "posicao": 1,
                "data_documento": None,
            },
        ],
    )

    assert [item["id"] for item in updated] == [second["id"], first["id"]]
    assert updated[0]["descricao"] == "Manifestação da unidade técnica"
    assert updated[1]["data_documento"] == "2025-03-02"


def test_adding_complement_preserves_all_existing_references_and_main_documents():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    references = [
        _create(service, f"TC 0000{number}/24") for number in range(1, 5)
    ]
    records_before = deepcopy(repository.records)
    main_objects_before = deepcopy(storage.objects)

    _add_complement(service, references[2]["id"])

    assert repository.records == records_before
    assert len(service.list()) == 4
    assert all(storage.objects[path] == value for path, value in main_objects_before.items())


@pytest.mark.parametrize(
    "upload,message",
    (
        (("vazio.pdf", b""), "1 byte"),
        (("grande.pdf", b"%PDF-" + b"x" * MAX_FILE), "10 MB"),
        (("texto.txt", b"texto"), "PDF e DOCX"),
        (("falso.pdf", b"invalid content"), "PDF inválido"),
    ),
)
def test_invalid_complements_are_rejected_before_storage(upload, message):
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service)
    storage.events.clear()

    with pytest.raises(MinutasValidationError, match=message):
        service.add_document(
            created["id"],
            {
                "tipo": "Cota Ministerial",
                "descricao": "",
                "posicao": 1,
                "data_documento": None,
            },
            upload,
        )

    assert storage.events == []


def test_other_complement_requires_description_and_positions_are_unique():
    service = _service()
    created = _create(service)
    with pytest.raises(MinutasValidationError, match="descrição"):
        _add_complement(
            service,
            created["id"],
            document_type=COMPLEMENT_TYPES[-1],
        )
    first = _add_complement(service, created["id"])
    second = _add_complement(service, created["id"], position=2, name="cota.pdf")
    with pytest.raises(MinutasValidationError, match="distinta"):
        service.update_documents(
            created["id"],
            [
                {**first, "posicao": 1},
                {**second, "posicao": 1},
            ],
        )


def test_complement_delete_requires_confirmation_and_restores_on_db_failure():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service)
    document = _add_complement(service, created["id"])
    before = deepcopy(storage.objects)

    with pytest.raises(MinutasValidationError, match="Confirme"):
        service.delete_document(created["id"], document["id"])
    repository.fail_document_delete = True
    with pytest.raises(RuntimeError, match="database"):
        service.delete_document(created["id"], document["id"], confirmed=True)
    assert storage.objects == before
    assert repository.get_document(document["id"]) == document

    repository.fail_document_delete = False
    service.delete_document(created["id"], document["id"], confirmed=True)
    assert repository.get_document(document["id"]) is None
    assert document["path"] not in storage.objects


def test_complement_database_create_failure_removes_uploaded_object():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service)
    before = deepcopy(storage.objects)
    repository.fail_document_create = True

    with pytest.raises(RuntimeError, match="database"):
        _add_complement(service, created["id"])

    assert storage.objects == before
    assert repository.documents == {}


def test_sedh_reorganization_is_atomic_and_does_not_move_storage_objects():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service, "TC 02650/25")
    old_main_path = created["auditoria_path"]
    original = _add_complement(service, created["id"], position=1)
    cota = _add_complement(
        service,
        created["id"],
        position=2,
        document_type="Cota Ministerial",
        name="cota.pdf",
    )
    objects_before = deepcopy(storage.objects)
    storage.events.clear()

    reference, documents = service.promote_document_to_primary(
        created["id"],
        original["id"],
        {
            "tipo": "Relatório de Complementação de Instrução",
            "descricao": "",
            "posicao": 3,
            "data_documento": None,
        },
        confirmed=True,
    )

    assert reference["auditoria_path"] == original["path"]
    assert [item["posicao"] for item in documents] == [2, 3]
    assert documents[0]["id"] == cota["id"]
    assert documents[1]["path"] == old_main_path
    assert storage.objects == objects_before
    assert storage.events == []


def test_failed_sedh_reorganization_preserves_metadata_and_storage():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service, "TC 02650/25")
    original = _add_complement(service, created["id"])
    records_before = deepcopy(repository.records)
    documents_before = deepcopy(repository.documents)
    objects_before = deepcopy(storage.objects)
    repository.fail_promote = True

    with pytest.raises(RuntimeError, match="database"):
        service.promote_document_to_primary(
            created["id"],
            original["id"],
            {
                "tipo": "Relatório de Complementação de Instrução",
                "descricao": "",
                "posicao": 3,
                "data_documento": None,
            },
            confirmed=True,
        )

    assert repository.records == records_before
    assert repository.documents == documents_before
    assert storage.objects == objects_before


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


def test_reference_delete_includes_complements_and_restores_all_on_failure():
    repository = FakeRepository()
    storage = FakeStorage()
    service = _service(repository, storage)
    created = _create(service)
    document = _add_complement(service, created["id"])
    before = deepcopy(storage.objects)
    repository.fail_delete = True

    with pytest.raises(RuntimeError, match="database"):
        service.delete(created["id"], confirmed=True)

    assert storage.objects == before
    assert repository.get_document(document["id"]) == document

    repository.fail_delete = False
    service.delete(created["id"], confirmed=True)
    assert repository.get(created["id"]) is None
    assert repository.get_document(document["id"]) is None
    assert storage.objects == {}


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


@pytest.mark.parametrize(
    "operation",
    (
        "list",
        "get",
        "download",
        "replace",
        "delete",
        "list_documents",
        "add_document",
        "download_document",
        "update_documents",
        "delete_document",
        "promote_document_to_primary",
    ),
)
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
        "list_documents": ("record",),
        "add_document": (
            "record",
            {
                "tipo": "Cota Ministerial",
                "descricao": "",
                "posicao": 1,
                "data_documento": None,
            },
            ("file.pdf", _pdf()),
        ),
        "download_document": ("record", "document"),
        "update_documents": ("record", []),
        "delete_document": ("record", "document", True),
        "promote_document_to_primary": (
            "record",
            "document",
            {
                "tipo": "Relatório de Complementação de Instrução",
                "descricao": "",
                "posicao": 1,
                "data_documento": None,
            },
            True,
        ),
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
