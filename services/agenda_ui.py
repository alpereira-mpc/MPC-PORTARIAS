"""Native Streamlit agenda, loaded only on its portal route."""

import calendar
from datetime import date, datetime, time, timedelta
import hashlib
import json
import logging
from zoneinfo import ZoneInfo
import streamlit as st
from database.agenda import AgendaStore
from services.agenda import (
    TYPES,
    STATUSES,
    EVENT_TYPES,
    MEETING_WITH,
    LOCATIONS,
    contexto_semana,
    institutional,
    conflicts,
    leitura_semana_salva,
)
from services.afastamentos import MOTIVOS, eligible_substitutes, substitution_pending
from services.ui_store import display_store
from services.date_format import format_date_br, format_datetime_br
from services.branding import module_title
from services.ui_theme import (
    badges,
    card_container,
    empty_state,
    filter_mark,
    render_html,
    render_record,
    status_tone,
    trip_html,
)


LOGGER = logging.getLogger("mpc.ai")
AVISO_SEMANA_IA = (
    "Análise gerada por inteligência artificial a partir dos registros da Agenda. "
    "Consulte os compromissos e afastamentos para conferência."
)
AVISO_SEMANA_ALTERADA = (
    "A Agenda desta semana foi alterada desde a última análise. "
    "Gere novamente para atualizar."
)


def display_datetime(value, date_only=False):
    """Brazilian presentation only; stored values remain ISO."""
    return format_date_br(value) if date_only else format_datetime_br(value)


@st.cache_data(ttl=30, max_entries=128, show_spinner=False)
def read_agenda(
    key, start, end, member, kind, status, _agenda, offset=None, active=False
):
    if offset is not None:
        return (_agenda.active_upcoming if active else _agenda.upcoming)(
            start, member, kind, status, offset
        )
    return (_agenda.active if active else _agenda.list)(
        start, end, member, kind, status
    )


def records(
    agenda,
    start,
    end,
    member=None,
    kind=None,
    status=None,
    *,
    offset=None,
    active=False,
):
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
    return read_agenda(key, start, end, member, kind, status, agenda, offset, active)


def _listing_summary(appointments, leaves):
    def word(count, singular, plural):
        return f"{count} {singular if count == 1 else plural}"

    return (
        word(appointments, "compromisso", "compromissos")
        + " · "
        + word(leaves, "afastamento", "afastamentos")
    )


def agenda_pdf_filename(start, end=None):
    first = start.strftime("%d-%m-%Y")
    return (
        f"Agenda_MPC-PB_{first}_a_{end.strftime('%d-%m-%Y')}.pdf"
        if end and end != start
        else f"Agenda_MPC-PB_{first}.pdf"
    )


def done(message):
    from services.alerts import invalidate_alert_summary

    read_agenda.clear()
    st.session_state["agenda_revision"] = st.session_state.get("agenda_revision", 0) + 1
    st.session_state.pop("agenda_edit", None)
    st.session_state.pop("agenda_leave_edit", None)
    st.session_state["agenda_message"] = message
    invalidate_alert_summary()
    st.rerun()


def move_page(key, delta):
    """Callbacks run before queries; Streamlit already supplies the rerun."""
    st.session_state[key] = max(0, st.session_state.get(key, 0) + delta)


def start_appointment_edit(record):
    st.session_state.pop("agenda_leave_edit", None)
    st.session_state["agenda_edit"] = record


def start_leave_edit(agenda, identifier):
    st.session_state.pop("agenda_edit", None)
    st.session_state["agenda_leave_edit"] = agenda.get_leave(identifier)


def audit_agenda(evento, acao, identifier=None, extra=None, entity_type="compromisso"):
    from services.audit import registrar_evento

    store = st.session_state.get("_mpc_store")
    if store is None and "agenda_store" in st.session_state:
        store = getattr(st.session_state["agenda_store"], "store", None)
    registrar_evento(
        store,
        evento=evento,
        modulo="agenda",
        acao=acao,
        entidade_tipo=entity_type,
        entidade_id=identifier,
        detalhes=extra,
    )


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


def editor(agenda, people, principal=None):
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
    existing_trips = (
        agenda.trips_for_commitments([old["id"]]).get(old["id"], {})
        if old.get("id")
        else {}
    )
    trip_member = None
    trip = None
    has_trip = st.checkbox(
        "Possui viagem aérea", value=bool(existing_trips), key=prefix + "has_trip"
    )
    if (has_trip or existing_trips) and members:
        trip_member = st.selectbox(
            "Procurador participante com logística",
            members,
            format_func=available.get,
            key=prefix + "trip_member",
        )
        keep_trip = has_trip and st.checkbox(
            "Cadastrar/manter logística para este procurador",
            value=not existing_trips or bool(existing_trips.get(trip_member)),
            key=prefix + "keep_trip",
        )
        if keep_trip:
            trip = trip_form(
                existing_trips.get(trip_member),
                prefix + f"trip_{trip_member}_",
                day,
                end_day,
            )
    removed_trip_members = [
        member for member in existing_trips if member not in members
    ]
    remove_orphan_trips = False
    if removed_trip_members:
        st.warning(
            "Há logística cadastrada para membro removido do compromisso. Remova-a explicitamente antes de salvar."
        )
        remove_orphan_trips = st.checkbox(
            "Confirmo a remoção da logística dos membros removidos",
            key=prefix + "remove_orphan_trips",
        )
    if st.button(
        "Salvar compromisso",
        type="primary",
        key=prefix + "save",
        disabled=not members
        or bool(alerts and not institution_ok)
        or bool(overlaps and not conflict_ok),
    ):
        try:
            identifier = agenda.save(
                record,
                institutional_confirmed=institution_ok,
                conflict_confirmed=conflict_ok,
                remove_trip_members=removed_trip_members if remove_orphan_trips else (),
            )
            if remove_orphan_trips:
                for member in removed_trip_members:
                    audit_agenda(
                        "VIAGEM_AEREA_REMOVIDA",
                        "REMOVER",
                        identifier,
                        {"compromisso_id": identifier, "procurador_id": member},
                        "viagem_aerea",
                    )
            if trip_member is not None and trip:
                trip_old = existing_trips.get(trip_member)
                agenda.upsert_commitment_trip(
                    identifier,
                    trip_member,
                    trip,
                    informed_by=getattr(principal, "email", None),
                )
                audit_agenda(
                    "VIAGEM_AEREA_EDITADA" if trip_old else "VIAGEM_AEREA_CADASTRADA",
                    "ALTERAR" if trip_old else "CRIAR",
                    identifier,
                    {
                        "compromisso_id": identifier,
                        "procurador_id": trip_member,
                        "aeroporto_ida": trip.get("aeroporto_ida"),
                        "aeroporto_volta": trip.get("aeroporto_volta"),
                    },
                    "viagem_aerea",
                )
                if trip.get("motorista_informado") and not (trip_old or {}).get(
                    "motorista_informado"
                ):
                    audit_agenda(
                        "MOTORISTA_MARCADO_COMO_INFORMADO",
                        "ALTERAR",
                        identifier,
                        {"compromisso_id": identifier, "procurador_id": trip_member},
                        "viagem_aerea",
                    )
            elif trip_member is not None and existing_trips.get(trip_member):
                agenda.delete_commitment_trip(identifier, trip_member)
                audit_agenda(
                    "VIAGEM_AEREA_REMOVIDA",
                    "REMOVER",
                    identifier,
                    {"compromisso_id": identifier, "procurador_id": trip_member},
                    "viagem_aerea",
                )
            audit_agenda(
                "COMPROMISSO_ALTERADO" if old.get("id") else "COMPROMISSO_CRIADO",
                "ALTERAR" if old.get("id") else "CRIAR",
                identifier,
                extra={"tipo": record.get("tipo")},
            )
            done("Compromisso salvo com sucesso.")
        except ValueError as exc:
            st.error(str(exc))
    if st.button("Voltar à agenda", key=prefix + "back"):
        st.session_state.pop("agenda_edit", None)
        st.rerun()


def consume_pending_open_agenda(agenda):
    focus = st.session_state.pop("pending_open_agenda", None)
    if focus and "agenda_edit" not in st.session_state:
        record = agenda.get(focus)
        if record:
            # Set navigation before its widget is instantiated on this rerun.
            st.session_state["agenda_section"] = (
                "Histórico"
                if record.get("situacao") in ("Realizado", "Cancelado")
                else "Agenda"
            )
            st.session_state["agenda_edit"] = record
        else:
            leave = agenda.get_leave(focus)
            if leave:
                st.session_state["agenda_section"] = (
                    "Histórico"
                    if leave["status"] in ("ENCERRADO", "CANCELADO")
                    else "Agenda"
                )
                st.session_state["agenda_leave_edit"] = leave
    return focus


def driver_message(name, trip, compromisso=None):
    def airport(leg):
        value = trip.get("aeroporto_" + leg) or trip.get("aeroporto")
        return (
            trip.get("aeroporto_" + leg + "_outro") or trip.get("aeroporto_outro")
            if value == "Outro"
            else value
        )

    lines = ["Bom dia.", ""]
    if trip.get("ida_data"):
        purpose = (
            f" para {compromisso}" if compromisso else " para compromisso institucional"
        )
        lines.append(
            f"No dia {datetime.fromisoformat(trip['ida_data']).strftime('%d/%m')}, o(a) Procurador(a) {name} viajará{purpose}."
        )
        if airport("ida"):
            lines.append(
                f"Saída pelo Aeroporto de {airport('ida')},"
                + (
                    f" com voo previsto para {trip['ida_hora'].replace(':', 'h')}."
                    if trip.get("ida_hora")
                    else "."
                )
            )
        if trip.get("ida_motorista_hora"):
            lines.append(
                f"Horário combinado para saída: {trip['ida_motorista_hora'].replace(':', 'h')}."
            )
    if trip.get("volta_data"):
        text = (
            f"O retorno será em {datetime.fromisoformat(trip['volta_data']).strftime('%d/%m')}"
            + (f" pelo Aeroporto de {airport('volta')}" if airport("volta") else "")
        )
        if trip.get("volta_chegada_hora"):
            text += f", com chegada prevista às {trip['volta_chegada_hora'].replace(':', 'h')}"
        lines.extend(["", text + "."])
        if trip.get("volta_motorista_hora"):
            lines.append(
                f"Horário combinado para busca no aeroporto: {trip['volta_motorista_hora'].replace(':', 'h')}."
            )
    return "\n".join(lines)


def render_trip_details(trip, name, *, key, compromisso=None):
    """Shared active/history display; no logistics field is required."""

    def airport(leg):
        value = trip.get("aeroporto_" + leg) or trip.get("aeroporto")
        return (
            trip.get("aeroporto_" + leg + "_outro") or trip.get("aeroporto_outro")
            if value == "Outro"
            else value
        )

    render_html(trip_html("Viagem aérea"))
    st.caption("✈️ Viagem aérea")
    if trip.get("ida_data"):
        text = "Ida: " + datetime.fromisoformat(trip["ida_data"]).strftime("%d/%m/%Y")
        if trip.get("ida_hora"):
            text += " às " + trip["ida_hora"]
        st.write("Ida")
        st.write(text)
        if airport("ida"):
            st.write("Aeroporto de", airport("ida"))
        if trip.get("ida_motorista_hora"):
            st.write("Horário combinado para saída:", trip["ida_motorista_hora"])
    if trip.get("volta_data"):
        text = "Volta: " + datetime.fromisoformat(trip["volta_data"]).strftime(
            "%d/%m/%Y"
        )
        if trip.get("volta_chegada_hora"):
            text += " às " + trip["volta_chegada_hora"]
        st.write("Volta")
        st.write(text)
        if airport("volta"):
            st.write("Aeroporto de", airport("volta"))
        if trip.get("volta_motorista_hora"):
            st.write(
                "Horário combinado para busca no aeroporto:",
                trip["volta_motorista_hora"],
            )
    st.write(
        "✅ Motorista informado"
        if trip.get("motorista_informado")
        else "⚠ Motorista ainda não informado"
    )
    st.text_area(
        "Mensagem para o motorista",
        value=driver_message(name, trip, compromisso),
        height=180,
        key=key,
    )


def trip_form(old, prefix, default_ida, default_volta):
    """Editable logistics shared by every participating procurador."""
    old = old or {}
    with st.expander("Logística de viagem aérea", expanded=True):
        has_ida = st.checkbox(
            "Informar ida", value=bool(old.get("ida_data")), key=prefix + "has_ida"
        )
        has_return = st.checkbox(
            "Informar volta",
            value=bool(old.get("volta_data")),
            key=prefix + "has_return",
        )
        airport_ida = airport_ida_other = ida_date = ida_time = ida_driver = None
        airport_volta = airport_volta_other = return_date = return_time = (
            return_driver
        ) = None
        if has_ida:
            airport_ida = st.selectbox(
                "Aeroporto de ida",
                ("João Pessoa", "Recife", "Outro"),
                index=(
                    ("João Pessoa", "Recife", "Outro").index(
                        old.get("aeroporto_ida", "João Pessoa")
                    )
                    if old.get("aeroporto_ida", "João Pessoa")
                    in ("João Pessoa", "Recife", "Outro")
                    else 0
                ),
                key=prefix + "airport_ida",
            )
            airport_ida_other = (
                st.text_input(
                    "Outro aeroporto de ida",
                    value=old.get("aeroporto_ida_outro") or "",
                    key=prefix + "airport_ida_other",
                )
                if airport_ida == "Outro"
                else None
            )
            a, b, c = st.columns((1.2, 1, 1))
            ida_date = a.date_input(
                "Data do voo",
                value=(
                    date.fromisoformat(old["ida_data"])
                    if old.get("ida_data")
                    else default_ida
                ),
                format="DD/MM/YYYY",
                key=prefix + "ida_date",
            )
            ida_time = b.text_input(
                "Horário do voo de ida",
                value=old.get("ida_hora") or "",
                key=prefix + "ida_time",
                placeholder="HH:MM",
            )
            ida_driver = c.text_input(
                "Horário combinado para o motorista",
                value=old.get("ida_motorista_hora") or "",
                key=prefix + "ida_driver",
                placeholder="HH:MM",
            )
        if has_return:
            return_airport = old.get("aeroporto_volta") or airport_ida or "João Pessoa"
            airport_volta = st.selectbox(
                "Aeroporto de volta",
                ("João Pessoa", "Recife", "Outro"),
                index=(
                    ("João Pessoa", "Recife", "Outro").index(return_airport)
                    if return_airport in ("João Pessoa", "Recife", "Outro")
                    else 0
                ),
                key=prefix + "airport_volta",
            )
            airport_volta_other = (
                st.text_input(
                    "Outro aeroporto de volta",
                    value=old.get("aeroporto_volta_outro") or "",
                    key=prefix + "airport_volta_other",
                )
                if airport_volta == "Outro"
                else None
            )
            a, b, c = st.columns((1.2, 1, 1))
            return_date = a.date_input(
                "Data da volta",
                value=(
                    date.fromisoformat(old["volta_data"])
                    if old.get("volta_data")
                    else default_volta
                ),
                format="DD/MM/YYYY",
                key=prefix + "return_date",
            )
            return_time_key = prefix + "return_time"
            return_driver_key = prefix + "return_driver"

            def default_return_driver():
                if not st.session_state.get(return_driver_key):
                    st.session_state[return_driver_key] = st.session_state.get(
                        return_time_key, ""
                    )

            return_time = b.text_input(
                "Chegada prevista",
                value=old.get("volta_chegada_hora") or "",
                key=return_time_key,
                placeholder="HH:MM",
                on_change=default_return_driver,
            )
            return_driver = c.text_input(
                "Horário combinado da volta",
                value=old.get("volta_motorista_hora")
                or old.get("volta_chegada_hora")
                or "",
                key=return_driver_key,
                placeholder="HH:MM",
            )
        return {
            "aeroporto_ida": airport_ida,
            "aeroporto_ida_outro": airport_ida_other,
            "ida_data": ida_date.isoformat() if ida_date else None,
            "ida_hora": ida_time or None,
            "ida_motorista_hora": ida_driver or None,
            "aeroporto_volta": airport_volta,
            "aeroporto_volta_outro": airport_volta_other,
            "volta_data": return_date.isoformat() if return_date else None,
            "volta_chegada_hora": return_time or None,
            "volta_motorista_hora": return_driver or None,
            "motorista_informado": st.checkbox(
                "Motorista informado",
                value=bool(old.get("motorista_informado")),
                key=prefix + "driver_informed",
            ),
            "observacao": st.text_area(
                "Observação logística",
                value=old.get("observacao") or "",
                key=prefix + "notes",
            ),
        }


def leave_editor(agenda, people, principal):
    old = st.session_state["agenda_leave_edit"]
    prefix = (
        "agenda_leave_"
        + old.get("id", "new")
        + "_"
        + str(st.session_state.get("agenda_revision", 0))
    )
    st.subheader("Editar afastamento" if old else "Cadastrar afastamento")
    available = [p for p in people if p["ativo"] or p["id"] == old.get("procurador_id")]
    ids = [p["id"] for p in available]
    holder_id = st.selectbox(
        "Procurador",
        ids,
        index=ids.index(old["procurador_id"]) if old.get("procurador_id") in ids else 0,
        format_func=lambda value: next(
            p["nome"] for p in available if p["id"] == value
        ),
        key=prefix + "holder",
    )
    holder = next(p for p in available if p["id"] == holder_id)
    motive = st.selectbox(
        "Motivo",
        MOTIVOS,
        index=MOTIVOS.index(old.get("motivo", "Férias")),
        key=prefix + "motive",
    )
    other = (
        st.text_input(
            "Especifique o motivo",
            value=old.get("motivo_outro") or "",
            key=prefix + "other",
        )
        if motive == "Outro"
        else ""
    )
    left, right = st.columns(2)
    start = left.date_input(
        "Data inicial",
        value=(
            date.fromisoformat(old["data_inicio"])
            if old.get("data_inicio")
            else date.today()
        ),
        format="DD/MM/YYYY",
        key=prefix + "start",
    )
    end = right.date_input(
        "Data final",
        value=date.fromisoformat(old["data_fim"]) if old.get("data_fim") else start,
        format="DD/MM/YYYY",
        key=prefix + "end",
    )
    candidates = eligible_substitutes(holder, people)
    substitute = None
    if candidates:
        candidate_ids = [p["id"] for p in candidates]
        substitute = st.selectbox(
            "Substituto",
            [None, *candidate_ids],
            index=(
                [None, *candidate_ids].index(old.get("substituto_id"))
                if old.get("substituto_id") in candidate_ids
                else 0
            ),
            format_func=lambda value: (
                "Selecione"
                if value is None
                else next(p["nome"] for p in candidates if p["id"] == value)
            ),
            key=prefix + "substitute",
        )
        if substitute is None:
            role = (
                "Procurador-Geral"
                if holder.get("funcao") == "Procurador-Geral"
                else "Subprocurador-Geral"
            )
            st.warning(
                f"O afastamento do {role} pode ser salvo agora; a substituição ficará pendente."
            )
    notes = st.text_area(
        "Observação", value=old.get("observacao") or "", key=prefix + "notes"
    )
    record = {
        "id": old.get("id"),
        "procurador_id": holder_id,
        "motivo": motive,
        "motivo_outro": other,
        "data_inicio": start.isoformat(),
        "data_fim": end.isoformat(),
        "substituto_id": substitute,
        "observacao": notes,
    }
    if agenda.leave_substitute_warning(
        substitute, record["data_inicio"], record["data_fim"], record["id"]
    ):
        st.warning(
            "Atenção: este procurador já está indicado como substituto em outro afastamento durante parte deste período."
        )
    if st.button("Salvar afastamento", type="primary", key=prefix + "save"):
        try:
            identifier = agenda.save_leave(
                record, created_by=getattr(principal, "email", None)
            )
            audit_agenda(
                "AFASTAMENTO_EDITADO" if old.get("id") else "AFASTAMENTO_CRIADO",
                "ALTERAR" if old.get("id") else "CRIAR",
                identifier,
                {
                    "procurador": holder["nome"],
                    "periodo": f"{record['data_inicio']} a {record['data_fim']}",
                    "motivo": motive,
                    "substituto": next(
                        (p["nome"] for p in people if p["id"] == substitute), None
                    ),
                },
                "afastamento",
            )
            done(
                "Afastamento salvo. Substituto ainda não definido."
                if substitution_pending(record, people)
                else "Afastamento salvo com sucesso."
            )
        except ValueError as exc:
            st.error(str(exc))
    if st.button("Voltar à agenda", key=prefix + "back"):
        st.session_state.pop("agenda_leave_edit", None)
        st.rerun()


def render_week_analysis(start, end, appointments, leaves, names):
    """Optional briefing for the week already shown. Gemini runs only on click."""
    context, signature = contexto_semana(start, end, appointments, leaves, names)
    period = context["periodo"]
    stored = st.session_state.get("agenda_semana_ia")
    text, stale = leitura_semana_salva(
        stored, period["inicio"], period["fim"], signature
    )
    if st.button(
        "↻ Atualizar análise" if text else "✨ Analisar semana com IA",
        key="agenda_semana_analisar",
    ):
        try:
            from services.ai_service import (
                GeminiErro,
                GeminiNaoConfigurada,
                analisar_semana_agenda,
            )

            with st.spinner("Analisando a semana..."):
                briefing = analisar_semana_agenda(context)
        except (GeminiErro, GeminiNaoConfigurada) as exc:
            st.error(str(exc))
        except Exception as exc:
            LOGGER.warning("Análise da semana falhou (%s).", type(exc).__name__)
            st.error("Não foi possível gerar a análise da semana no momento.")
        else:
            st.session_state["agenda_semana_ia"] = {
                "inicio": period["inicio"],
                "fim": period["fim"],
                "assinatura": signature,
                "texto": briefing,
            }
            st.rerun()
    if stale:
        st.caption(AVISO_SEMANA_ALTERADA)
    if text:
        st.markdown(text)
        st.caption(AVISO_SEMANA_IA)


def render(store=None, principal=None):
    from database.store import Store, unwrap_store
    from services.access import current_user, require_permission

    st.header(module_title("agenda", "AGENDA E AFASTAMENTOS DOS PROCURADORES"))
    if store is None:
        store = Store()
    else:
        store = unwrap_store(store)
    existing = (
        st.session_state["agenda_store"] if "agenda_store" in st.session_state else None
    )
    held = (
        unwrap_store(getattr(existing, "store", None)) if existing is not None else None
    )
    if type(existing) is AgendaStore and held is store:
        agenda = existing
    else:
        # Reload leftover session objects; prefer a real Store over a display proxy.
        agenda = AgendaStore(held if held is not None else store)
        st.session_state["agenda_store"] = agenda
    if principal is None:
        principal = current_user(agenda.store)
    require_permission(principal, "agenda")
    people = display_store(agenda.store).catalog("procuradores")
    names = {p["id"]: p["nome"] for p in people}
    if message := st.session_state.pop("agenda_message", None):
        st.success(message)
    consume_pending_open_agenda(agenda)
    agenda_edit = st.session_state.get("agenda_edit")
    if agenda_edit is not None and not agenda_edit.get("id"):
        editor(agenda, people, principal)
    agenda_leave_edit = st.session_state.get("agenda_leave_edit")
    if agenda_leave_edit is not None and not agenda_leave_edit.get("id"):
        leave_editor(agenda, people, principal)
    section = st.radio(
        "Seção", ["Agenda", "Histórico"], horizontal=True, key="agenda_section"
    )
    if section == "Histórico":
        with st.container(border=True):
            filter_mark()
            a, b, c = st.columns(3)
            history_member = a.selectbox(
                "Procurador",
                [None, *names],
                format_func=lambda p: names.get(p, "Todos"),
                key="agenda_history_member",
            )
            history_scope = b.selectbox(
                "Tipo de item",
                ("Todos", "Compromissos", "Afastamentos"),
                key="agenda_history_scope",
            )
            history_status = c.selectbox(
                "Situação",
                [None, "Realizado", "Cancelado", "ENCERRADO", "CANCELADO"],
                format_func=lambda value: value or "Todas",
                key="agenda_history_status",
            )
            d, e, f = st.columns(3)
            history_start = d.date_input(
                "Período inicial",
                value=None,
                key="agenda_history_start",
                format="DD/MM/YYYY",
            )
            history_end = e.date_input(
                "Período final",
                value=None,
                key="agenda_history_end",
                format="DD/MM/YYYY",
            )
            history_search = f.text_input("Busca", key="agenda_history_search")
        signature = (
            history_member,
            history_scope,
            history_status,
            history_start,
            history_end,
            history_search,
            st.session_state.get("agenda_revision", 0),
        )
        if st.session_state.get("agenda_history_filters") != signature:
            st.session_state["agenda_history_offset"] = 0
            st.session_state["agenda_history_filters"] = signature
        offset = st.session_state.get("agenda_history_offset", 0)
        start_iso = history_start.isoformat() if history_start else None
        end_iso = (history_end + timedelta(days=1)).isoformat() if history_end else None
        appointments = (
            agenda.history(
                member=history_member,
                status=(
                    history_status
                    if history_status in ("Realizado", "Cancelado")
                    else None
                ),
                start=start_iso,
                end=end_iso,
                search=history_search or None,
                offset=offset,
            )
            if history_scope != "Afastamentos"
            else []
        )
        leaves = (
            agenda.history_leaves(
                member=history_member,
                status=(
                    history_status
                    if history_status in ("ENCERRADO", "CANCELADO")
                    else None
                ),
                start=start_iso,
                end=end_iso,
                offset=offset,
            )
            if history_scope != "Compromissos"
            else []
        )
        for row in leaves:
            row["inicio"] = row["data_fim"] + "T00:00:00"
            row["afastamento"] = True
        rows = [*appointments, *leaves]
        rows.sort(key=lambda row: (row["inicio"], row["id"]), reverse=True)
        has_next = len(appointments) > 30 or len(leaves) > 30
        rows = rows[:30]
        trips = agenda.trips_for_commitments(
            row["id"] for row in rows if not row.get("afastamento")
        )
        if not rows:
            empty_state("Nenhum item histórico para os filtros selecionados.")
        for index, row in enumerate(rows):
            with card_container(index, f"agh_{row['id']}"):
                if row.get("afastamento"):
                    render_record(
                        "AFASTAMENTO — "
                        + str(names.get(row["procurador_id"], row["procurador_id"])),
                        badges_html=badges(
                            ("Afastamento", "neutral"),
                            (row["status"], status_tone(row["status"])),
                        ),
                        secondary=f"{row['motivo']} · {format_date_br(row['data_inicio'])} a {format_date_br(row['data_fim'])}",
                        meta=(
                            (
                                "Substituto(a): "
                                + names.get(
                                    row["substituto_id"], str(row["substituto_id"])
                                )
                            )
                            if row.get("substituto_id")
                            else (row.get("observacao") or "")
                        ),
                        accent="muted",
                    )
                    if row.get("observacao") and row.get("substituto_id"):
                        st.write(row["observacao"])
                    st.button(
                        "Abrir detalhes do afastamento",
                        key="agenda_history_leave_" + row["id"],
                        on_click=start_leave_edit,
                        args=(agenda, row["id"]),
                    )
                else:
                    hour = (
                        "Dia inteiro" if row.get("sem_hora") else row["inicio"][11:16]
                    )
                    title = row.get("titulo") or row.get("processo") or "Compromisso"
                    situation = row.get("situacao") or ""
                    render_record(
                        title,
                        badges_html=badges(
                            (TYPES[row["tipo"]], "brand"),
                            (situation, status_tone(situation)),
                        ),
                        secondary=display_datetime(row["inicio"], row.get("sem_hora"))
                        + " · "
                        + hour,
                        meta=" · ".join(
                            part
                            for part in (
                                " / ".join(
                                    names.get(p, str(p)) for p in row["procuradores"]
                                ),
                                row.get("local") or "",
                            )
                            if part
                        ),
                        accent=(
                            "muted"
                            if situation == "Cancelado"
                            else "success" if situation == "Realizado" else "brand"
                        ),
                    )
                    for procurador_id, trip in trips.get(row["id"], {}).items():
                        st.markdown("✈️ **Logística de viagem**")
                        st.write(names.get(procurador_id, str(procurador_id)))
                        render_trip_details(
                            trip,
                            names.get(procurador_id, ""),
                            key=f"agenda_history_trip_message_{row['id']}_{procurador_id}",
                            compromisso=row.get("titulo") or row.get("processo"),
                        )
                    st.button(
                        "Abrir detalhes",
                        key="agenda_history_edit_" + row["id"],
                        on_click=start_appointment_edit,
                        args=(row,),
                    )
            if row.get("afastamento"):
                editing = st.session_state.get("agenda_leave_edit")
                if editing and editing.get("id") == row["id"]:
                    leave_editor(agenda, people, principal)
            else:
                editing = st.session_state.get("agenda_edit")
                if editing and editing.get("id") == row["id"]:
                    editor(agenda, people, principal)
        previous, following = st.columns(2)
        previous.button(
            "Anterior",
            disabled=offset == 0,
            key="agenda_history_previous",
            on_click=move_page,
            args=("agenda_history_offset", -30),
        )
        following.button(
            "Próxima",
            disabled=not has_next,
            key="agenda_history_next",
            on_click=move_page,
            args=("agenda_history_offset", 30),
        )
        return
    if (
        "agenda_edit" not in st.session_state
        and "agenda_leave_edit" not in st.session_state
    ):
        new, leave, _ = st.columns([1.6, 1.8, 4.8])
        if new.button("+ Novo compromisso", type="primary", key="agenda_new"):
            st.session_state.pop("agenda_leave_edit", None)
            st.session_state["agenda_edit"] = {}
            st.rerun()
        if leave.button(
            "Cadastrar afastamento", type="primary", key="agenda_new_leave"
        ):
            st.session_state.pop("agenda_edit", None)
            st.session_state["agenda_leave_edit"] = {}
            st.rerun()
    with st.container(border=True):
        filter_mark()
        a, b, c, d = st.columns(4)
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
        item_scope = d.selectbox(
            "Tipo de item",
            ("Todos", "Somente compromissos", "Somente afastamentos"),
            key="agenda_filter_item_scope",
        )
    show_appointments = item_scope != "Somente afastamentos"
    show_leaves = item_scope != "Somente compromissos"
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
        rows = (
            records(agenda, today.isoformat(), None, member, kind, status, active=True)
            if show_appointments
            else []
        )
        leaves = (
            agenda.active_leaves(today.isoformat(), None, member, upcoming=True)
            if show_leaves
            and (item_scope == "Somente afastamentos" or (not kind and not status))
            else []
        )
    else:
        rows = (
            records(
                agenda,
                start.isoformat(),
                end.isoformat(),
                member,
                kind,
                status,
                active=True,
            )
            if show_appointments
            else []
        )
        leaves = (
            agenda.active_leaves(
                start.isoformat(), (end - timedelta(days=1)).isoformat(), member
            )
            if show_leaves
            and (item_scope == "Somente afastamentos" or (not kind and not status))
            else []
        )
    if rows or leaves:
        st.caption(_listing_summary(len(rows), len(leaves)))
    if view == "Semana":
        render_week_analysis(start, end, rows, leaves, names)
    for leave_record in leaves:
        leave_record["inicio"] = leave_record["data_inicio"] + "T00:00:00"
        leave_record["afastamento"] = True
    rows.extend(leaves)
    rows.sort(
        key=lambda row: (
            row["inicio"][:10],
            bool(row.get("afastamento")),
            row["inicio"],
            row["id"],
        )
    )
    trips = agenda.trips_for_commitments(
        row["id"] for row in rows if not row.get("afastamento")
    )
    if not rows:
        empty_state("Nenhum compromisso no período selecionado.")
    current_day = current_group = None
    for index, row in enumerate(rows):
        if row["inicio"][:10] != current_day:
            current_day = row["inicio"][:10]
            current_group = None
            st.subheader(datetime.fromisoformat(current_day).strftime("%d/%m/%Y"))
        group = "Afastamentos" if row.get("afastamento") else "Compromissos"
        if group != current_group:
            current_group = group
            st.markdown(f"#### {group}")
        if row.get("afastamento"):
            with card_container(index, f"agl_{row['id']}"):
                render_record(
                    "AFASTAMENTO — "
                    + str(names.get(row["procurador_id"], row["procurador_id"])),
                    badges_html=badges(
                        ("Afastamento", "neutral"),
                        (row["status"], status_tone(row["status"])),
                    ),
                    secondary=f"{row['motivo']}{' · ' + row['motivo_outro'] if row.get('motivo_outro') else ''} · {display_datetime(row['inicio'], True)} a {datetime.fromisoformat(row['data_fim']).strftime('%d/%m/%Y')}",
                    meta=(
                        (
                            "Substituto(a): "
                            + names.get(row["substituto_id"], str(row["substituto_id"]))
                        )
                        if row.get("substituto_id")
                        else ""
                    ),
                    accent="muted",
                )
                if not row.get("substituto_id") and substitution_pending(row, people):
                    st.warning("⚠ Substituto ainda não definido")
                st.button(
                    "Editar afastamento",
                    key="agenda_leave_edit_" + row["id"],
                    on_click=start_leave_edit,
                    args=(agenda, row["id"]),
                )
                if row["status"] != "CANCELADO" and st.button(
                    "Cancelar afastamento", key="agenda_leave_cancel_" + row["id"]
                ):
                    agenda.cancel_leave(row["id"])
                    audit_agenda(
                        "AFASTAMENTO_CANCELADO",
                        "CANCELAR",
                        row["id"],
                        entity_type="afastamento",
                    )
                    done("Afastamento cancelado; registro preservado.")
            editing = st.session_state.get("agenda_leave_edit")
            if editing and editing.get("id") == row["id"]:
                leave_editor(agenda, people, principal)
            continue
        # From here down, every record is a compromisso and has its own fields.
        hour = "Dia inteiro" if row["sem_hora"] else row["inicio"][11:16]
        title = row.get("titulo") or row.get("processo")
        past = (
            row["situacao"] not in ("Realizado", "Cancelado")
            and date.fromisoformat(row["inicio"][:10]) < today
        )
        situation = row["situacao"]
        accent = "muted" if situation == "Cancelado" else "warning" if past else "brand"
        with card_container(index, f"ag_{row['id']}"):
            render_record(
                title or "Compromisso",
                badges_html=badges(
                    (TYPES[row["tipo"]], "brand"),
                    (situation, status_tone(situation)),
                ),
                secondary=f"{hour} · {TYPES[row['tipo']]}",
                meta=" · ".join(
                    part
                    for part in (
                        " / ".join(names.get(p, str(p)) for p in row["procuradores"]),
                        row.get("local") or "",
                    )
                    if part
                ),
                accent=accent,
            )
            if past:
                st.warning("⚠ Compromisso passado ainda não encerrado")
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
                for procurador_id, trip in trips.get(row["id"], {}).items():
                    st.markdown("✈️ **Logística de viagem**")
                    st.write(names.get(procurador_id, str(procurador_id)))
                    render_trip_details(
                        trip,
                        names.get(procurador_id, ""),
                        key=f"agenda_trip_message_{row['id']}_{procurador_id}",
                        compromisso=title,
                    )
                st.button(
                    "Editar",
                    key="agenda_edit_" + row["id"],
                    on_click=start_appointment_edit,
                    args=(row,),
                )
                if row["situacao"] != "Cancelado" and st.button(
                    "Cancelar compromisso", key="agenda_cancel_" + row["id"]
                ):
                    agenda.cancel(row["id"])
                    audit_agenda("COMPROMISSO_CANCELADO", "CANCELAR", row["id"])
                    done("Compromisso cancelado; registro preservado.")
                confirmed = st.checkbox(
                    "Confirmo a exclusão definitiva deste compromisso.",
                    key="agenda_delete_confirm_" + row["id"],
                )
                if st.button(
                    "Excluir", key="agenda_delete_" + row["id"], disabled=not confirmed
                ):
                    agenda.delete(row["id"], confirmed=confirmed)
                    audit_agenda("COMPROMISSO_EXCLUIDO", "EXCLUIR", row["id"])
                    done("Compromisso excluído.")
        editing = st.session_state.get("agenda_edit")
        if editing and editing.get("id") == row["id"]:
            editor(agenda, people, principal)

    export_signature = (
        view,
        start.isoformat(),
        end.isoformat() if view != "Próximos" else None,
        member,
        kind,
        status,
        item_scope,
        st.session_state.get("agenda_revision", 0),
    )
    pdf_actions = st.container(horizontal_alignment="center", key="agenda_pdf_actions")
    if pdf_actions.button(
        "📄 Gerar PDF da listagem",
        key="agenda_generate_pdf",
        disabled=not rows,
    ):
        from document_generator.agenda_pdf import generate_agenda_pdf

        filter_labels = [
            "Período: "
            + (
                "a partir de " + today.strftime("%d/%m/%Y")
                if view == "Próximos"
                else start.strftime("%d/%m/%Y")
                + " a "
                + (end - timedelta(days=1)).strftime("%d/%m/%Y")
            ),
            "Procurador: " + names.get(member, "Todos"),
            "Tipo de item: " + item_scope,
            "Tipo de compromisso: " + TYPES.get(kind, "Todos"),
            "Situação: " + (status or "Todas"),
        ]
        last_day = None if view == "Próximos" else end - timedelta(days=1)
        st.session_state["agenda_pdf_export"] = {
            "signature": export_signature,
            "data": generate_agenda_pdf(rows, names, filter_labels),
            "name": agenda_pdf_filename(start, last_day),
        }
    export = st.session_state.get("agenda_pdf_export")
    if export and export["signature"] == export_signature:
        pdf_actions.download_button(
            "Baixar agenda em PDF",
            export["data"],
            export["name"],
            mime="application/pdf",
            key="agenda_download_pdf",
        )
