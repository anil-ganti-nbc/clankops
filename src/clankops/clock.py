"""Clock injection so tests do not depend on wall time."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


class TickingClock:
    """Advances by a fixed delta on every `now()` call. For duration tests."""

    def __init__(self, instant: datetime, step: timedelta | None = None) -> None:
        if instant.tzinfo is None:
            raise ValueError("clock instants must be timezone-aware UTC")
        self._instant = instant.astimezone(timezone.utc)
        self._step = step or timedelta(seconds=1)

    def now(self) -> datetime:
        current = self._instant
        self._instant = self._instant + self._step
        return current


def isoformat_utc(dt: datetime) -> str:
    if dt.tzinfo is None:
        raise ValueError("naive datetime refused; storage requires UTC")
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
