from database.postgresql import Cursor


class _RawCursor:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


def test_cursor_preserves_inserted_identity_value():
    assert Cursor(_RawCursor((123,)), returning_id=True).lastrowid == 123


def test_cursor_accepts_insert_select_with_no_returned_rows():
    assert Cursor(_RawCursor(None), returning_id=True).lastrowid is None
