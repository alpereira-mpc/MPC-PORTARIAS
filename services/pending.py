"""Aggregation of operational pending items. No dedicated pending table."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import json
import logging

from database.store import unwrap_store
from services.access import allowed_gabinetes, has_permission
from services.audit import INSTITUTIONAL_TZ, registrar_erro
from services.oficios import CLOSED, GABINETES

LOGGER = logging.getLogger("mpc.pending")
PAGE_SIZE = 50
SOURCE_CAP = 1000
VENCIDA = "VENCIDA"
HOJE = "HOJE"
URGENTE = "URGENTE"
PROXIMA = "PRÓXIMA"
FUTURA = "FUTURA"
SEM_PRAZO = "SEM PRAZO"
URGENCY_ORDER = (VENCIDA, HOJE, URGENTE, PROXIMA, FUTURA, SEM_PRAZO)
OFICIO_CLOSED = tuple(CLOSED)
PERIODS = (
    ("hoje", "Hoje"),
    ("3d", "Próximos 3 dias"),
    ("7d", "Próximos 7 dias"),
    ("30d", "Próximos 30 dias"),
    ("todos", "Todos"),
)


@dataclass(frozen=True)
class PendingItem:
    source_module: str
    source_id: str
    gabinete: str
    title: str
    subtitle: str
    due_date: date | None
    start_date: date | None
    end_date: date | None
    status_original: str
    urgency: str
    context: str
    navigation: str
    metadata: dict = field(default_factory=dict)


def today_recife(now=None):
    if isinstance(now, date) and not isinstance(now, datetime):
        return now
    if isinstance(now, datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=INSTITUTIONAL_TZ)
        return now.astimezone(INSTITUTIONAL_TZ).date()
    return datetime.now(INSTITUTIONAL_TZ).date()


def parse_date(value, today=None):
    """Calendar date in America/Recife; a DATE-only string is never shifted by UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        moment = value
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=INSTITUTIONAL_TZ)
        return moment.astimezone(INSTITUTIONAL_TZ).date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        head = text[:10]
        rest = text[10:]
        if "T" in text or rest.startswith(" ") or text.endswith("Z") or "+" in text[10:]:
            try:
                moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return date.fromisoformat(head)
            if moment.tzinfo is None:
                return date.fromisoformat(head)
            return moment.astimezone(INSTITUTIONAL_TZ).date()
        return date.fromisoformat(head)
    return None


def classify_deadline(due, today):
    if due is None:
        return SEM_PRAZO
    if due < today:
        return VENCIDA
    if due == today:
        return HOJE
    delta = (due - today).days
    if delta <= 3:
        return URGENTE
    if delta <= 7:
        return PROXIMA
    return FUTURA


def classify_agenda(start, today):
    if start is None or start < today:
        return None
    return classify_deadline(start, today)


def classify_memorando(situacao, start, today):
    if situacao == "EM ANDAMENTO":
        return HOJE
    if situacao == "AGENDADA":
        return classify_deadline(start, today)
    return None


def can_view_pendencias(principal):
    return any(
        has_permission(principal, module)
        for module in ("oficios", "agenda", "memorandos")
    )


def visible_cabinets(principal):
    if principal is None:
        return ()
    if principal.administrator:
        return GABINETES
    return tuple(code for code in GABINETES if code in allowed_gabinetes(principal))


def _authorized_cabinet(principal, gabinete):
    if principal.administrator:
        return True
    allowed = visible_cabinets(principal)
    if not allowed:
        return False
    return (gabinete or "") in allowed


def _sql_in(values):
    return ",".join("?" * len(values))


def _cabinet_map(connection):
    mapping = {}
    try:
        rows = connection.execute(
            "SELECT membro_id,sigla FROM oficio_series"
        ).fetchall()
    except Exception:
        return mapping
    for row in rows:
        mapping[int(row[0])] = row[1]
    return mapping


def _scope_cabinets(principal, gabinete_filter):
    if principal.administrator:
        allowed = list(GABINETES)
    else:
        allowed = list(visible_cabinets(principal))
    if gabinete_filter:
        if gabinete_filter not in allowed:
            return []
        return [gabinete_filter]
    return allowed


def _has_table(store, name):
    with store.connection(read_only=True) as connection:
        if store.backend == "postgresql":
            row = connection.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=current_schema() AND table_name=?",
                (name,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
    return bool(row)


def fetch_oficios(store, principal, *, today, gabinete_filter=None):
    if not _has_table(store, "oficios"):
        return []
    allowed = _scope_cabinets(principal, gabinete_filter)
    if not allowed:
        return []
    closed = OFICIO_CLOSED
    items = []
    with store.connection(read_only=True) as connection:
        mapping = _cabinet_map(connection)
        placeholders = _sql_in(allowed)
        rows = connection.execute(
            "SELECT o.id,o.direcao,o.serie,o.ano,o.numero,o.status,o.data,o.prazo,"
            "o.membro_id,o.assunto,o.destinatario,o.numero_externo "
            "FROM oficios o WHERE o.status NOT IN ("
            + _sql_in(closed)
            + ") AND o.status!='Rascunho' AND ("
            "(o.direcao='RECEBIDO' AND (o.prazo IS NOT NULL OR o.status IN "
            "('Recebido','Em análise','Aguardando providência','Encaminhado')))"
            " OR (o.direcao='ENVIADO' AND o.status IN "
            "('Gerado','Enviado','Aguardando resposta'))) "
            "AND ("
            "o.serie IN (" + placeholders + ") "
            "OR o.membro_id IN (SELECT membro_id FROM oficio_series WHERE sigla IN ("
            + placeholders
            + ")) "
            "OR EXISTS (SELECT 1 FROM oficio_destinatarios d "
            "JOIN oficio_series s ON s.membro_id=d.membro_id "
            "WHERE d.oficio_id=o.id AND s.sigla IN ("
            + placeholders
            + "))) "
            "ORDER BY o.prazo,o.data,o.id LIMIT ?",
            (*closed, *allowed, *allowed, *allowed, SOURCE_CAP),
        ).fetchall()
        for row in rows:
            gabinete = row["serie"] or mapping.get(int(row["membro_id"] or 0), "")
            if not gabinete:
                dest = connection.execute(
                    "SELECT s.sigla FROM oficio_destinatarios d "
                    "JOIN oficio_series s ON s.membro_id=d.membro_id "
                    "WHERE d.oficio_id=? ORDER BY s.sigla LIMIT 1",
                    (row["id"],),
                ).fetchone()
                gabinete = dest[0] if dest else ""
            if not _authorized_cabinet(principal, gabinete):
                continue
            if gabinete_filter and gabinete != gabinete_filter:
                continue
            due = parse_date(row["prazo"], today)
            number = row["numero"]
            if row["direcao"] == "ENVIADO" and number:
                title = f"Ofício nº {number}/{row['ano']}"
            elif row["numero_externo"]:
                title = f"Ofício {row['numero_externo']}"
            else:
                title = "Ofício recebido"
            assunto = (row["assunto"] or "").strip()
            items.append(
                PendingItem(
                    source_module="oficios",
                    source_id=str(row["id"]),
                    gabinete=gabinete or "—",
                    title=title,
                    subtitle=assunto[:80],
                    due_date=due,
                    start_date=parse_date(row["data"], today),
                    end_date=due,
                    status_original=row["status"],
                    urgency=classify_deadline(due, today),
                    context=row["destinatario"] or "",
                    navigation="Ofícios",
                    metadata={"direcao": row["direcao"]},
                )
            )
    return items


def fetch_agenda(store, principal, *, today, gabinete_filter=None):
    if not _has_table(store, "agenda_compromissos"):
        return []
    restrict = bool(visible_cabinets(principal)) or principal.administrator
    if restrict:
        allowed = _scope_cabinets(principal, gabinete_filter)
        if not allowed:
            return []
    else:
        allowed = []
        if gabinete_filter:
            return []
    start = today.isoformat()
    items = []
    with store.connection(read_only=True) as connection:
        mapping = _cabinet_map(connection)
        rows = connection.execute(
            "SELECT a.id,a.tipo,a.inicio,a.fim,a.situacao,a.payload "
            "FROM agenda_compromissos a "
            "WHERE a.situacao NOT IN ('Cancelado','Realizado') AND a.inicio>=? "
            "ORDER BY a.inicio,a.id LIMIT ?",
            (start, SOURCE_CAP),
        ).fetchall()
        people = connection.execute(
            "SELECT compromisso_id,procurador_id FROM agenda_compromisso_procuradores"
        ).fetchall()
        by_id = {}
        for link in people:
            by_id.setdefault(link[0], []).append(int(link[1]))
        for row in rows:
            members = by_id.get(row["id"], [])
            cabinets = {mapping[mid] for mid in members if mid in mapping}
            if gabinete_filter:
                if gabinete_filter not in cabinets:
                    continue
                gabinete = gabinete_filter
            elif restrict and allowed:
                overlap = cabinets.intersection(allowed)
                if not overlap:
                    continue
                gabinete = sorted(overlap)[0]
            else:
                gabinete = sorted(cabinets)[0] if cabinets else "—"
            if restrict and allowed and not (cabinets.intersection(allowed)):
                continue
            inicio = parse_date(row["inicio"], today)
            urgency = classify_agenda(inicio, today)
            if urgency is None:
                continue
            try:
                payload = json.loads(row["payload"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            title = (payload.get("titulo") or "").strip() or {
                "REUNIAO": "Reunião",
                "EVENTO": "Evento",
                "DESPACHO": "Despacho",
            }.get(row["tipo"], "Compromisso")
            extra = payload.get("reuniao_com") or payload.get("categoria") or ""
            items.append(
                PendingItem(
                    source_module="agenda",
                    source_id=str(row["id"]),
                    gabinete=gabinete or "—",
                    title=title[:80],
                    subtitle=extra[:80],
                    due_date=inicio,
                    start_date=inicio,
                    end_date=parse_date(row["fim"], today),
                    status_original=row["situacao"],
                    urgency=urgency,
                    context=row["tipo"],
                    navigation="Agenda",
                    metadata={"tipo": row["tipo"], "inicio": row["inicio"]},
                )
            )
    return items


def fetch_memorandos(store, principal, *, today, gabinete_filter=None):
    if not _has_table(store, "memorandos"):
        return []
    restrict = bool(visible_cabinets(principal)) or principal.administrator
    if restrict:
        allowed = _scope_cabinets(principal, gabinete_filter)
        if not allowed:
            return []
    else:
        allowed = []
        if gabinete_filter:
            return []
    items = []
    with store.connection(read_only=True) as connection:
        mapping = _cabinet_map(connection)
        rows = connection.execute(
            "SELECT m.id,m.status,m.numero_oficial,s.data_inicio,s.data_fim,"
            "s.gabinete_procurador_id,s.gabinete_snapshot,s.motivo "
            "FROM memorandos m JOIN memorandos_substituicao s ON s.memorando_id=m.id "
            "WHERE m.status!='CANCELADO' AND s.data_fim>=? "
            "ORDER BY s.data_inicio,m.id LIMIT ?",
            (today.isoformat(), SOURCE_CAP),
        ).fetchall()
        for row in rows:
            start = parse_date(row["data_inicio"], today)
            end = parse_date(row["data_fim"], today)
            if row["status"] == "CANCELADO" or (end and end < today):
                continue
            situacao = "AGENDADA" if start and start > today else "EM ANDAMENTO"
            urgency = classify_memorando(situacao, start, today)
            if urgency is None:
                continue
            gabinete = ""
            if row["gabinete_procurador_id"] is not None:
                gabinete = mapping.get(int(row["gabinete_procurador_id"]), "")
            if restrict and allowed:
                if gabinete and gabinete not in allowed:
                    continue
                if gabinete_filter and gabinete != gabinete_filter:
                    continue
            elif gabinete_filter and gabinete != gabinete_filter:
                continue
            number = (row["numero_oficial"] or "").strip()
            title = (
                f"Substituição {number}" if number else "Substituição de servidor"
            )
            items.append(
                PendingItem(
                    source_module="memorandos",
                    source_id=str(row["id"]),
                    gabinete=gabinete or "—",
                    title=title,
                    subtitle=(row["motivo"] or "")[:80],
                    due_date=start if situacao == "AGENDADA" else today,
                    start_date=start,
                    end_date=end,
                    status_original=situacao,
                    urgency=urgency,
                    context=(row["gabinete_snapshot"] or "")[:80],
                    navigation="Memorandos",
                    metadata={"status_documental": row["status"]},
                )
            )
    return items


def _in_period(item, period, today):
    if period in (None, "todos"):
        return True
    due = item.due_date
    if item.source_module == "memorandos" and item.status_original == "EM ANDAMENTO":
        due = today
    if due is None:
        return period == "todos"
    if period == "hoje":
        return due == today
    last = {"3d": 3, "7d": 7, "30d": 30}[period]
    return today <= due <= today + timedelta(days=last)


def collect_pending(
    store,
    principal,
    *,
    today=None,
    modules=None,
    gabinete=None,
    urgency=None,
    period="todos",
):
    """Backend-authorized aggregation. Unknown gabinetes are ignored, never expanded."""
    store = unwrap_store(store)
    today = today_recife(today)
    if not can_view_pendencias(principal):
        raise ValueError("Acesso não autorizado a este módulo.")
    if gabinete and not principal.administrator and gabinete not in visible_cabinets(
        principal
    ):
        return [], {}, today
    wanted = set(modules or ("oficios", "agenda", "memorandos"))
    errors = {}
    collected = []
    sources = (
        ("oficios", fetch_oficios, "oficios"),
        ("agenda", fetch_agenda, "agenda"),
        ("memorandos", fetch_memorandos, "memorandos"),
    )
    for key, loader, permission in sources:
        if key not in wanted or not has_permission(principal, permission):
            continue
        try:
            collected.extend(
                loader(store, principal, today=today, gabinete_filter=gabinete)
            )
            errors[key] = None
        except Exception as exc:
            LOGGER.exception("Falha ao carregar pendências de %s", key)
            registrar_erro(
                store, modulo="pendencias", acao="CONSULTAR", erro=exc, principal=principal
            )
            errors[key] = key
    filtered = []
    for item in collected:
        if urgency and item.urgency != urgency:
            continue
        if not _in_period(item, period, today):
            continue
        filtered.append(item)
    filtered.sort(
        key=lambda item: (
            URGENCY_ORDER.index(item.urgency)
            if item.urgency in URGENCY_ORDER
            else 9,
            item.due_date or date.max,
            item.title,
        )
    )
    return filtered, errors, today


def summarize(items, today):
    vencidas = sum(1 for i in items if i.urgency == VENCIDA)
    hoje = sum(1 for i in items if i.urgency == HOJE)
    d3 = sum(
        1
        for i in items
        if i.due_date and today < i.due_date <= today + timedelta(days=3)
    )
    d7 = sum(
        1
        for i in items
        if i.due_date and today < i.due_date <= today + timedelta(days=7)
    )
    return {
        "vencidas": vencidas,
        "hoje": hoje,
        "proximos_3": d3,
        "proximos_7": d7,
        "total": len(items),
    }


def pending_counts(store, principal, *, today=None):
    items, errors, today = collect_pending(store, principal, today=today, period="todos")
    return summarize(items, today), errors
