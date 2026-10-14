"""Regressões de especialidades em banco e Storage sintéticos, sem rede/IA."""

from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
import uuid

import pytest

from database.minutas_especialidades import MinutasSpecialtiesStore
from database.minutas_referencias import MinutasReferenciasStore, COLUMNS, COMPLEMENT_COLUMNS, EXTRACTION_COLUMNS
from services.minutas_acervo import (
    MinutasAcervoService, display_process_key, identify_process_keys, inventory, migration_plan,
)
from services.minutas_extraction import EXTRACTOR_VERSION, MinutasExtractionService
from services.minutas_library import MinutasLibraryService, MinutasAccessDenied, MinutasValidationError
from services.minutas_specialties import CATALOG, CLASSES, PROSECUTOR_KEY, RPPS, scope, suggest
from tests.access_testing import TEST_IDENTITY
from tests.test_minutas_extraction import _pdf as _text_pdf
from tests.test_minutas_library import _docx, _pdf


class SyntheticStore:
    backend = "sqlite"

    def __init__(self, path):
        self.path = path
        self.queries = []

    @contextmanager
    def connection(self, **kwargs):
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        c.set_trace_callback(self.queries.append)
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()


class Storage:
    def __init__(self):
        self.objects = {}
        self.downloads = []

    def upload(self, path, content, mime):
        assert path not in self.objects
        self.objects[path] = content

    def download(self, path):
        self.downloads.append(path)
        return self.objects[path]

    def delete(self, path):
        self.objects.pop(path, None)


def library(store, storage, specialty=RPPS):
    # Only the production constructor requires PostgreSQL; exercise the SAME SQL
    # repository against synthetic relational tables, not a dict implementation.
    repo = object.__new__(MinutasReferenciasStore)
    repo.store, repo.specialty_id, repo.include_acervo = store, specialty, True
    return MinutasLibraryService(repo, storage, TEST_IDENTITY,
        {"MINUTAS_PILOTO_ENABLED": True, "MINUTAS_PILOTO_EMAILS": [TEST_IDENTITY["email"]]})


def build_store(path, *, separated=False):
    store = SyntheticStore(path)
    with store.connection() as c:
        for table, columns in (("minutas_referencias", COLUMNS), ("minutas_documentos_complementares", COMPLEMENT_COLUMNS),
                               ("minutas_extracoes", EXTRACTION_COLUMNS)):
            definitions = ["id TEXT PRIMARY KEY"] + [name + " " + ("INTEGER" if name in ("exercicio", "posicao") or "tamanho" in name else "TEXT")
                for name in columns.split(",") if name != "id"]
            if table == "minutas_extracoes": definitions.append("UNIQUE(arquivo_path)")
            if table == "minutas_referencias" and separated:
                definitions.append("acervo TEXT NOT NULL DEFAULT 'ativo'")
            elif table == "minutas_referencias":
                definitions.append("UNIQUE(procuradora_chave,processo_chave)")
            c.execute(f"CREATE TABLE {table} (" + ",".join(definitions) + ")")
        if separated:
            c.execute("CREATE UNIQUE INDEX minutas_referencias_processo_ativo_uq ON minutas_referencias(procuradora_chave,processo_chave) WHERE acervo='ativo'")
            c.execute("CREATE UNIQUE INDEX minutas_referencias_processo_legado_uq ON minutas_referencias(procuradora_chave,processo_chave) WHERE acervo='legado'")
    return store


@pytest.fixture
def environment(tmp_path):
    store = build_store(tmp_path / "minutas.db")
    with store.connection() as c:
        sql = (Path(__file__).resolve().parents[1] / "database/minutas_especialidades.sql").read_text(encoding="utf-8")
        sql = re.sub(r"--[^\n]*", "", sql).replace("mpc_portarias.", "").replace("::text", "")
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE OR REPLACE VIEW")):
                c.execute(statement.replace("CREATE OR REPLACE VIEW", "CREATE VIEW"))
        for profile in CATALOG:
            c.execute("INSERT INTO minutas_especialidades(id,procuradora_chave,grupo,nome,perfil,estado,motor) VALUES(?,?,?,?,?,?,?)",
                tuple(profile[key] for key in ("id", "procuradora_chave", "grupo", "nome", "perfil", "estado", "motor")))
    return store, Storage()


@pytest.fixture
def separated(tmp_path):
    store = build_store(tmp_path / "minutas.db", separated=True)
    with store.connection() as c:
        sql = (Path(__file__).resolve().parents[1] / "database/minutas_especialidades.sql").read_text(encoding="utf-8")
        sql = re.sub(r"--[^\n]*", "", sql).replace("mpc_portarias.", "").replace("::text", "")
        for statement in sql.split(";"):
            statement = statement.strip()
            if statement.startswith(("CREATE TABLE", "CREATE INDEX", "CREATE OR REPLACE VIEW")):
                c.execute(statement.replace("CREATE OR REPLACE VIEW", "CREATE VIEW"))
        for profile in CATALOG:
            c.execute("INSERT INTO minutas_especialidades(id,procuradora_chave,grupo,nome,perfil,estado,motor) VALUES(?,?,?,?,?,?,?)",
                tuple(profile[key] for key in ("id", "procuradora_chave", "grupo", "nome", "perfil", "estado", "motor")))
    return store, Storage()


def seed(store, *, process="TC 01653/26", subtype="Instituto/Autarquia", entity="Instituto de Previdência do Município de Taperoá",
         nature="Prestação de Contas Anual", prosecutor=PROSECUTOR_KEY):
    identifier = str(uuid.uuid4())
    record = {key: None for key in COLUMNS.split(",")}
    record.update(id=identifier, procuradora="Elvira Samara" if prosecutor == PROSECUTOR_KEY else "Outra",
        procuradora_chave=prosecutor, numero_processo=process, processo_chave=re.sub(r"\D", "", process),
        exercicio=2026, subtipo=subtype, criado_em="1", criado_por="teste", atualizado_em="1", atualizado_por="teste")
    with store.connection() as c:
        for role in ("auditoria", "parecer"):
            record.update({role + "_nome": role + ".pdf", role + "_mime": "application/pdf",
                           role + "_tamanho": 100, role + "_path": identifier + "/" + role + ".pdf"})
        c.execute("INSERT INTO minutas_referencias (" + COLUMNS + ") VALUES(" + ",".join("?" for _ in record) + ")", tuple(record.values()))
        for role in ("auditoria", "parecer"):
            text = f"Origem: {entity}\nNatureza: {nature}\nConteúdo sintético {identifier} {role}"
            extraction = {key: "" for key in EXTRACTION_COLUMNS.split(",")}
            extraction.update(id=str(uuid.uuid4()), referencia_id=identifier, documento_id=None, papel=role,
                tipo="Relatório da Auditoria" if role == "auditoria" else "Parecer definitivo", posicao=0,
                procuradora=record["procuradora"], procuradora_chave=prosecutor, arquivo_nome=role + ".pdf",
                arquivo_mime="application/pdf", arquivo_tamanho=100, arquivo_path=record[role + "_path"],
                arquivo_sha256=sha256(text.encode()).hexdigest(), status="PROCESSADO", extrator_versao=EXTRACTOR_VERSION,
                texto_integral=text, unidades_json=json.dumps([{"pagina": 1, "texto": text}]), secoes_json="[]", atualizado_em="1")
            c.execute("INSERT INTO minutas_extracoes (" + EXTRACTION_COLUMNS + ") VALUES(" + ",".join("?" for _ in extraction) + ")", tuple(extraction.values()))
    return record


def test_catalog_separates_classes_themes_and_only_preserves_rpps_engine():
    assert len(CATALOG) == 24
    assert len({p["id"] for p in CATALOG}) == 24
    assert [p["id"] for p in CATALOG if p["motor"]] == [RPPS]
    assert scope(RPPS)["estado"] == "Funcional com ajustes"
    assert all(p["estado"] == "A iniciar" for p in CATALOG if p["id"] != RPPS)
    assert scope("elvira.tema.licitacoes")["grupo"] == "TEMATICO"
    assert "LICITACAO" not in CLASSES


@pytest.mark.parametrize(("entity", "subtype", "nature", "expected"), [
    ("Instituto de Previdência do Município de Taperoá", "Instituto/Autarquia", "PCA", RPPS),
    ("Câmara Municipal de Exemplo", "Câmara Municipal", "PCA", "elvira.pca.camaras"),
    ("Secretaria Municipal de Educação", "Secretaria Municipal ou Estadual", "PCA", "elvira.pca.secretarias_municipais"),
    ("Secretaria de Estado da Saúde", "Secretaria Municipal ou Estadual", "PCA", "elvira.pca.orgaos_estaduais"),
    ("Prefeitura de Exemplo", "Prefeitura", "PCA — Contas de Governo", "elvira.pca.governo"),
    ("Prefeitura de Exemplo", "Prefeitura", "PCA — Contas de Gestão", "elvira.pca.gestao"),
    ("Prefeitura de Exemplo", "Prefeitura", "PCA", None),
    ("Secretaria de Educação", "Secretaria Municipal ou Estadual", "PCA", None),
    ("Instituto de Previdência de Exemplo", "Câmara Municipal", "PCA", None),
    ("Instituto de Previdência de Exemplo", "Instituto/Autarquia", "Aposentadoria", None),
    ("Estado de Exemplo", None, "Aposentadoria", "elvira.pessoal.aposentadorias"),
])
def test_classification_uses_evidence_not_example_process_number(entity, subtype, nature, expected):
    record = {"id": "a", "subtipo": subtype, "procuradora_chave": PROSECUTOR_KEY, "numero_processo": "01653/26"}
    result = suggest(record, [{"referencia_id": "a", "procuradora_chave": PROSECUTOR_KEY,
                              "texto_integral": f"Origem: {entity}\nNatureza: {nature}"}])
    assert result["especialidade_id"] == expected
    assert (result["estado"] == "CLASSIFICADO") == bool(expected)


def test_migration_preserves_ids_files_extractions_and_is_idempotent(environment):
    store, storage = environment
    records = [seed(store), seed(store, process="01499/26", subtype="Câmara Municipal", entity="Câmara Municipal de Exemplo"),
        seed(store, process="02650/25", subtype="Secretaria Municipal ou Estadual", entity="Secretaria de Educação"),
        seed(store, process="02720/25", subtype="Prefeitura", entity="Prefeitura de Exemplo"),
        seed(store, process="09999/26", entity="Instituto de Previdência de Outra Cidade")]
    service = MinutasAcervoService(library(store, storage))
    before = service.repository.snapshot()
    plan = service.dry_run()
    assert plan["contagens"]["processos"] == 5
    assert plan["contagens"]["arquivos_vinculados"] == 10
    assert plan["resultados"] == {"CLASSIFICADO": 3, "PENDENTE": 2}
    with pytest.raises(ValueError, match="backup"):
        service.apply(plan, {})
    applied = service.apply(plan, {"identificador": "backup-sintetico-restaurado", "restauracao_verificada": True})
    assert applied["alterados"] == 5
    after = service.repository.snapshot()
    for key in ("processos", "documentos", "extracoes"):
        assert before[key] == after[key]
    assert applied["antes"] == applied["depois"]
    assert service.apply(service.dry_run(), {"identificador": "teste", "restauracao_verificada": True})["alterados"] == 0
    assert len(service.repository.list_records(pending=True)) == 2
    assert {r["id"] for r in service.repository.list_records()} == {r["id"] for r in records}
    assert storage.objects == {} and storage.downloads == []


def test_stale_plan_and_transaction_failure_do_not_modify_classification(environment, monkeypatch):
    store, storage = environment
    target = seed(store)
    service = MinutasAcervoService(library(store, storage))
    plan = service.dry_run()
    with store.connection() as c:
        c.execute("UPDATE minutas_referencias SET atualizado_em='2' WHERE id=?", (target["id"],))
    with pytest.raises(ValueError, match="Inventário alterado"):
        service.apply(plan, {"identificador": "teste", "restauracao_verificada": True})
    plan = service.dry_run()
    original = service.repository._event
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Falha sintética")
    monkeypatch.setattr(service.repository, "_event", fail)
    with pytest.raises(RuntimeError):
        service.apply(plan, {"identificador": "teste", "restauracao_verificada": True})
    assert service.repository.classification(target["id"]) is None
    assert service.repository.events(target["id"]) == []


def test_manual_correction_reversal_controlled_links_and_selective_revision(environment):
    store, storage = environment
    target = seed(store)
    scoped = library(store, storage)
    admin = MinutasAcervoService(scoped)
    untouched = library(store, storage, "elvira.pessoal.pensoes").repository.scope_revision()
    admin.classify(target["id"], RPPS, "PCA", "Instituto de Previdência", "Identificação documental")
    first_revision = scoped.repository.scope_revision()
    admin.link(target["id"], "elvira.tema.contratos", "Contrato analisado no parecer")
    assert len(library(store, storage, "elvira.tema.contratos").list()) == 1
    assert admin.link(target["id"], "elvira.tema.contratos", "Mesmo vínculo") is False
    admin.classify(target["id"], "elvira.pca.autarquias", "PCA", "Autarquia", "Correção humana")
    assert scoped.list() == []
    event = admin.repository.events(target["id"])[0]
    admin.restore(event["id"])
    assert len(scoped.list()) == 1
    assert len(library(store, storage, "elvira.tema.contratos").list()) == 1
    assert first_revision != scoped.repository.scope_revision()
    assert untouched == library(store, storage, "elvira.pessoal.pensoes").repository.scope_revision()
    with pytest.raises(ValueError, match="posterior"):
        admin.restore(event["id"])


def test_specialty_sql_isolation_download_context_missing_scope_and_other_prosecutor(environment):
    store, storage = environment
    target = seed(store)
    chamber = seed(store, process="01499/26", subtype="Câmara Municipal", entity="Câmara Municipal de Exemplo")
    foreign = seed(store, process="99999/26", prosecutor="outra")
    scoped = library(store, storage)
    admin = MinutasAcervoService(scoped)
    admin.apply(admin.dry_run(), {"identificador": "teste", "restauracao_verificada": True})
    assert [r["id"] for r in scoped.list()] == [target["id"]]
    assert scoped.repository.get(chamber["id"]) is None
    assert scoped.repository.list_documents(chamber["id"]) == []
    assert scoped.repository.list_extractions(chamber["id"]) == []
    assert scoped.repository.get_extraction(chamber["parecer_path"]) is None
    with pytest.raises(ValueError): scoped.download(chamber["id"], "parecer")
    with pytest.raises(ValueError): admin.classify(foreign["id"], RPPS, "PCA", "X", "X")
    rows = MinutasExtractionService(scoped).context_sources(target["id"])
    assert all(row["referencia_id"] == target["id"] and row["papel"] == "auditoria" for row in rows)
    with pytest.raises(ValueError, match="especialidade"):
        MinutasExtractionService(library(store, storage, None)).context_sources(target["id"])
    assert storage.downloads == []
    assert any("mv.especialidade_id='elvira.pca.rpps'" in query for query in store.queries)


def test_bulk_upload_dedup_versions_models_rollback_and_authorization(environment):
    store, storage = environment
    scoped = library(store, storage)
    admin = MinutasAcervoService(scoped)
    values = {"numero_processo": "01234/26", "exercicio": 2026, "classe": "PCA", "origem": "Instituto de Previdência",
              "fundamento": "Conferido pelo Assessor"}
    first = admin.add_file(RPPS, values, ("auditoria.pdf", _pdf(b"v1")), role="auditoria")
    assert first["estado"] == "CADASTRADO"
    assert admin.add_file(RPPS, values, ("copia.pdf", _pdf(b"v1")), role="auditoria")["estado"] == "DUPLICADO"
    with pytest.raises(ValueError, match="nova versão"):
        admin.add_file(RPPS, values, ("auditoria.pdf", _pdf(b"v2")), role="auditoria")
    second = admin.add_file(RPPS, values, ("auditoria.pdf", _pdf(b"v2")), role="auditoria", confirm_version=True)
    assert second["path"] != first["path"]
    model = admin.add_file(RPPS, values, ("modelo.pdf", _pdf(b"modelo")), role="modelo")
    documents = MinutasExtractionService(scoped)._documents(scoped.get(first["referencia_id"]))
    assert len(documents) == 2 and all(d["papel"] == "auditoria" for d in documents)
    assert model["path"] not in {d["path"] for d in documents}
    assert len(storage.objects) == 3
    with store.connection() as c:
        c.execute("CREATE TRIGGER fail_new BEFORE INSERT ON minutas_acervo_documentos BEGIN SELECT RAISE(ABORT,'falha'); END")
    with pytest.raises(sqlite3.IntegrityError):
        admin.add_file(RPPS, {**values, "numero_processo": "01235/26"}, ("erro.pdf", _pdf(b"erro")), role="parecer")
    assert len(scoped.list()) == 1 and len(storage.objects) == 3
    scoped.identity["email_verified"] = False
    with pytest.raises(MinutasAccessDenied): admin.add_file(RPPS, values, ("x.pdf", _pdf()), role="parecer")


def test_state_requires_human_validation_and_never_enables_new_engine(environment):
    store, storage = environment
    admin = MinutasAcervoService(library(store, storage))
    with pytest.raises(ValueError, match="revisão humana"):
        admin.update_state("elvira.pca.camaras", {"estado": "Validada"})
    admin.update_state("elvira.pca.camaras", {"estado": "Validada", "revisao_humana": True, "resultados_testes": "Comparação revisada", "casos_testados": 2})
    selected = next(p for p in admin.catalog() if p["id"] == "elvira.pca.camaras")
    assert selected["estado"] == "Validada" and selected["motor"] == ""


def test_rpps_generation_not_limited_to_four_examples_and_other_specialty_never_sent_to_ai():
    from tests.test_minutas_generation import _scenario, _record, _row, Library, _analysis_payload, _draft_result
    from services.minutas_generation import MinutasGenerationService, MinutasGenerationError
    target, history, foreign, rows = _scenario()
    target.update(numero_processo="TC 12345/26", processo_chave="1234526")
    chamber = _record("chamber", "TC 54321/26", "5432126")
    chamber["especialidade_id"] = "elvira.pca.camaras"
    rows.append(_row(chamber, "parecer", "PARECER EXCLUSIVO DA CAMARA"))
    lib = Library([target, history, foreign, chamber], rows)
    sent = []
    def analyze(context):
        sent.append(context)
        return {"analise": _analysis_payload(context)}
    def generate(context):
        sent.append(context)
        return _draft_result(context)
    service = MinutasGenerationService(lib, analyze=analyze, generate=generate)
    assert target in service.targets() and chamber not in service.targets()
    result = service.generate(target["id"], service.analyze(target["id"]), use_rag=False)
    assert result["processo_alvo_chave"] == "1234526"
    assert "PARECER EXCLUSIVO DA CAMARA" not in json.dumps(sent, ensure_ascii=False)
    assert "SEGREDO DO PARECER-ALVO" not in json.dumps(sent, ensure_ascii=False)
    lib.specialty_id = "elvira.pca.camaras"
    with pytest.raises(MinutasGenerationError, match="motor"):
        MinutasGenerationService(lib, analyze=analyze).targets()


def test_search_fails_closed_without_scope():
    from tests.test_minutas_search import Library
    from services.minutas_search import MinutasSearchService
    library = Library()
    library.specialty_id = None
    with pytest.raises(ValueError, match="especialidade"):
        MinutasSearchService(library).search("previdência")
    assert library.repository.calls == []


def test_inventory_before_schema_matches_catalog_and_paginates_pending(environment):
    store, storage = environment
    first = seed(store)
    seed(store, process="98765/26", subtype="Prefeitura", entity="Prefeitura Municipal")
    repo = MinutasSpecialtiesStore(store)
    assert inventory(repo.legacy_snapshot()) == inventory(repo.snapshot())
    assert len(repo.list_records(pending=True, page=1, limit=1)) == 1
    assert len(repo.list_records(pending=True, page=2, limit=1)) == 1
    assert repo.list_records(pending=True, page=3, limit=1) == []
    assert [r["id"] for r in repo.list_records(query="01653", role="parecer")] == [first["id"]]
    assert repo.list_records(role="modelo") == []
    assert repo.dashboard()["pendentes"] == 2
    assert sum(r["processos"] for r in repo.dashboard()["especialidades"]) == 0


def test_missing_relations_keep_the_module_closed(environment, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from services import minutas_ia_ui
    store, storage = environment
    with store.connection() as c:
        c.execute("DROP VIEW minutas_documentos_catalogo")
        for table in ("minutas_acervo_documentos", "minutas_acervo_vinculos", "minutas_classificacoes",
                      "minutas_especialidades_eventos", "minutas_especialidades"):
            c.execute("DROP TABLE " + table)
    assert MinutasSpecialtiesStore(store).missing_relations() == [
        "minutas_especialidades", "minutas_classificacoes", "minutas_acervo_vinculos",
        "minutas_especialidades_eventos", "minutas_acervo_documentos", "minutas_documentos_catalogo"]
    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *args, administrative=False:
        library(store, storage, None if administrative else st.session_state.get("minutas_specialty")))
    def page():
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY
        render(None, TEST_IDENTITY)
    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception
    assert any("Especialidades indisponíveis" in item.value and "minutas_especialidades" in item.value for item in app.warning)
    assert all(getattr(item, "key", None) != "minutas_specialty" for item in app.selectbox)


def test_specialized_ui_preserves_selection_and_does_not_call_ai_or_read_pdfs(environment, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from services import minutas_ia_ui, ai_service
    store, storage = environment
    seed(store)
    seed(store, process="02720/25", subtype="Prefeitura", entity="Prefeitura Municipal de Exemplo")
    admin = MinutasAcervoService(library(store, storage))
    admin.apply(admin.dry_run(), {"identificador": "teste", "restauracao_verificada": True})
    def no_ai(*args, **kwargs):
        raise AssertionError("Navegação não pode chamar IA")
    monkeypatch.setattr(ai_service, "analisar_fundamentos_minuta", no_ai)
    monkeypatch.setattr(ai_service, "gerar_minuta_parecer", no_ai)
    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *args, administrative=False:
        library(store, storage, None if administrative else st.session_state.get("minutas_specialty")))
    def page():
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY
        render(None, TEST_IDENTITY)
    app = AppTest.from_function(page, default_timeout=30).run()
    assert not app.exception and not app.error
    assert app.selectbox(key="minutas_specialty").value == RPPS
    assert app.radio(key="minutas_active_view").value == "Acervo"
    app.radio(key="minutas_active_view").set_value("Pesquisa Jurídica").run()
    assert not app.exception
    assert app.selectbox(key="minutas_specialty").value == RPPS
    app.radio(key="minutas_active_view").set_value("Gerar Minuta").run()
    assert not app.exception and not app.error
    app.session_state["minutas_legal_search"] = {"outro": "resultado antigo"}
    app.selectbox(key="minutas_specialty").set_value("elvira.pca.camaras").run()
    assert not app.exception
    assert "minutas_legal_search" not in app.session_state
    assert any("ainda não habilitada" in info.value for info in app.info)
    app.radio(key="minutas_active_view").set_value("Acervo").run()
    assert "Pendências de classificação" not in app.radio(key="minutas_acervo_mode").options
    app.radio(key="minutas_acervo_mode").set_value("Administração").run()
    assert not app.exception and not app.error
    assert any("sem classificação: 1" in caption.value for caption in app.caption)
    assert storage.downloads == []
    assert not any("ts_headline" in query for query in store.queries)


def test_batch_processing_summary_declares_total_success_only_when_every_required_step_succeeds():
    from services.minutas_specialties_ui import batch_processing_summary
    complete = batch_processing_summary(
        [{"nome": "a.pdf", "estado": "CADASTRADO", "extracao": "PROCESSADO"},
         {"nome": "b.pdf", "estado": "CADASTRADO", "extracao": "PROCESSADO"}], "parecer")
    assert complete["complete"] and complete["cadastrados"] == 2 and complete["processadas"] == 2 and complete["falhas"] == 0
    modelo = batch_processing_summary(
        [{"nome": "m.docx", "estado": "CADASTRADO"}, {"nome": "n.docx", "estado": "CADASTRADO"}], "modelo")
    assert modelo["complete"] and modelo["processadas"] == 0
    assert not batch_processing_summary(
        [{"nome": "a.pdf", "estado": "CADASTRADO", "extracao": "ATENCAO"}], "parecer")["complete"]
    assert not batch_processing_summary(
        [{"nome": "a.pdf", "estado": "DUPLICADO"}], "parecer")["complete"]
    assert not batch_processing_summary(
        [{"nome": "a.pdf", "falha": "HTTP 400"}], "parecer")["complete"]


def test_batch_upload_ui_keeps_a_single_themed_confirmation_across_reruns(environment, monkeypatch):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from services import minutas_ia_ui, ai_service
    store, storage = environment
    seed(store)
    monkeypatch.setattr(ai_service, "analisar_fundamentos_minuta", lambda *a, **k: (_ for _ in ()).throw(AssertionError("IA")))
    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *args, administrative=False:
        library(store, storage, None if administrative else st.session_state.get("minutas_specialty")))
    def page():
        from services.minutas_ia_ui import render
        from tests.access_testing import TEST_IDENTITY
        render(None, TEST_IDENTITY)
    app = AppTest.from_function(page, default_timeout=30).run()
    app.radio(key="minutas_acervo_mode").set_value("Adicionar documentos").run()
    app.session_state["minutas_batch_result_" + RPPS] = {
        "signature": "lote-persistido",
        "summary": {"cadastrados": 2, "processadas": 2, "atencao": 0, "duplicados": 0, "falhas": 0, "complete": True},
        "details": [
            {"nome": "parecer taperoá.pdf", "estado": "CADASTRADO", "extracao": "PROCESSADO"},
            {"nome": "relatorio.pdf", "estado": "CADASTRADO", "extracao": "PROCESSADO"},
        ],
    }
    app.run()
    html = " ".join(item.value for item in app.markdown)
    assert html.count("Cadastro e processamento concluídos!") == 1
    assert "2 documento(s) cadastrado(s)" in html
    assert "2 extração(ões) PROCESSADA(S)" in html
    assert "0 falha(s)" in html
    assert not app.success and not app.warning and not app.error
    assert "Ver detalhes do processamento" in [item.label for item in app.expander]
    app.radio(key="minutas_active_view").set_value("Pesquisa Jurídica").run()
    app.radio(key="minutas_active_view").set_value("Acervo").run()
    app.radio(key="minutas_acervo_mode").set_value("Adicionar documentos").run()
    html = " ".join(item.value for item in app.markdown)
    assert html.count("Cadastro e processamento concluídos!") == 1
    assert "minutas_batch_result_" + RPPS in app.session_state
    app.session_state["minutas_batch_result_" + RPPS] = {
        "signature": "lote-parcial",
        "summary": {"cadastrados": 1, "processadas": 0, "atencao": 1, "duplicados": 1, "falhas": 0, "complete": False},
        "details": [
            {"nome": "a.pdf", "estado": "CADASTRADO", "extracao": "ATENCAO", "extracao_erro": "texto insuficiente"},
            {"nome": "b.pdf", "estado": "DUPLICADO"},
        ],
    }
    app.run()
    html = " ".join(item.value for item in app.markdown)
    assert "Cadastro e processamento concluídos!" not in html
    assert "concluídos com ressalvas" in html
    assert "1 duplicidade(s)" in html
    assert not app.success


def test_duplicate_legacy_without_hash_does_not_require_reupload(environment):
    store, storage = environment
    target = seed(store)
    scoped = library(store, storage)
    admin = MinutasAcervoService(scoped)
    admin.apply(admin.dry_run(), {"identificador": "teste", "restauracao_verificada": True})
    with store.connection() as c:
        c.execute("DELETE FROM minutas_extracoes WHERE referencia_id=?", (target["id"],))
    content = _pdf(b"legado")
    storage.objects[target["auditoria_path"]] = content
    storage.objects[target["parecer_path"]] = _pdf(b"parecer")
    result = admin.add_file(RPPS, {"numero_processo": "01653/26", "exercicio": 2026, "classe": "PCA", "fundamento": "Conferido"},
                           ("outra-grafia.pdf", content), role="auditoria")
    assert result["estado"] == "DUPLICADO"
    assert len(storage.objects) == 2


def test_pending_pg_uuid_representation_is_idempotent(environment):
    store, storage = environment
    target = seed(store, subtype="Prefeitura", entity="Prefeitura Municipal")
    admin = MinutasAcervoService(library(store, storage))
    backup = {"identificador": "teste", "restauracao_verificada": True}
    admin.apply(admin.dry_run(), backup)
    original = store.connection
    class UUIDCursor:
        def __init__(self, cursor): self.cursor = cursor
        def fetchone(self):
            row = self.cursor.fetchone()
            return dict(row, referencia_id=uuid.UUID(row["referencia_id"])) if row else None
    class Connection:
        def __init__(self, c): self.c = c
        def execute(self, query, args=()):
            cursor = self.c.execute(query, args)
            return UUIDCursor(cursor) if query.startswith("SELECT * FROM minutas_classificacoes WHERE") else cursor
    @contextmanager
    def connection(**kwargs):
        with original(**kwargs) as c: yield Connection(c)
    store.connection = connection
    assert admin.apply(admin.dry_run(), backup)["alterados"] == 0


def test_storage_inventory_reports_missing_and_unlinked_without_deleting():
    from services.minutas_acervo import storage_inventory
    snapshot = {"documentos": [{"path": "ok.pdf"}, {"path": "ausente.pdf"}]}
    objects = [{"name": "ok.pdf"}, {"name": "outro-acervo.pdf"}]
    report = storage_inventory(snapshot, objects)
    assert report["vinculos_sem_objeto"] == ["ausente.pdf"]
    assert report["objetos_sem_vinculo_no_escopo"] == ["outro-acervo.pdf"]
    assert objects == [{"name": "ok.pdf"}, {"name": "outro-acervo.pdf"}]


def test_conflicting_formal_class_is_not_silently_assigned_to_rpps():
    record = {"id": "a", "procuradora_chave": PROSECUTOR_KEY, "subtipo": "Instituto/Autarquia", "classe": "CONSULTA"}
    result = suggest(record, [{"referencia_id": "a", "procuradora_chave": PROSECUTOR_KEY,
        "texto_integral": "Origem: Instituto de Previdência\nNatureza: Prestação de Contas Anual"}])
    assert result["estado"] == "PENDENTE" and result["especialidade_id"] is None


def test_identify_process_keys_reads_filename_and_keeps_numbering_year_off_the_accounts_year():
    name_keys, text_keys, years = identify_process_keys(
        "proc_02119_26_relatorio_de_analise_defesa.pdf",
        "Processo TC nº 02119/20\nExercício: 2026",
    )
    assert name_keys == {"0211926"} and text_keys == {"0211920"} and years == {2026}
    name_keys, text_keys, years = identify_process_keys(
        "TC 02434/25 - Instituto.pdf",
        "TC n.º 02434/25\nExercício das contas: 2024",
    )
    assert name_keys == text_keys == {"0243425"} and years == {2024}
    assert display_process_key("0267425") == "02674/25"
    assert identify_process_keys("parecer.pdf", "Sem número") == (set(), set(), set())


def test_audit_report_preview_recovers_active_process_and_ignores_legacy(separated):
    store, storage = separated
    hidden = seed(store, process="TC 02119/26", entity="Instituto legado")
    with store.connection() as c:
        c.execute("UPDATE minutas_referencias SET acervo='legado' WHERE id=?", (hidden["id"],))
    admin = MinutasAcervoService(library(store, storage))
    processes = [
        ("02119/26", 2024, "Instituto de Previdência do Município de Sertãozinho"),
        ("02674/25", 2023, "Instituto de Previdência do Município de Dona Inês"),
        ("02434/25", 2024, "Instituto de Previdência do Município de Soledade"),
        ("01880/25", 2022, "Instituto de Previdência do Município de Alhandra"),
        ("01653/26", 2025, "Instituto de Previdência do Município de Taperoá"),
        ("02720/25", 2024, "Instituto de Previdência do Município de Exemplo"),
    ]
    ids = {}
    opinion = (
        "EMENTA: Prestação de contas anuais.\n"
        "O processo foi instruído pela Auditoria e houve manifestação da defesa.\n"
        "Passo à análise ministerial dos documentos e das normas aplicáveis.\n"
        "Ante o exposto, opino pela regularidade com ressalvas das contas.\n"
        "É o parecer."
    )
    for number, year, origin in processes:
        result = admin.add_file(RPPS, {"numero_processo": number, "exercicio": year, "classe": "PCA",
            "origem": origin, "fundamento": "Conferido"},
            (f"parecer_{number.replace('/', '_')}.pdf", _text_pdf(opinion + origin)), role="parecer")
        ids[number] = result["referencia_id"]
        assert result["estado"] == "CADASTRADO"
    report = _text_pdf("Relatório de Análise de Defesa\nProcesso TC nº 02119/26\nSem campo de origem.")
    recovered = admin.preview_upload("proc_02119_26_relatorio_de_analise_defesa.pdf", report, "auditoria", RPPS)
    assert recovered["recuperado"] and recovered["referencia_id"] == ids["02119/26"] != hidden["id"]
    assert recovered["origem"] == "Instituto de Previdência do Município de Sertãozinho"
    assert recovered["exercicio"] == 2024
    assert "dados recuperados do acervo" in recovered["sugestao"]
    wrong_year = admin.preview_upload(
        "proc_02674_25_relatorio_de_analise_defesa.pdf",
        _text_pdf("Relatório de Análise de Defesa\nProcesso nº 02674/25\nExercício: 2026"),
        "complementar", RPPS)
    assert wrong_year["recuperado"] and wrong_year["exercicio"] == 2023
    assert wrong_year["origem"] == "Instituto de Previdência do Município de Dona Inês"
    assert any("exercício" in item.casefold() for item in wrong_year["advertencias"])
    conflict = admin.preview_upload(
        "proc_02119_26_relatorio_de_analise_defesa.pdf",
        _text_pdf("Processo TC nº 02119/20"),
        "auditoria", RPPS)
    assert not conflict["recuperado"] and not conflict["processo"]
    assert any("diverge" in item.casefold() for item in conflict["advertencias"])
    missing = admin.preview_upload("sem_numero.pdf", _text_pdf("Relatório sem identificação"), "auditoria", RPPS)
    assert not missing["recuperado"] and not missing["processo"]
    batch = [
        admin.preview_upload(f"proc_{number.replace('/', '_')}_relatorio_de_analise_defesa.pdf",
            _text_pdf(f"Relatório de Análise de Defesa do processo {number}"), "auditoria", RPPS)
        for number, year, origin in processes
    ]
    assert [item["referencia_id"] for item in batch] == [ids[number] for number, _, _ in processes]
    attached = admin.add_file(RPPS, {"numero_processo": "02119/26", "exercicio": 2024, "classe": "PCA",
        "origem": recovered["origem"], "fundamento": "Conferido"}, ("proc_02119_26_relatorio.pdf", report), role="auditoria")
    assert attached["estado"] == "CADASTRADO" and attached["referencia_id"] == ids["02119/26"]
    with pytest.raises(ValueError, match="outro processo"):
        admin.add_file(RPPS, {"numero_processo": "02674/25", "exercicio": 2023, "classe": "PCA",
            "origem": "Instituto de Previdência do Município de Dona Inês", "fundamento": "Conferido"},
                       ("copia.pdf", report), role="auditoria")
    parecer = admin.preview_upload("parecer.pdf", _text_pdf(
        "Origem: Instituto de Previdência do Município de Novo\nNatureza: Prestação de Contas Anual\n"
        "Processo TC nº 03333/26\nExercício: 2026"), "parecer", RPPS)
    assert not parecer["recuperado"] and parecer["processo"] == "03333/26"
    assert "Instituto de Previdência do Município de Novo" in parecer["origem"]
    isolated = admin.preview_upload("proc_09999_20_relatorio.pdf", _text_pdf("Relatório legado"), "auditoria", RPPS)
    assert not isolated["recuperado"]
    with store.connection() as c:
        assert c.execute("SELECT acervo FROM minutas_referencias WHERE id=?", (hidden["id"],)).fetchone()["acervo"] == "legado"


def test_bulk_upload_same_content_cannot_create_another_process(environment):
    store, storage = environment
    admin = MinutasAcervoService(library(store, storage))
    values = {"numero_processo": "01234/26", "exercicio": 2026, "classe": "PCA", "fundamento": "Conferido"}
    admin.add_file(RPPS, values, ("parecer.pdf", _pdf(b"unico")), role="parecer")
    with pytest.raises(ValueError, match="outro processo"):
        admin.add_file(RPPS, {**values, "numero_processo": "01235/26"}, ("copia.pdf", _pdf(b"unico")), role="parecer")
    assert len(admin.repository.list_records()) == 1 and len(storage.objects) == 1


def test_upload_reuses_legacy_process_key_with_prefix_and_rejects_bad_identifier(environment):
    store, storage = environment
    target = seed(store)
    with store.connection() as c:
        c.execute("UPDATE minutas_referencias SET processo_chave='processotc0165326' WHERE id=?", (target["id"],))
    admin = MinutasAcervoService(library(store, storage))
    admin.apply(admin.dry_run(), {"identificador": "teste", "restauracao_verificada": True})
    values = {"numero_processo": "TC 01653/26", "exercicio": 2026, "classe": "PCA", "fundamento": "Conferido"}
    added = admin.add_file(RPPS, values, ("defesa.pdf", _pdf(b"defesa")), role="complementar")
    assert added["referencia_id"] == target["id"]
    assert len(admin.repository.list_records()) == 1
    with pytest.raises(ValueError, match="cinco dígitos"):
        admin.add_file(RPPS, {**values, "numero_processo": "erro12345/26"}, ("x.pdf", _pdf(b"x")), role="parecer")


def test_partial_storage_upload_failure_is_compensated(environment, monkeypatch):
    store, storage = environment
    admin = MinutasAcervoService(library(store, storage))
    original = storage.upload
    def fail_after_upload(*args):
        original(*args)
        raise RuntimeError("Resposta perdida após upload sintético")
    monkeypatch.setattr(storage, "upload", fail_after_upload)
    with pytest.raises(RuntimeError):
        admin.add_file(RPPS, {"numero_processo": "12345/26", "exercicio": 2026, "classe": "PCA", "fundamento": "Conferido"},
                       ("x.pdf", _pdf(b"x")), role="parecer")
    assert storage.objects == {}
    assert admin.repository.list_records() == []


def test_extraction_scope_rejects_conflicting_prosecutor(environment):
    store, storage = environment
    with pytest.raises(ValueError, match="Procuradora incompatível"):
        library(store, storage).repository.list_extractions_by_procuradora("outra")


def test_rpps_with_no_other_precedent_keeps_existing_generation_block():
    from tests.test_minutas_generation import _scenario, Library
    from services.minutas_generation import MinutasGenerationService, MinutasGenerationError
    target, _history, _other, rows = _scenario()
    service = MinutasGenerationService(Library([target], [r for r in rows if r["referencia_id"] == target["id"]]))
    with pytest.raises(MinutasGenerationError, match="outro processo nesta especialidade"):
        service.prepare_context(target["id"])


def test_docx_rejects_another_specialty_before_reading_template():
    from tests.test_minutas_docx import Library
    from services.minutas_docx import MinutasDocxService, MinutasDocxError
    library = Library()
    library.specialty_id = "elvira.pca.camaras"
    with pytest.raises(MinutasDocxError, match="especialidade"):
        MinutasDocxService(library, source_summary=lambda _: {}).export("target-taperoa", {}, {})


def test_upload_does_not_hold_shared_write_transaction_during_storage_io(environment, monkeypatch):
    store, storage = environment
    original_connection = store.connection
    writing = []
    @contextmanager
    def connection(**kwargs):
        with original_connection(**kwargs) as c:
            writing.append(not kwargs.get("read_only", False))
            try: yield c
            finally: writing.pop()
    monkeypatch.setattr(store, "connection", connection)
    original_upload = storage.upload
    def upload(*args):
        assert not any(writing)
        original_upload(*args)
    monkeypatch.setattr(storage, "upload", upload)
    admin = MinutasAcervoService(library(store, storage))
    result = admin.add_file(RPPS, {"numero_processo": "12345/26", "exercicio": 2026, "classe": "PCA", "fundamento": "Conferido"},
                           ("x.pdf", _pdf(b"x")), role="parecer")
    assert result["estado"] == "CADASTRADO"


def test_legacy_stays_intact_while_active_archive_reuses_process_and_pdf(separated):
    store, storage = separated
    legacy = seed(store)
    digest = sha256(_pdf(b"parecer-legado")).hexdigest()
    with store.connection() as c:
        c.execute("UPDATE minutas_referencias SET acervo='legado'")
        c.execute("UPDATE minutas_extracoes SET arquivo_sha256=? WHERE referencia_id=? AND papel='parecer'", (digest, legacy["id"]))
        preserved = dict(c.execute(
            "SELECT id,numero_processo,processo_chave,auditoria_path,parecer_path FROM minutas_referencias WHERE id=?",
            (legacy["id"],)).fetchone())
    admin = MinutasAcervoService(library(store, storage))
    assert admin.repository.separation_enabled()
    assert admin.repository.list_records(RPPS) == []
    assert [row["id"] for row in admin.repository.list_records(legacy=True)] == [legacy["id"]]
    values = {"numero_processo": "01653/26", "exercicio": 2026, "classe": "PCA", "origem": "Instituto de Previdência",
              "fundamento": "Novo acervo ativo"}
    first = admin.add_file(RPPS, values, ("parecer.pdf", _pdf(b"parecer-legado")), role="parecer")
    assert first["estado"] == "CADASTRADO" and first["referencia_id"] != legacy["id"]
    second = admin.add_file(RPPS, values, ("auditoria.pdf", _pdf(b"relatorio-novo")), role="auditoria")
    assert second["referencia_id"] == first["referencia_id"]
    with pytest.raises(ValueError, match="outro processo"):
        admin.add_file(RPPS, {**values, "numero_processo": "01654/26"}, ("copia.pdf", _pdf(b"parecer-legado")), role="parecer")
    assert admin.add_file(RPPS, values, ("outra.pdf", _pdf(b"parecer-legado")), role="parecer")["estado"] == "DUPLICADO"
    with store.connection() as c:
        c.execute("INSERT INTO minutas_classificacoes(referencia_id,especialidade_id,estado,classe,origem,atualizado_em,atualizado_por) VALUES(?,?,?,?,?,?,?)",
                  (legacy["id"], RPPS, "CLASSIFICADO", "PCA", "teste", "1", "teste"))
        c.execute("INSERT INTO minutas_acervo_vinculos(referencia_id,especialidade_id,justificativa) VALUES(?,?,?)",
                  (legacy["id"], RPPS, "Histórico preservado"))
    active = library(store, storage)
    assert [row["id"] for row in active.list()] == [first["referencia_id"]]
    with pytest.raises(MinutasValidationError, match="não encontrada"):
        active.get(legacy["id"])
    assert legacy["id"] not in {row["id"] for row in admin.repository.list_records(RPPS)}
    with store.connection() as c:
        again = dict(c.execute(
            "SELECT id,numero_processo,processo_chave,auditoria_path,parecer_path FROM minutas_referencias WHERE id=?",
            (legacy["id"],)).fetchone())
    assert again == preserved
    store.queries.clear()
    active.repository.list_extractions_by_procuradora(PROSECUTOR_KEY)
    assert any("r.acervo='ativo'" in query for query in store.queries)
    captured = []
    original = store.connection

    class Tracing:
        def __init__(self, connection):
            self.connection = connection

        def execute(self, sql, params=()):
            captured.append(sql)
            return self.connection.execute(sql, params)

    @contextmanager
    def tracing(**kwargs):
        with original(**kwargs) as connection:
            yield Tracing(connection)

    store.connection = tracing
    from services.minutas_search import MinutasSearchService
    with pytest.raises(sqlite3.OperationalError):
        MinutasSearchService(active).search("parecer")
    assert any("r.acervo='ativo'" in sql for sql in captured)
    assert len(CATALOG) == 24


def test_outdated_text_does_not_automatically_classify_rpps(environment):
    store, storage = environment
    seed(store)
    with store.connection() as c:
        c.execute("UPDATE minutas_extracoes SET extrator_versao='antiga'")
    plan = MinutasAcervoService(library(store, storage)).dry_run()
    assert all(item["estado"] != "CLASSIFICADO" for item in plan["itens"])
    assert len(plan["contagens"]["nao_indexados"]) == 2


def test_acervo_storage_keeps_original_names_and_does_not_rewrite_legacy_objects(environment):
    store, storage = environment
    legacy = seed(store)
    legacy_path = legacy["parecer_path"]
    storage.objects[legacy_path] = _pdf(b"legado")
    scoped = library(store, storage)
    admin = MinutasAcervoService(scoped)
    admin.apply(admin.dry_run(), {"identificador": "teste", "restauracao_verificada": True})
    values = {"numero_processo": "12345/26", "exercicio": 2026, "classe": "PCA",
              "origem": "Instituto de Previdência", "fundamento": "Conferido"}
    original = "proc_01653_26_parecer - município de Taperoá - sanção.pdf"
    opinion = (
        "EMENTA: Prestação de contas anuais. Regularidade com ressalvas.\n\n"
        "O processo foi instruído pela Auditoria e houve manifestação da defesa.\n"
        "Passo à análise ministerial dos documentos e das normas aplicáveis.\n"
        "Ante o exposto, opino pela regularidade com ressalvas das contas.\n"
        "É o parecer."
    )
    content = _text_pdf(opinion)
    first = admin.add_file(RPPS, values, (original, content), role="parecer")
    assert first["estado"] == "CADASTRADO"
    assert re.fullmatch(r"acervo/[0-9a-f]{32}/[0-9a-f]{32}\.pdf", first["path"])
    assert original not in first["path"] and " " not in first["path"] and "á" not in first["path"]
    digest = sha256(content).hexdigest()
    assert sha256(storage.objects[first["path"]]).hexdigest() == digest
    with store.connection() as c:
        stored = dict(c.execute("SELECT nome,sha256,path FROM minutas_acervo_documentos WHERE path=?",
                                (first["path"],)).fetchone())
    assert stored["nome"] == original and stored["sha256"] == digest
    downloaded = scoped.download_path(first["referencia_id"], first["path"])
    assert downloaded.name == original and downloaded.content == content
    extracted = MinutasExtractionService(scoped).reprocess_document(first["referencia_id"], first["path"])
    assert extracted[0]["status"] == "PROCESSADO" and extracted[0]["arquivo_nome"] == original
    assert "regularidade com ressalvas" in extracted[0]["texto_integral"].casefold()
    same_name = admin.add_file(
        RPPS, values, (original, _text_pdf(opinion.replace("ressalvas", "irregularidade"))),
        role="parecer", confirm_version=True)
    assert same_name["estado"] == "CADASTRADO" and same_name["path"] != first["path"]
    with store.connection() as c:
        names = [row["nome"] for row in c.execute(
            "SELECT nome FROM minutas_acervo_documentos WHERE referencia_id=? ORDER BY posicao",
            (first["referencia_id"],)).fetchall()]
    assert names == [original, original]
    model = admin.add_file(RPPS, values, ("modelo - decisão e instrução.docx", _docx()), role="modelo")
    assert re.fullmatch(r"acervo/[0-9a-f]{32}/[0-9a-f]{32}\.docx", model["path"])
    model_file = scoped.download_path(first["referencia_id"], model["path"])
    assert model_file.name == "modelo - decisão e instrução.docx"
    assert scoped.download_path(legacy["id"], legacy_path).content == _pdf(b"legado")
    assert legacy_path in storage.objects and storage.objects[legacy_path] == _pdf(b"legado")
