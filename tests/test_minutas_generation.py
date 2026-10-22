import json
import re
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from services import ai_service
from services.minutas_generation import (
    DEVICE_INTRO_HARMONY,
    DEVICE_INTRO_NEUTRAL,
    EVIDENCE_CORRECTION_VERSION,
    RAG_RECOVERY_VERSION,
    VALIDATION_POLICY_VERSION,
    MinutasGenerationError,
    MinutasGenerationService,
    _canonical_process_key,
    _candidate_support_check,
    _clean_text,
    _continuous_word_sequence,
    _rag_query_plan,
    _rag_queries,
)
from services.minutas_extraction import EXTRACTOR_VERSION
from services.minutas_library import MinutasAccessDenied


TARGET_ID = "target-taperoa"
HISTORICAL_TEST_PROCESS_KEY = "0165326"
ORIENTACAO_REGRESSAO = (
    "Sustentar a linha argumentativa indicada pelo assessor nesta minuta experimental."
)


def _gerar(
    service,
    target_id,
    analyzed,
    use_rag=True,
    orientacao_juridica=None,
    contexto_orientacao=None,
    usuario="",
):
    if orientacao_juridica is None:
        orientacao_juridica = ORIENTACAO_REGRESSAO
    if contexto_orientacao is None:
        target = service.library.get(target_id)
        pacote = service.pacote_orientacao(
            orientacao_juridica, target, analyzed, usuario
        )
        orientacao_juridica = pacote["orientacao_juridica"]
        contexto_orientacao = pacote["contexto_orientacao"]
        usuario = pacote["usuario"]
    return service.generate(
        target_id,
        analyzed,
        use_rag=use_rag,
        orientacao_juridica=orientacao_juridica,
        contexto_orientacao=contexto_orientacao,
        usuario=usuario,
    )


def _confirmar_orientacao_ui(app, texto=ORIENTACAO_REGRESSAO):
    area = next(
        item
        for item in app.text_area
        if item.label == "Orientação jurídica e conclusão pretendida"
    )
    area.set_value(texto).run()
    next(
        item
        for item in app.button
        if item.label == "Confirmar orientação e habilitar geração"
    ).click().run()
HISTORICAL_TEST_PROCESS_KEYS = {"0165326", "0272025", "0149926", "0265025"}
_UI_HOLD = {}


def _record(
    identifier,
    process,
    key,
    *,
    procuradora="Elvira Samara",
    subtype="Instituto/Autarquia",
):
    return {
        "id": identifier,
        "especialidade_id": "elvira.pca.rpps",
        "classificacao_estado": "CLASSIFICADO",
        "classificacao_revisao": 1,
        "procuradora": procuradora,
        "procuradora_chave": procuradora.casefold(),
        "numero_processo": process,
        "processo_chave": key,
        "exercicio": 2026,
        "subtipo": subtype,
        "auditoria_nome": "auditoria.pdf",
        "auditoria_mime": "application/pdf",
        "auditoria_tamanho": 100,
        "auditoria_path": f"{identifier}/auditoria-0.pdf",
        "parecer_nome": "parecer.pdf",
        "parecer_mime": "application/pdf",
        "parecer_tamanho": 100,
        "parecer_path": f"{identifier}/parecer.pdf",
        "atualizado_em": "2026-10-13T10:00:00+00:00",
    }


def _row(reference, role, text, *, path=None, file_hash=None, position=0):
    file_path = path or (
        reference["auditoria_path"]
        if role == "auditoria" and position == 0
        else reference["parecer_path"]
        if role == "parecer"
        else f"{reference['id']}/{role}-{position}.pdf"
    )
    return {
        "referencia_id": reference["id"],
        "papel": role,
        "tipo": {
            "auditoria": "Relatório da Auditoria",
            "parecer": "Parecer definitivo",
            "complementar": "Relatório de Análise de Defesa",
        }[role],
        "posicao": position,
        "procuradora": reference["procuradora"],
        "procuradora_chave": reference["procuradora_chave"],
        "arquivo_nome": (
            reference["auditoria_nome"]
            if role == "auditoria" and position == 0
            else reference["parecer_nome"]
            if role == "parecer"
            else file_path.rsplit("/", 1)[-1]
        ),
        "arquivo_tamanho": 100,
        "arquivo_path": file_path,
        "arquivo_sha256": file_hash or (reference["id"] + role + str(position)),
        "texto_integral": text,
        "unidades_json": json.dumps([{"pagina": 1, "texto": text}], ensure_ascii=False),
        "secoes_json": "[]",
        "status": "PROCESSADO",
        "extrator_versao": EXTRACTOR_VERSION,
        "erro": "",
        "atualizado_em": "2026-10-13T10:00:00+00:00",
    }


class Repository:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []
        self.inventory_queries = []

    def list_extractions_by_procuradora(self, key):
        self.queries.append(key)
        return list(self.rows)

    def list_extraction_inventory_by_procuradora(self, key):
        self.inventory_queries.append(key)
        return [row for row in self.rows if row["procuradora_chave"] == key]

    def list_documents(self, reference_id):
        return [
            {
                "id": f"document-{index}",
                "referencia_id": row["referencia_id"],
                "tipo": row["tipo"],
                "posicao": row["posicao"],
                "nome": row["arquivo_nome"],
                "mime": "application/pdf",
                "tamanho": row["arquivo_tamanho"],
                "path": row["arquivo_path"],
            }
            for index, row in enumerate(self.rows)
            if row["referencia_id"] == reference_id
            and row["papel"] == "complementar"
        ]

    def get_extraction(self, path):
        return next((row for row in self.rows if row["arquivo_path"] == path), None)


class Library:
    specialty_id = "elvira.pca.rpps"
    def __init__(self, records, rows, *, authorized=True):
        self.records = {record["id"]: record for record in records}
        self.repository = Repository(rows)
        self.storage = object()
        self.authorized = authorized
        self.list_calls = 0

    def _authorize(self):
        if not self.authorized:
            raise MinutasAccessDenied("Acesso não autorizado a este módulo.")
        return "admin@test.local"

    def get(self, identifier):
        self._authorize()
        return self.records[identifier]

    def list(self):
        self._authorize()
        self.list_calls += 1
        return list(self.records.values())


def _scenario():
    target = _record(TARGET_ID, "TC 01653/26", HISTORICAL_TEST_PROCESS_KEY)
    history = _record("history-rio", "TC 02720/25", "tc0272025")
    other = _record(
        "other-office",
        "TC 99999/26",
        "tc9999926",
        procuradora="Outra Procuradora",
    )
    target_opinion = _row(
        target,
        "parecer",
        "SEGREDO DO PARECER-ALVO",
        path=target["parecer_path"],
        file_hash="target-opinion-hash",
    )
    rows = [
        _row(
            target,
            "auditoria",
            "Irregularidade previdenciária na página original.\n"
            "TRAMITA - Sistema de Tramitação de Processos e Documentos",
        ),
        _row(target, "complementar", "Defesa apresentada pelo gestor.", position=1),
        target_opinion,
        _row(history, "parecer", "Parecer histórico com fundamentação previdenciária."),
        _row(history, "auditoria", "Auditoria histórica sobre contribuições."),
        _row(
            history,
            "auditoria",
            "SEGREDO DO PARECER-ALVO",
            path="history-rio/duplicata.pdf",
            file_hash="target-opinion-hash",
            position=2,
        ),
        _row(other, "parecer", "CONTEÚDO DE OUTRA PROCURADORA"),
    ]
    return target, history, other, rows


def _analysis_payload(context, *, count=1, treatment="REJEITADA"):
    source = context["documentos_processo_alvo"][0]
    unit = source["conteudo"][0]
    page = unit.get("pagina", 0)
    support = next(
        line.strip() for line in unit["texto"].splitlines() if line.strip()
    )
    reference = {
        "fonte_id": source["fonte_id"],
        "pagina": page,
        "trecho_suporte": support,
    }
    cited = lambda: dict(reference)
    irregularities = []
    for index in range(1, count + 1):
        irregularities.append(
            {
                "id": f"IRR-{index}",
                "apontamento": (
                    "Irregularidade previdenciária na página original"
                    if index == 1
                    else f"Outro apontamento documentado {index}"
                ),
                "fatos_documentados": [
                    {"descricao": f"Fato comprovado {index}", "fontes": [cited()]}
                ],
                "normas_mencionadas": [
                    {
                        "descricao": "Norma citada no relatório",
                        "origem": "AUDITORIA",
                        "fontes": [cited()],
                    }
                ],
                "argumentos_defesa": [
                    {
                        "alegacao": "A defesa alegou ausência de dano.",
                        "comprovacao_documental": "NÃO COMPROVADA",
                        "fontes": [cited()],
                    }
                ],
                "avaliacao_auditoria": {
                    "descricao": "A Auditoria rejeitou a justificativa defensiva.",
                    "tratamento_defesa": treatment,
                    "fontes": [cited()],
                },
                "pontos_controvertidos": ["Extensão da consequência jurídica."],
                "hipoteses_juridicas": ["Hipótese sujeita à revisão ministerial."],
            }
        )
    return {
        "resumo_processual": "Análise exclusiva dos documentos instrutórios.",
        "irregularidades": irregularities,
        "sem_irregularidades_identificadas": not irregularities,
        "pontos_atencao_gerais": [],
    }


def _analysis_result(context, **changes):
    result = {
        "analise": _analysis_payload(context),
        "modelo": "modelo-analise",
        "duracao_ms": 10,
        "tokens_entrada": 80,
        "tokens_saida": 30,
        "tokens_total": 110,
    }
    result.update(changes)
    return result


def _draft_structure(
    ids=("IRR-1",),
    *,
    ministerial_treatment="MANTIDA_REJEICAO",
    divergence_grounds=(),
    cure_state="ALEGADO_NAO_COMPROVADO",
    cure_sources=(),
):
    return {
        "identificacao_processual": "Processo TC 01653/26",
        "ementa": "PRESTAÇÃO DE CONTAS. ANÁLISE DOCUMENTAL.",
        "relatorio": (
            "Relatório processual com referência à página 1. "
            "É o relatório. Passo a opinar."
        ),
        "fundamentacao_geral": "Fundamentação baseada nas fontes fornecidas.",
        "topicos_irregularidades": [
            {
                "titulo": "Irregularidades relacionadas",
                "irregularidades_ids": list(ids),
                "avaliacoes_irregularidades": [
                    {
                        "irregularidade_id": identifier,
                        "tratamento_ministerial_defesa": ministerial_treatment,
                        "fundamentos_divergencia": list(divergence_grounds),
                        "estado_saneamento": cure_state,
                        "fontes_saneamento": list(cure_sources),
                    }
                    for identifier in ids
                ],
                "texto": "A defesa foi confrontada com a conclusão da Auditoria.",
            }
        ],
        "analise_sancionatoria": {
            "conclusao": "MULTA_NAO_CABIVEL",
            "irregularidades_ids": list(ids),
            "fundamentos_legais": [],
            "responsaveis": [],
            "dano_ao_erario": "NAO_COMPROVADO_OU_NAO_QUANTIFICADO",
            "texto": (
                "Não se propõe multa, pois os elementos disponíveis não permitem "
                "individualizar responsabilidade sancionatória."
            ),
        },
        "conclusao_pedidos": "Conclusão ministerial sujeita à revisão humana.",
        "pedidos": ["Submeta-se a minuta à revisão jurídica humana."],
        "encerramento": "É o Parecer. Documento experimental sem caráter oficial.",
    }


def _draft_result(_context, **changes):
    result = {
        "estrutura": _draft_structure(),
        "modelo": "modelo-redacao",
        "duracao_ms": 20,
        "tokens_entrada": 100,
        "tokens_saida": 40,
        "tokens_total": 140,
    }
    result.update(changes)
    return result


def _search_result(reference, row, *, context=None, page=1, relevance=1.0):
    return {
        "referencia_id": reference["id"],
        "documento_id": f"extraction-{reference['id']}",
        "procuradora": reference["procuradora"],
        "processo": reference["numero_processo"],
        "exercicio": reference["exercicio"],
        "natureza": "Prestação de Contas Anual",
        "subtipo": reference["subtipo"],
        "documento": row["arquivo_nome"],
        "arquivo_path": row["arquivo_path"],
        "pagina": page,
        "localizacao": None,
        "trecho": "【Fundamentação】 previdenciária histórica.",
        "contexto": context or "Fundamentação previdenciária histórica pertinente.",
        "relevancia": relevance,
        "criterio_recuperacao": "Expressão pesquisada",
        "sha256_registrado": row["arquivo_sha256"],
        "fonte_versao": "source-version",
    }


@pytest.mark.parametrize(
    ("display", "stored_key"),
    (
        ("01653/26", "0165326"),
        ("TC 01653/26", "tc0165326"),
        ("TC-01653/26", "tc0165326"),
        ("Processo TC 01653/26", "processotc0165326"),
    ),
)
def test_taperoa_equivalent_formats_are_found_and_accepted(display, stored_key):
    target, history, other, rows = _scenario()
    target.update(numero_processo=display, processo_chave=stored_key)
    service = MinutasGenerationService(
        Library([target, history, other], rows), generate=lambda _context: {}
    )

    assert target in service.targets()
    assert service.prepare_context(target["id"])["contexto"]["processo_alvo"][
        "numero"
    ] == display


@pytest.mark.parametrize(
    "value",
    (
        "01654/26",
        "01653/25",
        "11653/26",
        "01653260",
        "TC 01653/26 adicional",
    ),
)
def test_unclassified_processes_are_strictly_rejected(value):
    assert _canonical_process_key(value) != HISTORICAL_TEST_PROCESS_KEY
    target = _record(TARGET_ID, value, re.sub(r"[^0-9a-z]", "", value.casefold()))
    target["classificacao_estado"] = "PENDENTE"
    service = MinutasGenerationService(Library([target], []))

    assert service.targets() == []
    with pytest.raises(MinutasGenerationError, match="não está classificado|inválida ou conflitante"):
        service.prepare_context(target["id"])


@pytest.mark.parametrize(
    ("display", "stored_key"),
    (
        ("01653/26", "tc0165326"),
        ("TC 02720/25", "0272025"),
        ("TC-01499/26", "tc0149926"),
        ("Processo TC 02650/25", "processotc0265025"),
    ),
)
def test_all_cross_validation_processes_are_canonically_enabled(display, stored_key):
    target = _record("enabled", display, stored_key)
    service = MinutasGenerationService(Library([target], []))

    assert _canonical_process_key(display) in HISTORICAL_TEST_PROCESS_KEYS
    assert service.targets() == [target]


def test_clean_text_removes_only_specific_validation_lines():
    text = "\n".join(
        (
            "A tramitação processual deve observar o contraditório.",
            "O ato administrativo foi registrado no TRAMITA em 10/01/2026.",
            "A validação jurídica do ato compete ao gestor.",
            "Verifique a autenticidade deste documento em https://validar.exemplo/abc",
            "Código verificador: ABC123",
            "Documento assinado eletronicamente por Servidor Exemplo",
            "TRAMITA - Sistema de Tramitação de Processos e Documentos",
        )
    )

    cleaned = _clean_text(text)

    assert "tramitação processual" in cleaned
    assert "registrado no TRAMITA" in cleaned
    assert "validação jurídica" in cleaned
    assert "Verifique a autenticidade" not in cleaned
    assert "Código verificador" not in cleaned
    assert "assinado eletronicamente" not in cleaned
    assert "Sistema de Tramitação" not in cleaned


@pytest.mark.parametrize(
    ("excerpt", "page_text"),
    (
        (
            "As contribuições previdenciárias foram recolhidas.",
            "As contribuições previdenciárias foram recolhidas.",
        ),
        (
            "“AS CONTRIBUICOES”,   PREVIDENCIARIAS foram recolhidas!",
            "As contribuições previdenciárias foram recolhidas.",
        ),
        (
            "As contribuições previdenciárias foram recolhidas",
            "As contri-\nbuições previ-\r\ndenciárias foram recolhidas.",
        ),
        (
            "O oficio final foi apresentado",
            "O oﬁcio ﬁnal foi apresentado.",
        ),
    ),
)
def test_evidence_matching_accepts_literal_word_sequences_with_pdf_artifacts(
    excerpt, page_text
):
    assert _continuous_word_sequence(excerpt, page_text) == (True, "")


def test_evidence_matching_rejects_paraphrase_without_literal_sequence():
    verified, reason = _continuous_word_sequence(
        "Os recolhimentos previdenciários estavam integralmente regulares.",
        "As contribuições previdenciárias foram recolhidas parcialmente.",
    )

    assert verified is False
    assert reason == "trecho não localizado na página indicada"


def test_candidate_safety_preserves_named_entities():
    safe, reason = _candidate_support_check(
        "O Instituto Taperoá registrou débito previdenciário.",
        "O Instituto Bananeiras registrou débito previdenciário.",
    )

    assert safe is False
    assert reason == "não preserva nomes ou entidades da afirmação"


def test_generation_service_reuses_inventory_only_within_current_instance():
    target, history, other, rows = _scenario()
    library = Library([target, history, other], rows)
    service = MinutasGenerationService(library)

    assert target in service.targets()
    first = service.source_summary(target["id"])
    second = service.source_summary(target["id"])

    assert first == second
    assert library.list_calls == 1
    assert library.repository.inventory_queries == [target["procuradora_chave"]]

    # Uma nova instância representa outro rerun e consulta novamente, evitando
    # que mudanças nas fontes fiquem ocultas por cache entre execuções.
    MinutasGenerationService(library).source_summary(target["id"])
    assert library.list_calls == 2
    assert library.repository.inventory_queries == [
        target["procuradora_chave"],
        target["procuradora_chave"],
    ]


def test_context_uses_target_instruction_and_excludes_contamination():
    target, history, other, rows = _scenario()
    library = Library([target, history, other], rows)
    service = MinutasGenerationService(library, generate=lambda _context: {})

    prepared = service.prepare_context(target["id"])
    context = prepared["contexto"]
    serialized = json.dumps(context, ensure_ascii=False)

    assert context["processo_alvo"]["parecer_definitivo_excluido"] is True
    assert [source["papel"] for source in context["documentos_processo_alvo"]] == [
        "auditoria",
        "complementar",
    ]
    assert context["documentos_processo_alvo"][0]["conteudo"][0]["pagina"] == 1
    assert "Irregularidade previdenciária" in serialized
    assert "TRAMITA" not in serialized
    assert "SEGREDO DO PARECER-ALVO" not in serialized
    assert "CONTEÚDO DE OUTRA PROCURADORA" not in serialized
    assert [item["processo"] for item in context["referencias_historicas"]] == [
        history["numero_processo"]
    ]
    assert library.repository.queries == [target["procuradora_chave"]]


def test_context_preserves_verified_folios_without_converting_pdf_pages():
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    audit["texto_integral"] = (
        "Irregularidade previdenciária na página original.\nfls. 101/110"
    )
    audit["unidades_json"] = json.dumps(
        [
            {
                "pagina": 1,
                "texto": "Irregularidade previdenciária na página original.\nfls. 101/110",
            }
        ],
        ensure_ascii=False,
    )
    complement = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"]
        and row["papel"] == "complementar"
    )
    complement["texto_integral"] = (
        "A defesa foi examinada no último relatório.\nfls. 150/158"
    )
    complement["unidades_json"] = json.dumps(
        [
            {
                "pagina": 1,
                "texto": "A defesa foi examinada no último relatório.\nfls. 150/158",
            }
        ],
        ensure_ascii=False,
    )
    no_folios = _row(
        target,
        "complementar",
        "Manifestação processual sem numeração de folhas.",
        position=2,
    )
    no_folios["unidades_json"] = json.dumps(
        [
            {"pagina": 1, "texto": "Primeira página física."},
            {"pagina": 2, "texto": "Segunda página física."},
        ],
        ensure_ascii=False,
    )
    single_folio = _row(
        target,
        "complementar",
        "Cota processual.\nfl. 170",
        position=3,
    )
    single_folio["unidades_json"] = json.dumps(
        [
            {"pagina": 1, "texto": "Cota processual.\nfl. 170"},
            {"pagina": 2, "texto": "Continuação sem folha expressa."},
        ],
        ensure_ascii=False,
    )
    rows.extend((no_folios, single_folio))
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
    )

    context = service.prepare_analysis_context(target["id"])["contexto"]
    sources = context["documentos_processo_alvo"]

    assert sources[0]["folhas_processuais"]["citacao"] == "fls. 101/110"
    assert sources[1]["folhas_processuais"]["citacao"] == "fls. 150/158"
    assert "folhas_processuais" not in sources[2]
    assert sources[2]["conteudo"][0]["pagina"] == 1
    assert sources[2]["conteudo"][1]["pagina"] == 2
    assert sources[3]["folhas_processuais"]["citacao"] == "fl. 170"
    assert sources[3]["folhas_processuais"]["fim"] is None

    result = _gerar(service, target["id"], service.analyze(target["id"]))
    report = "\n".join(result["estrutura"]["relatorio_paragrafos"])
    assert "Integram a instrução processual" not in report
    assert "Relatório da Auditoria (fls. 101/110)" not in report
    assert "Relatório de Análise de Defesa (fls. 150/158)" not in report
    assert "Relatório de Análise de Defesa (fl. 170)" not in report
    assert "Manifestação processual sem numeração" not in report
    assert "(fls. 1/2)" not in report
    assert report.endswith("É o Relatório. Passo a opinar.")


@pytest.mark.parametrize(
    "target_key", sorted(HISTORICAL_TEST_PROCESS_KEYS)
)
def test_each_cross_validation_target_excludes_its_opinion_and_preserves_chronology(
    target_key,
):
    process_numbers = {
        "0165326": "TC 01653/26",
        "0272025": "TC 02720/25",
        "0149926": "TC 01499/26",
        "0265025": "TC 02650/25",
    }
    records = [
        _record(f"process-{key}", number, key)
        for key, number in process_numbers.items()
    ]
    rows = []
    for record in records:
        rows.extend(
            (
                _row(record, "auditoria", f"Auditoria {record['numero_processo']}"),
                _row(
                    record,
                    "complementar",
                    f"Primeira etapa complementar {record['numero_processo']}",
                    position=1,
                ),
                _row(
                    record,
                    "complementar",
                    f"Segunda etapa complementar {record['numero_processo']}",
                    position=2,
                ),
                _row(
                    record,
                    "parecer",
                    f"Parecer reservado {record['numero_processo']}",
                    file_hash=f"opinion-{_canonical_process_key(record['numero_processo'])}",
                ),
            )
        )
    target = next(
        record
        for record in records
        if _canonical_process_key(record["numero_processo"]) == target_key
    )
    target_opinion = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "parecer"
    )
    duplicate_record = next(record for record in records if record is not target)
    duplicate_opinion = next(
        row
        for row in rows
        if row["referencia_id"] == duplicate_record["id"]
        and row["papel"] == "parecer"
    )
    duplicate_opinion.update(
        arquivo_sha256=target_opinion["arquivo_sha256"],
        texto_integral=target_opinion["texto_integral"],
        unidades_json=target_opinion["unidades_json"],
    )
    library = Library(records, rows)
    service = MinutasGenerationService(library)

    analysis_context = service.prepare_analysis_context(target["id"])["contexto"]
    drafting = service.prepare_context(target["id"])
    serialized_analysis = json.dumps(analysis_context, ensure_ascii=False)
    serialized_drafting = json.dumps(drafting["contexto"], ensure_ascii=False)
    target_sources = drafting["contexto"]["documentos_processo_alvo"]
    historical_processes = {
        item["processo"]
        for item in drafting["contexto"]["referencias_historicas"]
    }

    assert {
        _canonical_process_key(record["numero_processo"])
        for record in service.targets()
    } == HISTORICAL_TEST_PROCESS_KEYS
    assert drafting["contexto"]["processo_alvo"]["numero"] == target["numero_processo"]
    assert [source["papel"] for source in target_sources] == [
        "auditoria", "complementar", "complementar"
    ]
    assert [source["ordem_processual"] for source in target_sources] == [0, 1, 2]
    assert [source["etapa_processual"] for source in target_sources] == [
        "Relatório da Auditoria",
        "Relatório de Análise de Defesa",
        "Relatório de Análise de Defesa",
    ]
    assert target_opinion["texto_integral"] not in serialized_analysis
    assert target_opinion["texto_integral"] not in serialized_drafting
    assert target["numero_processo"] not in historical_processes
    assert duplicate_record["numero_processo"] not in historical_processes
    assert historical_processes == {
        record["numero_processo"]
        for record in records
        if record["id"] not in {target["id"], duplicate_record["id"]}
    }
    assert all(
        set(reference) == {"processo", "exercicio", "subtipo", "uso", "parecer"}
        for reference in drafting["contexto"]["referencias_historicas"]
    )


def test_reference_selection_combines_subtype_and_analysis_theme():
    target, _history, _other, base_rows = _scenario()
    same_subtype = _record(
        "history-same-subtype",
        "TC 01499/26",
        "0149926",
        subtype=target["subtipo"],
    )
    same_theme = _record(
        "history-same-theme", "TC 02650/25", "0265025", subtype="Prefeitura"
    )
    unrelated = _record(
        "history-unrelated", "TC 02720/25", "0272025", subtype="Prefeitura"
    )
    rows = [*base_rows[:3]]
    rows.extend(
        (
            _row(same_subtype, "parecer", "Padrão de redação ministerial."),
            _row(
                same_theme,
                "parecer",
                "Fundamentação jurídica sobre contribuição previdenciária.",
            ),
            _row(same_theme, "auditoria", "Texto de relatório sem a matéria pesquisada."),
            _row(unrelated, "parecer", "Outro padrão de redação."),
            _row(
                unrelated,
                "auditoria",
                "Irregularidade documentada sobre contribuição previdenciária.",
            ),
        )
    )
    service = MinutasGenerationService(
        Library([target, same_subtype, same_theme, unrelated], rows)
    )
    analysis_context = service.prepare_analysis_context(target["id"])["contexto"]
    prepared = service.prepare_context(
        target["id"], _analysis_payload(analysis_context)
    )

    assert [
        item["processo"] for item in prepared["contexto"]["referencias_historicas"]
    ] == [same_subtype["numero_processo"], same_theme["numero_processo"]]
    assert prepared["relatorios_historicos"] == 0
    historical = json.dumps(
        prepared["contexto"]["referencias_historicas"], ensure_ascii=False
    )
    assert "Irregularidade documentada sobre contribuição previdenciária." not in (
        historical
    )


def test_historical_reference_requires_current_processed_linked_extraction():
    target, outdated, other, rows = _scenario()
    valid = _record(
        "history-valid", "TC 01499/26", "0149926", subtype=target["subtipo"]
    )
    outdated_opinion = next(
        row
        for row in rows
        if row["referencia_id"] == outdated["id"] and row["papel"] == "parecer"
    )
    outdated_opinion["extrator_versao"] = "extrator-anterior"
    rows.append(_row(valid, "parecer", "Referência histórica válida e processada."))
    service = MinutasGenerationService(
        Library([target, outdated, valid, other], rows)
    )

    prepared = service.prepare_context(target["id"])
    selected = [
        item["processo"] for item in prepared["contexto"]["referencias_historicas"]
    ]

    assert valid["numero_processo"] in selected
    assert outdated["numero_processo"] not in selected


def test_parecer_without_audit_is_precedent_and_audit_text_is_not():
    target, _history, other, rows = _scenario()
    rows = [row for row in rows if row["referencia_id"] == target["id"]]
    founded = _record(
        "history-founded", "TC 01499/26", "0149926", subtype="Prefeitura"
    )
    decoy = _record(
        "history-audit-match", "TC 02650/25", "0265025", subtype="Prefeitura"
    )
    founded_opinion = _row(
        founded,
        "parecer",
        "Fundamentação: a contribuição previdenciária observa o art. 40 da Constituição.",
    )
    decoy_opinion = _row(decoy, "parecer", "Padrão genérico de merenda escolar.")
    decoy_audit = _row(
        decoy,
        "auditoria",
        "Irregularidade previdenciária SIGILOSO 98765 na contribuição previdenciária.",
    )
    target_opinion = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "parecer"
    )
    rows.extend((founded_opinion, decoy_opinion, decoy_audit))

    def search(_query, **_filters):
        return {
            "resultados": [
                _search_result(
                    decoy,
                    decoy_audit,
                    context=decoy_audit["texto_integral"],
                    relevance=5,
                ),
                _search_result(
                    founded,
                    founded_opinion,
                    context=founded_opinion["texto_integral"],
                    relevance=0.2,
                ),
                _search_result(
                    target,
                    target_opinion,
                    context="SEGREDO DO PARECER-ALVO contribuição previdenciária",
                    relevance=9,
                ),
            ]
        }

    service = MinutasGenerationService(
        Library([target, founded, decoy, other], rows),
        analyze=_analysis_result,
        search=search,
    )
    prepared = service.prepare_context(target["id"])
    selected = {
        item["processo"]
        for item in prepared["contexto"]["referencias_historicas"]
    }
    serialized = json.dumps(prepared["contexto"], ensure_ascii=False)

    assert founded["numero_processo"] in selected
    assert decoy["numero_processo"] not in selected
    assert target["numero_processo"] not in selected
    assert [source["papel"] for source in prepared["contexto"]["documentos_processo_alvo"]] == [
        "auditoria",
        "complementar",
    ]
    assert "SIGILOSO 98765" not in serialized
    assert "SEGREDO DO PARECER-ALVO" not in serialized
    assert "CONTEÚDO DE OUTRA PROCURADORA" not in serialized

    recovery = service.analyze(target["id"])["recuperacao_juridica"]
    passages = json.dumps(recovery["trechos"], ensure_ascii=False)
    assert {item["referencia_id"] for item in recovery["trechos"]} == {founded["id"]}
    assert "SIGILOSO 98765" not in passages
    assert "SEGREDO DO PARECER-ALVO" not in passages


def test_thin_opinion_and_missing_remission_do_not_fabricate_technical_analysis():
    target, _history, other, rows = _scenario()
    rows = [row for row in rows if row["referencia_id"] == target["id"]]
    missing = _record(
        "history-missing", "TC 01499/26", "0149926", subtype=target["subtipo"]
    )
    thin = _record(
        "history-thin", "TC 02650/25", "0265025", subtype=target["subtipo"]
    )
    rows.extend(
        (
            _row(missing, "parecer", "Conforme o relatório da auditoria."),
            _row(thin, "parecer", "Acompanho integralmente o relatório da auditoria."),
            _row(
                thin,
                "auditoria",
                "APURACAO TECNICA SIGILOSA 12345: desvio de combustível não descrito no parecer.",
            ),
        )
    )
    prepared = MinutasGenerationService(
        Library([target, missing, thin, other], rows)
    ).prepare_context(target["id"])
    by_process = {
        item["processo"]: item
        for item in prepared["contexto"]["referencias_historicas"]
    }

    missing_reference = by_process[missing["numero_processo"]]
    thin_reference = by_process[thin["numero_processo"]]
    assert "documento ausente" in missing_reference["limitacao"]
    assert "não deve ser inventada" in missing_reference["limitacao"]
    assert "12345" not in missing_reference["limitacao"]
    assert "não invente o conteúdo da análise técnica" in thin_reference["limitacao"]
    assert "APURACAO TECNICA SIGILOSA 12345" not in json.dumps(
        thin_reference, ensure_ascii=False
    )
    assert [source["papel"] for source in prepared["contexto"]["documentos_processo_alvo"]] == [
        "auditoria",
        "complementar",
    ]
    assert target["numero_processo"] not in by_process


def test_analysis_separates_defense_evidence_and_rejected_audit_conclusion():
    target, history, other, rows = _scenario()

    def analyze(context):
        return _analysis_result(
            context, analise=_analysis_payload(context, count=2, treatment="REJEITADA")
        )

    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze
    )
    result = service.analyze(target["id"])["analise"]

    assert len(result["irregularidades"]) == 2
    for irregularity in result["irregularidades"]:
        assert irregularity["fatos_documentados"][0]["descricao"].startswith("Fato")
        assert irregularity["argumentos_defesa"][0]["alegacao"].startswith(
            "A defesa alegou"
        )
        assert irregularity["argumentos_defesa"][0]["comprovacao_documental"] == (
            "NÃO COMPROVADA"
        )
        assert irregularity["avaliacao_auditoria"]["tratamento_defesa"] == (
            "REJEITADA"
        )
        assert irregularity["avaliacao_auditoria"]["fontes"][0]["pagina"] == 1


def test_analysis_marks_invented_source_or_page_as_unverified():
    target, history, other, rows = _scenario()

    def analyze(context):
        analysis = _analysis_payload(context)
        analysis["irregularidades"][0]["fatos_documentados"][0]["fontes"] = [
            {"fonte_id": "fonte-inexistente", "pagina": 999}
        ]
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze
    )

    result = service.analyze(target["id"])
    fact = result["analise"]["irregularidades"][0]["fatos_documentados"][0]
    assert fact["estado_conferencia"] == "NAO_VERIFICADA"
    assert result["suficiente_para_geracao"] is True
    assert result["pendencias_conferencia"][0]["natureza"] == "fonte não localizada"


def test_analysis_marks_existing_page_with_unverifiable_excerpt():
    target, history, other, rows = _scenario()

    def analyze(context):
        analysis = _analysis_payload(context)
        analysis["irregularidades"][0]["fatos_documentados"][0]["fontes"][0][
            "trecho_suporte"
        ] = "Trecho inexistente apesar de a página ser válida."
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze
    )

    result = service.analyze(target["id"])
    fact = result["analise"]["irregularidades"][0]["fatos_documentados"][0]
    assert fact["estado_conferencia"] == "NAO_VERIFICADA"
    assert result["suficiente_para_geracao"] is True
    assert result["pendencias_conferencia"][0] == {
        "irregularidade": "IRR-1",
        "categoria": "Fato documentado",
        "documento": "auditoria.pdf",
        "pagina": 1,
        "natureza": "trecho não localizado na página indicada",
        "bloqueante": False,
    }


def test_analysis_accepts_verifiable_excerpt_with_conservative_normalization():
    target, history, other, rows = _scenario()

    def analyze(context):
        analysis = _analysis_payload(context)
        analysis["irregularidades"][0]["fatos_documentados"][0]["fontes"][0][
            "trecho_suporte"
        ] = "“IRREGULARIDADE,   PREVIDENCIARIA” —\nNA PAGINA ORIGINAL!"
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze
    )

    result = service.analyze(target["id"])
    fact = result["analise"]["irregularidades"][0]["fatos_documentados"][0]
    assert fact["estado_conferencia"] == "VERIFICADA"
    assert fact["fontes"][0]["verificado"] is True
    assert "pagina_original" not in fact["fontes"][0]
    assert "pagina_corrigida_automaticamente" not in fact["fontes"][0]
    assert result["suficiente_para_geracao"] is True


@pytest.mark.parametrize("indicated_page", (1, 99))
def test_analysis_corrects_unique_literal_excerpt_copied_from_another_page(
    indicated_page,
):
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    first_page = "Irregularidade previdenciária na página original."
    second_page = "A contribuição de março não foi recolhida."
    audit["texto_integral"] = first_page + "\n" + second_page
    audit["unidades_json"] = json.dumps(
        [
            {"pagina": 1, "texto": first_page},
            {"pagina": 2, "texto": second_page},
        ]
    )

    def analyze(context):
        analysis = _analysis_payload(context)
        reference = analysis["irregularidades"][0]["avaliacao_auditoria"]["fontes"][
            0
        ]
        reference["pagina"] = indicated_page
        reference["trecho_suporte"] = second_page
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze
    )
    result = service.analyze(target["id"])

    reference = result["analise"]["irregularidades"][0]["avaliacao_auditoria"][
        "fontes"
    ][0]

    assert result["suficiente_para_geracao"] is True
    assert result["pendencias_conferencia"] == []
    assert reference["pagina"] == 2
    assert reference["pagina_original"] == indicated_page
    assert reference["pagina_corrigida_automaticamente"] is True
    assert reference["trecho_suporte"] == second_page
    assert reference["verificado"] is True
    diagnostic = result["diagnostico_conferencia"][0]
    assert diagnostic["campo"] == "Conclusão da Auditoria"
    assert diagnostic["trecho_suporte"] == second_page
    assert diagnostic["pagina_original"] == indicated_page
    assert diagnostic["pagina_corrigida"] == 2
    assert diagnostic["pagina_corrigida_automaticamente"] is True
    assert diagnostic["texto_pagina"] == second_page
    assert diagnostic["texto_pagina_original"] == (
        first_page if indicated_page == 1 else ""
    )
    assert diagnostic["bloqueante"] is False

    revalidated = MinutasGenerationService._validate_analysis(
        result["analise"], service.prepare_analysis_context(target["id"])["contexto"]
    )
    revalidated_reference = revalidated["irregularidades"][0][
        "avaliacao_auditoria"
    ]["fontes"][0]
    assert revalidated_reference["pagina"] == 2
    assert revalidated_reference["pagina_original"] == indicated_page
    assert revalidated_reference["pagina_corrigida_automaticamente"] is True


def test_analysis_warns_when_literal_excerpt_matches_multiple_pages():
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    first_page = "Irregularidade previdenciária na página original."
    repeated = "A contribuição de março não foi recolhida."
    audit["texto_integral"] = "\n".join((first_page, repeated, repeated))
    audit["unidades_json"] = json.dumps(
        [
            {"pagina": 1, "texto": first_page},
            {"pagina": 2, "texto": repeated},
            {"pagina": 3, "texto": repeated},
        ]
    )

    def analyze(context):
        analysis = _analysis_payload(context)
        analysis["irregularidades"][0]["avaliacao_auditoria"]["fontes"][0][
            "trecho_suporte"
        ] = repeated
        return _analysis_result(context, analise=analysis)

    result = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze
    ).analyze(target["id"])
    reference = result["analise"]["irregularidades"][0]["avaliacao_auditoria"][
        "fontes"
    ][0]

    assert result["suficiente_para_geracao"] is True
    assert reference["pagina"] == 1
    assert "pagina_original" not in reference
    assert "pagina_corrigida_automaticamente" not in reference
    assert result["pendencias_conferencia"][0]["natureza"] == (
        "trecho não localizado na página indicada"
    )
    diagnostic = result["diagnostico_conferencia"][0]
    assert diagnostic["correspondencias_em_outras_paginas"] == [2, 3]
    assert diagnostic["bloqueante"] is False


def test_paraphrase_exposes_only_same_document_literal_candidates():
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    candidate = (
        "A Auditoria registrou que o recolhimento previdenciário de março não "
        "foi comprovado nos autos."
    )
    audit["texto_integral"] += "\n" + candidate
    audit["unidades_json"] = json.dumps(
        [
            {"pagina": 1, "texto": "Irregularidade previdenciária na página original."},
            {"pagina": 2, "texto": candidate},
        ]
    )

    def analyze(context):
        analysis = _analysis_payload(context)
        fact = analysis["irregularidades"][0]["fatos_documentados"][0]
        fact["descricao"] = (
            "O recolhimento previdenciário de março não foi comprovado."
        )
        fact["fontes"][0]["trecho_suporte"] = (
            "Não houve prova do pagamento previdenciário de março."
        )
        return _analysis_result(context, analise=analysis)

    result = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        search=lambda *_args, **_kwargs: {"resultados": []},
    ).analyze(target["id"])

    assert result["suficiente_para_geracao"] is True
    diagnostic = next(
        item
        for item in result["diagnostico_conferencia"]
        if item["campo"] == "Fato documentado"
    )
    assert diagnostic["afirmacao_associada"].startswith("O recolhimento")
    assert diagnostic["referencia_chave"]
    assert [(item["pagina"], item["trecho_literal"]) for item in diagnostic["candidatos"]] == [
        (2, candidate)
    ]
    assert diagnostic["candidatos"][0]["substituicao_segura"] is True
    assert all(
        "Parecer histórico" not in item["trecho_literal"]
        for item in diagnostic["candidatos"]
    )


def test_paraphrase_with_multiple_candidates_remains_a_warning():
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    passages = (
        "O recolhimento previdenciário de março não foi comprovado no relatório.",
        "O recolhimento previdenciário de março não foi comprovado pela defesa.",
    )
    audit["texto_integral"] += "\n" + "\n".join(passages)
    audit["unidades_json"] = json.dumps(
        [
            {"pagina": 1, "texto": "Irregularidade previdenciária na página original."},
            {"pagina": 2, "texto": passages[0]},
            {"pagina": 3, "texto": passages[1]},
        ]
    )

    def analyze(context):
        analysis = _analysis_payload(context)
        fact = analysis["irregularidades"][0]["fatos_documentados"][0]
        fact["descricao"] = (
            "O recolhimento previdenciário de março não foi comprovado."
        )
        fact["fontes"][0]["trecho_suporte"] = (
            "Não houve prova do pagamento previdenciário de março."
        )
        return _analysis_result(context, analise=analysis)

    result = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        search=lambda *_args, **_kwargs: {"resultados": []},
    ).analyze(target["id"])
    diagnostic = next(
        item
        for item in result["diagnostico_conferencia"]
        if item["campo"] == "Fato documentado"
    )

    assert result["suficiente_para_geracao"] is True
    assert {item["pagina"] for item in diagnostic["candidatos"]} == {2, 3}
    assert all(
        item["substituicao_segura"] for item in diagnostic["candidatos"]
    )


def test_assisted_correction_revalidates_globally_without_new_ai_call():
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    candidate = (
        "A Auditoria registrou que o recolhimento previdenciário de março não "
        "foi comprovado nos autos."
    )
    audit["texto_integral"] += "\n" + candidate
    audit["unidades_json"] = json.dumps(
        [
            {"pagina": 1, "texto": "Irregularidade previdenciária na página original."},
            {"pagina": 2, "texto": candidate},
        ]
    )
    analysis_calls = []
    drafting_calls = []

    def analyze(context):
        analysis_calls.append(context)
        analysis = _analysis_payload(context, count=2)
        fact = analysis["irregularidades"][0]["fatos_documentados"][0]
        fact["descricao"] = (
            "O recolhimento previdenciário de março não foi comprovado."
        )
        fact["fontes"][0]["trecho_suporte"] = (
            "Não houve prova do pagamento previdenciário de março."
        )
        analysis["irregularidades"][1]["normas_mencionadas"][0][
            "descricao"
        ] = "Art. 37 da Constituição"
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        generate=lambda context: drafting_calls.append(context)
        or _draft_result(context),
        search=lambda *_args, **_kwargs: {"resultados": []},
    )
    analyzed = service.analyze(target["id"])
    preserved_irregularity = json.loads(
        json.dumps(analyzed["analise"]["irregularidades"][1])
    )
    preserved_recovery = analyzed["recuperacao_juridica"]
    diagnostic = next(
        item
        for item in analyzed["diagnostico_conferencia"]
        if item["campo"] == "Fato documentado"
    )
    selected = diagnostic["candidatos"][0]

    corrected = service.correct_evidence_reference(
        target["id"], analyzed, diagnostic["referencia_chave"], selected["id"]
    )

    assert len(analysis_calls) == 1
    assert corrected["suficiente_para_geracao"] is True
    assert corrected["pendencias_conferencia"] == []
    assert corrected["recuperacao_juridica"] == preserved_recovery
    assert corrected["analise"]["irregularidades"][1] == preserved_irregularity
    reference = corrected["analise"]["irregularidades"][0][
        "fatos_documentados"
    ][0]["fontes"][0]
    assert reference["pagina"] == 2
    assert reference["trecho_suporte"] == candidate
    assert reference["verificado"] is True
    assert reference["correcao_assistida"]["trecho_original"].startswith(
        "Não houve prova"
    )
    assert reference["correcao_assistida"]["revisao_humana_obrigatoria"] is True
    assert corrected["intervencoes_evidencias"][0]["fonte_id"] == reference[
        "fonte_id"
    ]

    _gerar(service, target["id"], corrected)
    assert len(drafting_calls) == 1


def test_assisted_correction_rejects_candidate_that_changes_objective_value():
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    candidate = "O débito previdenciário apurado foi de R$ 2.000,00."
    audit["texto_integral"] += "\n" + candidate
    audit["unidades_json"] = json.dumps(
        [
            {"pagina": 1, "texto": "Irregularidade previdenciária na página original."},
            {"pagina": 2, "texto": candidate},
        ]
    )

    def analyze(context):
        analysis = _analysis_payload(context)
        fact = analysis["irregularidades"][0]["fatos_documentados"][0]
        fact["descricao"] = "O débito previdenciário foi de R$ 1.000,00."
        fact["fontes"][0]["trecho_suporte"] = (
            "A dívida previdenciária totalizou mil reais."
        )
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        search=lambda *_args, **_kwargs: {"resultados": []},
    )
    analyzed = service.analyze(target["id"])
    diagnostic = next(
        item
        for item in analyzed["diagnostico_conferencia"]
        if item["campo"] == "Fato documentado"
    )
    unsafe = next(item for item in diagnostic["candidatos"] if item["pagina"] == 2)

    assert unsafe["substituicao_segura"] is False
    with pytest.raises(MinutasGenerationError, match="não preserva"):
        service.correct_evidence_reference(
            target["id"], analyzed, diagnostic["referencia_chave"], unsafe["id"]
        )
    assert analyzed["suficiente_para_geracao"] is True


def test_assisted_correction_rejects_other_process_stale_sources_and_access():
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    candidate = "O recolhimento previdenciário não foi comprovado nos autos."
    audit["texto_integral"] += "\n" + candidate
    audit["unidades_json"] = json.dumps(
        [
            {"pagina": 1, "texto": "Irregularidade previdenciária na página original."},
            {"pagina": 2, "texto": candidate},
        ]
    )

    def analyze(context):
        analysis = _analysis_payload(context)
        fact = analysis["irregularidades"][0]["fatos_documentados"][0]
        fact["descricao"] = "O recolhimento previdenciário não foi comprovado."
        fact["fontes"][0]["trecho_suporte"] = "Não houve prova do recolhimento."
        return _analysis_result(context, analise=analysis)

    library = Library([target, history, other], rows)
    service = MinutasGenerationService(
        library,
        analyze=analyze,
        search=lambda *_args, **_kwargs: {"resultados": []},
    )
    analyzed = service.analyze(target["id"])
    diagnostic = next(
        item
        for item in analyzed["diagnostico_conferencia"]
        if item["campo"] == "Fato documentado"
    )
    candidate_id = diagnostic["candidatos"][0]["id"]

    stale_rule = dict(analyzed)
    stale_rule["evidence_correction_version"] = "3c3-obsoleta"
    with pytest.raises(MinutasGenerationError, match="versão da conferência"):
        service.correct_evidence_reference(
            target["id"],
            stale_rule,
            diagnostic["referencia_chave"],
            candidate_id,
        )

    with pytest.raises(MinutasGenerationError, match="não pertence"):
        service.correct_evidence_reference(
            history["id"], analyzed, diagnostic["referencia_chave"], candidate_id
        )

    rows[0]["atualizado_em"] = "2026-10-14T12:00:00+00:00"
    with pytest.raises(MinutasGenerationError, match="fontes foram alteradas"):
        service.correct_evidence_reference(
            target["id"], analyzed, diagnostic["referencia_chave"], candidate_id
        )

    library.authorized = False
    with pytest.raises(MinutasAccessDenied):
        service.correct_evidence_reference(
            target["id"], analyzed, diagnostic["referencia_chave"], candidate_id
        )


@pytest.mark.parametrize(
    ("context_field", "source_id"),
    (
        ("documentos_outra_procuradora", "fonte-outra-procuradora"),
        ("referencias_juridicas_recuperadas", "fonte-rag-historica"),
    ),
)
def test_analysis_never_uses_unauthorized_or_rag_documents_as_factual_support(
    context_field, source_id
):
    target, history, other, rows = _scenario()
    context = MinutasGenerationService(
        Library([target, history, other], rows)
    ).prepare_analysis_context(target["id"])["contexto"]
    context[context_field] = [
        {
            "fonte_id": source_id,
            "conteudo": [
                {"pagina": 9, "texto": "Conclusão literal de documento não autorizado."}
            ],
        }
    ]
    analysis = _analysis_payload(context)
    analysis["irregularidades"][0]["avaliacao_auditoria"]["fontes"] = [
        {
            "fonte_id": source_id,
            "pagina": 9,
            "trecho_suporte": "Conclusão literal de documento não autorizado.",
        }
    ]

    validated = MinutasGenerationService._validate_analysis(analysis, context)
    reference = validated["irregularidades"][0]["avaliacao_auditoria"]["fontes"][0]

    assert reference["verificado"] is False
    assert reference["falha_verificacao"] == "fonte não localizada"
    assert validated["conferencia_documental"]["suficiente_para_geracao"] is True


def test_analysis_marks_defense_allegation_classified_as_documented_fact():
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    text = (
        "Irregularidade previdenciária na página original.\n"
        "A defesa alegou ausência de dano."
    )
    audit["texto_integral"] = text
    audit["unidades_json"] = json.dumps([{"pagina": 1, "texto": text}])

    def analyze(context):
        analysis = _analysis_payload(context)
        fact = analysis["irregularidades"][0]["fatos_documentados"][0]
        fact["descricao"] = "Ausência de dano."
        fact["fontes"][0]["trecho_suporte"] = "A defesa alegou ausência de dano."
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze
    )

    result = service.analyze(target["id"])
    fact = result["analise"]["irregularidades"][0]["fatos_documentados"][0]
    assert fact["fontes"][0]["verificado"] is True
    assert fact["estado_conferencia"] == "NAO_VERIFICADA"
    assert result["suficiente_para_geracao"] is True
    assert any(
        item["natureza"] == "alegação defensiva classificada como fato"
        for item in result["pendencias_conferencia"]
    )


def test_multiple_irregularities_preserve_analysis_and_allow_cautious_drafting():
    target, history, other, rows = _scenario()
    drafting_calls = []

    def analyze(context):
        analysis = _analysis_payload(context, count=2)
        second_fact = analysis["irregularidades"][1]["fatos_documentados"][0]
        second_fact["fontes"][0]["trecho_suporte"] = "Fato inexistente nesta página."
        analysis["irregularidades"][1]["avaliacao_auditoria"]["fontes"][0][
            "trecho_suporte"
        ] = "Conclusão inexistente nesta página."
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        generate=lambda context: drafting_calls.append(context)
        or _draft_result(context, estrutura=_draft_structure(ids=("IRR-1", "IRR-2"))),
    )
    result = service.analyze(target["id"])
    irregularities = result["analise"]["irregularidades"]

    assert irregularities[0]["fatos_documentados"][0]["estado_conferencia"] == (
        "VERIFICADA"
    )
    assert irregularities[1]["fatos_documentados"][0]["estado_conferencia"] == (
        "NAO_VERIFICADA"
    )
    assert result["suficiente_para_geracao"] is True
    generated = _gerar(service, target["id"], result)
    assert len(drafting_calls) == 1
    request_analysis = drafting_calls[0]["analise_estruturada"]
    pending_fact = request_analysis["irregularidades"][1]["fatos_documentados"][0]
    assert pending_fact["tratamento_redacao"].startswith("PENDENTE_DE_CONFERENCIA")
    assert pending_fact["fontes"][0]["trecho_suporte"] == ""
    assert pending_fact["fontes"][0]["uso_na_redacao"] == "NAO_COMPROVADO"
    assert "Fato inexistente nesta página." not in json.dumps(
        drafting_calls[0], ensure_ascii=False
    )
    assert len(generated["advertencias_documentais"]) == 2
    assert {
        warning["categoria"] for warning in generated["advertencias_documentais"]
    } == {"Fato documentado", "Conclusão da Auditoria"}


@pytest.mark.parametrize(
    ("identifier", "field"),
    (
        ("18.1", "avaliacao_auditoria"),
        ("18.2", "argumentos_defesa"),
        ("18.3", "fatos_documentados"),
        ("18.4", "avaliacao_auditoria"),
    ),
)
def test_taperoa_unlocated_citations_are_warnings_and_allow_generation(
    identifier, field
):
    target, history, other, rows = _scenario()
    drafting_calls = []

    def analyze(context):
        analysis = _analysis_payload(context, count=4)
        for index, irregularity in enumerate(analysis["irregularidades"], 1):
            irregularity["id"] = f"18.{index}"
        item = next(
            irregularity
            for irregularity in analysis["irregularidades"]
            if irregularity["id"] == identifier
        )
        evidence = item[field]
        if isinstance(evidence, list):
            evidence = evidence[0]
        evidence["fontes"][0]["trecho_suporte"] = (
            "Citação não localizada na extração autorizada."
        )
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        generate=lambda context: drafting_calls.append(context)
        or _draft_result(
            context,
            estrutura=_draft_structure(ids=("18.1", "18.2", "18.3", "18.4")),
        ),
        search=lambda *_args, **_kwargs: {"resultados": []},
    )

    analyzed = service.analyze(target["id"])
    generated = _gerar(service, target["id"], analyzed)

    assert analyzed["suficiente_para_geracao"] is True
    assert any(
        warning["irregularidade"] == identifier
        and warning["bloqueante"] is False
        for warning in analyzed["pendencias_conferencia"]
    )
    assert len(drafting_calls) == 1
    assert all(
        identifier in topic["irregularidades_ids"]
        for topic in generated["estrutura"]["topicos_irregularidades"]
    )


def test_old_analysis_policy_is_migrated_without_new_gemini_call():
    target, history, other, rows = _scenario()
    analysis_calls = []

    def analyze(context):
        analysis_calls.append(context)
        payload = _analysis_payload(context)
        payload["irregularidades"][0]["fatos_documentados"][0]["fontes"][0][
            "trecho_suporte"
        ] = "Paráfrase antiga sem correspondência literal."
        return _analysis_result(context, analise=payload)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        search=lambda *_args, **_kwargs: {"resultados": []},
    )
    current = service.analyze(target["id"])
    legacy = deepcopy(current)
    legacy.pop("validation_policy_version")
    legacy["suficiente_para_geracao"] = False
    legacy["pendencias_conferencia"][0]["bloqueante"] = True
    recovery_snapshot = deepcopy(legacy["recuperacao_juridica"])

    migrated = service.migrate_validation_policy(target["id"], legacy)

    assert len(analysis_calls) == 1
    assert migrated["validation_policy_version"] == VALIDATION_POLICY_VERSION
    assert migrated["suficiente_para_geracao"] is True
    assert migrated["pendencias_conferencia"][0]["bloqueante"] is False
    assert migrated["recuperacao_juridica"] == recovery_snapshot


def test_analysis_rejects_omitted_identifiable_audit_finding():
    target, history, other, rows = _scenario()

    def analyze(context):
        return _analysis_result(context, analise=_analysis_payload(context, count=0))

    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze
    )

    with pytest.raises(MinutasGenerationError, match="omitido apontamentos"):
        service.analyze(target["id"])


def test_analysis_accepts_process_legitimately_without_identifiable_findings():
    target, history, other, rows = _scenario()
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    text = "Não foram identificadas irregularidades na instrução."
    audit["texto_integral"] = text
    audit["unidades_json"] = json.dumps([{"pagina": 1, "texto": text}])
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=lambda context: _analysis_result(
            context, analise=_analysis_payload(context, count=0)
        ),
    )

    result = service.analyze(target["id"])
    assert result["analise"]["irregularidades"] == []
    assert result["analise"]["sem_irregularidades_identificadas"] is True


def test_quality_checks_warn_without_changing_legal_conclusion():
    target, history, other, rows = _scenario()

    def generate(_context):
        structure = _draft_structure(ids=())
        structure["ementa"] = "CONTAS IRREGULARES."
        structure["relatorio"] = "Valor de R$ 9.999,99 indicado na página 99."
        structure["conclusao_pedidos"] = "Opina-se pela regularidade das contas."
        return _draft_result(_context, estrutura=structure)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=generate,
    )
    analyzed = service.analyze(target["id"])
    result = _gerar(service, target["id"], analyzed)

    warnings = "\n".join(result["advertencias_qualidade"])
    assert "Possível omissão" in warnings
    assert "R$ 9.999,99" in warnings
    assert "página sem correspondência" in warnings
    assert "inconsistência entre a ementa" in warnings
    assert result["estrutura"]["conclusao_pedidos"] == (
        "Ante o exposto, em harmonia com o entendimento do Órgão Técnico, "
        "o Ministério Público de Contas opina pelo (a):"
    )
    assert any(
        "regularidade das contas" in item.casefold()
        for item in result["estrutura"]["pedidos"]
    )


def test_rejected_defense_with_only_generic_divergence_is_warned():
    target, history, other, rows = _scenario()
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=lambda context: _draft_result(
            context,
            estrutura=_draft_structure(
                ministerial_treatment="ACOLHIDA",
                divergence_grounds=(
                    "Aplicam-se proporcionalidade e ausência de dolo, conforme os "
                    "documentos juntados na página 1.",
                ),
            ),
        ),
    )

    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert any(
        "Divergência da Auditoria sem fundamento" in warning
        for warning in result["advertencias_qualidade"]
    )


def test_rejected_defense_with_specific_divergence_is_not_warned():
    target, history, other, rows = _scenario()
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=lambda context: _draft_result(
            context,
            estrutura=_draft_structure(
                ministerial_treatment="PARCIALMENTE_ACOLHIDA",
                divergence_grounds=(
                    "O comprovante da página 1 demonstra o recolhimento no prazo.",
                ),
            ),
        ),
    )

    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert not any(
        "Divergência da Auditoria sem fundamento" in warning
        for warning in result["advertencias_qualidade"]
    )


def test_categorical_cure_with_insufficient_documents_is_warned():
    target, history, other, rows = _scenario()

    def generate(context):
        structure = _draft_structure(cure_state="ALEGADO_NAO_COMPROVADO")
        structure["topicos_irregularidades"][0]["texto"] = (
            "A irregularidade foi sanada após a juntada anunciada pela defesa."
        )
        return _draft_result(context, estrutura=structure)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=generate,
    )

    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert any(
        "sem justificativa probatória textual verificável" in warning
        for warning in result["advertencias_qualidade"]
    )


def test_existing_document_and_page_without_cure_excerpt_is_warned():
    target, history, other, rows = _scenario()

    def generate(context):
        source = context["documentos_processo_alvo"][0]
        page = source["conteudo"][0].get("pagina", 0)
        structure = _draft_structure(
            cure_state="COMPROVADO",
            cure_sources=(
                {
                    "fonte_id": source["fonte_id"],
                    "pagina": page,
                    "trecho_suporte": "Regularização inexistente no conteúdo da página.",
                },
            ),
        )
        structure["topicos_irregularidades"][0]["texto"] = (
            "A irregularidade foi sanada."
        )
        return _draft_result(context, estrutura=structure)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=generate,
    )

    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert any(
        "sem justificativa probatória textual verificável" in warning
        for warning in result["advertencias_qualidade"]
    )


def test_verified_cure_with_specific_support_is_not_warned():
    target, history, other, rows = _scenario()

    def generate(context):
        source = context["documentos_processo_alvo"][0]
        unit = source["conteudo"][0]
        support = next(
            line.strip() for line in unit["texto"].splitlines() if line.strip()
        )
        structure = _draft_structure(
            ministerial_treatment="PARCIALMENTE_ACOLHIDA",
            divergence_grounds=(
                "O comprovante da página 1 demonstra o recolhimento no prazo.",
            ),
            cure_state="COMPROVADO",
            cure_sources=(
                {
                    "fonte_id": source["fonte_id"],
                    "pagina": unit.get("pagina", 0),
                    "trecho_suporte": support,
                },
            ),
        )
        structure["topicos_irregularidades"][0]["texto"] = (
            "A irregularidade foi sanada com suporte documental verificável."
        )
        return _draft_result(context, estrutura=structure)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=generate,
    )

    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert not any(
        "saneamento" in warning.casefold()
        for warning in result["advertencias_qualidade"]
    )


def test_coherent_draft_renders_numbered_requests_and_closing():
    target, history, other, rows = _scenario()
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
    )

    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert "1. Submeta-se a minuta à revisão jurídica humana." in result["texto"]
    assert "É o Parecer." in result["texto"]
    assert not any(
        "inconsistência entre a ementa" in warning
        for warning in result["advertencias_qualidade"]
    )


def test_duplicate_request_line_is_rendered_only_in_numbered_requests():
    target, history, other, rows = _scenario()

    def generate(context):
        structure = _draft_structure()
        request = structure["pedidos"][0]
        structure["conclusao_pedidos"] = (
            "Diante da fundamentação exposta, propõem-se os encaminhamentos:\n"
            f"1. {request}"
        )
        return _draft_result(context, estrutura=structure)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=generate,
    )

    result = _gerar(service, target["id"], service.analyze(target["id"]))

    request = result["estrutura"]["pedidos"][0]
    assert result["texto"].count(request) == 1
    assert "1. " + request in result["texto"]


def test_structured_paragraphs_are_used_without_breaking_legacy_structures():
    structure = _draft_structure()
    structure["relatorio"] = "Relatório legado."
    structure["relatorio_paragrafos"] = [
        "Apresentação do processo.",
        "É o relatório. Passo a opinar.",
    ]
    structure["fundamentacao_geral_paragrafos"] = [
        "Fundamentação geral em parágrafo próprio."
    ]
    structure["topicos_irregularidades"][0]["paragrafos"] = [
        "Constatação da Auditoria.",
        "Alegação da defesa.",
        "Análise ministerial.",
    ]

    validated = MinutasGenerationService._validate_structure(structure)
    rendered = MinutasGenerationService._structure_markdown(validated)

    assert "Relatório legado." not in rendered
    assert (
        "Apresentação do processo.\n\nÉ o Relatório. Passo a opinar."
        in rendered
    )
    assert rendered.count("É o Relatório. Passo a opinar.") == 1
    assert "Passa-se a opinar" not in rendered
    assert "Constatação da Auditoria.\n\nAlegação da defesa." in rendered
    assert MinutasGenerationService._validate_structure(_draft_structure())


def test_sanction_control_merges_into_matching_topic_without_autonomous_chapter():
    target = _record("target-taperoa", "TC 01653/26", "0165326")
    structure = _draft_structure()
    sanction_text = structure["analise_sancionatoria"]["texto"]

    normalized, _warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )
    rendered = MinutasGenerationService._structure_markdown(normalized)

    assert "Análise sancionatória" not in rendered
    assert "### Análise sancionatória" not in rendered
    assert sanction_text in rendered
    assert sanction_text in normalized["topicos_irregularidades"][0]["texto"]
    assert sanction_text in normalized["topicos_irregularidades"][0]["paragrafos"]
    assert not any("não foi vinculada" in warning for warning in _warnings)

    empty = _draft_structure()
    empty["analise_sancionatoria"]["texto"] = ""
    validated = MinutasGenerationService._validate_structure(empty)
    empty_rendered = MinutasGenerationService._structure_markdown(validated)
    assert validated["analise_sancionatoria"]["texto"] == ""
    assert "Análise sancionatória" not in empty_rendered


def test_generic_sanction_topic_title_is_replaced_and_content_is_kept():
    target = _record("target-taperoa", "TC 01653/26", "0165326")
    structure = _draft_structure()
    topic_text = structure["topicos_irregularidades"][0]["texto"]
    structure["topicos_irregularidades"][0]["titulo"] = "Análise sancionatória"

    normalized, warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )
    rendered = MinutasGenerationService._structure_markdown(normalized)

    assert normalized["topicos_irregularidades"][0]["titulo"] == "Irregularidade IRR-1"
    assert "Análise sancionatória" not in rendered
    assert topic_text in rendered
    assert any("capítulo autônomo" in warning for warning in warnings)


def test_unmatched_sanction_reasoning_is_preserved_without_invented_link():
    target = _record("target-taperoa", "TC 01653/26", "0165326")
    structure = _draft_structure(ids=("18.1", "18.2"))
    first, second = structure["topicos_irregularidades"][0], {
        "titulo": "18.2 Certificação dos conselhos",
        "irregularidades_ids": ["18.2"],
        "avaliacoes_irregularidades": [
            {
                "irregularidade_id": "18.2",
                "tratamento_ministerial_defesa": "MANTIDA_REJEICAO",
                "fundamentos_divergencia": [],
                "estado_saneamento": "ALEGADO_NAO_COMPROVADO",
                "fontes_saneamento": [],
            }
        ],
        "texto": "A certificação dos conselhos permanece controversa na instrução.",
    }
    first["irregularidades_ids"] = ["18.1"]
    first["avaliacoes_irregularidades"] = [
        item
        for item in first["avaliacoes_irregularidades"]
        if item["irregularidade_id"] == "18.1"
    ]
    structure["topicos_irregularidades"] = [first, second]
    sanction_text = (
        "A multa do art. 100 da LC nº 192/2024 depende de autoria e nexo "
        "individualizados em cada irregularidade."
    )
    structure["analise_sancionatoria"].update(
        conclusao="MULTA_CABIVEL",
        irregularidades_ids=["18.1", "18.2"],
        fundamentos_legais=["Art. 100, I, da LC nº 192/2024"],
        responsaveis=["Gestor individualizado na fundamentação"],
        texto=sanction_text,
    )
    structure["pedidos"] = [
        "Proponha-se a irregularidade das contas submetidas a julgamento.",
        "Aplique-se multa pela infração normativa individualizada.",
    ]

    normalized, warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )
    rendered = MinutasGenerationService._structure_markdown(normalized)

    assert sanction_text in normalized["fundamentacao_geral"]
    assert sanction_text not in normalized["topicos_irregularidades"][0]["texto"]
    assert sanction_text not in normalized["topicos_irregularidades"][1]["texto"]
    assert sanction_text in rendered
    assert "Análise sancionatória" not in rendered
    assert any("Aplique-se multa" in item for item in normalized["pedidos"])
    assert any("não foi vinculada" in warning for warning in warnings)
    assert any("sem vínculo único" in warning for warning in warnings)


def test_device_introduction_is_objective_and_follows_audit_position():
    target = _record("target-taperoa", "TC 01653/26", "0165326")
    harmony, _warnings = MinutasGenerationService._normalize_structure(
        _draft_structure(), target
    )
    assert harmony["conclusao_pedidos"] == DEVICE_INTRO_HARMONY
    assert harmony["conclusao_pedidos"].count("Ante o exposto") == 1
    assert "regularidade" not in harmony["conclusao_pedidos"].casefold()

    divergent_structure = _draft_structure(ministerial_treatment="ACOLHIDA")
    divergent_structure["conclusao_pedidos"] = (
        "Ante o exposto, em harmonia com o entendimento do Órgão Técnico, "
        "o Ministério Público de Contas opina pelo (a):\n"
        "Pela expedição de recomendação à gestão, no sentido de Recomendar "
        "que aprimore o controle previdenciário."
    )
    divergent, _warnings = MinutasGenerationService._normalize_structure(
        divergent_structure, target
    )
    assert divergent["conclusao_pedidos"] == DEVICE_INTRO_NEUTRAL
    assert "harmonia" not in divergent["conclusao_pedidos"].casefold()
    assert any("aprimore o controle previdenciário" in item for item in divergent["pedidos"])
    assert all("no sentido de Recomendar" not in item for item in divergent["pedidos"])


def test_draft_header_removes_bureaucratic_phrases_and_groups_recommendations():
    target, history, other, rows = _scenario()
    target.update(
        {
            "origem": "Instituto Municipal de Previdência",
            "responsavel": "Gestora Responsável",
            "parecer_numero": "999/2025",
        }
    )

    def generate(context):
        structure = _draft_structure()
        structure["relatorio_paragrafos"] = [
            "Integram a instrução processual: Relatório da Auditoria.",
            "A instrução processual é composta por relatório e defesa.",
            "O processo está instruído com relatório, defesa e parecer.",
            "A Auditoria apontou falha previdenciária relevante.",
        ]
        structure["fundamentacao_geral"] = (
            "Constam dos autos os seguintes documentos: relatório e defesa. "
            "A controvérsia exige exame jurídico da obrigação previdenciária."
        )
        structure["topicos_irregularidades"][0]["texto"] = (
            "Foram juntados aos autos os seguintes documentos: relatório e defesa. "
            "A defesa não comprovou o recolhimento indicado."
        )
        structure["pedidos"] = [
            "Proponha-se a regularidade com ressalvas das contas.",
            "Recomende-se à gestão que aprimore o controle previdenciário.",
            "Expeçam-se recomendações:\na) registre as providências adotadas;\n"
            "b) acompanhe os prazos legais.",
        ]
        return _draft_result(context, estrutura=structure)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=generate,
    )
    result = _gerar(service, target["id"], service.analyze(target["id"]))
    structure = result["estrutura"]

    assert structure["identificacao_processual"].splitlines() == [
        "Parecer Nº",
        "Processo TC Nº 01653/26",
        "Origem: Instituto Municipal de Previdência",
        "Natureza: Prestação de Contas Anuais - exercício 2026",
        "Responsável: Gestora Responsável",
    ]
    assert result["identificador_minuta"] == "MINUTA-TC-01653/26"
    assert "MINUTA-TC-01653/26" not in result["texto"]
    assert "999/2025" not in result["texto"]
    forbidden = (
        "Integram a instrução processual",
        "A instrução processual é composta por",
        "O processo está instruído com",
        "Constam dos autos os seguintes documentos",
        "Foram juntados aos autos os seguintes documentos",
    )
    assert not any(item in result["texto"] for item in forbidden)
    assert "A Auditoria apontou falha previdenciária relevante." in result["texto"]
    assert "A controvérsia exige exame jurídico" in result["texto"]
    assert "A defesa não comprovou o recolhimento" in result["texto"]

    recommendations = [
        item for item in structure["pedidos"] if "recomend" in item.casefold()
    ]
    assert len(recommendations) == 1
    assert recommendations[0].startswith(
        "Pela expedição de recomendações à gestão do Instituto Municipal de Previdência"
    )
    assert "no sentido de Recomendar" not in recommendations[0]
    assert "Recomendem-se, de forma consolidada" not in recommendations[0]
    assert "aprimore o controle previdenciário" in recommendations[0]
    assert "registre as providências adotadas" in recommendations[0]
    assert "acompanhe os prazos legais" in recommendations[0]
    assert "\na) " in recommendations[0]
    assert "\nb) " in recommendations[0]
    assert structure["pedidos"][0].startswith(
        "Proponha-se a regularidade com ressalvas"
    )
    renormalized, _warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )
    assert renormalized["pedidos"] == structure["pedidos"]


def test_recommendation_item_drops_nested_expedition_wrapper_without_changing_content():
    target = _record("target-taperoa", "TC 01653/26", "0165326")
    target.update(
        origem="Prefeitura Municipal de Frei Martinho",
        especialidade_id="elvira.pca.prefeituras",
        subtipo="Prefeitura",
    )
    structure = _draft_structure()
    structure["pedidos"] = [
        "Proponha-se a regularidade com ressalvas das contas.",
        (
            "Pela expedição de recomendação à gestão da Prefeitura Municipal de "
            "Frei Martinho, no sentido de que pela expedição de recomendações "
            "corretivas documente as providências adotadas."
        ),
    ]

    normalized, _warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )
    recommendations = [
        item for item in normalized["pedidos"] if "recomend" in item.casefold()
    ]
    assert len(recommendations) == 1
    assert recommendations[0] == (
        "Pela expedição de recomendação à gestão da Prefeitura Municipal de "
        "Frei Martinho, no sentido de que documente as providências adotadas."
    )
    assert "no sentido de que pela expedição" not in recommendations[0].casefold()
    assert "recomendações corretivas" not in recommendations[0].casefold()


def test_ementa_and_device_preliminary_opinion_divergence_is_warned_without_rewriting_merit():
    target = _record("target-taperoa", "TC 01653/26", "0165326")
    target.update(
        origem="Prefeitura Municipal de Frei Martinho",
        especialidade_id="elvira.pca.prefeituras",
        subtipo="Prefeitura",
        exercicio=2024,
    )
    structure = _draft_structure()
    structure["ementa"] = (
        "PRESTAÇÃO DE CONTAS ANUAIS. PARECER PRÉVIO FAVORÁVEL À APROVAÇÃO DAS "
        "CONTAS DE GOVERNO, COM RESSALVAS."
    )
    structure["pedidos"] = [
        "Proponha-se a emissão de parecer favorável sobre as contas de governo."
    ]
    normalized, _warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )
    assert "com ressalvas" in normalized["ementa"].casefold()
    assert normalized["pedidos"][0] == (
        "Pela emissão de parecer prévio favorável à aprovação das contas de governo "
        "de Prefeitura Municipal de Frei Martinho, relativas ao exercício de 2024."
    )
    warnings = MinutasGenerationService._quality_warnings(
        normalized,
        {"irregularidades": []},
        {"documentos_processo_alvo": [], "processo_alvo": {"numero": "02659/25"}},
    )
    assert any(
        "ementa" in warning.casefold() and "parecer prévio" in warning.casefold()
        for warning in warnings
    )
    assert "com ressalvas" in normalized["ementa"].casefold()
    assert "com ressalvas" not in normalized["pedidos"][0].casefold()


def test_header_metadata_uses_only_unambiguous_target_extractions():
    target, history, other, rows = _scenario()
    target_source = next(
        row for row in rows if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    target_text = (
        "ENTIDADE: Instituto de Previdência do Município de Taperoá\n"
        "GESTOR RESPONSÁVEL: André Batista de Queiroz\n"
        "Irregularidade previdenciária na página original."
    )
    target_source["texto_integral"] = target_text
    target_source["unidades_json"] = json.dumps(
        [{"pagina": 1, "texto": target_text}], ensure_ascii=False
    )

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
    )
    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert result["metadados_cabecalho"] == {
        "origem": "Instituto de Previdência do Município de Taperoá",
        "responsavel": "André Batista de Queiroz",
    }
    assert "Origem: Instituto de Previdência do Município de Taperoá" in result["texto"]
    assert "Responsável: André Batista de Queiroz" in result["texto"]


def test_header_metadata_omits_conflicting_or_non_entity_values():
    target, history, other, rows = _scenario()
    target_source = next(
        row for row in rows if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    target_text = (
        "ENTIDADE: Instituto de Previdência do Município de Taperoá\n"
        "ENTIDADE: Instituto Municipal de Previdência de Outra Cidade\n"
        "RESPONSÁVEL: Auditor da Unidade Técnica"
    )
    target_source["texto_integral"] = target_text
    target_source["unidades_json"] = json.dumps(
        [{"pagina": 1, "texto": target_text}], ensure_ascii=False
    )

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
    )
    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert result["metadados_cabecalho"] == {}
    assert "Origem: Não informada" in result["texto"]
    assert "Responsável: Não informado" in result["texto"]


def test_header_metadata_does_not_leak_from_historical_processes():
    target, history, other, rows = _scenario()
    historical_source = next(
        row for row in rows if row["referencia_id"] == history["id"] and row["papel"] == "auditoria"
    )
    historical_text = (
        "ENTIDADE: Prefeitura Municipal de Outra Cidade\n"
        "RESPONSÁVEL: Gestor Histórico"
    )
    historical_source["texto_integral"] = historical_text
    historical_source["unidades_json"] = json.dumps(
        [{"pagina": 1, "texto": historical_text}], ensure_ascii=False
    )

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
    )
    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert result["metadados_cabecalho"] == {}
    assert "Outra Cidade" not in result["texto"]


def test_header_metadata_supports_another_jurisdiction_type():
    target, history, other, rows = _scenario()
    target["subtipo"] = "Câmara Municipal"
    target_source = next(
        row for row in rows if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    target_text = (
        "ÓRGÃO: Câmara Municipal de Taperoá\n"
        "ORDENADOR DE DESPESA: Maria de Sousa"
    )
    target_source["texto_integral"] = target_text
    target_source["unidades_json"] = json.dumps(
        [{"pagina": 1, "texto": target_text}], ensure_ascii=False
    )

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
    )
    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert result["metadados_cabecalho"] == {
        "origem": "Câmara Municipal de Taperoá",
        "responsavel": "Maria de Sousa",
    }


def test_header_metadata_recovers_explicit_narrative_responsible_from_target_sources():
    target, history, other, rows = _scenario()
    target["subtipo"] = "Prefeitura"
    target_source = next(
        row for row in rows if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    target_text = (
        "ENTIDADE: Prefeitura Municipal de São Bentinho\n"
        "No exercício de 2025, esteve sob a responsabilidade da Prefeita Sra. "
        "Giovana Leite Cavalcanti Olimpio.\n"
        "O auditor João da Silva registrou o achado técnico."
    )
    target_source["texto_integral"] = target_text
    target_source["unidades_json"] = json.dumps(
        [{"pagina": 1, "texto": target_text}], ensure_ascii=False
    )
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
    )
    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert result["metadados_cabecalho"] == {
        "origem": "Prefeitura Municipal de São Bentinho",
        "responsavel": "Giovana Leite Cavalcanti Olimpio",
    }
    assert "Responsável: Giovana Leite Cavalcanti Olimpio" in result["texto"]


def test_header_metadata_reads_collapsed_cover_and_ignores_other_mayors():
    target, history, other, rows = _scenario()
    target["subtipo"] = "Prefeitura"
    target["origem"] = ""
    target_source = next(
        row for row in rows if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    target_text = (
        "TRIBUNAL DE CONTAS DO ESTADO DA PARAIBA Processo TC: 02659/25 "
        "Subcategoria: Prestacao de Contas Anual - PCA "
        "Jurisdicionado: Prefeitura Municipal de Frei Martinho "
        "Responsavel: Sebastiao Pinto Dantas Exercicio: 2024 "
        "Relatorio de Auditoria das contas. "
        "O Prefeito Joao Ninguem foi citado em precedente de outro municipio."
    )
    target_source["texto_integral"] = target_text
    target_source["unidades_json"] = json.dumps(
        [{"pagina": 1, "texto": target_text}], ensure_ascii=False
    )
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
    )
    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert result["metadados_cabecalho"] == {
        "origem": "Prefeitura Municipal de Frei Martinho",
        "responsavel": "Sebastiao Pinto Dantas",
    }
    assert "Responsável: Sebastiao Pinto Dantas" in result["texto"]
    assert "Municipal Sebastiao" not in result["texto"]
    assert "Joao Ninguem" not in result["estrutura"]["identificacao_processual"]
    assert "Não informado" not in result["estrutura"]["identificacao_processual"]


@pytest.mark.parametrize(
    "subtype",
    (
        "Instituto/Autarquia",
        "Câmara Municipal",
        "Secretaria Municipal ou Estadual",
    ),
)
def test_other_pca_uses_direct_accounts_outcome_and_preserves_relief(subtype):
    target, history, other, rows = _scenario()
    target["subtipo"] = subtype

    def generate(context):
        structure = _draft_structure()
        structure["pedidos"] = [
            "Proponha-se a emissão de parecer favorável.",
            "Proponha-se a regularidade com ressalvas das contas.",
            "Aplique-se a multa fundamentada no tópico 18.1.",
        ]
        return _draft_result(context, estrutura=structure)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=generate,
    )
    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert result["estrutura"]["pedidos"][0].startswith(
        "Proponha-se a regularidade com ressalvas"
    )
    assert all(
        "parecer favorável" not in item.casefold()
        for item in result["estrutura"]["pedidos"]
    )
    assert any("multa fundamentada" in item for item in result["estrutura"]["pedidos"])


def test_rpps_blocks_ambiguous_preliminary_opinion_request_only_in_device():
    target, _history, _other, _rows = _scenario()
    structure = _draft_structure()
    legitimate_reference = (
        "O parecer prévio é reservado às contas de governo e não se aplica ao RPPS."
    )
    structure["fundamentacao_geral"] = legitimate_reference
    structure["pedidos"] = [
        "Emissão de parecer prévio ou proposta de julgamento das contas como irregulares."
    ]

    normalized, warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )

    assert normalized["fundamentacao_geral"] == legitimate_reference
    assert normalized["pedidos"] == ["Julgamento das contas como irregulares."]
    assert all(
        "emissão de parecer prévio" not in item.casefold()
        for item in normalized["pedidos"]
    )
    assert any("PCA de RPPS" in warning for warning in warnings)


def test_rpps_orders_relief_and_rejects_debit_without_quantified_harm():
    target, _history, _other, _rows = _scenario()
    structure = _draft_structure()
    structure["analise_sancionatoria"].update(
        conclusao="MULTA_CABIVEL",
        fundamentos_legais=["Art. 100, I, da LC nº 192/2024"],
        responsaveis=["Gestor individualizado na fundamentação"],
    )
    structure["pedidos"] = [
        "Remessa de cópia ao órgão competente.",
        "Recomendação ao Instituto para corrigir a falha.",
        "Imputação de débito ao gestor.",
        "Aplicação de multa pela irregularidade e infração normativa individualizada.",
        "Julgamento das contas como irregulares.",
    ]

    normalized, warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )

    assert normalized["pedidos"][0] == "Julgamento das contas como irregulares."
    assert "multa" in normalized["pedidos"][1].casefold()
    assert all("débito" not in item.casefold() for item in normalized["pedidos"])
    assert "recomenda" in normalized["pedidos"][2].casefold()
    assert "remessa" in normalized["pedidos"][3].casefold()
    assert any("dano ao erário comprovado" in warning for warning in warnings)


def test_city_pca_orders_two_existing_competences_without_inventing_them():
    target, _history, _other, _rows = _scenario()
    target["subtipo"] = "Prefeitura"
    target["especialidade_id"] = "elvira.pca.prefeituras"
    target["origem"] = "Prefeitura Municipal de São Bentinho"
    structure = _draft_structure()
    structure["pedidos"] = [
        "Aplique-se a multa fundamentada no tópico 18.1.",
        "Proponha-se a irregularidade das contas submetidas a julgamento.",
        "Proponha-se a emissão de parecer contrário sobre as contas de governo.",
    ]
    normalized, _warnings = MinutasGenerationService._normalize_structure(structure, target)

    assert normalized["pedidos"][0] == (
        "Pela emissão de parecer prévio contrário à aprovação das contas de governo "
        "de Prefeitura Municipal de São Bentinho, relativas ao exercício de 2026, pelos fundamentos expostos."
    )
    assert "irregularidade das contas" in normalized["pedidos"][1]
    assert "multa fundamentada" in normalized["pedidos"][2]

    structure = _draft_structure()
    structure["pedidos"] = [
        "Proponha-se a emissão de parecer favorável sobre as contas de governo."
    ]
    normalized, warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )
    assert normalized["pedidos"] == [
        "Pela emissão de parecer prévio favorável à aprovação das contas de governo "
        "de Prefeitura Municipal de São Bentinho, relativas ao exercício de 2026."
    ]
    assert not warnings


@pytest.mark.parametrize(
    ("device_request", "expected"),
    (
        (
            "Proponha-se a emissão de parecer favorável sobre as contas de governo.",
            "Pela emissão de parecer prévio favorável à aprovação das contas de governo "
            "de Prefeitura Municipal de São Bentinho, relativas ao exercício de 2025.",
        ),
        (
            "Proponha-se a emissão de parecer favorável, com ressalvas, sobre as contas de governo.",
            "Pela emissão de parecer prévio favorável à aprovação, com ressalvas, das contas de governo "
            "de Prefeitura Municipal de São Bentinho, relativas ao exercício de 2025, pelos fundamentos expostos.",
        ),
        (
            "Proponha-se a emissão de parecer contrário sobre as contas de governo.",
            "Pela emissão de parecer prévio contrário à aprovação das contas de governo "
            "de Prefeitura Municipal de São Bentinho, relativas ao exercício de 2025, pelos fundamentos expostos.",
        ),
    ),
)
def test_prefectures_government_opinion_is_normalized_deterministically(device_request, expected):
    target, _history, _other, _rows = _scenario()
    target.update(
        especialidade_id="elvira.pca.prefeituras",
        subtipo="Prefeitura",
        origem="Prefeitura Municipal de São Bentinho",
        exercicio=2025,
    )
    structure = _draft_structure()
    structure["pedidos"] = [device_request, "Aplique-se multa individualizada, quando fundamentada."]

    normalized, warnings = MinutasGenerationService._normalize_structure(structure, target)
    repeated, repeated_warnings = MinutasGenerationService._normalize_structure(normalized, target)

    assert normalized["pedidos"] == [expected, structure["pedidos"][1]]
    assert repeated["pedidos"] == normalized["pedidos"]
    assert not warnings and not repeated_warnings


def test_prefectures_management_only_does_not_invent_government_opinion():
    target, _history, _other, _rows = _scenario()
    target.update(especialidade_id="elvira.pca.prefeituras", subtipo="Prefeitura")
    structure = _draft_structure()
    structure["pedidos"] = [
        "Proponha-se a irregularidade das contas submetidas a julgamento.",
        "Aplique-se multa individualizada, quando fundamentada.",
    ]

    normalized, warnings = MinutasGenerationService._normalize_structure(structure, target)

    assert normalized["pedidos"] == structure["pedidos"]
    assert all("parecer prévio" not in item.casefold() for item in normalized["pedidos"])
    assert not any("apenas de gestão" in warning for warning in warnings)

    documented = {
        "documentos_processo_alvo": [
            {
                "conteudo": [
                    {
                        "texto": (
                            "Compete emitir parecer prévio sobre as contas de governo "
                            "do Chefe do Poder Executivo."
                        )
                    }
                ]
            }
        ]
    }
    _normalized, documented_warnings = MinutasGenerationService._normalize_structure(
        structure, target, documented
    )
    assert any("apenas de gestão" in warning for warning in documented_warnings)


def test_city_pca_keeps_only_one_conclusion_for_each_documented_competence():
    target, _history, _other, _rows = _scenario()
    target["subtipo"] = "Prefeitura"
    target["especialidade_id"] = "elvira.pca.prefeituras"
    structure = _draft_structure()
    structure["pedidos"] = [
        "Proponha-se a emissão de parecer contrário sobre as contas de governo.",
        "Proponha-se a emissão de parecer favorável sobre as contas de governo.",
        "Proponha-se a irregularidade das contas submetidas a julgamento.",
        "Proponha-se a regularidade das contas submetidas a julgamento.",
        "Aplique-se a multa fundamentada no tópico 18.1.",
    ]

    normalized, warnings = MinutasGenerationService._normalize_structure(
        structure, target
    )

    assert normalized["pedidos"] == [
        "Proponha-se a emissão de parecer contrário sobre as contas de governo.",
        "Proponha-se a irregularidade das contas submetidas a julgamento.",
        "Aplique-se a multa fundamentada no tópico 18.1.",
    ]
    assert any("conclusões principais repetidas" in warning for warning in warnings)


def test_quality_warnings_preserve_invalid_currency_and_accounting_code():
    target, history, other, rows = _scenario()
    accounting_code = "1.1.1.1.01.00"

    def generate(context):
        structure = _draft_structure()
        structure["fundamentacao_geral"] = (
            "O código contábil "
            + accounting_code
            + " foi mantido. O valor indicado foi R$ 409.458.55."
        )
        structure["fundamentacao_geral_paragrafos"] = [
            structure["fundamentacao_geral"]
        ]
        return _draft_result(context, estrutura=structure)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=generate,
    )
    result = _gerar(service, target["id"], service.analyze(target["id"]))

    assert accounting_code in result["texto"]
    assert "R$ 409.458.55" in result["texto"]
    assert "R$ 409.458,55" not in result["texto"]
    assert any(
        "separadores incompatíveis" in warning
        for warning in result["advertencias_qualidade"]
    )


def test_drafting_contract_avoids_automatic_negative_requests_and_uses_rag():
    prompt = ai_service.PROMPT_MINUTA_PARECER
    schema = ai_service._MINUTA_OUTPUT_SCHEMA

    assert "Não crie pedido expresso de afastamento" in prompt
    assert "Preserve pedido negativo somente" in prompt
    assert "aproveite de modo explícito o critério" in prompt
    assert 'encerramento deve ser apenas "É o Parecer."' in prompt
    assert "sequencia_processual_verificada" in prompt
    assert "página física do PDF" in prompt
    assert "DEMAIS_PCA_JULGAMENTO_DIRETO" in prompt
    assert "PREFEITURA_CONDICIONAL_DUAS_COMPETENCIAS" in prompt
    assert "LC nº 192/2024" in prompt
    assert "Regimento Interno do TCE-PB" in prompt
    assert "o backend inserirá deterministicamente" in prompt
    assert "Integram a instrução processual" in prompt
    assert "nunca reutilize número do parecer definitivo" in prompt
    assert "não invente o conteúdo da análise técnica" in prompt
    assert "fundamentos jurídicos pertinentes" in prompt
    assert "reúna todas as recomendações em um único item" in prompt
    assert "em harmonia com o entendimento do Órgão Técnico" in prompt
    assert "expedição de recomendação no sentido de Recomendar" in prompt
    assert "nunca como capítulo do parecer" in prompt
    assert "Análise sancionatória" in prompt
    assert "não reproduza essa remissão" in prompt
    assert "déficit orçamentário ou atuarial" in prompt
    assert "analise_sancionatoria" in schema["required"]
    sanction_schema = schema["properties"]["analise_sancionatoria"]
    assert "dano_ao_erario" in sanction_schema["required"]
    assert "relatorio_paragrafos" in schema["required"]
    assert "fundamentacao_geral_paragrafos" in schema["required"]
    topic_schema = schema["properties"]["topicos_irregularidades"]["items"]
    assert "paragrafos" in topic_schema["required"]


def test_rag_queries_are_concise_and_built_from_irregularities_and_norms():
    analysis = {
        "irregularidades": [
            {
                "id": "18.1",
                "apontamento": "Classificação de receita intraorçamentária previdenciária",
                "normas_mencionadas": [{"descricao": "Portaria MPS 402/2008"}],
                "hipoteses_juridicas": ["Aplicação do princípio da insignificância"],
            },
            {
                "id": "18.2",
                "apontamento": "Certificação dos Conselhos Previdenciários",
                "normas_mencionadas": [{"descricao": "Lei municipal 123/2020"}],
                "hipoteses_juridicas": ["Incidência das circunstâncias práticas"],
            },
            {
                "id": "18.3",
                "apontamento": "Composição dos Conselhos Municipal Previdenciário e Fiscal",
                "normas_mencionadas": [{"descricao": "Lei municipal 456/2021"}],
                "hipoteses_juridicas": ["Distinção entre obrigação e recomendação"],
            },
            {
                "id": "18.4",
                "apontamento": "Evolução da dívida e parcelamentos previdenciários",
                "normas_mencionadas": [],
                "hipoteses_juridicas": ["Razoabilidade"],
            },
        ]
    }

    plan = _rag_query_plan(analysis)
    queries = plan["consultas"]

    assert len(queries) == 4
    assert plan["materias_nao_pesquisadas"] == []
    assert [item["irregularidade_id"] for item in queries] == [
        "18.1", "18.2", "18.3", "18.4"
    ]
    assert [item["consulta_principal"] for item in queries] == [
        "receita intraorçamentária",
        "certificação conselho previdenciário",
        "composição conselho previdenciário",
        "parcelamento previdenciário",
    ]
    assert all(len(item["consulta_principal"].split()) <= 3 for item in queries)
    assert all(item["consulta_alternativa"] for item in queries)
    assert all(
        generic not in _plain_query
        for _plain_query in [item["consulta_principal"].casefold() for item in queries]
        for generic in (
            "aplicação princípio",
            "incidência circunstâncias",
            "distinção entre",
        )
    )


def test_rag_filters_council_results_by_the_text_of_the_recovered_passage():
    target, history, other, rows = _scenario()
    opinion = next(
        row
        for row in rows
        if row["referencia_id"] == history["id"] and row["papel"] == "parecer"
    )

    def analyze(context):
        analysis = _analysis_payload(context)
        irregularity = analysis["irregularidades"][0]
        irregularity["apontamento"] = (
            "Irregularidade previdenciária na página original: composição dos "
            "Conselhos Previdenciário e Fiscal"
        )
        irregularity["hipoteses_juridicas"] = ["Distinção entre obrigação e recomendação"]
        irregularity["normas_mencionadas"] = []
        return _analysis_result(context, analise=analysis)

    def search(_query, **_filters):
        return {
            "resultados": [
                _search_result(
                    history,
                    opinion,
                    context="Aplicação do princípio da razoabilidade em despesas com combustível.",
                ),
                _search_result(
                    history,
                    opinion,
                    context="Pagamento de diárias e comprovação do deslocamento.",
                    page=2,
                ),
                _search_result(
                    history,
                    opinion,
                    context="A composição do conselho previdenciário exige membros representativos.",
                    page=3,
                ),
            ]
        }

    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze, search=search
    )
    recovery = service.analyze(target["id"])["recuperacao_juridica"]

    assert [item["pagina"] for item in recovery["trechos"]] == [3]
    assert recovery["descartes"]["sem_pertinencia"] == 2
    assert recovery["consultas"][0]["descartados"]["sem_pertinencia"] == 2


def test_rag_selects_only_parcelment_passages_from_simulated_precedents():
    target, _history, other, rows = _scenario()
    rows = [row for row in rows if row["referencia_id"] == target["id"]]
    jacarau = _record("jacarau", "TC 03101/26", "0310126")
    bananeiras = _record("bananeiras", "TC 03102/26", "0310226")
    jacarau_opinion = _row(jacarau, "parecer", "Parecer histórico de Jacaraú.")
    bananeiras_opinion = _row(bananeiras, "parecer", "Parecer histórico de Bananeiras.")
    rows.extend((jacarau_opinion, bananeiras_opinion))

    def analyze(context):
        analysis = _analysis_payload(context)
        irregularity = analysis["irregularidades"][0]
        irregularity["apontamento"] = (
            "Irregularidade previdenciária na página original: evolução da dívida "
            "e parcelamentos previdenciários"
        )
        irregularity["hipoteses_juridicas"] = ["Proporcionalidade"]
        irregularity["normas_mencionadas"] = []
        return _analysis_result(context, analise=analysis)

    def search(_query, **_filters):
        return {
            "resultados": [
                _search_result(
                    jacarau,
                    jacarau_opinion,
                    context="Jacaraú celebrou parcelamento da dívida previdenciária.",
                ),
                _search_result(
                    bananeiras,
                    bananeiras_opinion,
                    context="Bananeiras apresentou acordo de parcelamento dos débitos.",
                ),
                _search_result(
                    jacarau,
                    jacarau_opinion,
                    context="A nota atuarial avaliou investimentos e a meta de rentabilidade.",
                    page=2,
                ),
            ]
        }

    service = MinutasGenerationService(
        Library([target, jacarau, bananeiras, other], rows),
        analyze=analyze,
        search=search,
    )
    recovery = service.analyze(target["id"])["recuperacao_juridica"]

    assert {item["referencia_id"] for item in recovery["trechos"]} == {
        "jacarau",
        "bananeiras",
    }
    assert len(recovery["trechos"]) == 2
    assert recovery["descartes"]["sem_pertinencia"] == 1


def test_rag_uses_one_safe_alternative_when_specific_query_has_no_result():
    target, history, other, rows = _scenario()
    opinion = next(
        row
        for row in rows
        if row["referencia_id"] == history["id"] and row["papel"] == "parecer"
    )
    calls = []

    def search(query, **_filters):
        calls.append(query)
        return {
            "resultados": (
                [
                    _search_result(
                        history,
                        opinion,
                        context="Houve recolhimento previdenciário comprovado.",
                    )
                ]
                if query == "recolhimento previdenciário"
                else []
            )
        }

    def analyze(context):
        analysis = _analysis_payload(context)
        analysis["irregularidades"][0]["apontamento"] = (
            "Irregularidade previdenciária na página original: ausência de "
            "recolhimento de contribuições previdenciárias"
        )
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        search=search,
    )

    recovery = service.analyze(target["id"])["recuperacao_juridica"]

    assert calls == ["contribuição previdenciária", "recolhimento previdenciário"]
    assert recovery["estado"] == "CONCLUIDA"
    assert recovery["consultas_executadas"] == 2
    assert [item["tipo"] for item in recovery["consultas"][0]["tentativas"]] == [
        "principal", "alternativa"
    ]
    assert recovery["consultas"][0]["tentativas"][0]["resultados_retornados"] == 0
    assert recovery["consultas"][0]["tentativas"][1]["resultados_retornados"] == 1


def test_rag_balances_four_matters_and_limits_progressive_search_to_eight_calls():
    target, history, other, rows = _scenario()
    matters = (
        "irregularidade previdenciária contribuição recolhimento",
        "dívida ativa cobrança administrativa",
        "crédito adicional autorização legislativa",
        "regime próprio equilíbrio atuarial",
        "execução fiscal prescrição intercorrente",
        "responsabilidade fiscal disponibilidade caixa",
    )
    calls = []

    def analyze(context):
        analysis = _analysis_payload(context, count=len(matters))
        for irregularity, matter in zip(analysis["irregularidades"], matters):
            irregularity["apontamento"] = matter
            irregularity["hipoteses_juridicas"] = [matter]
            irregularity["normas_mencionadas"] = []
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        search=lambda query, **_filters: calls.append(query) or {"resultados": []},
    )

    recovery = service.analyze(target["id"])["recuperacao_juridica"]

    assert len(recovery["consultas"]) == 4
    assert len(recovery["materias_nao_pesquisadas"]) == 2
    assert recovery["consultas_executadas"] == 8
    assert len(calls) == 8
    assert all(len(query.split()) <= 3 for query in calls)


def test_real_recovery_flow_covers_four_matters_and_accepts_six_new_precedents():
    target, history, other, rows = _scenario()
    other_historical = [
        _record("history-third", "TC 01499/26", "0149926"),
        _record("history-fourth", "TC 02650/25", "0265025"),
    ]
    rows.extend(
        _row(record, "parecer", f"Parecer histórico {record['numero_processo']}.")
        for record in other_historical
    )
    new_records = [
        _record(
            f"new-precedent-{index}",
            f"TC {30000 + index:05d}/26",
            f"{30000 + index:05d}26",
        )
        for index in range(1, 7)
    ]
    new_opinions = [
        _row(
            record,
            "parecer",
            f"Fundamentação previdenciária histórica {index}.",
            file_hash=f"new-opinion-{index}",
        )
        for index, record in enumerate(new_records, 1)
    ]
    rows.extend(new_opinions)
    expected_queries = {
        "contribuição previdenciária",
        "FUNDEB",
        "licitação",
        "parcelamento previdenciário",
    }
    calls = []

    def analyze(context):
        analysis = _analysis_payload(context, count=4)
        configurations = (
            (
                "18.1",
                "Irregularidade previdenciária na página original: contribuições",
                ["Responsabilidade por contribuições previdenciárias"],
            ),
            ("18.2", "Aplicação de recursos do FUNDEB", ["Vinculação do FUNDEB"]),
            ("18.3", "Procedimento de licitação", ["Controle licitatório"]),
            (
                "18.4",
                "Ausência de comprovação documental",
                ["Parcelamento de débitos previdenciários"],
            ),
        )
        for irregularity, (identifier, matter, hypotheses) in zip(
            analysis["irregularidades"], configurations
        ):
            irregularity["id"] = identifier
            irregularity["apontamento"] = matter
            irregularity["hipoteses_juridicas"] = hypotheses
            irregularity["normas_mencionadas"] = []
        return _analysis_result(context, analise=analysis)

    def search(query, **_filters):
        calls.append(query)
        if query not in expected_queries:
            return {"resultados": []}
        index = sorted(expected_queries).index(query)
        return {
            "resultados": [
                _search_result(
                    new_records[index],
                    new_opinions[index],
                    context=f"Trecho pertinente a {query}.",
                )
            ]
        }

    eligible_records = [target, history, *other_historical, *new_records]
    assert len(eligible_records) == 10
    service = MinutasGenerationService(
        Library([*eligible_records, other], rows),
        analyze=analyze,
        search=search,
    )

    analyzed = service.analyze(target["id"])
    recovery = analyzed["recuperacao_juridica"]

    assert analyzed["rag_recovery_version"] == RAG_RECOVERY_VERSION
    assert recovery["versao"] == RAG_RECOVERY_VERSION
    assert {item["irregularidade_id"] for item in recovery["consultas"]} == {
        "18.1", "18.2", "18.3", "18.4"
    }
    assert set(calls) == expected_queries
    assert recovery["consultas_executadas"] == 4
    assert len(recovery["trechos"]) == 4
    assert all(item["referencia_id"].startswith("new-precedent-") for item in recovery["trechos"])
    assert all(item["referencia_id"] != target["id"] for item in recovery["trechos"])


def test_analysis_recovers_traced_legal_references_with_deduplication_and_limits():
    target, history, other, rows = _scenario()
    opinion = next(
        row
        for row in rows
        if row["referencia_id"] == history["id"] and row["papel"] == "parecer"
    )
    calls = []

    def search(query, **filters):
        calls.append((query, filters))
        return {
            "resultados": [
                _search_result(history, opinion, context="Fundamentação previdenciária comum."),
                _search_result(
                    history,
                    opinion,
                    context="  FUNDAMENTAÇÃO   previdenciária comum. ",
                    relevance=0.9,
                ),
                _search_result(
                    history,
                    opinion,
                    context="Segunda fundamentação previdenciária. " + "x" * 2000,
                    page=2,
                    relevance=0.8,
                ),
                _search_result(
                    history,
                    opinion,
                    context="Terceira fundamentação previdenciária do mesmo parecer.",
                    page=3,
                    relevance=0.7,
                ),
            ]
        }

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=lambda context: _analysis_result(
            context, analise=_analysis_payload(context, count=2)
        ),
        search=search,
    )

    analyzed = service.analyze(target["id"])
    recovery = analyzed["recuperacao_juridica"]

    assert recovery["estado"] == "CONCLUIDA"
    assert recovery["pareceres"] == 1
    assert len(recovery["trechos"]) == 2
    assert recovery["resultados_retornados"] > len(recovery["trechos"])
    assert recovery["descartes"]["duplicacao"] > 0
    assert all(item["pagina"] in {1, 2} for item in recovery["trechos"])
    assert all(len(item["trecho_literal"]) <= 1600 for item in recovery["trechos"])
    assert calls and len(calls) <= 3
    assert all(call[1]["exclude_reference_id"] == target["id"] for call in calls)
    assert all(target["parecer_path"] in call[1]["exclude_paths"] for call in calls)


def test_rag_excludes_text_equivalent_target_opinion_with_different_binary_hash():
    target, history, other, rows = _scenario()
    target_opinion = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "parecer"
    )
    historical_opinion = next(
        row
        for row in rows
        if row["referencia_id"] == history["id"] and row["papel"] == "parecer"
    )
    historical_opinion.update(
        arquivo_sha256="different-binary-hash",
        texto_integral="  segredo   do parecer-alvo  ",
    )

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        search=lambda *_args, **_kwargs: {
            "resultados": [_search_result(history, historical_opinion)]
        },
    )

    analyzed = service.analyze(target["id"])

    assert analyzed["recuperacao_juridica"]["estado"] == "SEM_RESULTADOS"
    assert analyzed["recuperacao_juridica"]["trechos"] == []
    assert "barreiras documentais" in (
        analyzed["recuperacao_juridica"]["motivo_ausencia"]
    )
    assert target_opinion["texto_integral"] not in json.dumps(
        analyzed, ensure_ascii=False
    )


def test_rag_rejects_search_results_from_another_prosecutor():
    target, history, other, rows = _scenario()
    other_opinion = next(
        row
        for row in rows
        if row["referencia_id"] == other["id"] and row["papel"] == "parecer"
    )
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        search=lambda *_args, **_kwargs: {
            "resultados": [_search_result(other, other_opinion)]
        },
    )

    analyzed = service.analyze(target["id"])

    assert analyzed["recuperacao_juridica"]["estado"] == "SEM_RESULTADOS"
    assert analyzed["recuperacao_juridica"]["trechos"] == []


def test_generation_can_compare_rag_enabled_and_disabled_without_changing_analysis():
    target, history, other, rows = _scenario()
    opinion = next(
        row
        for row in rows
        if row["referencia_id"] == history["id"] and row["papel"] == "parecer"
    )
    drafting_contexts = []

    def generate(context):
        drafting_contexts.append(context)
        return _draft_result(context)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=generate,
        search=lambda *_args, **_kwargs: {
            "resultados": [_search_result(history, opinion)]
        },
    )
    analyzed = service.analyze(target["id"])
    analysis_snapshot = json.dumps(analyzed["analise"], sort_keys=True)

    with_rag = _gerar(service, target["id"], analyzed, use_rag=True)
    without_rag = _gerar(service, target["id"], analyzed, use_rag=False)

    assert "referencias_juridicas_recuperadas" in drafting_contexts[0]
    assert drafting_contexts[0]["referencias_juridicas_recuperadas"]
    assert "referencias_juridicas_recuperadas" not in drafting_contexts[1]
    assert with_rag["referencias_juridicas_enviadas"] == 1
    assert without_rag["referencias_juridicas_enviadas"] == 0
    assert with_rag["rag_ativo"] is True
    assert without_rag["rag_ativo"] is False
    assert json.dumps(analyzed["analise"], sort_keys=True) == analysis_snapshot


def test_generation_continues_when_legal_search_finds_no_precedent():
    target, history, other, rows = _scenario()
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
        search=lambda *_args, **_kwargs: {"resultados": []},
    )

    analyzed = service.analyze(target["id"])
    result = _gerar(service, target["id"], analyzed, use_rag=True)

    assert analyzed["recuperacao_juridica"]["estado"] == "SEM_RESULTADOS"
    assert "lexical" in analyzed["recuperacao_juridica"]["motivo_ausencia"]
    assert result["referencias_juridicas_enviadas"] == 0
    assert result["texto"]


def test_stale_rag_state_is_refreshed_without_repeating_probative_analysis():
    target, history, other, rows = _scenario()
    analysis_calls = []
    search_calls = []

    def analyze(context):
        analysis_calls.append(context)
        return _analysis_result(context)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        search=lambda query, **_filters: search_calls.append(query)
        or {"resultados": []},
    )
    current = service.analyze(target["id"])
    stale = dict(current)
    stale.pop("rag_recovery_version")
    stale["recuperacao_juridica"] = {
        "estado": "SEM_RESULTADOS",
        "consultas": [
            {"consulta": "Classificação incorreta recursos provenientes contribuições servidores como"}
        ],
        "trechos": [],
    }
    calls_before_refresh = len(search_calls)

    refreshed = service.refresh_legal_recovery(target["id"], stale)

    assert len(analysis_calls) == 1
    assert len(search_calls) > calls_before_refresh
    assert refreshed["analise"] is stale["analise"]
    assert refreshed["rag_recovery_version"] == RAG_RECOVERY_VERSION
    assert refreshed["recuperacao_juridica"]["versao"] == RAG_RECOVERY_VERSION
    assert all(
        len(item["consulta_principal"].split()) <= 3
        for item in refreshed["recuperacao_juridica"]["consultas"]
    )


def test_complete_generation_uses_one_analysis_and_one_drafting_call():
    target, history, other, rows = _scenario()
    analysis_calls = []
    drafting_calls = []

    def analyze(context):
        analysis_calls.append(context)
        return _analysis_result(context)

    def generate(context):
        drafting_calls.append(context)
        return _draft_result(context)

    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze, generate=generate
    )
    analyzed = service.analyze(target["id"])
    result = _gerar(service, target["id"], analyzed)

    assert len(analysis_calls) == 1
    assert len(drafting_calls) == 1
    assert "referencias_historicas" not in analysis_calls[0]
    assert drafting_calls[0]["analise_estruturada"] == analyzed["analise"]
    assert "## Fundamentação" in result["texto"]
    assert result["modelo"] == "modelo-redacao"
    assert "contexto" not in result
    assert result["fontes_alvo"] == 2
    assert any("TC 01653/26" in source for source in result["fontes_utilizadas"])


def test_bradson_layers_reach_both_existing_calls_with_frei_martinho_evidence():
    from services.minutas_procuradores import exigir_procurador

    target, history, other, rows = _scenario()
    target.update(
        procuradora="Bradson Tibério Luna Camelo",
        procuradora_chave="bltc",
        numero_processo="TC 00001/24",
        processo_chave="0000124",
        exercicio=2024,
        subtipo="Prefeitura",
    )
    evidence = (
        "Transferências de emendas parlamentares: R$ 349.430,00. "
        "A defesa distingue receita efetivamente recebida de erro de classificação "
        "no Sagres e apresenta registros do FNS para conciliação. "
        "Diferença RGPS de R$ 14.078,60 e RPPS de R$ 23.413,57. "
        "Após revisão das bases de cálculo, foi indicado recolhimento de 99,05%."
    )
    audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    audit.update(
        procuradora=target["procuradora"],
        procuradora_chave="bltc",
        texto_integral=evidence,
        unidades_json=json.dumps([{"pagina": 1, "texto": evidence}], ensure_ascii=False),
    )
    defense = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "complementar"
    )
    defense.update(procuradora=target["procuradora"], procuradora_chave="bltc")
    target_opinion = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "parecer"
    )
    target_opinion.update(procuradora=target["procuradora"], procuradora_chave="bltc")
    history.update(procuradora=target["procuradora"], procuradora_chave="bltc")
    for row in rows:
        if row["referencia_id"] == history["id"]:
            row.update(procuradora=target["procuradora"], procuradora_chave="bltc")

    captured = []
    library = Library([target, history, other], rows)
    library.procurador = exigir_procurador("BLTC")
    service = MinutasGenerationService(
        library,
        analyze=lambda context: captured.append(("analise", context)),
        generate=lambda context: captured.append(("geracao", context)),
    )

    from services.minutas_orientacao import aplicar_bloco

    analysis_context = service.prepare_analysis_context(target["id"])["contexto"]
    drafting_context = service.prepare_context(target["id"])["contexto"]
    service._analyze(analysis_context)
    with pytest.raises(MinutasGenerationError, match="Informe a orientação jurídica"):
        service._generate(drafting_context)
    aplicar_bloco(drafting_context, ORIENTACAO_REGRESSAO)
    service._generate(drafting_context)

    assert [kind for kind, _context in captured] == ["analise", "geracao"]
    for _kind, context in captured:
        assert "bradson_evidencia_v1" in context["camadas_prompt"]
        assert "conclusão predeterminada" in context["camadas_prompt"]
    available = json.dumps(analysis_context, ensure_ascii=False)
    for value in ("349.430,00", "14.078,60", "23.413,57", "99,05%", "Sagres", "FNS"):
        assert value in available
    for value in ("349.430,00", "14.078,60", "23.413,57", "99,05%"):
        assert all(value not in context["camadas_prompt"] for _kind, context in captured)
    assert "aprovação das contas" not in available.casefold()


def test_bradson_legal_guidance_is_generation_only_and_can_be_replaced():
    from services.minutas_procuradores import exigir_procurador

    target, history, other, rows = _scenario()
    for record in (target, history):
        record.update(
            procuradora="Bradson Tibério Luna Camelo",
            procuradora_chave="bltc",
        )
    for row in rows:
        if row["referencia_id"] in {target["id"], history["id"]}:
            row.update(
                procuradora="Bradson Tibério Luna Camelo",
                procuradora_chave="bltc",
            )

    analysis_calls = []
    drafting_calls = []
    library = Library([target, history, other], rows)
    library.procurador = exigir_procurador("BLTC")
    service = MinutasGenerationService(
        library,
        analyze=lambda context: analysis_calls.append(context) or _analysis_result(context),
        generate=lambda context: drafting_calls.append(context) or _draft_result(context),
    )

    analyzed = service.analyze(target["id"])
    original_analysis = deepcopy(analyzed)
    favorable = "Priorizar o acolhimento da defesa quando a conciliação for comprovada."
    contrary = "Priorizar a manutenção do apontamento se a prova documental for insuficiente."

    _gerar(service, target["id"], analyzed, orientacao_juridica=favorable)
    _gerar(service, target["id"], analyzed, orientacao_juridica=contrary)
    with pytest.raises(MinutasGenerationError, match="Informe a orientação jurídica"):
        service.generate(target["id"], analyzed, orientacao_juridica="")

    assert len(analysis_calls) == 1
    assert len(drafting_calls) == 2
    assert "orientacao_juridica_usuario" not in analysis_calls[0]
    assert analyzed == original_analysis
    assert drafting_calls[0]["orientacao_juridica_usuario"]["texto"] == favorable
    assert drafting_calls[1]["orientacao_juridica_usuario"]["texto"] == contrary
    assert "conflito relevante" in drafting_calls[0]["camadas_prompt"]
    assert "conclusão predeterminada" in drafting_calls[0]["camadas_prompt"]
    assert "outro Procurador" in drafting_calls[0]["camadas_prompt"]


def test_legal_guidance_reaches_every_prosecutor_and_rejects_foreign_context():
    target, history, other, rows = _scenario()
    drafting = []
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=lambda context: drafting.append(context) or _draft_result(context),
    )
    analyzed = service.analyze(target["id"])
    texto = "Opinar pela aprovação das contas com ressalvas."

    _gerar(service, target["id"], analyzed, orientacao_juridica=texto)

    assert "Orientação do Assessor" in drafting[0]["camadas_prompt"]
    assert "bradson_evidencia_v1" not in drafting[0]["camadas_prompt"]
    assert drafting[0]["orientacao_juridica_usuario"]["papel"].startswith("diretriz")
    assert drafting[0]["orientacao_juridica_usuario"]["texto"] == texto
    with pytest.raises(MinutasGenerationError, match="não corresponde ao contexto"):
        service.generate(
            target["id"],
            analyzed,
            orientacao_juridica=texto,
            contexto_orientacao="contexto-de-outro-processo",
        )


def test_generation_revalidates_authorization_and_source_revision():
    target, history, other, rows = _scenario()
    library = Library([target, history, other], rows)
    drafting_calls = []
    service = MinutasGenerationService(
        library,
        analyze=_analysis_result,
        generate=lambda context: drafting_calls.append(context),
    )
    analyzed = service.analyze(target["id"])

    library.authorized = False
    with pytest.raises(MinutasAccessDenied):
        _gerar(service, target["id"], analyzed)
    with pytest.raises(MinutasAccessDenied):
        service.pilot_diagnostics(target["id"], analyzed)
    with pytest.raises(MinutasAccessDenied):
        service.refresh_legal_recovery(target["id"], analyzed)
    library.authorized = True

    mismatched = dict(analyzed)
    mismatched["processo_alvo_id"] = history["id"]
    assert service.pilot_diagnostics(target["id"], mismatched) == {
        "disponivel": False
    }
    with pytest.raises(MinutasGenerationError, match="não pertence"):
        _gerar(service, target["id"], mismatched)

    wrong_prosecutor = dict(analyzed, procuradora_chave="outra-procuradora")
    with pytest.raises(MinutasGenerationError, match="não pertence"):
        _gerar(service, target["id"], wrong_prosecutor)
    assert drafting_calls == []

    rows[0]["atualizado_em"] = "2026-10-13T11:00:00+00:00"
    assert service.pilot_diagnostics(target["id"], analyzed) == {
        "disponivel": False
    }
    with pytest.raises(MinutasGenerationError, match="fontes foram alteradas"):
        _gerar(service, target["id"], analyzed)
    assert drafting_calls == []


def test_context_limit_fails_without_cutting_target_or_calling_ai(monkeypatch):
    from services import minutas_generation

    target, history, other, rows = _scenario()
    calls = []
    monkeypatch.setattr(minutas_generation, "MAX_CONTEXT_BYTES", 100)
    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=calls.append
    )

    with pytest.raises(MinutasGenerationError, match="essenciais"):
        service.analyze(target["id"])

    assert calls == []


def test_context_budget_reserves_analysis_and_omits_optional_history(monkeypatch):
    from services import minutas_generation

    target, history, other, rows = _scenario()
    service = MinutasGenerationService(Library([target, history, other], rows))
    analysis_context = service.prepare_analysis_context(target["id"])["contexto"]
    analysis = _analysis_payload(analysis_context)
    without_analysis = service.prepare_context(target["id"])
    with_analysis = service.prepare_context(target["id"], analysis)
    essential_with_analysis = {
        **with_analysis["contexto"],
        "referencias_historicas": [],
    }
    limit = max(
        without_analysis["contexto_bytes"],
        minutas_generation._serialized_size(essential_with_analysis),
    )
    assert with_analysis["contexto_bytes"] > limit
    monkeypatch.setattr(minutas_generation, "MAX_CONTEXT_BYTES", limit)

    plain_limited = service.prepare_context(target["id"])
    analyzed_limited = service.prepare_context(target["id"], analysis)

    assert plain_limited["referencias_historicas"] == 1
    assert analyzed_limited["referencias_historicas"] == 0
    assert analyzed_limited["contexto"]["analise_estruturada"] == analysis
    assert analyzed_limited["contexto_bytes"] <= limit
    assert analyzed_limited["omitidas_por_limite"] == [
        f"Parecer histórico {history['numero_processo']}"
    ]


def test_missing_processed_audit_and_unauthorized_access_fail_before_ai():
    target, history, other, rows = _scenario()
    calls = []
    without_target = [row for row in rows if row["referencia_id"] != target["id"]]
    service = MinutasGenerationService(
        Library([target, history, other], without_target), analyze=calls.append
    )
    with pytest.raises(MinutasGenerationError, match="extração pendente"):
        service.analyze(target["id"])

    denied = MinutasGenerationService(
        Library([target, history], rows, authorized=False), analyze=calls.append
    )
    with pytest.raises(MinutasAccessDenied):
        denied.analyze(target["id"])
    assert calls == []


@pytest.mark.parametrize(
    ("status", "version", "message"),
    (
        ("ATENCAO", EXTRACTOR_VERSION, "necessita atenção"),
        ("PROCESSADO", "extrator-antigo", "desatualizada"),
    ),
)
def test_invalid_or_outdated_instruction_blocks_generation(status, version, message):
    target, history, other, rows = _scenario()
    target_audit = next(
        row
        for row in rows
        if row["referencia_id"] == target["id"] and row["papel"] == "auditoria"
    )
    target_audit.update(status=status, extrator_versao=version)
    calls = []
    service = MinutasGenerationService(
        Library([target, history, other], rows), analyze=calls.append
    )

    with pytest.raises(MinutasGenerationError, match=message):
        service.analyze(target["id"])

    assert calls == []


def test_shared_gemini_request_uses_models_limits_and_usage(monkeypatch):
    seen = {}
    raw = json.dumps(
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [{"text": json.dumps(_draft_structure())}]
                    },
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 20,
                "candidatesTokenCount": 8,
                "totalTokenCount": 28,
            },
        }
    ).encode()

    def execute(builder, label, operacao, **_kwargs):
        request = builder("test-key", "modelo-em-uso")
        seen.update(
            payload=json.loads(request.data.decode()), label=label, operacao=operacao
        )
        return raw, "modelo-em-uso"

    monkeypatch.setattr(ai_service, "_executar", execute)
    result = ai_service.gerar_minuta_parecer({"fontes": ["conteúdo"]})

    assert seen["operacao"] == "minutas_geracao"
    assert seen["payload"]["generationConfig"]["maxOutputTokens"] == 8192
    sent = seen["payload"]["contents"][0]["parts"][0]["text"]
    assert ai_service.PROMPT_MINUTA_PARECER in sent
    assert "conteúdo" in sent
    assert result["modelo"] == "modelo-em-uso"
    assert result["estrutura"]["conclusao_pedidos"]
    assert result["tokens_total"] == 28


def test_analysis_request_uses_structured_schema_and_minutas_models(monkeypatch):
    seen = {}
    context = {
        "documentos_processo_alvo": [
            {
                "fonte_id": "fonte-auditoria",
                "conteudo": [{"pagina": 7, "texto": "Apontamento documentado."}],
            }
        ]
    }
    raw = json.dumps(
        {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {
                        "parts": [{"text": json.dumps(_analysis_payload(context))}]
                    },
                }
            ],
            "usageMetadata": {"totalTokenCount": 42},
        }
    ).encode()

    def execute(builder, label, operacao, **kwargs):
        request = builder("test-key", kwargs["primary_model"])
        seen.update(
            payload=json.loads(request.data.decode()),
            label=label,
            operacao=operacao,
            kwargs=kwargs,
        )
        return raw, kwargs["primary_model"]

    monkeypatch.setattr(
        ai_service, "_minutas_models", lambda: ("modelo-minutas", "reserva-minutas")
    )
    monkeypatch.setattr(ai_service, "_executar", execute)
    result = ai_service.analisar_fundamentos_minuta(context)

    assert seen["operacao"] == "minutas_analise"
    assert seen["kwargs"] == {
        "primary_model": "modelo-minutas",
        "fallback_model": "reserva-minutas",
    }
    config = seen["payload"]["generationConfig"]
    assert config["maxOutputTokens"] == 8192
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["required"] == [
        "resumo_processual",
        "irregularidades",
        "sem_irregularidades_identificadas",
        "pontos_atencao_gerais",
    ]
    assert result["analise"]["irregularidades"][0]["avaliacao_auditoria"][
        "tratamento_defesa"
    ] == "REJEITADA"
    assert result["modelo"] == "modelo-minutas"
    assert result["tokens_total"] == 42


@pytest.mark.parametrize(
    ("payload", "message"),
    (
        (
            {
                "candidates": [
                    {
                        "finishReason": "MAX_TOKENS",
                        "content": {"parts": [{"text": "Minuta parcial"}]},
                    }
                ]
            },
            "incompleta",
        ),
        (
            {"candidates": []},
            "não retornou conteúdo",
        ),
        (
            {"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}},
            "bloqueada",
        ),
        (
            {
                "candidates": [
                    {
                        "finishReason": "SAFETY",
                        "content": {"parts": [{"text": "Texto bloqueado"}]},
                    }
                ]
            },
            "filtros de segurança",
        ),
    ),
)
def test_minuta_rejects_incomplete_empty_or_blocked_response(
    monkeypatch, payload, message
):
    monkeypatch.setattr(
        ai_service,
        "_executar",
        lambda *_args, **_kwargs: (json.dumps(payload).encode(), "modelo-em-uso"),
    )

    with pytest.raises(ai_service.GeminiErro, match=message):
        ai_service.gerar_minuta_parecer({"fontes": ["conteúdo"]})


def test_generation_propagates_controlled_gemini_failure():
    target, history, other, rows = _scenario()

    def unavailable(_context):
        raise ai_service.GeminiErro("O serviço de IA está indisponível no momento.")

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=unavailable,
    )
    analyzed = service.analyze(target["id"])

    with pytest.raises(ai_service.GeminiErro, match="indisponível"):
        _gerar(service, target["id"], analyzed)


def test_ui_calls_generation_only_on_click_and_keeps_result_on_rerun(monkeypatch):
    from services import minutas_ia_ui

    target = _record(TARGET_ID, "TC 01653/26", HISTORICAL_TEST_PROCESS_KEY)
    analysis_calls = []
    generation_calls = []
    refresh_calls = []
    export_calls = []
    diagnostic_calls = []
    revision = {"value": "revision-1"}
    refresh_failure = {"value": False}
    summary_calls = []

    class Generator:
        def __init__(self, _library):
            pass

        def targets(self):
            return [target]

        def source_summary(self, identifier):
            summary_calls.append(identifier)
            return {
                "fontes_alvo_previstas": 1,
                "referencias_historicas_disponiveis": 3,
                "issues": [],
                "fontes_versao": revision["value"],
            }

        def analyze(self, identifier):
            analysis_calls.append(identifier)
            return {
                "analise": {
                    "resumo_processual": "Resumo para conferência.",
                    "irregularidades": [],
                    "pontos_atencao_gerais": [],
                },
                "modelo": "modelo-analise",
                "duracao_ms": 50,
                "tokens_total": 20,
                "suficiente_para_geracao": True,
                "pendencias_conferencia": [],
                "fontes_versao": revision["value"],
                "rag_recovery_version": RAG_RECOVERY_VERSION,
                "evidence_correction_version": EVIDENCE_CORRECTION_VERSION,
                "validation_policy_version": VALIDATION_POLICY_VERSION,
                "recuperacao_juridica": {
                    "versao": RAG_RECOVERY_VERSION,
                    "estado": "SEM_RESULTADOS",
                    "consultas": [],
                    "trechos": [],
                },
            }

        def pilot_diagnostics(self, _identifier, _analyzed):
            diagnostic_calls.append(_identifier)
            return {"disponivel": False}

        def refresh_legal_recovery(self, identifier, analyzed):
            refresh_calls.append(identifier)
            if refresh_failure["value"]:
                raise RuntimeError("falha RAG sintética")
            refreshed = dict(analyzed)
            refreshed["rag_recovery_version"] = RAG_RECOVERY_VERSION
            refreshed["recuperacao_juridica"] = {
                "versao": RAG_RECOVERY_VERSION,
                "estado": "SEM_RESULTADOS",
                "consultas": [],
                "trechos": [],
            }
            return refreshed

        def generate(self, identifier, analyzed, use_rag=True, **_kwargs):
            assert analyzed["fontes_versao"] == revision["value"]
            generation_calls.append((identifier, use_rag))
            return {
                "texto": "# MINUTA\n\nConteúdo para revisão.",
                "modelo": "modelo-teste",
                "duracao_ms": 100,
                "contexto_bytes": 1000,
                "fontes_alvo": 1,
                "referencias_historicas": 3,
                "tokens_total": None,
                "fontes_versao": revision["value"],
                "fontes_utilizadas": ["TC 01653/26 — Relatório da Auditoria"],
                "omitidas_por_limite": [],
                "advertencias_qualidade": [
                    "Divergência jurídica sujeita à revisão humana."
                ],
                "advertencias_documentais": [
                    {
                        "irregularidade": "IRR-1",
                        "categoria": "Fato documentado",
                        "documento": "auditoria.pdf",
                        "pagina": 4,
                        "natureza": "trecho exige conferência",
                    }
                ],
            }

    class WordExporter:
        def __init__(self, _library, *, source_summary=None):
            pass

        def export(self, identifier, analyzed, generated):
            export_calls.append(identifier)
            assert analyzed["fontes_versao"] == revision["value"]
            assert generated["fontes_versao"] == revision["value"]
            return type(
                "Word",
                (),
                {
                    "content": b"PK-docx",
                    "name": "MINUTA_PARECER_TC_01653_26.docx",
                    "mime": (
                        "application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.document"
                    ),
                },
            )()

    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args: object())
    monkeypatch.setattr(minutas_ia_ui, "MinutasGenerationService", Generator)
    monkeypatch.setattr(minutas_ia_ui, "MinutasDocxService", WordExporter)
    _UI_HOLD.clear()

    def page():
        from services.minutas_ia_ui import _historical_generator_panel

        _historical_generator_panel(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    assert analysis_calls == []
    assert generation_calls == []
    assert summary_calls == [TARGET_ID]
    assert diagnostic_calls == []

    app.button(key=f"minutas_analysis_submit_{TARGET_ID}").click().run()
    assert analysis_calls == [TARGET_ID]
    assert generation_calls == []

    analysis_key = f"minutas_generation_analysis_{TARGET_ID}"
    stale = dict(app.session_state[analysis_key])
    stale.pop("rag_recovery_version")
    stale["recuperacao_juridica"] = {
        "estado": "SEM_RESULTADOS",
        "consultas": [{"consulta": "consulta longa antiga"}],
        "trechos": [],
    }
    app.session_state[analysis_key] = stale
    app.run()
    assert refresh_calls == []
    _confirmar_orientacao_ui(app)
    assert any(
        "navegar nesta tela não executa nova pesquisa" in item.value
        for item in app.caption
    )

    app.button(key=f"minutas_generation_submit_{TARGET_ID}").click().run()
    assert refresh_calls == [TARGET_ID]
    assert generation_calls == [(TARGET_ID, True)]
    assert export_calls == [TARGET_ID]
    assert any(
        item.label == "Baixar minuta em Word" for item in app.get("download_button")
    )
    assert any("Conteúdo para revisão" in item.value for item in app.markdown)
    assert any("Conteúdo para revisão" in item.value for item in app.code)
    assert any(
        "Geração concluída com pontos disponíveis" in item.value for item in app.info
    )
    assert any(
        item.label == "Conferência facultativa (2)" for item in app.expander
    )
    assert any(
        "auditoria.pdf, p. 4" in item.value for item in app.markdown
    )

    app.run()
    assert analysis_calls == [TARGET_ID]
    assert generation_calls == [(TARGET_ID, True)]
    assert refresh_calls == [TARGET_ID]
    assert export_calls == [TARGET_ID]
    assert diagnostic_calls == []
    assert any("Conteúdo para revisão" in item.value for item in app.markdown)

    stale_again = dict(app.session_state[analysis_key])
    stale_again.pop("rag_recovery_version")
    stale_again["recuperacao_juridica"] = {"estado": "SEM_RESULTADOS"}
    app.session_state[analysis_key] = stale_again
    refresh_failure["value"] = True
    app.run()
    app.button(key=f"minutas_generation_submit_{TARGET_ID}").click().run()
    assert refresh_calls == [TARGET_ID, TARGET_ID]
    assert generation_calls == [(TARGET_ID, True)]
    app.run()
    assert refresh_calls == [TARGET_ID, TARGET_ID]

    revision["value"] = "revision-2"
    app.run()
    assert analysis_calls == [TARGET_ID]
    assert generation_calls == [(TARGET_ID, True)]
    assert not any("Conteúdo para revisão" in item.value for item in app.markdown)
    assert any("fontes foram alteradas" in item.value for item in app.info)


def test_ui_bradson_legal_guidance_is_process_and_user_scoped(monkeypatch):
    from services import minutas_ia_ui
    from services.minutas_procuradores import exigir_procurador

    target = _record(TARGET_ID, "TC 01653/26", HISTORICAL_TEST_PROCESS_KEY)
    generation_calls = []
    analysis = {
        "analise": {
            "resumo_processual": "Resumo.",
            "irregularidades": [],
            "pontos_atencao_gerais": [],
        },
        "fontes_versao": "revision-guidance",
        "rag_recovery_version": RAG_RECOVERY_VERSION,
        "validation_policy_version": VALIDATION_POLICY_VERSION,
        "recuperacao_juridica": {
            "versao": RAG_RECOVERY_VERSION,
            "estado": "SEM_RESULTADOS",
            "trechos": [],
        },
    }

    class Generator:
        def __init__(self, _library):
            self.library = type(
                "Library", (), {"procurador": exigir_procurador("BLTC")}
            )()

        def targets(self):
            return [target]

        def source_summary(self, _identifier):
            return {
                "fontes_alvo_previstas": 1,
                "referencias_historicas_disponiveis": 1,
                "issues": [],
                "fontes_versao": "revision-guidance",
            }

        def analyze(self, _identifier):
            return deepcopy(analysis)

        def generate(self, identifier, analyzed, use_rag=True, orientacao_juridica=None, **_kwargs):
            generation_calls.append((identifier, analyzed, use_rag, orientacao_juridica))
            return {
                "texto": "# MINUTA\n\nConteúdo para revisão.",
                "fontes_versao": "revision-guidance",
                "advertencias_qualidade": [],
                "advertencias_documentais": [],
                "fontes_utilizadas": [],
                "omitidas_por_limite": [],
            }

    class WordExporter:
        def __init__(self, *_args, **_kwargs):
            pass

        def export(self, *_args):
            return type(
                "Word",
                (),
                {
                    "content": b"PK-docx",
                    "name": "minuta.docx",
                    "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                },
            )()

    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args: object())
    monkeypatch.setattr(minutas_ia_ui, "MinutasGenerationService", Generator)
    monkeypatch.setattr(minutas_ia_ui, "MinutasDocxService", WordExporter)

    def page():
        from services.minutas_ia_ui import _historical_generator_panel

        _historical_generator_panel(None, {"email": "assessor@mpc.pb.gov.br"})

    app = AppTest.from_function(page, default_timeout=30).run()
    app.button(key=f"minutas_analysis_submit_{TARGET_ID}").click().run()
    from services.minutas_orientacao import chave_estado_orientacao

    assert [item.label for item in app.text_area] == [
        "Orientação jurídica e conclusão pretendida"
    ]
    assert any(
        "Orientação do Assessor para a Minuta" in str(item.value)
        for item in app.markdown
    )
    orientation_key = app.text_area[0].key
    partes = (
        "assessor@mpc.pb.gov.br",
        target["procuradora_chave"],
        target["especialidade_id"],
        target["processo_chave"],
        analysis["fontes_versao"],
        "historico",
    )
    assert orientation_key == chave_estado_orientacao(*partes, sufixo="texto")
    assert orientation_key != chave_estado_orientacao(
        "outro@mpc.pb.gov.br", *partes[1:], sufixo="texto"
    )
    assert app.button(key=f"minutas_generation_submit_{TARGET_ID}").disabled is True

    app.text_area(key=orientation_key).set_value("Opinar pela aprovação com ressalvas.").run()
    assert app.button(key=f"minutas_generation_submit_{TARGET_ID}").disabled is True
    next(
        item for item in app.button
        if item.label == "Confirmar orientação e habilitar geração"
    ).click().run()
    app.button(key=f"minutas_generation_submit_{TARGET_ID}").click().run()
    app.text_area(key=orientation_key).set_value("Opinar pela rejeição da defesa.").run()
    assert app.button(key=f"minutas_generation_submit_{TARGET_ID}").disabled is True
    app.button(key=f"minutas_generation_submit_{TARGET_ID}").click().run()
    next(
        item for item in app.button
        if item.label == "Confirmar orientação e habilitar geração"
    ).click().run()
    app.button(key=f"minutas_generation_submit_{TARGET_ID}").click().run()

    assert [call[3] for call in generation_calls] == [
        "Opinar pela aprovação com ressalvas.",
        "Opinar pela rejeição da defesa.",
    ]
    assert app.session_state[f"minutas_generation_analysis_{TARGET_ID}"] == analysis
    app.run()
    assert app.session_state[orientation_key] == "Opinar pela rejeição da defesa."


def test_ui_shows_document_warning_and_keeps_drafting_enabled(monkeypatch):
    from services import minutas_ia_ui

    target = _record(TARGET_ID, "TC 01653/26", HISTORICAL_TEST_PROCESS_KEY)
    generation_calls = []
    correction_calls = []
    issue = {
        "irregularidade": "IRR-2",
        "categoria": "Fato documentado",
        "documento": "auditoria.pdf",
        "pagina": 4,
        "natureza": "trecho não localizado na página indicada",
        "bloqueante": False,
    }

    class Generator:
        def __init__(self, _library):
            pass

        def targets(self):
            return [target]

        def source_summary(self, _identifier):
            return {
                "fontes_alvo_previstas": 1,
                "referencias_historicas_disponiveis": 1,
                "issues": [],
                "fontes_versao": "revision-pending",
            }

        def analyze(self, _identifier):
            return {
                "analise": {
                    "resumo_processual": "Análise preservada para conferência.",
                    "irregularidades": [],
                    "pontos_atencao_gerais": [],
                },
                "modelo": "modelo-analise",
                "duracao_ms": 50,
                "tokens_total": 20,
                "suficiente_para_geracao": True,
                "pendencias_conferencia": [issue],
                "fontes_versao": "revision-pending",
                "rag_recovery_version": RAG_RECOVERY_VERSION,
                "evidence_correction_version": EVIDENCE_CORRECTION_VERSION,
                "validation_policy_version": VALIDATION_POLICY_VERSION,
                "recuperacao_juridica": {
                    "versao": RAG_RECOVERY_VERSION,
                    "estado": "SEM_RESULTADOS",
                    "consultas": [],
                    "trechos": [],
                },
            }

        def pilot_diagnostics(self, identifier, _analyzed):
            assert identifier == TARGET_ID
            return {
                "disponivel": True,
                "conferencia_documental": [
                    {
                        "irregularidade": "IRR-2",
                        "campo": "Fato documentado",
                        "documento": "auditoria.pdf",
                        "pagina": 4,
                        "trecho_suporte": "Citação literal não localizada.",
                        "texto_pagina": "Texto integral extraído da página quatro.",
                        "correspondencias_em_outras_paginas": [5],
                        "afirmacao_associada": (
                            "O recolhimento previdenciário não foi comprovado."
                        ),
                        "referencia_chave": "ref-pending",
                        "candidatos": [
                            {
                                "id": "candidate-safe",
                                "pagina": 5,
                                "trecho_literal": (
                                    "O recolhimento previdenciário não foi "
                                    "comprovado nos autos."
                                ),
                                "substituicao_segura": True,
                                "motivo_seguranca": (
                                    "elementos objetivos e qualificadores preservados"
                                ),
                            }
                        ],
                        "motivo": "trecho não localizado na página indicada",
                        "bloqueante": False,
                    },
                    {
                        "irregularidade": "IRR-4",
                        "campo": "Conclusão da Auditoria",
                        "documento": "analise-defesa.pdf",
                        "pagina": 9,
                        "pagina_original": 8,
                        "pagina_corrigida": 9,
                        "pagina_corrigida_automaticamente": True,
                        "trecho_suporte": "Conclusão literal localizada.",
                        "texto_pagina": "Conclusão literal localizada.",
                        "texto_pagina_original": "Texto da página oito.",
                        "correspondencias_em_outras_paginas": [],
                        "motivo": (
                            "correspondência literal normalizada única no mesmo "
                            "documento"
                        ),
                        "bloqueante": False,
                    },
                ],
                "recuperacao_juridica": {
                    "estado": "SEM_RESULTADOS",
                    "consultas": [
                        {
                            "consulta": "contribuição previdenciária recolhimento",
                            "consulta_principal": "contribuição previdenciária",
                            "consulta_alternativa": "recolhimento previdenciário",
                            "irregularidade_id": "IRR-2",
                            "materia": "Contribuições previdenciárias",
                            "resultados_retornados": 0,
                            "tentativas": [
                                {
                                    "tipo": "principal",
                                    "consulta": "contribuição previdenciária",
                                    "resultados_retornados": 0,
                                },
                                {
                                    "tipo": "alternativa",
                                    "consulta": "recolhimento previdenciário",
                                    "resultados_retornados": 0,
                                },
                            ],
                            "descartados": {
                                "barreira_documental": 0,
                                "duplicacao": 0,
                                "sem_pertinencia": 2,
                                "sem_trecho": 0,
                            },
                        }
                    ],
                    "trechos": [],
                    "resultados_retornados": 0,
                    "consultas_executadas": 2,
                    "motivo_ausencia": (
                        "A pesquisa lexical não retornou ocorrências para as "
                        "consultas construídas."
                    ),
                },
            }

        def correct_evidence_reference(
            self, identifier, analyzed, reference_key, candidate_id
        ):
            correction_calls.append(
                (identifier, reference_key, candidate_id)
            )
            corrected = dict(analyzed)
            corrected["suficiente_para_geracao"] = True
            corrected["pendencias_conferencia"] = []
            corrected["intervencoes_evidencias"] = [
                {"referencia_chave": reference_key, "candidato_id": candidate_id}
            ]
            return corrected

        def generate(self, *_args, **_kwargs):
            generation_calls.append(True)

    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args: object())
    monkeypatch.setattr(minutas_ia_ui, "MinutasGenerationService", Generator)
    _UI_HOLD.clear()

    def page():
        from services.minutas_ia_ui import _historical_generator_panel

        _historical_generator_panel(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    app.button(key=f"minutas_analysis_submit_{TARGET_ID}").click().run()
    app.toggle(key=f"minutas_generation_diagnostics_{TARGET_ID}").set_value(
        True
    ).run()

    warning_text = "\n".join(item.value for item in app.warning)
    review_text = "\n".join(
        [item.value for item in app.markdown]
        + [item.value for item in app.caption]
    )
    assert "pode ser gerada" in warning_text
    assert "IRR-2" in review_text
    assert "auditoria.pdf, p. 4" in review_text
    assert "trecho não localizado" in review_text
    diagnostic_text = "\n".join(
        [item.value for item in app.markdown]
        + [item.value for item in app.caption]
        + [item.value for item in app.info]
    )
    assert "Diagnóstico técnico do piloto" in [item.label for item in app.expander]
    assert "contribuição previdenciária" in diagnostic_text
    assert "recolhimento previdenciário" in diagnostic_text
    assert "Tentativa alternativa" in diagnostic_text
    assert "resultados retornados: 0" in diagnostic_text
    assert "sem pertinência temática: 2" in diagnostic_text
    assert any(
        "Citação literal não localizada." in item.value for item in app.code
    )
    assert any(
        "Texto integral extraído da página quatro." in item.value
        for item in app.code
    )
    assert any("página(s) 5" in item.value for item in app.warning)
    assert any(
        "Página corrigida automaticamente de 8 para 9" in item.value
        for item in app.success
    )
    assert app.button(key=f"minutas_generation_submit_{TARGET_ID}").disabled is True
    _confirmar_orientacao_ui(app)
    assert app.button(key=f"minutas_generation_submit_{TARGET_ID}").disabled is False
    assert generation_calls == []

    app.button(key=f"minutas_generation_submit_{TARGET_ID}").click().run()
    assert generation_calls == [True]

    app.button(key=f"minutas_evidence_review_{TARGET_ID}_ref-pending_open").click().run()
    assert any(
        "O recolhimento previdenciário não foi comprovado" in item.value
        for item in app.markdown
    )
    assert any(
        "não foi comprovado nos autos" in item.value for item in app.code
    )
    app.button(key=f"minutas_evidence_review_{TARGET_ID}_ref-pending_apply").click().run()
    assert correction_calls == [
        (TARGET_ID, "ref-pending", "candidate-safe")
    ]
    assert app.session_state[
        f"minutas_generation_analysis_{TARGET_ID}"
    ]["suficiente_para_geracao"] is True
    assert app.button(key=f"minutas_generation_submit_{TARGET_ID}").disabled is True
    _confirmar_orientacao_ui(app)
    assert app.button(key=f"minutas_generation_submit_{TARGET_ID}").disabled is False
