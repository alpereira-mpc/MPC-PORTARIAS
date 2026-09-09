import pytest
from database.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "test.db")
    s.configure(export_dir=str(tmp_path / "exports"))
    s.set_sequence(2026, 8, True)
    return s
