"""Agent admission around a derived resume packet.

Actor names are provenance metadata, not permission authorities.
Prepare writes zero ledger events. Admit never creates a Mission.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from clankops.errors import ValidationError
from clankops.resume import resume_packet
from clankops.store import Store


def require_actor(actor: str | None) -> str:
    name = (actor or "").strip()
    if not name:
        raise ValidationError("actor is required (metadata, not a permission)")
    return name


def prepare_agent(
    store: Store,
    clank: str,
    *,
    actor: str | None,
    now: datetime | None = None,
    include_github: bool = True,
    inspect_local=None,
    inspect_remote=None,
) -> dict[str, Any]:
    """Same canonical packet plus requested actor. Writes zero events."""
    requested = require_actor(actor)
    packet = resume_packet(
        store,
        clank,
        now=now,
        include_github=include_github,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
    )
    packet["requested_actor"] = requested
    packet["wrote_events"] = False
    return packet


def evaluate_admission(
    store: Store,
    clank: str,
    mission: str,
    *,
    actor: str | None,
    expect_context: str | None = None,
    now: datetime | None = None,
    include_github: bool = True,
    inspect_local=None,
    inspect_remote=None,
) -> dict[str, Any]:
    """Zero-write ownership and stale-context check. Does not open a Session."""
    requested = require_actor(actor)
    detail = store.clank_detail(clank)
    mission_row = store.resolve_mission(mission)
    if mission_row["clank_id"] != detail["clank_id"]:
        owner = store.clank_detail(mission_row["clank_id"])
        raise ValidationError(
            f"mission {mission_row['display_id']} belongs to "
            f"{owner.get('slug') or mission_row['clank_id']}, not {detail.get('slug')}"
        )
    packet = resume_packet(
        store,
        clank,
        now=now,
        include_github=include_github,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
    )
    current = packet["context_fingerprint"]
    expected = (expect_context or "").strip() or None
    if expected and expected != current:
        raise ValidationError(
            f"stale context: expected {expected} got {current}. "
            "Re-run agent prepare; refusing to admit against stale context."
        )
    return {
        "requested_actor": requested,
        "packet": packet,
        "mission_row": mission_row,
        "context_fingerprint": current,
        "expect_context": expected,
    }


def admit_agent(
    store: Store,
    clank: str,
    mission: str,
    *,
    actor: str | None,
    expect_context: str | None = None,
    source: str | None = None,
    launcher: str | None = None,
    now: datetime | None = None,
    include_github: bool = True,
    inspect_local=None,
    inspect_remote=None,
) -> dict[str, Any]:
    """Open/resume a Session for an explicit unfinished Mission.

    Computes the packet before any mutation so --expect-context can fail
    loudly against stale facts. Does not create a Mission. May reuse an
    already-open Session for this actor (Foundation 8).
    """
    evaluated = evaluate_admission(
        store,
        clank,
        mission,
        actor=actor,
        expect_context=expect_context,
        now=now,
        include_github=include_github,
        inspect_local=inspect_local,
        inspect_remote=inspect_remote,
    )
    opened = store.open_work_session(
        evaluated["mission_row"]["mission_id"],
        actor=evaluated["requested_actor"],
        source=source,
        launcher=launcher,
        context_fingerprint=evaluated["context_fingerprint"],
    )
    return {
        "admitted": True,
        "requested_actor": evaluated["requested_actor"],
        "launcher": (launcher or "").strip() or None,
        "context_fingerprint": evaluated["context_fingerprint"],
        "expect_context": evaluated["expect_context"],
        "mission": opened["mission"]["display_id"],
        "mission_id": opened["mission"]["mission_id"],
        "mission_state": opened["mission"]["state"],
        "session_id": opened["session"]["session_id"],
        "session": opened["session"],
        "mission_row": opened["mission"],
        "packet": evaluated["packet"],
    }
