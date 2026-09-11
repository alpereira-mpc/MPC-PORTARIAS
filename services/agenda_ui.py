"""Native Streamlit agenda, loaded only on its portal route."""

import calendar
from datetime import date, datetime, time, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo
import streamlit as st
from database.agenda import AgendaStore
from services.agenda import (
    TYPES,
    STATUSES,
    EVENT_TYPES,
    MEETING_WITH,
    LOCATIONS,
    institutional,
    conflicts,
)
from services.ui_store import display_store


def display_datetime(value, date_only=False):
    """Brazilian presentation only; stored values remain ISO."""
    return datetime.fromisoformat(value).strftime(
        "%d/%m/%Y" if date_only else "%d/%m/%Y às %H:%M"
    )


@st.cache_data(ttl=30, max_entries=128, show_spinner=False)
def read_agenda(key, start, end, member, kind, status, _agenda, offset=None):
    if offset is not None:
        return _agenda.upcoming(start, member, kind, status, offset)
    return _agenda.list(start, end, member, kind, status)


def records(agenda, start, end, member=None, kind=None, status=None, *, offset=None):
    store = agenda.store
    key = (
        store.read_cache_key(("agenda_compromissos", "agenda_compromisso_procuradores"))
        if store.backend == "postgresql"
        else (
            str(store.path.resolve()),
            store.path.stat().st_mtime_ns,
            st.session_state.get("agenda_revision", 0),
        )
    )
    return read_agenda(key, start, end, member, kind, status, agenda, offset)


def done(message):
    read_agenda.clear()
    st.session_state["agenda_revision"] = st.session_state.get("agenda_revision", 0) + 1
    st.session_state.pop("agenda_edit", None)
    st.session_state["agenda_message"] = message
    st.rerun()


def preset(label, choices, value, key):
    choice = st.selectbox(
        label,
        choices,
        index=(
            choices.index(value)
            if value in choices
            else len(choices) - 1 if value else 0
        ),
        key=key,
    )
    return (
        st.text_input(
            f"Especifique: {label}",
            value=value if value not in choices else "",
            key=key + "_other",
        )
        if choice == "Outro"
        else choice
    )


def editor(agenda, people):
    old = st.session_state["agenda_edit"]
    prefix = (
        "agenda_form_"
        + old.get("id", "new")
        + "_"
        + str(st.session_state.get("agenda_revision", 0))
    )
    st.subheader("Editar compromisso" if old else "Novo compromisso")
    kind = st.selectbox(
        "Tipo de compromisso",
        list(TYPES),
        format_func=TYPES.get,
        index=list(TYPES).index(old.get("tipo", "EVENTO")),
        key=prefix + "type",
    )
    available = {
        p["id"]: p["nome"]
        for p in people
        if p["ativo"] or p["id"] in old.get("procuradores", [])
    }
    if not available:
        st.warning("Cadastre um procurador ativo no módulo Portarias.")
        return
    selected = [p for p in old.get("procuradores", []) if p in available]
    if kind == "DESPACHO":
        members = [
            st.selectbox(
                "Membro do MPC-PB",
                list(available),
                index=list(available).index(selected[0]) if selected else 0,
                format_func=available.get,
                key=prefix + "single",
            )
        ]
    else:
        members = st.multiselect(
            "Membros do MPC-PB",
            list(available),
            default=selected,
            format_func=available.get,
            key=prefix + "multi",
        )
    initial = (
        datetime.fromisoformat(old["inicio"])
        if old
        else datetime.combine(
            datetime.now(ZoneInfo("America/Sao_Paulo")).date(), time(9)
        )
    )
    ending = datetime.fromisoformat(old["fim"]) if old.get("fim") else initial
    day = st.date_input(
        "Data de início" if kind == "EVENTO" else "Data",
        value=initial.date(),
        key=prefix + "day",
        format="DD/MM/YYYY",
    )
    untimed = kind == "EVENTO" and st.checkbox(
        "Evento sem horário (dia inteiro)",
        value=bool(old.get("sem_hora", True)),
        key=prefix + "untimed",
    )
    hour = (
        time.min
        if untimed
        else st.time_input("Hora inicial", value=initial.time(), key=prefix + "hour")
    )
    end_day = (
        st.date_input(
            "Data de término",
            value=ending.date(),
            key=prefix + "end_day",
            format="DD/MM/YYYY",
        )
        if kind == "EVENTO"
        else day
    )
    has_end = (
        False
        if untimed
        else st.checkbox(
            "Informar hora final", value=bool(old.get("fim")), key=prefix + "has_end"
        )
    )
    end_hour = (
        st.time_input("Hora final", value=ending.time(), key=prefix + "end_hour")
        if has_end
        else None
    )
    if kind == "EVENTO" and not untimed and end_day != day and not has_end:
        st.caption("Sem hora final, o período termina ao final da data de término.")
    end = (
        datetime.combine(end_day, time.min if untimed else end_hour or time(23, 59, 59))
        if untimed or has_end or end_day != day
        else None
    )
    status = st.selectbox(
        "Situação",
        STATUSES,
        index=STATUSES.index(old.get("situacao", "Agendado")),
        key=prefix + "status",
    )
    record = {
        "id": old.get("id"),
        "tipo": kind,
        "procuradores": members,
        "inicio": datetime.combine(day, hour).isoformat(),
        "fim": end.isoformat() if end else None,
        "sem_hora": untimed,
        "situacao": status,
    }
    signature = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()[
        :16
    ]
    # Reset both checkboxes whenever scheduling data changes, including A -> B -> A.
    if st.session_state.get(prefix + "signature") != signature:
        st.session_state[prefix + "institution"] = False
        st.session_state[prefix + "conflict"] = False
        st.session_state[prefix + "signature"] = signature
    alerts = (
        institutional(members, kind, day, agenda.bindings)
        if status != "Cancelado"
        else []
    )
    others = (
        records(
            agenda, day.isoformat(), (max(day, end_day) + timedelta(days=1)).isoformat()
        )
        if members
        else []
    )
    overlaps = conflicts(record, others)
    institution_ok = conflict_ok = False
    st.markdown("**Disponibilidade dos membros**")
    for rule in alerts:
        name = available.get(agenda.bindings[rule["key"]], rule["name"])
        st.warning(
            f"⚠️ ATENÇÃO À DISPONIBILIDADE — {name} possui {rule['activity']} neste dia da semana. Confirme a disponibilidade do membro antes de registrar este compromisso."
        )
    if alerts:
        institution_ok = st.checkbox(
            "Confirmo que verifiquei a disponibilidade do membro para este compromisso.",
            key=prefix + "institution",
        )
    if overlaps:
        st.warning(
            "⚠️ CONFLITO DE HORÁRIO — Este membro já possui outro compromisso agendado no período selecionado."
        )
        for row in overlaps:
            st.write(
                f"{display_datetime(row['inicio'], row['sem_hora'])} · {TYPES[row['tipo']]} · {row.get('titulo') or row.get('processo')}"
            )
        conflict_ok = st.checkbox(
            "Estou ciente do conflito e desejo registrar o compromisso mesmo assim.",
            key=prefix + "conflict",
        )
    if members and not alerts and not overlaps:
        st.success("Sem impedimentos identificados nos dados consultados.")
    if kind == "EVENTO":
        record["categoria"] = preset(
            "Tipo do evento", EVENT_TYPES, old.get("categoria", ""), prefix + "category"
        )
        record["titulo"] = st.text_input(
            "Nome do evento *", value=old.get("titulo", ""), key=prefix + "event_title"
        )
    elif kind == "REUNIAO":
        record["reuniao_com"] = preset(
            "Reunião com", MEETING_WITH, old.get("reuniao_com", ""), prefix + "with"
        )
        record["titulo"] = st.text_input(
            "Assunto *", value=old.get("titulo", ""), key=prefix + "meeting_title"
        )
    else:
        record["processo"] = st.text_input(
            "Processo *", value=old.get("processo", ""), key=prefix + "process"
        )
        record["titulo"] = st.text_input(
            "Interessado/assunto", value=old.get("titulo", ""), key=prefix + "subject"
        )
    record["local"] = preset(
        "Local", LOCATIONS[kind], old.get("local", ""), prefix + kind + "location"
    )
    record["observacoes"] = st.text_area(
        "Observações", value=old.get("observacoes", ""), key=prefix + "notes"
    )
    if st.button(
        "Salvar compromisso",
        type="primary",
        disabled=not members
        or bool(alerts and not institution_ok)
        or bool(overlaps and not conflict_ok),
    ):
        try:
            agenda.save(
                record,
                institutional_confirmed=institution_ok,
                conflict_confirmed=conflict_ok,
            )
            done("Compromisso salvo com sucesso.")
        except ValueError as exc:
            st.error(str(exc))
    if st.button("Voltar à agenda"):
        st.session_state.pop("agenda_edit", None)
        st.rerun()


def render(store=None, principal=None):
    from database.store import Store
    from services.access import current_user, require_permission

    st.header("AGENDA DOS PROCURADORES")
    if store is None:
        store = Store()
    existing = st.session_state.get("agenda_store")
    if type(existing) is not AgendaStore:
        # Streamlit may retain an instance of the previous class after a code reload.
        st.session_state["agenda_store"] = AgendaStore(
            getattr(existing, "store", store)
        )
    elif existing.store is not store:
        st.session_state["agenda_store"] = AgendaStore(store)
    agenda = st.session_state["agenda_store"]
    if principal is None:
        principal = current_user(agenda.store)
    require_permission(principal, "agenda")
    people = display_store(agenda.store).catalog("procuradores")
    names = {p["id"]: p["nome"] for p in people}
    if message := st.session_state.pop("agenda_message", None):
        st.success(message)
    if "agenda_edit" in st.session_state:
        editor(agenda, people)
        return
    if st.button("+ Novo compromisso", type="primary"):
        st.session_state["agenda_edit"] = {}
        st.rerun()
    a, b, c = st.columns(3)
    member = a.selectbox(
        "Procurador",
        [None, *names],
        format_func=lambda p: names.get(p, "Todos"),
        key="agenda_filter_member",
    )
    kind = b.selectbox(
        "Tipo",
        [None, *TYPES],
        format_func=lambda k: TYPES.get(k, "Todos"),
        key="agenda_filter_type",
    )
    status = c.selectbox(
        "Situação",
        [None, *STATUSES],
        format_func=lambda s: s or "Todas",
        key="agenda_filter_status",
    )
    if st.session_state.get("agenda_view") == "Lista":
        st.session_state["agenda_view"] = "Hoje"
    view = st.radio(
        "Visualização",
        ["Hoje", "Semana", "Mês", "Próximos"],
        horizontal=True,
        key="agenda_view",
    )
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    anchor = (
        today
        if view in ("Hoje", "Próximos")
        else st.date_input(
            "Data de referência", today, key="agenda_anchor", format="DD/MM/YYYY"
        )
    )
    start = anchor
    end = start + timedelta(days=1)
    if view == "Semana":
        start = anchor - timedelta(days=anchor.weekday())
        end = start + timedelta(days=7)
    elif view == "Mês":
        start = anchor.replace(day=1)
        end = start + timedelta(days=calendar.monthrange(anchor.year, anchor.month)[1])
    if view == "Próximos":
        signature = (
            today.isoformat(),
            member,
            kind,
            status,
            st.session_state.get("agenda_revision", 0),
        )
        if st.session_state.get("agenda_upcoming_filters") != signature:
            st.session_state["agenda_upcoming_offset"] = 0
            st.session_state["agenda_upcoming_filters"] = signature
        offset = st.session_state.get("agenda_upcoming_offset", 0)
        rows = records(
            agenda, today.isoformat(), None, member, kind, status, offset=offset
        )
        # A deletion in another session can empty the current page.
        if not rows and offset:
            st.session_state["agenda_upcoming_offset"] = 0
            st.rerun()
        has_next = len(rows) > 30
        rows = rows[:30]
        if rows:
            st.caption(f"Exibindo {offset + 1}–{offset + len(rows)}")
        previous, following = st.columns(2)
        if previous.button(
            "Anterior", disabled=offset == 0, key="agenda_upcoming_previous"
        ):
            st.session_state["agenda_upcoming_offset"] = max(0, offset - 30)
            st.rerun()
        if following.button(
            "Próxima", disabled=not has_next, key="agenda_upcoming_next"
        ):
            st.session_state["agenda_upcoming_offset"] = offset + 30
            st.rerun()
    else:
        rows = records(agenda, start.isoformat(), end.isoformat(), member, kind, status)
    if not rows:
        st.info("Nenhum compromisso no período selecionado.")
    current_day = None
    for row in rows:
        if row["inicio"][:10] != current_day:
            current_day = row["inicio"][:10]
            st.subheader(datetime.fromisoformat(current_day).strftime("%d/%m/%Y"))
        hour = "Dia inteiro" if row["sem_hora"] else row["inicio"][11:16]
        title = row.get("titulo") or row.get("processo")
        with st.container(border=True):
            st.markdown(f"**{hour} · {TYPES[row['tipo']]} · {title}**")
            st.write(" / ".join(names.get(p, str(p)) for p in row["procuradores"]))
            st.caption(f"{row['local']} · {row['situacao']}")
            with st.expander("Detalhes e ações"):
                st.write("Início:", display_datetime(row["inicio"], row["sem_hora"]))
                if row["fim"]:
                    st.write("Término:", display_datetime(row["fim"], row["sem_hora"]))
                for key, label in (
                    ("categoria", "Tipo do evento"),
                    ("reuniao_com", "Reunião com"),
                    ("processo", "Processo"),
                    ("observacoes", "Observações"),
                ):
                    if row.get(key):
                        st.write(f"{label}: {row[key]}")
                if st.button("Editar", key="agenda_edit_" + row["id"]):
                    st.session_state["agenda_edit"] = row
                    st.rerun()
                if row["situacao"] != "Cancelado" and st.button(
                    "Cancelar compromisso", key="agenda_cancel_" + row["id"]
                ):
                    agenda.cancel(row["id"])
                    done("Compromisso cancelado; registro preservado.")
                confirmed = st.checkbox(
                    "Confirmo a exclusão definitiva deste compromisso.",
                    key="agenda_delete_confirm_" + row["id"],
                )
                if st.button(
                    "Excluir", key="agenda_delete_" + row["id"], disabled=not confirmed
                ):
                    agenda.delete(row["id"], confirmed=confirmed)
                    done("Compromisso excluído.")
