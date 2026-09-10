"""Read-only Clank Terminal (Foundation 3 alpha).

Localhost HTML/JSON over projection tables. No mutations, no scheduler,
no remote bind by default.

Connection ownership: ThreadingHTTPServer is kept so the listen loop is
not blocked by a slow request, but **each request opens its own
read-only SQLite connection/Store and closes it before the handler
returns**. Worker threads never share a sqlite3 connection. SQLite's
thread check stays enabled.
"""

from __future__ import annotations

import html
import json
from datetime import timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from clankops.brief import format_brief
from clankops.census import load_census
from clankops.clock import Clock
from clankops.errors import NotFoundError, ValidationError
from clankops.readmodel import (
    DEFAULT_STALE,
    coverage_report,
    dossier,
    fleet_home,
    open_sessions,
    stale_sessions,
)
from clankops.reconcile import reconcile_clank, reconcile_fleet
from clankops.store import Store, open_readonly_store
from clankops.timefmt import parse_duration

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_STALE_LABEL = "24h"


def _json(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n").encode(
        "utf-8"
    )


def _unknown(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value)


def _mark(label: str) -> str:
    return f"<span class=\"mark\">[{html.escape(label)}]</span>"


def _load_census(census: dict[str, Any] | None, census_path: str | Path | None) -> dict[str, Any] | None:
    if census is not None:
        return census
    if not census_path:
        return None
    path = Path(census_path)
    if not path.is_file():
        return None
    return load_census(path)


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
    if query.get("older-than") or query.get("older_than"):
        raw = (query.get("older-than") or query.get("older_than") or [DEFAULT_STALE_LABEL])[0]
        try:
            older = parse_duration(raw)
        except ValidationError as exc:
            return HTTPStatus.BAD_REQUEST, "text/plain; charset=utf-8", f"{exc}\n".encode("utf-8")
    github_raw = (query.get("github") or [None])[0]
    try:
        if route in {"/", "/fleet"}:
            home = fleet_home(
                store,
                census=census,
                now=now,
                stale_after=older,
                include_github=github_raw in {"1", "true", "yes"},
            )
            return HTTPStatus.OK, "text/html; charset=utf-8", _fleet_html(home).encode("utf-8")
        if route == "/api/fleet":
            home = fleet_home(
                store,
                census=census,
                now=now,
                stale_after=older,
                include_github=github_raw in {"1", "true", "yes"},
            )
            return HTTPStatus.OK, "application/json; charset=utf-8", _json(home)
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
                        include_github=github_raw in {"1", "true", "yes"},
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
                        "stale_after": DEFAULT_STALE_LABEL,
                        "connection": "per-request",
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
                        include_github=github_raw not in {"0", "false", "no"},
                    )
                ),
            )
        if route.startswith("/api/clank/"):
            slug = unquote(route.removeprefix("/api/clank/"))
            return (
                HTTPStatus.OK,
                "application/json; charset=utf-8",
                _json(
                    dossier(
                        store,
                        slug,
                        now=now,
                        stale_after=older,
                        include_github=github_raw not in {"0", "false", "no"},
                    )
                ),
            )
        if route.startswith("/clank/"):
            slug = unquote(route.removeprefix("/clank/"))
            payload = dossier(
                store,
                slug,
                now=now,
                stale_after=older,
                include_github=github_raw not in {"0", "false", "no"},
            )
            return HTTPStatus.OK, "text/html; charset=utf-8", _dossier_html(payload).encode("utf-8")
    except NotFoundError as exc:
        return HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", f"{exc}\n".encode("utf-8")
    return HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"not found\n"


def _page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; }}
    body {{ font-family: ui-monospace, Consolas, monospace; margin: 1.5rem; background: #0b0f14; color: #d7e0ea; }}
    a {{ color: #7ec8e3; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.92rem; }}
    th, td {{ border-bottom: 1px solid #243040; text-align: left; padding: 0.35rem 0.5rem; vertical-align: top; }}
    .muted {{ color: #8aa0b5; }}
    pre {{ white-space: pre-wrap; }}
    header {{ margin-bottom: 1rem; }}
    .summary {{ display: flex; flex-wrap: wrap; gap: 0.6rem 1.2rem; margin: 0.8rem 0 1.2rem; }}
    .summary div {{ border: 1px solid #243040; padding: 0.35rem 0.55rem; }}
    .mark {{ font-weight: 700; }}
    .src {{ font-weight: 700; }}
    h2 {{ margin-top: 1.6rem; font-size: 1.05rem; }}
    .note {{ margin: 0.4rem 0 1rem; }}
  </style>
</head>
<body>
  <header>
    <strong>ClankOps Terminal</strong>
    <span class="muted"> read-only alpha · localhost · GET/HEAD only</span>
    <div><a href="/">fleet</a> · <a href="/api/fleet">api/fleet</a> · <a href="/health">health</a></div>
  </header>
  {body}
</body>
</html>
"""


def _fleet_html(home: dict[str, Any]) -> str:
    summary = home.get("summary") or {}
    coverage = home.get("coverage") or {}
    chips = [
        ("registered Clanks", summary.get("registered_clanks")),
        ("ACTIVE Missions", summary.get("active_missions")),
        ("PAUSED Missions", summary.get("paused_missions")),
        ("BLOCKED Missions", summary.get("blocked_missions")),
        ("open Sessions", summary.get("open_sessions")),
        (f"stale Sessions ({summary.get('stale_after') or DEFAULT_STALE_LABEL})", summary.get("stale_sessions")),
        (
            "VERIFIED coverage",
            (
                f"{_unknown(summary.get('verified_registered'))}/"
                f"{_unknown(summary.get('verified_candidates'))}"
                if summary.get("verified_candidates") is not None
                else "unknown"
            ),
        ),
        (
            "git evidence",
            (
                f"drift {summary.get('git_drift') or 0} · "
                f"aligned {summary.get('git_aligned') or 0} · "
                f"partial {summary.get('git_partial') or 0}"
            ),
        ),
    ]
    chip_html = "".join(
        f"<div><span class=\"muted\">{html.escape(label)}</span><br><strong>{html.escape(str(value))}</strong></div>"
        for label, value in chips
    )
    note = ""
    if coverage:
        unresolved = coverage.get("verified_unresolved_count")
        note = (
            f"<p class=\"note muted\">VERIFIED census identities: {coverage.get('verified_candidates')}; "
            f"registered: {coverage.get('verified_registered')}; unresolved: {unresolved}. "
            f"{html.escape(coverage.get('verified_denominator_note') or '')}</p>"
        )
    cells = []
    for row in home.get("rows") or []:
        slug = html.escape(row["slug"])
        state = row.get("mission_state")
        state_mark = _mark(state) if state else _mark("unknown")
        session_bits = []
        if row.get("open_session_id"):
            session_bits.append(_mark("open"))
            session_bits.append(html.escape(_unknown(row.get("actor"))))
            session_bits.append(f"<span class=\"muted\">{html.escape(row['open_session_id'][:8])}</span>")
        else:
            session_bits.append(_mark("no-session"))
        if row.get("stale_session"):
            session_bits.append(_mark("stale"))
        if row.get("anomaly"):
            session_bits.append(_mark("ANOMALY"))
        wt = row.get("working_tree")
        wt_mark = _mark(wt) if wt else _mark("unknown")
        next_action = row.get("next_action")
        rec_status = row.get("reconcile_status") or "unknown"
        cells.append(
            "<tr>"
            f"<td><a href=\"/clank/{slug}\">{slug}</a><br><span class=\"muted\">{html.escape(row.get('display_name') or slug)}</span></td>"
            f"<td>{html.escape(_unknown(row.get('mission_display')))}</td>"
            f"<td>{state_mark}</td>"
            f"<td>{' '.join(session_bits)}</td>"
            f"<td>{html.escape(_unknown(row.get('latest_checkpoint_utc')))}</td>"
            f"<td>{html.escape(_unknown(row.get('branch')))}<br><span class=\"muted\">live {html.escape(_unknown(row.get('observed_branch')))}</span></td>"
            f"<td>{html.escape(_unknown(row.get('head_short')))}<br><span class=\"muted\">live {html.escape(_unknown(row.get('observed_head_short')))}</span></td>"
            f"<td>{wt_mark}</td>"
            f"<td>{html.escape(_unknown(next_action))}</td>"
            f"<td>{html.escape(_unknown(row.get('age_since_last_event')))}</td>"
            f"<td>{_mark(rec_status)}</td>"
            "</tr>"
        )
    table = (
        "<table><thead><tr>"
        "<th>Clank</th><th>Mission</th><th>Mission state</th><th>actor / open Session</th>"
        "<th>last checkpoint</th><th>branch</th><th>HEAD</th><th>working tree</th>"
        "<th>next action</th><th>age since last event</th><th>evidence</th>"
        "</tr></thead><tbody>"
        + "".join(cells)
        + "</tbody></table>"
    )
    return _page(
        "ClankOps Terminal",
        f"<div class=\"summary\">{chip_html}</div>{note}{table}",
    )


def _list_block(title: str, rows: list[dict[str, Any]], render) -> str:
    if not rows:
        return f"<h2>{html.escape(title)}</h2><p class=\"muted\">none recorded</p>"
    items = "".join(f"<li>{render(row)}</li>" for row in rows)
    return f"<h2>{html.escape(title)}</h2><ul>{items}</ul>"


def _dossier_html(payload: dict[str, Any]) -> str:
    ident = payload["identity"]
    now = payload.get("now") or {}
    sessions = now.get("open_sessions") or []
    session_html = _mark("none") if not sessions else "".join(
        f"<div>{_mark('open')} {html.escape(_unknown(s.get('actor')))} "
        f"{html.escape(s.get('session_id') or '')} "
        f"age {html.escape(_unknown(s.get('age')))}"
        f"{' ' + _mark('stale') if s.get('stale') else ''}"
        f"{' ' + _mark('ANOMALY') if s.get('anomaly') else ''}</div>"
        for s in sessions
    )
    next_action = now.get("next_action")
    now_section = f"""
<h2>NOW</h2>
<table>
  <tr><th>Mission</th><td>{html.escape(_unknown(now.get('mission_display')))} {html.escape(_unknown(now.get('mission_objective')))}</td></tr>
  <tr><th>state</th><td>{_mark(_unknown(now.get('mission_state')))}</td></tr>
  <tr><th>open Sessions</th><td>{session_html}</td></tr>
  <tr><th>checkpoint</th><td>{html.escape(_unknown((now.get('checkpoint') or {}).get('recorded_utc') if now.get('checkpoint') else None))}</td></tr>
  <tr><th>branch / HEAD</th><td>{html.escape(_unknown(now.get('branch')))} / {html.escape(_unknown(now.get('head')))}</td></tr>
  <tr><th>working tree</th><td>{_mark(_unknown(now.get('working_tree')))}</td></tr>
  <tr><th>tests</th><td>{html.escape(_unknown(now.get('tests')))}</td></tr>
  <tr><th>blockers</th><td>{html.escape('; '.join(b.get('description') or '' for b in (now.get('blockers') or [])) or 'none recorded')}</td></tr>
  <tr><th>outstanding tasks</th><td>{html.escape('; '.join(t.get('title') or '' for t in (now.get('outstanding_tasks') or [])) or 'none recorded')}</td></tr>
  <tr><th>next action</th><td>{html.escape(_unknown(next_action))}</td></tr>
</table>
"""
    rec = payload.get("reconcile") or {}
    rec_rows = []
    for item in rec.get("drift") or []:
        rec_rows.append(
            "<tr>"
            f"<td>{_mark(str(item.get('kind') or 'mismatch'))}</td>"
            f"<td>{html.escape(_unknown(item.get('field')))}</td>"
            f"<td>{_mark(str(item.get('source') or ''))}<span class=\"src\"> {html.escape(_unknown(item.get('source')))}</span></td>"
            f"<td>{html.escape(_unknown(item.get('recorded')))}</td>"
            f"<td>{html.escape(_unknown(item.get('observed')))}</td>"
            "</tr>"
        )
    local = rec.get("observed_local") or {}
    github = rec.get("observed_github") or {}
    recorded = rec.get("recorded") or {}
    status = rec.get("status") or "unknown"
    captions = {
        "aligned": "all comparable recorded claims corroborated",
        "partial": "some recorded claims corroborated; others unobservable",
        "no-record": "no recorded Git state to reconcile",
        "unknown": "recorded state could not be independently verified",
        "drift": "at least one independently compared fact contradicts the recorded state",
    }
    caption = captions.get(status, captions["unknown"])
    if status == "drift" and rec_rows:
        result_block = (
            f"<p class=\"muted\">{html.escape(caption)}</p>"
            "<table><thead><tr><th>kind</th><th>field</th><th>source</th><th>recorded</th><th>observed</th></tr></thead><tbody>"
            + "".join(rec_rows)
            + "</tbody></table>"
        )
    else:
        result_block = f"<p class=\"muted\">{html.escape(caption)}</p>"
    cmp_rows = []
    for field, item in (rec.get("comparisons") or {}).items():
        cmp_rows.append(
            "<tr>"
            f"<td>{html.escape(field)}</td>"
            f"<td>{_mark(str(item.get('status') or 'unknown'))}</td>"
            f"<td>{_mark(str(item.get('source') or 'none'))}<span class=\"src\"> {html.escape(_unknown(item.get('source')))}</span></td>"
            f"<td>{html.escape(_unknown(item.get('recorded')))}</td>"
            f"<td>{html.escape(_unknown(item.get('observed')))}</td>"
            "</tr>"
        )
    cmp_table = (
        "<table><thead><tr><th>field</th><th>coverage</th><th>source</th><th>recorded</th><th>observed</th></tr></thead><tbody>"
        + "".join(cmp_rows)
        + "</tbody></table>"
        if cmp_rows
        else ""
    )
    github_err = github.get("error") if github else "not requested"
    prs = github.get("open_prs") if github else []
    pr_text = ", ".join(
        f"#{p.get('number')} {p.get('head_ref')} {(p.get('head') or '')[:7]}"
        for p in (prs or [])
    ) or "none"
    checks = (github.get("checks") or {}) if github else {}
    check_runs = checks.get("runs") or []
    check_run_text = ", ".join(
        f"{run.get('name') or 'check'} {run.get('conclusion') or run.get('status') or 'unknown'}"
        for run in check_runs
    ) or "no check-runs"
    reconcile_section = f"""
<h2>RECONCILIATION</h2>
<p class="muted">Independent LOCAL_GIT / GITHUB observation vs recorded claims. Observer success is not corroboration. Empty CI checks are none, never success. History is not rewritten.</p>
<table>
  <tr><th>status</th><td>{_mark(_unknown(status))} {html.escape(caption)}</td></tr>
  <tr><th>recorded claim</th><td>{html.escape(_unknown(recorded.get('branch')))} / {html.escape(_unknown(recorded.get('head')))} / {html.escape(_unknown(recorded.get('working_tree')))} [{html.escape(_unknown(recorded.get('event_source')))}]</td></tr>
  <tr><th>LOCAL_GIT</th><td>{_mark('LOCAL_GIT')} {html.escape(_unknown(local.get('branch')))} / {html.escape(_unknown(local.get('head')))} / {html.escape(_unknown(local.get('working_tree')))} {html.escape(local.get('error') or '')}</td></tr>
  <tr><th>GITHUB</th><td>{_mark('GITHUB')} default {html.escape(_unknown(github.get('default_branch') if github else None))} / {html.escape(_unknown(github.get('default_branch_head') if github else None))} PRs {html.escape(pr_text)} {html.escape(str(github_err or ''))}</td></tr>
  <tr><th>CI checks</th><td>{_mark('GITHUB')} {html.escape(_unknown(checks.get('state')))} sha {html.escape(_unknown(checks.get('sha')))} {html.escape(check_run_text)} {html.escape(_unknown(checks.get('error')))}</td></tr>
</table>
{cmp_table}
{result_block}
"""
    trows = []
    for event in payload.get("timeline") or []:
        labels = event.get("provenance") or [event.get("source")]
        src = " ".join(_mark(str(label)) + f"<span class=\"src\"> {html.escape(str(label))}</span>" for label in labels if label)
        trows.append(
            "<tr>"
            f"<td>{html.escape(str(event.get('ledger_seq')))}</td>"
            f"<td>{html.escape(_unknown(event.get('ts_utc')))}</td>"
            f"<td>{html.escape(_unknown(event.get('event_type')))}</td>"
            f"<td>{html.escape(_unknown(event.get('actor')))}</td>"
            f"<td>{src}</td>"
            f"<td>{html.escape(_unknown(event.get('session_id')))}</td>"
            f"<td>{html.escape(_unknown(event.get('summary')) if event.get('summary') else '') or html.escape('—')}</td>"
            "</tr>"
        )
    timeline = (
        "<h2>TIMELINE</h2>"
        "<p class=\"muted\">canonical ledger_seq order · provenance shown as text, never colour alone</p>"
        "<table><thead><tr><th>seq</th><th>timestamp</th><th>event type</th><th>actor</th>"
        "<th>source</th><th>Session</th><th>summary</th></tr></thead><tbody>"
        + "".join(trows)
        + "</tbody></table>"
    )
    missions = _list_block(
        "MISSIONS",
        payload.get("missions") or [],
        lambda m: f"{_mark(m.get('state') or 'unknown')} {html.escape(m.get('display_id') or '')} — {html.escape(m.get('objective') or '')}",
    )
    features = _list_block(
        "FEATURES",
        payload.get("features") or [],
        lambda f: f"{_mark(f.get('state') or 'unknown')} {html.escape(f.get('name') or '')}",
    )
    tasks = _list_block(
        "TASKS",
        payload.get("tasks") or [],
        lambda t: f"{_mark(t.get('state') or 'unknown')} {html.escape(t.get('title') or '')}",
    )
    decisions = _list_block(
        "DECISIONS",
        payload.get("decisions") or [],
        lambda d: html.escape(d.get("statement") or ""),
    )
    brief_pre = (
        "<details><summary class=\"muted\">raw brief (input)</summary>"
        f"<pre>{html.escape(format_brief(payload.get('brief') or payload))}</pre></details>"
    )
    heading = f"<h1>{html.escape(ident.get('display_name') or ident.get('slug') or '')}</h1>"
    return _page(
        f"{ident.get('slug')} · ClankOps Terminal",
        heading + now_section + reconcile_section + timeline + missions + features + tasks + decisions + brief_pre,
    )


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
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-ClankOps-Mode", "read-only")
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


def serve(
    *,
    db_path: str | Path,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    census: dict[str, Any] | None = None,
    census_path: str | Path | None = None,
    stale_after: timedelta = DEFAULT_STALE,
    clock: Clock | None = None,
) -> ThreadingHTTPServer:
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
    return ThreadingHTTPServer((host, port), handler)
