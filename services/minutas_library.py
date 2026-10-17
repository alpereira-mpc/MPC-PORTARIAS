"""Authorized orchestration for the Minutas IA reference library."""

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import PurePath
import re
import uuid

from database.store import now
from services.access import normalize_email
from services.minutas_piloto import can_access_minutas_piloto
from services.minutas_procuradores import (
    exigir_admin_estrutural,
    exigir_gestao_acervo,
    exigir_procurador,
    legado_espo,
    mesmo_escopo,
)
from services.oficios import validate_upload


PROCURADORA = "Elvira Samara"
SUBTIPOS = (
    "Prefeitura",
    "Câmara Municipal",
    "Instituto/Autarquia",
    "Secretaria Municipal ou Estadual",
)
DOCUMENT_KINDS = ("auditoria", "parecer")
COMPLEMENT_TYPES = (
    "Relatório de Análise de Defesa",
    "Relatório de Complementação de Instrução",
    "Cota Ministerial",
    "Outros relatórios ou manifestações processuais",
)
OTHER_COMPLEMENT_TYPE = "Outros relatórios ou manifestações processuais"


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


def coerce_account_year(value, *, required):
    if value in (None, ""):
        if required:
            raise MinutasValidationError("Informe um exercício válido.")
        return None
    return _validate_year(value)


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


def _validate_complement_metadata(values):
    document_type = values.get("tipo")
    if document_type not in COMPLEMENT_TYPES:
        raise MinutasValidationError("Tipo de documento complementar inválido.")
    description = " ".join(str(values.get("descricao") or "").strip().split())
    if document_type == OTHER_COMPLEMENT_TYPE and not description:
        raise MinutasValidationError(
            "Informe uma descrição para outros relatórios ou manifestações."
        )
    try:
        position = int(values.get("posicao"))
    except (TypeError, ValueError) as exc:
        raise MinutasValidationError("Informe uma posição cronológica válida.") from exc
    if position < 1:
        raise MinutasValidationError("Informe uma posição cronológica válida.")
    document_date = values.get("data_documento")
    if document_date in (None, ""):
        normalized_date = None
    else:
        try:
            if isinstance(document_date, datetime):
                document_date = document_date.date()
            normalized_date = (
                document_date.isoformat()
                if isinstance(document_date, date)
                else date.fromisoformat(str(document_date)).isoformat()
            )
        except (TypeError, ValueError) as exc:
            raise MinutasValidationError("Informe uma data válida.") from exc
    return {
        "tipo": document_type,
        "descricao": description,
        "posicao": position,
        "data_documento": normalized_date,
    }


class MinutasLibraryService:
    """Administrative API; future reader roles can use a separate service."""

    def __init__(self, repository, storage, identity, secrets, procurador=None, principal=None):
        self.repository = repository
        self.storage = storage
        self.identity = dict(identity or {})
        self.secrets = secrets
        self.specialty_id = getattr(repository, "specialty_id", None)
        # Ausência explícita mantém o piloto ESPO para chamadores anteriores a esta expansão.
        # A interface nova sempre informa o Procurador selecionado.
        self.procurador = exigir_procurador(procurador) if procurador is not None else legado_espo()
        self.principal = principal
        try:
            self.repository.procuradora_chave = self.procurador.chave
        except Exception:
            pass

    def require_specialty(self):
        self._authorize()
        from services.minutas_specialties import scope
        return scope(self.specialty_id)

    def download_path(self, identifier, path):
        """Revalidate the scoped relationship, including retained document versions."""
        record = self.get(identifier)
        from services.minutas_extraction import MinutasExtractionService
        documents = MinutasExtractionService(self)._documents(record, include_models=True)
        document = next((d for d in documents if d["path"] == path), None)
        if document is None:
            raise MinutasValidationError("Documento fora do acervo selecionado.")
        return Download(document["nome"], document["mime"], self.storage.download(path))

    def _authorize(self):
        if not can_access_minutas_piloto(self.identity, self.secrets):
            raise MinutasAccessDenied("Acesso não autorizado a este módulo.")
        from services.estagiarios_acesso import exigir_gabinete_minutas

        exigir_gabinete_minutas(self.principal, getattr(self.procurador, "sigla", None))
        return normalize_email(self.identity.get("email"))

    @staticmethod
    def _path(identifier, kind, name):
        suffix = PurePath(name).suffix.lower()
        if suffix not in {".pdf", ".docx"}:
            raise MinutasValidationError("Aceitos apenas PDF e DOCX.")
        return f"referencias/{identifier}/{kind}/{uuid.uuid4().hex}{suffix}"

    def create(self, values, auditoria, parecer):
        exigir_gestao_acervo(self)
        actor = self._authorize()
        procuradora = str(values.get("procuradora") or "").strip()
        if procuradora != self.procurador.nome_exibicao:
            raise MinutasValidationError("Procurador inválido.")
        process, process_key = _normalize_process(values.get("numero_processo"))
        subtype = values.get("subtipo")
        if subtype not in SUBTIPOS:
            raise MinutasValidationError("Subtipo de PCA inválido.")
        year = _validate_year(values.get("exercicio"))
        procuradora_key = self.procurador.chave
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
        return [record for record in self.repository.list() if mesmo_escopo(record, self.procurador)]

    def get(self, identifier):
        self._authorize()
        record = self.repository.get(identifier)
        if not mesmo_escopo(record, self.procurador):
            raise MinutasValidationError("Referência não encontrada.")
        return record

    def download(self, identifier, kind):
        self._authorize()
        if kind not in DOCUMENT_KINDS:
            raise MinutasValidationError("Tipo de documento inválido.")
        record = self.get(identifier)
        content = self.storage.download(record[f"{kind}_path"])
        return Download(record[f"{kind}_nome"], record[f"{kind}_mime"], content)

    def list_documents(self, identifier):
        self._authorize()
        self.get(identifier)
        return self.repository.list_documents(identifier)

    def add_document(self, identifier, values, upload):
        exigir_gestao_acervo(self)
        actor = self._authorize()
        self.get(identifier)
        metadata = _validate_complement_metadata(values)
        if upload is None:
            raise MinutasValidationError("Selecione o documento complementar.")
        document = _validate_file("parecer", upload)
        document["path"] = self._path(identifier, "complementares", document["name"])
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
        stamp = now()
        record = {
            "id": str(uuid.uuid4()),
            "referencia_id": identifier,
            **metadata,
            "nome": document["name"],
            "mime": document["mime"],
            "tamanho": document["size"],
            "path": document["path"],
            "criado_em": stamp,
            "criado_por": actor,
            "atualizado_em": stamp,
            "atualizado_por": actor,
        }
        try:
            return self.repository.create_document(record)
        except Exception as exc:
            try:
                self.storage.delete(document["path"])
            except Exception:
                pass
            if "unique" in str(exc).casefold():
                raise MinutasValidationError(
                    "Já existe um documento nessa posição cronológica."
                ) from exc
            raise

    def download_document(self, identifier, document_id):
        self._authorize()
        self.get(identifier)
        document = self.repository.get_document(document_id)
        if not document or str(document["referencia_id"]) != str(identifier):
            raise MinutasValidationError("Documento complementar não encontrado.")
        content = self.storage.download(document["path"])
        return Download(document["nome"], document["mime"], content)

    def update_documents(self, identifier, documents):
        exigir_gestao_acervo(self)
        actor = self._authorize()
        self.get(identifier)
        current = self.repository.list_documents(identifier)
        current_ids = {str(document["id"]) for document in current}
        submitted_ids = {str(document.get("id")) for document in documents}
        if current_ids != submitted_ids or len(documents) != len(current_ids):
            raise MinutasValidationError("A relação de documentos foi alterada.")
        normalized = []
        for document in documents:
            normalized.append(
                {"id": str(document["id"]), **_validate_complement_metadata(document)}
            )
        positions = [document["posicao"] for document in normalized]
        if len(positions) != len(set(positions)):
            raise MinutasValidationError(
                "Cada documento deve ter uma posição cronológica distinta."
            )
        return self.repository.update_documents(identifier, normalized, actor)

    def delete_document(self, identifier, document_id, confirmed=False):
        self._authorize()
        exigir_admin_estrutural(self)
        if confirmed is not True:
            raise MinutasValidationError("Confirme a exclusão do documento.")
        self.get(identifier)
        document = self.repository.get_document(document_id)
        if not document or str(document["referencia_id"]) != str(identifier):
            raise MinutasValidationError("Documento complementar não encontrado.")
        snapshot = self.storage.download(document["path"])
        try:
            self.storage.delete(document["path"])
            self.repository.delete_document(document_id, identifier)
        except Exception:
            try:
                self.storage.upload(document["path"], snapshot, document["mime"])
            except Exception:
                pass
            raise

    def promote_document_to_primary(
        self, identifier, document_id, displaced_values, confirmed=False
    ):
        exigir_gestao_acervo(self)
        actor = self._authorize()
        if confirmed is not True:
            raise MinutasValidationError("Confirme a reorganização dos documentos.")
        self.get(identifier)
        selected = self.repository.get_document(document_id)
        if not selected or str(selected["referencia_id"]) != str(identifier):
            raise MinutasValidationError("Documento complementar não encontrado.")
        if selected["tipo"] != "Relatório de Análise de Defesa":
            raise MinutasValidationError(
                "Selecione o Relatório de Análise de Defesa original."
            )
        if selected["mime"] != "application/pdf":
            raise MinutasValidationError("O Relatório da Auditoria deve ser PDF.")
        displaced = {
            "id": str(uuid.uuid4()),
            **_validate_complement_metadata(displaced_values),
        }
        return self.repository.promote_document(
            identifier, document_id, displaced, actor
        )

    def replace(self, identifier, kind, upload):
        exigir_gestao_acervo(self)
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
        exigir_admin_estrutural(self)
        self._authorize()
        if confirmed is not True:
            raise MinutasValidationError("Confirme a exclusão da referência.")
        record = self.get(identifier)
        complementary = self.repository.list_documents(identifier)
        stored = [
            {
                "path": record[f"{kind}_path"],
                "mime": record[f"{kind}_mime"],
            }
            for kind in DOCUMENT_KINDS
        ] + [
            {"path": document["path"], "mime": document["mime"]}
            for document in complementary
        ]
        snapshots = {
            item["path"]: self.storage.download(item["path"]) for item in stored
        }
        removed = []
        try:
            for item in stored:
                removed.append(item)
                self.storage.delete(item["path"])
            self.repository.delete(identifier)
        except Exception:
            for item in removed:
                try:
                    self.storage.upload(
                        item["path"], snapshots[item["path"]], item["mime"]
                    )
                except Exception:
                    pass
            raise
