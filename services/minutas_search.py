"""Authorized PostgreSQL full-text search for historical definitive opinions."""

from hashlib import sha256
import re

from services.minutas_extraction import EXTRACTOR_VERSION
from services.minutas_library import (
    MinutasValidationError,
    PROCURADORA,
    SUBTIPOS,
)


SEARCH_NATURE = "Prestação de Contas Anual"
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 20


class MinutasSearchService:
    """Search valid opinion extractions without loading the full library in Python."""

    def __init__(self, library):
        self.library = library
        self.repository = library.repository

    @staticmethod
    def _query(value):
        query = re.sub(r"\s+", " ", str(value or "")).strip()
        if len(query) < 2 or len(query) > 200 or not re.search(r"[0-9A-Za-zÀ-ÿ]", query):
            raise MinutasValidationError(
                "Informe ao menos dois caracteres para pesquisar pareceres."
            )
        return query

    def search(
        self,
        query,
        *,
        procuradora=PROCURADORA,
        year=None,
        nature=SEARCH_NATURE,
        subtype=None,
        exclude_reference_id=None,
        exclude_process_key=None,
        exclude_paths=(),
        exclude_hashes=(),
        page=1,
        page_size=DEFAULT_PAGE_SIZE,
    ):
        self.library._authorize()
        query = self._query(query)
        if procuradora != PROCURADORA:
            raise MinutasValidationError(
                "Procuradora não habilitada para a pesquisa jurídica piloto."
            )
        if nature not in (None, "", SEARCH_NATURE):
            raise MinutasValidationError("Natureza não habilitada nesta fase.")
        if subtype not in (None, "") and subtype not in SUBTIPOS:
            raise MinutasValidationError("Subtipo de PCA inválido.")
        try:
            page = int(page)
            page_size = int(page_size)
            year = int(year) if year not in (None, "") else None
        except (TypeError, ValueError) as exc:
            raise MinutasValidationError("Filtros de pesquisa inválidos.") from exc
        if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
            raise MinutasValidationError("Paginação de pesquisa inválida.")
        if year is not None and not 1900 <= year <= 2100:
            raise MinutasValidationError("Exercício inválido.")

        rows = self.repository.search_opinions(
            query,
            PROCURADORA.casefold(),
            EXTRACTOR_VERSION,
            year=year,
            subtype=subtype or None,
            exclude_reference_id=exclude_reference_id,
            exclude_process_key=exclude_process_key,
            exclude_paths=tuple(exclude_paths),
            exclude_hashes=tuple(exclude_hashes),
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        has_next = len(rows) > page_size
        results = []
        for row in rows[:page_size]:
            source_identity = "|".join(
                str(row.get(field) or "")
                for field in (
                    "arquivo_sha256",
                    "extrator_versao",
                    "atualizado_em",
                )
            )
            results.append(
                {
                    "referencia_id": str(row["referencia_id"]),
                    "documento_id": str(row["documento_id"]),
                    "procuradora": row["procuradora"],
                    "processo": row["numero_processo"],
                    "exercicio": int(row["exercicio"]),
                    "natureza": SEARCH_NATURE,
                    "subtipo": row["subtipo"],
                    "documento": row["arquivo_nome"],
                    "arquivo_path": row["arquivo_path"],
                    "pagina": row.get("pagina"),
                    "localizacao": row.get("localizacao") or None,
                    "trecho": str(row.get("trecho") or "").strip(),
                    "contexto": str(row.get("contexto") or "").strip(),
                    "relevancia": float(row.get("relevancia") or 0),
                    "criterio_recuperacao": f"Expressão pesquisada: {query}",
                    "sha256_registrado": row["arquivo_sha256"],
                    "fonte_versao": sha256(source_identity.encode()).hexdigest(),
                }
            )
        return {
            "consulta": query,
            "pagina": page,
            "tamanho_pagina": page_size,
            "tem_anterior": page > 1,
            "tem_proxima": has_next,
            "resultados": results,
        }

    def download_opinion(self, reference_id):
        """Revalidate authorization before accessing the private original."""
        return self.library.download(reference_id, "parecer")
