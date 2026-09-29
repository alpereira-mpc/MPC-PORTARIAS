"""Temporary administrator-only UI for the one-off 2026 representations backfill."""

from pathlib import Path
from tempfile import TemporaryDirectory

import streamlit as st

from scripts.backfill_representacoes_2026 import SOURCES, run
from services.access import require_permission


EXPECTED = {source.arquivo for source in SOURCES}


def _files(uploaded):
    indexed = {item.name: item.getvalue() for item in uploaded or []}
    extra = set(indexed) - EXPECTED
    missing = EXPECTED - set(indexed)
    if extra or missing or len(indexed) != 8:
        details = []
        if missing:
            details.append("Ausentes: " + ", ".join(sorted(missing)))
        if extra:
            details.append("Inesperados: " + ", ".join(sorted(extra)))
        raise ValueError("São exigidos exatamente os 8 PDFs autorizados. " + " ".join(details))
    return indexed


def _with_files(files, callback):
    with TemporaryDirectory(prefix="mpc-backfill-representacoes-") as directory:
        root = Path(directory)
        for name, content in files.items():
            (root / name).write_bytes(content)
        return callback(root)


def render(store, principal):
    """Temporary tool; remove after the controlled historical import completes."""
    require_permission(principal, "admin")
    st.subheader("Importação histórica — Representações 2026")
    st.caption("Ferramenta temporária, exclusiva de administrador. Não envia comunicações nem usa IA.")
    uploaded = st.file_uploader("Os 8 PDFs protocolados", type=["pdf"], accept_multiple_files=True, key="backfill_rep_2026_files")
    try:
        files = _files(uploaded)
    except ValueError as exc:
        st.warning(str(exc))
        return
    digest = tuple(sorted((name, len(content), hash(content)) for name, content in files.items()))
    if st.button("Simular importação", key="backfill_rep_2026_simulate"):
        rows = _with_files(files, lambda root: run(store, root))
        st.session_state["backfill_rep_2026_plan"] = (digest, rows)
    current = st.session_state.get("backfill_rep_2026_plan")
    if not current or current[0] != digest:
        return
    rows = current[1]
    table = [{"Processo": row[0].numero, "Existe?": bool(row[3]), "Ação": row[4], "Situação": row[0].situacao, "Fase": row[0].fase, "Procurador(es)": ", ".join(row[0].procuradores), "Relator": row[0].relator, "PDF": row[1].name, "Alertas": "; ".join(row[5]) or "—"} for row in rows]
    st.dataframe(table, hide_index=True, use_container_width=True)
    for row in rows:
        if row[5]:
            st.error(row[0].numero + ": " + "; ".join(row[5]))
    blocked = any(row[4] in {"CONFLICT", "ERROR"} for row in rows)
    confirmation = st.text_input("Digite IMPORTAR 2026 para confirmar", key="backfill_rep_2026_confirmation")
    if st.button("Importar 8 representações", disabled=blocked or confirmation != "IMPORTAR 2026", key="backfill_rep_2026_apply"):
        # Re-plan in run() immediately before writes, preventing stale browser state.
        _with_files(files, lambda root: run(store, root, apply=True, actor=principal.email))
        st.success("Importação histórica concluída.")
