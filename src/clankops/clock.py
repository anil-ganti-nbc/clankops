"""Clock injection so tests do not depend on wall time."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("clock instants must be timezone-aware UTC")
        self._instant = instant.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._instant


def isoformat_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime refused; storage requires UTC")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
