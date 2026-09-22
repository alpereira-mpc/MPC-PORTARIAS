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
    ("vencidas", "Vencidas"),
    ("3d", "Próximos 3 dias"),
    ("7d", "Próximos 7 dias"),
    ("30d", "Próximos 30 dias"),
    ("todas", "Todas ativas"),
)
CORE_MODULES = ("oficios", "agenda", "memorandos")
EXTENDED_MODULES = ("tarefas", "representacoes", "ouvidoria", "admin")
REP_CLOSED = ("JULGADA", "ENCERRADA", "ARQUIVADA", "CANCELADA")
OUVI_CLOSED = ("ENCERRADA", "ARQUIVADA")
TASK_ACTIVE = ("A_FAZER", "EM_ANDAMENTO", "AGUARDANDO")
TASK_STATUS_LABELS = {
    "A_FAZER": "A fazer",
    "EM_ANDAMENTO": "Em andamento",
    "AGUARDANDO": "Aguardando",
}
REP_STATUS_LABELS = {
    "IDEIA": "Ideia / avaliação inicial",
    "PESQUISA": "Em pesquisa",
    "ELABORACAO": "Em elaboração",
    "MINUTA_REVISAO": "Minuta em revisão",
    "APROVADA": "Aprovada para assinatura",
    "AGUARDANDO_PROTOCOLO": "Aguardando protocolo",
    "PROTOCOLADA": "Protocolada",
    "EM_TRAMITACAO": "Em tramitação",
    "SUSPENSA": "Suspensa",
}
OUVI_STATUS_LABELS = {
    "RECEBIDA": "Recebida",
    "TRIAGEM": "Em triagem",
    "ANALISE": "Em análise",
    "AGUARDANDO_PROVIDENCIA": "Aguardando providência",
    "PROVIDENCIA_ANDAMENTO": "Providência em andamento",
}


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
        for module in (*CORE_MODULES, "representacoes", "ouvidoria", "admin")
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
            + ") AND ("
            "o.status='Rascunho' OR "
            "(o.direcao='RECEBIDO' AND (o.prazo IS NOT NULL OR o.status IN "
            "('Recebido','Em análise','Aguardando providência','Encaminhado')))"
            " OR (o.direcao='ENVIADO' AND o.status IN "
            "('Rascunho','Gerado','Enviado','Aguardando resposta'))) "
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


def fetch_afastamentos(
    store,
    principal,
    *,
    today,
    gabinete_filter=None,
    connection=None,
    mapping=None,
    limit=SOURCE_CAP,
):
    if not _has_table(store, "agenda_afastamentos", connection):
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
        rows = connection.execute(
            "SELECT id,procurador_id,motivo,data_inicio,data_fim "
            "FROM agenda_afastamentos WHERE cancelado=0 AND data_fim>=? "
            "ORDER BY data_inicio,id LIMIT ?",
            (today.isoformat(), limit),
        ).fetchall()
        for row in rows:
            start = parse_date(row["data_inicio"], today)
            end = parse_date(row["data_fim"], today)
            if end and end < today:
                continue
            situacao = "AGENDADA" if start and start > today else "EM ANDAMENTO"
            urgency = classify_memorando(situacao, start, today)
            if urgency is None:
                continue
            gabinete = mapping.get(int(row["procurador_id"] or 0), "")
            if restrict and allowed:
                if gabinete and gabinete not in allowed:
                    continue
                if gabinete_filter and gabinete != gabinete_filter:
                    continue
            elif gabinete_filter and gabinete != gabinete_filter:
                continue
            items.append(
                PendingItem(
                    source_module="agenda",
                    source_id=str(row["id"]),
                    gabinete=gabinete or "—",
                    title="Afastamento",
                    subtitle=(row["motivo"] or "")[:80],
                    due_date=start if situacao == "AGENDADA" else today,
                    start_date=start,
                    end_date=end,
                    status_original=situacao,
                    urgency=urgency,
                    context="afastamento",
                    navigation="Agenda",
                    metadata={"afastamento_id": str(row["id"])},
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


def fetch_tarefas(
    store,
    principal,
    *,
    today,
    gabinete_filter=None,
    connection=None,
    mapping=None,
    limit=SOURCE_CAP,
):
    _ = mapping
    if gabinete_filter:
        return []
    if not _has_table(store, "tarefas", connection):
        return []
    owner = getattr(principal, "id", None)
    if owner is None:
        return []
    items = []
    ctx = None
    if connection is None:
        ctx = store.connection(read_only=True)
        connection = ctx.__enter__()
    try:
        placeholders = _sql_in(TASK_ACTIVE)
        rows = connection.execute(
            "SELECT id,titulo,prioridade,status,prazo_data,categoria "
            "FROM tarefas WHERE owner_user_id=? AND status IN ("
            + placeholders
            + ") ORDER BY prazo_data,id LIMIT ?",
            (owner, *TASK_ACTIVE, limit),
        ).fetchall()
        for row in rows:
            due = parse_date(row["prazo_data"], today)
            if due is None and row["prioridade"] == "URGENTE":
                urgency = URGENTE
                due = today
            else:
                urgency = classify_deadline(due, today)
            items.append(
                PendingItem(
                    source_module="tarefas",
                    source_id=str(row["id"]),
                    gabinete="—",
                    title=(row["titulo"] or "Tarefa")[:80],
                    subtitle=(row["categoria"] or "")[:80],
                    due_date=due,
                    start_date=due,
                    end_date=due,
                    status_original=TASK_STATUS_LABELS.get(row["status"], row["status"]),
                    urgency=urgency,
                    context=row["prioridade"] or "",
                    navigation="Tarefas",
                    metadata={"prioridade": row["prioridade"]},
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


def fetch_representacoes(
    store,
    principal,
    *,
    today,
    gabinete_filter=None,
    connection=None,
    mapping=None,
    limit=SOURCE_CAP,
):
    _ = mapping
    if gabinete_filter:
        return []
    if not _has_table(store, "representacoes", connection):
        return []
    items = []
    ctx = None
    if connection is None:
        ctx = store.connection(read_only=True)
        connection = ctx.__enter__()
    try:
        closed = _sql_in(REP_CLOSED)
        rows = connection.execute(
            "SELECT id,titulo,situacao,data_abertura,numero_processo,prioridade "
            "FROM representacoes WHERE situacao NOT IN ("
            + closed
            + ") ORDER BY data_abertura,id LIMIT ?",
            (*REP_CLOSED, limit),
        ).fetchall()
        for row in rows:
            waiting = row["situacao"] == "AGUARDANDO_PROTOCOLO"
            due = today if waiting else None
            urgency = URGENTE if waiting else SEM_PRAZO
            if row["prioridade"] == "URGENTE" and urgency == SEM_PRAZO:
                urgency = URGENTE
                due = today
            status = REP_STATUS_LABELS.get(row["situacao"], row["situacao"])
            if waiting:
                status = REP_STATUS_LABELS["AGUARDANDO_PROTOCOLO"]
            elif not row["numero_processo"] and row["situacao"] in (
                "IDEIA",
                "PESQUISA",
                "ELABORACAO",
                "MINUTA_REVISAO",
                "APROVADA",
            ):
                status = "Em preparação"
            items.append(
                PendingItem(
                    source_module="representacoes",
                    source_id=str(row["id"]),
                    gabinete="—",
                    title=(row["titulo"] or "Representação")[:80],
                    subtitle=(row["numero_processo"] or "")[:80],
                    due_date=due,
                    start_date=parse_date(row["data_abertura"], today),
                    end_date=due,
                    status_original=status,
                    urgency=urgency,
                    context=row["situacao"],
                    navigation="Representações",
                    metadata={"situacao": row["situacao"]},
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


def fetch_ouvidoria(
    store,
    principal,
    *,
    today,
    gabinete_filter=None,
    connection=None,
    mapping=None,
    limit=SOURCE_CAP,
):
    if not _has_table(store, "ouvidoria_manifestacoes", connection):
        return []
    restrict = bool(visible_cabinets(principal)) or principal.administrator
    allowed = _scope_cabinets(principal, gabinete_filter) if restrict else []
    if restrict and gabinete_filter and not allowed:
        return []
    items = []
    ctx = None
    if connection is None:
        ctx = store.connection(read_only=True)
        connection = ctx.__enter__()
    try:
        closed = _sql_in(OUVI_CLOSED)
        mapping = mapping if mapping is not None else _cabinet_map(connection)
        rows = connection.execute(
            "SELECT id,numero_interno,titulo,situacao,data_recebimento,"
            "prioridade,representacao_id,procurador_responsavel_id "
            "FROM ouvidoria_manifestacoes WHERE situacao NOT IN ("
            + closed
            + ") ORDER BY data_recebimento,id LIMIT ?",
            (*OUVI_CLOSED, limit),
        ).fetchall()
        for row in rows:
            gabinete = mapping.get(int(row["procurador_responsavel_id"] or 0), "")
            if gabinete_filter and gabinete != gabinete_filter:
                continue
            waiting = row["situacao"] in ("TRIAGEM", "ANALISE", "AGUARDANDO_PROVIDENCIA")
            due = today if waiting else None
            urgency = URGENTE if waiting else SEM_PRAZO
            if row["prioridade"] == "URGENTE" and urgency == SEM_PRAZO:
                urgency = URGENTE
                due = today
            linked = "Vinculada a representação" if row["representacao_id"] else ""
            items.append(
                PendingItem(
                    source_module="ouvidoria",
                    source_id=str(row["id"]),
                    gabinete=gabinete or "—",
                    title=(row["numero_interno"] or row["titulo"] or "Notícia de fato")[:80],
                    subtitle=((row["titulo"] or "") + ((" · " + linked) if linked else ""))[:80],
                    due_date=due,
                    start_date=parse_date(row["data_recebimento"], today),
                    end_date=due,
                    status_original=OUVI_STATUS_LABELS.get(row["situacao"], row["situacao"]),
                    urgency=urgency,
                    context=row["situacao"],
                    navigation="Ouvidoria",
                    metadata={"situacao": row["situacao"]},
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


def fetch_access_requests(
    store,
    principal,
    *,
    today,
    gabinete_filter=None,
    connection=None,
    mapping=None,
    limit=SOURCE_CAP,
):
    _ = mapping
    if not _has_table(store, "access_requests", connection):
        return []
    items = []
    ctx = None
    if connection is None:
        ctx = store.connection(read_only=True)
        connection = ctx.__enter__()
    try:
        rows = connection.execute(
            "SELECT id,nome,email,gabinete,created_at FROM access_requests "
            "WHERE status='pendente' ORDER BY created_at,id LIMIT ?",
            (limit,),
        ).fetchall()
        for row in rows:
            gabinete = row["gabinete"] or "—"
            if gabinete_filter and gabinete != gabinete_filter:
                continue
            due = today
            items.append(
                PendingItem(
                    source_module="access_requests",
                    source_id=str(row["id"]),
                    gabinete=gabinete,
                    title="Solicitação de acesso",
                    subtitle=((row["nome"] or "") + " · " + (row["email"] or ""))[:80],
                    due_date=due,
                    start_date=due,
                    end_date=due,
                    status_original="Pendente",
                    urgency=URGENTE,
                    context=row["gabinete"] or "",
                    navigation="Administração",
                    metadata={"secao": "Solicitações"},
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


def item_in_period(item, period, today):
    if period in (None, "todos", "todas"):
        return True
    due = item.due_date
    if item.source_module == "memorandos" and item.status_original == "EM ANDAMENTO":
        due = today
    if due is None:
        return period in ("todos", "todas")
    if period == "vencidas":
        return due < today
    if period == "hoje":
        return due == today
    last = {"3d": 3, "7d": 7, "30d": 30}[period]
    return today < due <= today + timedelta(days=last)


def _in_period(item, period, today):
    return item_in_period(item, period, today)


def collect_pending(
    store,
    principal,
    *,
    today=None,
    now=None,
    modules=None,
    gabinete=None,
    urgency=None,
    period="todas",
    alert_window=False,
    pesquisa=None,
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
    if modules:
        wanted = set(modules)
    else:
        wanted = set(CORE_MODULES)
        if not alert_window:
            wanted.update(EXTENDED_MODULES)
    errors = {}
    collected = []
    sources = (
        ("oficios", fetch_oficios, "oficios"),
        ("agenda", fetch_agenda, "agenda"),
        ("afastamentos", fetch_afastamentos, "agenda"),
        ("memorandos", fetch_memorandos, "memorandos"),
        ("tarefas", fetch_tarefas, "tarefas"),
        ("representacoes", fetch_representacoes, "representacoes"),
        ("ouvidoria", fetch_ouvidoria, "ouvidoria"),
        ("admin", fetch_access_requests, "admin"),
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
        wanted &= set(CORE_MODULES)
    extra = {
        "oficios": {"due_on_or_before": due_on_or_before},
        "agenda": {"start_from": start_from, "until": until},
        "memorandos": {"start_on_or_before": start_on_or_before},
    }
    with store.connection(read_only=True) as connection:
        mapping = _cabinet_map(connection)
        for key, loader, permission in sources:
            selected = key if key != "afastamentos" else "agenda"
            if selected not in wanted or not has_permission(principal, permission):
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
                        **extra.get(key, {}),
                    )
                )
                errors[selected] = None
            except Exception as exc:
                LOGGER.exception("Falha ao carregar pendências de %s", key)
                registrar_erro(
                    store, modulo="pendencias", acao="CONSULTAR", erro=exc, principal=principal
                )
                errors[selected] = selected
    needle = (pesquisa or "").strip().casefold()
    filtered = []
    for item in collected:
        if urgency and item.urgency != urgency:
            continue
        if not _in_period(item, period, today):
            continue
        if needle:
            hay = " ".join(
                part
                for part in (
                    item.title,
                    item.subtitle,
                    item.gabinete,
                    item.status_original,
                    item.navigation,
                )
                if part
            ).casefold()
            if needle not in hay:
                continue
        filtered.append(item)
    filtered.sort(
        key=lambda item: (
            URGENCY_ORDER.index(item.urgency)
            if item.urgency in URGENCY_ORDER
            else 9,
            item.due_date or date.max,
            item.source_module,
            item.gabinete or "",
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
