"""Date presentation helpers; persistence and comparisons remain ISO/typed."""

from datetime import date, datetime


def _missing(value):
    if value is None or value == "":
        return True
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def _temporal(value):
    if _missing(value):
        return None
    if isinstance(value, (datetime, date)):
        return value
    converter = getattr(value, "to_pydatetime", None)
    if callable(converter):
        return converter()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                return date.fromisoformat(text)
            except ValueError:
                return value
    return value


def format_date_br(value, *, empty="—"):
    """Format one UI date without changing its stored or computational value."""
    parsed = _temporal(value)
    if parsed is None:
        return empty
    if isinstance(parsed, (datetime, date)):
        return parsed.strftime("%d/%m/%Y")
    return str(parsed)


def format_datetime_br(value, *, empty="—", seconds=False):
    """Format one UI timestamp, preserving its current timezone semantics."""
    parsed = _temporal(value)
    if parsed is None:
        return empty
    if isinstance(parsed, datetime):
        pattern = "%d/%m/%Y %H:%M:%S" if seconds else "%d/%m/%Y %H:%M"
        return parsed.strftime(pattern)
    if isinstance(parsed, date):
        return parsed.strftime("%d/%m/%Y")
    return str(parsed)
