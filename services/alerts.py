"""Internal alerts derived from live records. No dedicated alerts table."""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import logging
import json

from database.store import unwrap_store
from services.access import has_permission
from services.audit import INSTITUTIONAL_TZ, registrar_erro
from services.pending import (
    HOJE,
    SEM_PRAZO,
    URGENTE,
    VENCIDA,
    can_view_pendencias,
    collect_pending,
    now_recife,
    parse_datetime,
    summarize,
    today_recife,
    visible_cabinets,
)

LOGGER = logging.getLogger("mpc.alerts")
SOURCE_CAP = 1000
CRITICO = "CRÍTICO"
ALTO = "ALTO"
ATENCAO = "ATENÇÃO"
INFORMATIVO = "INFORMATIVO"
SEVERITY_ORDER = (CRITICO, ALTO, ATENCAO, INFORMATIVO)
TWO_HOURS = timedelta(hours=2)
ONE_DAY = timedelta(hours=24)
PERIODS = (
    ("hoje", "Hoje"),
    ("3d", "Próximos 3 dias"),
    ("7d", "Próximos 7 dias"),
    ("todos", "Todos"),
)


@dataclass(frozen=True)
class AlertItem:
    source_module: str
    source_id: str
    gabinete: str
    severity: str
    category: str
    title: str
    description: str
    date: date | None
    datetime: datetime | None
    source_status: str
    navigation_target: str
    metadata: dict = field(default_factory=dict)


def can_view_alertas(principal):
    return has_permission(principal, "alertas")


def empty_pending_counts():
    return {
        "vencidas": 0,
        "hoje": 0,
        "proximos_3": 0,
        "proximos_7": 0,
        "total": 0,
    }


def empty_alert_counts():
    return {
        "criticos": 0,
        "altos": 0,
        "atencao": 0,
        "informativos": 0,
        "total": 0,
    }


def summarize_alerts(items):
    counts = empty_alert_counts()
    for item in items:
        if item.severity == CRITICO:
            counts["criticos"] += 1
        elif item.severity == ALTO:
            counts["altos"] += 1
        elif item.severity == ATENCAO:
            counts["atencao"] += 1
        elif item.severity == INFORMATIVO:
            counts["informativos"] += 1
    counts["total"] = len(items)
    return counts


def _sort_key(item):
    rank = (
        SEVERITY_ORDER.index(item.severity)
        if item.severity in SEVERITY_ORDER
        else 9
    )
    when = item.datetime
    if when is None and item.date is not None:
        when = datetime.combine(item.date, datetime.min.time(), INSTITUTIONAL_TZ)
    if when is None:
        when = datetime.max.replace(tzinfo=INSTITUTIONAL_TZ)
    elif when.tzinfo is None:
        when = when.replace(tzinfo=INSTITUTIONAL_TZ)
    if item.source_module == "tarefas":
        metadata = item.metadata or {}
        return (
            rank,
            int(metadata.get("task_alert_rank", 99)),
            int(metadata.get("task_priority_rank", 99)),
            when,
            item.title,
        )
    if item.category in ("viagem_ida", "viagem_volta"):
        return (rank, int((item.metadata or {}).get("trip_rank", 99)), 99, when, item.title)
    # Every source must use the same tuple shape: mixing datetime and int in
    # the second position fails when a task and another module share severity.
    return (rank, 99, 99, when, item.title)


def trip_alerts(store, tomorrow):
    from database.agenda import AgendaStore
    items = []
    for row in AgendaStore(store, load_bindings=False).trip_alert_window(tomorrow.isoformat()):
        informed = bool(row.get("motorista_informado"))
        payload = json.loads(row.get("payload") or "{}")
        commitment = payload.get("titulo") or payload.get("processo") or "Compromisso institucional"
        for leg, field, hour, title in (
            ("ida", "ida_data", "ida_hora", "✈️ Voo de ida amanhã"),
            ("volta", "volta_data", "volta_chegada_hora", "✈️ Retorno amanhã"),
        ):
            if row.get(field) != tomorrow.isoformat(): continue
            airport = row.get("aeroporto_" + leg + "_outro") if row.get("aeroporto_" + leg) == "Outro" else row.get("aeroporto_" + leg)
            detail = f"{row['nome']}\nEvento: {commitment}\n{tomorrow.strftime('%d/%m')} às {row.get(hour) or '--:--'} — {airport or 'Aeroporto não informado'}"
            detail += "\n" + ("✅ Motorista informado" if informed else "⚠ Motorista ainda não informado")
            rank = (0 if leg == "ida" else 1) if not informed else (2 if leg == "ida" else 3)
            items.append(AlertItem("agenda", f"viagem:{row['compromisso_id']}:{row['procurador_id']}:{leg}:{tomorrow}", "—", ALTO if not informed else ATENCAO, f"viagem_{leg}", title, detail, tomorrow, None, "AGENDADA", "Agenda", {"compromisso_id": row["compromisso_id"], "procurador_id": row["procurador_id"], "trip_rank": rank}))
    return items


def alert_from_oficio(item):
    if item.urgency == VENCIDA:
        return _from_pending(
            item,
            CRITICO,
            "prazo_vencido",
            "Prazo vencido",
        )
    if item.urgency == HOJE:
        return _from_pending(
            item,
            ALTO,
            "prazo_hoje",
            "Prazo vence hoje",
        )
    if item.urgency == URGENTE:
        return _from_pending(
            item,
            ATENCAO,
            "prazo_proximo",
            "Prazo próximo",
        )
    if item.urgency == SEM_PRAZO:
        return _from_pending(
            item,
            INFORMATIVO,
            "providencia_pendente",
            "Providência pendente",
        )
    return None


def alert_from_agenda(item, now):
    metadata = item.metadata or {}
    # A recorded leave is not an upcoming commitment. Administrative alerts that
    # come from another source, such as an ongoing substitution, stay intact.
    if metadata.get("afastamento_id") or item.context == "afastamento":
        return None
    start = parse_datetime(metadata.get("inicio"))
    if start is None and item.start_date:
        start = parse_datetime(item.start_date)
    if start is None:
        return None
    if start < now:
        return None
    remaining = start - now
    if remaining <= TWO_HOURS:
        return _from_pending(
            item,
            CRITICO,
            "compromisso_breve",
            "Compromisso em breve",
            moment=start,
        )
    if start.date() == now.date():
        return _from_pending(
            item,
            ALTO,
            "compromisso_hoje",
            "Compromisso hoje",
            moment=start,
        )
    if remaining <= ONE_DAY:
        return _from_pending(
            item,
            ATENCAO,
            "compromisso_proximo",
            "Compromisso próximo",
            moment=start,
        )
    return None


def alert_from_memorando(item):
    if item.status_original == "EM ANDAMENTO":
        return _from_pending(
            item,
            ALTO,
            "substituicao_andamento",
            "Substituição em andamento",
        )
    if item.status_original == "AGENDADA" and item.urgency == HOJE:
        return _from_pending(
            item,
            ALTO,
            "substituicao_hoje",
            "Substituição inicia hoje",
        )
    if item.status_original == "AGENDADA" and item.urgency == URGENTE:
        return _from_pending(
            item,
            ATENCAO,
            "substituicao_proxima",
            "Substituição próxima",
        )
    return None


def alert_from_pending(item, now):
    if item.source_module == "oficios":
        return alert_from_oficio(item)
    if item.source_module == "agenda":
        return alert_from_agenda(item, now)
    if item.source_module == "memorandos":
        return alert_from_memorando(item)
    return None


def task_alerts(store, principal, now):
    """Small owner-scoped alert window; at most one alert per personal task."""
    from database.tarefas import TarefasStore, effective_deadline

    alerts = []
    priority_rank = {"URGENTE": 0, "ALTA": 1, "NORMAL": 2, "BAIXA": 3}
    for row in TarefasStore(store).alert_window(principal.id, now):
        due = date.fromisoformat(row["prazo_data"]) if row.get("prazo_data") else None
        deadline = effective_deadline(row, INSTITUTIONAL_TZ)
        reminder = datetime.fromisoformat(row["lembrete_atingido_em"]) if row.get("lembrete_atingido_em") else None
        overdue = deadline is not None and deadline < now
        today_due = deadline is not None and deadline.date() == now.date() and not overdue
        tomorrow_due = deadline is not None and deadline.date() == now.date().fromordinal(now.date().toordinal() + 1)
        upcoming_due = deadline is not None and now.date().fromordinal(now.date().toordinal() + 2) <= deadline.date() <= now.date().fromordinal(now.date().toordinal() + 7)
        reminder_due = reminder is not None and reminder <= now
        if not (overdue or today_due or tomorrow_due or upcoming_due or reminder_due):
            continue
        if overdue:
            severity = CRITICO if row["prioridade"] == "URGENTE" else ALTO
            category, title, alert_rank = "tarefa_atrasada", "ATRASADA", 0 if row["prioridade"] == "URGENTE" else 1
        elif reminder_due:
            severity, category, title, alert_rank = ATENCAO, "lembrete_tarefa", "LEMBRETE", 2
        elif today_due:
            severity, category, title, alert_rank = ATENCAO, "tarefa_hoje", "VENCE HOJE", 3
        elif tomorrow_due:
            severity, category, title, alert_rank = INFORMATIVO, "tarefa_amanha", "VENCE AMANHÃ", 4
        else:
            severity, category, title, alert_rank = INFORMATIVO, "tarefa_proxima", "PRÓXIMO PRAZO", 5
        # A date-only deadline intentionally has no datetime in the UI: it is
        # due at the end of its local day, but should not display a fake 00:00.
        moment = reminder if reminder_due and not overdue else deadline if row.get("prazo_hora") else None
        alerts.append(AlertItem("tarefas", str(row["id"]), "—", severity, category, title, row["titulo"][:160], due, moment, row["status"], "Tarefas", {"task_id": row["id"], "task_alert_rank": alert_rank, "task_priority_rank": priority_rank.get(row["prioridade"], 9)}))
    return alerts


def engagement_alerts(store, principal, now):
    """One indexed, user-scoped query for due reminders and follow notices."""
    from database.record_engagement import RecordEngagementStore

    module_map = {
        "oficio_enviado": "oficios",
        "oficio_recebido": "oficios",
        "memorando": "memorandos",
        "representacao": "representacoes",
        "ouvidoria": "ouvidoria",
        "tarefa": "tarefas",
    }
    result = []
    for row in RecordEngagementStore(store).due(principal.id, now):
        moment = parse_datetime(row["lembrar_em"])
        kind = row["tipo"]
        result.append(
            AlertItem(
                source_module=module_map[row["origem_modulo"]],
                source_id=str(row["origem_id"]),
                gabinete="—",
                severity=ATENCAO if kind == "LEMBRETE" else INFORMATIVO,
                category="lembrete_registro" if kind == "LEMBRETE" else "registro_seguido",
                title="LEMBRETE" if kind == "LEMBRETE" else "ATUALIZAÇÃO",
                description=(row.get("texto") or "Lembrete do registro")[:160],
                date=moment.date() if moment else now.date(),
                datetime=moment,
                source_status="PENDENTE",
                navigation_target=module_map[row["origem_modulo"]],
                metadata={
                    "notice_id": row["id"],
                    "notice_type": kind,
                    "origin_module": row["origem_modulo"],
                    "origin_id": row["origem_id"],
                },
            )
        )
    return result


def _from_pending(item, severity, category, title, moment=None):
    extra = (item.subtitle or item.context or "").strip()
    description = item.title
    if extra and extra != item.title:
        description = f"{item.title} · {extra[:80]}"
    when = moment
    if when is None and item.due_date:
        when = parse_datetime(item.due_date)
    return AlertItem(
        source_module=item.source_module,
        source_id=item.source_id,
        gabinete=item.gabinete,
        severity=severity,
        category=category,
        title=title,
        description=description[:160],
        date=when.date() if when else item.due_date,
        datetime=when,
        source_status=item.status_original,
        navigation_target=item.navigation,
        metadata=dict(item.metadata or {}),
    )


def _in_period(item, period, today):
    if period in (None, "todos"):
        return True
    if item.source_module in ("sistema", "access_requests"):
        return True
    due = item.date
    if item.source_module == "memorandos" and item.source_status == "EM ANDAMENTO":
        due = today
    if item.severity == CRITICO and item.source_module == "oficios":
        return True
    if due is None:
        return period == "todos"
    if period == "hoje":
        return due == today
    last = {"3d": 3, "7d": 7}[period]
    if due < today:
        return True
    return today <= due <= today + timedelta(days=last)


def system_alerts(store, *, cached=None, principal=None):
    """Admin-only current Saúde conditions. No live diagnostics.

    Historical ERRO_OPERACIONAL rows are audit incidents, not live alerts.
    Only current database/schema/PDF/DOCX/audit-subsystem failures surface here.
    """
    from services.system_health import ATTENTION, ERROR

    if principal is not None and not has_permission(principal, "admin"):
        return []
    report = cached if isinstance(cached, dict) else None
    if not report:
        return []
    items = []
    database = report.get("database") or {}
    if database.get("status") == ERROR:
        items.append(
            AlertItem(
                source_module="sistema",
                source_id="database",
                gabinete="—",
                severity=CRITICO,
                category="banco",
                title="Banco indisponível",
                description=(database.get("summary") or "Não foi possível consultar o banco")[:160],
                date=today_recife(),
                datetime=None,
                source_status=ERROR,
                navigation_target="Administração",
                metadata={"secao": "Sistema", "aba": "Saúde"},
            )
        )
    schema = report.get("schema")
    if schema:
        status = schema.get("status")
        if status == ERROR:
            items.append(
                AlertItem(
                    source_module="sistema",
                    source_id="schema",
                    gabinete="—",
                    severity=CRITICO,
                    category="schema",
                    title="Falha no schema",
                    description=(schema.get("summary") or "Schema indisponível")[:160],
                    date=today_recife(),
                    datetime=None,
                    source_status=status,
                    navigation_target="Administração",
                    metadata={"secao": "Sistema", "aba": "Saúde"},
                )
            )
        elif status == ATTENTION and (
            schema.get("missing_tables") or schema.get("missing_columns")
        ):
            items.append(
                AlertItem(
                    source_module="sistema",
                    source_id="schema",
                    gabinete="—",
                    severity=ALTO,
                    category="schema",
                    title="Schema incompleto",
                    description=(schema.get("summary") or "Estrutura esperada não encontrada")[:160],
                    date=today_recife(),
                    datetime=None,
                    source_status=status,
                    navigation_target="Administração",
                    metadata={"secao": "Sistema", "aba": "Saúde"},
                )
            )
    documents = report.get("documents") or {}
    pdf = documents.get("pdf") or {}
    if pdf.get("status") in (ATTENTION, ERROR):
        items.append(
            AlertItem(
                source_module="sistema",
                source_id="pdf",
                gabinete="—",
                severity=ATENCAO,
                category="conversor_pdf",
                title="Conversor PDF indisponível",
                description=(pdf.get("summary") or "Conversor PDF não detectado")[:160],
                date=today_recife(),
                datetime=None,
                source_status=pdf.get("status") or ATTENTION,
                navigation_target="Administração",
                metadata={"secao": "Sistema", "aba": "Saúde"},
            )
        )
    docx = documents.get("docx") or {}
    if docx.get("status") == ERROR:
        items.append(
            AlertItem(
                source_module="sistema",
                source_id="docx",
                gabinete="—",
                severity=ALTO,
                category="geracao_docx",
                title="Geração DOCX indisponível",
                description=(docx.get("summary") or "Geração DOCX indisponível")[:160],
                date=today_recife(),
                datetime=None,
                source_status=ERROR,
                navigation_target="Administração",
                metadata={"secao": "Sistema", "aba": "Saúde"},
            )
        )
    audit = report.get("audit") or {}
    if audit.get("status") == ERROR:
        items.append(
            AlertItem(
                source_module="sistema",
                source_id="audit",
                gabinete="—",
                severity=ATENCAO,
                category="auditoria",
                title="Falha na auditoria",
                description=(audit.get("summary") or "Auditoria indisponível")[:160],
                date=today_recife(),
                datetime=None,
                source_status=ERROR,
                navigation_target="Administração",
                metadata={"secao": "Sistema", "aba": "Saúde"},
            )
        )
    return items[:SOURCE_CAP]


def access_request_alerts(store, principal):
    """One derived alert from pending access-request COUNT. Admin only."""
    if not has_permission(principal, "admin"):
        return []
    try:
        from services.access_requests import count_pending_access_requests

        count = count_pending_access_requests(store)
    except Exception:
        LOGGER.exception("Falha ao carregar alertas de solicitações de acesso")
        return []
    if count <= 0:
        return []
    if count == 1:
        title = "Há 1 solicitação de acesso pendente."
    else:
        title = f"Há {count} solicitações de acesso pendentes."
    return [
        AlertItem(
            source_module="access_requests",
            source_id="pending",
            gabinete="—",
            severity=ATENCAO,
            category="acesso_pendente",
            title=title,
            description="Administração → Solicitações",
            date=today_recife(),
            datetime=None,
            source_status="pendente",
            navigation_target="Administração",
            metadata={"secao": "Solicitações"},
        )
    ]


def collect_alerts(
    store,
    principal,
    *,
    now=None,
    modules=None,
    gabinete=None,
    severity=None,
    period="todos",
    cached_health=None,
):
    store = unwrap_store(store)
    now = now_recife(now)
    today = now.date()
    if not can_view_alertas(principal):
        raise ValueError("Acesso não autorizado a este módulo.")
    if gabinete and not principal.administrator and gabinete not in visible_cabinets(
        principal
    ):
        return [], {}, today
    wanted = set(modules) if modules else None
    errors = {}
    collected = []
    operational_wanted = {"oficios", "agenda", "memorandos"}
    need_operational = wanted is None or bool(wanted & operational_wanted)
    if need_operational and can_view_pendencias(principal):
        pending_modules = None
        if wanted is not None:
            pending_modules = tuple(wanted & operational_wanted)
        if pending_modules or wanted is None:
            items, errors, today = collect_pending(
                store,
                principal,
                today=today,
                now=now,
                modules=pending_modules,
                gabinete=gabinete,
                period="todos",
                alert_window=True,
            )
            for item in items:
                alert = alert_from_pending(item, now)
                if alert:
                    collected.append(alert)
    if wanted is None or "tarefas" in wanted:
        try:
            collected.extend(task_alerts(store, principal, now))
            errors["tarefas"] = None
        except Exception as exc:
            LOGGER.exception("Falha ao carregar alertas de tarefas")
            errors["tarefas"] = "tarefas"
    engagement_modules = {"oficios", "memorandos", "representacoes", "ouvidoria", "tarefas"}
    if wanted is None or bool(wanted & engagement_modules):
        try:
            notices = engagement_alerts(store, principal, now)
            if wanted is not None:
                notices = [item for item in notices if item.source_module in wanted]
            collected.extend(notices)
        except Exception:
            LOGGER.exception("Falha ao carregar lembretes e acompanhamentos")
    if has_permission(principal, "agenda") and (wanted is None or "agenda" in wanted):
        try:
            collected.extend(trip_alerts(store, today + timedelta(days=1)))
        except Exception:
            LOGGER.exception("Falha ao carregar alertas de viagens")
    if has_permission(principal, "admin") and (
        wanted is None or "sistema" in wanted
    ) and not gabinete:
        try:
            collected.extend(
                system_alerts(store, cached=cached_health, principal=principal)
            )
            errors["sistema"] = None
        except Exception as exc:
            LOGGER.exception("Falha ao carregar alertas de sistema")
            registrar_erro(
                store,
                modulo="alertas",
                acao="CONSULTAR",
                erro=exc,
                principal=principal,
            )
            errors["sistema"] = "sistema"
    elif not has_permission(principal, "admin"):
        errors.pop("sistema", None)
    if has_permission(principal, "admin") and (
        wanted is None or "access_requests" in wanted
    ) and not gabinete:
        try:
            collected.extend(access_request_alerts(store, principal))
        except Exception:
            LOGGER.exception("Falha ao carregar alertas de solicitações de acesso")
    filtered = []
    for item in collected:
        if item.source_module in ("sistema", "access_requests") and not has_permission(
            principal, "admin"
        ):
            continue
        if severity and item.severity != severity:
            continue
        if wanted and item.source_module not in wanted:
            continue
        if (
            gabinete
            and item.source_module not in ("sistema", "access_requests")
            and item.gabinete != gabinete
        ):
            continue
        if not _in_period(item, period, today):
            continue
        filtered.append(item)
    filtered.sort(key=_sort_key)
    return filtered[: SOURCE_CAP], errors, today


BELL_LIMIT = 5
BELL_CACHE_SECONDS = 20
BELL_CACHE_KEY = "_alerts_bell_cache"
ALERTS_REVISION_KEY = "alerts_revision"


def _state_map(state=None):
    if state is not None:
        return state
    try:
        import streamlit as st

        return st.session_state
    except Exception:
        return None


def alerts_revision(state=None):
    mapping = _state_map(state)
    if mapping is None or ALERTS_REVISION_KEY not in mapping:
        return 0
    try:
        return int(mapping[ALERTS_REVISION_KEY] or 0)
    except (TypeError, ValueError):
        return 0


def invalidate_alert_summary(state=None):
    """Bump the session revision so the bell cache misses on the next render."""
    mapping = _state_map(state)
    if mapping is None:
        return 0
    value = alerts_revision(mapping) + 1
    mapping[ALERTS_REVISION_KEY] = value
    return value


def get_alert_summary(store, principal, *, now=None, cached_health=None, revision=None):
    """Lightweight bell payload: counts plus the top active alerts."""
    _ = revision
    items, errors, today = collect_alerts(
        store, principal, now=now, cached_health=cached_health
    )
    counts = summarize_alerts(items)
    return {
        "total": counts["total"],
        "counts": counts,
        "top": items[:BELL_LIMIT],
        "errors": errors,
        "today": today,
    }


def dashboard_counts(store, principal, *, now=None, cached_health=None):
    """Single operational collect for Home: Pendências + Alertas.

    ``cached_health`` is accepted for API stability; system alerts stay on
    the Alertas page so Home does not run Saúde checks.
    """
    _ = cached_health
    now = now_recife(now)
    today = now.date()
    pending = empty_pending_counts()
    alerts = empty_alert_counts()
    errors = {}
    converted = []
    if can_view_pendencias(principal):
        items, errors, today = collect_pending(store, principal, today=today)
        pending = summarize(items, today)
        converted = [
            alert for item in items if (alert := alert_from_pending(item, now))
        ]
    alerts = summarize_alerts(converted)
    return pending, alerts, errors
