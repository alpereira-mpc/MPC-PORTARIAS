import pytest
from database.store import Store


@pytest.fixture(autouse=True)
def isolate_database_secrets(monkeypatch):
    """Tests never inherit a developer's or Cloud's production connection."""
    import streamlit as st

    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(st, "secrets", {})


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.configure(export_dir=str(tmp_path / "exports"))
    s.set_sequence(2026, 8, True)
    return s
