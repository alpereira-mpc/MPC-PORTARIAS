"""Local SQLite performance profiler. Never connects to Supabase or production PG.

Usage (from repository root):

    .\\.venv\\Scripts\\python.exe scripts\\profile_performance.py --phase baseline
    .\\.venv\\Scripts\\python.exe scripts\\profile_performance.py --phase after

Writes JSON under tmp/ (gitignored). No institutional audit events for each query.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.pop("DATABASE_URL", None)
os.environ.pop("MPC_TEST_POSTGRES_URL", None)

from tests.access_testing import TEST_IDENTITY, seed_access


SKIP_SQL_PREFIXES = (
    "begin",
    "commit",
    "rollback",
    "pragma",
)


class QueryProbe:
    def __init__(self):
        self.reset()
        self._original_connection = None

    def reset(self):
        self.calls = 0
        self.sql = []
        self.elapsed_ms = 0.0
        self._connects = 0

    def install(self):
        from database import store as persistence

        probe = self
        original = persistence.Store.connection
        probe._original_connection = original

        def connection(self, *, read_only=False, isolation=None, statement_timeout=None):
            from contextlib import contextmanager

            @contextmanager
            def wrapped():
                probe._connects += 1
                with original(
                    self,
                    read_only=read_only,
                    isolation=isolation,
                    statement_timeout=statement_timeout,
                ) as inner:

                    class Counting:
                        def execute(this, sql, parameters=()):
                            text = sql if isinstance(sql, str) else str(sql)
                            start = time.perf_counter()
                            try:
                                return inner.execute(sql, parameters)
                            finally:
                                probe.calls += 1
                                probe.elapsed_ms += (time.perf_counter() - start) * 1000
                                probe.sql.append(" ".join(text.split()))

                        def executemany(this, sql, seq):
                            text = sql if isinstance(sql, str) else str(sql)
                            start = time.perf_counter()
                            try:
                                return inner.executemany(sql, seq)
                            finally:
                                probe.calls += 1
                                probe.elapsed_ms += (time.perf_counter() - start) * 1000
                                probe.sql.append(" ".join(text.split()) + " [executemany]")

                        def __getattr__(this, name):
                            return getattr(inner, name)

                    yield Counting()

            return wrapped()

        persistence.Store.connection = connection
        return self

    def restore(self):
        if self._original_connection is not None:
            from database import store as persistence

            persistence.Store.connection = self._original_connection

    def snapshot(self):
        meaningful = [
            s
            for s in self.sql
            if not s.lower().startswith(SKIP_SQL_PREFIXES)
        ]
        counts = Counter(meaningful)
        repeated = {q: n for q, n in counts.items() if n > 1}
        return {
            "execute_calls": self.calls,
            "meaningful_queries": len(meaningful),
            "unique_queries": len(counts),
            "repeated_query_shapes": len(repeated),
            "top_repeated": sorted(repeated.items(), key=lambda x: -x[1])[:8],
            "sqlite_execute_ms": round(self.elapsed_ms, 2),
            "sqlite_connects": self._connects,
        }


def _seed_volume(store, today: date):
    from database.agenda import AgendaStore
    from database.memorandos import MemorandosStore
    from database.oficios import OficiosStore

    oficios = OficiosStore(store)
    AgendaStore(store)
    MemorandosStore(store)
    member = next(s["membro_id"] for s in oficios.series() if s["sigla"] == "PROGE")
    stamp = datetime(2026, 9, 1, 12, 0, 0).isoformat()
    payload = "{}"
    rows = []
    for i in range(250):
        due = today + timedelta(days=(i % 40) - 10)
        rows.append(
            (
                uuid.uuid4().hex,
                "RECEBIDO",
                None,
                today.year,
                None,
                "Recebido",
                today.replace(day=1).isoformat(),
                due.isoformat(),
                member,
                f"Assunto {i}",
                "Destinatário",
                f"EXT-{i}",
                None,
                payload,
                stamp,
                stamp,
                None,
                None,
            )
        )
    with store.connection() as connection:
        connection.executemany(
            "INSERT INTO oficios(id,direcao,serie,ano,numero,status,data,prazo,"
            "membro_id,assunto,destinatario,numero_externo,responde_a,payload,"
            "criada,atualizada,data_envio,cancelada) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        connection.executemany(
            "INSERT INTO oficio_destinatarios(oficio_id,membro_id) VALUES(?,?)",
            [(row[0], member) for row in rows],
        )
        agenda_rows = []
        links = []
        for i in range(180):
            start = datetime.combine(today + timedelta(days=i), datetime.min.time()) + timedelta(
                hours=10
            )
            identifier = uuid.uuid4().hex
            agenda_rows.append(
                (
                    identifier,
                    "REUNIAO",
                    start.isoformat(),
                    (start + timedelta(hours=1)).isoformat(),
                    "Agendado",
                    json.dumps(
                        {
                            "titulo": f"Reunião {i}",
                            "reuniao_com": "Conselheiro",
                            "local": "Gabinete",
                            "sem_hora": False,
                        },
                        ensure_ascii=False,
                    ),
                    stamp,
                    stamp,
                )
            )
            links.append((identifier, member))
        connection.executemany(
            "INSERT INTO agenda_compromissos(id,tipo,inicio,fim,situacao,payload,criada,atualizada) "
            "VALUES(?,?,?,?,?,?,?,?)",
            agenda_rows,
        )
        connection.executemany(
            "INSERT INTO agenda_compromisso_procuradores(compromisso_id,procurador_id) VALUES(?,?)",
            links,
        )
        memo_rows = []
        subst = []
        for i in range(80):
            identifier = uuid.uuid4().hex
            start = today + timedelta(days=(i % 20) - 5)
            end = start + timedelta(days=5)
            memo_rows.append(
                (
                    identifier,
                    "SUBSTITUICAO",
                    "FINALIZADO",
                    "admin@test.local",
                    stamp,
                    stamp,
                    stamp,
                    f"M-{i}",
                    json.dumps(
                        {
                            "etapas": [
                                {
                                    "substituido": {"nome": "Servidor A"},
                                    "substituto": {"nome": "Servidor B"},
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                )
            )
            subst.append(
                (
                    identifier,
                    start.isoformat(),
                    end.isoformat(),
                    member,
                    "PROGE",
                    "Cargo comissionado",
                    "Férias regulamentares",
                    "",
                    member,
                    "Signatário",
                    "Procuradora-Geral",
                )
            )
        connection.executemany(
            "INSERT INTO memorandos(id,tipo,status,criado_por,criado_em,atualizado_em,"
            "finalizado_em,numero_oficial,payload) VALUES(?,?,?,?,?,?,?,?,?)",
            memo_rows,
        )
        connection.executemany(
            "INSERT INTO memorandos_substituicao(memorando_id,data_inicio,data_fim,"
            "gabinete_procurador_id,gabinete_snapshot,natureza_funcao,motivo,motivo_texto,"
            "signatario_id,signatario_nome,signatario_cargo) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            subst,
        )


def _seed_v2(store, today):
    """Cover the modules added since V1, using only the temporary SQLite DB."""
    from database.tramita_reports import TramitaReportsStore
    from database.tarefas import TarefasStore

    reports = TramitaReportsStore(store)
    rows = [dict(protocolo=f"{i:05}/26", tipo="Processo", subcategoria="Denúncia",
                 origem="Origem sintética", data_realizacao="2026-09-01 09:00",
                 procurador=f"Procurador {i % 7}", motivo_distribuicao="",
                 data_devolucao="2026-09-03 09:00", motivo_devolucao="Analisado Com Parecer")
            for i in range(2000)]
    reports.import_rows(kind="ENTRADAS", file_name="sintetico-entrada.xls", file_hash="v2-entry", actor="profiler", competence="2026-09", rows=rows)
    reports.import_rows(kind="SAIDAS", file_name="sintetico-saida.xls", file_hash="v2-exit", actor="profiler", competence="2026-09", rows=rows)
    stock = [dict(protocolo=row["protocolo"], tipo="Processo", digital="Sim",
                  subcategoria="Denúncia", jurisdicionado="Origem sintética", fase="Análise",
                  procurador=row["procurador"], dias_com_procurador=i % 100, assistente="",
                  dias_com_assistente=None, dias_no_mpc=100, prescricao="") for i, row in enumerate(rows)]
    reports.import_rows(kind="ESTOQUE", file_name="sintetico-estoque.xls", file_hash="v2-stock", actor="profiler", snapshot_date=today.isoformat(), rows=stock)
    tasks = TarefasStore(store)
    for i in range(40):
        tasks.create(1, {"titulo": f"Tarefa {i}", "prazo_data": today.isoformat()})
    with store.connection() as c:
        # Keep the next-day fixtures used by the original bell measurement.
        c.execute("UPDATE agenda_compromissos SET situacao='Realizado' WHERE inicio>=?", ("2026-12-01",))


def _measure(probe, name, fn, repeats=3):
    samples = []
    last = None
    for _ in range(repeats):
        probe.reset()
        start = time.perf_counter()
        last = fn()
        total = (time.perf_counter() - start) * 1000
        snap = probe.snapshot()
        snap["total_ms"] = round(total, 2)
        samples.append(snap)
    best = min(samples, key=lambda s: s["total_ms"])
    best["repeats"] = repeats
    best["median_ms"] = round(
        sorted(s["total_ms"] for s in samples)[len(samples) // 2], 2
    )
    return best, last


def _apptest_nav(store, identity, destination, extra_state=None):
    from streamlit.testing.v1 import AppTest
    from database.store import ROOT as APP_ROOT

    with patch("services.access.oidc_identity", lambda: identity):
        with patch("database.store.Store", lambda *a, **k: store):
            app = AppTest.from_file(str(APP_ROOT / "app.py"), default_timeout=60).run()
            if destination != "Início":
                if extra_state:
                    for key, value in extra_state.items():
                        app.session_state[key] = value
                app.sidebar.radio(key="portal_module").set_value(destination).run()
            return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="run", help="baseline|after|run")
    args = parser.parse_args()
    probe = QueryProbe().install()
    report = {"phase": args.phase, "backend": "sqlite", "flows": {}}
    try:
        with TemporaryDirectory(dir=str(ROOT / "tmp"), ignore_cleanup_errors=True) as tmp:
            db_path = Path(tmp) / "profile.db"
            os.environ["MPC_DB_PATH"] = str(db_path)
            from database.access import _READY as ACCESS_READY
            from database.agenda import _READY as AGENDA_READY
            from database.audit import _READY as AUDIT_READY
            from database.memorandos import _READY as MEMO_READY
            from database.store import Store, _INITIALIZED

            _INITIALIZED.clear()
            ACCESS_READY.clear()
            AGENDA_READY.clear()
            MEMO_READY.clear()
            AUDIT_READY.clear()

            t0 = time.perf_counter()
            store = Store(db_path)
            seed_access(store)
            report["startup_store_ms"] = round((time.perf_counter() - t0) * 1000, 2)

            today = date(2026, 9, 14)
            t1 = time.perf_counter()
            _seed_volume(store, today)
            _seed_v2(store, today)
            report["seed_ms"] = round((time.perf_counter() - t1) * 1000, 2)

            from services.access import resolve_principal
            from services.alerts import collect_alerts, get_alert_summary
            from services.pending import collect_pending, pending_counts

            principal = resolve_principal(store, TEST_IDENTITY)
            now = datetime(2026, 9, 14, 9, 0, 0)

            def home_services():
                return get_alert_summary(store, principal, now=now)

            report["flows"]["H_sininho_frio"], summary = _measure(
                probe, "bell", home_services
            )
            report["bell_total_alerts"] = summary["total"] if summary else None

            def pending_all():
                return collect_pending(store, principal, today=today)

            report["flows"]["G_pendencias"], pending = _measure(
                probe, "pending", pending_all
            )
            report["pending_items"] = len(pending[0]) if pending else None

            def alerts_page():
                return collect_alerts(store, principal, now=now)

            report["flows"]["alertas_pagina"], _ = _measure(probe, "alerts", alerts_page)
            report["flows"]["pending_counts"], _ = _measure(
                probe, "counts", lambda: pending_counts(store, principal, today=today)
            )

            from database.oficios import OficiosStore
            from database.agenda import AgendaStore
            from database.memorandos import MemorandosStore

            oficios = OficiosStore(store)
            agenda = AgendaStore(store)
            memorandos = MemorandosStore(store)
            from database.tramita_reports import TramitaReportsStore
            from database.tarefas import TarefasStore

            reports = TramitaReportsStore(store)
            tasks = TarefasStore(store)
            from services.ouvidoria import list_records as list_ouvidoria
            from services.ouvidoria import overview as overview_ouvidoria
            from services.representacoes import (
                list_records as list_representacoes,
                overview as overview_representacoes,
                people_context,
            )
            from services.search import global_search

            def representacoes_list_reads():
                context = people_context(store)
                return overview_representacoes(store), list_representacoes(store), context

            def ouvidoria_list_reads():
                return overview_ouvidoria(store), list_ouvidoria(store)

            extra_flows = {
                "agenda_historico31": lambda: agenda.history(),
                "tarefas_ativas": lambda: tasks.list_active(1),
                "tarefas_historico": lambda: tasks.list_history(1),
                "relatorios_schema_quente": lambda: TramitaReportsStore(store),
                "relatorios_producao": lambda: reports.production_summary("2026-09"),
                "relatorios_pagina": lambda: reports.movement_page("2026-09", {}),
                "relatorios_estoque": lambda: reports.stock_summary(today.isoformat()),
                "relatorios_estoque_pagina": lambda: reports.stock_details(today.isoformat(), {}),
                "representacoes_lista": representacoes_list_reads,
                "ouvidoria_lista": ouvidoria_list_reads,
                "busca_global": lambda: global_search(store, principal, "Assunto"),
            }
            for name, operation in extra_flows.items():
                report["flows"][name], _ = _measure(probe, name, operation)

            report["flows"]["oficios_overview"], _ = _measure(
                probe, "oficios", lambda: oficios.overview(2026, member=1)
            )
            report["flows"]["oficios_list50"], _ = _measure(
                probe,
                "olist",
                lambda: oficios.list(direction="RECEBIDO", limit=50, offset=0),
            )
            report["flows"]["agenda_hoje"], _ = _measure(
                probe,
                "agenda",
                lambda: agenda.list(today.isoformat(), (today + timedelta(days=1)).isoformat()),
            )
            report["flows"]["agenda_mes"], _ = _measure(
                probe,
                "mes",
                lambda: agenda.list(today.replace(day=1).isoformat(), "2026-10-01"),
            )
            report["flows"]["memorandos_list200"], _ = _measure(
                probe, "memo", lambda: memorandos.list(limit=200, offset=0)
            )
            if hasattr(memorandos, "situacao_counts"):
                report["flows"]["memorandos_counts"], _ = _measure(
                    probe, "mc", lambda: memorandos.situacao_counts(today)
                )

            from services.audit import overview as audit_overview
            from services.system_health import diagnose as evaluate_health

            report["flows"]["I_auditoria_overview"], _ = _measure(
                probe, "audit", lambda: audit_overview(store, principal)
            )
            report["flows"]["J_saude"], _ = _measure(
                probe, "health", lambda: evaluate_health(store)
            )

            identity = dict(TEST_IDENTITY)
            try:
                from streamlit.testing.v1 import AppTest
                from database.store import ROOT as APP_ROOT

                def run_home():
                    with patch("services.access.oidc_identity", lambda: identity):
                        with patch("database.store.Store", lambda *a, **k: store):
                            return AppTest.from_file(
                                str(APP_ROOT / "app.py"), default_timeout=60
                            ).run()

                report["flows"]["A_home_apos_auth"], app = _measure(
                    probe, "home", run_home, repeats=1
                )
                if app is not None:

                    def rerun_home():
                        with patch("services.access.oidc_identity", lambda: identity):
                            with patch("database.store.Store", lambda *a, **k: store):
                                return app.run()

                    report["flows"]["B_home_rerun"], _ = _measure(
                        probe, "rerun", rerun_home, repeats=1
                    )

                    def go(name, extra=None):
                        def inner():
                            if extra:
                                for key, value in extra.items():
                                    app.session_state[key] = value
                            try:
                                radio = app.sidebar.radio(key="portal_module")
                            except Exception:
                                radios = list(app.sidebar.radio)
                                radio = radios[0] if radios else None
                            if radio is None:
                                raise RuntimeError("radio portal_module ausente")
                            with patch("services.access.oidc_identity", lambda: identity):
                                with patch("database.store.Store", lambda *a, **k: store):
                                    radio.set_value(name).run()
                            return app

                        return inner

                    report["flows"]["C_home_agenda"], _ = _measure(
                        probe, "nav", go("Agenda"), repeats=1
                    )
                    rerun = go("Início")
                    rerun()
                    report["flows"]["D_home_oficios"], _ = _measure(
                        probe, "nav", go("Ofícios"), repeats=1
                    )
                    rerun()
                    report["flows"]["E_home_memorandos"], _ = _measure(
                        probe, "nav", go("Memorandos"), repeats=1
                    )
                    rerun()
                    report["flows"]["F_home_portarias"], _ = _measure(
                        probe, "nav", go("Portarias"), repeats=1
                    )
                    rerun()
                    report["flows"]["G_home_pendencias"], _ = _measure(
                        probe, "nav", go("Pendências"), repeats=1
                    )
                    rerun()
                    report["flows"]["H_home_representacoes"], _ = _measure(
                        probe, "nav", go("Representações"), repeats=1
                    )
                    rerun()
                    report["flows"]["H_home_ouvidoria"], _ = _measure(
                        probe, "nav", go("Ouvidoria"), repeats=1
                    )
                    rerun()
                    report["flows"]["I_home_admin"], _ = _measure(
                        probe,
                        "nav",
                        go("Administração", {"admin_secao": "Acessos e Auditoria"}),
                        repeats=1,
                    )
            except Exception as exc:
                report["apptest_error"] = repr(exc)
    finally:
        probe.restore()

    out = ROOT / "tmp" / f"profile_performance_{args.phase}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: report["flows"].get(k) for k in sorted(report["flows"])}, indent=2))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
