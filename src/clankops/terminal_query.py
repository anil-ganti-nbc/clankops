"""Bounded Terminal Beta filter grammar. No SQL interpolation. No eval."""

from __future__ import annotations

import re
import shlex
from typing import Any

from clankops.enums import MissionState

FILTER_KEYS = frozenset(
    {
        "state",
        "session",
        "attention",
        "git",
        "harvest",
        "clank",
        "mission",
    }
)
STATE_VALUES = {
    "active": MissionState.ACTIVE,
    "paused": MissionState.PAUSED,
    "blocked": MissionState.BLOCKED,
    "planned": MissionState.PLANNED,
    "completed": MissionState.COMPLETED,
    "abandoned": MissionState.ABANDONED,
    "superseded": MissionState.SUPERSEDED,
}
SESSION_VALUES = frozenset({"open", "none", "stale"})
ATTENTION_VALUES = frozenset({"any", "integrity", "informational", "age", "none"})
GIT_VALUES = frozenset({"dirty", "clean", "unknown"})
HARVEST_VALUES = frozenset({"observed", "failed", "never"})
TOKEN_RE = re.compile(r"^([a-z_]+):(.*)$", re.I)
FAILURE_RESULTS = frozenset(
    {
        "TIMEOUT",
        "ERROR",
        "PATH_MISSING",
        "NOT_A_GIT_REPOSITORY",
        "AMBIGUOUS_CANONICAL_PATH",
        "STALE_OBSERVATION",
        "GIT_MISSING",
    }
)
SUCCESS_HARVEST = frozenset({"OBSERVED_CHANGED", "OBSERVED_UNCHANGED"})


class QueryError(ValueError):
    """Visible operator validation error. Not a control-plane crash."""


def parse_filter(raw: str | None) -> dict[str, Any]:
    """Parse command-bar text into structured filters plus free-text terms.

    Grammar: optional `key:value` tokens mixed with unqualified words.
    Multiple structured filters are AND. Unknown keys and invalid values
    fail closed with QueryError — they never silently match the full fleet.
    """
    text = (raw or "").strip()
    filters: dict[str, list[str]] = {key: [] for key in FILTER_KEYS}
    terms: list[str] = []
    if not text:
        return {"filters": filters, "terms": terms, "raw": ""}
    try:
        parts = shlex.split(text, posix=True)
    except ValueError as exc:
        raise QueryError(f"invalid query: {exc}") from exc
    for part in parts:
        match = TOKEN_RE.match(part)
        if not match:
            terms.append(part.casefold())
            continue
        key = match.group(1).casefold()
        value = match.group(2).strip()
        if key not in FILTER_KEYS:
            raise QueryError(f"unknown filter key: {key}")
        if not value:
            raise QueryError(f"invalid filter value for {key}: empty")
        _validate(key, value)
        filters[key].append(value)
    return {"filters": filters, "terms": terms, "raw": text}


def _validate(key: str, value: str) -> None:
    lowered = value.casefold()
    allowed = {
        "state": STATE_VALUES,
        "session": SESSION_VALUES,
        "attention": ATTENTION_VALUES,
        "git": GIT_VALUES,
        "harvest": HARVEST_VALUES,
    }.get(key)
    if allowed is not None and lowered not in allowed:
        raise QueryError(f"invalid filter value for {key}: {value}")
    if key == "clank" and any(ch in value for ch in ";'\""):
        raise QueryError(f"invalid filter value for {key}: {value}")
    if key == "mission" and not re.fullmatch(r"COPS-\d{6}", value, re.I):
        raise QueryError(f"invalid filter value for {key}: {value}")


def row_search_blob(row: dict[str, Any]) -> str:
    parts = [
        row.get("slug"),
        row.get("display_name"),
        row.get("mission_display"),
        row.get("mission_objective"),
        row.get("next_action"),
        " ".join(row.get("task_titles") or []),
        " ".join(row.get("feature_names") or []),
        " ".join(row.get("decision_statements") or []),
        row.get("timeline_summary"),
    ]
    return " ".join(str(part) for part in parts if part).casefold()


def apply_filters(rows: list[dict[str, Any]], parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """AND-combine structured filters and case-insensitive free-text terms."""
    filters = parsed.get("filters") or {}
    terms = parsed.get("terms") or []
    out = []
    for row in rows:
        if not _row_matches(row, filters, terms):
            continue
        out.append(row)
    return out


def _row_matches(row: dict[str, Any], filters: dict[str, list[str]], terms: list[str]) -> bool:
    for value in filters.get("state") or []:
        wanted = STATE_VALUES[value.casefold()]
        if row.get("mission_state") != wanted:
            return False
    for value in filters.get("session") or []:
        kind = value.casefold()
        open_count = int(row.get("open_session_count") or 0)
        if kind == "open" and open_count <= 0:
            return False
        if kind == "none" and open_count > 0:
            return False
        if kind == "stale" and not row.get("stale_session"):
            return False
    for value in filters.get("attention") or []:
        kind = value.casefold()
        classes = set(row.get("attention_classes") or [])
        count = int(row.get("attention_count") or 0)
        if kind == "any" and count <= 0:
            return False
        if kind == "none" and count > 0:
            return False
        if kind in {"integrity", "informational", "age"} and kind not in classes:
            return False
    for value in filters.get("git") or []:
        kind = value.casefold()
        dirty = row.get("harvest_dirty")
        if kind == "dirty" and dirty is not True:
            return False
        if kind == "clean" and dirty is not False:
            return False
        if kind == "unknown" and dirty is not None:
            return False
    for value in filters.get("harvest") or []:
        kind = value.casefold()
        result = row.get("harvest_result")
        never = bool(row.get("harvest_never"))
        if kind == "observed" and result not in SUCCESS_HARVEST:
            return False
        if kind == "failed" and result not in FAILURE_RESULTS:
            return False
        if kind == "never" and not never:
            return False
    for value in filters.get("clank") or []:
        if str(row.get("slug") or "").casefold() != value.casefold():
            return False
    for value in filters.get("mission") or []:
        if str(row.get("mission_display") or "").casefold() != value.casefold():
            return False
    blob = row_search_blob(row)
    for term in terms:
        if term not in blob:
            return False
    return True
