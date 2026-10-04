"""Editorial contract for institutional prose: presentation, prompt and UX."""

from streamlit.testing.v1 import AppTest

from document_generator.institutional_report_pdf import _flow_text
from reportlab.lib.styles import getSampleStyleSheet
from services import ai_service
from services.institutional_presentation import editorial_facts, format_report_period
from services.institutional_report_content import build_ai_context, validate_numbers


def _annual_snapshot():
    return {
        "metadados": {
            "tipo": "ANUAL",
            "ano": 2026,
            "trimestre": None,
            "descricao_periodo": "Acumulado de janeiro a 09/2026",
            "periodo_parcial": True,
        },
        "indicadores_gerais": {
            "distributed": 1760,
            "production": 1849,
            "opinions": 1320,
            "quotas": 529,
            "production_rate": 1849 / 1760 * 100,
            "median_days": 11.04,
        },
        "serie_mensal": [
            {
                "month": month,
                "summary": {"distributed": 100 + month, "production": 90 + month},
            }
            for month in range(1, 10)
        ],
        "composicao_producao": {
            "pareceres_percentual": 1320 / 1849 * 100,
            "cotas_percentual": 529 / 1849 * 100,
        },
        "por_procurador": [
            {
                "procurador": "Bradson",
                "distributed": 100,
                "production": 180,
                "opinions": 120,
                "quotas": 60,
                "median_days": 9.2,
            },
            {
                "procurador": "Elvira",
                "distributed": 300,
                "production": 260,
                "opinions": 200,
                "quotas": 60,
                "median_days": 14.1,
            },
        ],
        "faixas_permanencia": [
            {"faixa": "0–7 dias", "quantidade": 800},
            {"faixa": "8–15 dias", "quantidade": 400},
            {"faixa": "16–30 dias", "quantidade": 200},
            {"faixa": "31–60 dias", "quantidade": 100},
            {"faixa": "61–90 dias", "quantidade": 40},
            {"faixa": "Mais de 90 dias", "quantidade": 10},
        ],
        "comparacao_periodo_anterior": None,
        "cobertura_historica": {"meses_disponiveis": list(range(1, 10))},
    }


def _quarterly_snapshot():
    return {
        "metadados": {
            "tipo": "TRIMESTRAL",
            "ano": 2026,
            "trimestre": 3,
            "descricao_periodo": "Período 01/07/2026 a 30/09/2026",
            "periodo_parcial": False,
        },
        "indicadores_gerais": {
            "distributed": 545,
            "production": 594,
            "opinions": 424,
            "quotas": 170,
            "production_rate": 108.9908,
            "median_days": 7.8,
        },
        "serie_mensal": [
            {"month": 7, "summary": {"distributed": 200, "production": 210}},
            {"month": 8, "summary": {"distributed": 176, "production": 229}},
            {"month": 9, "summary": {"distributed": 169, "production": 155}},
        ],
        "composicao_producao": {
            "pareceres_percentual": 424 / 594 * 100,
            "cotas_percentual": 170 / 594 * 100,
        },
        "por_procurador": [{"procurador": "Sheyla", "production": 90, "opinions": 70}],
        "faixas_permanencia": [{"faixa": "0–7 dias", "quantidade": 300}],
        "comparacao_periodo_anterior": {"distributed": 500, "production": 520},
        "cobertura_historica": {"meses_disponiveis": [7, 8, 9]},
    }


def test_ai_context_uses_pareceres_and_hides_the_technical_key():
    import json

    from services.institutional_report_content import build_ai_context
    from services.relatorios_ui import _snapshot_procurador_rows

    snapshot = _annual_snapshot()
    frozen = json.dumps(snapshot["indicadores_gerais"])
    context = build_ai_context(snapshot)
    rendered = json.dumps(context, ensure_ascii=False)
    assert "opinions" not in rendered
    assert "opiniões" not in rendered.lower()
    assert context["fatos_para_redacao"]["indicadores"]["pareceres"]["raw"] == 1320
    assert context["dados_tecnicos"]["indicadores_gerais"]["pareceres"] == 1320
    assert "opinions" in frozen
    assert snapshot["indicadores_gerais"]["opinions"] == 1320
    prompt = ai_service.PROMPT_RELATORIO_INSTITUCIONAL.lower()
    assert "nunca escreva opiniões nem opinions" in prompt
    assert "pareceres" in prompt
    rows = _snapshot_procurador_rows(snapshot)
    assert rows[0]["Pareceres"] == 120
    assert "opinions" not in rows[0]
    assert "opiniões" not in json.dumps(rows, ensure_ascii=False).lower()


def test_old_period_wording_is_presented_without_rewriting_the_snapshot():
    snapshot = _annual_snapshot()
    frozen = snapshot["metadados"]["descricao_periodo"]
    assert format_report_period(snapshot) == "Acumulado de janeiro a setembro de 2026"
    assert snapshot["metadados"]["descricao_periodo"] == frozen
    context = build_ai_context(snapshot)
    assert context["periodo"] == "Acumulado de janeiro a setembro de 2026"
    assert "09/2026" not in context["periodo"]
    assert "09/2026" not in str(context["fatos_para_redacao"])


def test_annual_facts_use_display_values_and_do_not_list_every_month():
    facts = editorial_facts(_annual_snapshot())
    indicators = facts["indicadores"]
    assert indicators["distribuicoes"]["display"] == "1.760"
    assert indicators["producao"]["display"] == "1.849"
    assert indicators["pareceres"]["display"] == "1.320 (71,4%)"
    assert indicators["cotas"]["display"] == "529 (28,6%)"
    assert indicators["producao_distribuicoes"]["display"] == "105,1%"
    assert indicators["mediana"]["display"] == "11,0 dias"
    evolution = facts["evolucao"]
    assert evolution["nao_enumerar_todos_os_meses"] is True
    assert "meses" not in evolution
    assert "Bradson" not in str(facts)
    assert "não transcreva" in facts["procuradores"]["instrucao"].lower()
    assert facts["comparacao"]["disponivel"] is False
    assert "anual anterior" in facts["comparacao"]["texto"]


def test_quarterly_facts_keep_three_months_without_asking_for_a_dump():
    facts = editorial_facts(_quarterly_snapshot())
    months = [item["mes"] for item in facts["evolucao"]["meses"]]
    assert months == ["julho", "agosto", "setembro"]
    assert "não transforme" in facts["evolucao"]["instrucao"].lower()
    assert facts["indicadores"]["distribuicoes"]["display"] == "545"
    assert facts["indicadores"]["producao"]["display"] == "594"
    assert facts["indicadores"]["mediana"]["display"] == "7,8 dias"
    assert facts["comparacao"]["disponivel"] is True


def test_prompt_contract_forbids_transcription_and_efficiency_language():
    prompt = ai_service.PROMPT_RELATORIO_INSTITUCIONAL.lower()
    assert "não use estas expressões: taxa de produção" in prompt
    assert "taxa de eficiência" in prompt
    assert "índice de conclusão" in prompt
    assert "não enumere todos os meses" in prompt
    assert "não transcreva" in prompt
    assert "não invente pontos de atenção" in prompt
    assert "display" in prompt
    assert "não recalcule percentuais" in prompt
    context = build_ai_context(_annual_snapshot())
    assert context["tipo_relatorio"] == "ANUAL"
    assert context["fatos_para_redacao"]["procuradores"]["quantidade"] == 2
    assert "Bradson" not in str(context["fatos_para_redacao"])


def test_permanence_bands_and_rounded_displays_are_not_unknown():
    snapshot = _annual_snapshot()
    text = (
        "A mediana foi de 11,0 dias. As faixas 0–7, 8–15, 16–30, 31–60 e 61–90 "
        "reúnem os registros, com 71,4% em pareceres e 1.760 distribuições."
    )
    assert validate_numbers(text, snapshot) == []
    assert validate_numbers("Há 999 registros fora do snapshot.", snapshot) == ["999"]


def _suggestion_plan():
    return {
        "lacunas": [{"mensagem": "Há uma lacuna anual."}],
        "sugestoes": [
            {
                "tipo": "TRIMESTRAL",
                "ano": 2026,
                "trimestre": 1,
                "parcial": False,
                "titulo": "1º trimestre de 2026",
                "cobertura": "janeiro a março",
            },
            {
                "tipo": "ANUAL",
                "ano": 2026,
                "trimestre": None,
                "parcial": True,
                "titulo": "Ano de 2026",
                "cobertura": "janeiro a setembro",
            },
        ],
    }


def test_suggestions_follow_the_selected_report_type():
    def annual_page():
        from services.institutional_governance_ui import render_ready_periods
        from tests.test_institutional_report_editorial import _suggestion_plan

        render_ready_periods(
            _suggestion_plan(),
            administrator=True,
            selected_token="outro",
            tipo="ANUAL",
        )

    annual = AppTest.from_function(annual_page, default_timeout=30).run()
    visible = "\n".join(
        item.value for item in (*annual.markdown, *annual.caption, *annual.info)
    )
    assert "1º trimestre de 2026" not in visible
    assert "Relatório anual parcial disponível" in visible
    assert "Há uma lacuna anual." in visible

    def quarterly_page():
        from services.institutional_governance_ui import render_ready_periods
        from tests.test_institutional_report_editorial import _suggestion_plan

        render_ready_periods(
            _suggestion_plan(),
            administrator=True,
            selected_token="outro",
            tipo="TRIMESTRAL",
        )

    quarterly = AppTest.from_function(quarterly_page, default_timeout=30).run()
    visible = "\n".join(
        item.value
        for item in (*quarterly.markdown, *quarterly.caption, *quarterly.info)
    )
    assert "1º trimestre de 2026" in visible
    assert "Relatório anual parcial disponível" not in visible
    assert "Há uma lacuna anual." not in visible


def test_pdf_keeps_short_paragraphs_and_simple_bullets():
    styles = getSampleStyleSheet()
    flow = _flow_text(
        "O período concentrou o volume no meio do ano.\n\n"
        "Destaques do período:\n"
        "- Distribuições atingiram o maior volume em março.\n"
        "- A produção acompanhou o fluxo no encerramento.",
        styles["Normal"],
        styles["Normal"],
    )
    assert len(flow) == 4
    rendered = " ".join(item.getPlainText() for item in flow)
    assert "Destaques do período:" in rendered
    assert "• Distribuições atingiram o maior volume em março." in rendered
    assert "<" not in rendered
