"""Profile timestamp formatting in US Eastern."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

EST = ZoneInfo("America/New_York")


def format_unix_est(ts: int | None) -> str:
    """Format UNIX epoch seconds as Eastern wall-clock string."""
    if ts is None:
        return "unknown"
    dt = datetime.fromtimestamp(ts, tz=EST)
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def format_iso_est(value: str | datetime | None) -> str | None:
    """Parse ISO or datetime and format as Eastern wall-clock string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EST).strftime("%Y-%m-%d %H:%M:%S %Z")
