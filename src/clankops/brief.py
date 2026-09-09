"""Human-readable brief formatting. Never fabricates a next action."""

from __future__ import annotations

import json
from typing import Any


def format_brief(brief: dict[str, Any]) -> str:
    ident = brief["identity"]
    mission = brief.get("mission")
    checkpoint = brief.get("checkpoint")
    lines = [
        f"Clank: {ident['display_name']} ({ident['slug']})",
        f"ID: {ident['clank_id']}",
        f"Lifecycle: {brief.get('lifecycle_state')}",
        f"Classification: {ident.get('classification') or 'unknown'}",
        f"Path: {ident.get('local_path') or 'unknown'}",
        "",
    ]
    if mission:
        lines.append(
            f"Mission: {mission['display_id']} [{mission['state']}] {mission['objective']}"
        )
    else:
        lines.append("Mission: none recorded")
    lines.append("")
    if checkpoint:
        lines.append(f"Latest checkpoint: {checkpoint['recorded_utc']}")
        if checkpoint.get("completed"):
            lines.append(f"  Completed: {checkpoint['completed']}")
        if checkpoint.get("current_work"):
            lines.append(f"  Current: {checkpoint['current_work']}")
        if checkpoint.get("next_action"):
            lines.append(f"  Next action (recorded): {checkpoint['next_action']}")
        else:
            lines.append("  Next action (recorded): none")
        if checkpoint.get("tests"):
            lines.append(f"  Tests: {checkpoint['tests']}")
        if checkpoint.get("branch"):
            lines.append(f"  Branch: {checkpoint['branch']}")
        if checkpoint.get("head"):
            lines.append(f"  HEAD: {checkpoint['head']}")
        if checkpoint.get("working_tree"):
            lines.append(f"  Working tree: {checkpoint['working_tree']}")
        if checkpoint.get("notes"):
            lines.append(f"  Notes: {checkpoint['notes']}")
    else:
        lines.append("Latest checkpoint: none recorded")
        lines.append("  Next action (recorded): none")
    lines.append("")
    lines.append(f"Branch: {brief.get('branch') or 'unknown'}")
    lines.append(f"HEAD: {brief.get('head') or 'unknown'}")
    lines.append(f"Tests: {brief.get('tests') or 'unknown'}")
    lines.append("")
    tasks = brief.get("outstanding_tasks") or []
    if tasks:
        lines.append("Outstanding tasks:")
        for task in tasks:
            lines.append(f"  - [{task['state']}] {task['title']} ({task['task_id'][:8]})")
    else:
        lines.append("Outstanding tasks: none recorded")
    blockers = brief.get("blockers") or []
    if blockers:
        lines.append("Blockers:")
        for b in blockers:
            lines.append(f"  - {b['description']} ({b['blocker_id'][:8]})")
    else:
        lines.append("Blockers: none recorded")
    decisions = brief.get("decisions") or []
    if decisions:
        lines.append("Recent decisions:")
        for d in decisions:
            lines.append(f"  - {d['statement']}")
            if d.get("rationale"):
                lines.append(f"      why: {d['rationale']}")
    else:
        lines.append("Recent decisions: none recorded")
    next_action = brief.get("next_action")
    lines.append("")
    if next_action:
        lines.append(f"Suggested next action: {next_action}")
    else:
        lines.append("Suggested next action: none recorded (refusing to invent one)")
    return "\n".join(lines) + "\n"


def format_history(events: list[Any]) -> str:
    lines = []
    for event in events:
        payload = event.payload
        summary = payload.get("objective") or payload.get("to_state") or payload.get("title")
        if summary is None:
            summary = payload.get("statement") or payload.get("name") or payload.get("slug")
        if summary is None:
            summary = json.dumps(payload, sort_keys=True)[:120]
        lines.append(
            f"{event.ts_utc}  {event.event_type:24}  src={event.source:14}  "
            f"actor={event.actor}  {summary}"
        )
    if not lines:
        return "No events recorded.\n"
    return "\n".join(lines) + "\n"
