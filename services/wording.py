"""Institutional Portuguese. No member identity or legal rule is hard-coded here."""

from datetime import date
from copy import deepcopy
import re
from services.placeholders import reject_placeholders

MONTHS = (
    "",
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
FEMININE = {
    "Procurador": "Procuradora",
    "Procurador-Geral": "Procuradora-Geral",
    "Subprocurador-Geral": "Subprocuradora-Geral",
    "Ouvidor": "Ouvidora",
    "Corregedor": "Corregedora",
}


def parsed(value):
    result = value if isinstance(value, date) else date.fromisoformat(value)
    if not 1000 <= result.year <= 9999:
        raise ValueError("O ano deve ter quatro algarismos.")
    return result


def long_date(value):
    value = parsed(value)
    return f"{value.day} de {MONTHS[value.month]} de {value.year:04d}"


def period(start, end):
    start, end = parsed(start), parsed(end)
    if end < start:
        raise ValueError("A data final não pode ser anterior à inicial.")
    if start == end:
        return long_date(start)
    if start.year != end.year:
        return f"{long_date(start)} a {long_date(end)}"
    if start.month != end.month:
        return f"{start.day} de {MONTHS[start.month]} a {long_date(end)}"
    return f"{start.day} a {long_date(end)}"


def role(name, gender):
    canonical = {v: k for k, v in FEMININE.items()}.get(name, name)
    return FEMININE.get(canonical, canonical) if gender == "feminino" else canonical


def article(person):
    return "a" if person["genero"] == "feminino" else "o"


def reason_text(substitution):
    text = substitution.get("motivo_texto", "")
    if substitution.get("motivo_id") and "{do_titular}" in text:
        holder = substitution.get("titular")
        if not holder:
            raise ValueError("Selecione o titular para completar a redação do motivo.")
        text = text.replace(
            "{do_titular}", "da titular" if article(holder) == "a" else "do titular"
        )
    reject_placeholders(text)
    return text


def normalized_payload(payload):
    result = deepcopy(payload)
    for sub in result.get("substituicoes", []):
        sub["motivo_texto"] = reason_text(sub)
    reject_placeholders(result)
    return result


def signer_role(payload):
    if payload.get("qualidade_outro"):
        return payload["qualidade_outro"].strip()
    return role("Procurador-Geral", payload["signatario"]["genero"])


def substitution_text(s, signer, index, previous=None):
    holder, substitute = s["titular"], s["substituto"]
    function = s["funcao"]
    sub_role = (
        role(substitute["funcao"], substitute["genero"])
        if function == "Procurador-Geral"
        and substitute["funcao"] == "Subprocurador-Geral"
        else role(substitute["cargo_base"], substitute["genero"])
    )
    lead = "R E S O L V E" + (", ainda," if index else "")
    lead += f" designar {article(substitute)} {sub_role} do Ministério Público de Contas {substitute['nome']}, para substituir "
    if function == "Procurador-Geral" and holder["id"] == signer["id"]:
        target = f"{'esta' if article(holder) == 'a' else 'este'} {role(function, holder['genero'])}"
    elif function in ("Ouvidor", "Corregedor"):
        target = f"{article(holder)} {role(holder['cargo_base'], holder['genero'])} {holder['nome']}, na função de {role(function, holder['genero'])}"
    else:
        target = (
            f"{article(holder)} {role(function, holder['genero'])} {holder['nome']}"
        )
        if (
            s["assento"]
            and s["assento"] != "Não se aplica"
            and function != "Procurador-Geral"
        ):
            target += f", com assento {'no' if s['assento'] == 'Tribunal Pleno' else 'na'} {s['assento']}"
    same = (
        previous and s["inicio"] == previous["inicio"] and s["fim"] == previous["fim"]
    )
    when = (
        "durante o período acima mencionado"
        if s.get("decorrente") and same
        else f"no período de {period(s['inicio'], s['fim'])}"
    )
    reason = "" if s.get("decorrente") else ", " + reason_text(s)
    text = lead + target + ", " + when + reason
    return text if text.endswith((".", "!", "?")) else text + "."


def compose(payload):
    payload = normalized_payload(payload)
    signer = payload["signatario"]
    quality = signer_role(payload)
    header = f"{article(signer).upper()} {quality.upper()}"
    if payload.get("em_exercicio") and not payload.get("qualidade_outro"):
        header += " EM EXERCÍCIO"
    bases = list(
        dict.fromkeys(
            s["base_legal"].strip().rstrip(".") for s in payload["substituicoes"]
        )
    )
    intro = (
        header
        + " do Ministério Público de Contas do Estado da Paraíba, no uso de suas atribuições legais e nos termos do "
        + "; e do ".join(bases)
        + ","
    )
    body = [
        substitution_text(s, signer, i, payload["substituicoes"][i - 1] if i else None)
        for i, s in enumerate(payload["substituicoes"])
    ]
    signature = quality + " do Ministério Público de Contas da Paraíba"
    if payload.get("em_exercicio") and not payload.get("qualidade_outro"):
        signature += " em exercício"
    result = {
        "intro": intro,
        "body": body,
        "signature_name": signer["nome"].upper(),
        "signature_role": signature,
        "notes": list(
            dict.fromkeys(
                s["nota"].strip()
                for s in payload["substituicoes"]
                if s.get("nota", "").strip()
            )
        ),
    }
    result.update(payload.get("manual", {}))
    return result


def preview_text(payload, number=None):
    c = compose(payload)
    title = f"PORTARIA – PROGE N.º {number if number else '[PRÉVIA]'}/{parsed(payload['data']).year}"
    return "\n\n".join(
        [
            title,
            "João Pessoa, " + long_date(payload["data"]) + ".",
            c["intro"],
            *c["body"],
            c["signature_name"],
            c["signature_role"],
            *c["notes"],
        ]
    )


def validate(payload):
    payload = normalized_payload(payload)
    parsed(payload["data"])
    signer = payload.get("signatario")
    if not signer or not signer.get("nome", "").strip():
        raise ValueError("Signatário obrigatório.")
    substitutions = payload.get("substituicoes", [])
    if not substitutions:
        raise ValueError("Adicione pelo menos uma substituição.")
    for i, s in enumerate(substitutions):
        for field in ("titular", "substituto"):
            person = s.get(field)
            if not person or not person.get("nome", "").strip():
                raise ValueError(f"Substituição {i+1}: {field} obrigatório.")
            if person.get("genero") not in ("feminino", "masculino"):
                raise ValueError("Gênero gramatical inválido.")
        if s["titular"]["id"] == s["substituto"]["id"]:
            raise ValueError("Titular e substituto não podem ser a mesma pessoa.")
        period(s["inicio"], s["fim"])
        if not s.get("funcao", "").strip() or s["funcao"] == "Outro":
            raise ValueError("Informe a função substituída.")
        if not s.get("base_legal", "").strip():
            raise ValueError("Base legal obrigatória.")
        if s["funcao"] == "Subprocurador-Geral" and s["assento"] not in (
            "1ª Câmara",
            "2ª Câmara",
        ):
            raise ValueError("Câmara obrigatória para Subprocuradoria-Geral.")
        if not s.get("decorrente") and not s.get("motivo_texto", "").strip():
            raise ValueError("Motivo obrigatório.")
        if s.get("decorrente") and not i:
            raise ValueError("A primeira substituição deve ter motivo próprio.")
    content = compose(payload)
    if (
        any(
            not content[k].strip()
            for k in ("intro", "signature_name", "signature_role")
        )
        or not content["body"]
        or any(not p.strip() for p in content["body"])
    ):
        raise ValueError("A prévia não pode ter campos obrigatórios vazios.")
    for text in [content["intro"], *content["body"], *content["notes"]]:
        if re.search(r"\b\d{1,2}/\d{1,2}/\d{1,3}\b|\bde \d{3}\b", text):
            raise ValueError("A prévia contém ano truncado.")
