"""Read-only Git inspection. Never mutates a repository."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from clankops.githubinspect import github_repo_id
from clankops.redact import redact_url, sanitize_text

GIT_TIMEOUT_SEC = 30
_GIT_SAFE_ENV = {
    "GIT_TERMINAL_PROMPT": "0",
    "GIT_OPTIONAL_LOCKS": "0",
}
_SCP_REMOTE = re.compile(r"^(?P<user>[^/@]+)@(?P<host>[^:]+):(?P<path>.+)$")
_UNSAFE_REMOTE = re.compile(
    r"(?i)(://[^/\s]+:[^/\s]+@)|([?&](token|password|passwd|secret|access[_-]?token|bearer)=)"
    r"|\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_"
)
_FORBIDDEN_HARVEST_VERBS = frozenset(
    {"fetch", "pull", "push", "clone", "ls-remote", "remote-update"}
)
RESULT_PATH_MISSING = "PATH_MISSING"
RESULT_NOT_A_GIT_REPOSITORY = "NOT_A_GIT_REPOSITORY"
RESULT_TIMEOUT = "TIMEOUT"
RESULT_ERROR = "ERROR"
RESULT_OBSERVED = "OBSERVED"


def run_git(
    cwd: str | Path,
    args: list[str],
    *,
    timeout: float | int | None = GIT_TIMEOUT_SEC,
) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.update(_GIT_SAFE_ENV)
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
            shell=False,
            env=env,
        )
        return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "git executable not found"
    except subprocess.TimeoutExpired:
        return 124, "", "git timed out"
    except OSError as exc:
        return 1, "", str(exc)


def git_executable_available(*, timeout: float | int | None = GIT_TIMEOUT_SEC) -> bool:
    code, _, _ = run_git(".", ["--version"], timeout=timeout)
    return code != 127


def harvest_run_git(
    cwd: str | Path,
    args: list[str],
    *,
    timeout: float | int | None = GIT_TIMEOUT_SEC,
) -> tuple[int, str, str]:
    """Git invocation for Fleet Harvest. Local only. Never fetch/pull/push."""
    verbs = [part for part in args if not str(part).startswith("-")]
    if any(verb in _FORBIDDEN_HARVEST_VERBS for verb in verbs):
        raise RuntimeError(f"harvest refuses network git verb: {verbs[:1]}")
    return run_git(cwd, ["-c", "core.hooksPath=", *args], timeout=timeout)


def _bounded_detail(text: str | None, *, limit: int = 240) -> str | None:
    cleaned = sanitize_text(text or "") or ""
    cleaned = cleaned.replace("\r", " ").replace("\n", " ").strip()
    if not cleaned:
        return None
    if len(cleaned) > limit:
        return cleaned[: limit - 3] + "..."
    return cleaned


def _remote_looks_unsafe(url: str) -> bool:
    if _UNSAFE_REMOTE.search(url):
        return True
    try:
        parts = urlsplit(url)
    except ValueError:
        return True
    if parts.password:
        return True
    if parts.username and parts.username.lower() not in {"git", "gitolite"}:
        return True
    if parts.query:
        return True
    return False


def normalize_remote_identity(url: str | None) -> dict[str, Any]:
    """Safe remote identity. Never persist userinfo, tokens, or query secrets."""
    if not url or not str(url).strip():
        return {"present": False, "redacted": False, "identity": None}
    raw = str(url).strip()
    unsafe = _remote_looks_unsafe(raw)
    identity = _safe_remote_identity(raw)
    if not identity:
        return {"present": True, "redacted": True, "identity": None}
    return {"present": True, "redacted": unsafe, "identity": identity}


def _safe_remote_identity(raw: str) -> str | None:
    gh = github_repo_id(raw)
    if gh:
        return f"github.com/{gh}"
    text = raw.strip()
    scp = _SCP_REMOTE.match(text)
    if scp and "://" not in text:
        host = scp.group("host").strip()
        path = scp.group("path").removesuffix(".git").strip("/")
        if not host or not path or _remote_looks_unsafe(host) or _remote_looks_unsafe(path):
            return None
        if gh_host := github_repo_id(f"{host}/{path}"):
            return f"github.com/{gh_host}"
        if "github.com" in host.lower():
            return f"github.com/{path}"
        return f"{host}/{path}"
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    host = parts.hostname
    if not host:
        return None
    path = (parts.path or "").removesuffix(".git").strip("/")
    gh = github_repo_id(f"{host}/{path}" if path else host)
    if gh:
        return f"github.com/{gh}"
    if path:
        return f"{host}/{path}"
    return host


def parse_worktree_porcelain(text: str | None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def _flush() -> None:
        nonlocal current
        if current:
            records.append(current)
            current = None

    for line in (text or "").splitlines():
        if not line.strip():
            _flush()
            continue
        if line.startswith("worktree "):
            _flush()
            current = {
                "path": line[len("worktree ") :],
                "head": None,
                "branch": None,
                "detached": False,
                "locked": False,
                "prunable": False,
                "main_worktree": False,
            }
            continue
        if current is None:
            continue
        if line.startswith("HEAD "):
            current["head"] = line[5:].strip() or None
        elif line.startswith("branch "):
            ref = line[7:].strip()
            current["branch"] = ref.removeprefix("refs/heads/") or None
            current["detached"] = False
        elif line == "detached":
            current["detached"] = True
            current["branch"] = None
        elif line.startswith("locked"):
            current["locked"] = True
        elif line.startswith("prunable"):
            current["prunable"] = True
    _flush()
    if records:
        records[0]["main_worktree"] = True
    return records


def observe_local_git_checkout(
    path: str | Path,
    *,
    timeout: float | int | None = GIT_TIMEOUT_SEC,
    runner=None,
) -> dict[str, Any]:
    """Harvest observation of one checkout. Counts, not dirty paths. No fetch."""
    run = runner or harvest_run_git
    root = Path(path)
    if not root.exists():
        return {
            "ok": False,
            "result_code": RESULT_PATH_MISSING,
            "error_class": RESULT_PATH_MISSING,
            "detail": "canonical path unavailable",
            "state": None,
        }
    if not root.is_dir():
        return {
            "ok": False,
            "result_code": RESULT_NOT_A_GIT_REPOSITORY,
            "error_class": RESULT_NOT_A_GIT_REPOSITORY,
            "detail": "canonical path is not a directory",
            "state": None,
        }

    def _call(args: list[str]) -> tuple[int, str, str]:
        return run(root, args, timeout=timeout)

    code, out, err = _call(["rev-parse", "--is-inside-work-tree"])
    if code == 127:
        return {
            "ok": False,
            "result_code": RESULT_ERROR,
            "error_class": "GIT_MISSING",
            "detail": _bounded_detail(err or "git executable not found"),
            "state": None,
        }
    if code == 124:
        return {
            "ok": False,
            "result_code": RESULT_TIMEOUT,
            "error_class": RESULT_TIMEOUT,
            "detail": _bounded_detail(err or "git timed out"),
            "state": None,
        }
    if code != 0 or (out or "").strip().lower() != "true":
        return {
            "ok": False,
            "result_code": RESULT_NOT_A_GIT_REPOSITORY,
            "error_class": RESULT_NOT_A_GIT_REPOSITORY,
            "detail": _bounded_detail(err or out or "not a git repository"),
            "state": None,
        }

    state: dict[str, Any] = {
        "checkout_path": str(root),
        "repository_present": True,
        "branch": None,
        "detached": None,
        "head": None,
        "dirty": None,
        "dirty_count": None,
        "tracked_changes": None,
        "untracked": None,
        "upstream": None,
        "upstream_ahead_local": None,
        "upstream_behind_local": None,
        "remotes": [],
        "worktrees": [],
    }

    code, out, err = _call(["rev-parse", "--abbrev-ref", "HEAD"])
    if code == 124:
        return {
            "ok": False,
            "result_code": RESULT_TIMEOUT,
            "error_class": RESULT_TIMEOUT,
            "detail": _bounded_detail(err or "git timed out"),
            "state": None,
        }
    if code == 0:
        branch = out.strip() or None
        state["detached"] = branch in {None, "HEAD"}
        state["branch"] = None if state["detached"] else branch
    code, out, err = _call(["rev-parse", "HEAD"])
    if code == 124:
        return {
            "ok": False,
            "result_code": RESULT_TIMEOUT,
            "error_class": RESULT_TIMEOUT,
            "detail": _bounded_detail(err or "git timed out"),
            "state": None,
        }
    if code == 0:
        state["head"] = out.strip() or None

    code, out, err = _call(["status", "--porcelain=v1", "-b"])
    if code == 124:
        return {
            "ok": False,
            "result_code": RESULT_TIMEOUT,
            "error_class": RESULT_TIMEOUT,
            "detail": _bounded_detail(err or "git timed out"),
            "state": None,
        }
    if code == 0:
        lines = [ln for ln in (out or "").splitlines() if ln.strip()]
        dirty_lines = [ln for ln in lines if not ln.startswith("##")]
        state["dirty"] = bool(dirty_lines)
        state["dirty_count"] = len(dirty_lines)
        tracked = 0
        untracked = 0
        for line in dirty_lines:
            if line.startswith("??"):
                untracked += 1
            else:
                tracked += 1
        state["tracked_changes"] = tracked
        state["untracked"] = untracked
    else:
        state["dirty"] = None

    code, out, err = _call(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if code == 124:
        return {
            "ok": False,
            "result_code": RESULT_TIMEOUT,
            "error_class": RESULT_TIMEOUT,
            "detail": _bounded_detail(err or "git timed out"),
            "state": None,
        }
    if code == 0 and out.strip():
        state["upstream"] = out.strip()
        acode, aout, aerr = _call(["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
        if acode == 124:
            return {
                "ok": False,
                "result_code": RESULT_TIMEOUT,
                "error_class": RESULT_TIMEOUT,
                "detail": _bounded_detail(aerr or "git timed out"),
                "state": None,
            }
        if acode == 0 and aout.strip():
            left, _, right = aout.strip().partition("\t")
            if not right:
                left, _, right = aout.strip().partition(" ")
            try:
                state["upstream_behind_local"] = int(left)
                state["upstream_ahead_local"] = int(right)
            except ValueError:
                state["upstream_behind_local"] = None
                state["upstream_ahead_local"] = None
    else:
        state["upstream"] = None
        state["upstream_ahead_local"] = None
        state["upstream_behind_local"] = None

    code, out, err = _call(["remote", "-v"])
    if code == 124:
        return {
            "ok": False,
            "result_code": RESULT_TIMEOUT,
            "error_class": RESULT_TIMEOUT,
            "detail": _bounded_detail(err or "git timed out"),
            "state": None,
        }
    remotes: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None]] = set()
    if code == 0:
        for line in (out or "").splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name, url = parts[0], parts[1]
            ident = normalize_remote_identity(url)
            key = (name, ident.get("identity"))
            if key in seen:
                continue
            seen.add(key)
            remotes.append(
                {
                    "name": name,
                    "identity": ident.get("identity"),
                    "redacted": bool(ident.get("redacted")),
                    "present": True,
                }
            )
    state["remotes"] = remotes

    code, out, err = _call(["worktree", "list", "--porcelain"])
    if code == 124:
        return {
            "ok": False,
            "result_code": RESULT_TIMEOUT,
            "error_class": RESULT_TIMEOUT,
            "detail": _bounded_detail(err or "git timed out"),
            "state": None,
        }
    if code == 0:
        state["worktrees"] = parse_worktree_porcelain(out)

    return {
        "ok": True,
        "result_code": RESULT_OBSERVED,
        "error_class": None,
        "detail": None,
        "state": state,
    }


def inspect_git(path: str | Path) -> dict[str, Any]:
    """Inspect a directory for Git metadata without altering it.

    Dirty state is recorded, never cleaned. Ownership / safe.directory
    failures are captured as evidence rather than auto-fixed.
    """
    root = Path(path)
    result: dict[str, Any] = {
        "is_git": False,
        "git_error": None,
        "remotes": {},
        "canonical_remote": None,
        "default_branch": None,
        "current_branch": None,
        "head": None,
        "dirty": None,
        "dirty_count": 0,
        "dirty_sample": [],
        "latest_commit_timestamp": None,
        "latest_commit_subject": None,
        "latest_commit_author": None,
        "branches": [],
        "local_branches": [],
        "worktrees": None,
        "origin_head_ref": None,
    }
    git_dir = root / ".git"
    if not git_dir.exists():
        return result
    result["is_git"] = True
    code, out, err = run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if code != 0:
        result["git_error"] = err or out or f"git rev-parse failed ({code})"
        return result

    code, out, err = run_git(root, ["remote", "-v"])
    remotes: dict[str, list[dict[str, str]]] = {}
    if code == 0:
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                name, url = parts[0], redact_url(parts[1]) or parts[1]
                kind = parts[2] if len(parts) > 2 else ""
                remotes.setdefault(name, []).append({"url": url, "kind": kind})
    result["remotes"] = remotes
    if "origin" in remotes:
        result["canonical_remote"] = remotes["origin"][0]["url"]
    elif remotes:
        first = next(iter(remotes.values()))
        result["canonical_remote"] = first[0]["url"]

    code, out, _ = run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if code == 0:
        result["current_branch"] = out
    code, out, _ = run_git(root, ["rev-parse", "HEAD"])
    if code == 0:
        result["head"] = out
    code, out, _ = run_git(root, ["log", "-1", "--format=%cI%x09%s%x09%an"])
    if code == 0 and out:
        bits = out.split("\t", 2)
        result["latest_commit_timestamp"] = bits[0] or None
        result["latest_commit_subject"] = bits[1] if len(bits) > 1 else None
        result["latest_commit_author"] = bits[2] if len(bits) > 2 else None
    code, out, _ = run_git(root, ["symbolic-ref", "refs/remotes/origin/HEAD"])
    if code == 0 and out:
        result["origin_head_ref"] = out
        result["default_branch"] = out.rsplit("/", 1)[-1]
    code, out, _ = run_git(root, ["branch", "-a", "--format=%(refname:short)"])
    if code == 0:
        result["branches"] = [b for b in out.splitlines() if b]
    code, out, _ = run_git(
        root,
        ["branch", "--format=%(refname:short)|%(committerdate:iso-strict)|%(upstream:short)"],
    )
    if code == 0:
        local = []
        for line in out.splitlines():
            name, _, rest = line.partition("|")
            date, _, upstream = rest.partition("|")
            local.append(
                {
                    "name": name,
                    "committerdate": date or None,
                    "upstream": upstream or None,
                }
            )
        result["local_branches"] = local
    code, out, _ = run_git(root, ["status", "--porcelain=v1", "-b"])
    if code == 0:
        lines = out.splitlines()
        result["status_branch_line"] = lines[0] if lines else ""
        dirty_lines = [ln for ln in lines[1:] if ln.strip()]
        result["dirty"] = bool(dirty_lines)
        result["dirty_count"] = len(dirty_lines)
        result["dirty_sample"] = dirty_lines[:20]
    else:
        result["dirty"] = None
    code, out, _ = run_git(root, ["worktree", "list", "--porcelain"])
    if code == 0:
        result["worktrees"] = out or None
    code, out, _ = run_git(root, ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"])
    if code == 0 and out:
        result["upstream"] = out
        acode, aout, _ = run_git(root, ["rev-list", "--left-right", "--count", "@{upstream}...HEAD"])
        if acode == 0 and aout:
            left, _, right = aout.strip().partition("\t")
            if not right:
                left, _, right = aout.strip().partition(" ")
            try:
                result["behind"] = int(left)
                result["ahead"] = int(right)
            except ValueError:
                result["behind"] = None
                result["ahead"] = None
    if result["default_branch"] is None and result["current_branch"] not in {None, "HEAD"}:
        result["default_branch"] = result["current_branch"]
    return result
