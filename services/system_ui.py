"""Administration screens for operational health and logical backup."""

from pathlib import Path
import logging

import streamlit as st

from services.access import require_permission
from services.audit import format_local, registrar_evento
from services.backup import backup_filename, generate_backup, unique_backup_path
from services.system_health import ATTENTION, ERROR, OK, diagnose

LOGGER = logging.getLogger("mpc.sistema")
HEALTH_KEY = "sistema_health"
BACKUP_RESULT = "sistema_backup_result"
BACKUP_ERROR = "sistema_backup_error"
BACKUP_RUNNING = "sistema_backup_running"
BACKUP_OFFERED = "sistema_backup_offered"


def _tone(status):
    if status == OK:
        return "green"
    if status == ATTENTION:
        return "orange"
    return "red"


def _badge(status, summary):
    color = _tone(status)
    st.markdown(f":{color}[**{status}**] — {summary}")


def _event_line(item):
    if not item:
        return "—"
    when = format_local(item.get("criado_em"))
    event = item.get("evento") or ""
    return f"{when} · {event}".strip(" ·")


def render_health(store, principal):
    require_permission(principal, "admin")
    st.subheader("Saúde do sistema")
    st.write(
        "Visão operacional do Ferramentas MPC-PB. Os testes não alteram o schema "
        "nem geram documentos."
    )
    refresh = st.button("Atualizar diagnóstico", key="sistema_health_refresh")
    if refresh or HEALTH_KEY not in st.session_state:
        st.session_state[HEALTH_KEY] = diagnose(store)
        if refresh:
            from services.alerts import invalidate_alert_summary

            invalidate_alert_summary()
    report = st.session_state[HEALTH_KEY]
    _badge(report["status"], "estado geral do portal")
    st.caption(
        "Verificado em "
        + format_local(report["checked_at"])
        + " (exibido em America/Recife)."
    )

    database = report["database"]
    schema = report["schema"]
    audit = report["audit"]
    documents = report["documents"]
    application = report["application"]
    activity = report["activity"]

    a, b = st.columns(2)
    with a, st.container(border=True):
        st.markdown("**Banco de dados**")
        _badge(database["status"], database["summary"].split(" — ", 1)[-1])
        st.write(database["summary"])
        st.caption(
            "Engine: "
            + database.get("engine", "—")
            + " · Ambiente: "
            + str(database.get("environment") or "—")
        )
        if database.get("latency_ms") is not None:
            st.caption(f"Latência aproximada: {database['latency_ms']} ms")
        st.caption("Horário da verificação: " + format_local(database.get("checked_at")))
    with b, st.container(border=True):
        st.markdown("**Schema**")
        _badge(schema["status"], schema["summary"].split(" — ", 1)[-1])
        st.write(schema["summary"])
        st.caption(
            f"{schema.get('tables_present', 0)}/{schema.get('tables_expected', 0)} "
            "estruturas essenciais disponíveis"
        )
        st.caption(
            f"Marcadores: {schema.get('markers_ok', 0)}/"
            f"{len(schema.get('expected_markers') or [])}"
        )
        if schema.get("sqlite_user_version") is not None:
            st.caption(f"SQLite user_version: {schema['sqlite_user_version']}")
        if schema.get("postgres_schema_versions"):
            st.caption(
                "schema_migrations: "
                + ", ".join(str(v) for v in schema["postgres_schema_versions"])
            )
        missing = (schema.get("missing_tables") or []) + (
            schema.get("missing_columns") or []
        )
        if missing:
            st.write("Ausências: " + ", ".join(missing[:12]))
        with st.expander("Ver detalhes técnicos"):
            for item in schema.get("items") or []:
                mark = "presente" if item["presente"] else "ausente"
                st.write(f"{item['nome']}: {mark}")
            for marker in schema.get("markers") or []:
                mark = "aplicado" if marker["presente"] else "ausente"
                st.write(f"{marker['chave']}: {mark}")

    c, d = st.columns(2)
    with c, st.container(border=True):
        st.markdown("**Auditoria**")
        _badge(audit["status"], audit["summary"].split(" — ", 1)[-1])
        st.write(audit["summary"])
        if audit.get("last_event"):
            st.caption("Evento: " + str(audit["last_event"]))
        st.caption(f"Eventos nas últimas 24 horas: {audit.get('events_24h', 0)}")
        st.caption(
            "Erros operacionais registrados nas últimas 24 horas: "
            + str(audit.get("errors_24h", 0))
        )
    with d, st.container(border=True):
        st.markdown("**Documentos**")
        docx = documents["docx"]
        pdf = documents["pdf"]
        st.write("Geração DOCX")
        _badge(docx["status"], docx["summary"])
        st.write("Conversão para PDF")
        _badge(pdf["status"], pdf["summary"])

    with st.container(border=True):
        st.markdown("**Aplicação**")
        _badge(application["status"], application["summary"].split(" — ", 1)[-1])
        st.write(application["summary"])
        st.caption(f"Python: {application.get('python')}")
        st.caption(f"Streamlit: {application.get('streamlit')}")
        st.caption(f"Build: {application.get('build')}")
        st.caption(f"Ambiente: {application.get('environment')}")
        st.caption(
            "Horário do servidor (America/Recife): " + application.get("server_time", "—")
        )

    with st.container(border=True):
        st.markdown("**Atividade**")
        st.write("Último acesso registrado: " + _event_line(activity.get("last_access")))
        st.write(
            "Último documento finalizado: " + _event_line(activity.get("last_document"))
        )
        st.write(
            "Última alteração administrativa: "
            + _event_line(activity.get("last_admin"))
        )
        st.write("Última atividade do sistema: " + _event_line(activity.get("last_activity")))

    with st.container(border=True):
        st.markdown("**Erros recentes**")
        st.caption("Registros históricos da Auditoria; não indicam falha atual por si só.")
        errors = audit.get("errors_24h") or 0
        if errors:
            label = "erro operacional registrado" if errors == 1 else "erros operacionais registrados"
            st.write(f"{errors} {label} nas últimas 24 horas")
            if st.button("Ver na Auditoria", key="sistema_goto_audit"):
                from services.access_ui import request_admin_navigation

                request_admin_navigation(
                    secao="Acessos e Auditoria",
                    audit_tab="Auditoria",
                )
        else:
            st.write("Nenhum erro operacional registrado nas últimas 24 horas.")
        week = audit.get("errors_7d") or 0
        if week and week != errors:
            st.caption(
                f"{week} erro(s) operacional(is) registrado(s) nos últimos 7 dias."
            )


def _clear_backup_file(info):
    path = Path(info["caminho"]) if info and info.get("caminho") else None
    if path and path.is_file():
        path.unlink(missing_ok=True)


def render_backup(store, principal):
    require_permission(principal, "admin")
    st.subheader("Backup administrativo")
    st.warning(
        "O backup pode conter dados e documentos institucionais. Armazene-o em local seguro."
    )
    st.write(
        "Gera um arquivo ZIP lógico das tabelas da aplicação, compatível com "
        "SQLite e PostgreSQL. A restauração automática não está disponível nesta versão."
    )
    previous = st.session_state.get(BACKUP_RESULT)
    if previous:
        st.info(
            "Último backup gerado nesta sessão: "
            + previous.get("gerado_em_local", "—")
        )
    running = bool(st.session_state.get(BACKUP_RUNNING))
    generate = st.button(
        "Gerar backup",
        type="primary",
        disabled=running,
        key="sistema_backup_gerar",
    )
    if generate and not running:
        st.session_state[BACKUP_RUNNING] = True
        path = unique_backup_path()
        try:
            with st.status("Gerando backup…", expanded=True) as status:
                def progress(message):
                    status.write(message)

                result = generate_backup(store, principal, path, progress=progress)
                status.update(label="Backup concluído", state="complete")
            st.session_state[BACKUP_RESULT] = result
            st.session_state.pop(BACKUP_ERROR, None)
            st.session_state[BACKUP_OFFERED] = None
            if previous and previous.get("caminho") != result.get("caminho"):
                _clear_backup_file(previous)
        except ValueError as exc:
            path.unlink(missing_ok=True)
            st.session_state[BACKUP_ERROR] = str(exc)
        except Exception:
            path.unlink(missing_ok=True)
            LOGGER.exception("Falha visível ao gerar backup")
            st.session_state[BACKUP_ERROR] = "Não foi possível gerar o backup."
        finally:
            st.session_state[BACKUP_RUNNING] = False
        st.rerun()

    error = st.session_state.get(BACKUP_ERROR)
    if error:
        st.error(error)

    result = st.session_state.get(BACKUP_RESULT)
    if not result:
        return
    path = Path(result["caminho"])
    if not path.is_file():
        st.warning("O arquivo temporário deste backup não está mais disponível. Gere novamente.")
        st.session_state.pop(BACKUP_RESULT, None)
        return
    st.success("Backup pronto para download.")
    st.write(f"Horário: {result.get('gerado_em_local')}")
    st.write(f"Tabelas: {result.get('quantidade_tabelas')}")
    st.write(f"Registros: {result.get('quantidade_registros')}")
    st.write(f"Documentos: {result.get('quantidade_documentos')}")
    st.write(f"Tamanho: {result.get('tamanho')} bytes")
    if result.get("sha256"):
        st.caption("SHA-256: " + result["sha256"])
    data = path.read_bytes()
    offered = st.download_button(
        "Baixar backup",
        data=data,
        file_name=result.get("arquivo") or backup_filename(),
        mime="application/zip",
        key="sistema_backup_download_" + str(result.get("sha256") or "")[:16],
    )
    token = result.get("sha256")
    if token and st.session_state.get(BACKUP_OFFERED) != token:
        registrar_evento(
            store,
            evento="BACKUP_DISPONIBILIZADO",
            modulo="admin",
            acao="BACKUP",
            resultado="OK",
            principal=principal,
            entidade_tipo="backup",
            entidade_id=result.get("arquivo"),
            detalhes={
                "tabelas": result.get("quantidade_tabelas"),
                "registros": result.get("quantidade_registros"),
                "documentos": result.get("quantidade_documentos"),
                "tamanho": result.get("tamanho"),
                "sha256": result.get("sha256"),
            },
        )
        st.session_state[BACKUP_OFFERED] = token
    if offered:
        st.caption("O download foi solicitado neste navegador.")


def render(store, principal):
    require_permission(principal, "admin")
    st.subheader("Sistema")
    area = st.radio(
        "Sistema",
        ["Saúde", "Backup"],
        horizontal=True,
        key="admin_sistema_aba",
    )
    if area == "Backup":
        render_backup(store, principal)
        return
    render_health(store, principal)
