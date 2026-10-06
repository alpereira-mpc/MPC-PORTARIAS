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


def _annual_facts(summary, monthly, bands, events):
    """Frozen analytical facts used only by the annual editorial and PDF views."""
    production = float(summary.get("production") or 0)
    distributed = float(summary.get("distributed") or 0)
    durations = sorted(
        turnaround_days(event)
        for event in events
        if event["tipo_movimentacao"] == "SAIDA"
        and event["classificacao_producao"] in ("PARECER", "COTA")
        and turnaround_days(event) is not None
    )
    counts = {item["faixa"]: item["quantidade"] for item in bands}

    def percentile(level):
        if not durations:
            return None
        position = (len(durations) - 1) * level
        lower, upper = int(position), min(int(position) + 1, len(durations) - 1)
        return durations[lower] + (durations[upper] - durations[lower]) * (
            position - lower
        )

    rows = [item for item in monthly if isinstance(item, dict) and item.get("summary")]

    def month_fact(row):
        value = row["summary"]
        return {
            "mes": row.get("month"),
            "distribuicoes": value.get("distributed"),
            "producao": value.get("production"),
            "saldo": (value.get("production") or 0) - (value.get("distributed") or 0),
        }

    monthly_facts = [month_fact(row) for row in rows]
    more_60 = int(counts.get("61–90 dias", 0)) + int(counts.get("Mais de 90 dias", 0))
    total_duration = sum(int(value) for value in counts.values())
    return {
        "saldo_fluxo": production - distributed,
        "indice_fluxo": production / distributed * 100 if distributed else None,
        "pico_producao": max(
            monthly_facts, key=lambda row: row["producao"], default=None
        ),
        "pico_distribuicoes": max(
            monthly_facts, key=lambda row: row["distribuicoes"], default=None
        ),
        "menor_producao": min(
            monthly_facts, key=lambda row: row["producao"], default=None
        ),
        "menor_distribuicoes": min(
            monthly_facts, key=lambda row: row["distribuicoes"], default=None
        ),
        "ultimo_mes": monthly_facts[-1] if monthly_facts else None,
        "meses_producao_superior_distribuicao": sum(
            row["saldo"] > 0 for row in monthly_facts
        ),
        "meses_distribuicao_superior_producao": sum(
            row["saldo"] < 0 for row in monthly_facts
        ),
        "quantidade_mais_60": more_60,
        "quantidade_mais_90": int(counts.get("Mais de 90 dias", 0)),
        "percentual_0_7": (
            int(counts.get("0–7 dias", 0)) / total_duration * 100
            if total_duration
            else None
        ),
        "percentual_mais_60": (
            more_60 / total_duration * 100 if total_duration else None
        ),
        "percentual_mais_90": (
            int(counts.get("Mais de 90 dias", 0)) / total_duration * 100
            if total_duration
            else None
        ),
        "p75_permanencia": percentile(0.75),
        "p90_permanencia": percentile(0.90),
    }


def _quarterly_facts(summary, monthly, bands, events, procuradores):
    """Frozen facts for the quarterly report; calculations never depend on AI."""
    production = float(summary.get("production") or 0)
    distributed = float(summary.get("distributed") or 0)
    durations = sorted(
        turnaround_days(event)
        for event in events
        if event["tipo_movimentacao"] == "SAIDA"
        and event["classificacao_producao"] in ("PARECER", "COTA")
        and turnaround_days(event) is not None
    )
    counts = {item["faixa"]: int(item["quantidade"]) for item in bands}
    order = (
        "0–7 dias",
        "8–15 dias",
        "16–30 dias",
        "31–60 dias",
        "61–90 dias",
        "Mais de 90 dias",
    )

    def percentile(level):
        if not durations:
            return None
        position = (len(durations) - 1) * level
        lower, upper = int(position), min(int(position) + 1, len(durations) - 1)
        return durations[lower] + (durations[upper] - durations[lower]) * (
            position - lower
        )

    rows = [item for item in monthly if isinstance(item, dict) and item.get("summary")]
    monthly_facts = [
        {
            "mes": row.get("month"),
            "distribuicoes": row["summary"].get("distributed"),
            "producao": row["summary"].get("production"),
            "saldo": (row["summary"].get("production") or 0)
            - (row["summary"].get("distributed") or 0),
        }
        for row in rows
    ]
    total_duration = sum(counts.get(label, 0) for label in order)
    more_90 = counts.get("Mais de 90 dias", 0)
    more_60 = counts.get("61–90 dias", 0) + more_90
    main_band = max(order, key=lambda label: counts.get(label, 0), default=None)

    def interval(field, *, share=False):
        values = []
        for row in procuradores or []:
            value = row.get(field)
            if share:
                total = float(row.get("production") or 0)
                value = float(row.get("opinions") or 0) / total * 100 if total else None
            if value is not None:
                values.append(float(value))
        return {"minimo": min(values), "maximo": max(values)} if values else None

    return {
        "total_distribuicoes": distributed,
        "total_producao": production,
        "total_pareceres": summary.get("opinions"),
        "total_cotas": summary.get("quotas"),
        "saldo_fluxo": production - distributed,
        "relacao_producao_distribuicoes": summary.get("production_rate"),
        "pico_distribuicoes": max(monthly_facts, key=lambda row: row["distribuicoes"] or 0, default=None),
        "menor_distribuicoes": min(monthly_facts, key=lambda row: row["distribuicoes"] or 0, default=None),
        "pico_producao": max(monthly_facts, key=lambda row: row["producao"] or 0, default=None),
        "menor_producao": min(monthly_facts, key=lambda row: row["producao"] or 0, default=None),
        "saldo_por_mes": monthly_facts,
        "meses_producao_superior_distribuicao": sum(row["saldo"] > 0 for row in monthly_facts),
        "meses_distribuicao_superior_producao": sum(row["saldo"] < 0 for row in monthly_facts),
        "mediana_permanencia": summary.get("median_days"),
        "faixa_predominante_permanencia": main_band,
        "quantidade_0_7": counts.get("0–7 dias", 0),
        "quantidade_8_15": counts.get("8–15 dias", 0),
        "quantidade_16_30": counts.get("16–30 dias", 0),
        "quantidade_31_60": counts.get("31–60 dias", 0),
        "quantidade_61_90": counts.get("61–90 dias", 0),
        "quantidade_mais_90": more_90,
        "quantidade_mais_60": more_60,
        "percentual_mais_60": more_60 / total_duration * 100 if total_duration else None,
        "percentual_mais_90": more_90 / total_duration * 100 if total_duration else None,
        "p75_permanencia": percentile(0.75),
        "p90_permanencia": percentile(0.90),
        "participacao_pareceres": summary.get("opinions") / production * 100 if production else None,
        "participacao_cotas": summary.get("quotas") / production * 100 if production else None,
        "intervalo_distribuicoes_procuradores": interval("distributed"),
        "intervalo_producao_procuradores": interval("production"),
        "intervalo_participacao_pareceres": interval("opinions", share=True),
        "intervalo_mediana_procuradores": interval("median_days"),
    }


def _quarterly_comparison_facts(summary, prior):
    """Freeze comparable quarterly deltas, including percentage-point changes."""
    if not isinstance(prior, dict):
        return {"periodo_anterior_identificado": None, "disponivel": False}

    def delta(key, *, percentage_points=False):
        current, previous = summary.get(key), prior.get(key)
        if current is None or previous is None:
            return None
        absolute = float(current) - float(previous)
        return {
            "anterior": previous,
            "atual": current,
            "delta_absoluto": absolute,
            "delta_percentual": (
                None
                if percentage_points or not float(previous)
                else absolute / float(previous) * 100
            ),
            "delta_pontos_percentuais": absolute if percentage_points else None,
        }

    return {
        "periodo_anterior_identificado": prior.get("periodo"),
        "disponivel": True,
        "distribuicoes": delta("distributed"),
        "producao": delta("production"),
        "pareceres": delta("opinions"),
        "cotas": delta("quotas"),
        "relacao_producao_distribuicoes": delta(
            "production_rate", percentage_points=True
        ),
        "mediana_permanencia": delta("median_days"),
    }


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
        if set(prior_months) == set(range((prior_quarter - 1) * 3 + 1, prior_quarter * 3 + 1)):
            prior_start, prior_end = _period_bounds(
                "TRIMESTRAL", prior_year, prior_quarter
            )
            prior = reports.period_report(
                prior_start.isoformat(), prior_end.isoformat()
            )["summary"]
            prior = {
                **prior,
                "periodo": f"{prior_quarter}º trimestre de {prior_year}",
                "ano": prior_year,
                "trimestre": prior_quarter,
            }
    elif months:
        prior_months = set(reports.months_for_year(ano - 1))
        comparable_months = set(months)
        if comparable_months.issubset(prior_months):
            last_month = max(months)
            prior_end = date(
                ano - 1 + (last_month == 12),
                1 if last_month == 12 else last_month + 1,
                1,
            )
            prior = reports.period_report(
                date(ano - 1, 1, 1).isoformat(), prior_end.isoformat()
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
    if tipo == "ANUAL":
        snapshot["fatos_anuais"] = _annual_facts(
            report["summary"], monthly, snapshot["faixas_permanencia"], period["events"]
        )
    elif tipo == "TRIMESTRAL":
        snapshot["fatos_trimestrais"] = _quarterly_facts(
            report["summary"],
            monthly,
            snapshot["faixas_permanencia"],
            period["events"],
            report["by_procurador"],
        )
        snapshot["fatos_trimestrais"]["comparacao"] = _quarterly_comparison_facts(
            report["summary"], prior
        )
    snapshot["metadados"]["descricao_periodo"] = format_report_period(snapshot)
    return snapshot


def can_finalize(snapshot):
    """Annual accumulated reports may be partial, but no period may contain gaps."""
    coverage = snapshot["cobertura_historica"]
    return not coverage["meses_ausentes"] and not coverage["lacunas_no_ano"]


def require_institutional_administrator(principal):
    if not getattr(principal, "administrator", False):
        raise PermissionError(
            "Apenas administradores podem executar esta ação em relatórios institucionais."
        )


def _require_administrator(principal):
    """Compatibility alias for existing institutional-report service calls."""
    require_institutional_administrator(principal)


def create_institutional_report(store, principal, *, new_version=False, **params):
    """Authorized public entry point for creating one report version."""
    from database.institutional_reports import InstitutionalReportsStore

    require_institutional_administrator(principal)
    repository = InstitutionalReportsStore(store)
    if new_version:
        return repository.create_new_version(**params)
    return repository.create(**params)


def finalize_institutional_report(store, identifier, principal):
    """Authorized public entry point for freezing an editable report version."""
    from database.institutional_reports import InstitutionalReportsStore

    require_institutional_administrator(principal)
    return InstitutionalReportsStore(store).finalize(
        identifier, getattr(principal, "email", "") or ""
    )


def report_workflow_state(versions, selected_id=None):
    """Return the one, user-facing workflow decision for a report period.

    Versions arrive newest first.  Keeping this small decision object outside the
    Streamlit views prevents the header, editor and history from inventing
    conflicting rules of their own.
    """
    versions = list(versions or [])
    editable = next(
        (item for item in versions if item.get("status") in ("RASCUNHO", "EM_REVISAO")),
        None,
    )
    official = next(
        (item for item in versions if item.get("status") in ("FINALIZADO", "ENVIADO")),
        None,
    )
    selected = next((item for item in versions if item.get("id") == selected_id), None)
    latest = versions[0] if versions else None
    return {
        "kind": "EMPTY" if not versions else ("EDITABLE" if editable else "OFFICIAL"),
        "latest": latest,
        "selected": selected or latest,
        "editable": editable,
        "official": official,
        "has_editable_successor": bool(
            selected
            and selected.get("status") in ("FINALIZADO", "ENVIADO")
            and editable
            and editable.get("id") != selected.get("id")
        ),
        "can_create": bool(not versions),
        "can_create_new": bool(
            latest and latest.get("status") in ("FINALIZADO", "ENVIADO")
        ),
    }


def deletion_eligibility(repository, identifier):
    """Validate the only supported permanent-deletion policy.

    A finalised version is still removable when it never became an official
    artifact: it must be the newest version and have neither PDF nor delivery.
    """
    report = repository.get(identifier)
    if not report:
        raise ValueError("Relatório institucional não encontrado.")
    versions = repository.list_for_period(
        report["tipo"], report["ano"], report["trimestre"]
    )
    if not versions or versions[0]["id"] != report["id"]:
        raise ValueError("Somente a versão mais recente do período pode ser excluída.")
    if report["status"] == "ENVIADO":
        raise ValueError("Versões enviadas não podem ser excluídas.")
    if repository.pdf_artifact(identifier):
        raise ValueError("Esta versão possui PDF oficial e não pode ser excluída.")
    if repository.list_distributions(identifier):
        raise ValueError(
            "Esta versão possui histórico de distribuição e não pode ser excluída."
        )
    if report["status"] not in ("RASCUNHO", "EM_REVISAO", "FINALIZADO"):
        raise ValueError("Esta versão não pode ser excluída.")
    reason = {
        "RASCUNHO": "RASCUNHO_EXCLUIDO",
        "EM_REVISAO": "REVISAO_EXCLUIDA",
        "FINALIZADO": "FINALIZADO_NAO_PUBLICADO_EXCLUIDO",
    }[report["status"]]
    return {**report, "motivo_tecnico": reason}


def delete_editable_report_version(store, identifier, principal):
    """Permanently delete one eligible latest version, with a minimal audit trail."""
    from database.institutional_reports import InstitutionalReportsStore
    from services.audit import registrar_evento

    _require_administrator(principal)
    repository = InstitutionalReportsStore(store)
    eligible = deletion_eligibility(repository, identifier)
    registrar_evento(
        store,
        evento="RELATORIO_INSTITUCIONAL_VERSAO_EXCLUIDA",
        modulo="relatorios",
        acao="EXCLUIR_VERSAO",
        principal=principal,
        entidade_tipo="relatorio_institucional",
        entidade_id=eligible["id"],
        detalhes={
            "tipo": eligible["tipo"],
            "ano": eligible["ano"],
            "trimestre": eligible["trimestre"],
            "versao": eligible["versao"],
            "status_anterior": eligible["status"],
            "motivo_tecnico": eligible["motivo_tecnico"],
        },
    )
    return repository.delete_version(identifier)


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


def delete_institutional_report_period(store, tipo, ano, trimestre, principal):
    """Administrator-only removal of a whole institutional-report period.

    This deliberately removes only report snapshots and their dependent PDF and
    distribution records. Source data is never referenced by this operation.
    """
    from database.institutional_reports import InstitutionalReportsStore
    from services.audit import registrar_evento

    _require_administrator(principal)
    removed = InstitutionalReportsStore(store).delete_period_administratively(
        tipo, ano, trimestre
    )
    registrar_evento(
        store,
        evento="RELATORIO_INSTITUCIONAL_EXCLUIDO_ADMINISTRATIVAMENTE",
        modulo="relatorios",
        acao="EXCLUIR_ADMINISTRATIVAMENTE",
        principal=principal,
        entidade_tipo="relatorio_institucional",
        entidade_id=removed[0]["id"],
        detalhes={
            "tipo": tipo,
            "ano": int(ano),
            "trimestre": trimestre,
            "versoes_removidas": [item["id"] for item in removed],
        },
    )
    return removed
