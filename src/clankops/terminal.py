"""Read-only Clank Terminal Beta (Fleet Command Centre).

Presents evidence. Does not create authority. Browser GET/HEAD only.
Each HTTP worker opens its own read-only Store and closes it before return.
"""

from __future__ import annotations

import json
from datetime import timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from clankops.attention import CLASS_INTEGRITY, attention_report
from clankops.census import load_census
from clankops.ci import CI_ARTIFACT_KIND, ci_view_from_artifact, latest_mission_ci
from clankops.clock import Clock, isoformat_utc
from clankops.errors import NotFoundError, ValidationError
from clankops.harvest import local_git_harvest_view
from clankops.readmodel import (
    DEFAULT_STALE,
    TIMELINE_DEFAULT,
    TIMELINE_MAX,
    coverage_report,
    dossier,
    fleet_home,
    ledger_fingerprint,
    list_sessions,
    open_sessions,
    stale_sessions,
)
from clankops.reconcile import reconcile_clank, reconcile_fleet
from clankops.store import Store, open_readonly_store
from clankops.terminal_query import QueryError, apply_filters, parse_filter
from clankops.terminal_views import (
    attention_html,
    dossier_html,
    fleet_html,
    mode_label,
    session_dossier_html as _session_dossier_html,
    sessions_html,
)
from clankops.timefmt import format_age, parse_duration, parse_utc

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_STALE_LABEL = "24h"
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
SECURITY_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    (
        "Content-Security-Policy",
        "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
        "img-src 'none'; connect-src 'self'; base-uri 'none'; form-action 'self'",
    ),
    ("X-ClankOps-Mode", "read-only"),
)


def snapshot_inspect_local(_path: str) -> dict[str, Any]:
    """Do not inspect the working tree. Snapshot pages must not run git."""
    return {
        "is_git": False,
        "git_error": "snapshot: live local not requested",
        "current_branch": None,
        "head": None,
        "dirty": None,
        "dirty_count": 0,
        "ahead": None,
        "behind": None,
        "upstream": None,
    }


def _json(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n").encode(
        "utf-8"
    )


def _load_census(census: dict[str, Any] | None, census_path: str | Path | None) -> dict[str, Any] | None:
    if census is not None:
        return census
    if not census_path:
        return None
    path = Path(census_path)
    if not path.is_file():
        return None
    return load_census(path)


def _flag(query: dict[str, list[str]], name: str) -> bool:
    raw = (query.get(name) or [None])[0]
    return str(raw or "").strip().lower() in {"1", "true", "yes"}


def _query_one(query: dict[str, list[str]], name: str) -> str | None:
    raw = (query.get(name) or [None])[0]
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _github_api_flag(query: dict[str, list[str]], *, default: bool) -> bool:
    """Foundation 3 GitHub query semantics for reconcile API routes."""
    raw = _query_one(query, "github")
    if raw is None:
        return default
    lowered = raw.lower()
    if default:
        return lowered not in {"0", "false", "no"}
    return lowered in {"1", "true", "yes"}


def _timeline_limit(query: dict[str, list[str]]) -> int:
    raw = (query.get("limit") or [str(TIMELINE_DEFAULT)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return TIMELINE_DEFAULT
    return max(1, min(value, TIMELINE_MAX))


def _snapshot(store: Store, now) -> dict[str, Any]:
    fp = ledger_fingerprint(store)
    instant = now or store.clock.now()
    return {
        "generated_at": isoformat_utc(instant),
        "ledger_event_count": fp["event_count"],
        "max_ledger_seq": fp["max_ledger_seq"],
        "consistency": "read-time; not a transactionally frozen multi-page snapshot",
    }


def _mode(live_local: bool, github: bool) -> dict[str, Any]:
    return {
        "live_local": live_local,
        "github": github,
        "label": mode_label(live_local=live_local, github=github),
    }


def _inspect_local(live_local: bool):
    return None if live_local else snapshot_inspect_local


def _decorate_rows(store: Store, home: dict[str, Any], *, now) -> dict[str, Any]:
    items = (home.get("attention") or {}).get("items") or []
    by_clank: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_clank.setdefault(item.get("clank"), []).append(item)
    tasks: dict[str, list[str]] = {}
    for row in store.conn.execute("SELECT clank_id, title FROM tasks"):
        tasks.setdefault(row["clank_id"], []).append(row["title"] or "")
    features: dict[str, list[str]] = {}
    for row in store.conn.execute("SELECT clank_id, name FROM features"):
        features.setdefault(row["clank_id"], []).append(row["name"] or "")
    decisions: dict[str, list[str]] = {}
    for row in store.conn.execute("SELECT clank_id, statement FROM decisions"):
        decisions.setdefault(row["clank_id"], []).append(row["statement"] or "")
    ci_by_mission: dict[str, dict[str, Any]] = {}
    for row in store.conn.execute(
        """
        SELECT * FROM artifacts
        WHERE kind = ?
        ORDER BY created_utc DESC, artifact_id DESC
        """,
        (CI_ARTIFACT_KIND,),
    ):
        if row["mission_id"] not in ci_by_mission:
            ci_by_mission[row["mission_id"]] = ci_view_from_artifact(row)
    deploy_counts: dict[str, int] = {}
    deploy_total = 0
    from clankops.deployment import current_deployments

    for clank in store.list_clanks():
        surfaces = current_deployments(store, clank["clank_id"])
        deploy_counts[clank["slug"]] = len(surfaces)
        deploy_total += len(surfaces)
    harvest_observed = 0
    harvest_never = 0
    harvest_failed = 0
    integrity = 0
    for row in home.get("rows") or []:
        view = local_git_harvest_view(store, row["slug"], now=now)
        row["harvest"] = view
        row["harvest_result"] = view.get("latest_result")
        row["harvest_never"] = bool(view.get("never_harvested"))
        state = view.get("semantic_state") or {}
        row["harvest_dirty"] = state.get("dirty")
        if view.get("never_harvested"):
            harvest_never += 1
        elif view.get("latest_result") in {"OBSERVED_CHANGED", "OBSERVED_UNCHANGED"}:
            harvest_observed += 1
        elif view.get("latest_result"):
            harvest_failed += 1
        clank_items = by_clank.get(row["slug"]) or []
        row["attention_count"] = len(clank_items)
        row["attention_classes"] = sorted({item.get("class") for item in clank_items if item.get("class")})
        row["attention_top"] = clank_items[0] if clank_items else {}
        integrity += sum(1 for item in clank_items if item.get("class") == CLASS_INTEGRITY)
        sessions = []
        for item in home.get("open_sessions") or []:
            if item.get("clank_id") == row.get("clank_id"):
                sessions.append(item)
        process = (sessions[0].get("managed_process") if sessions else {}) or {}
        row["process_status"] = process.get("status") or "UNKNOWN"
        if sessions:
            row["handoff"] = process.get("handoff") or sessions[0].get("handoff") or "MISSING"
        else:
            row["handoff"] = "UNKNOWN"
        row["task_titles"] = tasks.get(row["clank_id"], [])
        row["feature_names"] = features.get(row["clank_id"], [])
        row["decision_statements"] = decisions.get(row["clank_id"], [])
        mission_id = row.get("mission_id")
        ci = ci_by_mission.get(mission_id) if mission_id else None
        row["ci_summary"] = (ci or {}).get("state") or "UNKNOWN"
        row["ci"] = ci
        row["deployment_count"] = deploy_counts.get(row["slug"], 0)
        recorded = parse_utc(row.get("latest_checkpoint_utc"))
        instant = now or store.clock.now()
        row["checkpoint_age"] = format_age((instant - recorded) if recorded else None)
        last = store.conn.execute(
            "SELECT ledger_seq FROM events WHERE clank_id = ? ORDER BY ledger_seq DESC LIMIT 1",
            (row["clank_id"],),
        ).fetchone()
        row["last_event_seq"] = last["ledger_seq"] if last else None
    summary = home.setdefault("summary", {})
    summary["harvest_observed"] = harvest_observed
    summary["harvest_never"] = harvest_never
    summary["harvest_failed"] = harvest_failed
    summary["attention_count"] = len(items)
    summary["attention_integrity"] = integrity
    summary["deployment_surfaces"] = deploy_total
    summary["ci_missions"] = len(ci_by_mission)
    summary.setdefault("planned_missions", 0)
    return home


def _command_centre(
    store: Store,
    *,
    census: dict[str, Any] | None,
    now,
    stale_after: timedelta,
    live_local: bool,
    github: bool,
    threshold_label: str | None,
    threshold_source: str | None,
    query_text: str,
) -> tuple[dict[str, Any], str | None]:
    home = fleet_home(
        store,
        census=census,
        now=now,
        stale_after=stale_after,
        include_github=github,
        inspect_local=_inspect_local(live_local),
        threshold_label=threshold_label,
        threshold_source=threshold_source,
        live_local=live_local,
    )
    home = _decorate_rows(store, home, now=now)
    home["snapshot"] = _snapshot(store, now)
    home["mode"] = _mode(live_local, github)
    error = None
    if query_text.strip():
        try:
            parsed = parse_filter(query_text)
            home["rows"] = apply_filters(home.get("rows") or [], parsed)
        except QueryError as exc:
            error = str(exc)
            home["rows"] = []
    return home, error


def dispatch(
    store: Store,
    method: str,
    path: str,
    *,
    census: dict[str, Any] | None = None,
    stale_after: timedelta = DEFAULT_STALE,
    now=None,
) -> tuple[int, str, bytes]:
    """Pure request handler used by tests and the HTTP wrapper."""
    method = method.upper()
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    query = parse_qs(parsed.query)
    if method not in {"GET", "HEAD"}:
        return HTTPStatus.METHOD_NOT_ALLOWED, "text/plain; charset=utf-8", b"read-only\n"
    older = stale_after
    threshold_label = None
    threshold_source = None
    if query.get("older-than") or query.get("older_than"):
        raw = (query.get("older-than") or query.get("older_than") or [DEFAULT_STALE_LABEL])[0]
        try:
            older = parse_duration(raw)
        except ValidationError as exc:
            return HTTPStatus.BAD_REQUEST, "text/plain; charset=utf-8", f"{exc}\n".encode("utf-8")
        threshold_label = raw
        threshold_source = "operator-supplied"
    live_local = _flag(query, "live")
    github = _flag(query, "github")
    q = (query.get("q") or [""])[0]
    try:
        if route in {"/", "/fleet"}:
            home, error = _command_centre(
                store,
                census=census,
                now=now,
                stale_after=older,
                live_local=live_local,
                github=github,
                threshold_label=threshold_label,
                threshold_source=threshold_source,
                query_text=q,
            )
            return HTTPStatus.OK, "text/html; charset=utf-8", fleet_html(
                home, query=q, error=error
            ).encode("utf-8")
        if route == "/api/fleet":
            home, error = _command_centre(
                store,
                census=census,
                now=now,
                stale_after=older,
                live_local=live_local,
                github=github,
                threshold_label=threshold_label,
                threshold_source=threshold_source,
                query_text=q,
            )
            if error:
                home = {**home, "query_error": error}
            return HTTPStatus.OK, "application/json; charset=utf-8", _json(home)
        if route == "/attention":
            report = attention_report(
                store,
                now=now,
                stale_after=older,
                include_github=github,
                inspect_local=_inspect_local(live_local),
                live_local=live_local,
            )
            clank = (query.get("clank") or [None])[0]
            klass = (query.get("class") or [None])[0]
            reason = (query.get("reason") or query.get("reason_code") or [None])[0]
            items = report.get("items") or []
            if clank:
                items = [item for item in items if item.get("clank") == clank]
            if klass:
                items = [item for item in items if item.get("class") == klass]
            if reason:
                items = [item for item in items if item.get("reason_code") == reason]
            report = {**report, "items": items}
            report["snapshot"] = _snapshot(store, now)
            report["mode"] = _mode(live_local, github)
            return HTTPStatus.OK, "text/html; charset=utf-8", attention_html(report).encode("utf-8")
        if route == "/api/attention":
            clank = (query.get("clank") or [None])[0]
            report = attention_report(
                store,
                clank,
                now=now,
                stale_after=older,
                include_github=github,
                inspect_local=_inspect_local(live_local),
                live_local=live_local,
            )
            klass = (query.get("class") or [None])[0]
            reason = (query.get("reason") or query.get("reason_code") or [None])[0]
            items = report.get("items") or []
            if klass:
                items = [item for item in items if item.get("class") == klass]
            if reason:
                items = [item for item in items if item.get("reason_code") == reason]
            report = {**report, "items": items, "snapshot": _snapshot(store, now), "mode": _mode(live_local, github)}
            return HTTPStatus.OK, "application/json; charset=utf-8", _json(report)
        if route == "/sessions":
            payload = {
                "sessions": list_sessions(store, now=now, stale_after=older),
                "snapshot": _snapshot(store, now),
                "mode": _mode(live_local, github),
            }
            return HTTPStatus.OK, "text/html; charset=utf-8", sessions_html(payload).encode("utf-8")
        if route == "/api/sessions":
            payload = {
                "sessions": list_sessions(store, now=now, stale_after=older),
                "snapshot": _snapshot(store, now),
                "mode": _mode(live_local, github),
            }
            return HTTPStatus.OK, "application/json; charset=utf-8", _json(payload)
        if route == "/api/coverage":
            if census is None:
                return HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"census unavailable\n"
            return (
                HTTPStatus.OK,
                "application/json; charset=utf-8",
                _json(coverage_report(store, census)),
            )
        if route == "/api/sessions/open":
            return (
                HTTPStatus.OK,
                "application/json; charset=utf-8",
                _json(open_sessions(store, now=now, stale_after=older)),
            )
        if route == "/api/sessions/stale":
            return (
                HTTPStatus.OK,
                "application/json; charset=utf-8",
                _json(stale_sessions(store, older_than=older, now=now)),
            )
        if route == "/api/reconcile":
            return (
                HTTPStatus.OK,
                "application/json; charset=utf-8",
                _json(
                    reconcile_fleet(
                        store,
                        include_github=_github_api_flag(query, default=False),
                    )
                ),
            )
        if route == "/health":
            return (
                HTTPStatus.OK,
                "application/json; charset=utf-8",
                _json(
                    {
                        "ok": True,
                        "mode": "read-only",
                        "terminal": "beta",
                        "stale_after": DEFAULT_STALE_LABEL,
                        "connection": "per-request",
                        "snapshot": _snapshot(store, now),
                    }
                ),
            )
        if route.startswith("/api/clank/") and route.endswith("/reconcile"):
            slug = unquote(route.removeprefix("/api/clank/").removesuffix("/reconcile").rstrip("/"))
            return (
                HTTPStatus.OK,
                "application/json; charset=utf-8",
                _json(
                    reconcile_clank(
                        store,
                        slug,
                        include_github=_github_api_flag(query, default=True),
                    )
                ),
            )
        if route.startswith("/api/clank/"):
            slug = unquote(route.removeprefix("/api/clank/"))
            payload = _dossier_payload(
                store,
                slug,
                now=now,
                stale_after=older,
                live_local=live_local,
                github=github,
                query=query,
            )
            return HTTPStatus.OK, "application/json; charset=utf-8", _json(payload)
        if route.startswith("/clank/"):
            slug = unquote(route.removeprefix("/clank/"))
            payload = _dossier_payload(
                store,
                slug,
                now=now,
                stale_after=older,
                live_local=live_local,
                github=github,
                query=query,
            )
            return HTTPStatus.OK, "text/html; charset=utf-8", dossier_html(payload).encode("utf-8")
    except NotFoundError as exc:
        return HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", f"{exc}\n".encode("utf-8")
    except ValidationError as exc:
        return HTTPStatus.BAD_REQUEST, "text/plain; charset=utf-8", f"{exc}\n".encode("utf-8")
    return HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n"


def _dossier_payload(
    store: Store,
    slug: str,
    *,
    now,
    stale_after: timedelta,
    live_local: bool,
    github: bool,
    query: dict[str, list[str]],
) -> dict[str, Any]:
    limit = _timeline_limit(query)
    payload = dossier(
        store,
        slug,
        now=now,
        stale_after=stale_after,
        include_github=github,
        inspect_local=_inspect_local(live_local),
        timeline_limit=limit,
        timeline_source=_query_one(query, "source"),
        timeline_event_type=_query_one(query, "event_type"),
        timeline_mission=_query_one(query, "mission"),
    )
    payload["timeline_limit"] = limit
    payload["timeline_available"] = payload.get("timeline_matched", len(payload.get("timeline") or []))
    payload["snapshot"] = _snapshot(store, now)
    payload["mode"] = _mode(live_local, github)
    payload["attention"] = attention_report(
        store,
        slug,
        now=now,
        stale_after=stale_after,
        include_github=github,
        inspect_local=_inspect_local(live_local),
        reconcile=payload.get("reconcile"),
        live_local=live_local,
    )
    now_block = payload.get("now") or {}
    current_mission_id = now_block.get("mission_id")
    payload["ci"] = latest_mission_ci(store, current_mission_id)
    clank_id = (payload.get("identity") or {}).get("clank_id")
    for mission in payload.get("missions") or []:
        count = store.conn.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE mission_id = ?",
            (mission["mission_id"],),
        ).fetchone()
        mission["session_count"] = int(count["n"] if count else 0)
        cp = store.latest_checkpoint(clank_id, mission["mission_id"]) if clank_id else None
        mission["next_action"] = cp.get("next_action") if cp else None
        mission["created_utc"] = mission.get("created_utc") or mission.get("created_at")
        mission["checkpoint"] = (
            {
                "recorded_utc": cp.get("recorded_utc"),
                "branch": cp.get("branch"),
                "head": cp.get("head"),
                "working_tree": cp.get("working_tree"),
                "next_action": cp.get("next_action"),
                "current_work": cp.get("current_work"),
                "completed": cp.get("completed"),
            }
            if cp
            else None
        )
        mission["ci"] = latest_mission_ci(store, mission.get("mission_id"))
    return payload


def _fleet_html(home: dict[str, Any]) -> str:
    """Compatibility wrapper for Foundation tests."""
    return fleet_html(home)


def _dossier_html(payload: dict[str, Any]) -> str:
    """Compatibility wrapper for Foundation tests."""
    return dossier_html(payload)


class TerminalHandler(BaseHTTPRequestHandler):
    db_path: str
    census: dict[str, Any] | None = None
    stale_after: timedelta = DEFAULT_STALE
    clock: Clock | None = None

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _write(self, status: int, content_type: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for key, value in SECURITY_HEADERS:
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _handle(self, method: str) -> None:
        store = open_readonly_store(self.db_path, clock=self.clock)
        try:
            status, ctype, body = dispatch(
                store,
                method,
                self.path,
                census=self.census,
                stale_after=self.stale_after,
            )
        finally:
            store.conn.close()
        self._write(status, ctype, body)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._handle("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._handle("PUT")

    def do_PATCH(self) -> None:  # noqa: N802
        self._handle("PATCH")

    def do_DELETE(self) -> None:  # noqa: N802
        self._handle("DELETE")


def assert_bind_allowed(host: str, *, allow_remote: bool = False) -> str:
    cleaned = (host or "").strip() or DEFAULT_HOST
    if cleaned in LOOPBACK_HOSTS:
        return cleaned
    try:
        if ip_address(cleaned).is_loopback:
            return cleaned
    except ValueError:
        pass
    if allow_remote:
        return cleaned
    raise ValidationError(
        f"non-loopback Terminal bind {cleaned!r} requires --allow-remote"
    )


def serve(
    *,
    db_path: str | Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    census: dict[str, Any] | None = None,
    census_path: str | Path | None = None,
    stale_after: timedelta = DEFAULT_STALE,
    clock: Clock | None = None,
    allow_remote: bool = False,
) -> ThreadingHTTPServer:
    bound = assert_bind_allowed(host, allow_remote=allow_remote)
    loaded = _load_census(census, census_path)
    handler = type(
        "BoundTerminalHandler",
        (TerminalHandler,),
        {
            "db_path": str(Path(db_path)),
            "census": loaded,
            "stale_after": stale_after,
            "clock": clock,
        },
    )
    return ThreadingHTTPServer((bound, port), handler)
