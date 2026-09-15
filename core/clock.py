import datetime
from zoneinfo import ZoneInfo


TEHRAN = ZoneInfo("Asia/Tehran")


def tehran_now():
    """Return a naive Tehran timestamp for SQLite DateTime columns."""
    return datetime.datetime.now(TEHRAN).replace(tzinfo=None)


def tehran_naive(value):
    """Interpret naive stored timestamps as Tehran local time."""
    if value.tzinfo is not None:
        return value.astimezone(TEHRAN).replace(tzinfo=None)
    return value
