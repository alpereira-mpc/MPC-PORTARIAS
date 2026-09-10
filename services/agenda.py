"""Central catalogs and local institutional availability rules."""

from datetime import datetime, time, timedelta
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
    "Ministério Público",
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
