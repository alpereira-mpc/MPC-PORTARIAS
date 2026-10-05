"""Human presentation of frozen institutional facts. Snapshots stay unchanged."""

import re

MONTHS = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)
ANNUAL_WITHOUT_PRIOR = (
    "Não há período anual anterior equivalente disponível para comparação."
)
PRIOR_WITHOUT_DATA = "Não há período anterior disponível para comparação."


def format_count(value):
    """Brazilian grouping for a count. None stays absent."""
    if value is None:
        return None
    number = int(round(float(value)))
    return f"{number:,}".replace(",", ".")


def format_percent(value):
    """One decimal place and a comma, already scaled as a percentage."""
    if value is None:
        return None
    return f"{float(value):.1f}".replace(".", ",") + "%"


def format_days(value):
    """One decimal place for a median expressed in days."""
    if value is None:
        return None
    return f"{float(value):.1f}".replace(".", ",") + " dias"


def format_report_period(snapshot):
    """Readable period from type, year and frozen coverage. Ignores stored wording."""
    metadata = (snapshot or {}).get("metadados") or {}
    year = metadata.get("ano")
    if metadata.get("tipo") == "TRIMESTRAL":
        quarter = metadata.get("trimestre")
        if quarter in (None, ""):
            return f"Trimestre de {year}" if year else "Trimestre"
        return f"{int(quarter)}º trimestre de {year}"
    months = _months(snapshot)
    if not months or year is None:
        return f"Acumulado de {year}" if year else "Acumulado"
    if months == list(range(1, 13)):
        return f"Janeiro a dezembro de {year}"
    start = MONTHS[months[0] - 1]
    end = MONTHS[months[-1] - 1]
    if months == list(range(months[0], months[-1] + 1)):
        return f"Acumulado de {start} a {end} de {year}"
    names = ", ".join(MONTHS[month - 1] for month in months)
    return f"Acumulado de {names} de {year}"


def missing_comparison_text(snapshot):
    """Deterministic sentence when the frozen snapshot has no previous period."""
    if ((snapshot or {}).get("metadados") or {}).get("tipo") == "ANUAL":
        return ANNUAL_WITHOUT_PRIOR
    return PRIOR_WITHOUT_DATA


def comparison_available(snapshot):
    prior = (snapshot or {}).get("comparacao_periodo_anterior")
    if not isinstance(prior, dict):
        return False
    return any(
        prior.get(key) is not None
        for key in ("distributed", "production", "opinions", "quotas")
    )


def _months(snapshot):
    raw = ((snapshot or {}).get("cobertura_historica") or {}).get(
        "meses_disponiveis"
    ) or []
    months = []
    for month in raw:
        try:
            months.append(int(month))
        except (TypeError, ValueError):
            continue
    return sorted(set(months))


_WRITING_KEYS = {
    "distributed": "distribuicoes",
    "production": "producao",
    "opinions": "pareceres",
    "quotas": "cotas",
    "production_rate": "producao_distribuicoes",
    "median_days": "mediana",
}


def writing_indicators(summary):
    """Rename technical keys before they reach the model. Snapshots stay unchanged."""
    renamed = {}
    for key, value in (summary or {}).items():
        if "opinion" in str(key).lower():
            renamed["pareceres"] = value
            continue
        renamed[_WRITING_KEYS.get(key, key)] = value
    return renamed


def overview_period_label(report):
    """Human period for the index. Version stays out of this label."""
    snapshot = (report or {}).get("snapshot_dados") or {}
    metadata = snapshot.get("metadados") or {}
    tipo = report.get("tipo") or metadata.get("tipo")
    year = report.get("ano") or metadata.get("ano")
    if tipo == "TRIMESTRAL":
        if snapshot:
            return format_report_period(snapshot)
        quarter = report.get("trimestre") or metadata.get("trimestre")
        return f"{int(quarter)}º trimestre de {year}"
    if snapshot:
        human = format_report_period(snapshot)
        if human.lower().startswith("acumulado de "):
            human = human[13:]
        elif human:
            human = human[0].lower() + human[1:]
        return f"Anual — {human}"
    return f"Anual — {year}"


def editorial_facts(snapshot):
    """Separate calculation values from the wording the model should copy."""
    snapshot = snapshot or {}
    summary = snapshot.get("indicadores_gerais") or {}
    composition = snapshot.get("composicao_producao") or {}
    metadata = snapshot.get("metadados") or {}
    tipo = metadata.get("tipo") or ""
    facts = {
        "periodo": format_report_period(snapshot),
        "tipo_relatorio": tipo,
        "periodo_parcial": bool(metadata.get("periodo_parcial")),
        "indicadores": _indicator_facts(summary, composition),
        "evolucao": _evolution_facts(snapshot, tipo),
        "permanencia": _permanence_facts(snapshot, summary),
        "procuradores": _procurador_facts(snapshot),
        "comparacao": _comparison_facts(snapshot, summary),
    }
    if tipo == "ANUAL":
        facts["fatos_anuais"] = snapshot.get("fatos_anuais") or {}
    return facts


def _shown(raw, display):
    return {"raw": raw, "display": display}


def _indicator_facts(summary, composition):
    opinions = summary.get("opinions")
    quotas = summary.get("quotas")
    production = summary.get("production")
    opinion_share = composition.get("pareceres_percentual")
    quota_share = composition.get("cotas_percentual")
    if opinion_share is None and production:
        opinion_share = float(opinions or 0) / float(production) * 100
    if quota_share is None and production:
        quota_share = float(quotas or 0) / float(production) * 100
    return {
        "distribuicoes": _shown(
            summary.get("distributed"), format_count(summary.get("distributed"))
        ),
        "producao": _shown(production, format_count(production)),
        "pareceres": _shown(opinions, _share(opinions, opinion_share)),
        "cotas": _shown(quotas, _share(quotas, quota_share)),
        "producao_distribuicoes": _shown(
            summary.get("production_rate"),
            format_percent(summary.get("production_rate")),
        ),
        "mediana": _shown(
            summary.get("median_days"), format_days(summary.get("median_days"))
        ),
    }


def _share(count, percent):
    if count is None:
        return None
    if percent is None:
        return format_count(count)
    return f"{format_count(count)} ({format_percent(percent)})"


def _series(snapshot):
    rows = []
    for item in snapshot.get("serie_mensal") or []:
        if not isinstance(item, dict):
            continue
        try:
            month = int(item.get("month", item.get("mes")))
        except (TypeError, ValueError):
            continue
        if month < 1 or month > 12:
            continue
        summary = item.get("summary") or item.get("indicadores") or {}
        rows.append(
            {
                "mes": MONTHS[month - 1],
                "distribuicoes": summary.get("distributed"),
                "producao": summary.get("production"),
            }
        )
    return rows


def _peak(rows, field):
    ranked = [row for row in rows if row.get(field) is not None]
    if not ranked:
        return None
    chosen = max(ranked, key=lambda row: float(row[field]))
    return {"mes": chosen["mes"], "display": format_count(chosen[field])}


def _evolution_facts(snapshot, tipo):
    rows = _series(snapshot)
    if tipo == "TRIMESTRAL":
        return {
            "modo": "trimestral",
            "meses": [
                {
                    "mes": row["mes"],
                    "distribuicoes": format_count(row["distribuicoes"]),
                    "producao": format_count(row["producao"]),
                }
                for row in rows
            ],
            "maior_distribuicao": _peak(rows, "distribuicoes"),
            "menor_distribuicao": _trough(rows, "distribuicoes"),
            "maior_producao": _peak(rows, "producao"),
            "menor_producao": _trough(rows, "producao"),
            "instrucao": (
                "Escreva um ou dois parágrafos, sem bullets. "
                "Relacione a evolução das Distribuições, a da Produção e o mês de maior ou menor volume. "
                "Não transforme a seção em uma lista dos três registros."
            ),
        }
    return {
        "modo": "anual",
        "nao_enumerar_todos_os_meses": True,
        "pico_distribuicoes": _peak(rows, "distribuicoes"),
        "pico_producao": _peak(rows, "producao"),
        "maior_afastamento": _divergence(rows),
        "encerramento": _closing(rows),
        "instrucao": (
            "Não enumere todos os meses. Identifique no máximo quatro padrões "
            "e, se usar lista, no máximo quatro linhas iniciadas por hífen."
        ),
    }


def _trough(rows, field):
    ranked = [row for row in rows if row.get(field) is not None]
    if not ranked:
        return None
    chosen = min(ranked, key=lambda row: float(row[field]))
    return {"mes": chosen["mes"], "display": format_count(chosen[field])}


def _divergence(rows):
    usable = [
        row
        for row in rows
        if row.get("distribuicoes") is not None and row.get("producao") is not None
    ]
    if not usable:
        return None
    chosen = max(
        usable,
        key=lambda row: abs(float(row["producao"]) - float(row["distribuicoes"])),
    )
    return {
        "mes": chosen["mes"],
        "distribuicoes": format_count(chosen["distribuicoes"]),
        "producao": format_count(chosen["producao"]),
    }


def _closing(rows):
    if not rows:
        return None
    last = rows[-1]
    return {
        "mes": last["mes"],
        "distribuicoes": format_count(last["distribuicoes"]),
        "producao": format_count(last["producao"]),
    }


def _permanence_facts(snapshot, summary):
    bands = [
        item
        for item in snapshot.get("faixas_permanencia") or []
        if isinstance(item, dict) and item.get("quantidade") is not None
    ]
    main = max(bands, key=lambda item: float(item["quantidade"])) if bands else None
    long_bands = [
        {
            "faixa": item.get("faixa"),
            "quantidade": format_count(item.get("quantidade")),
        }
        for item in bands
        if _long_band(item.get("faixa")) and float(item["quantidade"]) > 0
    ]
    return {
        "mediana": _shown(
            summary.get("median_days"), format_days(summary.get("median_days"))
        ),
        "faixa_principal": (
            None
            if main is None
            else {
                "faixa": main.get("faixa"),
                "quantidade": format_count(main.get("quantidade")),
            }
        ),
        "registros_em_faixas_longas": long_bands,
        "instrucao": (
            "Priorize a mediana, a concentração principal e as faixas longas "
            "somente quando houver registros. Não transcreva todas as faixas "
            "e não qualifique o tempo como rapidez ou lentidão."
        ),
    }


def _long_band(label):
    text = str(label or "")
    numbers = [int(token) for token in re.findall(r"\d+", text)]
    return any(number >= 61 for number in numbers) or "mais de" in text.lower()


def _procurador_facts(snapshot):
    rows = [
        row for row in snapshot.get("por_procurador") or [] if isinstance(row, dict)
    ]
    productions = [
        row.get("production") for row in rows if row.get("production") is not None
    ]
    distributed = [
        row.get("distributed") for row in rows if row.get("distributed") is not None
    ]
    shares = []
    medians = [
        row.get("median_days") for row in rows if row.get("median_days") is not None
    ]
    for row in rows:
        production = row.get("production")
        opinions = row.get("opinions")
        if production and opinions is not None:
            shares.append(float(opinions) / float(production) * 100)
    return {
        "quantidade": len(rows),
        "producao_minima": format_count(min(productions)) if productions else None,
        "producao_maxima": format_count(max(productions)) if productions else None,
        "distribuicoes_minimas": (
            format_count(min(distributed)) if distributed else None
        ),
        "distribuicoes_maximas": (
            format_count(max(distributed)) if distributed else None
        ),
        "participacao_pareceres_minima": (
            format_percent(min(shares)) if shares else None
        ),
        "participacao_pareceres_maxima": (
            format_percent(max(shares)) if shares else None
        ),
        "mediana_minima": format_days(min(medians)) if medians else None,
        "mediana_maxima": format_days(max(medians)) if medians else None,
        "instrucao": (
            "Interprete o conjunto em um ou dois parágrafos. "
            "Não transcreva as linhas, não cite todos os nomes e não faça ranking. "
            "Quando falar desse tipo de produção, escreva Pareceres."
        ),
    }


def _comparison_facts(snapshot, summary):
    if not comparison_available(snapshot):
        return {"disponivel": False, "texto": missing_comparison_text(snapshot)}
    prior = snapshot.get("comparacao_periodo_anterior") or {}
    return {
        "disponivel": True,
        "instrucao": (
            "Interprete somente estes deltas, em um ou dois parágrafos. "
            "Escolha no máximo três movimentos relevantes. Não liste todos os indicadores."
        ),
        "distribuicoes": _delta(
            summary.get("distributed"), prior.get("distributed"), format_count
        ),
        "producao": _delta(
            summary.get("production"), prior.get("production"), format_count
        ),
        "pareceres": _delta(
            summary.get("opinions"), prior.get("opinions"), format_count
        ),
        "cotas": _delta(summary.get("quotas"), prior.get("quotas"), format_count),
        "producao_distribuicoes": _delta(
            summary.get("production_rate"), prior.get("production_rate"), format_percent
        ),
        "mediana": _delta(
            summary.get("median_days"), prior.get("median_days"), format_days
        ),
    }


def _delta(current, prior, formatter):
    if current is None or prior is None:
        return None
    difference = float(current) - float(prior)
    if difference > 0:
        direction = "maior"
    elif difference < 0:
        direction = "menor"
    else:
        direction = "igual"
    return {
        "atual": formatter(current),
        "anterior": formatter(prior),
        "diferenca": formatter(abs(difference)),
        "sentido": direction,
    }
