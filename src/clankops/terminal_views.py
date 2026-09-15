"""Server-rendered Terminal Beta HTML. No CDN, no npm, inline CSS/JS only."""

from __future__ import annotations

import html
from typing import Any

from clankops.attention import CLASS_MARK
from clankops.brief import format_brief
from clankops.timefmt import short_head

HELP_BINDINGS = (
    ("/", "focus command/filter bar"),
    ("j", "next fleet row"),
    ("k", "previous fleet row"),
    ("Enter", "open selected Clank dossier"),
    ("g f", "fleet"),
    ("g a", "attention"),
    ("g s", "sessions"),
    ("r", "refresh current read-only snapshot"),
    ("?", "shortcut/help overlay"),
    ("Esc", "clear focus / help / selection"),
)


def _unknown(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    return str(value)


def mark(label: str) -> str:
    return f'<span class="mark">[{html.escape(str(label))}]</span>'


def badge(source: str) -> str:
    return f'<span class="src">{mark(source)}</span>'


def mode_label(*, live_local: bool, github: bool) -> str:
    if live_local and github:
        return "LIVE LOCAL+GITHUB NETWORK"
    if live_local:
        return "LIVE LOCAL"
    if github:
        return "GITHUB NETWORK"
    return "SNAPSHOT"


def _css() -> str:
    return """
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: #07090c; color: #c9d4df; }
body { font-family: ui-monospace, "Cascadia Mono", Consolas, monospace; font-size: 12px; line-height: 1.35; }
a { color: #8fd0ea; text-decoration: none; }
a:hover { text-decoration: underline; }
a:focus, button:focus, input:focus, select:focus { outline: 2px solid #e0b15c; outline-offset: 1px; }
.status { display: flex; flex-wrap: wrap; gap: 0.4rem 1rem; padding: 0.4rem 0.7rem; border-bottom: 1px solid #243040; background: #0b1016; position: sticky; top: 0; z-index: 5; }
.status strong { letter-spacing: 0.08em; }
.nav a { margin-right: 0.8rem; }
.command { display: flex; gap: 0.5rem; padding: 0.35rem 0.7rem; border-bottom: 1px solid #243040; align-items: center; }
#command-bar { flex: 1; background: #10161d; color: #d7e0ea; border: 1px solid #3a4a5c; padding: 0.28rem 0.45rem; font: inherit; }
.strip { display: flex; flex-wrap: wrap; gap: 0.45rem 0.9rem; padding: 0.4rem 0.7rem; border-bottom: 1px solid #243040; }
.strip div { border: 1px solid #243040; padding: 0.2rem 0.4rem; }
.muted { color: #8aa0b5; }
.mark { font-weight: 700; }
.src { font-weight: 700; }
.warn { color: #e0b15c; }
.err { border: 1px solid #e07a7a; color: #e07a7a; margin: 0.5rem 0.7rem; padding: 0.4rem; }
.grid-wrap { overflow-x: auto; }
table.grid { border-collapse: collapse; width: max-content; min-width: 100%; }
table.grid th, table.grid td { border-bottom: 1px solid #243040; text-align: left; padding: 0.28rem 0.45rem; vertical-align: top; white-space: nowrap; }
table.grid th { position: sticky; top: 0; background: #10161d; z-index: 2; }
table.grid td.wrap { white-space: normal; max-width: 18rem; }
tr.selected td { box-shadow: inset 3px 0 0 #e0b15c; background: #13202c; }
.second { display: block; color: #8aa0b5; font-size: 11px; }
main { padding: 0.6rem 0.7rem 2rem; }
h1, h2 { font-size: 13px; letter-spacing: 0.06em; margin: 1rem 0 0.4rem; }
.matrix { width: 100%; border-collapse: collapse; }
.matrix th, .matrix td { border: 1px solid #243040; padding: 0.28rem 0.4rem; }
.attention-item { display: flex; gap: 0.6rem; border: 1px solid #243040; border-left-width: 0.45rem; padding: 0.4rem 0.55rem; margin: 0.35rem 0; }
.attention-integrity { border-left-style: solid; }
.attention-informational { border-left-style: dotted; }
.attention-age { border-left-style: dashed; }
#help { display: none; position: fixed; inset: 10% 15%; background: #0b1016; border: 1px solid #e0b15c; padding: 1rem; z-index: 20; overflow: auto; }
#help.open { display: block; }
kbd { border: 1px solid #3a4a5c; padding: 0 0.25rem; }
"""


def _js() -> str:
    return r"""
(function () {
  function isTypingTarget(el) {
    if (!el) return false;
    var tag = (el.tagName || "").toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select") return true;
    if (el.isContentEditable) return true;
    return false;
  }
  var rows = Array.prototype.slice.call(document.querySelectorAll("tr[data-clank-href]"));
  var selected = 0;
  function paint() {
    rows.forEach(function (row, idx) {
      if (idx === selected) row.classList.add("selected");
      else row.classList.remove("selected");
    });
    if (rows[selected]) rows[selected].setAttribute("aria-selected", "true");
  }
  if (rows.length) paint();
  var help = document.getElementById("help");
  var command = document.getElementById("command-bar");
  var awaitingG = false;
  document.addEventListener("keydown", function (ev) {
    if (ev.ctrlKey || ev.altKey || ev.metaKey) return;
    if (isTypingTarget(ev.target) && ev.key !== "Escape") return;
    if (ev.key === "/") {
      ev.preventDefault();
      if (command) command.focus();
      return;
    }
    if (ev.key === "?") {
      ev.preventDefault();
      if (help) help.classList.toggle("open");
      return;
    }
    if (ev.key === "Escape") {
      if (help && help.classList.contains("open")) help.classList.remove("open");
      else if (command && document.activeElement === command) command.blur();
      return;
    }
    if (ev.key === "r") {
      ev.preventDefault();
      window.location.reload();
      return;
    }
    if (ev.key === "g") { awaitingG = true; return; }
    if (awaitingG) {
      awaitingG = false;
      if (ev.key === "f") { window.location.href = "/"; return; }
      if (ev.key === "a") { window.location.href = "/attention"; return; }
      if (ev.key === "s") { window.location.href = "/sessions"; return; }
    }
    if (!rows.length) return;
    if (ev.key === "j") {
      ev.preventDefault();
      selected = Math.min(rows.length - 1, selected + 1);
      paint();
      rows[selected].scrollIntoView({ block: "nearest" });
    } else if (ev.key === "k") {
      ev.preventDefault();
      selected = Math.max(0, selected - 1);
      paint();
      rows[selected].scrollIntoView({ block: "nearest" });
    } else if (ev.key === "Enter") {
      var href = rows[selected] && rows[selected].getAttribute("data-clank-href");
      if (href) window.location.href = href;
    }
  });
})();
"""


def page(title: str, body: str, *, status: dict[str, Any] | None = None) -> str:
    bar = _status_bar(status or {})
    help_items = "".join(
        f"<div><kbd>{html.escape(key)}</kbd> {html.escape(desc)}</div>"
        for key, desc in HELP_BINDINGS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="referrer" content="no-referrer"/>
  <title>{html.escape(title)}</title>
  <style>{_css()}</style>
</head>
<body>
  {bar}
  {body}
  <div id="help" role="dialog" aria-label="keyboard shortcuts">{help_items}</div>
  <script>{_js()}</script>
</body>
</html>
"""


def _status_bar(status: dict[str, Any]) -> str:
    mode = status.get("mode_label") or "SNAPSHOT"
    seq = _unknown(status.get("max_ledger_seq"))
    generated = _unknown(status.get("generated_at"))
    view = html.escape(status.get("view") or "fleet")
    q = html.escape(status.get("query") or "")
    extra = ""
    if mode == "LIVE LOCAL" or "LIVE LOCAL" in mode:
        extra += mark("LIVE LOCAL")
    if "GITHUB" in mode:
        extra += mark("GITHUB NETWORK")
    if mode == "SNAPSHOT":
        extra += mark("SNAPSHOT")
    clanks = status.get("registered_clanks")
    active = status.get("active_missions")
    blocked = status.get("blocked_missions")
    return f"""
<div class="status">
  <strong>CLANKOPS TERMINAL BETA</strong>
  {extra}
  <span>LEDGER #{html.escape(str(seq))}</span>
  <span>{html.escape(str(clanks or 0))} CLANKS</span>
  <span>{html.escape(str(active or 0))} ACTIVE</span>
  <span>{html.escape(str(blocked or 0))} BLOCKED</span>
  <span class="muted">snapshot at {html.escape(str(generated))}</span>
  <span class="muted">view {view}{(' · q=' + q) if q else ''}</span>
</div>
<div class="nav status">
  <a href="/">fleet</a>
  <a href="/attention">attention</a>
  <a href="/sessions">sessions</a>
  <a href="/health">status</a>
  <span class="muted">read-only · GET/HEAD · no harvest · no GitHub unless ?github=1</span>
</div>
"""


def status_html(payload: dict[str, Any]) -> str:
    """Terminal process/read-path status. Not fleet or source operational health."""
    snap = payload.get("snapshot") or {}
    generated = _unknown(snap.get("generated_at"))
    status = {
        "mode_label": "SNAPSHOT",
        "max_ledger_seq": snap.get("max_ledger_seq"),
        "generated_at": snap.get("generated_at"),
        "view": "status",
    }
    rows = (
        ("mode", payload.get("mode") or "read-only"),
        ("Terminal version", payload.get("terminal") or "beta"),
        ("DB / read path", "readable" if payload.get("ok") else "unavailable"),
        ("connection", payload.get("connection") or "per-request"),
        ("ledger event count", snap.get("ledger_event_count")),
        ("max ledger seq", snap.get("max_ledger_seq")),
        ("snapshot generated time", generated),
    )
    cells = "".join(
        "<tr>"
        f"<th>{html.escape(str(label))}</th>"
        f"<td>{html.escape(_unknown(value))}</td>"
        "</tr>"
        for label, value in rows
    )
    body = f"""
<h1>TERMINAL STATUS</h1>
<p>This describes the ClankOps Terminal process/read path. It is not fleet/source operational health.</p>
<table class="grid">
  <thead><tr><th>field</th><th>evidence</th></tr></thead>
  <tbody>{cells}</tbody>
</table>
<p class="muted">Machine-readable copy: <a href="/api/health">/api/health</a>. No collectors, Git, GitHub, or Harvest run from this page.</p>
"""
    return page("Terminal status · ClankOps Terminal Beta", body, status=status)


def session_dossier_html(session: dict[str, Any]) -> str:
    """Open/closed Session evidence. Process EXITED is not a handoff."""
    process = session.get("managed_process") or {}
    status = process.get("status") or "UNKNOWN"
    sid = html.escape(session.get("session_id") or "")
    actor = html.escape(_unknown(session.get("actor")))
    launcher = html.escape(_unknown(session.get("launcher")))
    bits = [
        f"<div>{mark('SESSION')} {sid}</div>",
        f"<div>actor: {actor} {mark(str(session.get('actor') or 'unknown'))}</div>",
        f"<div>launcher: {launcher}</div>",
    ]
    if status == "EXITED":
        code = html.escape(str(process.get("exit_code")))
        age = html.escape(str(process.get("process_age") or "unknown"))
        bits.append(f"<div>process: {mark('EXITED')} (code {code}, {age} ago)</div>")
    elif status == "START_FAILED":
        bits.append(f"<div>process: {mark('START_FAILED')} (never started)</div>")
    else:
        bits.append(f"<div>process: {mark('UNKNOWN')}</div>")
    if session.get("open"):
        bits.append(
            f"<div>session: {mark('OPEN')} — handoff: {mark('MISSING')} — explicit handoff required</div>"
        )
    else:
        handoff = session.get("handoff") or process.get("handoff") or "UNKNOWN"
        bits.append(f"<div>session: {mark('CLOSED')}</div>")
        bits.append(f"<div>handoff: {mark(str(handoff))}</div>")
    if session.get("stale"):
        bits.append(f" {mark('stale')}")
    if session.get("anomaly"):
        bits.append(f" {mark('ANOMALY')}")
    return "".join(bits)


def command_bar(raw: str, *, action: str = "/", error: str | None = None) -> str:
    err = f'<div class="err" id="query-error">{html.escape(error)}</div>' if error else ""
    return f"""
<form class="command" method="get" action="{html.escape(action)}">
  <label for="command-bar">filter</label>
  <input id="command-bar" name="q" value="{html.escape(raw or "")}" placeholder="state:active attention:integrity oem" autocomplete="off"/>
  <button type="submit">apply</button>
</form>
{err}
"""


def fleet_html(home: dict[str, Any], *, query: str = "", error: str | None = None) -> str:
    summary = home.get("summary") or {}
    snap = home.get("snapshot") or {}
    mode = home.get("mode") or {}
    status = {
        "mode_label": mode.get("label") or "SNAPSHOT",
        "max_ledger_seq": snap.get("max_ledger_seq"),
        "generated_at": snap.get("generated_at"),
        "view": "fleet",
        "query": query,
        "registered_clanks": summary.get("registered_clanks"),
        "active_missions": summary.get("active_missions"),
        "blocked_missions": summary.get("blocked_missions"),
    }
    chips = [
        ("registered", summary.get("registered_clanks")),
        ("ACTIVE", summary.get("active_missions")),
        ("PAUSED", summary.get("paused_missions")),
        ("BLOCKED", summary.get("blocked_missions")),
        ("PLANNED", summary.get("planned_missions")),
        ("open Sessions", summary.get("open_sessions")),
        ("stale Sessions", summary.get("stale_sessions")),
        ("attention", summary.get("attention_count")),
        ("coverage", (home.get("attention") or {}).get("coverage")),
        ("integrity", summary.get("attention_integrity")),
        ("harvested", summary.get("harvest_observed")),
        ("never/no-path", summary.get("harvest_never")),
        ("harvest failures", summary.get("harvest_failed")),
        ("deploy surfaces", summary.get("deployment_surfaces")),
        ("CI artefacts", summary.get("ci_missions")),
    ]
    strip = "".join(
        f'<div><span class="muted">{html.escape(label)}</span><br><strong>{html.escape(str(value if value is not None else 0))}</strong></div>'
        for label, value in chips
    )
    cells = []
    for row in home.get("rows") or []:
        slug = html.escape(row.get("slug") or "")
        href = f"/clank/{slug}"
        att = row.get("attention_top") or {}
        harvest = row.get("harvest") or {}
        state = harvest.get("semantic_state") or {}
        harvest_head = short_head(state.get("head")) or "unknown"
        harvest_branch = state.get("branch") or ("HEAD" if state.get("detached") else "unknown")
        dirty = state.get("dirty")
        if dirty is True:
            tree = f"DIRTY({state.get('dirty_count') or 0})"
        elif dirty is False:
            tree = "CLEAN"
        else:
            tree = "UNKNOWN"
        rec_head = html.escape(_unknown(row.get("head_short")))
        rec_branch = html.escape(_unknown(row.get("branch")))
        process = row.get("process_status") or "UNKNOWN"
        handoff = row.get("handoff") or "UNKNOWN"
        if harvest.get("never_harvested"):
            git_cell = f'{badge("UNKNOWN")} never harvested<span class="second">{html.escape(_unknown(harvest.get("latest_result") or "NONE"))}</span>'
        else:
            git_cell = (
                f'{badge("LOCAL_GIT") if harvest.get("source") else badge("UNKNOWN")} '
                f"{html.escape(str(harvest_branch))} @ {html.escape(str(harvest_head))} {mark(tree)}"
                f'<span class="second">{html.escape(_unknown(harvest.get("state_observation_age")))} '
                f"{html.escape(_unknown(harvest.get('latest_result') or 'NONE'))}</span>"
            )
        if not row.get("head_short") and not row.get("branch"):
            rec_cell = f'{badge("REC")} none recorded<span class="second">{html.escape(_unknown(row.get("checkpoint_age")))}</span>'
        else:
            rec_cell = (
                f'{badge("REC")} {rec_branch} @ {rec_head}'
                f'<span class="second">{html.escape(_unknown(row.get("working_tree")))} {html.escape(_unknown(row.get("checkpoint_age")))}</span>'
            )
        next_text = row.get("next_action")
        next_cell = html.escape(next_text) if next_text else mark("UNKNOWN")
        cells.append(
            f'<tr data-clank-href="{href}" data-nav-row="1">'
            f'<td><a href="{href}">{slug}</a><span class="second">{html.escape(row.get("display_name") or slug)} {mark(row.get("lifecycle") or "UNKNOWN")}</span></td>'
            f'<td>{html.escape(_unknown(row.get("mission_display")))} {mark(row.get("mission_state") or "unknown")}<span class="second">{html.escape(_unknown(row.get("mission_objective")))}</span></td>'
            f'<td>{html.escape(str(row.get("open_session_count") or 0))} {html.escape(_unknown(row.get("actor")))}<span class="second">process {mark(process)} handoff {mark(handoff)}</span></td>'
            f'<td class="wrap">{next_cell}</td>'
            f"<td>{rec_cell}</td>"
            f"<td>{git_cell}</td>"
            f'<td>{mark(row.get("reconcile_status") or "unknown")}<span class="second">CI {html.escape(_unknown(row.get("ci_summary")))} deploy {html.escape(str(row.get("deployment_count") or 0))}</span></td>'
            f'<td><a href="/attention?clank={slug}">{html.escape(str(row.get("attention_count") or 0))}</a> {html.escape(att.get("class") or "")}<span class="second">{html.escape(att.get("reason_code") or "none")}</span></td>'
            f'<td>{html.escape(_unknown(row.get("age_since_last_event")))}<span class="second">seq {html.escape(_unknown(row.get("last_event_seq")))}</span></td>'
            "</tr>"
        )
    table = (
        '<div class="grid-wrap"><table class="grid" id="fleet-grid"><thead><tr>'
        "<th>CLANK</th><th>MISSION</th><th>SESSION</th><th>NEXT</th><th>CHECKPOINT</th>"
        "<th>LOCAL GIT</th><th>EVIDENCE</th><th>ATTENTION</th><th>RECENT</th>"
        "</tr></thead><tbody>"
        + "".join(cells)
        + "</tbody></table></div>"
    )
    note = (
        '<p class="muted">Default is a snapshot of recorded ClankOps state. '
        "Harvested LOCAL_GIT is not live. Add ?live=1 for Foundation 3 local inspect, "
        "?github=1 for GitHub network. Refresh harvest with <code>clankctl harvest local-git</code> — "
        "the browser will not run it.</p>"
    )
    att_report = home.get("attention") or {}
    att_items = att_report.get("items") or []
    att_summary = (
        f'<p class="muted">attention {html.escape(str(summary.get("attention_count") or 0))} · '
        f'integrity {html.escape(str(summary.get("attention_integrity") or 0))} · '
        f'coverage {html.escape(_unknown(att_report.get("coverage")))} · '
        f'live-local checks {html.escape(_unknown(att_report.get("live_local_checks")))} · '
        f'<a href="/attention">open queue</a>'
        + (
            f' · top {html.escape(str((att_items[0] or {}).get("reason_code") or ""))}'
            if att_items
            else ""
        )
        + (
            " · use ?live=1 to evaluate Foundation 7 live-local reasons"
            if att_report.get("live_local_checks") == "UNOBSERVABLE IN SNAPSHOT"
            else ""
        )
        + "</p>"
    )
    return page(
        "ClankOps Terminal Beta",
        command_bar(query, error=error)
        + f'<div class="strip">{strip}</div>'
        + note
        + att_summary
        + table
        + _attention_list(home.get("attention") or {}),
        status=status,
    )


def _attention_coverage(attention: dict[str, Any]) -> str:
    items = attention.get("items") or []
    coverage = attention.get("coverage") or "UNKNOWN"
    checks = attention.get("live_local_checks") or "UNKNOWN"
    bits = [
        f"attention items: {len(items)}",
        f"coverage: {html.escape(str(coverage))}",
        f"live-local-dependent checks: {html.escape(str(checks))}",
        (
            "live-local observable: "
            f"{html.escape(str(attention.get('live_local_observable_count', 'unknown')))}"
        ),
        (
            "live-local unobservable: "
            f"{html.escape(str(attention.get('live_local_unobservable_count', 'unknown')))}"
        ),
    ]
    for row in attention.get("live_local_unobservable") or []:
        bits.append(
            f"{html.escape(str(row.get('clank') or 'unknown'))}: "
            f"{html.escape(str(row.get('error') or 'unobservable'))}"
        )
    if checks == "UNOBSERVABLE IN SNAPSHOT":
        bits.append("use ?live=1 to evaluate Foundation 7 live-local reasons")
    return f'<p class="muted" id="attention-coverage">{" · ".join(bits)}</p>'


def _attention_list(attention: dict[str, Any], *, heading: str = "ATTENTION") -> str:
    items = attention.get("items") or []
    threshold = html.escape(_unknown(attention.get("threshold")))
    source = html.escape(_unknown(attention.get("threshold_source")))
    header = (
        f"<h2>{html.escape(heading)}</h2>"
        f'<p class="muted">Derived; writes zero events. threshold {threshold} ({source}). '
        "Old is not wrong. A different deployed SHA is not failure. "
        f"{mark(attention.get('class_integrity_mark') or '■')} integrity "
        f"{mark(attention.get('class_info_mark') or '◇')} informational "
        f"{mark(attention.get('class_age_mark') or '○')} age</p>"
        + _attention_coverage(attention)
    )
    if not items:
        if attention.get("coverage") == "PARTIAL":
            return (
                header
                + '<p class="muted">zero items here is not an unqualified none; '
                "the live-local-dependent reason set was not fully evaluable</p>"
            )
        return header + '<p class="muted">none derived</p>'
    blocks = []
    for item in items:
        klass = item.get("class") or "unknown"
        mark_ch = html.escape(CLASS_MARK.get(klass, "·"))
        slug = html.escape(item.get("clank") or "")
        evidence = item.get("evidence") or {}
        bits = []
        for key, value in evidence.items():
            if value in (None, "", [], {}):
                continue
            bits.append(f"{html.escape(str(key))}={html.escape(str(value))}")
        blocks.append(
            f'<div class="attention-item attention-{html.escape(klass)}" id="attention-{html.escape(item.get("reason_code") or "")}">'
            f'<div aria-hidden="true">{mark_ch}</div><div>'
            f'<div><a href="/clank/{slug}">{html.escape(item.get("clank_name") or slug)}</a> {mark(item.get("reason_code") or "unknown")}</div>'
            f'<div>{html.escape(item.get("reason") or "")}</div>'
            f'<div class="muted">mission {html.escape(_unknown(item.get("mission")))} · '
            f'age {html.escape(_unknown(item.get("age")))} · source {html.escape(_unknown(item.get("source")))} · '
            f'class {html.escape(klass)}</div>'
            f'<div class="muted">{" · ".join(bits)}</div>'
            f'<div class="muted">action {html.escape(item.get("suggested_action") or "")}</div>'
            f'<div class="muted">provenance {html.escape(", ".join(str(p) for p in (item.get("provenance") or [])) or "unknown")}'
            f' threshold {html.escape(_unknown(item.get("threshold")))} ({html.escape(_unknown(item.get("threshold_source")))})</div>'
            "</div></div>"
        )
    return header + "".join(blocks)


def attention_html(payload: dict[str, Any], *, query: str = "", error: str | None = None) -> str:
    snap = payload.get("snapshot") or {}
    status = {
        "mode_label": (payload.get("mode") or {}).get("label") or "SNAPSHOT",
        "max_ledger_seq": snap.get("max_ledger_seq"),
        "generated_at": snap.get("generated_at"),
        "view": "attention",
        "query": query,
        "registered_clanks": payload.get("registered_clanks"),
        "active_missions": payload.get("active_missions"),
        "blocked_missions": payload.get("blocked_missions"),
    }
    note = (
        '<p class="muted">Filters: <code>clank</code>, <code>class</code>, '
        "<code>reason</code> / <code>reason_code</code> query parameters. "
        "The fleet command bar is not used here.</p>"
    )
    return page(
        "Attention · ClankOps Terminal Beta",
        note + _attention_list(payload, heading="ATTENTION QUEUE"),
        status=status,
    )


def sessions_html(payload: dict[str, Any]) -> str:
    snap = payload.get("snapshot") or {}
    status = {
        "mode_label": (payload.get("mode") or {}).get("label") or "SNAPSHOT",
        "max_ledger_seq": snap.get("max_ledger_seq"),
        "generated_at": snap.get("generated_at"),
        "view": "sessions",
        "registered_clanks": payload.get("registered_clanks"),
        "active_missions": payload.get("active_missions"),
        "blocked_missions": payload.get("blocked_missions"),
    }
    rows = []
    for row in payload.get("sessions") or []:
        process = row.get("managed_process") or {}
        status_p = process.get("status") or "UNKNOWN"
        handoff = process.get("handoff") or row.get("handoff") or "UNKNOWN"
        session_state = "OPEN" if row.get("open") else "CLOSED"
        rows.append(
            "<tr>"
            f'<td><a href="/clank/{html.escape(row.get("clank_slug") or "")}">{html.escape(_unknown(row.get("clank_slug")))}</a></td>'
            f'<td>{html.escape(_unknown(row.get("mission_display")))}</td>'
            f'<td>{html.escape(_unknown(row.get("session_id")))}</td>'
            f'<td>{html.escape(_unknown(row.get("actor")))}</td>'
            f'<td>{html.escape(_unknown(row.get("launcher")))}</td>'
            f'<td>{html.escape(_unknown(row.get("started_utc")))}<span class="second">{html.escape(_unknown(row.get("age")))}</span></td>'
            f'<td>{mark(session_state)} {mark("stale") if row.get("stale") else ""}</td>'
            f'<td>{mark(status_p)} code {html.escape(_unknown(process.get("exit_code")))}</td>'
            f'<td>{mark(handoff)}</td>'
            "</tr>"
        )
    table = (
        '<p class="muted">Process EXITED with Session OPEN is not a handoff. '
        "Session CLOSED without HANDOFF_RECORDED stays UNKNOWN. Never collapse to finished.</p>"
        '<table class="grid"><thead><tr>'
        "<th>Clank</th><th>Mission</th><th>Session</th><th>actor</th><th>launcher</th>"
        "<th>started / age</th><th>session</th><th>process</th><th>handoff</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return page("Sessions · ClankOps Terminal Beta", table, status=status)


def _harvest_block(view: dict[str, Any]) -> str:
    latest = str(view.get("latest_result") or "")
    state = view.get("semantic_state") or {}
    failed = latest in {
        "TIMEOUT",
        "ERROR",
        "PATH_MISSING",
        "NOT_A_GIT_REPOSITORY",
        "STALE_OBSERVATION",
        "AMBIGUOUS_CANONICAL_PATH",
    }
    source = view.get("source") or "UNKNOWN"
    status = "NONE"
    if state:
        status = "OBSERVED"
    if latest in {"TIMEOUT", "ERROR", "PATH_MISSING", "NOT_A_GIT_REPOSITORY", "STALE_OBSERVATION", "AMBIGUOUS_CANONICAL_PATH"}:
        status = latest or "NONE"
    elif latest in {"OBSERVED_CHANGED", "OBSERVED_UNCHANGED"}:
        status = "OBSERVED"
    head = (state.get("head") or "")[:12] or "unknown"
    branch = state.get("branch") or ("HEAD" if state.get("detached") else "unknown")
    if state.get("dirty") is True:
        tree = f"DIRTY ({state.get('dirty_count') or 0} paths)"
    elif state.get("dirty") is False:
        tree = "CLEAN"
    else:
        tree = "UNKNOWN"
    worktrees = state.get("worktrees") or []
    wt_text = ", ".join(
        f"{wt.get('path') or wt.get('name') or 'unknown'} {wt.get('branch') or ''}".strip()
        for wt in worktrees
        if isinstance(wt, dict)
    ) or str(len(worktrees))
    last_good = ""
    if failed and state:
        last_good = (
            f"<tr><th>LAST KNOWN GOOD STATE</th><td>{html.escape(str(branch))} / "
            f"{html.escape(str(head))} / {html.escape(tree)}</td></tr>"
        )
    latest_line = (
        f"<tr><th>LATEST CHECK</th><td>{mark(latest or 'NONE')} {mark(status)} "
        f"{html.escape(view.get('latest_result_detail') or '')}</td></tr>"
    )
    hint = (
        '<p class="muted">Refresh with: <code>clankctl harvest local-git '
        + html.escape(str(view.get("slug") or ""))
        + "</code>. The browser does not execute it.</p>"
    )
    never = "<tr><th>harvest</th><td>never harvested</td></tr>" if view.get("never_harvested") else ""
    return f"""
<h2>LOCAL GIT</h2>
<h2>HARVESTED LOCAL GIT</h2>
<p class="muted">Harvested local checkout facts. This is not live inspect, GitHub, CI, deployment, or health.</p>
{hint}
<table>
  <tr><th>status</th><td>{mark(status)}</td></tr>
  <tr><th>source</th><td>{mark(source)}</td></tr>
  <tr><th>checkout</th><td>{html.escape(_unknown(view.get("checkout_path")))}</td></tr>
  <tr><th>branch / HEAD / tree</th><td>{html.escape(str(branch))} / {html.escape(str(head))} / {mark(tree.split()[0] if tree else "UNKNOWN")} {html.escape(tree)}</td></tr>
  <tr><th>worktrees</th><td>{html.escape(wt_text)}</td></tr>
  <tr><th>state age</th><td>{html.escape(_unknown(view.get("state_observation_age")))}</td></tr>
  <tr><th>last checked</th><td>{html.escape(_unknown(view.get("check_age")))}</td></tr>
  {never}
  {last_good}
  {latest_line}
</table>
"""


def _reconcile_section(payload: dict[str, Any]) -> str:
    rec = payload.get("reconcile") or {}
    rec_rows = []
    for item in rec.get("drift") or []:
        rec_rows.append(
            "<tr>"
            f"<td>{mark(str(item.get('kind') or 'mismatch'))}</td>"
            f"<td>{html.escape(_unknown(item.get('field')))}</td>"
            f"<td>{badge(str(item.get('source') or ''))}</td>"
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
            f'<p class="muted">{html.escape(caption)}</p>'
            "<table><thead><tr><th>kind</th><th>field</th><th>source</th><th>recorded</th><th>observed</th></tr></thead><tbody>"
            + "".join(rec_rows)
            + "</tbody></table>"
        )
    else:
        result_block = f'<p class="muted">{html.escape(caption)}</p>'
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
    context_text = ", ".join(
        f"{ctx.get('context') or 'status'} {ctx.get('state') or 'unknown'}"
        for ctx in (checks.get("contexts") or [])
    ) or "no status contexts"
    return f"""
<h2>RECONCILIATION</h2>
<p class="muted">Independent LOCAL_GIT / GITHUB observation vs recorded claims. Observer success is not corroboration. Empty CI is none only when check-runs and status contexts were both observed empty. Unavailable status evidence is unknown, never none. Success requires completed check-runs. History is not rewritten.</p>
<table>
  <tr><th>status</th><td>{mark(_unknown(status))} {html.escape(caption)}</td></tr>
  <tr><th>recorded claim</th><td>{html.escape(_unknown(recorded.get('branch')))} / {html.escape(_unknown(recorded.get('head')))} / {html.escape(_unknown(recorded.get('working_tree')))} [{html.escape(_unknown(recorded.get('event_source')))}]</td></tr>
  <tr><th>LOCAL_GIT</th><td>{mark('LOCAL_GIT')} {html.escape(_unknown(local.get('branch')))} / {html.escape(_unknown(local.get('head')))} / {html.escape(_unknown(local.get('working_tree')))} {html.escape(local.get('error') or '')}</td></tr>
  <tr><th>GITHUB</th><td>{mark('GITHUB')} default {html.escape(_unknown(github.get('default_branch') if github else None))} / {html.escape(_unknown(github.get('default_branch_head') if github else None))} PRs {html.escape(pr_text)} {html.escape(str(github_err or ''))}</td></tr>
  <tr><th>CI checks</th><td>{mark('GITHUB')} {html.escape(_unknown(checks.get('state')))} sha {html.escape(_unknown(checks.get('sha')))} {html.escape(check_run_text)}; {html.escape(context_text)} {html.escape(_unknown(checks.get('error')))}</td></tr>
</table>
{result_block}
"""


def _plane_cell(
    value: Any,
    *,
    plane: str,
    badge_name: str,
    requested: bool = True,
    observed: bool = False,
) -> str:
    if not requested:
        inner = "UNKNOWN / NOT REQUESTED"
    elif not observed:
        inner = "UNKNOWN"
    else:
        inner = f"{badge(badge_name)} {html.escape(_unknown(value))}"
    return f'<td data-plane="{html.escape(plane)}">{inner}</td>'


def _evidence_matrix(payload: dict[str, Any]) -> str:
    now = payload.get("now") or {}
    harvest = payload.get("local_git_harvest") or {}
    rec_state = harvest.get("semantic_state") or {}
    rec = payload.get("reconcile") or {}
    local = rec.get("observed_local") or {}
    github = rec.get("observed_github") or {}
    ci = payload.get("ci") or {}
    deploys = payload.get("deployments") or []
    deploy_sha = ", ".join(
        f"{d.get('surface_id')}={(d.get('sha_short') or (d.get('deployed_sha') or '')[:7] or 'unknown')}"
        for d in deploys
    ) or "UNKNOWN"
    mode = payload.get("mode")
    if mode is not None:
        live_on = bool(mode.get("live_local"))
        gh_on = bool(mode.get("github"))
    else:
        live_on = True
        gh_on = rec.get("observed_github") is not None
    harvested_observed = bool(harvest.get("source")) and not harvest.get("never_harvested")
    ci_observed = bool(ci)
    deploy_observed = bool(deploys)
    rec_observed = bool(now.get("branch") or now.get("head") or now.get("working_tree"))
    rows = [
        (
            "branch",
            now.get("branch"),
            rec_state.get("branch"),
            local.get("branch") if live_on else None,
            github.get("default_branch") if gh_on else None,
            None,
            None,
        ),
        (
            "HEAD",
            now.get("head"),
            rec_state.get("head"),
            local.get("head") if live_on else None,
            github.get("default_branch_head") if gh_on else None,
            ci.get("sha"),
            deploy_sha if deploys else None,
        ),
        (
            "working tree",
            now.get("working_tree"),
            "DIRTY" if rec_state.get("dirty") else ("CLEAN" if rec_state.get("dirty") is False else None),
            local.get("working_tree") if live_on else None,
            None,
            None,
            None,
        ),
        (
            "CI SHA/state",
            None,
            None,
            None,
            None,
            f"{ci.get('sha') or 'UNKNOWN'} {ci.get('state') or ''}".strip() if ci else None,
            None,
        ),
        (
            "deployment SHA",
            None,
            None,
            None,
            None,
            None,
            deploy_sha if deploys else None,
        ),
        (
            "runtime",
            None,
            None,
            None,
            None,
            None,
            ", ".join(
                f"{d.get('surface_id')} running={d.get('running') or 'unknown'}"
                for d in deploys
            )
            if deploys
            else None,
        ),
    ]
    body = []
    for field, recorded, harvested, live, gh, ci_v, dep in rows:
        body.append(
            "<tr>"
            f"<td>{html.escape(field)}</td>"
            + _plane_cell(
                recorded,
                plane="recorded",
                badge_name="REC",
                observed=rec_observed and recorded is not None,
            )
            + _plane_cell(
                harvested,
                plane="harvested",
                badge_name="LOCAL_GIT",
                observed=harvested_observed and harvested is not None,
            )
            + _plane_cell(
                live,
                plane="live",
                badge_name="LIVE LOCAL",
                requested=live_on,
                observed=live_on and live is not None,
            )
            + _plane_cell(
                gh,
                plane="github",
                badge_name="GITHUB",
                requested=gh_on,
                observed=gh_on and gh is not None,
            )
            + _plane_cell(
                ci_v,
                plane="ci",
                badge_name="CI",
                observed=ci_observed and ci_v is not None,
            )
            + _plane_cell(
                dep,
                plane="deploy",
                badge_name="DEPLOY",
                observed=deploy_observed and dep is not None,
            )
            + "</tr>"
        )
    return (
        "<h2>EVIDENCE MATRIX</h2>"
        '<p class="muted">No best-truth column. UNKNOWN stays UNKNOWN. Planes are not ranked. '
        "Empty cells are unobserved; source badges appear only after evidence exists.</p>"
        '<table class="matrix"><thead><tr><th>field</th><th>RECORDED</th><th>HARVESTED LOCAL</th>'
        "<th>LIVE LOCAL</th><th>GITHUB</th><th>CI</th><th>DEPLOYMENT</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def dossier_html(payload: dict[str, Any]) -> str:
    ident = payload.get("identity") or {}
    now = payload.get("now") or {}
    snap = payload.get("snapshot") or {}
    mode = payload.get("mode") or {}
    status = {
        "mode_label": mode.get("label") or "SNAPSHOT",
        "max_ledger_seq": snap.get("max_ledger_seq"),
        "generated_at": snap.get("generated_at"),
        "view": f"clank:{ident.get('slug')}",
        "registered_clanks": 1,
        "active_missions": 1 if now.get("mission_state") == "ACTIVE" else 0,
        "blocked_missions": 1 if now.get("mission_state") == "BLOCKED" else 0,
    }
    sessions = now.get("open_sessions") or []
    session_html = mark("none") if not sessions else "".join(session_dossier_html(s) for s in sessions)
    harvest = payload.get("local_git_harvest") or {}
    harvest["slug"] = ident.get("slug")
    timeline_rows = []
    events = list(payload.get("timeline") or [])
    display = list(reversed(events))
    for event in display:
        labels = event.get("provenance") or [event.get("source")]
        src = " ".join(
            f"{badge(str(label))} {html.escape(str(label))}" for label in labels if label
        )
        timeline_rows.append(
            "<tr>"
            f"<td>{html.escape(str(event.get('ledger_seq')))}</td>"
            f"<td>{html.escape(_unknown(event.get('ts_utc')))}</td>"
            f"<td>{html.escape(_unknown(event.get('event_type')))}</td>"
            f"<td>{html.escape(_unknown(event.get('actor')))}</td>"
            f"<td>{src}</td>"
            f"<td>{html.escape(_unknown(event.get('session_id')))}</td>"
            f"<td class='wrap'>{html.escape(_unknown(event.get('summary')))}</td>"
            "</tr>"
        )
    missions = []
    for mission in payload.get("missions") or []:
        recon = mission.get("reconciliation")
        recon_bit = (
            f" {mark('RECONCILED')} {html.escape(recon.get('from_state') or '')} -> "
            f"{html.escape(recon.get('to_state') or '')}"
            if recon
            else ""
        )
        ci = mission.get("ci") or {}
        ci_bit = (
            f" CI {html.escape(str(ci.get('state') or 'UNKNOWN'))} "
            f"{html.escape(short_head(ci.get('sha')) or 'unknown')} "
            f"{html.escape(_unknown(ci.get('created_utc')))}"
            if ci
            else " CI UNKNOWN"
        )
        cp = mission.get("checkpoint") or {}
        cp_bit = (
            f"{html.escape(_unknown(cp.get('branch')))} @ "
            f"{html.escape(short_head(cp.get('head')) or 'unknown')} "
            f"{html.escape(_unknown(cp.get('working_tree')))} "
            f"stopped {html.escape(_unknown(cp.get('current_work') or cp.get('next_action')))}"
            if cp
            else "checkpoint UNKNOWN"
        )
        missions.append(
            "<tr>"
            f"<td>{html.escape(mission.get('display_id') or '')}</td>"
            f"<td>{mark(mission.get('state') or 'unknown')}</td>"
            f"<td class='wrap'>{html.escape(mission.get('objective') or '')}</td>"
            f"<td>{html.escape(_unknown(mission.get('created_utc')))}</td>"
            f"<td>{html.escape(_unknown(mission.get('next_action')))}</td>"
            f"<td>{html.escape(str(mission.get('session_count') or ''))}{recon_bit}</td>"
            f"<td class='wrap'>{ci_bit}</td>"
            f"<td class='wrap'>{cp_bit}</td>"
            "</tr>"
        )
    deploys = []
    for dep in payload.get("deployments") or []:
        deploys.append(
            "<tr>"
            f"<td>{html.escape(_unknown(dep.get('surface_id')))}</td>"
            f"<td>{mark(dep.get('environment') or 'unknown')}</td>"
            f"<td>{html.escape(_unknown(dep.get('host_identity')))}</td>"
            f"<td>{html.escape(_unknown(dep.get('sha_short') or (dep.get('deployed_sha') or '')[:7]))}</td>"
            f"<td>{mark(dep.get('running') or 'unknown')}</td>"
            f"<td>{html.escape(_unknown(dep.get('scheduler')))}</td>"
            f"<td>{html.escape(_unknown(dep.get('collection_authority')))} / {html.escape(_unknown(dep.get('notification_authority')))} collection: {html.escape(_unknown(dep.get('collection_authority')))} notification: {html.escape(_unknown(dep.get('notification_authority')))}</td>"
            f"<td>{html.escape(_unknown(dep.get('observed_at') or dep.get('age')))} {badge(str(dep.get('source') or 'UNKNOWN'))} {html.escape(_unknown(dep.get('observed_how')))}</td>"
            "</tr>"
        )
    tasks_done = [t for t in (payload.get("tasks") or []) if t.get("state") == "DONE"]
    tasks_open = [t for t in (payload.get("tasks") or []) if t.get("state") != "DONE"]
    identity = f"""
<h1>{html.escape(ident.get("display_name") or ident.get("slug") or "")}</h1>
<table>
  <tr><th>slug</th><td>{html.escape(_unknown(ident.get("slug")))}</td></tr>
  <tr><th>lifecycle</th><td>{mark(ident.get("lifecycle") or payload.get("lifecycle_state") or "UNKNOWN")}</td></tr>
</table>
<h2>NOW</h2>
<table>
  <tr><th>Mission</th><td>{html.escape(_unknown(now.get("mission_display")))} {mark(now.get("mission_state") or "unknown")}</td></tr>
  <tr><th>objective</th><td>{html.escape(_unknown(now.get("mission_objective")))}</td></tr>
  <tr><th>next action</th><td>{html.escape(_unknown(now.get("next_action")))}</td></tr>
  <tr><th>blockers</th><td>{html.escape("; ".join(b.get("description") or "" for b in (now.get("blockers") or [])) or "none recorded")}</td></tr>
  <tr><th>outstanding tasks</th><td>{html.escape("; ".join(t.get("title") or "" for t in (now.get("outstanding_tasks") or [])) or "none recorded")}</td></tr>
  <tr><th>Sessions</th><td>{session_html}</td></tr>
</table>
<h2>RECORDED CODE</h2>
<p>{badge("REC")} {html.escape(_unknown(now.get("branch")))} @ {html.escape(_unknown(now.get("head")))} {mark(now.get("working_tree") or "unknown")} tests {html.escape(_unknown(now.get("tests")))}</p>
"""
    recs = payload.get("reconciliations") or []
    recon_html = "<h2>MISSION RECONCILIATIONS</h2>"
    if not recs:
        recon_html += '<p class="muted">none recorded</p>'
    else:
        recon_html += "<ul>" + "".join(
            f"<li>{mark('RECONCILED')} {html.escape(r.get('mission_display') or '')} "
            f"{html.escape(str(r.get('from_state') or ''))} -> {html.escape(str(r.get('to_state') or ''))} "
            f"reason {html.escape(r.get('reason') or 'unknown')} "
            f"(reconciliation, not ordinary lifecycle)</li>"
            for r in recs
        ) + "</ul>"
    timeline = (
        "<h2>TIMELINE</h2>"
        '<p class="muted">newest first for scanning; ledger_seq remains canonical order. bounded.</p>'
        '<table class="grid"><thead><tr><th>seq</th><th>timestamp</th><th>event type</th>'
        "<th>actor</th><th>source</th><th>Session</th><th>summary</th></tr></thead><tbody>"
        + "".join(timeline_rows)
        + "</tbody></table>"
    )
    brief_pre = (
        '<details><summary class="muted">raw brief (input)</summary>'
        f"<pre>{html.escape(format_brief(payload.get('brief') or payload))}</pre></details>"
    )
    body = (
        identity
        + _harvest_block(harvest)
        + _reconcile_section(payload)
        + _evidence_matrix(payload)
        + _attention_list(payload.get("attention") or {}, heading="ATTENTION")
        + "<h2>MISSIONS</h2><h2>MISSION HISTORY</h2>"
        + '<table class="grid"><thead><tr><th>id</th><th>state</th><th>objective</th>'
        "<th>created</th><th>next</th><th>sessions / recon</th><th>CI</th><th>checkpoint</th>"
        "</tr></thead><tbody>"
        + "".join(missions)
        + "</tbody></table>"
        + recon_html
        + timeline
        + "<h2>FEATURES</h2><ul>"
        + "".join(
            f"<li>{mark(f.get('state') or 'unknown')} {html.escape(f.get('name') or '')}</li>"
            for f in (payload.get("features") or [])
        )
        + ("<li class='muted'>none recorded</li>" if not payload.get("features") else "")
        + "</ul>"
        + f"<h2>TASKS</h2><p>{mark('outstanding')} {len(tasks_open)} · {mark('done')} {len(tasks_done)}</p><ul>"
        + "".join(
            f"<li>{mark(t.get('state') or 'unknown')} {html.escape(t.get('title') or '')}</li>"
            for t in (payload.get("tasks") or [])
        )
        + ("<li class='muted'>none recorded</li>" if not payload.get("tasks") else "")
        + "</ul>"
        + "<h2>DECISIONS</h2><ul>"
        + "".join(f"<li>{html.escape(d.get('statement') or '')}</li>" for d in (payload.get("decisions") or []))
        + ("<li class='muted'>none recorded</li>" if not payload.get("decisions") else "")
        + "</ul>"
        + "<h2>ARTEFACTS</h2><h2>ARTEFACTS / CI</h2><ul>"
        + "".join(
            f"<li>{badge(str(a.get('source') or 'UNKNOWN'))} {html.escape(a.get('kind') or '')} "
            f"{html.escape(a.get('title') or a.get('ref') or '')}</li>"
            for a in (payload.get("artifacts") or [])
        )
        + ("<li class='muted'>none recorded</li>" if not payload.get("artifacts") else "")
        + "</ul>"
        + "<h2>DEPLOYMENTS</h2>"
        + (
            '<table class="grid"><thead><tr><th>surface</th><th>env</th><th>host</th><th>SHA</th>'
            "<th>running</th><th>scheduler</th><th>authority</th><th>observed</th></tr></thead><tbody>"
            + "".join(deploys)
            + "</tbody></table>"
            if deploys
            else '<p class="muted">none recorded</p>'
        )
        + brief_pre
    )
    return page(f"{ident.get('slug')} · ClankOps Terminal Beta", body, status=status)
