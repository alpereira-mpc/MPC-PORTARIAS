"""Deterministic integrity checks for a frozen institutional report version.

The rules read the snapshot, the saved content and the metadata already loaded.
They do not query Tramita, call a language model, change status, or send mail.
"""

import hashlib
import json
import math
import re
from datetime import date, datetime

from document_generator.institutional_report_pdf import institutional_pdf_filename
from services.institutional_report_content import (
    SECTIONS,
    _number_value,
    normalize_content,
    validate_numbers,
)


OK = "OK"
ATENCAO = "ATENCAO"
ERRO = "ERRO"
INTEGRITY_LABELS = {OK: "OK", ATENCAO: "Atenção", ERRO: "Bloqueada"}
RATE_TOLERANCE = 0.15
SHORT_SUMMARY = 40
LONG_SECTION = 8000
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
SUMMARY_FIELDS = (
    ("distributed", "Distribuídos", "inteiro"),
    ("production", "Produção", "inteiro"),
    ("opinions", "Pareceres", "inteiro"),
    ("quotas", "Cotas", "inteiro"),
    ("production_rate", "Produção/Distribuições", "percentual"),
    ("median_days", "Mediana", "dias"),
)
FINALIZATION_SCOPES = {
    "snapshot",
    "cobertura",
    "indicadores",
    "conteudo",
    "status",
    "versao",
}
INTEGRITY_SCOPES = FINALIZATION_SCOPES | {"pdf"}
STRANGE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffd]")
EMPTY_BULLET = re.compile(r"(?m)^\s*[-*•]\s*$")
TIMELINE_LABELS = {
    "RELATORIO_INSTITUCIONAL_CRIADO": "Relatório criado",
    "RELATORIO_INSTITUCIONAL_NOVA_VERSAO": "Nova versão criada",
    "RELATORIO_INSTITUCIONAL_IA_GERADA": "Conteúdo gerado",
    "RELATORIO_INSTITUCIONAL_SECAO_REGENERADA": "Seção regenerada",
    "RELATORIO_INSTITUCIONAL_CONTEUDO_EDITADO": "Conteúdo editado",
    "RELATORIO_INSTITUCIONAL_EM_REVISAO": "Marcado para revisão",
    "RELATORIO_INSTITUCIONAL_FINALIZADO": "Finalizado",
    "RELATORIO_INSTITUCIONAL_PDF_GERADO": "PDF oficial gerado",
    "RELATORIO_INSTITUCIONAL_PDF_REGENERADO": "PDF oficial regenerado",
    "RELATORIO_DISTRIBUICAO_CONCLUIDA": "Distribuído",
    "RELATORIO_DISTRIBUICAO_PARCIAL": "Distribuição parcial",
    "RELATORIO_DISTRIBUICAO_FALHOU": "Falha na distribuição",
    "RELATORIO_DISTRIBUICAO_BLOQUEADA": "Distribuição bloqueada",
    "RELATORIO_INSTITUCIONAL_VERSAO_EXCLUIDA": "Versão em elaboração excluída",
}
ENVIO_LABELS = {
    "PREPARADO": "Preparado",
    "ENVIANDO": "Enviando",
    "ENVIADO": "Enviado",
    "PARCIAL": "Parcial",
    "FALHA": "Falha",
}


def _check(
    codigo,
    status,
    titulo,
    mensagem,
    *,
    escopo,
    bloqueia_finalizacao=False,
    secao=None,
    valor=None,
):
    item = {
        "codigo": codigo,
        "status": status,
        "titulo": titulo,
        "mensagem": mensagem,
        "escopo": escopo,
        "bloqueia_finalizacao": bool(bloqueia_finalizacao),
    }
    if secao:
        item["secao"] = secao
    if valor is not None:
        item["valor"] = valor
    return item


def _worst(checks, scopes=None):
    selected = [
        item for item in checks if scopes is None or item.get("escopo") in scopes
    ]
    if any(item["status"] == ERRO for item in selected):
        return ERRO
    if any(item["status"] == ATENCAO for item in selected):
        return ATENCAO
    return OK


def _finite(value):
    if isinstance(value, bool) or isinstance(value, str):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _canonical_hash(value):
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
            default=_json_default,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"Valor não serializável: {type(value).__name__}")


def snapshot_hash(snapshot):
    """SHA-256 of the canonical snapshot. It is not the PDF hash."""
    return _canonical_hash(snapshot)


def content_hash(content):
    """SHA-256 of the structured content. It is not the PDF hash."""
    return _canonical_hash(content)


def quarter_months(quarter):
    start = (int(quarter) - 1) * 3 + 1
    return list(range(start, start + 3))


def _month_name(month):
    return MONTHS[int(month) - 1]


def _coverage_months(coverage):
    raw = coverage.get("meses_disponiveis") if isinstance(coverage, dict) else None
    if not isinstance(raw, list):
        return None
    try:
        return sorted({int(month) for month in raw})
    except (TypeError, ValueError):
        return None


def _annual_shape(months):
    if not months:
        return "vazia", []
    if months[0] != 1:
        return "lacuna", list(range(1, months[0])) + [
            month for month in range(1, months[-1] + 1) if month not in months
        ]
    missing = [month for month in range(1, months[-1] + 1) if month not in months]
    if missing:
        return "lacuna", missing
    if months == list(range(1, 13)):
        return "completa", []
    return "parcial", []


def period_key(report):
    quarter = report.get("trimestre")
    return (
        report.get("tipo"),
        int(report["ano"]) if report.get("ano") is not None else None,
        int(quarter) if quarter is not None else None,
    )


def suggest_periods(coverage_by_year, existing):
    """Periods whose frozen historical months already support a report.

    ``existing`` contains (tipo, ano, trimestre ou None) for any saved version.
    Nothing is created here.
    """
    known = set(existing or ())
    suggestions = []
    gaps = []
    for year, months in sorted((coverage_by_year or {}).items()):
        try:
            present = sorted({int(month) for month in months or []})
        except (TypeError, ValueError):
            present = []
        year = int(year)
        for quarter in range(1, 5):
            expected = quarter_months(quarter)
            if all(month in present for month in expected):
                if ("TRIMESTRAL", year, quarter) not in known:
                    suggestions.append(
                        {
                            "tipo": "TRIMESTRAL",
                            "ano": year,
                            "trimestre": quarter,
                            "parcial": False,
                            "meses": expected,
                            "titulo": f"{quarter}º trimestre de {year}",
                            "cobertura": (
                                f"{_month_name(expected[0])} a {_month_name(expected[-1])}"
                            ),
                        }
                    )
        if not present:
            continue
        shape, missing = _annual_shape(present)
        if shape == "lacuna":
            gaps.append(
                {
                    "ano": year,
                    "meses": missing,
                    "mensagem": "Há lacuna na cobertura histórica: "
                    + ", ".join(f"{_month_name(month)}/{year}" for month in missing)
                    + ".",
                }
            )
            continue
        if ("ANUAL", year, None) in known:
            continue
        last = present[-1]
        suggestions.append(
            {
                "tipo": "ANUAL",
                "ano": year,
                "trimestre": None,
                "parcial": shape == "parcial",
                "meses": present,
                "titulo": (
                    f"Acumulado de janeiro a {_month_name(last)} de {year}"
                    if shape == "parcial"
                    else f"Ano de {year}"
                ),
                "cobertura": f"janeiro a {_month_name(last)}",
            }
        )
    return {"sugestoes": suggestions, "lacunas": gaps}


def _metadata_checks(report, snapshot):
    checks = []
    meta = snapshot.get("metadados") if isinstance(snapshot, dict) else None
    if not isinstance(snapshot, dict) or not snapshot or not isinstance(meta, dict):
        checks.append(
            _check(
                "SNAPSHOT_PRESENTE",
                ERRO,
                "Snapshot",
                "Esta versão não possui snapshot congelado.",
                escopo="snapshot",
                bloqueia_finalizacao=True,
            )
        )
        return checks
    checks.append(
        _check(
            "SNAPSHOT_PRESENTE",
            OK,
            "Snapshot",
            "O snapshot desta versão está presente.",
            escopo="snapshot",
        )
    )
    pairs = (
        ("tipo", "SNAPSHOT_TIPO", "O tipo do snapshot não corresponde ao relatório."),
        ("ano", "SNAPSHOT_ANO", "O ano do snapshot não corresponde ao relatório."),
    )
    for field, code, message in pairs:
        if meta.get(field) != report.get(field) and not (
            field == "ano" and int(meta.get(field) or 0) == int(report.get(field) or 0)
        ):
            checks.append(
                _check(
                    code,
                    ERRO,
                    "Vínculo do snapshot",
                    message,
                    escopo="snapshot",
                    bloqueia_finalizacao=True,
                )
            )
    report_quarter = report.get("trimestre")
    snapshot_quarter = meta.get("trimestre")
    same_quarter = snapshot_quarter == report_quarter or (
        snapshot_quarter is not None
        and report_quarter is not None
        and int(snapshot_quarter) == int(report_quarter)
    )
    if report.get("tipo") == "ANUAL":
        same_quarter = snapshot_quarter in (None, 0) and report_quarter in (None, 0)
    if not same_quarter:
        checks.append(
            _check(
                "SNAPSHOT_TRIMESTRE",
                ERRO,
                "Vínculo do snapshot",
                "O trimestre do snapshot não corresponde ao relatório.",
                escopo="snapshot",
                bloqueia_finalizacao=True,
            )
        )
    required = (
        (
            "cobertura_historica",
            "COBERTURA_PRESENTE",
            "Cobertura",
            "A cobertura não está no snapshot.",
        ),
        (
            "indicadores_gerais",
            "INDICADORES_PRESENTES",
            "Indicadores",
            "Os indicadores gerais não estão no snapshot.",
        ),
        (
            "serie_mensal",
            "SERIE_PRESENTE",
            "Série",
            "A série temporal não está no snapshot.",
        ),
        (
            "composicao_producao",
            "COMPOSICAO_PRESENTE",
            "Composição",
            "A composição da produção não está no snapshot.",
        ),
        (
            "por_procurador",
            "PROCURADORES_PRESENTES",
            "Procuradores",
            "Os dados por Procurador não estão no snapshot.",
        ),
    )
    for key, code, title, message in required:
        if key not in snapshot or snapshot.get(key) is None:
            checks.append(
                _check(
                    code,
                    ERRO,
                    title,
                    message,
                    escopo="snapshot",
                    bloqueia_finalizacao=True,
                )
            )
        else:
            checks.append(
                _check(
                    code,
                    OK,
                    title,
                    "Presente no snapshot.",
                    escopo="snapshot",
                )
            )
    note = snapshot.get("nota_metodologica")
    if isinstance(note, dict) and any(
        isinstance(item, str) and item.strip() for item in note.values()
    ):
        checks.append(
            _check(
                "METODOLOGIA_PRESENTE",
                OK,
                "Nota metodológica",
                "A nota metodológica está registrada no snapshot.",
                escopo="snapshot",
            )
        )
    else:
        checks.append(
            _check(
                "METODOLOGIA_PRESENTE",
                ATENCAO,
                "Nota metodológica",
                "A nota metodológica não está disponível nesta versão.",
                escopo="snapshot",
            )
        )
    if (
        "comparacao_periodo_anterior" not in snapshot
        or snapshot.get("comparacao_periodo_anterior") is None
    ):
        if report.get("tipo") == "ANUAL":
            checks.append(
                _check(
                    "COMPARACAO_AUSENTE",
                    OK,
                    "Comparação anterior",
                    "Não há período anual anterior disponível para comparação.",
                    escopo="snapshot",
                )
            )
        else:
            checks.append(
                _check(
                    "COMPARACAO_AUSENTE",
                    ATENCAO,
                    "Comparação anterior",
                    "Não há comparação com o período anterior neste snapshot.",
                    escopo="snapshot",
                )
            )
    return checks


def _coverage_checks(report, snapshot):
    if not isinstance(snapshot, dict):
        return []
    coverage = snapshot.get("cobertura_historica")
    months = _coverage_months(coverage)
    if months is None:
        return []
    checks = []
    tipo = report.get("tipo") or (snapshot.get("metadados") or {}).get("tipo")
    if tipo == "TRIMESTRAL":
        quarter = report.get("trimestre") or (snapshot.get("metadados") or {}).get(
            "trimestre"
        )
        expected = quarter_months(quarter) if quarter else []
        if months != expected:
            checks.append(
                _check(
                    "COBERTURA_TRIMESTRAL",
                    ERRO,
                    "Cobertura trimestral",
                    "O trimestre não reúne os três meses esperados.",
                    escopo="cobertura",
                    bloqueia_finalizacao=True,
                )
            )
        else:
            checks.append(
                _check(
                    "COBERTURA_TRIMESTRAL",
                    OK,
                    "Cobertura trimestral",
                    "Os três meses do trimestre estão cobertos.",
                    escopo="cobertura",
                )
            )
        return checks
    shape, missing = _annual_shape(months)
    if shape == "lacuna":
        names = ", ".join(f"{_month_name(month)}" for month in missing)
        checks.append(
            _check(
                "COBERTURA_LACUNA",
                ERRO,
                "Cobertura anual",
                f"A cobertura anual não é contínua. Meses ausentes: {names}.",
                escopo="cobertura",
                bloqueia_finalizacao=True,
            )
        )
    elif shape == "completa":
        checks.append(
            _check(
                "COBERTURA_ANUAL",
                OK,
                "Cobertura anual",
                "O ano está coberto de janeiro a dezembro.",
                escopo="cobertura",
            )
        )
    elif shape == "parcial":
        checks.append(
            _check(
                "ANUAL_PARCIAL",
                ATENCAO,
                "Relatório anual parcial",
                "A cobertura vai de janeiro ao último mês disponível. "
                "Isso é um relatório anual parcial, não um anual completo.",
                escopo="cobertura",
            )
        )
    else:
        checks.append(
            _check(
                "COBERTURA_ANUAL",
                ERRO,
                "Cobertura anual",
                "A cobertura anual não possui meses.",
                escopo="cobertura",
                bloqueia_finalizacao=True,
            )
        )
    return checks


def _indicator_checks(snapshot):
    if not isinstance(snapshot, dict) or not isinstance(
        snapshot.get("indicadores_gerais"), dict
    ):
        return []
    summary = snapshot["indicadores_gerais"]
    checks = []
    for key, label, kind in SUMMARY_FIELDS:
        if key not in summary or summary.get(key) is None:
            if key == "production_rate" and not summary.get("distributed"):
                continue
            checks.append(
                _check(
                    "INDICADOR_AUSENTE",
                    ATENCAO,
                    label,
                    f"O indicador {label} não está registrado nesta versão.",
                    escopo="indicadores",
                )
            )
            continue
        value = summary[key]
        if not _finite(value):
            checks.append(
                _check(
                    "INDICADOR_NAO_FINITO",
                    ERRO,
                    "Indicador",
                    "Há valor numérico inválido nos indicadores gerais.",
                    escopo="indicadores",
                    bloqueia_finalizacao=True,
                )
            )
        elif kind != "percentual" and float(value) < 0:
            checks.append(
                _check(
                    "INDICADOR_NEGATIVO",
                    ERRO,
                    "Indicador",
                    "Há contagem ou mediana negativa nos indicadores gerais.",
                    escopo="indicadores",
                    bloqueia_finalizacao=True,
                )
            )
    opinions = summary.get("opinions")
    quotas = summary.get("quotas")
    production = summary.get("production")
    if all(_finite(item) for item in (opinions, quotas, production)):
        if int(round(opinions)) + int(round(quotas)) != int(round(production)):
            checks.append(
                _check(
                    "PRODUCAO_COMPOSICAO",
                    ERRO,
                    "Produção",
                    "A produção não corresponde à soma de pareceres e cotas.",
                    escopo="indicadores",
                    bloqueia_finalizacao=True,
                )
            )
        else:
            checks.append(
                _check(
                    "PRODUCAO_COMPOSICAO",
                    OK,
                    "Produção",
                    "A produção corresponde à soma de pareceres e cotas.",
                    escopo="indicadores",
                )
            )
    distributed = summary.get("distributed")
    rate = summary.get("production_rate")
    if _finite(distributed) and _finite(production) and rate is not None:
        if float(distributed) == 0:
            checks.append(
                _check(
                    "PRODUCAO_DISTRIBUICOES",
                    ATENCAO,
                    "Produção/Distribuições",
                    "Não há distribuições para calcular o percentual.",
                    escopo="indicadores",
                )
            )
        elif not _finite(rate):
            checks.append(
                _check(
                    "PRODUCAO_DISTRIBUICOES",
                    ERRO,
                    "Produção/Distribuições",
                    "O percentual de produção sobre distribuições não é um número válido.",
                    escopo="indicadores",
                    bloqueia_finalizacao=True,
                )
            )
        else:
            expected = float(production) / float(distributed) * 100
            if abs(float(rate) - expected) <= RATE_TOLERANCE or round(
                float(rate), 1
            ) == round(expected, 1):
                checks.append(
                    _check(
                        "PRODUCAO_DISTRIBUICOES",
                        OK,
                        "Produção/Distribuições",
                        "O percentual confere com os valores congelados.",
                        escopo="indicadores",
                    )
                )
            else:
                checks.append(
                    _check(
                        "PRODUCAO_DISTRIBUICOES",
                        ERRO,
                        "Produção/Distribuições",
                        "O percentual não confere com produção e distribuições congeladas.",
                        escopo="indicadores",
                        bloqueia_finalizacao=True,
                    )
                )
    coverage = _coverage_months(snapshot.get("cobertura_historica"))
    series = snapshot.get("serie_mensal")
    if isinstance(series, list) and coverage is not None:
        try:
            found = sorted(
                {
                    int(item.get("month"))
                    for item in series
                    if isinstance(item, dict) and item.get("month") is not None
                }
            )
        except (TypeError, ValueError):
            found = []
        invalid = any(month < 1 or month > 12 for month in found)
        if invalid or found != coverage:
            checks.append(
                _check(
                    "SERIE_MESES",
                    ERRO,
                    "Série mensal",
                    "Os meses da série não correspondem à cobertura congelada.",
                    escopo="indicadores",
                    bloqueia_finalizacao=True,
                )
            )
        else:
            checks.append(
                _check(
                    "SERIE_MESES",
                    OK,
                    "Série mensal",
                    "A série tem os mesmos meses da cobertura.",
                    escopo="indicadores",
                )
            )
        for item in series:
            summary_row = item.get("summary") if isinstance(item, dict) else None
            if not isinstance(summary_row, dict):
                continue
            for key, _label, kind in SUMMARY_FIELDS:
                value = summary_row.get(key)
                if value is None:
                    continue
                if not _finite(value) or (kind != "percentual" and float(value) < 0):
                    checks.append(
                        _check(
                            "SERIE_VALORES",
                            ERRO,
                            "Série mensal",
                            "A série mensal contém valor inválido.",
                            escopo="indicadores",
                            bloqueia_finalizacao=True,
                        )
                    )
                    break
            else:
                continue
            break
    people = snapshot.get("por_procurador")
    if isinstance(people, list) and not people:
        checks.append(
            _check(
                "PROCURADORES_VAZIO",
                ATENCAO,
                "Procuradores",
                "O snapshot não lista Procuradores neste período.",
                escopo="indicadores",
            )
        )
    if not checks:
        checks.append(
            _check(
                "INDICADORES_COERENTES",
                OK,
                "Indicadores",
                "Os indicadores gerais são coerentes.",
                escopo="indicadores",
            )
        )
    return checks


def _ignored_number(value, report, snapshot):
    meta = snapshot.get("metadados") if isinstance(snapshot, dict) else {}
    meta = meta or {}
    candidates = [meta.get("ano"), report.get("ano"), report.get("versao")]
    quarter = report.get("trimestre") or meta.get("trimestre")
    if quarter:
        candidates.append(quarter)
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            if abs(float(value) - float(candidate)) <= 0.11:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _content_checks(report, snapshot):
    raw = report.get("conteudo_estruturado")
    checks = []
    if not isinstance(raw, dict):
        checks.append(
            _check(
                "CONTEUDO_SALVO",
                ATENCAO,
                "Conteúdo",
                "O conteúdo estruturado ainda não foi salvo.",
                escopo="conteudo",
            )
        )
        return checks
    checks.append(
        _check(
            "CONTEUDO_SALVO",
            OK,
            "Conteúdo",
            "O conteúdo estruturado está salvo nesta versão.",
            escopo="conteudo",
        )
    )
    if not isinstance(snapshot, dict) or not snapshot.get("metadados"):
        return checks
    content = normalize_content(raw, snapshot)
    for key, label in SECTIONS:
        text = (content.get(key) or {}).get("texto") or ""
        stripped = text.strip()
        if not stripped:
            checks.append(
                _check(
                    "SECAO_VAZIA",
                    ATENCAO,
                    label,
                    f"A seção {label} está vazia.",
                    escopo="conteudo",
                    secao=key,
                )
            )
            continue
        if key == "resumo_executivo" and len(stripped) < SHORT_SUMMARY:
            checks.append(
                _check(
                    "TEXTO_CURTO",
                    ATENCAO,
                    label,
                    "O resumo executivo está muito curto e merece revisão.",
                    escopo="conteudo",
                    secao=key,
                )
            )
        if len(stripped) > LONG_SECTION:
            checks.append(
                _check(
                    "TEXTO_LONGO",
                    ATENCAO,
                    label,
                    "A seção está muito longa em relação ao padrão esperado.",
                    escopo="conteudo",
                    secao=key,
                )
            )
        if EMPTY_BULLET.search(text):
            checks.append(
                _check(
                    "BULLET_VAZIO",
                    ATENCAO,
                    label,
                    "Há um item de lista vazio nesta seção.",
                    escopo="conteudo",
                    secao=key,
                )
            )
        if (
            STRANGE.search(text)
            or "```" in text
            or (stripped.startswith("{") and stripped.endswith("}"))
        ):
            checks.append(
                _check(
                    "TEXTO_ESTRANHO",
                    ATENCAO,
                    label,
                    "A seção contém marcação ou caracteres que não pertencem ao texto institucional.",
                    escopo="conteudo",
                    secao=key,
                )
            )
        unknown = []
        try:
            tokens = validate_numbers(text, snapshot)
        except (KeyError, TypeError, ValueError, OverflowError):
            tokens = []
        for token in tokens:
            value = _number_value(token)
            if value is None or _ignored_number(value, report, snapshot):
                continue
            unknown.append(token)
        for token in unknown:
            checks.append(
                _check(
                    "NUMERO_NAO_RECONHECIDO",
                    ATENCAO,
                    label,
                    "O texto contém valor numérico que não pôde ser relacionado "
                    "aos dados congelados desta versão.",
                    escopo="conteudo",
                    secao=key,
                    valor=token,
                )
            )
    return checks


def _version_checks(versions):
    if not versions:
        return []
    numbers = []
    for item in versions:
        try:
            numbers.append(int(item["versao"]))
        except (KeyError, TypeError, ValueError):
            return [
                _check(
                    "VERSAO_NUMERO",
                    ERRO,
                    "Versões",
                    "Há uma versão sem número válido neste período.",
                    escopo="versao",
                    bloqueia_finalizacao=True,
                )
            ]
    expected = list(range(1, max(numbers) + 1))
    if sorted(numbers) != expected or len(numbers) != len(set(numbers)):
        return [
            _check(
                "VERSAO_SEQUENCIA",
                ERRO,
                "Versões",
                "As versões deste período não formam a sequência 1, 2, 3…",
                escopo="versao",
                bloqueia_finalizacao=True,
            )
        ]
    return [
        _check(
            "VERSAO_SEQUENCIA",
            OK,
            "Versões",
            "As versões deste período estão em sequência.",
            escopo="versao",
        )
    ]


def _status_checks(report, distributions):
    status = report.get("status")
    checks = []
    if status not in {"RASCUNHO", "EM_REVISAO", "FINALIZADO", "ENVIADO"}:
        checks.append(
            _check(
                "STATUS_DESCONHECIDO",
                ERRO,
                "Status",
                "O status desta versão não é reconhecido.",
                escopo="status",
                bloqueia_finalizacao=True,
            )
        )
        return checks
    if distributions is None:
        return checks
    if status in {"RASCUNHO", "EM_REVISAO"} and distributions:
        checks.append(
            _check(
                "STATUS_DISTRIBUICAO",
                ERRO,
                "Status",
                "Há distribuição registrada em uma versão que ainda não foi finalizada.",
                escopo="status",
                bloqueia_finalizacao=True,
            )
        )
    if status == "ENVIADO" and not any(
        item.get("status_envio") == "ENVIADO" for item in distributions
    ):
        checks.append(
            _check(
                "STATUS_ENVIO",
                ERRO,
                "Status",
                "A versão está marcada como enviada sem um envio concluído.",
                escopo="status",
                bloqueia_finalizacao=False,
            )
        )
    return checks


def _pdf_metadata_checks(report, pdf_metadata):
    status = report.get("status")
    if status not in {"FINALIZADO", "ENVIADO"}:
        return []
    if not pdf_metadata:
        return [
            _check(
                "PDF_AUSENTE",
                ERRO if status == "ENVIADO" else ATENCAO,
                "PDF oficial",
                (
                    "O PDF oficial desta versão ainda não está armazenado."
                    if status == "FINALIZADO"
                    else "A versão enviada não possui PDF oficial."
                ),
                escopo="pdf",
                bloqueia_finalizacao=False,
            )
        ]
    checks = []
    if int(pdf_metadata.get("relatorio_id") or 0) != int(report.get("id") or 0):
        checks.append(
            _check(
                "PDF_VERSAO",
                ERRO,
                "PDF oficial",
                "O artefato armazenado pertence a outra versão.",
                escopo="pdf",
            )
        )
    expected = institutional_pdf_filename(report)
    if pdf_metadata.get("nome_arquivo") != expected:
        checks.append(
            _check(
                "PDF_NOME",
                ATENCAO,
                "PDF oficial",
                "O nome do arquivo não corresponde ao padrão desta versão.",
                escopo="pdf",
            )
        )
    size = pdf_metadata.get("tamanho", pdf_metadata.get("tamanho_bytes"))
    digest = str(pdf_metadata.get("sha256") or "")
    if not size or int(size) <= 0 or len(digest) != 64:
        checks.append(
            _check(
                "PDF_METADADOS",
                ERRO,
                "PDF oficial",
                "Os metadados do PDF oficial estão incompletos.",
                escopo="pdf",
            )
        )
    else:
        checks.append(
            _check(
                "PDF_METADADOS",
                OK,
                "PDF oficial",
                "Os metadados do PDF oficial estão registrados.",
                escopo="pdf",
            )
        )
    return checks


def verify_pdf_bytes(report, artifact):
    """Cryptographic check. The caller loads the bytes only for this call."""
    checks = []
    if not artifact or not artifact.get("conteudo"):
        checks.append(
            _check(
                "PDF_BYTES",
                ERRO,
                "PDF oficial",
                "Não há bytes do PDF oficial para conferir.",
                escopo="pdf",
            )
        )
    else:
        content = bytes(artifact["conteudo"])
        if not content.startswith(b"%PDF"):
            checks.append(
                _check(
                    "PDF_FORMATO",
                    ERRO,
                    "PDF oficial",
                    "O arquivo armazenado não começa com a assinatura de um PDF.",
                    escopo="pdf",
                )
            )
        size = artifact.get("tamanho")
        if size is not None and int(size) != len(content):
            checks.append(
                _check(
                    "PDF_TAMANHO",
                    ERRO,
                    "PDF oficial",
                    "O tamanho armazenado não corresponde aos bytes do PDF.",
                    escopo="pdf",
                )
            )
        digest = hashlib.sha256(content).hexdigest()
        if digest != str(artifact.get("sha256") or ""):
            checks.append(
                _check(
                    "PDF_SHA256",
                    ERRO,
                    "PDF oficial",
                    "O SHA-256 recalculado não corresponde ao hash registrado.",
                    escopo="pdf",
                )
            )
        if int(artifact.get("relatorio_id") or 0) != int(report.get("id") or 0):
            checks.append(
                _check(
                    "PDF_VERSAO",
                    ERRO,
                    "PDF oficial",
                    "O artefato carregado pertence a outra versão.",
                    escopo="pdf",
                )
            )
        if artifact.get("nome_arquivo") != institutional_pdf_filename(report):
            checks.append(
                _check(
                    "PDF_NOME",
                    ATENCAO,
                    "PDF oficial",
                    "O nome do arquivo não corresponde ao padrão desta versão.",
                    escopo="pdf",
                )
            )
    if not any(item["status"] == ERRO for item in checks):
        checks.append(
            _check(
                "PDF_INTEGRO",
                OK,
                "PDF oficial",
                "PDF oficial íntegro.",
                escopo="pdf",
            )
        )
    return {"status": _worst(checks), "checks": checks}


def _distribution_checks(report, recipients, gmail_configured, pdf_metadata):
    status = report.get("status")
    if status not in {"FINALIZADO", "ENVIADO"}:
        return [
            _check(
                "DISTRIBUICAO_STATUS",
                OK,
                "Distribuição",
                "A distribuição fica disponível depois da finalização.",
                escopo="distribuicao",
            )
        ]
    checks = []
    if not pdf_metadata:
        checks.append(
            _check(
                "DISTRIBUICAO_PDF",
                ERRO,
                "Distribuição",
                "Não há PDF oficial para distribuir.",
                escopo="distribuicao",
            )
        )
    if recipients is not None:
        blockers = list(recipients.get("bloqueios") or [])
        if blockers:
            for message in blockers:
                checks.append(
                    _check(
                        "DISTRIBUICAO_DESTINATARIOS",
                        ERRO,
                        "Destinatários",
                        message,
                        escopo="distribuicao",
                    )
                )
        elif not recipients.get("destinatarios"):
            checks.append(
                _check(
                    "DISTRIBUICAO_DESTINATARIOS",
                    ERRO,
                    "Destinatários",
                    "Não há destinatários válidos para a distribuição.",
                    escopo="distribuicao",
                )
            )
        else:
            checks.append(
                _check(
                    "DISTRIBUICAO_DESTINATARIOS",
                    OK,
                    "Destinatários",
                    "Todos os Procuradores ativos possuem e-mail válido e sem duplicidade.",
                    escopo="distribuicao",
                )
            )
    if gmail_configured is False:
        checks.append(
            _check(
                "DISTRIBUICAO_GMAIL",
                ATENCAO,
                "Envio",
                "O envio institucional não está configurado neste ambiente.",
                escopo="distribuicao",
            )
        )
    elif gmail_configured is True:
        checks.append(
            _check(
                "DISTRIBUICAO_GMAIL",
                OK,
                "Envio",
                "A configuração local de envio está presente.",
                escopo="distribuicao",
            )
        )
    return checks


def validate_institutional_report_version(
    report,
    *,
    pdf_metadata=None,
    distributions=None,
    recipients=None,
    gmail_configured=None,
    versions=None,
    pdf_bytes_result=None,
):
    """Return the consolidated integrity of one already loaded version."""
    report = report or {}
    snapshot = report.get("snapshot_dados") or {}
    checks = []
    checks.extend(_metadata_checks(report, snapshot))
    checks.extend(_coverage_checks(report, snapshot))
    checks.extend(_indicator_checks(snapshot))
    checks.extend(_content_checks(report, snapshot))
    checks.extend(_version_checks(versions))
    checks.extend(_status_checks(report, distributions))
    checks.extend(_pdf_metadata_checks(report, pdf_metadata))
    if pdf_bytes_result:
        checks.extend(pdf_bytes_result.get("checks") or [])
    checks.extend(
        _distribution_checks(report, recipients, gmail_configured, pdf_metadata)
    )
    status = _worst(checks, INTEGRITY_SCOPES)
    return {
        "status": status,
        "rotulo": INTEGRITY_LABELS[status],
        "checks": checks,
    }


def finalization_readiness(result, *, status, unsaved=False):
    """Human finalization gate. Attention never finalizes by itself."""
    checks = list(result.get("checks") or [])
    if unsaved:
        checks.append(
            _check(
                "EDICAO_NAO_SALVA",
                ERRO,
                "Edição pendente",
                "Salve as alterações textuais antes de finalizar o relatório.",
                escopo="conteudo",
                bloqueia_finalizacao=True,
            )
        )
    errors = [
        item
        for item in checks
        if item["status"] == ERRO and item.get("bloqueia_finalizacao")
    ]
    attentions = [
        item
        for item in checks
        if item["status"] == ATENCAO and item.get("escopo") in FINALIZATION_SCOPES
    ]
    if status in {"FINALIZADO", "ENVIADO"}:
        label = "Versão já finalizada"
        ready = False
        blocked = True
        confirm = False
    elif errors:
        label = "Existem pendências que impedem a finalização"
        ready = False
        blocked = True
        confirm = False
    elif attentions:
        label = "Pode ser finalizado, mas existem pontos de atenção"
        ready = False
        blocked = False
        confirm = True
    else:
        label = "Pronto para finalizar"
        ready = True
        blocked = False
        confirm = False
    return {
        "rotulo": label,
        "pronto": ready,
        "bloqueado": blocked,
        "requer_confirmacao": confirm,
        "erros": errors,
        "atencoes": attentions,
    }


def distribution_readiness(result, *, status, summaries=None):
    summaries = list(summaries or [])
    sent = [item for item in summaries if item.get("status_envio") == "ENVIADO"]
    relevant = [
        item
        for item in result.get("checks") or []
        if item.get("escopo") in {"pdf", "distribuicao", "status"}
        and item["status"] in {ERRO, ATENCAO}
    ]
    blocking = [item for item in relevant if item["status"] == ERRO]
    if status == "ENVIADO" and sent and not blocking:
        label = "Distribuição concluída"
        ready = True
    elif status not in {"FINALIZADO", "ENVIADO"}:
        label = "A distribuição fica disponível após a finalização"
        ready = False
    elif blocking:
        label = "A distribuição possui pendências"
        ready = False
    elif any(item["status"] == ATENCAO for item in relevant):
        label = "A distribuição ainda não está pronta"
        ready = False
    elif status == "FINALIZADO":
        label = "Pronta para distribuição"
        ready = True
    else:
        label = "Distribuição pendente"
        ready = False
    return {
        "rotulo": label,
        "pronta": ready,
        "pendencias": relevant,
        "envios": summaries,
    }


def report_cycle(report, result, *, pdf_metadata=None, summaries=None):
    """Reflect the version. This never changes the stored status."""
    checks = result.get("checks") or []

    def state_for(scope, *, done, pending="pendente"):
        scoped = [item for item in checks if item.get("escopo") == scope]
        if any(item["status"] == ERRO for item in scoped):
            return "erro"
        if any(item["status"] == ATENCAO for item in scoped):
            return "atencao"
        return "concluido" if done else pending

    status = report.get("status")
    content_done = isinstance(report.get("conteudo_estruturado"), dict)
    reviewed = status in {"EM_REVISAO", "FINALIZADO", "ENVIADO"}
    finalized = status in {"FINALIZADO", "ENVIADO"}
    sent = any(item.get("status_envio") == "ENVIADO" for item in summaries or [])
    partial = any(item.get("status_envio") == "PARCIAL" for item in summaries or [])
    failed = any(item.get("status_envio") == "FALHA" for item in summaries or [])
    if sent:
        distribution = "concluido"
    elif partial:
        distribution = "atencao"
    elif failed:
        distribution = "erro"
    else:
        distribution = "pendente"
    pdf_state = state_for("pdf", done=bool(pdf_metadata))
    if finalized and not pdf_metadata and pdf_state == "concluido":
        pdf_state = "pendente"
    return [
        {"etapa": "Snapshot", "estado": state_for("snapshot", done=True)},
        {"etapa": "Conteúdo", "estado": state_for("conteudo", done=content_done)},
        {
            "etapa": "Revisão",
            "estado": "concluido" if reviewed else "pendente",
        },
        {
            "etapa": "Finalização",
            "estado": "concluido" if finalized else "pendente",
        },
        {"etapa": "PDF", "estado": pdf_state},
        {"etapa": "Distribuição", "estado": distribution},
    ]


def describe_audit_events(rows):
    """Keep only events that were actually recorded."""
    described = []
    for row in rows or []:
        label = TIMELINE_LABELS.get(row.get("evento"))
        if not label:
            continue
        described.append(
            {
                "quando": row.get("criado_em"),
                "titulo": label,
                "evento": row.get("evento"),
            }
        )
    return described


def _format_number(value, kind):
    if value is None or not _finite(value):
        return "—"
    if kind == "inteiro":
        return f"{int(round(float(value))):,}".replace(",", ".")
    formatted = f"{float(value):.1f}".replace(".", ",")
    return formatted + "%" if kind == "percentual" else formatted


def _signed_delta(before, after, kind):
    if not _finite(before) or not _finite(after):
        return "—"
    diff = float(after) - float(before)
    if kind == "inteiro":
        return f"{int(round(diff)):+d}"
    formatted = f"{diff:+.1f}".replace(".", ",")
    return formatted + " p.p." if kind == "percentual" else formatted


def compare_versions(left, right, *, left_pdf=None, right_pdf=None):
    """Objective differences between two versions of the same period."""
    if period_key(left) != period_key(right):
        return {
            "compativel": False,
            "mensagem": "A comparação só é feita entre versões do mesmo período.",
        }
    left_snapshot = left.get("snapshot_dados") or {}
    right_snapshot = right.get("snapshot_dados") or {}
    left_summary = left_snapshot.get("indicadores_gerais") or {}
    right_summary = right_snapshot.get("indicadores_gerais") or {}
    indicators = []
    for key, label, kind in SUMMARY_FIELDS:
        before = left_summary.get(key)
        after = right_summary.get(key)
        indicators.append(
            {
                "indicador": label,
                "anterior": _format_number(before, kind),
                "atual": _format_number(after, kind),
                "diferenca": _signed_delta(before, after, kind),
                "alterado": _finite(before)
                and _finite(after)
                and abs(float(after) - float(before)) > 0.05,
            }
        )
    left_content = (
        normalize_content(left.get("conteudo_estruturado"), left_snapshot)
        if left_snapshot.get("metadados")
        else {}
    )
    right_content = (
        normalize_content(right.get("conteudo_estruturado"), right_snapshot)
        if right_snapshot.get("metadados")
        else {}
    )
    sections = []
    for key, label in SECTIONS:
        left_text = ((left_content.get(key) or {}).get("texto") or "").strip()
        right_text = ((right_content.get(key) or {}).get("texto") or "").strip()
        sections.append(
            {
                "secao": key,
                "titulo": label,
                "alterada": left_text != right_text,
                "texto_a": left_text,
                "texto_b": right_text,
            }
        )
    left_months = _coverage_months(left_snapshot.get("cobertura_historica")) or []
    right_months = _coverage_months(right_snapshot.get("cobertura_historica")) or []
    pdf = None
    if left_pdf or right_pdf:
        pdf = {
            "nome_a": (left_pdf or {}).get("nome_arquivo"),
            "nome_b": (right_pdf or {}).get("nome_arquivo"),
            "hash_a": (left_pdf or {}).get("sha256"),
            "hash_b": (right_pdf or {}).get("sha256"),
            "tamanho_a": (left_pdf or {}).get("tamanho"),
            "tamanho_b": (right_pdf or {}).get("tamanho"),
        }
    return {
        "compativel": True,
        "metadados": {
            "status_a": left.get("status"),
            "status_b": right.get("status"),
            "corte_a": left.get("data_corte"),
            "corte_b": right.get("data_corte"),
            "cobertura_a": left_months,
            "cobertura_b": right_months,
        },
        "indicadores": indicators,
        "secoes": sections,
        "pdf": pdf,
        "snapshots_iguais": snapshot_hash(left_snapshot)
        == snapshot_hash(right_snapshot),
        "conteudos_iguais": content_hash(left.get("conteudo_estruturado"))
        == content_hash(right.get("conteudo_estruturado")),
    }


def overview_row(report):
    """Light status for the institutional index. No PDF bytes are read."""
    from services.institutional_presentation import overview_period_label

    result = validate_institutional_report_version(report)
    period = overview_period_label(report)
    pdf = "OK" if report.get("pdf_sha256") else "—"
    delivery = report.get("ultimo_envio")
    if delivery == "ENVIADO":
        delivery_label = "Enviado"
    elif delivery:
        delivery_label = ENVIO_LABELS.get(delivery, delivery)
    elif report.get("status") == "FINALIZADO":
        delivery_label = "Pendente"
    else:
        delivery_label = "—"
    return {
        "Período": period,
        "Status": {
            "RASCUNHO": "Rascunho",
            "EM_REVISAO": "Em revisão",
            "FINALIZADO": "Finalizado",
            "ENVIADO": "Enviado",
        }.get(report.get("status"), report.get("status") or "—"),
        "Validação": result["rotulo"],
        "PDF": pdf,
        "Distribuição": delivery_label,
    }
