"""Duration and UTC helpers for read-only observability."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from clankops.errors import ValidationError

_DURATION = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h|d)$", re.IGNORECASE)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_duration(text: str) -> timedelta:
    raw = (text or "").strip().lower()
    match = _DURATION.match(raw)
    if not match:
        raise ValidationError("duration must look like 24h, 30m, 7d, or 90s")
    amount = float(match.group(1))
    unit = match.group(2)
    if unit == "s":
        return timedelta(seconds=amount)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def format_age(delta: timedelta | None) -> str | None:
    if delta is None:
        return None
    seconds = int(delta.total_seconds())
    if seconds < 0:
        seconds = 0
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{minutes}m"
    return f"{minutes}m"


def short_head(head: str | None) -> str | None:
    if not head:
        return None
    return head[:7]
