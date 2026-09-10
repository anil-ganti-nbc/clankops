"""Shared pytest fixtures."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from clankops.clock import FrozenClock
from clankops.store import open_store

FROZEN = datetime(2026, 9, 10, 4, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolate_clankops_session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI tests must not inherit the operator's live CLANKOPS_SESSION_ID."""
    monkeypatch.delenv("CLANKOPS_SESSION_ID", raising=False)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "clankops.db"


@pytest.fixture
def store(db_path: Path):
    s = open_store(db_path, actor="tester", clock=FrozenClock(FROZEN))
    yield s
    s.conn.close()
