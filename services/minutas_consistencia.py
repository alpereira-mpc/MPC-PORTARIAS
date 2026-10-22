"""Regras de conteúdo de PCA, sem dependência de modelos ou estilos DOCX."""

from copy import deepcopy
import re
import unicodedata


def chave(text):
    return " ".join(unicodedata.normalize("NFKD", str(text or ""))
                    .encode("ascii", "ignore").decode().lower().split())


_NAME = r"[A-ZÀ-Ý][A-Za-zÀ-ÿ'’\-]+(?:\s+(?:(?:de|da|do|dos|das|e)\s+)?[A-ZÀ-Ý][A-Za-zÀ-ÿ'’\-]+){1,7}"
_ROLE = (r"(?:[Pp]refeit[oa](?:\s+[Mm]unicipal)?|[Pp]residente\s+da\s+"
         r"[Cc][âa]mara(?:\s+[Mm]unicipal)?|[Dd]iretor(?:a)?[- ]Presidente|"
         r"[Ss]ecret[aá]ri[oa](?:\s+[Mm]unicipal|\s+[Ee]stadual)?|"
         r"[Oo]rdenador(?:a)?\s+de\s+[Dd]espesas|[Gg]estor(?:a)?(?:\s+de\s+RPPS)?)")
_NARRATIVE = re.compile(rf"(?:[Ss]r[a]?\.?\s+)?(?P<nome>{_NAME}),\s*(?P<cargo>(?i:{_ROLE}))\b")
_ROLE_FIRST = re.compile(rf"(?i:{_ROLE})\s+(?:[Ss]r[a]?\.?\s+)?(?P<nome>{_NAME})(?=[,.;\n]|$)")
_LABEL = re.compile(r"\b(?:respons[aá]ve(?:l|is)|gestor(?:a)?\s+respons[aá]vel|"
                    r"dirigente\s+respons[aá]vel|ordenador(?:a)?\s+de\s+despesas?)\s*[:\-–—]\s*(.+)$", re.I)
_PERIOD = re.compile(r"\b\d{2}/\d{2}/\d{4}\s*(?:a|até|[-–])\s*\d{2}/\d{2}/\d{4}\b")


def candidatos(text, exercise=None):
    """Somente relações explícitas nome/cargo; preserva o período literal."""
    result = []
    for line in re.split(r"\n|(?<!Sr\.)(?<!Sra\.)(?<=[.])\s+(?=[A-ZÀ-Ý])", str(text or "")):
        if not linha_do_exercicio(line, exercise):
            continue
        period = _PERIOD.search(line)
        label = _LABEL.search(line)
        values = []
        if label:
            value = _PERIOD.sub("", label[1]).strip(" ()-–;.")
            value = re.sub(r"\s*\(?exerc[ií]cio\s*(?:de\s*)?\d{4}\)?", "", value, flags=re.I)
            for part in re.split(r"\s*;\s*", value):
                part = re.sub(r"^(?:Sr[a]?\.?\s+)", "", part)
                if re.fullmatch(_NAME, part):
                    values.append(part)
        values.extend(match["nome"] for match in _NARRATIVE.finditer(line))
        values.extend(match["nome"] for match in _ROLE_FIRST.finditer(line))
        for value in values:
            result.append({"nome": value, "periodo": period[0] if period else "",
                           "trecho": line.strip(), "coletivo": bool(label and ";" in label[1])})
    return result


def linha_do_exercicio(line, exercise=None):
    line = str(line or "")
    if re.search(r"\b(?:atual|ex[- ]gestor|ex[- ]prefeito|advogado|procurador|auditor|relator)\b", chave(line)):
        return False
    years = re.findall(r"\b(?:19|20)\d{2}\b", line)
    return not (exercise and years and str(exercise) not in years)


def resolver_responsavel(target, sources, fallback):
    records = []
    for source in sources:
        for unit in source.get("conteudo", ()):
            if isinstance(unit, dict):
                for record in candidatos(unit.get("texto"), target.get("exercicio")):
                    records.append({**record, "fonte_id": source.get("fonte_id") or source.get("id"),
                                    "pagina": unit.get("pagina")})
    names = {chave(r["nome"]): r["nome"] for r in records}
    explicit = str(target.get("responsavel") or "").strip()
    if not linha_do_exercicio(explicit, target.get("exercicio")):
        explicit = ""
    if not linha_do_exercicio(fallback, target.get("exercicio")):
        fallback = ""
    if chave(explicit) in {"nao informado", "nao identificavel", "nao identificado", "a identificar", "pendente"}:
        explicit = ""
    if explicit and names and any(key not in chave(explicit) for key in names):
        return "", ["Responsáveis dos metadados e dos documentos divergem; confira nomes e períodos."], records
    periods = {r["periodo"] for r in records if r["periodo"]}
    if periods and any(period not in periods for period in _PERIOD.findall(explicit)):
        return "", ["Os períodos de responsabilidade dos metadados e dos documentos divergem; revise a identificação."], records
    if len(names) > 1:
        if not all(any(chave(r["nome"]) == name and (r["periodo"] or r["coletivo"]) for r in records) for name in names):
            return "", ["Há mais de um responsável possível sem delimitação segura de períodos; revise a identificação."], records
        values = dict.fromkeys(r["nome"] + (f" ({r['periodo']})" if r["periodo"] else "") for r in records if r["periodo"] or r["coletivo"])
        return "; ".join(values), [], records
    if len(names) == 1 and periods:
        return "; ".join(dict.fromkeys(f"{r['nome']} ({r['periodo']})" for r in records if r["periodo"])), [], records
    return explicit or next(iter(names.values()), fallback or ""), [], records


def titulo_sancionatorio(title):
    title = re.sub(r"^[\d\W]+", "", chave(title)).strip()
    title = re.sub(r"^[ivxlcdm]+[.)\-:]\s*", "", title)
    return bool(re.fullmatch(
        r"(?:(?:da|das|do|dos) )?(?:analise sancionatoria|dosimetria(?: da sancao| das sancoes| da pena)?|"
        r"aplicacao de (?:penalidades|sancoes|multas)|sancoes(?: e penalidades)?|"
        r"responsabilizacao e sancoes|penalidades)", title))


def integrar_sancoes(structure):
    """Migra o campo de controle legado para tópico vinculado, sem excluir mérito."""
    result = deepcopy(structure)
    topics = result.get("topicos_irregularidades") or []
    body_fields = [result.get(k, "") for k in ("relatorio", "fundamentacao_geral", "conclusao_pedidos")]
    body_fields.extend(t.get("texto", "") for t in topics)
    body_fields.extend(p for k in ("relatorio_paragrafos", "fundamentacao_geral_paragrafos") for p in result.get(k, []))
    body_fields.extend(p for t in topics for p in t.get("paragrafos", []))
    if any(titulo_sancionatorio(line) for text in body_fields for line in str(text).splitlines()):
        raise ValueError("Capítulo sancionatório autônomo no conteúdo: integre a análise à irregularidade e gere novamente.")
    for topic in topics:
        if titulo_sancionatorio(topic.get("titulo")):
            raise ValueError("Capítulo sancionatório autônomo: integre a análise à irregularidade correspondente e gere novamente.")
    sanction = result.get("analise_sancionatoria") or {}
    text = str(sanction.get("texto") or "").strip()
    ids = set(sanction.get("irregularidades_ids") or [])
    # O campo permanece para as verificações jurídicas; nunca vira capítulo.
    if text:
        matches = [t for t in topics if ids and ids <= set(t.get("irregularidades_ids") or [])]
        if len(matches) == 1:
            topic = matches[0]
            paragraphs = topic.get("paragrafos") or str(topic.get("texto") or "").split("\n\n")
            if chave(text) not in chave(" ".join(paragraphs)):
                topic["paragrafos"] = [*paragraphs, text]
                topic["texto"] = "\n\n".join(topic["paragrafos"])
        elif sanction.get("conclusao") == "MULTA_CABIVEL":
            raise ValueError("Fundamentação da multa sem vínculo único com a questão jurídica; individualize a análise nos tópicos.")
    return result


def pendencias_identidade(structure, target):
    pending = list(target.get("pendencias_identificacao") or [])
    responsible = str(target.get("responsavel") or "").strip()
    if not responsible or chave(responsible) in {"nao informado", "nao identificado"}:
        pending.append("Identifique o responsável pelas contas e seu período nos documentos do processo antes de exportar o DOCX.")
    else:
        # Examina somente relações explícitas de responsabilidade, não nomes soltos.
        body = "\n".join(str(structure.get(k) or "") for k in ("relatorio", "fundamentacao_geral", "conclusao_pedidos"))
        body += "\n" + "\n".join(t.get("texto", "") for t in structure.get("topicos_irregularidades", []))
        body += "\n" + "\n".join(
            str(p) for key in ("relatorio_paragrafos", "fundamentacao_geral_paragrafos", "pedidos")
            for p in structure.get(key, [])
        )
        body += "\n" + "\n".join(str(p) for t in structure.get("topicos_irregularidades", []) for p in t.get("paragrafos", []))
        for record in candidatos(body, target.get("exercicio")):
            if (chave(record["nome"]) not in chave(responsible)
                    or (record["periodo"] and _PERIOD.search(responsible) and record["periodo"] not in responsible)):
                pending.append("O corpo identifica responsável diferente do cabeçalho; confira a pessoa e o período antes de exportar.")
                break
    return list(dict.fromkeys(pending))
