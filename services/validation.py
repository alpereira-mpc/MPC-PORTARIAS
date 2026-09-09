from services.wording import parsed


def overlaps(a, b):
    return parsed(a["inicio"]) <= parsed(b["fim"]) and parsed(b["inicio"]) <= parsed(
        a["fim"]
    )


def warnings_for(payload, other_payloads=()):
    messages = []
    subs = payload["substituicoes"]
    for i, sub in enumerate(subs):
        if parsed(payload["data"]) > parsed(sub["inicio"]):
            messages.append(
                f"Substituição {i+1}: afastamento começa antes da data da Portaria."
            )
        if (
            sub["funcao"] == "Procurador-Geral"
            and sub["substituto"]["funcao"] != "Subprocurador-Geral"
        ):
            messages.append(
                f"Substituição {i+1}: substituto da Procuradoria-Geral não está cadastrado como Subprocurador-Geral."
            )
        if (
            sub.get("decorrente")
            and i
            and sub["titular"]["id"] != subs[i - 1]["substituto"]["id"]
        ):
            messages.append(
                f"Substituição {i+1}: titular não corresponde ao substituto anterior."
            )
        for other in subs[:i]:
            if (
                overlaps(sub, other)
                and sub["substituto"]["id"] == other["substituto"]["id"]
            ):
                messages.append(
                    "Um mesmo procurador acumula substituições em períodos coincidentes."
                )
            if overlaps(sub, other) and (
                (
                    sub["substituto"]["id"] == other["titular"]["id"]
                    and not other.get("decorrente")
                )
                or (
                    other["substituto"]["id"] == sub["titular"]["id"]
                    and not sub.get("decorrente")
                )
            ):
                messages.append(
                    "Um substituto também está indicado em afastamento no mesmo período."
                )
        for prior in other_payloads:
            for other in prior["substituicoes"]:
                if overlaps(sub, other) and (
                    {sub["titular"]["id"], sub["substituto"]["id"]}
                    & {other["titular"]["id"], other["substituto"]["id"]}
                ):
                    messages.append(
                        "Há Portaria finalizada envolvendo estes procuradores no mesmo período. Confira o histórico."
                    )
    return list(dict.fromkeys(messages))
