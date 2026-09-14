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

from clankops.ci import CI_ARTIFACT_KIND
from clankops.cli import main
from clankops.clock import FrozenClock
from clankops.deployment import capture_deployment
from clankops.enums import EventSource, EventType
from clankops.errors import ValidationError
from clankops.events import list_events
from clankops.harvest import harvest_local_git
from clankops.readmodel import ledger_fingerprint
from clankops.store import open_readonly_store, open_store
from clankops.terminal import assert_bind_allowed, dispatch, serve
from clankops.terminal_query import QueryError, apply_filters, parse_filter
from clankops.terminal_views import HELP_BINDINGS, _js, fleet_html

from test_fleet_harvest1 import SECRET_FILE, SECRET_PASSWORD, SECRET_QUERY, SECRET_TOKEN, _init_repo, _git
from test_foundation2 import T0, _census, _http, running_terminal
from test_foundation3 import HEAD, _local
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

    for path in ("/", "/fleet", "/attention", "/sessions", "/health", "/clank/oem-radar", "/clank/watch-clank", "/clank/clankops"):
        status, ctype, body = dispatch(store, "GET", path, now=T0, census=_census())
        assert status == 200, path
        text = body.decode("utf-8")
        if path != "/health":
            assert "CLANKOPS TERMINAL BETA" in text
            assert "[SNAPSHOT]" in text
            assert "id=\"command-bar\"" in text or path in {"/sessions", "/health"} or "command-bar" in text or path.startswith("/clank/")
        else:
            payload = json.loads(text)
            assert payload["ok"] is True
            assert payload["snapshot"]["max_ledger_seq"] == before["max_ledger_seq"]

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
        for path in ("/", "/api/fleet", "/clank/oem-radar", "/attention"):
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
    assert "[LOCAL_GIT]" in page  # evidence plane label
    assert "never harvested" in page
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
