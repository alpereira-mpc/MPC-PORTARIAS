from dataclasses import dataclass
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from database.minutas_referencias import MinutasReferenciasStore
from services.minutas_extraction import EXTRACTOR_VERSION
from services.minutas_library import MinutasAccessDenied, MinutasValidationError, PROCURADORA
from services.minutas_search import MinutasSearchService, SEARCH_NATURE


def _result(process="TC 01653/26", relevance=0.8, **changes):
    row = {
        "referencia_id": "reference-1",
        "documento_id": "extraction-1",
        "procuradora": PROCURADORA,
        "numero_processo": process,
        "exercicio": 2026,
        "subtipo": "Instituto/Autarquia",
        "arquivo_nome": "parecer.pdf",
        "arquivo_path": "references/reference-1/parecer.pdf",
        "arquivo_sha256": "a" * 64,
        "extrator_versao": EXTRACTOR_VERSION,
        "atualizado_em": "2026-10-13T10:00:00+00:00",
        "pagina": 7,
        "localizacao": "",
        "trecho": "Contribuição 【previdenciária】 não recolhida.",
        "contexto": "Contexto integral da página original do parecer.",
        "relevancia": relevance,
    }
    row.update(changes)
    return row


class SearchRepository:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def search_opinions(self, query, procuradora_key, extractor_version, **filters):
        self.calls.append((query, procuradora_key, extractor_version, filters))
        rows = sorted(self.rows, key=lambda item: item["relevancia"], reverse=True)
        year = filters.get("year")
        subtype = filters.get("subtype")
        if year is not None:
            rows = [item for item in rows if item["exercicio"] == year]
        if subtype is not None:
            rows = [item for item in rows if item["subtipo"] == subtype]
        offset = filters["offset"]
        return rows[offset : offset + filters["limit"] + 1]


@dataclass
class Download:
    name: str = "parecer.pdf"
    mime: str = "application/pdf"
    content: bytes = b"pdf"


class Library:
    specialty_id = "elvira.pca.rpps"
    def __init__(self, rows=(), *, authorized=True):
        self.repository = SearchRepository(rows)
        self.authorized = authorized
        self.downloads = []

    def _authorize(self):
        if not self.authorized:
            raise MinutasAccessDenied("Acesso não autorizado a este módulo.")
        return "admin@mpc.pb.gov.br"

    def download(self, reference_id, kind):
        self._authorize()
        self.downloads.append((reference_id, kind))
        return Download()


def test_search_returns_theme_expression_page_and_structured_source():
    library = Library([_result()])
    service = MinutasSearchService(library)

    response = service.search(
        '"contribuição previdenciária"',
        year=2026,
        subtype="Instituto/Autarquia",
    )

    item = response["resultados"][0]
    assert item["processo"] == "TC 01653/26"
    assert item["procuradora"] == PROCURADORA
    assert item["pagina"] == 7
    assert item["documento"] == "parecer.pdf"
    assert item["natureza"] == SEARCH_NATURE
    assert item["criterio_recuperacao"] == (
        'Expressão pesquisada: "contribuição previdenciária"'
    )
    assert item["sha256_registrado"] == "a" * 64
    assert library.downloads == []
    assert len(item["fonte_versao"]) == 64
    call = library.repository.calls[0]
    assert call[1:3] == (PROCURADORA.casefold(), EXTRACTOR_VERSION)
    assert call[3]["year"] == 2026


def test_legal_reference_results_are_ordered_by_relevance_and_paginated():
    rows = [
        _result("TC 01499/26", 0.4, referencia_id="reference-2"),
        _result("TC 02720/25", 1.7, referencia_id="reference-3"),
        _result("TC 02650/25", 0.9, referencia_id="reference-4"),
    ]
    service = MinutasSearchService(Library(rows))

    response = service.search("art. 37 da Constituição", page_size=2)

    assert [item["processo"] for item in response["resultados"]] == [
        "TC 02720/25",
        "TC 02650/25",
    ]
    assert response["tem_proxima"] is True


def test_empty_search_and_live_replacement_or_deletion_are_not_cached():
    library = Library([])
    service = MinutasSearchService(library)

    assert service.search("prescrição")["resultados"] == []
    library.repository.rows = [_result(arquivo_sha256="b" * 64)]
    replaced = service.search("prescrição")["resultados"]
    assert replaced and replaced[0]["fonte_versao"]
    library.repository.rows.clear()
    assert service.search("prescrição")["resultados"] == []
    assert len(library.repository.calls) == 3


def test_search_isolated_by_prosecutor_and_authorized_server_side():
    denied = Library([_result()], authorized=False)
    with pytest.raises(MinutasAccessDenied):
        MinutasSearchService(denied).search("licitação")
    assert denied.repository.calls == []

    allowed = Library([_result()])
    with pytest.raises(MinutasValidationError, match="contexto selecionado"):
        MinutasSearchService(allowed).search(
            "licitação", procuradora="Outra Procuradora"
        )
    assert allowed.repository.calls == []
    downloaded = MinutasSearchService(allowed).download_opinion("reference-1")
    assert downloaded.name == "parecer.pdf"
    assert allowed.downloads == [("reference-1", "parecer")]


def test_search_recovers_opinion_without_exercise_in_aposentadorias():
    library = Library([_result(exercicio=None, subtipo=None)])
    library.specialty_id = "elvira.pessoal.aposentadorias"
    response = MinutasSearchService(library).search("registro de aposentadoria")
    assert response["especialidade_id"] == "elvira.pessoal.aposentadorias"
    assert response["resultados"][0]["exercicio"] is None
    assert library.repository.calls[0][3]["year"] is None
    assert library.repository.calls[0][3]["specialty_id"] == "elvira.pessoal.aposentadorias"


def test_four_pilot_processes_remain_searchable():
    processes = ("TC 01653/26", "TC 01499/26", "TC 02720/25", "TC 02650/25")
    rows = [
        _result(process, 1 - index / 10, referencia_id=f"reference-{index}")
        for index, process in enumerate(processes)
    ]

    response = MinutasSearchService(Library(rows)).search("prestação de contas")

    assert {item["processo"] for item in response["resultados"]} == set(processes)


class QueryResult:
    def __init__(self, rows=()):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self):
        self.query = None
        self.parameters = None

    def execute(self, query, parameters=()):
        self.query = query
        self.parameters = parameters
        return QueryResult()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class Store:
    backend = "postgresql"

    def __init__(self):
        self.connection_instance = Connection()

    def connection(self, **_kwargs):
        return self.connection_instance


def test_postgresql_query_filters_valid_opinions_and_blocks_cross_prosecutor_linkage():
    from database.postgresql import parameters
    from psycopg._queries import PostgresQuery
    from psycopg.adapt import Transformer

    store = Store()
    repository = MinutasReferenciasStore(store)

    repository.search_opinions(
        "Lei 8.429",
        PROCURADORA.casefold(),
        EXTRACTOR_VERSION,
        specialty_id="elvira.pca.rpps",
        year=2025,
        subtype="Prefeitura",
        exclude_reference_id="target-reference",
        exclude_process_key="0165326",
        exclude_paths=("target/parecer.pdf",),
        exclude_hashes=("a" * 64,),
        limit=10,
        offset=0,
    )

    sql = store.connection_instance.query
    assert "websearch_to_tsquery('portuguese',?)" in sql
    assert "ts_rank_cd" in sql
    assert "e.papel='parecer'" in sql
    assert "papel='auditoria'" not in sql
    assert "auditoria_path" not in sql
    assert "e.status='PROCESSADO'" in sql
    assert "e.extrator_versao=?" in sql
    assert "e.procuradora_chave=?" in sql
    assert "r.procuradora_chave=?" in sql
    assert "e.procuradora_chave=r.procuradora_chave" in sql
    assert "doc.path=e.arquivo_path" in sql
    assert "doc.nome=e.arquivo_nome" in sql
    assert "doc.mime=e.arquivo_mime" in sql
    assert "doc.tamanho=e.arquivo_tamanho" in sql
    assert "mv.especialidade_id=?" in sql
    assert "mc.estado='CLASSIFICADO'" in sql
    assert "e.arquivo_sha256<>repeat('0',64)" in sql
    assert "e.referencia_id<>?" in sql
    assert "r.processo_chave<>?" in sql
    assert "e.arquivo_path<>?" in sql
    assert "e.arquivo_sha256<>?" in sql
    assert "LIMIT ? OFFSET ?" in sql
    assert store.connection_instance.parameters[3:5] == (
        PROCURADORA.casefold(),
        PROCURADORA.casefold(),
    )
    assert store.connection_instance.parameters[-2:] == (11, 0)
    adapted = PostgresQuery(Transformer())
    adapted.convert(parameters(sql), store.connection_instance.parameters)
    assert len(adapted.params) == len(store.connection_instance.parameters)


def test_result_context_uses_ranked_fragments_around_distinct_occurrences():
    store = Store()
    repository = MinutasReferenciasStore(store)

    repository.search_opinions(
        "contribuição previdenciária",
        PROCURADORA.casefold(),
        EXTRACTOR_VERSION,
        specialty_id="elvira.pca.rpps",
    )

    sql = store.connection_instance.query
    assert sql.count("ts_headline('portuguese',unit->>'texto',q.terms") == 2
    assert "MaxWords=180" in sql
    assert "MaxFragments=2" in sql
    assert "LEFT(unit->>'texto',6000) AS contexto" not in sql
    assert "SELECT DISTINCT" not in sql


def test_search_index_sql_is_additive_idempotent_and_keeps_access_private():
    sql = (
        Path(__file__).resolve().parents[1]
        / "database"
        / "minutas_pesquisa_juridica.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE INDEX IF NOT EXISTS" in sql
    assert "USING GIN" in sql
    assert "to_tsvector('portuguese'" in sql
    assert "papel = 'parecer' AND status = 'PROCESSADO'" in sql
    assert "auditoria" not in sql
    assert "REVOKE ALL" in sql
    assert "PUBLIC, anon, authenticated" in sql
    assert "GRANT " not in sql


def test_search_ui_queries_only_after_submit_and_keeps_result_on_rerun(monkeypatch):
    from services import minutas_ia_ui

    calls = []

    class Search:
        def __init__(self, _library):
            pass

        def search(self, query, **filters):
            calls.append((query, filters))
            return {
                "consulta": query,
                "pagina": filters["page"],
                "tamanho_pagina": 10,
                "tem_anterior": False,
                "tem_proxima": False,
                "resultados": [
                    {
                        "referencia_id": "reference-1",
                        "processo": "TC 01653/26",
                        "subtipo": "Instituto/Autarquia",
                        "procuradora": PROCURADORA,
                        "exercicio": 2026,
                        "documento": "parecer.pdf",
                        "pagina": 7,
                        "trecho": "Trecho histórico localizado.",
                        "criterio_recuperacao": f"Expressão pesquisada: {query}",
                        "contexto": "Contexto da página.",
                    }
                ],
            }

    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args: Library())
    monkeypatch.setattr(minutas_ia_ui, "MinutasSearchService", Search)

    def page():
        from services.minutas_ia_ui import _search_tab

        _search_tab(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    assert calls == []
    app.text_input[0].set_value("contribuição previdenciária")
    app.button[0].click().run()
    assert len(calls) == 1
    assert any("Trecho histórico localizado" in item.value for item in app.markdown)
    app.run()
    assert len(calls) == 1
    assert app.button(key="minutas_search_back")
    app.button(key="minutas_search_back").click().run()
    assert "minutas_legal_search" not in app.session_state
    assert not any("Trecho histórico localizado" in item.value for item in app.markdown)
    assert len(calls) == 1
