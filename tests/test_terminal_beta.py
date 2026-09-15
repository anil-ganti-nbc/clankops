"""Terminal Beta: Fleet Command Centre regressions."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from clankops.attention import REASON_DIRTY_WITHOUT_OPEN_SESSION
from clankops.ci import CI_ARTIFACT_KIND, latest_mission_ci
from clankops.cli import main
from clankops.clock import FrozenClock, TickingClock
from clankops.deployment import capture_deployment
from clankops.enums import EventSource, EventType
from clankops.errors import ValidationError
from clankops.events import list_events, list_recent_events
from clankops.harvest import harvest_local_git
from clankops.readmodel import clank_timeline, ledger_fingerprint
from clankops.store import open_readonly_store, open_store
from clankops.terminal import assert_bind_allowed, dispatch, serve
from clankops.terminal_query import QueryError, apply_filters, parse_filter
from clankops.terminal_views import (
    HELP_BINDINGS,
    _count,
    _js,
    _status_bar,
    attention_html,
    dossier_html,
    fleet_html,
    sessions_html,
    status_html,
)

from test_fleet_harvest1 import SECRET_FILE, SECRET_PASSWORD, SECRET_QUERY, SECRET_TOKEN, _init_repo, _git
from test_foundation2 import T0, _census, _http, running_terminal
from test_foundation3 import CANON, HEAD, _local
from test_foundation6 import HETZNER_SURFACE, NAS_SURFACE, _hetzner, _nas
from test_foundation10 import _launch, _paused

HOSTILE = '<script>alert(1)</script> onerror= " \' </style> & amp'
WEBHOOK = "https://hooks.slack.com/services/T000/B000/XXXX"
SECRETS = (SECRET_PASSWORD, SECRET_TOKEN, SECRET_QUERY, SECRET_FILE, WEBHOOK)


def _seed_fleet(store, tmp_path: Path) -> dict:
    repo = _init_repo(tmp_path / "oem")
    store.register_clank(
        "oem-radar",
        display_name="OEM Radar " + HOSTILE,
        local_path=str(repo),
        remotes=["https://github.com/anil-ganti-nbc/oem-radar.git"],
    )
    oem = store.start_mission("oem-radar", "handheld adapters")
    store.record_checkpoint(
        oem["display_id"],
        branch="recorded-branch",
        head="recaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        working_tree="clean",
        next_action="land Terminal Beta",
        tests="python -m pytest: not run",
    )
    store.add_task(oem["display_id"], "write adapters")
    store.add_feature("oem-radar", "handheld coverage")
    store.add_decision(oem["display_id"], "Ship adapters before dashboard")
    harvest_local_git(store, "oem-radar")
    (repo / "wip.txt").write_text("dirty\n", encoding="utf-8")
    harvest_local_git(store, "oem-radar")

    store.register_clank("watch-clank", display_name="Watch Clank")
    watch = store.start_mission("watch-clank", "keep collecting")
    store.pause_mission(watch["display_id"])

    store.register_clank("blocked-clank", display_name="Blocked")
    blocked = store.start_mission("blocked-clank", "waiting on cookies")
    store.block_mission(blocked["display_id"])

    store.register_clank("nopath-clank", display_name="No Path")
    store.start_mission("nopath-clank", "no checkout")

    store.register_clank("clankops", display_name="ClankOps")
    cops = store.start_mission("clankops", "Terminal Beta: Fleet Command Centre")
    store.record_checkpoint(cops["display_id"], next_action="operator review of Terminal Beta PR")
    return {"oem": oem, "watch": watch, "blocked": blocked, "cops": cops, "repo": repo}


def _blob(*parts: object) -> str:
    return "\n".join(str(part) for part in parts)


def _header(html: str) -> str:
    return html.split('<div class="nav status">', 1)[0]


def test_parse_filter_grammar_and_and_semantics() -> None:
    parsed = parse_filter("state:active attention:integrity oem")
    assert parsed["filters"]["state"] == ["active"]
    assert parsed["filters"]["attention"] == ["integrity"]
    assert parsed["terms"] == ["oem"]
    with pytest.raises(QueryError, match="unknown filter key"):
        parse_filter("colour:red")
    with pytest.raises(QueryError, match="invalid filter value"):
        parse_filter("state:actvie")
    rows = [
        {
            "slug": "oem-radar",
            "display_name": "OEM Radar",
            "mission_display": "COPS-000001",
            "mission_state": "ACTIVE",
            "mission_objective": "handheld",
            "next_action": "land it",
            "open_session_count": 1,
            "stale_session": False,
            "attention_count": 2,
            "attention_classes": ["integrity"],
            "harvest_dirty": True,
            "harvest_result": "OBSERVED_CHANGED",
            "harvest_never": False,
            "task_titles": ["write adapters"],
            "feature_names": [],
            "decision_statements": [],
        },
        {
            "slug": "watch-clank",
            "display_name": "Watch",
            "mission_display": "COPS-000002",
            "mission_state": "PAUSED",
            "open_session_count": 0,
            "attention_count": 0,
            "attention_classes": [],
            "harvest_dirty": False,
            "harvest_result": None,
            "harvest_never": True,
        },
    ]
    assert [r["slug"] for r in apply_filters(rows, parse_filter("state:active"))] == ["oem-radar"]
    assert [r["slug"] for r in apply_filters(rows, parse_filter("state:paused"))] == ["watch-clank"]
    assert apply_filters(rows, parse_filter("session:open"))[0]["slug"] == "oem-radar"
    assert apply_filters(rows, parse_filter("session:none"))[0]["slug"] == "watch-clank"
    assert apply_filters(rows, parse_filter("attention:any"))[0]["slug"] == "oem-radar"
    assert apply_filters(rows, parse_filter("attention:integrity"))[0]["slug"] == "oem-radar"
    assert apply_filters(rows, parse_filter("git:dirty"))[0]["slug"] == "oem-radar"
    assert apply_filters(rows, parse_filter("git:clean"))[0]["slug"] == "watch-clank"
    assert apply_filters(rows, parse_filter("harvest:observed"))[0]["slug"] == "oem-radar"
    assert apply_filters(rows, parse_filter("harvest:never"))[0]["slug"] == "watch-clank"
    assert apply_filters(rows, parse_filter("clank:oem-radar"))[0]["slug"] == "oem-radar"
    assert apply_filters(rows, parse_filter("mission:COPS-000001"))[0]["slug"] == "oem-radar"
    assert apply_filters(rows, parse_filter("handheld"))[0]["slug"] == "oem-radar"
    combo = apply_filters(rows, parse_filter("state:active git:dirty session:open"))
    assert [r["slug"] for r in combo] == ["oem-radar"]
    assert apply_filters(rows, parse_filter("state:active harvest:never")) == []
    injected = apply_filters(rows, parse_filter("OR 1=1; DROP TABLE events"))
    assert injected == []
    with pytest.raises(QueryError, match="invalid query"):
        parse_filter("' OR 1=1 --")


def test_required_html_and_json_routes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    github_calls: list[str] = []
    harvest_calls: list[str] = []
    monkeypatch.setattr("clankops.reconcile.inspect_github", lambda repo: github_calls.append(repo) or {"ok": False, "error": "blocked"})
    monkeypatch.setattr("clankops.githubinspect.inspect_github", lambda repo: github_calls.append(repo) or {"ok": False})
    real_harvest = harvest_local_git

    def counting_harvest(*args, **kwargs):
        harvest_calls.append("run")
        return real_harvest(*args, **kwargs)

    monkeypatch.setattr("clankops.harvest.harvest_local_git", counting_harvest)
    store = open_store(tmp_path / "beta.db", actor="cursor", clock=FrozenClock(T0))
    ids = _seed_fleet(store, tmp_path)
    before = ledger_fingerprint(store)
    event_types = {e.event_type for e in list_events(store.conn)}
    harvest_before = sum(1 for e in list_events(store.conn) if e.event_type == EventType.LOCAL_GIT_HARVEST_COMPLETED)
    observed_before = sum(1 for e in list_events(store.conn) if e.event_type == EventType.LOCAL_GIT_STATE_OBSERVED)

    for path in ("/", "/fleet", "/attention", "/sessions", "/health", "/status", "/clank/oem-radar", "/clank/watch-clank", "/clank/clankops"):
        status, ctype, body = dispatch(store, "GET", path, now=T0, census=_census())
        assert status == 200, path
        text = body.decode("utf-8")
        assert "text/html" in ctype
        assert "CLANKOPS TERMINAL BETA" in text
        assert "[SNAPSHOT]" in text
        assert "snapshot at " in text
        assert "id=\"command-bar\"" in text or path in {"/sessions", "/health", "/status", "/attention"} or "command-bar" in text or path.startswith("/clank/")

    status, _, body = dispatch(store, "GET", "/", now=T0)
    home = body.decode("utf-8")
    assert "id=\"command-bar\"" in home
    assert "data-clank-href=\"/clank/oem-radar\"" in home
    assert "id=\"help\"" in home
    assert "[ACTIVE]" in home
    assert "[PAUSED]" in home
    assert "[BLOCKED]" in home
    assert "[REC]" in home
    assert "recorded-branch" in home
    assert "land Terminal Beta" in home
    assert "HEALTH 83" not in home
    assert "fleet health" not in home.lower()
    assert "Harvest now" not in home
    assert "complete Mission" not in home
    assert "health score" not in home.lower()
    assert "isTypingTarget" in _js()
    assert any(key == "/" for key, _ in HELP_BINDINGS)

    status, _, body = dispatch(store, "GET", "/attention?clank=oem-radar", now=T0)
    assert status == 200
    attn = body.decode("utf-8")
    assert "ATTENTION" in attn
    assert "■" in attn or "attention-integrity" in attn

    status, _, body = dispatch(store, "GET", "/sessions", now=T0)
    sess = body.decode("utf-8")
    assert "process" in sess
    assert "handoff" in sess
    assert "finished" not in sess.lower() or "Never collapse to finished" in sess

    status, _, body = dispatch(store, "GET", "/clank/oem-radar", now=T0)
    dossier = body.decode("utf-8")
    assert "<h2>NOW</h2>" in dossier
    assert "[REC]" in dossier
    assert "recorded-branch" in dossier
    assert "recaaaaaa" in dossier or "recaaa" in dossier
    assert "EVIDENCE MATRIX" in dossier
    assert "[LOCAL_GIT]" in dossier
    assert "&lt;script&gt;" in dossier
    assert "<script>alert" not in dossier
    assert "ledger_seq" in dossier or ">seq<" in dossier
    assert "bounded" in dossier

    status, _, body = dispatch(store, "GET", "/api/fleet", now=T0)
    fleet = json.loads(body.decode("utf-8"))
    assert "oem-radar" in {row["slug"] for row in fleet["rows"]}
    assert fleet["summary"]["registered_clanks"] >= 1
    assert fleet["snapshot"]["max_ledger_seq"] == before["max_ledger_seq"]
    assert fleet["mode"]["label"] == "SNAPSHOT"

    status, _, body = dispatch(store, "GET", "/api/clank/oem-radar", now=T0)
    clank_json = json.loads(body.decode("utf-8"))
    assert clank_json["identity"]["slug"] == "oem-radar"
    assert clank_json["snapshot"]["max_ledger_seq"] == before["max_ledger_seq"]
    assert clank_json["timeline_limit"] == 200
    seqs = [row["ledger_seq"] for row in clank_json["timeline"]]
    assert seqs == sorted(seqs)

    status, _, body = dispatch(store, "GET", "/api/attention", now=T0)
    assert status == 200
    status, _, body = dispatch(store, "GET", "/api/sessions", now=T0)
    assert status == 200

    for method in ("POST", "PUT", "PATCH", "DELETE"):
        for path in ("/", "/api/fleet", "/clank/oem-radar", "/attention", "/health", "/api/health"):
            status, _, _ = dispatch(store, method, path)
            assert status == 405

    status, _, _ = dispatch(store, "HEAD", "/")
    assert status == 200
    assert ledger_fingerprint(store) == before
    assert harvest_calls == []
    assert github_calls == []
    after_types = [e.event_type for e in list_events(store.conn)]
    assert sum(1 for t in after_types if t == EventType.LOCAL_GIT_HARVEST_COMPLETED) == harvest_before
    assert sum(1 for t in after_types if t == EventType.LOCAL_GIT_STATE_OBSERVED) == observed_before
    store.conn.close()


def test_live_and_github_flags_are_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "clankops.reconcile.inspect_github",
        lambda repo: calls.append(repo) or {
            "source": "GITHUB",
            "ok": True,
            "repo": repo,
            "default_branch": "main",
            "default_branch_head": "ghbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            "open_prs": [],
        },
    )
    store = open_store(tmp_path / "flags.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", remotes=["https://github.com/anil-ganti-nbc/oem-radar.git"])
    store.start_mission("oem-radar", "handheld")
    status, _, body = dispatch(store, "GET", "/", now=T0)
    assert "[SNAPSHOT]" in body.decode("utf-8")
    assert calls == []
    status, _, body = dispatch(store, "GET", "/?github=1", now=T0)
    assert "[GITHUB NETWORK]" in body.decode("utf-8")
    assert calls
    status, _, body = dispatch(
        store,
        "GET",
        "/clank/oem-radar?live=1",
        now=T0,
    )
    assert "[LIVE LOCAL]" in body.decode("utf-8")
    store.conn.close()


def test_filters_via_http_and_invalid_query(tmp_path: Path) -> None:
    store = open_store(tmp_path / "q.db", actor="cursor", clock=FrozenClock(T0))
    _seed_fleet(store, tmp_path)
    status, _, body = dispatch(store, "GET", "/?q=state:active", now=T0)
    text = body.decode("utf-8")
    grid = text.split('id="fleet-grid"', 1)[-1].split("</table>", 1)[0]
    assert "oem-radar" in grid
    assert "blocked-clank" not in grid
    status, _, body = dispatch(store, "GET", "/?q=state:paused", now=T0)
    assert "watch-clank" in body.decode("utf-8")
    status, _, body = dispatch(store, "GET", "/?q=state:blocked", now=T0)
    assert "blocked-clank" in body.decode("utf-8")
    status, _, body = dispatch(store, "GET", "/?q=session:open", now=T0)
    assert "oem-radar" in body.decode("utf-8")
    status, _, body = dispatch(store, "GET", "/?q=git:dirty", now=T0)
    assert "oem-radar" in body.decode("utf-8")
    status, _, body = dispatch(store, "GET", "/?q=harvest:observed", now=T0)
    assert "oem-radar" in body.decode("utf-8")
    status, _, body = dispatch(store, "GET", "/?q=harvest:never", now=T0)
    assert "nopath-clank" in body.decode("utf-8")
    status, _, body = dispatch(store, "GET", "/?q=clank:watch-clank", now=T0)
    page = body.decode("utf-8")
    assert "watch-clank" in page
    assert "data-clank-href=\"/clank/oem-radar\"" not in page
    status, _, body = dispatch(store, "GET", "/?q=state:actvie", now=T0)
    err = body.decode("utf-8")
    assert "invalid filter value" in err
    assert "id=\"query-error\"" in err
    assert "data-nav-row" not in err
    store.conn.close()


def test_xss_secrets_bind_and_non_conflation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("clankops.reconcile.inspect_github", lambda repo: (_ for _ in ()).throw(RuntimeError("github")))
    store = open_store(tmp_path / "sec.db", actor="cursor", clock=FrozenClock(T0))
    repo = _init_repo(tmp_path / "sec-repo")
    _git(repo, ["remote", "add", "origin", f"https://alice:{SECRET_PASSWORD}@github.com/anil-ganti-nbc/oem-radar.git"])
    (repo / SECRET_FILE).write_text("nope\n", encoding="utf-8")
    store.register_clank("oem-radar", display_name="OEM " + HOSTILE, local_path=str(repo))
    oem = store.start_mission("oem-radar", HOSTILE)
    store.record_checkpoint(
        oem["display_id"],
        branch="checkpoint-branch",
        head="checkpointhaedaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        working_tree="clean",
        next_action=HOSTILE,
    )
    store.pause_mission(oem["display_id"])
    result = _launch(store, returncode=0)
    harvest_local_git(store, "oem-radar")
    capture_deployment(store, "oem-radar", **_hetzner(deployed_sha="deployedsha11111111111111111111111111111"))
    capture_deployment(store, "oem-radar", **_nas(deployed_sha="deployedsha22222222222222222222222222222"))
    store.attach_artifact(
        mission=oem["display_id"],
        kind=CI_ARTIFACT_KIND,
        ref="cisha333333333333333333333333333333333333",
        title="CI success",
        source=EventSource.CI,
        metadata={"sha": "cisha333333333333333333333333333333333333", "state": "success"},
    )

    pages = []
    for path in ("/", "/attention", "/sessions", "/clank/oem-radar"):
        status, _, body = dispatch(store, "GET", path, now=T0)
        assert status == 200
        pages.append(body.decode("utf-8"))
    blob = _blob(*pages)
    for secret in SECRETS:
        assert secret not in blob
    assert "&lt;script&gt;" in blob
    assert "<script>alert" not in blob
    dossier = pages[-1]
    assert "[REC]" in dossier and "checkpoint-branch" in dossier
    assert HETZNER_SURFACE in dossier and NAS_SURFACE in dossier
    assert "deployment failed" not in dossier.lower()
    assert "wrong" not in dossier.lower() or "Old is not" in dossier or "not automatically wrong" in "/"
    assert "[EXITED]" in dossier
    assert "explicit handoff required" in dossier
    assert result["session_id"] in dossier
    sess = pages[2]
    assert "[EXITED]" in sess
    assert "[OPEN]" in sess
    assert "handoff" in sess.lower()
    with pytest.raises(ValidationError, match="allow-remote"):
        assert_bind_allowed("0.0.0.0")
    assert assert_bind_allowed("127.0.0.1") == "127.0.0.1"
    assert assert_bind_allowed("0.0.0.0", allow_remote=True) == "0.0.0.0"
    assert main(["--db", str(tmp_path / "no.db"), "terminal", "--host", "10.0.0.1", "--port", "9"]) == 2
    store.conn.close()


def test_harvest_failure_keeps_last_known_good(tmp_path: Path) -> None:
    store = open_store(tmp_path / "fail.db", actor="cursor", clock=FrozenClock(T0))
    repo = _init_repo(tmp_path / "repo")
    store.register_clank("oem-radar", local_path=str(repo))
    store.start_mission("oem-radar", "handheld")
    harvest_local_git(store, "oem-radar")
    good = store.conn.execute("SELECT state_json FROM local_git_observations").fetchone()
    head = json.loads(good["state_json"])["head"]

    def boom(path, timeout=None):
        return {"ok": False, "result_code": "TIMEOUT", "error_class": "TIMEOUT", "detail": "git timed out", "state": None}

    harvest_local_git(store, "oem-radar", observe=boom)
    status, _, body = dispatch(store, "GET", "/clank/oem-radar", now=T0)
    html = body.decode("utf-8")
    assert "TIMEOUT" in html
    assert "LAST KNOWN GOOD STATE" in html
    assert head[:7] in html or head[:12] in html
    store.register_clank("watch-clank")
    status, _, body = dispatch(store, "GET", "/clank/watch-clank", now=T0)
    page = body.decode("utf-8")
    assert "never harvested" in page
    assert 'data-plane="harvested">UNKNOWN' in page
    assert 'data-plane="live">UNKNOWN / NOT REQUESTED' in page
    store.conn.close()


def test_process_handoff_and_timeline_bounds(tmp_path: Path) -> None:
    store = open_store(tmp_path / "proc.db", actor="cursor", clock=FrozenClock(T0))
    mission = _paused(store)
    launched = _launch(store, returncode=0)
    status, _, body = dispatch(store, "GET", "/sessions", now=T0)
    html = body.decode("utf-8")
    assert "[EXITED]" in html
    assert "[OPEN]" in html
    assert "[MISSING]" in html
    assert launched["session_id"] in html
    store.end_session(launched["session_id"])
    status, _, body = dispatch(store, "GET", "/sessions", now=T0)
    html = body.decode("utf-8")
    assert "[CLOSED]" in html
    assert "[UNKNOWN]" in html
    assert "[RECORDED]" not in html
    store.register_clank("watch-clank")
    handed = store.start_mission("watch-clank", "canonical handoff")
    store.handoff_mission(handed["display_id"], "PAUSED", current_work="pausing")
    status, _, body = dispatch(store, "GET", "/sessions", now=T0)
    assert "[RECORDED]" in body.decode("utf-8")
    status, _, body = dispatch(store, "GET", "/api/clank/oem-radar?limit=3", now=T0)
    payload = json.loads(body.decode("utf-8"))
    assert payload["timeline_limit"] == 3
    assert len(payload["timeline"]) <= 3
    html_status, _, html_body = dispatch(store, "GET", "/clank/oem-radar?limit=5", now=T0)
    assert html_status == 200
    text = html_body.decode("utf-8")
    assert "TIMELINE" in text
    store.conn.close()


def test_http_headers_methods_and_concurrent_connections(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "net.db"
    store = open_store(db, actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", display_name="OEM Radar")
    store.start_mission("oem-radar", "handheld")
    before = ledger_fingerprint(store)
    store.conn.close()
    opens: list[int] = []
    real_open = open_readonly_store

    def counting_open(path, **kwargs):
        opens.append(threading.get_ident())
        return real_open(path, **kwargs)

    monkeypatch.setattr("clankops.terminal.open_readonly_store", counting_open)
    with running_terminal(db, census=_census(), clock=FrozenClock(T0)) as base:
        req = urllib.request.Request(base + "/", method="GET")
        with urllib.request.urlopen(req, timeout=8) as resp:
            assert resp.headers.get("Cache-Control") == "no-store"
            assert resp.headers.get("X-Content-Type-Options") == "nosniff"
            assert resp.headers.get("Referrer-Policy") == "no-referrer"
            body = resp.read().decode("utf-8")
            assert "CLANKOPS TERMINAL BETA" in body
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            status, _ = _http(base + "/", method=method)
            assert status == 405
        head_req = urllib.request.Request(base + "/", method="HEAD")
        with urllib.request.urlopen(head_req, timeout=8) as resp:
            assert resp.status == 200
            assert resp.read() == b""
        urls = [base + "/api/fleet", base + "/clank/oem-radar", base + "/attention", base + "/sessions"] * 3
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(_http, urls))
        assert all(status == 200 for status, _ in results)
    assert len(set(opens)) >= 2
    reader = open_readonly_store(db)
    assert ledger_fingerprint(reader) == before
    reader.conn.close()


def test_keyboard_markup_and_no_colour_only_states() -> None:
    html = fleet_html(
        {
            "summary": {"registered_clanks": 1, "active_missions": 1, "blocked_missions": 0},
            "snapshot": {"max_ledger_seq": 9, "generated_at": "t"},
            "mode": {"label": "SNAPSHOT"},
            "rows": [
                {
                    "slug": "oem-radar",
                    "display_name": "OEM",
                    "lifecycle": "ACTIVE",
                    "mission_display": "COPS-000001",
                    "mission_state": "ACTIVE",
                    "next_action": "do the thing",
                    "attention_count": 1,
                    "attention_top": {"class": "integrity", "reason_code": "GIT_DRIFT"},
                }
            ],
            "attention": {
                "items": [
                    {
                        "clank": "oem-radar",
                        "clank_name": "OEM",
                        "class": "integrity",
                        "reason_code": "GIT_DRIFT",
                        "reason": "recorded head a != observed b",
                    }
                ]
            },
        }
    )
    assert "id=\"command-bar\"" in html
    assert "data-clank-href=\"/clank/oem-radar\"" in html
    assert "tr.selected" in html or "class=\"selected\"" in _js() or "selected" in html
    assert "[ACTIVE]" in html
    assert "[GIT_DRIFT]" in html
    assert "■" in html
    assert "isTypingTarget" in _js()
    assert "input" in _js() and "textarea" in _js()
    for key, desc in HELP_BINDINGS:
        assert key in html
        assert desc in html


def _attach_ci(store, mission: str, sha: str, *, state: str, title: str) -> dict:
    return store.attach_artifact(
        mission=mission,
        kind=CI_ARTIFACT_KIND,
        ref=sha,
        title=title,
        source=EventSource.CI,
        artifact_source=EventSource.CI,
        metadata={"sha": sha, "state": state},
    )


def test_current_mission_ci_does_not_fall_back_to_another_mission(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ci-fallback.db", actor="cursor", clock=TickingClock(T0))
    store.register_clank("oem-radar")
    older = store.start_mission("oem-radar", "mission A")
    _attach_ci(store, older["display_id"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", state="success", title="green")
    store.record_checkpoint(older["display_id"], current_work="paused A", next_action="stop A")
    store.pause_mission(older["display_id"])
    current = store.start_mission("oem-radar", "mission B")
    status, _, body = dispatch(store, "GET", "/api/clank/oem-radar", now=T0)
    payload = json.loads(body.decode("utf-8"))
    assert payload["now"]["mission_id"] == current["mission_id"]
    assert payload["ci"] is None
    by_id = {row["mission_id"]: row for row in payload["missions"]}
    assert by_id[older["mission_id"]]["ci"]["sha"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert by_id[older["mission_id"]]["ci"]["state"] == "success"
    assert by_id[older["mission_id"]]["ci"]["state"] != by_id[older["mission_id"]]["ci"]["title"]
    assert by_id[current["mission_id"]]["ci"] is None
    html_status, _, html_body = dispatch(store, "GET", "/clank/oem-radar", now=T0)
    html = html_body.decode("utf-8")
    assert 'data-plane="ci">UNKNOWN' in html
    assert html_status == 200
    store.conn.close()


def test_current_mission_shows_its_own_latest_ci_never_older_mission(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ci-b.db", actor="cursor", clock=TickingClock(T0))
    store.register_clank("oem-radar")
    older = store.start_mission("oem-radar", "mission A")
    _attach_ci(store, older["display_id"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", state="success", title="A CI")
    store.pause_mission(older["display_id"])
    current = store.start_mission("oem-radar", "mission B")
    _attach_ci(store, current["display_id"], "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", state="failure", title="looks like success")
    status, _, body = dispatch(store, "GET", "/api/clank/oem-radar", now=T0)
    payload = json.loads(body.decode("utf-8"))
    assert payload["ci"]["sha"] == "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert payload["ci"]["state"] == "failure"
    assert payload["ci"]["title"] == "looks like success"
    by_id = {row["mission_id"]: row for row in payload["missions"]}
    assert by_id[older["mission_id"]]["ci"]["sha"].startswith("aaa")
    assert by_id[current["mission_id"]]["ci"]["sha"].startswith("bbb")
    store.conn.close()


def test_same_mission_latest_ci_is_created_utc_then_artifact_id(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ci-latest.db", actor="cursor", clock=TickingClock(T0))
    store.register_clank("oem-radar")
    mission = store.start_mission("oem-radar", "handheld")
    first = _attach_ci(store, mission["display_id"], "1111111111111111111111111111111111111111", state="pending", title="CI1")
    second = _attach_ci(store, mission["display_id"], "2222222222222222222222222222222222222222", state="success", title="CI2")
    view = latest_mission_ci(store, mission["mission_id"])
    assert view["artifact_id"] == second["artifact_id"]
    assert view["sha"] == "2222222222222222222222222222222222222222"
    assert view["state"] == "success"
    assert view["state"] != first["title"]
    status, _, body = dispatch(store, "GET", "/api/clank/oem-radar", now=T0)
    payload = json.loads(body.decode("utf-8"))
    assert payload["ci"]["artifact_id"] == second["artifact_id"]
    assert payload["ci"]["state"] == "success"
    store.conn.close()


def test_ci_state_never_taken_from_title(tmp_path: Path) -> None:
    store = open_store(tmp_path / "ci-title.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar")
    mission = store.start_mission("oem-radar", "handheld")
    store.attach_artifact(
        mission=mission["display_id"],
        kind=CI_ARTIFACT_KIND,
        ref="cccccccccccccccccccccccccccccccccccccccc",
        title="success",
        source=EventSource.CI,
        metadata={"sha": "cccccccccccccccccccccccccccccccccccccccc"},
    )
    view = latest_mission_ci(store, mission["mission_id"])
    assert view["title"] == "success"
    assert view["state"] is None
    status, _, body = dispatch(store, "GET", "/api/clank/oem-radar", now=T0)
    payload = json.loads(body.decode("utf-8"))
    assert payload["ci"]["state"] is None
    store.conn.close()


def test_bounded_timeline_read_and_filters(tmp_path: Path) -> None:
    store = open_store(tmp_path / "tl.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar")
    first = store.start_mission("oem-radar", "mission one")
    store.record_checkpoint(first["display_id"], source=EventSource.LOCAL_GIT, next_action="continue")
    for i in range(250):
        store.add_decision(first["display_id"], f"decision {i}")
    store.pause_mission(first["display_id"])
    second = store.start_mission("oem-radar", "mission two")
    store.add_decision(second["display_id"], "second mission only")
    clank_id = store.resolve_clank("oem-radar")["clank_id"]
    recent = list_recent_events(store.conn, clank_id=clank_id, limit=200)
    assert len(recent) == 200
    seqs = [event.ledger_seq for event in recent]
    assert seqs == sorted(seqs)
    all_events = list_events(store.conn, clank_id=clank_id)
    assert len(all_events) > 200
    assert seqs == [event.ledger_seq for event in all_events[-200:]]
    bounded = clank_timeline(store, clank_id, limit=50)
    assert bounded["limit"] == 50
    assert len(bounded["events"]) == 50
    assert bounded["matched"] == len(all_events)
    by_source = clank_timeline(store, clank_id, source=EventSource.LOCAL_GIT, limit=20)
    assert by_source["events"]
    assert all(row["source"] == EventSource.LOCAL_GIT for row in by_source["events"])
    by_type = clank_timeline(store, clank_id, event_type=EventType.DECISION_RECORDED, limit=20)
    assert len(by_type["events"]) == 20
    assert all(row["event_type"] == EventType.DECISION_RECORDED for row in by_type["events"])
    by_mission = clank_timeline(store, clank_id, mission=second["display_id"], limit=20)
    assert by_mission["events"]
    assert all(row["mission_id"] == second["mission_id"] for row in by_mission["events"])
    combined = clank_timeline(
        store,
        clank_id,
        source=EventSource.USER,
        event_type=EventType.DECISION_RECORDED,
        mission=second["display_id"],
        limit=10,
    )
    assert combined["matched"] == 1
    assert combined["events"][0]["summary"].startswith("second mission only") or "second mission only" in combined["events"][0]["summary"]
    status, _, body = dispatch(store, "GET", "/api/clank/oem-radar?limit=40", now=T0)
    payload = json.loads(body.decode("utf-8"))
    assert len(payload["timeline"]) == 40
    assert payload["timeline_limit"] == 40
    html_status, _, html_body = dispatch(store, "GET", "/clank/oem-radar?limit=40", now=T0)
    html = html_body.decode("utf-8")
    displayed = [row["ledger_seq"] for row in reversed(payload["timeline"])]
    assert str(displayed[0]) in html
    assert payload["timeline"][0]["ledger_seq"] < payload["timeline"][-1]["ledger_seq"]
    with pytest.raises(ValidationError, match="unknown timeline source"):
        clank_timeline(store, clank_id, source="NOPE")
    with pytest.raises(ValidationError, match="unknown timeline event_type"):
        clank_timeline(store, clank_id, event_type="NOT_AN_EVENT")
    store.register_clank("watch-clank")
    other = store.start_mission("watch-clank", "elsewhere")
    with pytest.raises(ValidationError, match="does not belong"):
        clank_timeline(store, clank_id, mission=other["display_id"])
    bad_source, _, src_body = dispatch(store, "GET", "/api/clank/oem-radar?source=NOPE", now=T0)
    assert bad_source == 400
    assert b"unknown timeline source" in src_body
    missing, _, miss_body = dispatch(store, "GET", "/api/clank/oem-radar?mission=COPS-999999", now=T0)
    assert missing == 404
    store.conn.close()


def test_legacy_reconcile_api_is_live_local_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_calls: list[str] = []
    github_calls: list[str] = []
    monkeypatch.setattr(
        "clankops.reconcile.inspect_git",
        lambda path: local_calls.append(path) or _local("main", HEAD, dirty=False),
    )
    monkeypatch.setattr(
        "clankops.reconcile.inspect_github",
        lambda repo: github_calls.append(repo) or {
            "source": "GITHUB",
            "ok": True,
            "error": None,
            "repo": repo,
            "default_branch": "main",
            "default_branch_head": HEAD,
            "open_prs": [],
            "checks": {
                "source": "GITHUB",
                "ok": True,
                "error": None,
                "sha": HEAD,
                "state": "success",
                "runs": [],
                "contexts": [],
            },
        },
    )
    store = open_store(tmp_path / "api.db", actor="cursor", clock=FrozenClock(T0))
    repo = _init_repo(tmp_path / "oem")
    store.register_clank("oem-radar", local_path=str(repo), remotes=[CANON])
    store.start_mission("oem-radar", "handheld")
    dispatch(store, "GET", "/", now=T0)
    dispatch(store, "GET", "/fleet", now=T0)
    dispatch(store, "GET", "/attention", now=T0)
    dispatch(store, "GET", "/sessions", now=T0)
    dispatch(store, "GET", "/clank/oem-radar", now=T0)
    assert local_calls == []
    assert github_calls == []
    status, _, body = dispatch(store, "GET", "/api/reconcile", now=T0)
    assert status == 200
    assert local_calls
    assert github_calls == []
    fleet = json.loads(body.decode("utf-8"))
    assert fleet["include_github"] is False
    local_calls.clear()
    dispatch(store, "GET", "/api/reconcile?github=1", now=T0)
    assert github_calls
    github_calls.clear()
    local_calls.clear()
    status, _, body = dispatch(store, "GET", "/api/clank/oem-radar/reconcile", now=T0)
    assert status == 200
    assert local_calls
    assert github_calls
    github_calls.clear()
    dispatch(store, "GET", "/api/clank/oem-radar/reconcile?github=0", now=T0)
    assert github_calls == []
    local_calls.clear()
    dispatch(store, "GET", "/api/clank/oem-radar/reconcile?live=0", now=T0)
    assert local_calls
    store.conn.close()


def test_snapshot_attention_coverage_is_partial_until_live(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "clankops.reconcile.inspect_git",
        lambda path: _local("main", HEAD, dirty=True),
    )
    store = open_store(tmp_path / "att.db", actor="cursor", clock=FrozenClock(T0))
    repo = _init_repo(tmp_path / "oem")
    store.register_clank("oem-radar", local_path=str(repo), remotes=[CANON])
    mission = store.start_mission("oem-radar", "handheld")
    store.record_checkpoint(
        mission["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
        working_tree="clean",
    )
    store.pause_mission(mission["display_id"])
    status, _, body = dispatch(store, "GET", "/attention", now=T0)
    html = body.decode("utf-8")
    assert status == 200
    assert "coverage: PARTIAL" in html
    assert "UNOBSERVABLE IN SNAPSHOT" in html
    assert "use ?live=1 to evaluate Foundation 7 live-local reasons" in html
    assert REASON_DIRTY_WITHOUT_OPEN_SESSION not in html
    assert "none derived" not in html
    assert 'id="command-bar"' not in html
    api_status, _, api_body = dispatch(store, "GET", "/api/attention", now=T0)
    report = json.loads(api_body.decode("utf-8"))
    assert api_status == 200
    assert report["coverage"] == "PARTIAL"
    assert report["live_local_checks"] == "UNOBSERVABLE IN SNAPSHOT"
    assert REASON_DIRTY_WITHOUT_OPEN_SESSION not in [item["reason_code"] for item in report["items"]]
    live_status, _, live_body = dispatch(store, "GET", "/attention?live=1", now=T0)
    live_html = live_body.decode("utf-8")
    assert live_status == 200
    assert REASON_DIRTY_WITHOUT_OPEN_SESSION in live_html
    assert "coverage: EVALUATED" in live_html
    live_api = json.loads(dispatch(store, "GET", "/api/attention?live=1", now=T0)[2].decode("utf-8"))
    assert live_api["coverage"] == "EVALUATED"
    assert live_api["live_local_checks"] == "EVALUATED"
    assert live_api["live_local_observable_count"] == 1
    assert live_api["live_local_unobservable_count"] == 0
    assert any(item["reason_code"] == REASON_DIRTY_WITHOUT_OPEN_SESSION for item in live_api["items"])
    q_status, _, q_body = dispatch(store, "GET", "/attention?q=state:active", now=T0)
    assert q_status == 200
    assert "coverage: PARTIAL" in q_body.decode("utf-8")
    store.conn.close()


def test_live_attention_coverage_follows_actual_local_observability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "clankops.reconcile.inspect_git",
        lambda path: _local("main", HEAD, dirty=True),
    )
    store = open_store(tmp_path / "obs.db", actor="cursor", clock=FrozenClock(T0))
    repo = _init_repo(tmp_path / "oem")
    store.register_clank("oem-radar", local_path=str(repo), remotes=[CANON])
    oem = store.start_mission("oem-radar", "handheld")
    store.record_checkpoint(
        oem["display_id"],
        next_action="keep going",
        branch="main",
        head=HEAD,
        working_tree="clean",
    )
    store.pause_mission(oem["display_id"])
    store.register_clank("watch-clank", display_name="Watch Clank")
    watch = store.start_mission("watch-clank", "keep collecting")
    store.record_checkpoint(watch["display_id"], next_action="keep collecting")
    store.pause_mission(watch["display_id"])

    oem_live = json.loads(dispatch(store, "GET", "/api/attention?clank=oem-radar&live=1", now=T0)[2].decode("utf-8"))
    assert oem_live["coverage"] == "EVALUATED"
    assert oem_live["live_local_checks"] == "EVALUATED"
    assert oem_live["live_local_observable_count"] == 1
    assert oem_live["live_local_unobservable_count"] == 0
    assert any(item["reason_code"] == REASON_DIRTY_WITHOUT_OPEN_SESSION for item in oem_live["items"])

    watch_live = json.loads(
        dispatch(store, "GET", "/api/attention?clank=watch-clank&live=1", now=T0)[2].decode("utf-8")
    )
    assert watch_live["coverage"] == "PARTIAL"
    assert watch_live["live_local_checks"] == "UNOBSERVABLE"
    assert watch_live["live_local_observable_count"] == 0
    assert watch_live["live_local_unobservable_count"] == 1
    assert watch_live["live_local_unobservable"][0]["clank"] == "watch-clank"
    assert "no local_path" in str(watch_live["live_local_unobservable"][0]["error"])
    assert REASON_DIRTY_WITHOUT_OPEN_SESSION not in [item["reason_code"] for item in watch_live["items"]]
    watch_html = dispatch(store, "GET", "/attention?clank=watch-clank&live=1", now=T0)[2].decode("utf-8")
    assert "coverage: PARTIAL" in watch_html
    assert "live-local-dependent checks: UNOBSERVABLE" in watch_html
    assert "none derived" not in watch_html
    assert "use ?live=1 to evaluate Foundation 7 live-local reasons" not in watch_html
    dossier = json.loads(dispatch(store, "GET", "/api/clank/watch-clank?live=1", now=T0)[2].decode("utf-8"))
    assert dossier["attention"]["coverage"] == "PARTIAL"
    assert dossier["attention"]["live_local_checks"] == "UNOBSERVABLE"
    dossier_html = dispatch(store, "GET", "/clank/watch-clank?live=1", now=T0)[2].decode("utf-8")
    assert "coverage: PARTIAL" in dossier_html
    assert "EVALUATED" not in dossier_html.split("id=\"attention-coverage\"")[1].split("</p>")[0]

    fleet_live = json.loads(dispatch(store, "GET", "/api/attention?live=1", now=T0)[2].decode("utf-8"))
    assert fleet_live["coverage"] == "PARTIAL"
    assert fleet_live["live_local_checks"] == "PARTIAL"
    assert fleet_live["live_local_observable_count"] == 1
    assert fleet_live["live_local_unobservable_count"] == 1
    unobs = {row["clank"]: row["error"] for row in fleet_live["live_local_unobservable"]}
    assert unobs == {"watch-clank": "no local_path"}
    assert any(item["reason_code"] == REASON_DIRTY_WITHOUT_OPEN_SESSION for item in fleet_live["items"])
    fleet_html = dispatch(store, "GET", "/?live=1", now=T0)[2].decode("utf-8")
    assert "coverage PARTIAL" in fleet_html or "coverage: PARTIAL" in fleet_html
    assert "use ?live=1 to evaluate Foundation 7 live-local reasons" not in fleet_html

    snap = json.loads(dispatch(store, "GET", "/api/attention", now=T0)[2].decode("utf-8"))
    assert snap["coverage"] == "PARTIAL"
    assert snap["live_local_checks"] == "UNOBSERVABLE IN SNAPSHOT"
    assert snap["live_local_observable_count"] == 0
    assert snap["live_local_unobservable_count"] == 2
    store.conn.close()


def test_empty_evidence_planes_do_not_stamp_source_badges(tmp_path: Path) -> None:
    store = open_store(tmp_path / "planes.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("watch-clank")
    store.start_mission("watch-clank", "keep collecting")
    status, _, body = dispatch(store, "GET", "/clank/watch-clank", now=T0)
    html = body.decode("utf-8")
    assert status == 200
    assert 'data-plane="harvested">UNKNOWN' in html
    assert "[LOCAL_GIT]" not in html.split('data-plane="harvested">')[1].split("</td>")[0]
    assert 'data-plane="live">UNKNOWN / NOT REQUESTED' in html
    assert 'data-plane="github">UNKNOWN / NOT REQUESTED' in html
    assert 'data-plane="ci">UNKNOWN' in html
    live_html = dispatch(store, "GET", "/clank/watch-clank?live=1", now=T0)[2].decode("utf-8")
    assert "UNKNOWN / NOT REQUESTED" in live_html
    assert 'data-plane="github">UNKNOWN / NOT REQUESTED' in live_html
    gh_html = dispatch(store, "GET", "/clank/watch-clank?github=1", now=T0)[2].decode("utf-8")
    assert 'data-plane="live">UNKNOWN / NOT REQUESTED' in gh_html
    store.conn.close()


def test_terminal_status_html_and_api_health_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git_calls: list[str] = []
    github_calls: list[str] = []
    harvest_calls: list[str] = []
    monkeypatch.setattr(
        "clankops.gitinspect.inspect_git",
        lambda *a, **k: git_calls.append("git") or {"is_git": False},
    )
    monkeypatch.setattr(
        "clankops.githubinspect.inspect_github",
        lambda *a, **k: github_calls.append("gh") or {"ok": False},
    )
    monkeypatch.setattr(
        "clankops.harvest.harvest_local_git",
        lambda *a, **k: harvest_calls.append("harvest") or {},
    )
    store = open_store(tmp_path / "status.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar")
    before = ledger_fingerprint(store)

    status, ctype, body = dispatch(store, "GET", "/health", now=T0)
    html = body.decode("utf-8")
    assert status == 200
    assert ctype.startswith("text/html")
    assert "CLANKOPS TERMINAL BETA" in html
    assert 'href="/">fleet</a>' in html
    assert 'href="/attention">attention</a>' in html
    assert 'href="/sessions">sessions</a>' in html
    assert 'href="/health">status</a>' in html
    assert "TERMINAL STATUS" in html
    assert "ClankOps Terminal process/read path" in html
    assert "not fleet/source operational health" in html
    assert "read-only" in html
    assert ">beta<" in html or "beta" in html
    assert "per-request" in html
    assert "snapshot at 2026-09-10T04:00:00.000000Z" in html
    assert "Pretty-print" not in html
    assert not html.lstrip().startswith("{")

    alias, alias_type, alias_body = dispatch(store, "GET", "/status", now=T0)
    assert alias == 200
    assert alias_type.startswith("text/html")
    assert "TERMINAL STATUS" in alias_body.decode("utf-8")

    head_status, head_type, _ = dispatch(store, "HEAD", "/health", now=T0)
    assert head_status == 200
    assert head_type.startswith("text/html")
    api_head_status, api_head_type, _ = dispatch(store, "HEAD", "/api/health", now=T0)
    assert api_head_status == 200
    assert api_head_type.startswith("application/json")

    api_status, api_type, api_body = dispatch(store, "GET", "/api/health", now=T0)
    payload = json.loads(api_body.decode("utf-8"))
    assert api_status == 200
    assert api_type.startswith("application/json")
    assert payload == {
        "ok": True,
        "mode": "read-only",
        "terminal": "beta",
        "stale_after": "24h",
        "connection": "per-request",
        "snapshot": {
            "generated_at": "2026-09-10T04:00:00.000000Z",
            "ledger_event_count": before["event_count"],
            "max_ledger_seq": before["max_ledger_seq"],
            "consistency": "read-time; not a transactionally frozen multi-page snapshot",
        },
    }
    assert ledger_fingerprint(store) == before
    assert git_calls == []
    assert github_calls == []
    assert harvest_calls == []
    store.conn.close()


def test_shared_header_unknown_is_not_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _count(None) == "UNKNOWN"
    assert _count(0) == "0"
    assert _count(18) == "18"
    assert _count(True) == "UNKNOWN"
    assert _count(False) == "UNKNOWN"
    assert _count("18") == "UNKNOWN"
    assert _count(1.5) == "UNKNOWN"

    missing = _status_bar({})
    assert "UNKNOWN CLANKS" in missing
    assert "UNKNOWN ACTIVE" in missing
    assert "UNKNOWN BLOCKED" in missing
    assert "0 CLANKS" not in missing
    assert "0 ACTIVE" not in missing
    assert "0 BLOCKED" not in missing

    explicit_none = _status_bar(
        {"registered_clanks": None, "active_missions": None, "blocked_missions": None}
    )
    assert "UNKNOWN CLANKS" in explicit_none
    assert "UNKNOWN ACTIVE" in explicit_none
    assert "UNKNOWN BLOCKED" in explicit_none
    assert "0 CLANKS" not in explicit_none

    zeros = _status_bar(
        {"registered_clanks": 0, "active_missions": 0, "blocked_missions": 0}
    )
    assert "0 CLANKS" in zeros
    assert "0 ACTIVE" in zeros
    assert "0 BLOCKED" in zeros
    assert "UNKNOWN CLANKS" not in zeros

    positive = _status_bar(
        {"registered_clanks": 18, "active_missions": 3, "blocked_missions": 2}
    )
    assert "18 CLANKS" in positive
    assert "3 ACTIVE" in positive
    assert "2 BLOCKED" in positive

    fleet = fleet_html(
        {
            "summary": {"registered_clanks": 18, "active_missions": 3, "blocked_missions": 0},
            "snapshot": {"max_ledger_seq": 9, "generated_at": "t"},
            "mode": {"label": "SNAPSHOT"},
            "rows": [],
            "attention": {},
        }
    )
    fleet_header = _header(fleet)
    assert "18 CLANKS" in fleet_header
    assert "3 ACTIVE" in fleet_header
    assert "0 BLOCKED" in fleet_header
    assert "UNKNOWN CLANKS" not in fleet_header

    attn_header = _header(attention_html({"snapshot": {}, "mode": {"label": "SNAPSHOT"}, "items": []}))
    assert "UNKNOWN CLANKS" in attn_header
    assert "UNKNOWN ACTIVE" in attn_header
    assert "UNKNOWN BLOCKED" in attn_header
    assert "0 CLANKS" not in attn_header

    sess_header = _header(sessions_html({"snapshot": {}, "mode": {"label": "SNAPSHOT"}, "sessions": []}))
    assert "UNKNOWN CLANKS" in sess_header
    assert "UNKNOWN ACTIVE" in sess_header
    assert "UNKNOWN BLOCKED" in sess_header
    assert "0 CLANKS" not in sess_header

    status_header = _header(
        status_html(
            {
                "ok": True,
                "mode": "read-only",
                "terminal": "beta",
                "connection": "per-request",
                "snapshot": {"generated_at": "t", "max_ledger_seq": 1, "ledger_event_count": 1},
            }
        )
    )
    assert "UNKNOWN CLANKS" in status_header
    assert "UNKNOWN ACTIVE" in status_header
    assert "UNKNOWN BLOCKED" in status_header
    assert "0 CLANKS" not in status_header
    assert "TERMINAL STATUS" in status_html(
        {
            "ok": True,
            "mode": "read-only",
            "terminal": "beta",
            "connection": "per-request",
            "snapshot": {},
        }
    )

    dossier = dossier_html(
        {
            "identity": {"slug": "oem-radar", "display_name": "OEM Radar", "clank_id": "id-oem"},
            "now": {"mission_state": "ACTIVE", "open_sessions": []},
            "snapshot": {"max_ledger_seq": 4, "generated_at": "t"},
            "mode": {"label": "SNAPSHOT"},
            "timeline": [],
        }
    )
    dossier_header = _header(dossier)
    assert "UNKNOWN CLANKS" in dossier_header
    assert "UNKNOWN ACTIVE" in dossier_header
    assert "UNKNOWN BLOCKED" in dossier_header
    assert "1 CLANKS" not in dossier_header
    assert "1 ACTIVE" not in dossier_header
    assert "0 BLOCKED" not in dossier_header

    git_calls: list[str] = []
    github_calls: list[str] = []
    harvest_calls: list[str] = []
    store = open_store(tmp_path / "header.db", actor="cursor", clock=FrozenClock(T0))
    _seed_fleet(store, tmp_path)
    before = ledger_fingerprint(store)
    monkeypatch.setattr(
        "clankops.gitinspect.inspect_git",
        lambda *a, **k: git_calls.append("git") or {"is_git": False},
    )
    monkeypatch.setattr(
        "clankops.githubinspect.inspect_github",
        lambda *a, **k: github_calls.append("gh") or {"ok": False},
    )
    monkeypatch.setattr(
        "clankops.harvest.harvest_local_git",
        lambda *a, **k: harvest_calls.append("harvest") or {},
    )
    home = dispatch(store, "GET", "/", now=T0, census=_census())[2].decode("utf-8")
    home_header = _header(home)
    assert "5 CLANKS" in home_header
    assert "3 ACTIVE" in home_header
    assert "1 BLOCKED" in home_header
    assert "0 CLANKS" not in home_header

    for path in ("/attention", "/sessions", "/health", "/clank/oem-radar"):
        text = dispatch(store, "GET", path, now=T0, census=_census())[2].decode("utf-8")
        header = _header(text)
        assert "UNKNOWN CLANKS" in header, path
        assert "UNKNOWN ACTIVE" in header, path
        assert "UNKNOWN BLOCKED" in header, path
        assert "0 CLANKS" not in header, path
        assert "5 CLANKS" not in header, path
        assert "1 CLANKS" not in header, path
        if path == "/health":
            assert "TERMINAL STATUS" in text
            assert "not fleet/source operational health" in text

    assert ledger_fingerprint(store) == before
    assert git_calls == []
    assert github_calls == []
    assert harvest_calls == []
    store.conn.close()

