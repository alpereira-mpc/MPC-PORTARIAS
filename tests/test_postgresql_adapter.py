from database.postgresql import Connection, Cursor, IDENTITY_TABLES


class _RawCursor:
    def __init__(self, row, rowcount=-1):
        self.row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self.row


def test_cursor_preserves_inserted_identity_value():
    assert Cursor(_RawCursor((123,)), returning_id=True).lastrowid == 123


def test_cursor_accepts_insert_select_with_no_returned_rows():
    assert Cursor(_RawCursor(None), returning_id=True).lastrowid is None


class _RawConnection:
    def __init__(self):
        self.statement = None

    def execute(self, statement, values):
        self.statement = statement
        return _RawCursor((41,), rowcount=1)


def test_peticoes_insert_returns_its_postgresql_identity():
    raw = _RawConnection()
    cursor = Connection(raw).execute(
        "INSERT INTO peticoes(numero_tramita) VALUES(?)", ("116439/26",)
    )
    assert "RETURNING id" in raw.statement
    assert cursor.lastrowid == 41


def test_estagiarios_insert_uses_the_shared_postgresql_identity_contract():
    """Repositories receive lastrowid; the adapter owns INSERT ... RETURNING."""
    assert "estagiarios_lotacoes" in IDENTITY_TABLES
    raw = _RawConnection()
    cursor = Connection(raw).execute(
        "INSERT INTO estagiarios_lotacoes(pessoa_id) VALUES(?)", (17,)
    )
    assert "RETURNING id" in raw.statement
    assert cursor.lastrowid == 41
