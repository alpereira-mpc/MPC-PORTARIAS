"""Authorized orchestration for the Minutas IA reference library."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePath
import re
import uuid

from database.store import now
from services.access import normalize_email
from services.minutas_piloto import can_access_minutas_piloto
from services.oficios import validate_upload


PROCURADORA = "Elvira Samara"
SUBTIPOS = (
    "Prefeitura",
    "Câmara Municipal",
    "Instituto/Autarquia",
    "Secretaria Municipal ou Estadual",
)
DOCUMENT_KINDS = ("auditoria", "parecer")


class MinutasAccessDenied(PermissionError):
    pass


class MinutasValidationError(ValueError):
    pass


class MinutasDuplicateError(ValueError):
    pass


@dataclass(frozen=True)
class Download:
    name: str
    mime: str
    content: bytes


def _normalize_process(value):
    display = " ".join(str(value or "").strip().split())
    if not display or len(display) > 100:
        raise MinutasValidationError("Informe um número de processo válido.")
    key = re.sub(r"[^0-9a-z]", "", display.casefold())
    if not key:
        raise MinutasValidationError("Informe um número de processo válido.")
    return display, key


def _validate_year(value):
    try:
        year = int(value)
    except (TypeError, ValueError) as exc:
        raise MinutasValidationError("Informe um exercício válido.") from exc
    if not 1900 <= year <= datetime.now().year + 1:
        raise MinutasValidationError("Informe um exercício válido.")
    return year


def _validate_file(kind, upload):
    if not isinstance(upload, (tuple, list)) or len(upload) != 2:
        raise MinutasValidationError("Selecione os dois documentos obrigatórios.")
    name, content = upload
    if not isinstance(name, str) or not isinstance(content, bytes):
        raise MinutasValidationError("Arquivo inválido.")
    try:
        safe_name, mime = validate_upload(name, content)
    except ValueError as exc:
        raise MinutasValidationError(str(exc)) from exc
    if kind == "auditoria" and mime != "application/pdf":
        raise MinutasValidationError("O Relatório da Auditoria deve ser PDF.")
    return {"name": safe_name, "mime": mime, "size": len(content), "content": content}


class MinutasLibraryService:
    """Administrative API; future reader roles can use a separate service."""

    def __init__(self, repository, storage, identity, secrets):
        self.repository = repository
        self.storage = storage
        self.identity = dict(identity or {})
        self.secrets = secrets

    def _authorize(self):
        if not can_access_minutas_piloto(self.identity, self.secrets):
            raise MinutasAccessDenied("Acesso não autorizado a este módulo.")
        return normalize_email(self.identity.get("email"))

    @staticmethod
    def _path(identifier, kind, name):
        suffix = PurePath(name).suffix.lower()
        return f"referencias/{identifier}/{kind}/{uuid.uuid4().hex}{suffix}"

    def create(self, values, auditoria, parecer):
        actor = self._authorize()
        procuradora = str(values.get("procuradora") or "").strip()
        if procuradora != PROCURADORA:
            raise MinutasValidationError("Procuradora inválida.")
        process, process_key = _normalize_process(values.get("numero_processo"))
        subtype = values.get("subtipo")
        if subtype not in SUBTIPOS:
            raise MinutasValidationError("Subtipo de PCA inválido.")
        year = _validate_year(values.get("exercicio"))
        procuradora_key = procuradora.casefold()
        if self.repository.get_by_process(procuradora_key, process_key):
            raise MinutasDuplicateError(
                "Já existe uma referência para este processo e Procuradora."
            )
        files = {
            "auditoria": _validate_file("auditoria", auditoria),
            "parecer": _validate_file("parecer", parecer),
        }
        identifier = str(uuid.uuid4())
        uploaded = []
        try:
            for kind, document in files.items():
                document["path"] = self._path(identifier, kind, document["name"])
                uploaded.append(document["path"])
                self.storage.upload(
                    document["path"], document["content"], document["mime"]
                )
            stamp = now()
            record = {
                "id": identifier,
                "procuradora": procuradora,
                "procuradora_chave": procuradora_key,
                "numero_processo": process,
                "processo_chave": process_key,
                "exercicio": year,
                "subtipo": subtype,
                "criado_em": stamp,
                "criado_por": actor,
                "atualizado_em": stamp,
                "atualizado_por": actor,
            }
            for kind, document in files.items():
                for field in ("name", "mime", "size", "path"):
                    column = {"name": "nome", "size": "tamanho"}.get(field, field)
                    record[f"{kind}_{column}"] = document[field]
            return self.repository.create(record)
        except Exception as exc:
            for path in reversed(uploaded):
                try:
                    self.storage.delete(path)
                except Exception:
                    pass
            if "unique" in str(exc).casefold():
                raise MinutasDuplicateError(
                    "Já existe uma referência para este processo e Procuradora."
                ) from exc
            raise

    def list(self):
        self._authorize()
        return self.repository.list()

    def get(self, identifier):
        self._authorize()
        record = self.repository.get(identifier)
        if not record:
            raise MinutasValidationError("Referência não encontrada.")
        return record

    def download(self, identifier, kind):
        self._authorize()
        if kind not in DOCUMENT_KINDS:
            raise MinutasValidationError("Tipo de documento inválido.")
        record = self.get(identifier)
        content = self.storage.download(record[f"{kind}_path"])
        return Download(record[f"{kind}_nome"], record[f"{kind}_mime"], content)

    def replace(self, identifier, kind, upload):
        actor = self._authorize()
        if kind not in DOCUMENT_KINDS:
            raise MinutasValidationError("Tipo de documento inválido.")
        current = self.get(identifier)
        document = _validate_file(kind, upload)
        document["path"] = self._path(identifier, kind, document["name"])
        try:
            self.storage.upload(
                document["path"], document["content"], document["mime"]
            )
        except Exception:
            try:
                self.storage.delete(document["path"])
            except Exception:
                pass
            raise
        try:
            updated = self.repository.replace_document(
                identifier, kind, document, actor
            )
        except Exception:
            try:
                self.storage.delete(document["path"])
            except Exception:
                pass
            raise
        cleanup_warning = False
        try:
            self.storage.delete(current[f"{kind}_path"])
        except Exception:
            cleanup_warning = True
        if cleanup_warning:
            updated["cleanup_pending"] = True
        return updated

    def delete(self, identifier, confirmed=False):
        self._authorize()
        if confirmed is not True:
            raise MinutasValidationError("Confirme a exclusão da referência.")
        record = self.get(identifier)
        snapshots = {
            kind: self.storage.download(record[f"{kind}_path"])
            for kind in DOCUMENT_KINDS
        }
        removed = []
        try:
            for kind in DOCUMENT_KINDS:
                path = record[f"{kind}_path"]
                removed.append(kind)
                self.storage.delete(path)
            self.repository.delete(identifier)
        except Exception:
            for kind in removed:
                try:
                    self.storage.upload(
                        record[f"{kind}_path"],
                        snapshots[kind],
                        record[f"{kind}_mime"],
                    )
                except Exception:
                    pass
            raise
