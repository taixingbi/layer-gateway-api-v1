import re

from app.core.time_util import eastern_from_timestamp, eastern_now_iso


def test_eastern_now_iso_is_iso8601_with_offset():
    s = eastern_now_iso()
    assert "T" in s
    assert re.search(r"[+-]\d{2}:\d{2}$", s), s


def test_eastern_from_timestamp_matches_known_utc_instant():
    # 2020-01-15 12:00:00 UTC → 07:00 EST (same calendar date in standard time)
    s = eastern_from_timestamp(1579089600.0)
    assert s.startswith("2020-01-15T07:00:00")
    assert s.endswith("-05:00")
