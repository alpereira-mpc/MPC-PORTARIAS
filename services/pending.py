"""Aggregation of operational pending items. No dedicated pending table."""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
import json
import logging

from database.store import schema_key_of, unwrap_store
from services.access import allowed_gabinetes, has_permission
from services.audit import INSTITUTIONAL_TZ, registrar_erro
from services.oficios import CLOSED, GABINETES

LOGGER = logging.getLogger("mpc.pending")
PAGE_SIZE = 50
SOURCE_CAP = 1000
_TABLES = {}
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


def now_recife(now=None):
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=INSTITUTIONAL_TZ)
        return now.astimezone(INSTITUTIONAL_TZ)
    if isinstance(now, date):
        return datetime.combine(now, time.min, INSTITUTIONAL_TZ)
    return datetime.now(INSTITUTIONAL_TZ)


def parse_datetime(value):
    """Aware datetime in America/Recife; DATE-only strings stay on that calendar day."""
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=INSTITUTIONAL_TZ)
        return value.astimezone(INSTITUTIONAL_TZ)
    if isinstance(value, date):
        return datetime.combine(value, time.min, INSTITUTIONAL_TZ)
    text = str(value).strip()
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        head = text[:10]
        rest = text[10:]
        if "T" in text or rest.startswith(" ") or text.endswith("Z") or "+" in text[10:]:
            try:
                moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return datetime.combine(date.fromisoformat(head), time.min, INSTITUTIONAL_TZ)
            if moment.tzinfo is None:
                return moment.replace(tzinfo=INSTITUTIONAL_TZ)
            return moment.astimezone(INSTITUTIONAL_TZ)
        return datetime.combine(date.fromisoformat(head), time.min, INSTITUTIONAL_TZ)
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


def _has_table(store, name, connection=None):
    key = (schema_key_of(store), name)
    cached = _TABLES.get(key)
    if cached is not None:
        return cached

    def lookup(active):
        if store.backend == "postgresql":
            row = active.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema=current_schema() AND table_name=?",
                (name,),
            ).fetchone()
        else:
            row = active.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
        return bool(row)

    if connection is not None:
        found = lookup(connection)
    else:
        with store.connection(read_only=True) as active:
            found = lookup(active)
    _TABLES[key] = found
    return found


def fetch_oficios(
    store,
    principal,
    *,
    today,
    gabinete_filter=None,
    connection=None,
    mapping=None,
    due_on_or_before=None,
    limit=SOURCE_CAP,
):
    if not _has_table(store, "oficios", connection):
        return []
    allowed = _scope_cabinets(principal, gabinete_filter)
    if not allowed:
        return []
    closed = OFICIO_CLOSED
    items = []
    ctx = None
    if connection is None:
        ctx = store.connection(read_only=True)
        connection = ctx.__enter__()
    try:
        cabinets = mapping if mapping is not None else _cabinet_map(connection)
        placeholders = _sql_in(allowed)
        extra = ""
        params = [*closed, *allowed, *allowed, *allowed]
        if due_on_or_before is not None:
            extra = " AND (o.prazo IS NULL OR o.prazo<=?) "
            params.append(due_on_or_before)
        params.append(limit)
        rows = connection.execute(
            "SELECT o.id,o.direcao,o.serie,o.ano,o.numero,o.status,o.data,o.prazo,"
            "o.membro_id,o.assunto,o.destinatario,o.numero_externo,dest.sigla AS dest_sigla "
            "FROM oficios o LEFT JOIN ("
            "SELECT d.oficio_id,MIN(s.sigla) AS sigla FROM oficio_destinatarios d "
            "JOIN oficio_series s ON s.membro_id=d.membro_id GROUP BY d.oficio_id"
            ") dest ON dest.oficio_id=o.id WHERE o.status NOT IN ("
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
            + extra
            + "ORDER BY o.prazo,o.data,o.id LIMIT ?",
            params,
        ).fetchall()
        for row in rows:
            gabinete = (
                row["serie"]
                or cabinets.get(int(row["membro_id"] or 0), "")
                or (row["dest_sigla"] or "")
            )
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
    except BaseException:
        if ctx is not None:
            ctx.__exit__(*__import__("sys").exc_info())
        raise
    else:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def fetch_agenda(
    store,
    principal,
    *,
    today,
    gabinete_filter=None,
    connection=None,
    mapping=None,
    start_from=None,
    until=None,
    limit=SOURCE_CAP,
):
    if not _has_table(store, "agenda_compromissos", connection):
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
    start = start_from or today.isoformat()
    items = []
    ctx = None
    if connection is None:
        ctx = store.connection(read_only=True)
        connection = ctx.__enter__()
    try:
        mapping = mapping if mapping is not None else _cabinet_map(connection)
        extra = ""
        params = [start]
        if until is not None:
            extra = " AND a.inicio<=? "
            params.append(until)
        params.append(limit)
        rows = connection.execute(
            "SELECT a.id,a.tipo,a.inicio,a.fim,a.situacao,a.payload,p.procurador_id "
            "FROM ("
            "SELECT a.id,a.tipo,a.inicio,a.fim,a.situacao,a.payload "
            "FROM agenda_compromissos a "
            "WHERE a.situacao NOT IN ('Cancelado','Realizado') AND a.inicio>=? "
            + extra
            + "ORDER BY a.inicio,a.id LIMIT ?"
            ") a LEFT JOIN agenda_compromisso_procuradores p ON p.compromisso_id=a.id "
            "ORDER BY a.inicio,a.id",
            params,
        ).fetchall()
        grouped = {}
        order = []
        for row in rows:
            record = grouped.get(row["id"])
            if record is None:
                record = {"row": row, "members": []}
                grouped[row["id"]] = record
                order.append(row["id"])
            if row["procurador_id"] is not None:
                record["members"].append(int(row["procurador_id"]))
        for identifier in order:
            packed = grouped[identifier]
            row = packed["row"]
            members = packed["members"]
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
            local = (payload.get("local") or "").strip()
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
                    metadata={"tipo": row["tipo"], "inicio": row["inicio"], "local": local},
                )
            )
        return items
    except BaseException:
        if ctx is not None:
            ctx.__exit__(*__import__("sys").exc_info())
        raise
    else:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def fetch_memorandos(
    store,
    principal,
    *,
    today,
    gabinete_filter=None,
    connection=None,
    mapping=None,
    start_on_or_before=None,
    limit=SOURCE_CAP,
):
    if not _has_table(store, "memorandos", connection):
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
    ctx = None
    if connection is None:
        ctx = store.connection(read_only=True)
        connection = ctx.__enter__()
    try:
        mapping = mapping if mapping is not None else _cabinet_map(connection)
        extra = ""
        params = [today.isoformat()]
        if start_on_or_before is not None:
            extra = " AND s.data_inicio<=? "
            params.append(start_on_or_before)
        params.append(limit)
        rows = connection.execute(
            "SELECT m.id,m.status,m.numero_oficial,s.data_inicio,s.data_fim,"
            "s.gabinete_procurador_id,s.gabinete_snapshot,s.motivo "
            "FROM memorandos m JOIN memorandos_substituicao s ON s.memorando_id=m.id "
            "WHERE m.status!='CANCELADO' AND s.data_fim>=? "
            + extra
            + "ORDER BY s.data_inicio,m.id LIMIT ?",
            params,
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
    except BaseException:
        if ctx is not None:
            ctx.__exit__(*__import__("sys").exc_info())
        raise
    else:
        if ctx is not None:
            ctx.__exit__(None, None, None)


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
    now=None,
    modules=None,
    gabinete=None,
    urgency=None,
    period="todos",
    alert_window=False,
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
    due_on_or_before = None
    start_from = None
    until = None
    start_on_or_before = None
    if alert_window:
        moment = now_recife(now)
        today = moment.date()
        due_on_or_before = (today + timedelta(days=3)).isoformat()
        start_from = moment.isoformat()
        until = (moment + timedelta(hours=24)).isoformat()
        start_on_or_before = due_on_or_before
    extra = {
        "oficios": {"due_on_or_before": due_on_or_before},
        "agenda": {"start_from": start_from, "until": until},
        "memorandos": {"start_on_or_before": start_on_or_before},
    }
    with store.connection(read_only=True) as connection:
        mapping = _cabinet_map(connection)
        for key, loader, permission in sources:
            if key not in wanted or not has_permission(principal, permission):
                continue
            try:
                collected.extend(
                    loader(
                        store,
                        principal,
                        today=today,
                        gabinete_filter=gabinete,
                        connection=connection,
                        mapping=mapping,
                        **extra[key],
                    )
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


def count_pending(store, principal, **kwargs):
    items, errors, today = collect_pending(store, principal, **kwargs)
    return summarize(items, today), errors, today


def list_pending(store, principal, *, limit=PAGE_SIZE, offset=0, **kwargs):
    items, errors, today = collect_pending(store, principal, **kwargs)
    if offset < 0 or not 1 <= limit <= SOURCE_CAP:
        raise ValueError("Página inválida.")
    return items[offset : offset + limit], len(items), errors, today


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
    counts, errors, _ = count_pending(store, principal, today=today, period="todos")
    return counts, errors
