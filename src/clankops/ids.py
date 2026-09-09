"""Durable identifiers.

ClankOps uses UUIDv7 as the primary key for every durable entity
(Clank, Mission, Session, Event, Feature, Task, Decision, Blocker,
Artifact, Relationship). UUIDv7 is time-ordered, globally unique, and
stable across renames, path moves, and GitHub repository renames.

Human-facing Mission identifiers (COPS-000123) are additional display
labels stored beside the UUID. Application logic must never depend on
the display identifier alone.
"""

from __future__ import annotations

import uuid

MISSION_DISPLAY_PREFIX = "COPS"


def new_id() -> str:
    """Return a lowercase UUIDv7 string."""
    return str(uuid.uuid7())


def format_mission_display_id(n: int) -> str:
    if n < 1:
        raise ValueError("mission display sequence must be >= 1")
    return f"{MISSION_DISPLAY_PREFIX}-{n:06d}"


def is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False
