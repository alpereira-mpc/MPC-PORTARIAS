"""Operational health checks for Ferramentas MPC-PB. Read-only; no schema writes."""

from datetime import datetime, timedelta, timezone
import importlib
import os
import platform
import subprocess
import sys
import time

from database.inventory import (
    ESSENTIAL_COLUMNS,
    SCHEMA_MARKERS,
    essential_tables,
)
from database.store import unwrap_store
from services.audit import INSTITUTIONAL_TZ, format_local
import re

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

OK = "OK"
ATTENTION = "ATENÇÃO"
ERROR = "ERRO"
APP_NAME = "Ferramentas MPC-PB"
_VERSION_CACHE = None


def _status(*parts):
    values = [p for p in parts if p]
    if ERROR in values:
        return ERROR
    if ATTENTION in values:
        return ATTENTION
    return OK


def list_tables(connection, backend):
    if backend == "postgresql":
        rows = connection.execute(
            "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname=current_schema()"
        ).fetchall()
        return {r[0] for r in rows}
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def list_columns(connection, backend, table):
    if not IDENT.match(table):
        return set()
    if backend == "postgresql":
        rows = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema=current_schema() AND table_name=?",
            (table,),
        ).fetchall()
        return {r[0] for r in rows}
    rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def app_version():
    global _VERSION_CACHE
    if _VERSION_CACHE is not None:
        return _VERSION_CACHE
    from database.store import ROOT

    text = (ROOT / "app.py").read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("VERSION = "):
            _VERSION_CACHE = line.split("=", 1)[1].strip().strip("\"'")
            return _VERSION_CACHE
    _VERSION_CACHE = ""
    return _VERSION_CACHE


def git_build():
    from database.store import ROOT

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=8", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = (completed.stdout or "").strip()
    return value or None


def runtime_environment():
    if os.environ.get("STREAMLIT_CLOUD") or os.environ.get("STREAMLIT_SHARING_MODE"):
        return "Streamlit Cloud"
    hostname = (os.environ.get("HOSTNAME") or "").casefold()
    if hostname.startswith("streamlit") or "streamlitapp" in hostname:
        return "Streamlit Cloud"
    return "local"


def ping_database(store):
    store = unwrap_store(store)
    started = time.perf_counter()
    checked = datetime.now(timezone.utc).isoformat()
    try:
        with store.connection(read_only=True) as connection:
            row = connection.execute("SELECT 1").fetchone()
            if row is None or int(row[0]) != 1:
                raise ValueError("consulta vazia")
        latency = int(round((time.perf_counter() - started) * 1000))
        engine = "PostgreSQL" if store.backend == "postgresql" else "SQLite"
        if store.backend == "postgresql":
            summary = f"{OK} — PostgreSQL conectado — {latency} ms"
            detail = "schema da aplicação (nome omitido)"
        else:
            summary = f"{OK} — SQLite local — {latency} ms"
            detail = "arquivo local"
        return {
            "status": OK,
            "engine": engine,
            "backend": store.backend,
            "environment": runtime_environment(),
            "latency_ms": latency,
            "checked_at": checked,
            "summary": summary,
            "detail": detail,
        }
    except Exception:
        engine = (
            "PostgreSQL"
            if getattr(store, "backend", None) == "postgresql"
            else "SQLite"
        )
        return {
            "status": ERROR,
            "engine": engine,
            "backend": getattr(store, "backend", "unknown"),
            "environment": runtime_environment(),
            "latency_ms": None,
            "checked_at": checked,
            "summary": f"{ERROR} — Não foi possível consultar o banco",
            "detail": "",
        }


def inspect_schema(store):
    store = unwrap_store(store)
    expected = essential_tables(store.backend)
    missing_tables = []
    missing_columns = []
    markers = []
    sqlite_version = None
    pg_versions = []
    try:
        with store.connection(read_only=True) as connection:
            present = list_tables(connection, store.backend)
            for table in expected:
                if table not in present:
                    missing_tables.append(table)
                    continue
                wanted = ESSENTIAL_COLUMNS.get(table, ())
                if wanted:
                    columns = list_columns(connection, store.backend, table)
                    for column in wanted:
                        if column not in columns:
                            missing_columns.append(f"{table}.{column}")
            marker_rows = connection.execute(
                "SELECT chave FROM configuracoes WHERE chave IN ({})".format(
                    ",".join("?" * len(SCHEMA_MARKERS))
                ),
                SCHEMA_MARKERS,
            ).fetchall()
            found_markers = {r[0] for r in marker_rows}
            for marker in SCHEMA_MARKERS:
                markers.append(
                    {
                        "chave": marker,
                        "presente": marker in found_markers,
                    }
                )
            if store.backend == "sqlite":
                sqlite_version = connection.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]
            else:
                pg_versions = [
                    r[0]
                    for r in connection.execute(
                        "SELECT version FROM schema_migrations ORDER BY version"
                    )
                ]
    except Exception:
        return {
            "status": ERROR,
            "summary": f"{ERROR} — Não foi possível validar o schema",
            "expected_markers": list(SCHEMA_MARKERS),
            "markers": [],
            "markers_ok": 0,
            "tables_present": 0,
            "tables_expected": len(expected),
            "missing_tables": [],
            "missing_columns": [],
            "sqlite_user_version": None,
            "postgres_schema_versions": [],
            "items": [],
        }
    missing_markers = [m["chave"] for m in markers if not m["presente"]]
    version_issue = False
    if store.backend == "sqlite" and sqlite_version != 2:
        version_issue = True
    if store.backend == "postgresql" and pg_versions != [1]:
        version_issue = True
    components = missing_tables + missing_columns + missing_markers
    if version_issue:
        components.append(
            "user_version"
            if store.backend == "sqlite"
            else "schema_migrations"
        )
    if missing_tables or missing_columns or missing_markers or version_issue:
        status = ATTENTION
        if missing_tables:
            shown = ", ".join(missing_tables[:8])
            summary = f"{ATTENTION} — Estrutura esperada não encontrada ({shown})"
        elif missing_columns:
            shown = ", ".join(missing_columns[:8])
            summary = f"{ATTENTION} — Estrutura esperada não encontrada ({shown})"
        else:
            summary = f"{ATTENTION} — Marcadores de schema incompletos"
    else:
        status = OK
        summary = f"{OK} — Schema íntegro"
    items = [{"nome": table, "presente": table not in missing_tables} for table in expected]
    present_count = sum(1 for item in items if item["presente"])
    return {
        "status": status,
        "summary": summary,
        "expected_markers": list(SCHEMA_MARKERS),
        "markers": markers,
        "markers_ok": sum(1 for m in markers if m["presente"]),
        "tables_present": present_count,
        "tables_expected": len(expected),
        "missing_tables": missing_tables,
        "missing_columns": missing_columns,
        "sqlite_user_version": sqlite_version,
        "postgres_schema_versions": pg_versions,
        "items": items,
        "components": components,
    }


def inspect_audit(store):
    store = unwrap_store(store)
    now = datetime.now(timezone.utc)
    start = (now - timedelta(hours=24)).isoformat()
    week = (now - timedelta(days=7)).isoformat()
    try:
        with store.connection(read_only=True) as connection:
            tables = list_tables(connection, store.backend)
            if "auditoria_eventos" not in tables:
                return {
                    "status": ATTENTION,
                    "summary": f"{ATTENTION} — Estrutura esperada não encontrada (auditoria_eventos)",
                    "last_event": None,
                    "last_event_at": None,
                    "events_24h": 0,
                    "errors_24h": 0,
                    "errors_7d": 0,
                }
            last = connection.execute(
                "SELECT evento,criado_em FROM auditoria_eventos "
                "ORDER BY criado_em DESC, id DESC LIMIT 1"
            ).fetchone()
            events_24h = connection.execute(
                "SELECT COUNT(*) FROM auditoria_eventos WHERE criado_em>=?",
                (start,),
            ).fetchone()[0]
            errors_24h = connection.execute(
                "SELECT COUNT(*) FROM auditoria_eventos "
                "WHERE evento='ERRO_OPERACIONAL' AND criado_em>=?",
                (start,),
            ).fetchone()[0]
            errors_7d = connection.execute(
                "SELECT COUNT(*) FROM auditoria_eventos "
                "WHERE evento='ERRO_OPERACIONAL' AND criado_em>=?",
                (week,),
            ).fetchone()[0]
    except Exception:
        return {
            "status": ERROR,
            "summary": f"{ERROR} — Auditoria indisponível",
            "last_event": None,
            "last_event_at": None,
            "events_24h": 0,
            "errors_24h": 0,
            "errors_7d": 0,
        }
    if last is None:
        return {
            "status": ATTENTION,
            "summary": f"{ATTENTION} — Nenhum evento de auditoria registrado",
            "last_event": None,
            "last_event_at": None,
            "events_24h": 0,
            "errors_24h": int(errors_24h),
            "errors_7d": int(errors_7d),
        }
    return {
        "status": OK,
        "summary": f"{OK} — Último evento: {format_local(last[1])}",
        "last_event": last[0],
        "last_event_at": last[1],
        "events_24h": int(events_24h),
        "errors_24h": int(errors_24h),
        "errors_7d": int(errors_7d),
    }


def inspect_documents():
    docx_ok = True
    try:
        importlib.import_module("lxml.etree")
        importlib.import_module("document_generator.docx")
    except Exception:
        docx_ok = False
    from document_generator.pdf import libreoffice_path

    libreoffice = libreoffice_path()
    word = False
    if sys.platform == "win32":
        try:
            importlib.import_module("win32com.client")
            word = True
        except Exception:
            word = False
    pdf_ok = bool(libreoffice or word)
    return {
        "docx": {
            "status": OK if docx_ok else ERROR,
            "summary": OK if docx_ok else f"{ERROR} — Geração DOCX indisponível",
        },
        "pdf": {
            "status": OK if pdf_ok else ATTENTION,
            "summary": (
                f"{OK} — conversor PDF disponível"
                if pdf_ok
                else f"{ATTENTION} — conversor PDF não detectado"
            ),
            "libreoffice": bool(libreoffice),
            "word": word,
        },
        "status": _status(OK if docx_ok else ERROR, OK if pdf_ok else ATTENTION),
    }


def inspect_application():
    version = app_version() or "não identificada"
    build = git_build()
    now = datetime.now(INSTITUTIONAL_TZ)
    try:
        import streamlit as st

        streamlit_version = st.__version__
    except Exception:
        streamlit_version = "não identificada"
    return {
        "status": OK,
        "python": platform.python_version(),
        "streamlit": streamlit_version,
        "app_version": version,
        "build": build or "não identificado",
        "environment": runtime_environment(),
        "server_time": now.strftime("%d/%m/%Y %H:%M:%S"),
        "timezone": "America/Recife",
        "summary": f"{OK} — {APP_NAME} {version}",
    }


def inspect_activity(store):
    store = unwrap_store(store)
    empty = {
        "last_access": None,
        "last_document": None,
        "last_admin": None,
        "last_activity": None,
    }
    try:
        with store.connection(read_only=True) as connection:
            tables = list_tables(connection, store.backend)
            if "auditoria_eventos" not in tables:
                return empty

            def latest(clause, args=()):
                row = connection.execute(
                    "SELECT evento,modulo,criado_em FROM auditoria_eventos "
                    + clause
                    + " ORDER BY criado_em DESC, id DESC LIMIT 1",
                    args,
                ).fetchone()
                if not row:
                    return None
                return {"evento": row[0], "modulo": row[1], "criado_em": row[2]}

            last_access = latest("WHERE evento IN ('SESSAO_INICIADA','ACESSO_AUTORIZADO')")
            last_document = latest(
                "WHERE evento IN ('PORTARIA_FINALIZADA','OFICIO_FINALIZADO','MEMORANDO_FINALIZADO')"
            )
            last_admin = latest("WHERE modulo='admin'")
            last_activity = latest("")
    except Exception:
        return empty
    return {
        "last_access": last_access,
        "last_document": last_document,
        "last_admin": last_admin,
        "last_activity": last_activity,
    }


def diagnose(store):
    store = unwrap_store(store)
    database = ping_database(store)
    if database["status"] == ERROR:
        schema = {
            "status": ERROR,
            "summary": f"{ERROR} — Schema não verificado (banco indisponível)",
            "tables_present": 0,
            "tables_expected": len(essential_tables(getattr(store, "backend", "sqlite"))),
            "markers": [],
            "markers_ok": 0,
            "expected_markers": list(SCHEMA_MARKERS),
            "missing_tables": [],
            "missing_columns": [],
            "items": [],
            "sqlite_user_version": None,
            "postgres_schema_versions": [],
        }
        audit = {
            "status": ERROR,
            "summary": f"{ERROR} — Auditoria não verificada (banco indisponível)",
            "last_event": None,
            "events_24h": 0,
            "errors_24h": 0,
            "errors_7d": 0,
        }
        activity = {
            "last_access": None,
            "last_document": None,
            "last_admin": None,
            "last_activity": None,
        }
    else:
        schema = inspect_schema(store)
        audit = inspect_audit(store)
        activity = inspect_activity(store)
    documents = inspect_documents()
    application = inspect_application()
    overall = _status(
        database["status"],
        schema["status"],
        audit["status"],
        documents["status"],
        application["status"],
    )
    return {
        "status": overall,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "database": database,
        "schema": schema,
        "audit": audit,
        "documents": documents,
        "application": application,
        "activity": activity,
    }
