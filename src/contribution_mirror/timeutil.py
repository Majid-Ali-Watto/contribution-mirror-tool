"""Timestamps from different vendors, normalised to the one form git prints for UTC."""
import re
from datetime import datetime, timezone

_FRACTION = re.compile(r"\.(\d+)")
_OFFSET_NO_COLON = re.compile(r"([+-]\d{2})(\d{2})$")


def parse_iso(value):
    """Any ISO-8601 timestamp as an aware datetime. Handles what the vendors really send: a trailing Z or an
    offset (with or without colon), and fractions of any length (GitLab: milliseconds, Azure DevOps: 7 digits).
    Python 3.9/3.10 only accept 3 or 6 fraction digits, so the fraction is normalised to 6."""
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    elif "T" in s:                                  # +0530 -> +05:30 (only after a time, never in a bare date)
        s = _OFFSET_NO_COLON.sub(r"\1:\2", s)
    s = _FRACTION.sub(lambda m: "." + (m.group(1) + "000000")[:6], s, count=1)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_utc_z(value):
    """'YYYY-MM-DDTHH:MM:SSZ' in UTC: exactly how git's %aI prints a UTC date, so re-runs dedupe against the mirror."""
    return parse_iso(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
