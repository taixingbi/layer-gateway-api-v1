"""Wall-clock timestamps for logs: US Eastern via IANA ``America/New_York`` (EST / EDT)."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

US_EASTERN = ZoneInfo("America/New_York")


def eastern_now_iso(timespec: str = "seconds") -> str:
    """Current instant as US Eastern ISO-8601 with offset (e.g. ``...-05:00`` / ``...-04:00``)."""
    return datetime.now(US_EASTERN).isoformat(timespec=timespec)


def eastern_from_timestamp(created: float, timespec: str = "seconds") -> str:
    """Convert a UNIX timestamp (e.g. ``LogRecord.created``) to US Eastern ISO-8601."""
    return datetime.fromtimestamp(created, tz=timezone.utc).astimezone(US_EASTERN).isoformat(timespec=timespec)
