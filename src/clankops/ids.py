"""Durable identifiers.

ClankOps uses UUIDv7 as the primary key for every durable entity
(Clank, Mission, Session, Event, Feature, Task, Decision, Blocker,
Artifact, Relationship). UUIDv7 is time-ordered, globally unique, and
stable across renames, path moves, and GitHub repository renames.

stdlib ``uuid.uuid7()`` requires Python 3.14+. This project does not
silently fall back to UUID4.

Human-facing Mission identifiers (COPS-000123) are additional display
labels stored beside the UUID. Application logic must never depend on
the display identifier alone.
"""

from __future__ import annotations

import re
import sys
import uuid

MISSION_DISPLAY_PREFIX = "COPS"
MIN_PYTHON = (3, 14)
_DISPLAY_RE = re.compile(rf"{re.escape(MISSION_DISPLAY_PREFIX)}-(\d+)$")


def assert_python_contract() -> None:
    if sys.version_info < MIN_PYTHON:
        raise RuntimeError(
            "ClankOps requires Python "
            f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ for stdlib uuid.uuid7(); "
            f"found {sys.version_info.major}.{sys.version_info.minor}"
        )
    if not hasattr(uuid, "uuid7"):
        raise RuntimeError("stdlib uuid.uuid7() is required; UUID4 fallback is forbidden")


assert_python_contract()


def new_id() -> str:
    """Return a lowercase UUIDv7 string."""
    value = uuid.uuid7()
    if value.version != 7:
        raise RuntimeError("identity generator must produce UUIDv7")
    return str(value)


def format_mission_display_id(n: int) -> str:
    if n < 1:
        raise ValueError("mission display sequence must be >= 1")
    return f"{MISSION_DISPLAY_PREFIX}-{n:06d}"


def parse_mission_display_n(display_id: str | None) -> int | None:
    if not display_id:
        return None
    match = _DISPLAY_RE.fullmatch(str(display_id).strip())
    if not match:
        return None
    return int(match.group(1))


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False
