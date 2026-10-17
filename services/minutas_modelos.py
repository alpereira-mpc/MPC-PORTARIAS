"""Modelo DOCX visual por Procuradora e especialidade. Não alimenta acervo, extração nem RAG."""

from hashlib import sha256
import logging
import uuid

from database.minutas_especialidades import _first
from database.store import now
from services.minutas_library import MinutasValidationError, _validate_file
from services.minutas_procuradores import chave_efetiva, exigir_gestao_acervo, legado_espo
from services.minutas_specialties import scope
from services.minutas_storage import modelo_object_path


def models_enabled(connection, backend):
    """Tabela aditiva. Ausente até o SQL manual: a exportação segue no modelo institucional."""
    if backend == "postgresql":
        result = connection.execute(
            "SELECT 1 AS ok FROM information_schema.tables "
            "WHERE table_schema=current_schema() AND table_name='minutas_modelos_visuais'"
        )
    else:
        result = connection.execute(
            "SELECT 1 AS ok FROM sqlite_master WHERE type='table' AND name='minutas_modelos_visuais'"
        )
    return _first(result) is not None


class MinutasModeloVisualService:
    def __init__(self, library):
        self.library = library

    def _chave(self):
        procurador = getattr(self.library, "procurador", None)
        if procurador is not None:
            return procurador.chave
        repository = getattr(self.library, "repository", None)
        if repository is None:
            return legado_espo().chave
        return chave_efetiva(repository)

    def _enabled(self, connection):
        return models_enabled(connection, getattr(self.library.repository.store, "backend", ""))

    def enabled(self):
        with self.library.repository.store.connection(read_only=True) as connection:
            return self._enabled(connection)

    def active(self, specialty):
        self.library._authorize()
        scope(specialty)
        with self.library.repository.store.connection(read_only=True) as connection:
            if not self._enabled(connection):
                return None
            row = connection.execute(
                "SELECT * FROM minutas_modelos_visuais WHERE procuradora_chave=? "
                "AND especialidade_id=? AND ativo=1",
                (self._chave(), specialty),
            ).fetchone()
        return dict(row) if row else None

    def versions(self, specialty):
        self.library._authorize()
        scope(specialty)
        with self.library.repository.store.connection(read_only=True) as connection:
            if not self._enabled(connection):
                return []
            rows = connection.execute(
                "SELECT id,versao,ativo,nome,sha256,criado_em FROM minutas_modelos_visuais "
                "WHERE procuradora_chave=? AND especialidade_id=? ORDER BY versao",
                (self._chave(), specialty),
            ).fetchall()
        return [dict(row) for row in rows]

    def content(self, specialty):
        row = self.active(specialty)
        if not row:
            return None
        return self.library.storage.download(row["path"])

    def save(self, specialty, upload):
        """Substitui o modelo ativo e conserva as versões anteriores. Não cria processo."""
        exigir_gestao_acervo(self.library)
        actor = self.library._authorize()
        scope(specialty)
        file = _validate_file("parecer", upload)
        if file["mime"] != "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
            raise MinutasValidationError("O modelo visual aceita apenas DOCX.")
        digest = sha256(file["content"]).hexdigest()
        with self.library.repository.store.connection(read_only=True) as connection:
            if not self._enabled(connection):
                raise MinutasValidationError(
                    "O cadastro de modelo visual ainda não está disponível neste banco. "
                    "A exportação permanece no modelo institucional."
                )
            current = connection.execute(
                "SELECT * FROM minutas_modelos_visuais WHERE procuradora_chave=? "
                "AND especialidade_id=? AND sha256=?",
                (self._chave(), specialty, digest),
            ).fetchone()
        if current and int(current["ativo"]) == 1:
            return {"estado": "ATUAL", "id": str(current["id"]), "versao": int(current["versao"]), "path": current["path"]}
        if current:
            with self.library.repository.store.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                self._activate(connection, specialty, str(current["id"]))
            return {"estado": "REATIVADO", "id": str(current["id"]), "versao": int(current["versao"]), "path": current["path"]}
        path = modelo_object_path(file["name"])
        uploaded = False
        persisted = False
        try:
            self.library.storage.upload(path, file["content"], file["mime"])
            uploaded = True
            with self.library.repository.store.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                if not self._enabled(connection):
                    raise MinutasValidationError(
                        "O cadastro de modelo visual ainda não está disponível neste banco."
                    )
                version = connection.execute(
                    "SELECT COALESCE(MAX(versao),0)+1 AS next FROM minutas_modelos_visuais "
                    "WHERE procuradora_chave=? AND especialidade_id=?",
                    (self._chave(), specialty),
                ).fetchone()["next"]
                identifier = str(uuid.uuid4())
                stamp = now()
                connection.execute(
                    "UPDATE minutas_modelos_visuais SET ativo=0 WHERE procuradora_chave=? "
                    "AND especialidade_id=? AND ativo=1",
                    (self._chave(), specialty),
                )
                connection.execute(
                    "INSERT INTO minutas_modelos_visuais (id,procuradora_chave,especialidade_id,versao,ativo,"
                    "nome,mime,tamanho,path,sha256,criado_em,criado_por) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (identifier, self._chave(), specialty, int(version), 1, file["name"], file["mime"],
                     file["size"], path, digest, stamp, actor),
                )
            persisted = True
            return {"estado": "CADASTRADO", "id": identifier, "versao": int(version), "path": path}
        finally:
            if uploaded and not persisted:
                try:
                    self.library.storage.delete(path)
                except Exception:
                    logging.getLogger(__name__).warning(
                        "MINUTAS: compensação do modelo visual falhou; conferir o inventário do bucket."
                    )

    def _activate(self, connection, specialty, identifier):
        connection.execute(
            "UPDATE minutas_modelos_visuais SET ativo=0 WHERE procuradora_chave=? "
            "AND especialidade_id=? AND ativo=1",
            (self._chave(), specialty),
        )
        connection.execute(
            "UPDATE minutas_modelos_visuais SET ativo=1 WHERE id=? AND procuradora_chave=? "
            "AND especialidade_id=?",
            (identifier, self._chave(), specialty),
        )


def active_model_bytes(library):
    """Bytes do modelo ativo da especialidade da sessão. None usa o modelo institucional."""
    repository = getattr(library, "repository", None)
    store = getattr(repository, "store", None) if repository else None
    storage = getattr(library, "storage", None)
    specialty = getattr(library, "specialty_id", None)
    if store is None or storage is None or not specialty:
        return None
    service = MinutasModeloVisualService(library)
    if not service.enabled():
        return None
    row = service.active(specialty)
    if not row:
        return None
    return storage.download(row["path"])
