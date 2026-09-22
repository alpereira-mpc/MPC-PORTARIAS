from datetime import date, datetime

import pandas as pd

from services.date_format import format_date_br, format_datetime_br


def test_format_date_br_accepts_iso_native_and_pandas_values():
    assert format_date_br("2026-09-22") == "22/09/2026"
    assert format_date_br("2026-01-05") == "05/01/2026"
    assert format_date_br(date(2026, 9, 22)) == "22/09/2026"
    assert format_date_br(pd.Timestamp("2026-01-05")) == "05/01/2026"


def test_format_datetime_br_uses_minutes_by_default_and_optional_seconds():
    value = datetime(2026, 1, 5, 8, 30, 45)
    assert format_datetime_br(value) == "05/01/2026 08:30"
    assert format_datetime_br("2026-01-05T08:30:45") == "05/01/2026 08:30"
    assert format_datetime_br(value, seconds=True) == "05/01/2026 08:30:45"


def test_format_date_helpers_handle_missing_and_invalid_values_safely():
    for value in (None, "", pd.NaT, float("nan")):
        assert format_date_br(value) == "—"
        assert format_datetime_br(value) == "—"
    assert format_date_br(None, empty="") == ""
    assert format_date_br("texto livre") == "texto livre"


def test_ui_formatting_does_not_change_order_or_iso_persistence_values():
    values = [date(2026, 9, 22), date(2025, 12, 31), date(2026, 1, 1)]
    assert sorted(values) == [date(2025, 12, 31), date(2026, 1, 1), date(2026, 9, 22)]
    assert [value.isoformat() for value in sorted(values)] == [
        "2025-12-31",
        "2026-01-01",
        "2026-09-22",
    ]


def test_brazilian_date_input_keeps_native_date_value():
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_string(
        "import streamlit as st\n"
        "from datetime import date\n"
        "st.date_input('Data', date(2026, 9, 22), format='DD/MM/YYYY')\n"
    ).run()

    assert not app.exception
    assert app.date_input[0].value == date(2026, 9, 22)
