import json
import re

import pytest
from streamlit.testing.v1 import AppTest

from services import ai_service
from services.minutas_generation import (
    HISTORICAL_TEST_PROCESS_KEY,
    MinutasGenerationError,
    MinutasGenerationService,
    _canonical_process_key,
    _clean_text,
    _continuous_word_sequence,
)
from services.minutas_extraction import EXTRACTOR_VERSION
from services.minutas_library import MinutasAccessDenied


TARGET_ID = "target-taperoa"
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

    def list_extractions_by_procuradora(self, key):
        self.queries.append(key)
        return list(self.rows)

    def list_extraction_inventory_by_procuradora(self, key):
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
    def __init__(self, records, rows, *, authorized=True):
        self.records = {record["id"]: record for record in records}
        self.repository = Repository(rows)
        self.storage = object()
        self.authorized = authorized

    def _authorize(self):
        if not self.authorized:
            raise MinutasAccessDenied("Acesso não autorizado a este módulo.")
        return "admin@test.local"

    def get(self, identifier):
        self._authorize()
        return self.records[identifier]

    def list(self):
        self._authorize()
        return list(self.records.values())


def _scenario():
    target = _record(TARGET_ID, "TC 01653/26", HISTORICAL_TEST_PROCESS_KEY)
    history = _record("history-rio", "TC 02720/25", "tc0272025", subtype="Prefeitura")
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

    assert service.targets() == [target]
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
def test_other_processes_are_strictly_rejected(value):
    assert _canonical_process_key(value) != HISTORICAL_TEST_PROCESS_KEY
    target = _record(TARGET_ID, value, re.sub(r"[^0-9a-z]", "", value.casefold()))
    service = MinutasGenerationService(Library([target], []))

    assert service.targets() == []
    with pytest.raises(MinutasGenerationError, match="somente o processo histórico"):
        service.prepare_context(target["id"])


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


def test_reference_selection_combines_subtype_and_analysis_theme():
    target, _history, _other, base_rows = _scenario()
    same_subtype = _record(
        "history-same-subtype",
        "TC 01001/25",
        "0100125",
        subtype=target["subtipo"],
    )
    same_theme = _record(
        "history-same-theme", "TC 01002/25", "0100225", subtype="Prefeitura"
    )
    unrelated = _record(
        "history-unrelated", "TC 01003/25", "0100325", subtype="Prefeitura"
    )
    rows = [*base_rows[:3]]
    rows.extend(
        (
            _row(same_subtype, "parecer", "Padrão de redação ministerial."),
            _row(same_theme, "parecer", "Padrão jurídico da Procuradora."),
            _row(
                same_theme,
                "auditoria",
                "Irregularidade documentada sobre contribuição previdenciária.",
            ),
            _row(unrelated, "parecer", "Outro padrão de redação."),
            _row(unrelated, "auditoria", "Aquisição de merenda por licitação."),
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
    assert prepared["relatorios_historicos"] == 1


def test_historical_reference_requires_current_processed_linked_extraction():
    target, outdated, other, rows = _scenario()
    valid = _record(
        "history-valid", "TC 01004/25", "0100425", subtype=target["subtipo"]
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
    assert result["suficiente_para_geracao"] is False
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
    assert result["suficiente_para_geracao"] is False
    assert result["pendencias_conferencia"][0] == {
        "irregularidade": "IRR-1",
        "categoria": "Fato documentado",
        "documento": "auditoria.pdf",
        "pagina": 1,
        "natureza": "trecho não localizado na página indicada",
        "bloqueante": True,
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
    assert result["suficiente_para_geracao"] is True


def test_analysis_marks_excerpt_copied_from_another_page():
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
        analysis["irregularidades"][0]["fatos_documentados"][0]["fontes"][0][
            "trecho_suporte"
        ] = second_page
        return _analysis_result(context, analise=analysis)

    result = MinutasGenerationService(
        Library([target, history, other], rows), analyze=analyze
    ).analyze(target["id"])

    assert result["suficiente_para_geracao"] is False
    assert result["pendencias_conferencia"][0]["pagina"] == 1
    assert result["pendencias_conferencia"][0]["natureza"] == (
        "trecho não localizado na página indicada"
    )


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
    assert result["suficiente_para_geracao"] is False
    assert any(
        item["natureza"] == "alegação defensiva classificada como fato"
        for item in result["pendencias_conferencia"]
    )


def test_multiple_irregularities_preserve_analysis_and_block_unsupported_drafting():
    target, history, other, rows = _scenario()
    drafting_calls = []

    def analyze(context):
        analysis = _analysis_payload(context, count=2)
        second_fact = analysis["irregularidades"][1]["fatos_documentados"][0]
        second_fact["fontes"][0]["trecho_suporte"] = "Fato inexistente nesta página."
        return _analysis_result(context, analise=analysis)

    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=analyze,
        generate=lambda context: drafting_calls.append(context),
    )
    result = service.analyze(target["id"])
    irregularities = result["analise"]["irregularidades"]

    assert irregularities[0]["fatos_documentados"][0]["estado_conferencia"] == (
        "VERIFICADA"
    )
    assert irregularities[1]["fatos_documentados"][0]["estado_conferencia"] == (
        "NAO_VERIFICADA"
    )
    assert result["suficiente_para_geracao"] is False
    with pytest.raises(
        MinutasGenerationError,
        match=r"IRR-2.*Fato documentado.*auditoria\.pdf, p\. 1.*não localizado",
    ):
        service.generate(target["id"], result)
    assert drafting_calls == []


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
    result = service.generate(target["id"], analyzed)

    warnings = "\n".join(result["advertencias_qualidade"])
    assert "Possível omissão" in warnings
    assert "R$ 9.999,99" in warnings
    assert "página sem correspondência" in warnings
    assert "inconsistência entre a ementa" in warnings
    assert result["estrutura"]["conclusao_pedidos"] == (
        "Opina-se pela regularidade das contas."
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

    result = service.generate(target["id"], service.analyze(target["id"]))

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

    result = service.generate(target["id"], service.analyze(target["id"]))

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

    result = service.generate(target["id"], service.analyze(target["id"]))

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

    result = service.generate(target["id"], service.analyze(target["id"]))

    assert any(
        "sem justificativa probatória textual verificável" in warning
        for warning in result["advertencias_qualidade"]
    )


def test_coherent_draft_renders_numbered_requests_and_closing():
    target, history, other, rows = _scenario()
    service = MinutasGenerationService(
        Library([target, history, other], rows),
        analyze=_analysis_result,
        generate=_draft_result,
    )

    result = service.generate(target["id"], service.analyze(target["id"]))

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

    result = service.generate(target["id"], service.analyze(target["id"]))

    request = result["estrutura"]["pedidos"][0]
    assert result["texto"].count(request) == 1
    assert "1. " + request in result["texto"]


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
    result = service.generate(target["id"], analyzed)

    assert len(analysis_calls) == 1
    assert len(drafting_calls) == 1
    assert "referencias_historicas" not in analysis_calls[0]
    assert drafting_calls[0]["analise_estruturada"] == analyzed["analise"]
    assert "## Fundamentação" in result["texto"]
    assert result["modelo"] == "modelo-redacao"
    assert "contexto" not in result
    assert result["fontes_alvo"] == 2
    assert any("TC 01653/26" in source for source in result["fontes_utilizadas"])


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
        service.generate(target["id"], analyzed)
    library.authorized = True

    rows[0]["atualizado_em"] = "2026-10-13T11:00:00+00:00"
    with pytest.raises(MinutasGenerationError, match="fontes foram alteradas"):
        service.generate(target["id"], analyzed)
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
        service.generate(target["id"], analyzed)


def test_ui_calls_generation_only_on_click_and_keeps_result_on_rerun(monkeypatch):
    from services import minutas_ia_ui

    target = _record(TARGET_ID, "TC 01653/26", HISTORICAL_TEST_PROCESS_KEY)
    analysis_calls = []
    generation_calls = []
    revision = {"value": "revision-1"}
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
            }

        def generate(self, identifier, analyzed):
            assert analyzed["fontes_versao"] == revision["value"]
            generation_calls.append(identifier)
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
            }

    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args: object())
    monkeypatch.setattr(minutas_ia_ui, "MinutasGenerationService", Generator)
    _UI_HOLD.clear()

    def page():
        from services.minutas_ia_ui import _generator_tab

        _generator_tab(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    assert analysis_calls == []
    assert generation_calls == []
    assert summary_calls == [TARGET_ID]

    app.button(key=f"minutas_analysis_submit_{TARGET_ID}").click().run()
    assert analysis_calls == [TARGET_ID]
    assert generation_calls == []

    app.button(key=f"minutas_generation_submit_{TARGET_ID}").click().run()
    assert generation_calls == [TARGET_ID]
    assert any("Conteúdo para revisão" in item.value for item in app.markdown)
    assert any("Conteúdo para revisão" in item.value for item in app.code)

    app.run()
    assert analysis_calls == [TARGET_ID]
    assert generation_calls == [TARGET_ID]
    assert any("Conteúdo para revisão" in item.value for item in app.markdown)

    revision["value"] = "revision-2"
    app.run()
    assert analysis_calls == [TARGET_ID]
    assert generation_calls == [TARGET_ID]
    assert not any("Conteúdo para revisão" in item.value for item in app.markdown)
    assert any("fontes foram alteradas" in item.value for item in app.info)


def test_ui_shows_document_issue_and_disables_drafting(monkeypatch):
    from services import minutas_ia_ui

    target = _record(TARGET_ID, "TC 01653/26", HISTORICAL_TEST_PROCESS_KEY)
    generation_calls = []
    issue = {
        "irregularidade": "IRR-2",
        "categoria": "Fato documentado",
        "documento": "auditoria.pdf",
        "pagina": 4,
        "natureza": "trecho não localizado na página indicada",
        "bloqueante": True,
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
                "suficiente_para_geracao": False,
                "pendencias_conferencia": [issue],
                "fontes_versao": "revision-pending",
            }

        def generate(self, *_args):
            generation_calls.append(True)

    monkeypatch.setattr(minutas_ia_ui, "_service", lambda *_args: object())
    monkeypatch.setattr(minutas_ia_ui, "MinutasGenerationService", Generator)
    _UI_HOLD.clear()

    def page():
        from services.minutas_ia_ui import _generator_tab

        _generator_tab(None, {})

    app = AppTest.from_function(page, default_timeout=30).run()
    app.button(key=f"minutas_analysis_submit_{TARGET_ID}").click().run()

    warning_text = "\n".join(item.value for item in app.warning)
    assert "IRR-2" in warning_text
    assert "auditoria.pdf, p. 4" in warning_text
    assert "trecho não localizado" in warning_text
    assert app.button(key=f"minutas_generation_submit_{TARGET_ID}").disabled is True
    assert generation_calls == []
