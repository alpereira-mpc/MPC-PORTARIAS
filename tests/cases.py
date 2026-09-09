from copy import deepcopy


def sample(store, number=8):
    members = {p["id"]: p for p in store.catalog("procuradores")}
    bases = {b["funcao"]: b for b in store.catalog("bases_legais")}

    def sub(holder, substitute, function, seat, start, end, dependent=False):
        return dict(
            titular=deepcopy(members[holder]),
            substituto=deepcopy(members[substitute]),
            funcao=function,
            assento=seat,
            inicio=start,
            fim=end,
            motivo_id=1,
            motivo_texto="por motivo de gozo de férias regulamentares",
            decorrente=dependent,
            mesmo_periodo=dependent,
            base_legal=bases[function]["texto"],
            nota=bases[function]["nota"],
        )

    p = dict(
        data="2026-09-08",
        signatario=deepcopy(members[1]),
        em_exercicio=False,
        qualidade_outro="",
        substituicoes=[
            sub(2, 7, "Subprocurador-Geral", "1ª Câmara", "2026-09-08", "2026-09-14")
        ],
    )
    if number == 5:
        p.update(
            data="2026-05-19",
            substituicoes=[
                sub(4, 6, "Ouvidor", "Não se aplica", "2026-05-18", "2026-05-27")
            ],
        )
    elif number == 6:
        p.update(
            data="2026-06-26",
            substituicoes=[
                sub(
                    1,
                    2,
                    "Procurador-Geral",
                    "Tribunal Pleno",
                    "2026-06-29",
                    "2026-07-09",
                ),
                sub(
                    2,
                    6,
                    "Subprocurador-Geral",
                    "1ª Câmara",
                    "2026-06-29",
                    "2026-07-09",
                    True,
                ),
            ],
        )
    elif number == 4:
        p.update(
            data="2026-04-14",
            signatario=deepcopy(members[3]),
            em_exercicio=True,
            substituicoes=[
                sub(4, 6, "Ouvidor", "Não se aplica", "2026-04-13", "2026-04-30")
            ],
        )
    return p
