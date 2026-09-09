# Repository Guidelines

## Project Structure

MPC-PB — Gerador de Portarias PROGE is a local Python 3.12/Streamlit application. `app.py` implements the four screens. `database/` holds SQLite persistence and initial seed data; `services/` contains Portuguese wording, validation, and exports; `document_generator/` handles DOCX and PDF conversion. Institutional template packages and artwork live in `templates/` and `assets/`.

`referencias/` contains immutable original DOC/PDF pairs. `tests/` covers domain rules, persistence, and the Streamlit workflow. `docs/` records visual validation and sample documents. Runtime data belongs in `data/`, generated documents in `exports/`, and development intermediates in ignored `tmp/`.

## Development Commands

Use PowerShell from the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
.\.venv\Scripts\python.exe -m pytest -q
```

These create the environment, install dependencies, launch locally, and run tests. `Iniciar_MPC.cmd` launches an existing environment. Install `requirements-dev.txt` for Black; format with `python -m black app.py database services document_generator tests scripts`.

## Coding Style & Naming

Use four-space indentation, Black formatting, `snake_case` functions/modules, and `PascalCase` classes. Keep business rules independent of Streamlit. Use testable Portuguese templates, explicit grammatical gender, and full four-digit years. Store configurable members, reasons, and legal bases in SQLite. Seed changes must never overwrite existing user settings.

## Testing Guidelines

Use pytest and `tests/test_*.py`; isolate databases and files with `tmp_path`. Test draft/finalized/cancelled transitions, concurrent and repeated finalization, annual rollover, recovery, and a fresh process reopening the database. Cover gender, cascading substitutions, footnotes, manual overrides, and invalid inputs. No numerical coverage threshold is established.

For generator changes, render Portarias 5/2026, 6/2026, and 8/2026 and inspect every page against the references. Preserve the institutional package structure documented in `docs/PADRAO_INSTITUCIONAL.md`. Report any unavailable converter instead of claiming PDF validation passed.

## Safety & Contributions

Never modify reference originals, silently overwrite exports, or send documents to external services. Finalized acts may only be deleted through the explicit administrative routine with confirmation, backup, audit log, and transactional sequence recalculation. Ordinary cancellation retains the number. Finalization must atomically persist a unique annual number and DOCX snapshot. Preserve administrative baselines and existing data across migrations.

No Git metadata or historical commit convention is available. Use concise imperative commit subjects. PRs should describe behavior, test evidence, migration implications, and rendered comparisons for document or interface changes. Exclude runtime databases, logs, environments, and exports from commits.
