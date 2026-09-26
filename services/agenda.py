"""Central catalogs and local institutional availability rules."""

from datetime import datetime, time, timedelta
import hashlib
import json
import unicodedata

TYPES = {"EVENTO": "Evento", "REUNIAO": "Reunião", "DESPACHO": "Despacho"}
STATUSES = ("Agendado", "Confirmado", "Realizado", "Cancelado")
EVENT_TYPES = (
    "Congresso",
    "Seminário",
    "Curso",
    "Palestra",
    "Solenidade",
    "Audiência Pública",
    "Sessão Especial",
    "Evento Institucional",
    "Outro",
)
MEETING_WITH = (
    "Presidência do TCE-PB",
    "Conselheiro",
    "Conselheiro Substituto",
    "Procurador do MPC-PB",
    "Secretaria do TCE-PB",
    "Prefeitura",
    "Câmara Municipal",
    "Ministério Público Estadual",
    "Ministério Público Federal",
    "Tribunal de Justiça",
    "Órgão Estadual",
    "Entidade Externa",
    "Outro",
)
LOCATIONS = {
    "EVENTO": (
        "TCE-PB",
        "Tribunal Pleno",
        "1ª Câmara",
        "2ª Câmara",
        "Auditório",
        "Sala de Reuniões",
        "Videoconferência",
        "Externo",
        "Outro",
    ),
    "REUNIAO": (
        "Gabinete",
        "Sala de Reuniões",
        "Presidência do TCE-PB",
        "Tribunal Pleno",
        "1ª Câmara",
        "2ª Câmara",
        "Auditório",
        "Videoconferência",
        "Externo",
        "Outro",
    ),
    "DESPACHO": ("Gabinete", "Sala de Reuniões", "Videoconferência", "Outro"),
}
RULES = (
    {
        "key": "bradson",
        "name": "Bradson Tibério Luna Camelo",
        "weekday": 1,
        "activity": "Sessão da 2ª Câmara",
        "types": ("REUNIAO", "DESPACHO"),
        "confirmation": True,
    },
    {
        "key": "elvira",
        "name": "Elvira Samara Pereira de Oliveira",
        "weekday": 2,
        "activity": "Sessão do Tribunal Pleno",
        "types": ("REUNIAO", "DESPACHO"),
        "confirmation": True,
    },
    {
        "key": "isabella",
        "name": "Isabella Barbosa Marinho Falcão",
        "weekday": 3,
        "activity": "Sessão da 1ª Câmara",
        "types": ("REUNIAO", "DESPACHO"),
        "confirmation": True,
    },
)


def normalized(value):
    return " ".join(
        "".join(
            c
            for c in unicodedata.normalize("NFKD", value)
            if not unicodedata.combining(c)
        )
        .casefold()
        .split()
    )


def institutional(member_ids, kind, day, bindings):
    return [
        r
        for r in RULES
        if bindings.get(r["key"]) in member_ids
        and day.weekday() == r["weekday"]
        and kind in r["types"]
    ]


def interval(record):
    start = datetime.fromisoformat(record["inicio"])
    end = datetime.fromisoformat(record["fim"]) if record.get("fim") else start
    if record.get("sem_hora"):
        return datetime.combine(start.date(), time.min), datetime.combine(
            end.date() + timedelta(days=1), time.min
        )
    # Without an end time, compare the instant at minute precision; no invented duration in UI.
    return start, end if end > start else start + timedelta(minutes=1)


def conflicts(record, others):
    if record["situacao"] == "Cancelado":
        return []
    start, end = interval(record)
    return [
        r
        for r in others
        if r["id"] != record.get("id")
        and r["situacao"] != "Cancelado"
        and set(r["procuradores"]) & set(record["procuradores"])
        and interval(r)[0] < end
        and interval(r)[1] > start
    ]


def validate(record):
    if record["tipo"] not in TYPES or record["situacao"] not in STATUSES:
        raise ValueError("Tipo ou situação inválida.")
    ids = record["procuradores"]
    if (
        not ids
        or len(set(ids)) != len(ids)
        or (record["tipo"] == "DESPACHO" and len(ids) != 1)
    ):
        raise ValueError(
            "Selecione os membros do compromisso; despacho exige um único membro."
        )
    start = datetime.fromisoformat(record["inicio"])
    end = datetime.fromisoformat(record["fim"]) if record.get("fim") else None
    if end and (end < start or (end == start and not record.get("sem_hora"))):
        raise ValueError("O término deve ser posterior ao início.")
    if record["tipo"] != "EVENTO" and (
        record.get("sem_hora") or (end and end.date() != start.date())
    ):
        raise ValueError(
            "Reuniões e despachos exigem horário e devem terminar no mesmo dia."
        )
    required = {
        "EVENTO": ("titulo", "categoria"),
        "REUNIAO": ("titulo", "reuniao_com"),
        "DESPACHO": ("processo",),
    }[record["tipo"]]
    if any(not record.get(k, "").strip() for k in (*required, "local")):
        raise ValueError("Preencha os campos obrigatórios e os complementos de Outro.")


MAX_DIAS_ANALISE = 31


def validar_intervalo_analise(inicio, fim):
    if fim < inicio:
        raise ValueError("A data final não pode ser anterior à data inicial.")
    if (fim - inicio).days + 1 > MAX_DIAS_ANALISE:
        raise ValueError("O período analisado pode ter no máximo 31 dias.")


def contexto_periodo(inicio, fim, compromissos, afastamentos, nomes):
    """Facts for an inclusive date range. The caller already loaded these records."""
    exclusive = fim + timedelta(days=1)
    items = [
        row
        for row in compromissos or []
        if not row.get("afastamento")
        and row.get("situacao") != "Cancelado"
        and _no_periodo(row, inicio, exclusive)
    ]
    leaves = [
        row
        for row in afastamentos or []
        if not row.get("cancelado") and _afastamento_no_periodo(row, inicio, fim)
    ]
    por_dia = {
        (inicio + timedelta(days=offset)).isoformat(): 0
        for offset in range((fim - inicio).days + 1)
    }
    for row in items:
        for day in _dias_compromisso(row):
            if day.isoformat() in por_dia:
                por_dia[day.isoformat()] += 1
    for leave in leaves:
        for day in _dias_afastamento(leave, inicio, fim):
            por_dia[day.isoformat()] += 1
    peak = max(por_dia.values(), default=0)
    context = {
        "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "total_compromissos": len(items),
        "total_afastamentos": len(leaves),
        "compromissos_sem_horario": sum(1 for row in items if row.get("sem_hora")),
        "quantidade_por_dia": por_dia,
        "dias_maior_concentracao": [
            day for day, count in por_dia.items() if peak and count == peak
        ],
        "compromissos": [_compromisso_resumo(row, nomes) for row in items[:80]],
        "afastamentos": [_afastamento_resumo(row, nomes) for row in leaves[:40]],
        "sobreposicoes_compromissos": _sobreposicoes(items, nomes)[:30],
        "coincidencias_afastamento_compromisso": _coincidencias(items, leaves, nomes)[
            :30
        ],
    }
    omitted = max(0, len(items) - 80) + max(0, len(leaves) - 40)
    if omitted:
        context["registros_omitidos"] = omitted
    signature = hashlib.sha256(
        json.dumps(context, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return context, signature


def leitura_analise_salva(stored, inicio, fim, assinatura):
    """Return the saved text for this period and whether its data changed."""
    if not isinstance(stored, dict):
        return None, False
    if stored.get("inicio") != inicio or stored.get("fim") != fim:
        return None, False
    text = str(stored.get("texto") or "").strip()
    if not text:
        return None, False
    return text, stored.get("assinatura") != assinatura


def _no_periodo(row, start, end):
    try:
        begin = datetime.fromisoformat(row["inicio"])
    except (KeyError, TypeError, ValueError):
        return False
    finish = begin
    if row.get("fim"):
        try:
            finish = datetime.fromisoformat(row["fim"])
        except (TypeError, ValueError):
            finish = begin
    return begin < datetime.combine(end, time.min) and finish >= datetime.combine(
        start, time.min
    )


def _afastamento_no_periodo(row, start, last):
    try:
        begin = datetime.fromisoformat(row["data_inicio"]).date()
        end = datetime.fromisoformat(row["data_fim"]).date()
    except (KeyError, TypeError, ValueError):
        return False
    return begin <= last and end >= start


def _dias_afastamento(row, inicio, fim):
    try:
        first = datetime.fromisoformat(row["data_inicio"]).date()
        last = datetime.fromisoformat(row["data_fim"]).date()
    except (KeyError, TypeError, ValueError):
        return []
    current = max(first, inicio)
    end = min(last, fim)
    days = []
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return days


def _dias_compromisso(row):
    start = datetime.fromisoformat(row["inicio"]).date()
    if not row.get("fim"):
        return [start]
    finish = datetime.fromisoformat(row["fim"])
    last = finish.date()
    if not row.get("sem_hora") and finish.time() == time.min and last > start:
        last -= timedelta(days=1)
    if last < start:
        last = start
    days = []
    current = start
    while current <= last:
        days.append(current)
        current += timedelta(days=1)
    return days


def _hora(value, untimed):
    if untimed or not value:
        return None
    try:
        return datetime.fromisoformat(value).strftime("%H:%M")
    except (TypeError, ValueError):
        return None


def _nomes(ids, nomes):
    found = []
    for identifier in ids or []:
        name = (nomes or {}).get(identifier)
        if name and name not in found:
            found.append(str(name)[:80])
    return ", ".join(found)


def _titulo(row):
    text = str(
        row.get("titulo")
        or row.get("processo")
        or TYPES.get(row.get("tipo"), "Compromisso")
    )
    return " ".join(text.split())[:120]


def _compromisso_resumo(row, nomes):
    return {
        "data": row["inicio"][:10],
        "inicio": _hora(row.get("inicio"), row.get("sem_hora")),
        "fim": _hora(row.get("fim"), row.get("sem_hora")),
        "titulo": _titulo(row),
        "responsavel": _nomes(row.get("procuradores"), nomes),
        "tipo": TYPES.get(row.get("tipo"), ""),
    }


def _afastamento_resumo(row, nomes):
    return {
        "inicio": row.get("data_inicio"),
        "fim": row.get("data_fim"),
        "motivo": str(row.get("motivo") or "")[:80],
        "responsavel": _nomes([row.get("procurador_id")], nomes),
    }


def _sobreposicoes(items, nomes):
    timed = [row for row in items if not row.get("sem_hora")]
    found = []
    for index, left in enumerate(timed):
        left_start, left_end = interval(left)
        for right in timed[index + 1 :]:
            if not (
                set(left.get("procuradores") or [])
                & set(right.get("procuradores") or [])
            ):
                continue
            right_start, right_end = interval(right)
            if left_start < right_end and right_start < left_end:
                moment = max(left_start, right_start)
                found.append(
                    {
                        "data": moment.date().isoformat(),
                        "itens": [
                            _compromisso_resumo(left, nomes),
                            _compromisso_resumo(right, nomes),
                        ],
                    }
                )
    return found


def _coincidencias(items, leaves, nomes):
    found = []
    for leave in leaves:
        holder = leave.get("procurador_id")
        try:
            leave_start = datetime.fromisoformat(leave["data_inicio"]).date()
            leave_end = datetime.fromisoformat(leave["data_fim"]).date()
        except (KeyError, TypeError, ValueError):
            continue
        for row in items:
            if holder not in (row.get("procuradores") or []):
                continue
            if any(leave_start <= day <= leave_end for day in _dias_compromisso(row)):
                found.append(
                    {
                        "afastamento": _afastamento_resumo(leave, nomes),
                        "compromisso": _compromisso_resumo(row, nomes),
                    }
                )
    return found
