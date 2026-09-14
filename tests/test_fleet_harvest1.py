"""Fleet Harvest 1: automatic local Git evidence."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clankops.cli import main
from clankops.clock import FrozenClock, TickingClock, isoformat_utc
from clankops.enums import EventSource, EventType, MissionState
from clankops.events import list_events
from clankops.gitinspect import (
    RESULT_ERROR,
    RESULT_NOT_A_GIT_REPOSITORY,
    RESULT_PATH_MISSING,
    RESULT_TIMEOUT,
    _bounded_detail,
    harvest_run_git,
    normalize_remote_identity,
    observe_local_git_checkout,
    run_git,
)
from clankops.harvest import (
    RESULT_AMBIGUOUS_CANONICAL_PATH,
    RESULT_NO_CANONICAL_PATH,
    RESULT_OBSERVED_CHANGED,
    RESULT_OBSERVED_UNCHANGED,
    RESULT_STALE_OBSERVATION,
    WRITE_CHANGED,
    WRITE_STALE,
    _record_state_observation,
    checkout_key_for_path,
    format_harvest_text,
    harvest_local_git,
    local_git_harvest_view,
    semantic_state_payload,
    state_fingerprint,
)
from clankops.projections import dump_projection_state, rebuild_projections
from clankops.readmodel import dossier, ledger_fingerprint
from clankops.resume import format_resume_text, resume_packet
from clankops.store import open_store
from clankops.terminal import _dossier_html

from test_foundation2 import T0
from test_foundation3 import _seed

SECRET_PASSWORD = "SuperSecretPassw0rd"
SECRET_TOKEN = "ghp_ABCDEFGHIJKLMNOP1234leak"
SECRET_PAT = "github_pat_ABCDEFGHIJKLMNOPleak"
SECRET_QUERY = "leakquerytokenXYZ"
SECRET_FILE = "secret-notes-DO-NOT-PERSIST.txt"
SECRETS = (SECRET_PASSWORD, SECRET_TOKEN, SECRET_PAT, SECRET_QUERY, SECRET_FILE)

def _git(path: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "Harvest Test",
            "GIT_AUTHOR_EMAIL": "harvest@example.test",
            "GIT_COMMITTER_NAME": "Harvest Test",
            "GIT_COMMITTER_EMAIL": "harvest@example.test",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_OPTIONAL_LOCKS": "0",
        }
    )
    return subprocess.run(
        ["git", *args],
        cwd=str(path),
        check=check,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        shell=False,
        env=env,
        encoding="utf-8",
        errors="replace",
    )


def _init_repo(path: Path, *, branch: str = "main") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, ["init", "-b", branch])
    _git(path, ["config", "user.email", "harvest@example.test"])
    _git(path, ["config", "user.name", "Harvest Test"])
    _git(path, ["config", "commit.gpgsign", "false"])
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _git(path, ["add", "README.md"])
    _git(path, ["commit", "-m", "init"])
    return path


def _events(store, event_type: str | None = None) -> list:
    rows = list_events(store.conn)
    if event_type is None:
        return rows
    return [event for event in rows if event.event_type == event_type]


def _blob(*parts: object) -> str:
    return json.dumps(parts, default=str, ensure_ascii=False)


def _assert_no_secrets(text: str) -> None:
    lowered = text
    for secret in SECRETS:
        assert secret not in lowered


def _surfaces(store, clank: str = "oem-radar") -> str:
    packet = resume_packet(store, clank, include_github=False)
    html = _dossier_html(dossier(store, clank, include_github=False))
    events = [
        {"type": e.event_type, "source": e.source, "payload": e.payload, "mission": e.mission_id, "session": e.session_id}
        for e in list_events(store.conn)
    ]
    projections = dump_projection_state(store.conn)
    return _blob(packet, html, events, projections)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    return _init_repo(tmp_path / "repo")


def test_remote_identities_never_keep_credentials() -> None:
    https_pw = normalize_remote_identity(
        f"https://alice:{SECRET_PASSWORD}@github.com/anil-ganti-nbc/oem-radar.git"
    )
    https_token = normalize_remote_identity(
        f"https://x-access-token:{SECRET_TOKEN}@github.com/org/repo.git"
    )
    query = normalize_remote_identity(
        f"https://github.com/org/repo.git?token={SECRET_QUERY}"
    )
    ssh = normalize_remote_identity("git@github.com:anil-ganti-nbc/watch-clank.git")
    gh = normalize_remote_identity("https://github.com/anil-ganti-nbc/oem-radar.git")
    bad = normalize_remote_identity("::::not-a-remote")
    assert https_pw["identity"] == "github.com/anil-ganti-nbc/oem-radar"
    assert https_pw["redacted"] is True
    assert https_token["identity"] == "github.com/org/repo"
    assert https_token["redacted"] is True
    assert query["identity"] == "github.com/org/repo"
    assert query["redacted"] is True
    assert ssh["identity"] == "github.com/anil-ganti-nbc/watch-clank"
    assert ssh["redacted"] is False
    assert gh["identity"] == "github.com/anil-ganti-nbc/oem-radar"
    assert bad["present"] is True
    assert bad["redacted"] is True
    assert bad["identity"] is None
    for row in (https_pw, https_token, query, ssh, gh, bad):
        _assert_no_secrets(json.dumps(row))


def test_stderr_token_is_redacted() -> None:
    cleaned = _bounded_detail(f"fatal: {SECRET_TOKEN} while reading remote")
    assert cleaned
    _assert_no_secrets(cleaned)


def test_first_observation_emits_state_and_run(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store, path=str(repo), remotes=[], branch="main")
    before = _events(store)
    result = harvest_local_git(store, "oem-radar", actor="cursor")
    assert result["observed_changed"] == 1
    observed = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)
    runs = _events(store, EventType.LOCAL_GIT_HARVEST_COMPLETED)
    assert len(observed) == 1
    assert len(runs) == 1
    event = observed[0]
    assert event.source == EventSource.LOCAL_GIT
    assert event.mission_id is None
    assert event.session_id is None
    assert event.payload.get("dirty") is False
    assert event.payload.get("upstream_ahead_local") is None
    assert event.payload.get("upstream_behind_local") is None
    assert event.payload.get("upstream_freshness")
    assert "alternate_checkouts" not in event.payload
    assert runs[0].source == EventSource.SYSTEM
    assert runs[0].mission_id is None
    assert runs[0].session_id is None
    assert store.resolve_mission(mission["display_id"])["state"] == MissionState.ACTIVE
    types = {e.event_type for e in _events(store) if e.event_id not in {x.event_id for x in before}}
    assert EventType.CHECKPOINT_RECORDED not in types
    assert EventType.MISSION_CREATED not in types or len(_events(store, EventType.MISSION_CREATED)) == 1
    assert EventType.HANDOFF_RECORDED not in types
    assert EventType.MISSION_STATE_RECONCILED not in types
    assert EventType.MISSION_STATE_CHANGED not in types
    assert EventType.SESSION_STARTED not in types
    assert EventType.SESSION_ENDED not in types
    assert EventType.DEPLOYMENT_OBSERVED not in types
    assert not any(e.event_type == EventType.ARTIFACT_ATTACHED for e in _events(store) if e.event_id not in {x.event_id for x in before})
    store.conn.close()


def test_identical_second_observation_dedups_but_records_run(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    first = harvest_local_git(store, "oem-radar")
    second = harvest_local_git(store, "oem-radar")
    assert first["observed_changed"] == 1
    assert second["observed_unchanged"] == 1
    assert len(_events(store, EventType.LOCAL_GIT_STATE_OBSERVED)) == 1
    assert len(_events(store, EventType.LOCAL_GIT_HARVEST_COMPLETED)) == 2
    view = local_git_harvest_view(store, "oem-radar")
    assert view["latest_result"] == RESULT_OBSERVED_UNCHANGED
    assert view["state_fingerprint"] == first["results"][0]["state_fingerprint"]
    store.conn.close()


def test_semantic_changes_emit_new_observation(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    harvest_local_git(store, "oem-radar")
    assert len(_events(store, EventType.LOCAL_GIT_STATE_OBSERVED)) == 1
    (repo / "more.txt").write_text("x\n", encoding="utf-8")
    harvest_local_git(store, "oem-radar")
    assert len(_events(store, EventType.LOCAL_GIT_STATE_OBSERVED)) == 2
    dirty = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)[-1]
    assert dirty.payload["dirty"] is True
    assert dirty.payload["dirty_count"] == 1
    (repo / "second.txt").write_text("z\n", encoding="utf-8")
    harvest_local_git(store, "oem-radar")
    assert len(_events(store, EventType.LOCAL_GIT_STATE_OBSERVED)) == 3
    assert _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)[-1].payload["dirty_count"] == 2
    _git(repo, ["add", "-A"])
    _git(repo, ["commit", "-m", "more"])
    harvest_local_git(store, "oem-radar")
    clean = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)[-1]
    assert clean.payload["dirty"] is False
    _git(repo, ["checkout", "-b", "feature"])
    harvest_local_git(store, "oem-radar")
    assert _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)[-1].payload["branch"] == "feature"
    store.conn.close()


def test_display_time_excluded_from_semantic_fingerprint() -> None:
    payload = {
        "clank_id": "c",
        "checkout_key": "k",
        "branch": "main",
        "head": "abc",
        "dirty": False,
    }
    assert state_fingerprint(payload) == state_fingerprint(payload)


def test_worktree_and_remote_changes_alter_fingerprint(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    harvest_local_git(store, "oem-radar")
    _git(repo, ["remote", "add", "origin", "https://github.com/anil-ganti-nbc/oem-radar.git"])
    harvest_local_git(store, "oem-radar")
    assert len(_events(store, EventType.LOCAL_GIT_STATE_OBSERVED)) == 2
    wt = tmp_path / "linked"
    _git(repo, ["worktree", "add", str(wt), "-b", "wt-branch"])
    harvest_local_git(store, "oem-radar")
    latest = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)[-1]
    assert len(latest.payload.get("worktrees") or []) >= 2
    store.conn.close()


def test_secrets_absent_from_all_surfaces(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    _git(
        repo,
        [
            "remote",
            "add",
            "origin",
            f"https://alice:{SECRET_PASSWORD}@github.com/anil-ganti-nbc/oem-radar.git",
        ],
    )
    _git(repo, ["remote", "add", "tokenish", f"https://x-access-token:{SECRET_TOKEN}@github.com/org/repo.git"])
    _git(repo, ["remote", "add", "query", f"https://github.com/org/repo.git?token={SECRET_QUERY}"])
    _git(repo, ["remote", "add", "ssh", "git@github.com:anil-ganti-nbc/watch-clank.git"])
    (repo / SECRET_FILE).write_text("private plan\n", encoding="utf-8")
    (repo / "credentials.env").write_text("x=1\n", encoding="utf-8")
    harvest_local_git(store, "oem-radar")
    text = _surfaces(store)
    _assert_no_secrets(text)
    assert "credentials.env" not in text
    html = _dossier_html(dossier(store, "oem-radar", include_github=False))
    assert "LOCAL GIT" in html
    assert "[OBSERVED]" in html
    store.conn.close()


def test_no_fetch_and_list_argv(tmp_path: Path, repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], bool]] = []
    real = subprocess.run

    def spy(*args, **kwargs):
        argv = list(args[0]) if args else list(kwargs.get("args") or [])
        calls.append((argv, bool(kwargs.get("shell"))))
        return real(*args, **kwargs)

    monkeypatch.setattr("clankops.gitinspect.subprocess.run", spy)
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    harvest_local_git(store, "oem-radar")
    assert calls
    for argv, shell in calls:
        assert shell is False
        assert isinstance(argv, list)
        assert argv[0] == "git"
        assert "fetch" not in argv
        assert "pull" not in argv
        assert "push" not in argv
        assert "clone" not in argv
        assert "ls-remote" not in argv
    with pytest.raises(RuntimeError):
        harvest_run_git(repo, ["fetch", "origin"])
    store.conn.close()


def test_timeout_isolated_and_later_targets_run(tmp_path: Path, repo: Path) -> None:
    other = _init_repo(tmp_path / "other")
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", local_path=str(repo))
    store.register_clank("watch-clank", local_path=str(other))

    def observe(path, timeout=None):
        if Path(path).resolve() == repo.resolve():
            return {
                "ok": False,
                "result_code": "TIMEOUT",
                "error_class": "TIMEOUT",
                "detail": "git timed out",
                "state": None,
            }
        return observe_local_git_checkout(path, timeout=timeout)

    result = harvest_local_git(store, observe=observe)
    by_slug = {row["slug"]: row["result"] for row in result["results"]}
    assert by_slug["oem-radar"] == "TIMEOUT"
    assert by_slug["watch-clank"] in {RESULT_OBSERVED_CHANGED, RESULT_OBSERVED_UNCHANGED}
    assert result["errors"] == 1
    from clankops.harvest import harvest_exit_code

    assert harvest_exit_code(result) == 1
    store.conn.close()


def test_run_git_timeout_does_not_hang(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        assert kwargs.get("timeout") == 1
        raise subprocess.TimeoutExpired(["git"], 1)

    monkeypatch.setattr("clankops.gitinspect.subprocess.run", boom)
    code, _out, err = run_git(".", ["status"], timeout=1)
    assert code == 124
    assert "timed out" in err


def test_canonical_selection_and_targeting(tmp_path: Path, repo: Path) -> None:
    other = _init_repo(tmp_path / "watch")
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", local_path=str(repo))
    store.register_clank("watch-clank", local_path=str(other))
    store.register_clank("no-path")
    store.register_clank("ambiguous")
    store.update_ref("ambiguous", "local_path", str(tmp_path / "a"), canonical=True)
    store.update_ref("ambiguous", "local_path", str(tmp_path / "b"), canonical=True)
    store.update_ref("oem-radar", "local_path", str(tmp_path / "dup"), canonical=False)
    seen: list[str] = []

    def observe(path, timeout=None):
        seen.append(str(Path(path).resolve()))
        return observe_local_git_checkout(path, timeout=timeout)

    targeted = harvest_local_git(store, "oem-radar", observe=observe)
    assert targeted["target_count"] == 1
    assert targeted["results"][0]["slug"] == "oem-radar"
    assert all(Path(p).resolve() == repo.resolve() for p in seen)
    seen.clear()
    fleet = harvest_local_git(store, observe=observe)
    slugs = {row["slug"]: row["result"] for row in fleet["results"]}
    assert slugs["no-path"] == RESULT_NO_CANONICAL_PATH
    assert slugs["ambiguous"] == RESULT_AMBIGUOUS_CANONICAL_PATH
    assert slugs["oem-radar"] in {RESULT_OBSERVED_CHANGED, RESULT_OBSERVED_UNCHANGED}
    assert slugs["watch-clank"] in {RESULT_OBSERVED_CHANGED, RESULT_OBSERVED_UNCHANGED}
    assert not any(Path(p).name == "dup" for p in seen)
    assert fleet["errors"] >= 1
    store.conn.close()


def test_dry_run_writes_nothing(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    before = ledger_fingerprint(store)
    packet_before = resume_packet(store, "oem-radar", include_github=False)["context_fingerprint"]
    snap_before = dump_projection_state(store.conn)
    result = harvest_local_git(store, "oem-radar", dry_run=True)
    assert result["dry_run"] is True
    assert result["wrote_events"] is False
    assert result["observed_changed"] == 1
    assert _events(store, EventType.LOCAL_GIT_STATE_OBSERVED) == []
    assert _events(store, EventType.LOCAL_GIT_HARVEST_COMPLETED) == []
    assert ledger_fingerprint(store) == before
    assert dump_projection_state(store.conn) == snap_before
    assert resume_packet(store, "oem-radar", include_github=False)["context_fingerprint"] == packet_before
    store.conn.close()


def test_resume_fingerprint_ignores_identical_harvest_time(tmp_path: Path, repo: Path) -> None:
    store = open_store(
        tmp_path / "h.db",
        actor="cursor",
        clock=TickingClock(T0, timedelta(hours=1)),
    )
    _seed(store, path=str(repo))
    harvest_local_git(store, "oem-radar")
    first = resume_packet(store, "oem-radar", include_github=False)
    harvest_local_git(store, "oem-radar")
    second = resume_packet(store, "oem-radar", include_github=False)
    assert first["local_git_harvest"]["state_fingerprint"] == second["local_git_harvest"]["state_fingerprint"]
    assert first["context_fingerprint"] == second["context_fingerprint"]
    (repo / "changed.txt").write_text("n\n", encoding="utf-8")
    harvest_local_git(store, "oem-radar")
    third = resume_packet(store, "oem-radar", include_github=False)
    assert third["context_fingerprint"] != second["context_fingerprint"]
    store.conn.close()


def test_rebuild_and_failed_check_keeps_last_state(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    harvest_local_git(store, "oem-radar")
    before = dump_projection_state(store.conn)
    rebuild_projections(store.conn)
    after = dump_projection_state(store.conn)
    assert after["local_git_observations"] == before["local_git_observations"]
    assert after["local_git_harvest_runs"] == before["local_git_harvest_runs"]
    assert after["local_git_harvest_results"] == before["local_git_harvest_results"]
    good = local_git_harvest_view(store, "oem-radar")

    def boom(path, timeout=None):
        return {
            "ok": False,
            "result_code": "TIMEOUT",
            "error_class": "TIMEOUT",
            "detail": "git timed out",
            "state": None,
        }

    harvest_local_git(store, "oem-radar", observe=boom)
    view = local_git_harvest_view(store, "oem-radar")
    assert view["latest_result"] == "TIMEOUT"
    assert view["semantic_state"]["head"] == good["semantic_state"]["head"]
    html = _dossier_html(dossier(store, "oem-radar", include_github=False))
    assert "TIMEOUT" in html
    assert good["semantic_state"]["head"][:7] in html or (good["semantic_state"]["head"] or "")[:12] in html
    store.conn.close()


def test_concurrent_identical_and_distinct_ledger_seq(tmp_path: Path, repo: Path) -> None:
    db = tmp_path / "c.db"
    setup = open_store(db, actor="cursor", clock=FrozenClock(T0))
    _seed(setup, path=str(repo))
    setup.conn.close()
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    errors: list[BaseException] = []

    def observe(path, timeout=None):
        result = observe_local_git_checkout(path, timeout=timeout)
        barrier.wait(timeout=10)
        return result

    def worker() -> None:
        store = open_store(db, actor="cursor")
        try:
            harvest_local_git(store, "oem-radar", observe=observe)
        except BaseException as exc:  # noqa: BLE001 — collect for assertion
            with lock:
                errors.append(exc)
        finally:
            store.conn.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
        assert not thread.is_alive()
    assert errors == []
    store = open_store(db, actor="cursor")
    observed = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)
    runs = _events(store, EventType.LOCAL_GIT_HARVEST_COMPLETED)
    assert len(observed) == 1
    assert len(runs) == 2
    seqs = [event.ledger_seq for event in list_events(store.conn)]
    assert len(seqs) == len(set(seqs))
    store.conn.close()


def test_windows_path_and_detached_and_missing_git(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    mixed = str(repo).replace("\\", "/")
    _seed(store, path=mixed)
    harvest_local_git(store, "oem-radar")
    key = checkout_key_for_path(repo)
    event = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)[0]
    assert event.payload["checkout_key"] == key
    _git(repo, ["checkout", "--detach", "HEAD"])
    harvest_local_git(store, "oem-radar")
    detached = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)[-1]
    assert detached.payload["detached"] is True
    assert detached.payload["branch"] is None
    missing = harvest_local_git(store, "oem-radar", git_available=False)
    assert missing["results"][0]["result"] == "ERROR"
    assert missing["errors"] == 1
    store.conn.close()


class _SharedClock:
    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            raise ValueError("clock instants must be timezone-aware UTC")
        self._instant = instant.astimezone(timezone.utc)
        self._lock = threading.Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._instant

    def advance(self, delta: timedelta) -> None:
        with self._lock:
            self._instant = self._instant + delta


HEAD_A = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
HEAD_B = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _ok_observe(head: str, *, branch: str = "main") -> dict:
    return {
        "ok": True,
        "result_code": "OBSERVED",
        "state": {
            "branch": branch,
            "detached": False,
            "head": head,
            "dirty": False,
            "dirty_count": 0,
            "tracked_changes": 0,
            "untracked": 0,
            "upstream": None,
            "upstream_ahead_local": None,
            "upstream_behind_local": None,
            "remotes": [],
            "worktrees": [],
        },
    }


def _fail_observe(code: str, detail: str = "forced failure") -> dict:
    return {
        "ok": False,
        "result_code": code,
        "error_class": code,
        "detail": detail,
        "state": None,
    }


def test_observer_exception_isolated_and_secret_absent(tmp_path: Path, repo: Path) -> None:
    other = _init_repo(tmp_path / "other")
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    store.register_clank("oem-radar", local_path=str(repo))
    store.register_clank("watch-clank", local_path=str(other))

    def observe(path, timeout=None):
        if Path(path).resolve() == repo.resolve():
            raise RuntimeError(f"inspect exploded {SECRET_TOKEN} {SECRET_PAT}")
        return observe_local_git_checkout(path, timeout=timeout)

    result = harvest_local_git(store, observe=observe)
    by_slug = {row["slug"]: row["result"] for row in result["results"]}
    assert by_slug["oem-radar"] == RESULT_ERROR
    assert by_slug["watch-clank"] in {RESULT_OBSERVED_CHANGED, RESULT_OBSERVED_UNCHANGED}
    assert result["errors"] == 1
    assert result["observed_changed"] == 1
    assert len(_events(store, EventType.LOCAL_GIT_STATE_OBSERVED)) == 1
    assert len(_events(store, EventType.LOCAL_GIT_HARVEST_COMPLETED)) == 1
    blob = _blob(
        result,
        format_harvest_text(result),
        _surfaces(store, "oem-radar"),
        _surfaces(store, "watch-clank"),
        format_resume_text(resume_packet(store, "oem-radar", include_github=False)),
        format_resume_text(resume_packet(store, "watch-clank", include_github=False)),
        _dossier_html(dossier(store, "oem-radar", include_github=False)),
        _dossier_html(dossier(store, "watch-clank", include_github=False)),
    )
    _assert_no_secrets(blob)
    store.conn.close()


class _ProcessWide(BaseException):
    pass


def test_observer_baseexception_is_not_swallowed(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))

    def observe(path, timeout=None):
        raise _ProcessWide("process-wide abort")

    with pytest.raises(_ProcessWide):
        harvest_local_git(store, "oem-radar", observe=observe)
    assert _events(store, EventType.LOCAL_GIT_HARVEST_COMPLETED) == []
    store.conn.close()


def test_concurrent_stale_writer_does_not_replace_newer_state(tmp_path: Path, repo: Path) -> None:
    _run_divergent_observation_race(tmp_path, repo, newer_commits_first=True)


def test_concurrent_newer_observation_appends_after_older_commit(tmp_path: Path, repo: Path) -> None:
    _run_divergent_observation_race(tmp_path, repo, newer_commits_first=False)


def _run_divergent_observation_race(
    tmp_path: Path,
    repo: Path,
    *,
    newer_commits_first: bool,
) -> None:
    db = tmp_path / ("race-newer-first.db" if newer_commits_first else "race-older-first.db")
    clock = _SharedClock(T0)
    setup = open_store(db, actor="cursor", clock=FrozenClock(T0))
    _seed(setup, path=str(repo))
    setup.conn.close()
    a_observed = threading.Event()
    b_observed = threading.Event()
    a_committed = threading.Event()
    b_committed = threading.Event()
    lock = threading.Lock()
    errors: list[BaseException] = []
    outcomes: dict[str, dict] = {}

    def observe_a(path, timeout=None):
        return _ok_observe(HEAD_A)

    def observe_b(path, timeout=None):
        assert a_observed.wait(timeout=15)
        clock.advance(timedelta(seconds=1))
        return _ok_observe(HEAD_B)

    def before_a() -> None:
        a_observed.set()
        if newer_commits_first:
            assert b_committed.wait(timeout=15)
        else:
            assert b_observed.wait(timeout=15)

    def before_b() -> None:
        b_observed.set()
        if not newer_commits_first:
            assert a_committed.wait(timeout=15)

    def worker(name: str, observe, before_record) -> None:
        store = open_store(db, actor="cursor", clock=clock)
        try:
            result = harvest_local_git(
                store,
                "oem-radar",
                observe=observe,
                before_record=before_record,
            )
            with lock:
                outcomes[name] = result
        except BaseException as exc:  # noqa: BLE001 — collect for assertion
            with lock:
                errors.append(exc)
        finally:
            if name == "a":
                a_committed.set()
            else:
                b_committed.set()
            store.conn.close()

    thread_a = threading.Thread(target=worker, args=("a", observe_a, before_a))
    thread_b = threading.Thread(target=worker, args=("b", observe_b, before_b))
    thread_a.start()
    thread_b.start()
    for thread in (thread_a, thread_b):
        thread.join(timeout=30)
        assert not thread.is_alive()
    assert errors == []
    assert outcomes["b"]["observed_changed"] == 1
    store = open_store(db, actor="cursor")
    observed = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)
    runs = _events(store, EventType.LOCAL_GIT_HARVEST_COMPLETED)
    assert observed[-1].payload["head"] == HEAD_B
    assert local_git_harvest_view(store, "oem-radar")["semantic_state"]["head"] == HEAD_B
    assert len(runs) == 2
    seqs = [event.ledger_seq for event in list_events(store.conn)]
    assert len(seqs) == len(set(seqs))
    if newer_commits_first:
        assert outcomes["a"]["errors"] == 1
        assert outcomes["a"]["results"][0]["result"] == RESULT_STALE_OBSERVATION
        assert len(observed) == 1
    else:
        assert outcomes["a"]["observed_changed"] == 1
        assert len(observed) == 2
        assert observed[0].payload["head"] == HEAD_A
        assert observed[0].payload["observed_at"] < observed[1].payload["observed_at"]
    store.conn.close()


def test_tied_observation_time_fails_closed(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "tie.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    harvest_local_git(store, "oem-radar", observe=lambda path, timeout=None: _ok_observe(HEAD_A))
    view = local_git_harvest_view(store, "oem-radar")
    payload = semantic_state_payload(
        clank_id=view["clank_id"],
        checkout_key=view["checkout_key"],
        checkout_path=view["checkout_path"],
        observed=_ok_observe(HEAD_B)["state"],
    )
    outcome, event = _record_state_observation(
        store,
        actor="cursor",
        payload=payload,
        fingerprint=state_fingerprint(payload),
        observed_at=view["state_observed_at"],
        expected_generation=0,
    )
    assert outcome == WRITE_STALE
    assert event is None
    assert local_git_harvest_view(store, "oem-radar")["semantic_state"]["head"] == HEAD_A
    store.conn.close()


def test_newer_observation_time_appends_after_older_commit(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "newer.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    harvest_local_git(store, "oem-radar", observe=lambda path, timeout=None: _ok_observe(HEAD_A))
    view = local_git_harvest_view(store, "oem-radar")
    payload = semantic_state_payload(
        clank_id=view["clank_id"],
        checkout_key=view["checkout_key"],
        checkout_path=view["checkout_path"],
        observed=_ok_observe(HEAD_B)["state"],
    )
    outcome, event = _record_state_observation(
        store,
        actor="cursor",
        payload=payload,
        fingerprint=state_fingerprint(payload),
        observed_at=isoformat_utc(T0 + timedelta(seconds=1)),
        expected_generation=0,
    )
    assert outcome == WRITE_CHANGED
    assert event is not None
    assert local_git_harvest_view(store, "oem-radar")["semantic_state"]["head"] == HEAD_B
    store.conn.close()


def test_resume_fingerprint_notices_failed_evidence(tmp_path: Path, repo: Path) -> None:
    store = open_store(
        tmp_path / "h.db",
        actor="cursor",
        clock=TickingClock(T0, timedelta(hours=1)),
    )
    _seed(store, path=str(repo))

    def success(path, timeout=None):
        return _ok_observe(HEAD_A)

    harvest_local_git(store, "oem-radar", observe=success)
    first = resume_packet(store, "oem-radar", include_github=False)
    harvest_local_git(store, "oem-radar", observe=success)
    second = resume_packet(store, "oem-radar", include_github=False)
    assert first["context_fingerprint"] == second["context_fingerprint"]
    assert first["local_git_harvest"]["latest_failure"] is None

    def timed_out(path, timeout=None):
        return _fail_observe(RESULT_TIMEOUT, "git timed out")

    harvest_local_git(store, "oem-radar", observe=timed_out)
    timeout_packet = resume_packet(store, "oem-radar", include_github=False)
    assert timeout_packet["context_fingerprint"] != second["context_fingerprint"]
    assert timeout_packet["local_git_harvest"]["latest_failure"] == RESULT_TIMEOUT
    harvest_local_git(store, "oem-radar", observe=timed_out)
    timeout_again = resume_packet(store, "oem-radar", include_github=False)
    assert timeout_again["context_fingerprint"] == timeout_packet["context_fingerprint"]

    previous = timeout_again["context_fingerprint"]
    for code in (RESULT_ERROR, RESULT_PATH_MISSING, RESULT_NOT_A_GIT_REPOSITORY):
        def fail(path, timeout=None, result_code=code):
            return _fail_observe(result_code)

        harvest_local_git(store, "oem-radar", observe=fail)
        packet = resume_packet(store, "oem-radar", include_github=False)
        assert packet["local_git_harvest"]["latest_failure"] == code
        assert packet["context_fingerprint"] != previous
        harvest_local_git(store, "oem-radar", observe=fail)
        again = resume_packet(store, "oem-radar", include_github=False)
        assert again["context_fingerprint"] == packet["context_fingerprint"]
        previous = packet["context_fingerprint"]
    store.conn.close()


def test_noncanonical_ref_does_not_emit_local_git_observation(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    first = harvest_local_git(store, "oem-radar")
    assert first["observed_changed"] == 1
    assert len(_events(store, EventType.LOCAL_GIT_STATE_OBSERVED)) == 1
    store.update_ref("oem-radar", "local_path", str(tmp_path / "dup"), canonical=False)
    second = harvest_local_git(store, "oem-radar")
    assert second["observed_unchanged"] == 1
    assert len(_events(store, EventType.LOCAL_GIT_STATE_OBSERVED)) == 1
    event = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)[0]
    assert "alternate_checkouts" not in event.payload
    assert second["results"][0]["alternate_checkouts"]
    view = local_git_harvest_view(store, "oem-radar")
    assert view["alternate_checkouts"]
    assert "alternate_checkouts" not in (view.get("semantic_state") or {})
    store.conn.close()


def test_terminal_source_unknown_without_observation(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    view = local_git_harvest_view(store, "oem-radar")
    assert view["source"] is None
    assert view["never_harvested"] is True
    html = _dossier_html(dossier(store, "oem-radar", include_github=False))
    assert "<th>source</th><td>" in html
    assert "[UNKNOWN]" in html
    packet = resume_packet(store, "oem-radar", include_github=False)
    assert packet["local_git_harvest"]["source"] is None
    assert "never harvested" in format_resume_text(packet)
    harvest_local_git(store, "oem-radar")
    observed = local_git_harvest_view(store, "oem-radar")
    assert observed["source"] == EventSource.LOCAL_GIT
    html_observed = _dossier_html(dossier(store, "oem-radar", include_github=False))
    assert "[LOCAL_GIT]" in html_observed
    text = format_resume_text(resume_packet(store, "oem-radar", include_github=False))
    assert "source=LOCAL_GIT" in text
    store.conn.close()


def test_cli_json_and_source_authority(tmp_path: Path, repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "cli.db"
    store = open_store(db, actor="user", clock=FrozenClock(T0))
    _seed(store, path=str(repo))
    store.conn.close()
    code = main(["--db", str(db), "--actor", "user", "--source", "GITHUB", "--json", "harvest", "local-git", "oem-radar"])
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["source_authority"]["observation"] == EventSource.LOCAL_GIT
    assert payload["source_authority"]["harvest_run"] == EventSource.SYSTEM
    _assert_no_secrets(json.dumps(payload))
    store = open_store(db, actor="user")
    observed = _events(store, EventType.LOCAL_GIT_STATE_OBSERVED)[0]
    run = _events(store, EventType.LOCAL_GIT_HARVEST_COMPLETED)[0]
    assert observed.source == EventSource.LOCAL_GIT
    assert run.source == EventSource.SYSTEM
    store.conn.close()
    unknown = main(["--db", str(db), "harvest", "local-git", "no-such-clank"])
    assert unknown == 2


def test_non_conflation(tmp_path: Path, repo: Path) -> None:
    store = open_store(tmp_path / "h.db", actor="cursor", clock=FrozenClock(T0))
    mission = _seed(store, path=str(repo), branch="main")
    clank_count = len(store.list_clanks())
    harvest_local_git(store, "oem-radar")
    (repo / "wip.txt").write_text("dirty\n", encoding="utf-8")
    harvest_local_git(store, "oem-radar")
    wt = tmp_path / "extra-wt"
    _git(repo, ["worktree", "add", str(wt), "-b", "extra"])
    harvest_local_git(store, "oem-radar")
    row = store.resolve_mission(mission["display_id"])
    assert row["state"] == MissionState.ACTIVE
    assert store.resolve_clank("oem-radar")["lifecycle"] != "ABANDONED"
    assert len(store.list_clanks()) == clank_count
    assert _events(store, EventType.HANDOFF_RECORDED) == []
    assert _events(store, EventType.DEPLOYMENT_OBSERVED) == []
    assert _events(store, EventType.MISSION_STATE_RECONCILED) == []
    packet = resume_packet(store, "oem-radar", include_github=False)
    rec = packet.get("reconcile") or {}
    assert rec.get("status") != "aligned" or packet["local_git_harvest"]["semantic_state"]["head"]
    assert packet["local_git_harvest"]["source"] == EventSource.LOCAL_GIT
    store.conn.close()
