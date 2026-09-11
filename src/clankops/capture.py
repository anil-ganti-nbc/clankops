"""Shared unfinished-Mission targeting for explicit capture writes.

Never use Store.active_or_unfinished_mission() on these paths: that helper
can attach to a completed or abandoned Mission.
"""

from __future__ import annotations

from typing import Any

from clankops.enums import MissionState
from clankops.errors import ValidationError
from clankops.store import Store

UNFINISHED_MISSION_STATES = frozenset(
    {
        MissionState.PLANNED,
        MissionState.ACTIVE,
        MissionState.PAUSED,
        MissionState.BLOCKED,
    }
)


def resolve_unfinished_capture_mission(
    store: Store,
    clank: str,
    clank_id: str,
    mission: str | None,
    *,
    attach_kind: str,
    attach_kind_plural: str,
) -> dict[str, Any]:
    """Attach only to an unfinished Mission on the requested Clank."""
    if mission:
        row = store.resolve_mission(mission)
        if row["clank_id"] != clank_id:
            owner = store.clank_detail(row["clank_id"])
            raise ValidationError(
                f"mission {row['display_id']} belongs to "
                f"{owner.get('slug') or row['clank_id']}, not {clank}"
            )
        if row["state"] not in UNFINISHED_MISSION_STATES:
            raise ValidationError(
                f"mission {row['display_id']} is {row['state']}; "
                f"{attach_kind_plural} attach only to unfinished Missions "
                "(PLANNED / ACTIVE / PAUSED / BLOCKED)"
            )
        return row
    unfinished = store.unfinished_missions(clank)
    if not unfinished:
        raise ValidationError(f"no unfinished Mission for {clank} to attach {attach_kind}")
    if len(unfinished) > 1:
        listed = ", ".join(f"{m['display_id']} [{m['state']}]" for m in unfinished)
        raise ValidationError(
            f"multiple unfinished Missions for {clank}: {listed}. "
            "Pass --mission COPS-xxxxxx"
        )
    return unfinished[0]
