from __future__ import annotations

from datetime import UTC, datetime


def as_utc(dt: datetime) -> datetime:
    """Normalize DB datetimes to explicit UTC for browser-safe serialization."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def browser_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return as_utc(dt).isoformat().replace("+00:00", "Z")
