"""Human-reviewed textual content for frozen institutional report snapshots."""

import re
import logging

from database.institutional_reports import InstitutionalReportsStore
from database.store import now
from services import ai_service
from services.audit import registrar_evento
from services.institutional_presentation import (
    MONTHS,
    comparison_available,
    editorial_facts,
    format_count,
    format_report_period,
    missing_comparison_text,
    writing_indicators,
)


LOGGER = logging.getLogger(__name__)


SECTIONS = (
    ("resumo_executivo", "Resumo executivo"),
    ("evolucao_periodo", "Evolução do período"),
    ("composicao_producao", "Composição da produção"),
    ("permanencia", "Permanência"),
    ("producao_procurador", "Produção por Procurador"),
    ("comparacao_periodo_anterior", "Comparação com período anterior"),
    ("sintese_pontos_atencao", "Síntese do período"),
    ("nota_metodologica", "Nota metodológica"),
)
AI_SECTIONS = tuple(key for key, _label in SECTIONS[:-1])
PROMPT_VERSION = "editorial-2026-10"
NUMBER = re.compile(r"(?<![\w/])\d{1,3}(?:[.]\d{3})*(?:[,\.]\d+)?%?(?![\w/])")
BAND_NUMBER = re.compile(r"\d+")


def build_ai_context(snapshot, *, data_corte=None):
    """Formatted facts for prose, with raw values kept apart from the wording."""
    meta = snapshot["metadados"]
    facts = editorial_facts(snapshot)
    coverage = snapshot.get("cobertura_historica") or {}
    return {
        "tipo_relatorio": meta["tipo"],
        "periodo": format_report_period(snapshot),
        "periodo_parcial": meta.get("periodo_parcial"),
        "ano": meta.get("ano"),
        "trimestre": meta.get("trimestre"),
        "data_corte": data_corte,
        "fatos_para_redacao": facts,
        "dados_tecnicos": {
            "indicadores_gerais": writing_indicators(
                snapshot.get("indicadores_gerais") or {}
            ),
            "composicao_producao": snapshot.get("composicao_producao") or {},
        },
        "cobertura": {
            "parcial": bool(meta.get("periodo_parcial")),
            "meses_ausentes": coverage.get("meses_ausentes") or [],
            "lacunas_no_ano": coverage.get("lacunas_no_ano") or [],
        },
        "metodologia": snapshot.get("nota_metodologica") or {},
    }


def _methodology(snapshot):
    entries = snapshot.get("nota_metodologica", {})
    return "\n\n".join(value for value in entries.values() if isinstance(value, str))


def _record(
    text="",
    *,
    generated=False,
    manual=False,
    model=None,
    review=False,
    unknown=None,
    prompt_version=None,
):
    record = {
        "texto": text or "",
        "gerado_por_ia": bool(generated),
        "editado_manualmente": bool(manual),
        "gerado_em": now() if generated else None,
        "editado_em": now() if manual else None,
        "modelo": model,
        "requer_revisao": bool(review),
        "valores_desconhecidos": unknown or [],
    }
    if prompt_version:
        record["prompt_version"] = prompt_version
    return record


def normalize_content(content, snapshot):
    """Upgrade the Phase 1 empty string structure without a schema migration."""
    source = content if isinstance(content, dict) else {}
    result = {}
    for key, _label in SECTIONS:
        current = source.get(key, {})
        if isinstance(current, str):
            current = {"texto": current}
        if not isinstance(current, dict):
            current = {}
        result[key] = _record(current.get("texto", ""))
        result[key].update(
            {field: current.get(field, result[key][field]) for field in result[key]}
        )
        if current.get("prompt_version"):
            result[key]["prompt_version"] = current["prompt_version"]
    if not result["nota_metodologica"]["texto"]:
        result["nota_metodologica"] = _record(_methodology(snapshot))
    return result


def _number_value(token):
    cleaned = token.strip().rstrip("%")
    if "." in cleaned and "," in cleaned:
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif "," in cleaned:
        cleaned = cleaned.replace(",", ".")
    elif cleaned.count(".") == 1 and len(cleaned.split(".")[1]) == 3:
        cleaned = cleaned.replace(".", "")
    else:
        cleaned = cleaned.replace(".", "") if cleaned.count(".") > 1 else cleaned
    try:
        return float(cleaned)
    except ValueError:
        return None


def _known_numbers(value, known):
    if isinstance(value, dict):
        for item in value.values():
            _known_numbers(item, known)
    elif isinstance(value, list):
        for item in value:
            _known_numbers(item, known)
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        known.add(round(float(value), 1))
        known.add(round(float(value)))


def _allowed_numbers(snapshot):
    known = set()
    _known_numbers(snapshot, known)
    known.update(range(0, 32))
    year = (snapshot.get("metadados") or {}).get("ano")
    if isinstance(year, (int, float)) and not isinstance(year, bool):
        known.add(float(year))
    for item in snapshot.get("faixas_permanencia") or []:
        if not isinstance(item, dict):
            continue
        for token in BAND_NUMBER.findall(str(item.get("faixa") or "")):
            known.add(float(token))
    return known


def validate_numbers(text, snapshot):
    known = _allowed_numbers(snapshot)
    unknown = []
    for token in NUMBER.findall(text or ""):
        value = _number_value(token)
        if value is not None and all(abs(value - item) > 0.11 for item in known):
            unknown.append(token)
    return sorted(set(unknown))[:12]


def annual_fallback_content(snapshot):
    """Short deterministic prose when the optional AI service is unavailable."""
    facts = editorial_facts(snapshot)
    indicators = facts["indicadores"]
    annual = facts.get("fatos_anuais") or {}
    balance = annual.get("saldo_fluxo", 0)
    sign = "+" if balance > 0 else ""
    peak_p = annual.get("pico_producao") or {}
    peak_d = annual.get("pico_distribuicoes") or {}
    closing = annual.get("ultimo_mes") or {}
    permanence = facts["permanencia"]
    procuradores = facts["procuradores"]
    more_60 = annual.get("quantidade_mais_60", 0)
    more_90 = annual.get("quantidade_mais_90", 0)
    period = facts["periodo"].replace("Acumulado de ", "").lower()
    bands = [
        item
        for item in snapshot.get("faixas_permanencia") or []
        if isinstance(item, dict)
        and item.get("faixa")
        and item.get("quantidade") is not None
    ]
    predominant_band = max(bands, key=lambda item: item["quantidade"], default=None)
    band_sentence = ""
    if predominant_band:
        band_sentence = (
            f" A maior concentração ocorreu na faixa de {predominant_band['faixa']}, "
            f"com {format_count(predominant_band['quantidade'])} registros."
        )
    procurador_fields = (
        "producao_minima",
        "producao_maxima",
        "distribuicoes_minimas",
        "distribuicoes_maximas",
        "participacao_pareceres_minima",
        "participacao_pareceres_maxima",
        "mediana_minima",
        "mediana_maxima",
    )
    if all(procuradores.get(field) is not None for field in procurador_fields):
        procurador_text = (
            f"A produção individual variou entre {procuradores['producao_minima']} e "
            f"{procuradores['producao_maxima']} registros, enquanto as distribuições "
            f"variaram entre {procuradores['distribuicoes_minimas']} e "
            f"{procuradores['distribuicoes_maximas']}. A participação de pareceres "
            f"oscilou entre {procuradores['participacao_pareceres_minima']} e "
            f"{procuradores['participacao_pareceres_maxima']}, e a mediana de "
            f"permanência entre {procuradores['mediana_minima']} e "
            f"{procuradores['mediana_maxima']}."
        )
    else:
        procurador_text = (
            "Não há dados individuais suficientes para análise no período."
        )
    return {
        "resumo_executivo": f"Este relatório consolida os indicadores de produção do MPC-PB no período de {period}. Foram registrados {indicators['producao']['display']} registros de produção e {indicators['distribuicoes']['display']} distribuições, com saldo de {sign}{balance:.0f} registros.",
        "evolucao_periodo": f"O fluxo no período analisado apresentou variações ao longo dos meses. O pico de distribuições ocorreu em {MONTHS[int(peak_d.get('mes', 1)) - 1]}, com {format_count(peak_d.get('distribuicoes'))} registros, enquanto o maior volume de produção foi registrado em {MONTHS[int(peak_p.get('mes', 1)) - 1]}, com {format_count(peak_p.get('producao'))}. No último mês disponível, foram registradas {format_count(closing.get('distribuicoes'))} distribuições e {format_count(closing.get('producao'))} produções.",
        "composicao_producao": f"A produção foi composta por {indicators['pareceres']['display']} pareceres e {indicators['cotas']['display']} cotas.",
        "permanencia": f"A mediana de permanência foi de {indicators['mediana']['display']}. Foram registrados {format_count(more_60)} registros com permanência superior a 60 dias.",
        "producao_procurador": procurador_text,
        "comparacao_periodo_anterior": missing_comparison_text(snapshot),
        "sintese_pontos_atencao": f"No acumulado de {period}, a produção registrou {indicators['producao']['display']} registros, frente a {indicators['distribuicoes']['display']} distribuições, resultando em saldo de {sign}{balance:.0f}. A mediana de permanência foi de {indicators['mediana']['display']}.{band_sentence}",
    }


def quarterly_fallback_content(snapshot):
    """Editorially complete prose when the optional AI is unavailable."""
    facts = editorial_facts(snapshot)
    indicators = facts["indicadores"]
    quarterly = facts.get("fatos_trimestrais") or {}
    period = facts["periodo"]
    balance = quarterly.get("saldo_fluxo")
    balance_text = f"{float(balance):+.0f}" if balance is not None else "—"
    peak_d = quarterly.get("pico_distribuicoes") or {}
    peak_p = quarterly.get("pico_producao") or {}
    monthly_positive = quarterly.get("meses_producao_superior_distribuicao")
    months = len((snapshot.get("serie_mensal") or []))
    permanence = quarterly.get("faixa_predominante_permanencia")
    more_60 = quarterly.get("quantidade_mais_60")
    more_90 = quarterly.get("quantidade_mais_90")
    procuradores = facts["procuradores"]
    comparison = facts["comparacao"]
    relation = quarterly.get("relacao_producao_distribuicoes")
    evolution = (
        f"No trimestre, o maior volume de distribuições ocorreu em {MONTHS[int(peak_d.get('mes', 1)) - 1]}, "
        f"com {format_count(peak_d.get('distribuicoes'))} registros, enquanto a produção atingiu "
        f"seu pico em {MONTHS[int(peak_p.get('mes', 1)) - 1]}, com {format_count(peak_p.get('producao'))} registros."
        if peak_d and peak_p
        else "A evolução mensal está apresentada nos indicadores consolidados do período."
    )
    if monthly_positive is not None and months:
        evolution += (
            f" A produção superou as distribuições em {monthly_positive} de {months} meses do período."
        )
    fields = (
        "producao_minima", "producao_maxima", "distribuicoes_minimas",
        "distribuicoes_maximas", "participacao_pareceres_minima",
        "participacao_pareceres_maxima", "mediana_minima", "mediana_maxima",
    )
    if all(procuradores.get(field) is not None for field in fields):
        procurador_text = (
            f"A produção individual variou entre {procuradores['producao_minima']} e {procuradores['producao_maxima']} registros, "
            f"enquanto as distribuições oscilaram entre {procuradores['distribuicoes_minimas']} e {procuradores['distribuicoes_maximas']}. "
            f"A participação de pareceres variou entre {procuradores['participacao_pareceres_minima']} e {procuradores['participacao_pareceres_maxima']}, "
            f"e a mediana de permanência entre {procuradores['mediana_minima']} e {procuradores['mediana_maxima']}."
        )
    else:
        procurador_text = "Não há dados individuais suficientes para análise no período."
    comparison_text = missing_comparison_text(snapshot)
    if comparison.get("disponivel"):
        distribution = comparison.get("distribuicoes") or {}
        production = comparison.get("producao") or {}
        relation_delta = comparison.get("producao_distribuicoes") or {}
        comparison_text = (
            f"Na comparação com o período anterior, as distribuições passaram de {distribution.get('anterior', '—')} para {distribution.get('atual', '—')} "
            f"(Dif. {distribution.get('delta', '—')}) e a produção de {production.get('anterior', '—')} para {production.get('atual', '—')} "
            f"(Dif. {production.get('delta', '—')}). A relação Produção/Distribuições variou {relation_delta.get('delta', '—')}."
        )
    long_text = ""
    if more_60 is not None:
        long_text = f" Foram registrados {format_count(more_60)} eventos acima de 60 dias, dos quais {format_count(more_90 or 0)} acima de 90 dias."
    band_text = f" A faixa predominante foi de {permanence}." if permanence else ""
    return {
        "resumo_executivo": (
            f"Este relatório consolida os indicadores de produção do MPC-PB no {period}. "
            f"No período, foram registrados {indicators['producao']['display']} atos de produção e "
            f"{indicators['distribuicoes']['display']} distribuições, resultando em saldo de {balance_text} registros."
        ),
        "evolucao_periodo": evolution,
        "composicao_producao": (
            f"{indicators['pareceres']['display']} pareceres e {indicators['cotas']['display']} cotas compuseram a produção do período."
        ),
        "permanencia": (
            f"A mediana de permanência foi de {indicators['mediana']['display']}." + band_text + long_text
        ),
        "producao_procurador": procurador_text,
        "comparacao_periodo_anterior": comparison_text,
        "sintese_pontos_atencao": (
            f"A produção ficou em {balance_text} registros em relação às distribuições, com relação Produção/Distribuições de "
            f"{format_count(relation) if relation is None else f'{float(relation):.1f}'.replace('.', ',') + '%'} ."
        ).replace("% .", "%.") + band_text + long_text,
    }


class InstitutionalReportContentService:
    def __init__(self, store):
        self.store = store
        self.reports = InstitutionalReportsStore(store)

    @staticmethod
    def _authorized(principal):
        if not getattr(principal, "administrator", False):
            raise PermissionError(
                "Apenas administradores podem editar o conteúdo institucional."
            )

    def _editable(self, identifier, principal):
        self._authorized(principal)
        report = self.reports.get(identifier)
        if not report or report["status"] not in ("RASCUNHO", "EM_REVISAO"):
            raise ValueError(
                "Relatório finalizado é somente leitura; crie uma nova versão."
            )
        return report

    def generate_all(self, identifier, principal):
        report = self._editable(identifier, principal)
        snapshot = report["snapshot_dados"]
        metadata = snapshot.get("metadados") or {}
        LOGGER.info(
            "RELATORIO_IA | etapa=generate_all | report_id=%s | tipo=%s | versao=%s | trimestre=%s | status=%s",
            identifier,
            metadata.get("tipo"),
            report.get("versao"),
            report.get("trimestre"),
            report.get("status"),
        )
        requested = [
            key
            for key in AI_SECTIONS
            if key != "comparacao_periodo_anterior" or comparison_available(snapshot)
        ]
        try:
            generated, model = ai_service.gerar_conteudo_relatorio_institucional(
                build_ai_context(snapshot, data_corte=report["data_corte"]),
                None if tuple(requested) == AI_SECTIONS else tuple(requested),
            )
        except ai_service.GeminiErro:
            fallback = annual_fallback_content if metadata.get("tipo") == "ANUAL" else quarterly_fallback_content
            generated, model = (fallback(snapshot), "fallback-deterministico")
        content = normalize_content(report["conteudo_estruturado"], snapshot)
        for section, text in generated.items():
            unknown = validate_numbers(text, snapshot)
            content[section] = _record(
                text,
                generated=True,
                model=model,
                review=bool(unknown),
                unknown=unknown,
                prompt_version=PROMPT_VERSION,
            )
        if not comparison_available(snapshot):
            content["comparacao_periodo_anterior"] = _record(
                missing_comparison_text(snapshot)
            )
        LOGGER.info(
            "RELATORIO_IA | etapa=persistencia_inicio | report_id=%s", identifier
        )
        updated = self.reports.save_content(
            identifier, content, principal.email, status="EM_REVISAO"
        )
        LOGGER.info(
            "RELATORIO_IA | etapa=persistencia_ok | report_id=%s | status=%s | secoes=%s",
            identifier,
            updated.get("status"),
            len(generated),
        )
        registrar_evento(
            self.store,
            evento="RELATORIO_INSTITUCIONAL_IA_GERADA",
            modulo="relatorios",
            acao="GERAR_CONTEUDO",
            principal=principal,
            entidade_tipo="relatorio_institucional",
            entidade_id=identifier,
            detalhes={"modelo": model, "secoes": len(generated)},
        )
        return updated

    def regenerate(self, identifier, section, principal):
        if section not in AI_SECTIONS:
            raise ValueError("Esta seção não é regenerada por IA.")
        report = self._editable(identifier, principal)
        snapshot = report["snapshot_dados"]
        content = normalize_content(report["conteudo_estruturado"], snapshot)
        if section == "comparacao_periodo_anterior" and not comparison_available(
            snapshot
        ):
            content[section] = _record(missing_comparison_text(snapshot))
            updated = self.reports.save_content(identifier, content, principal.email)
            return updated
        try:
            generated, model = ai_service.gerar_conteudo_relatorio_institucional(
                build_ai_context(snapshot, data_corte=report["data_corte"]), section
            )
        except ai_service.GeminiErro:
            fallback = (
                annual_fallback_content
                if (snapshot.get("metadados") or {}).get("tipo") == "ANUAL"
                else quarterly_fallback_content
            )
            generated, model = (fallback(snapshot), "fallback-deterministico")
        text = generated[section]
        unknown = validate_numbers(text, snapshot)
        content[section] = _record(
            text,
            generated=True,
            model=model,
            review=bool(unknown),
            unknown=unknown,
            prompt_version=PROMPT_VERSION,
        )
        updated = self.reports.save_content(identifier, content, principal.email)
        registrar_evento(
            self.store,
            evento="RELATORIO_INSTITUCIONAL_SECAO_REGENERADA",
            modulo="relatorios",
            acao="REGENERAR_SECAO",
            principal=principal,
            entidade_tipo="relatorio_institucional",
            entidade_id=identifier,
            detalhes={"secao": section, "modelo": model},
        )
        return updated

    def save_manual(self, identifier, texts, principal):
        report = self._editable(identifier, principal)
        content = normalize_content(
            report["conteudo_estruturado"], report["snapshot_dados"]
        )
        changed = []
        for section, text in (texts or {}).items():
            if section not in content or text == content[section]["texto"]:
                continue
            unknown = validate_numbers(text, report["snapshot_dados"])
            prior = content[section]
            content[section] = _record(
                text,
                generated=prior["gerado_por_ia"],
                manual=True,
                model=prior["modelo"],
                review=bool(unknown),
                unknown=unknown,
                prompt_version=prior.get("prompt_version"),
            )
            changed.append(section)
        updated = self.reports.save_content(identifier, content, principal.email)
        if changed:
            registrar_evento(
                self.store,
                evento="RELATORIO_INSTITUCIONAL_CONTEUDO_EDITADO",
                modulo="relatorios",
                acao="SALVAR_CONTEUDO",
                principal=principal,
                entidade_tipo="relatorio_institucional",
                entidade_id=identifier,
                detalhes={"secoes": changed},
            )
        return updated

    def mark_for_review(self, identifier, principal):
        report = self._editable(identifier, principal)
        content = normalize_content(
            report["conteudo_estruturado"], report["snapshot_dados"]
        )
        if not any(item["texto"].strip() for item in content.values()):
            raise ValueError(
                "Preencha ao menos uma seção antes de marcar para revisão."
            )
        updated = self.reports.save_content(
            identifier, content, principal.email, status="EM_REVISAO"
        )
        registrar_evento(
            self.store,
            evento="RELATORIO_INSTITUCIONAL_EM_REVISAO",
            modulo="relatorios",
            acao="MARCAR_REVISAO",
            principal=principal,
            entidade_tipo="relatorio_institucional",
            entidade_id=identifier,
            detalhes={},
        )
        return updated
