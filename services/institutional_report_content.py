"""Human-reviewed textual content for frozen institutional report snapshots."""

import re

from database.institutional_reports import InstitutionalReportsStore
from database.store import now
from services import ai_service
from services.audit import registrar_evento
from services.institutional_presentation import (
    comparison_available,
    editorial_facts,
    format_report_period,
    missing_comparison_text,
)


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
            "indicadores_gerais": snapshot.get("indicadores_gerais") or {},
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
        requested = [
            key
            for key in AI_SECTIONS
            if key != "comparacao_periodo_anterior" or comparison_available(snapshot)
        ]
        generated, model = ai_service.gerar_conteudo_relatorio_institucional(
            build_ai_context(snapshot, data_corte=report["data_corte"]),
            None if tuple(requested) == AI_SECTIONS else tuple(requested),
        )
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
        updated = self.reports.save_content(
            identifier, content, principal.email, status="EM_REVISAO"
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
        generated, model = ai_service.gerar_conteudo_relatorio_institucional(
            build_ai_context(snapshot, data_corte=report["data_corte"]),
            section,
        )
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
