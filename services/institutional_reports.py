"""Snapshot construction and validation for institutional period reports."""

from datetime import date

from database.tramita_reports import TramitaReportsStore
from services.institutional_presentation import format_report_period
from services.tramita_reports import aging_band, turnaround_days


EMPTY_STRUCTURED_CONTENT = {
    "resumo_executivo": "",
    "analise_evolucao": "",
    "analise_composicao": "",
    "analise_permanencia": "",
    "analise_procuradores": "",
    "comparacao_periodo_anterior": "",
    "sintese_final": "",
}


def _period_bounds(tipo, ano, trimestre):
    if tipo == "TRIMESTRAL":
        if trimestre not in (1, 2, 3, 4):
            raise ValueError("Informe um trimestre entre 1 e 4.")
        initial_month = (trimestre - 1) * 3 + 1
        start = date(ano, initial_month, 1)
        end = date(
            ano + (trimestre == 4), 1 if trimestre == 4 else initial_month + 3, 1
        )
        return start, end
    if tipo == "ANUAL" and trimestre is None:
        return date(ano, 1, 1), date(ano + 1, 1, 1)
    raise ValueError("Identificação de período inválida.")


def _coverage(reports, year, months, expected_months):
    annual = reports.historical_coverage(year)
    available = sorted(month for month in months if month in expected_months)
    missing = [month for month in expected_months if month not in available]
    return {
        "meses_disponiveis": available,
        "meses_esperados": expected_months,
        "meses_ausentes": missing,
        "ultimo_mes_disponivel": annual["last_month"],
        "completo_no_periodo": not missing,
        "completo_ate_ultimo_mes": annual["complete_through_last_month"],
        "lacunas_no_ano": annual["missing_months"],
    }


def _composition(summary):
    production = summary["production"]
    return {
        "pareceres": summary["opinions"],
        "cotas": summary["quotas"],
        "pareceres_percentual": (
            summary["opinions"] / production * 100 if production else None
        ),
        "cotas_percentual": (
            summary["quotas"] / production * 100 if production else None
        ),
    }


def _methodology():
    return {
        "versao": "relatorios-indicadores-2026-01",
        "distribuicoes": "Entradas são atribuídas à data de realização.",
        "producao": "Produção considera somente saídas classificadas como Parecer ou Cota, atribuídas à data de devolução.",
        "permanencia": "A mediana considera o intervalo entre distribuição e devolução dos eventos produtivos.",
        "origem": "Somente eventos da referência histórica Tramita 2026 participam dos indicadores oficiais.",
    }


def _duration_bands(events):
    counts = {}
    for event in events:
        if event["tipo_movimentacao"] != "SAIDA" or event[
            "classificacao_producao"
        ] not in ("PARECER", "COTA"):
            continue
        band = aging_band(turnaround_days(event))
        if band:
            counts[band] = counts.get(band, 0) + 1
    return [
        {"faixa": band, "quantidade": quantity} for band, quantity in counts.items()
    ]


def build_report_snapshot(store, *, tipo, ano, trimestre=None):
    """Build a deterministic snapshot solely from the canonical report aggregates."""
    reports = TramitaReportsStore(store)
    start, end = _period_bounds(tipo, int(ano), trimestre)
    available_year = reports.months_for_year(ano)
    expected = (
        list(range((trimestre - 1) * 3 + 1, trimestre * 3 + 1))
        if tipo == "TRIMESTRAL"
        else list(range(1, (max(available_year) if available_year else 0) + 1))
    )
    months = [month for month in available_year if month in expected]
    coverage = _coverage(reports, ano, months, expected)
    period = reports.period_data(start.isoformat(), end.isoformat())
    report = period["report"]
    monthly = reports.monthly_reports(ano, months)
    quarterly = reports.quarterly_reports(ano)
    prior = None
    if tipo == "TRIMESTRAL":
        prior_year = ano if trimestre > 1 else ano - 1
        prior_quarter = trimestre - 1 if trimestre > 1 else 4
        prior_months = [
            month
            for month in reports.months_for_year(prior_year)
            if (prior_quarter - 1) * 3 < month <= prior_quarter * 3
        ]
        if prior_months:
            prior_start, prior_end = _period_bounds(
                "TRIMESTRAL", prior_year, prior_quarter
            )
            prior = reports.period_report(
                prior_start.isoformat(), prior_end.isoformat()
            )["summary"]
    elif reports.months_for_year(ano - 1):
        prior = reports.period_report(
            date(ano - 1, 1, 1).isoformat(), date(ano, 1, 1).isoformat()
        )["summary"]
    protocols = len({row["protocolo"] for row in period["events"] if row["protocolo"]})
    annual_partial = tipo == "ANUAL" and coverage["ultimo_mes_disponivel"] not in (
        None,
        12,
    )
    snapshot = {
        "metadados": {
            "tipo": tipo,
            "ano": int(ano),
            "trimestre": trimestre,
            "data_inicio": start.isoformat(),
            "data_fim": end.isoformat(),
            "periodo_parcial": annual_partial,
            "descricao_periodo": "",
        },
        "indicadores_gerais": report["summary"],
        "serie_mensal": monthly,
        "serie_trimestral": quarterly,
        "por_procurador": report["by_procurador"],
        "composicao_producao": _composition(report["summary"]),
        "faixas_permanencia": _duration_bands(period["events"]),
        "protocolos_distintos": protocols,
        "cobertura_historica": coverage,
        "comparacao_periodo_anterior": prior,
        "nota_metodologica": _methodology(),
    }
    snapshot["metadados"]["descricao_periodo"] = format_report_period(snapshot)
    return snapshot


def can_finalize(snapshot):
    """Annual accumulated reports may be partial, but no period may contain gaps."""
    coverage = snapshot["cobertura_historica"]
    return not coverage["meses_ausentes"] and not coverage["lacunas_no_ano"]


def _require_administrator(principal):
    if not getattr(principal, "administrator", False):
        raise PermissionError(
            "Apenas administradores podem excluir uma versão em elaboração."
        )


def delete_editable_report_version(store, identifier, principal):
    """Delete one latest draft or review version and keep an audit record."""
    from database.institutional_reports import InstitutionalReportsStore
    from services.audit import registrar_evento

    _require_administrator(principal)
    removed = InstitutionalReportsStore(store).delete_editable_version(identifier)
    registrar_evento(
        store,
        evento="RELATORIO_INSTITUCIONAL_VERSAO_EXCLUIDA",
        modulo="relatorios",
        acao="EXCLUIR_VERSAO",
        principal=principal,
        entidade_tipo="relatorio_institucional",
        entidade_id=removed["id"],
        detalhes={
            "tipo": removed["tipo"],
            "ano": removed["ano"],
            "trimestre": removed["trimestre"],
            "versao": removed["versao"],
            "status": removed["status"],
        },
    )
    return removed


def delete_editable_period_versions(store, tipo, ano, trimestre, principal):
    """Delete the newest editable versions, stopping at a frozen one."""
    from database.institutional_reports import InstitutionalReportsStore

    _require_administrator(principal)
    repository = InstitutionalReportsStore(store)
    removed = []
    while True:
        latest = repository.latest_for_period(tipo, ano, trimestre)
        if not latest or latest["status"] not in ("RASCUNHO", "EM_REVISAO"):
            break
        removed.append(delete_editable_report_version(store, latest["id"], principal))
    if not removed:
        raise ValueError("Não há versão em elaboração para excluir.")
    return removed
