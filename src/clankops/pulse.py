"""Fleet Pulse 1: deterministic Windows harvest-task specification.

This module schedules observation. It does not interpret work, mutate
Missions, or talk to Task Scheduler. The PowerShell installer consumes
the spec. Importing this package does not install a task.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from clankops.errors import ValidationError
from clankops.redact import sanitize_text

DEFAULT_INTERVAL_MINUTES = 10
MIN_INTERVAL_MINUTES = 1
MAX_INTERVAL_MINUTES = 1440
CANONICAL_TASK_NAME = "ClankOps Fleet Harvest"
MULTIPLE_INSTANCES = "IgnoreNew"

CLI_TAIL = ("harvest", "local-git")
_STANDALONE_FORBIDDEN = frozenset(
    {"github", "fetch", "pull", "push", "clone", "ls-remote", "ssh"}
)
_BOOTSTRAP_FORBIDDEN = re.compile(
    r"\b(github|fetch|pull|push|clone|ls-remote|ssh)\b",
    re.I,
)
_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "sys.argv = [sys.argv[0], *sys.argv[2:]]; "
    "from clankops.cli import main; raise SystemExit(main())"
)
_HEALTH_RE = re.compile(r"health", re.I)


def default_db_path(*, environ: dict[str, str] | None = None, home: Path | None = None) -> Path:
    env = environ if environ is not None else os.environ
    raw = (env.get("CLANKOPS_DB") or "").strip()
    if raw:
        return Path(raw).expanduser()
    base = home if home is not None else Path.home()
    return base / ".clankops" / "clankops.db"


def package_sys_path_entry() -> Path:
    import clankops

    return Path(clankops.__file__).resolve().parent.parent


_WIN_ABS = re.compile(r"^[A-Za-z]:[\\/]")


def resolve_operational_path(path: str | Path) -> str:
    """Absolutize a path without requiring it to exist.

    Windows drive-letter paths stay Windows-shaped even when tests run on POSIX,
    so quoting coverage for Program Files / spaced USERPROFILE is portable.
    """
    text = os.path.expanduser(str(path))
    if _WIN_ABS.match(text):
        return os.path.normpath(text.replace("/", "\\"))
    raw = Path(text)
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    return os.path.normpath(str(raw))


def parse_interval_minutes(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise ValidationError("harvest pulse interval must be an integer number of minutes")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValidationError("harvest pulse does not support sub-minute scheduling")
        value = int(value)
    text = str(value).strip()
    if not text or not re.fullmatch(r"[+-]?\d+", text):
        if re.search(r"[.\s:/]|ms|sec|second|hour|\d+s$", text, re.I):
            raise ValidationError("harvest pulse does not support sub-minute scheduling")
        raise ValidationError("harvest pulse interval must be an integer number of minutes")
    minutes = int(text)
    if minutes < MIN_INTERVAL_MINUTES:
        raise ValidationError("harvest pulse interval must be at least 1 minute")
    if minutes > MAX_INTERVAL_MINUTES:
        raise ValidationError(
            f"harvest pulse interval must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES} minutes"
        )
    return minutes


def quote_windows_arg(arg: str) -> str:
    """Quote one argument for CommandLineToArgvW / CreateProcess."""
    if not arg:
        return '""'
    if not re.search(r'[\s"]', arg):
        return arg
    buf: list[str] = ['"']
    backslashes = 0
    for ch in arg:
        if ch == "\\":
            backslashes += 1
            continue
        if ch == '"':
            buf.append("\\" * (backslashes * 2 + 1))
            buf.append('"')
            backslashes = 0
            continue
        if backslashes:
            buf.append("\\" * backslashes)
            backslashes = 0
        buf.append(ch)
    buf.append("\\" * (backslashes * 2))
    buf.append('"')
    return "".join(buf)


def join_windows_args(argv: list[str]) -> str:
    return " ".join(quote_windows_arg(part) for part in argv)


def _secret_shaped(text: str) -> bool:
    cleaned = sanitize_text(text) or ""
    return cleaned != text or "[redacted]" in cleaned.lower()


def _assert_command_is_local_harvest(parts: list[str]) -> None:
    if len(parts) < 2 or parts[-2:] != ["harvest", "local-git"]:
        raise ValidationError("harvest pulse command must invoke harvest local-git")
    standalone = {part.lower() for part in parts}
    if standalone & _STANDALONE_FORBIDDEN:
        raise ValidationError("harvest pulse command must not include network or GitHub verbs")
    bootstrap = parts[1] if len(parts) > 1 and parts[0] == "-c" else ""
    if bootstrap and _BOOTSTRAP_FORBIDDEN.search(bootstrap):
        raise ValidationError("harvest pulse command must not include network or GitHub verbs")
    for part in parts:
        if _secret_shaped(part):
            raise ValidationError("harvest pulse command must not contain secret-shaped values")


def build_argument_vector(*, db_path: str | Path, sys_path_entry: str | Path) -> list[str]:
    argv = [
        "-c",
        _BOOTSTRAP,
        str(sys_path_entry),
        "--db",
        str(db_path),
        *CLI_TAIL,
    ]
    _assert_command_is_local_harvest(argv)
    return argv


def build_task_spec(
    *,
    interval_minutes: Any = DEFAULT_INTERVAL_MINUTES,
    python_exe: str | Path | None = None,
    db_path: str | Path | None = None,
    task_name: str = CANONICAL_TASK_NAME,
    environ: dict[str, str] | None = None,
    home: Path | None = None,
    sys_path_entry: str | Path | None = None,
) -> dict[str, Any]:
    """Pure spec. Does not consult or mutate Task Scheduler."""
    name = (task_name or "").strip() or CANONICAL_TASK_NAME
    if name != CANONICAL_TASK_NAME:
        raise ValidationError(f"canonical harvest pulse task name is {CANONICAL_TASK_NAME!r}")
    minutes = parse_interval_minutes(interval_minutes)
    python = resolve_operational_path(python_exe if python_exe is not None else sys.executable)
    db = resolve_operational_path(
        db_path if db_path is not None else default_db_path(environ=environ, home=home)
    )
    src = resolve_operational_path(sys_path_entry if sys_path_entry is not None else package_sys_path_entry())
    argument_vector = build_argument_vector(db_path=db, sys_path_entry=src)
    argument_string = join_windows_args(argument_vector)
    spec = {
        "task_name": CANONICAL_TASK_NAME,
        "interval_minutes": minutes,
        "python_exe": python,
        "db_path": db,
        "sys_path_entry": src,
        "execute": python,
        "argument_vector": argument_vector,
        "argument_string": argument_string,
        "environment": [],
        "multiple_instances": MULTIPLE_INSTANCES,
        "start_when_available": True,
        "replay_missed_intervals": False,
        "disallow_start_if_on_batteries": False,
        "stop_if_going_on_batteries": False,
        "wake_to_run": False,
        "run_only_if_logged_on": True,
        "allow_demand_start": True,
        "store_password": False,
        "run_level": "Limited",
        "logon_type": "Interactive",
        "one_shot": True,
        "network": False,
        "command_identity": {
            "module": "clankops",
            "argv_tail": ["--db", db, *CLI_TAIL],
        },
        "settings": {
            "MultipleInstances": MULTIPLE_INSTANCES,
            "StartWhenAvailable": True,
            "DisallowStartIfOnBatteries": False,
            "StopIfGoingOnBatteries": False,
            "WakeToRun": False,
            "RunOnlyIfNetworkAvailable": False,
            "AllowDemandStart": True,
            "ExecutionTimeLimitMinutes": 60,
        },
    }
    rendered = join_windows_args([python, *argument_vector])
    if _secret_shaped(rendered):
        raise ValidationError("harvest pulse spec refused to persist a secret-shaped value")
    return spec


def identity_fields(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_name": spec.get("task_name"),
        "execute": spec.get("execute"),
        "argument_string": spec.get("argument_string"),
        "interval_minutes": spec.get("interval_minutes"),
        "multiple_instances": spec.get("multiple_instances"),
    }


def plan_install(existing: dict[str, Any] | None, spec: dict[str, Any]) -> dict[str, Any]:
    """Idempotent install plan. Never talks to Task Scheduler."""
    desired = identity_fields(spec)
    if existing is None:
        return {"action": "create", "changed_fields": sorted(desired), "spec": spec}
    current_name = (existing.get("task_name") or "").strip()
    if current_name and current_name != CANONICAL_TASK_NAME:
        raise ValidationError(f"refusing to mutate non-canonical task {current_name!r}")
    current = identity_fields({**desired, **existing, "task_name": current_name or CANONICAL_TASK_NAME})
    changed = [key for key, value in desired.items() if current.get(key) != value]
    if not changed:
        return {"action": "unchanged", "changed_fields": [], "spec": spec}
    return {"action": "replace", "changed_fields": changed, "spec": spec}


def plan_remove(existing: dict[str, Any] | None) -> dict[str, Any]:
    if existing is None:
        return {"action": "absent", "task_name": CANONICAL_TASK_NAME}
    name = (existing.get("task_name") or "").strip() or CANONICAL_TASK_NAME
    if name != CANONICAL_TASK_NAME:
        raise ValidationError(f"refusing to remove non-canonical task {name!r}")
    return {"action": "remove", "task_name": CANONICAL_TASK_NAME}


def load_existing_json(raw: str | None) -> dict[str, Any] | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text or text in {"null", "none", "{}"}:
        return None
    data = json.loads(text)
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValidationError("existing task facts must be a JSON object")
    return data


def format_spec_text(spec: dict[str, Any]) -> str:
    lines = [
        f"task name: {spec['task_name']}",
        f"cadence: every {spec['interval_minutes']} minutes",
        f"python executable: {spec['python_exe']}",
        f"database: {spec['db_path']}",
        f"overlap: {spec['multiple_instances']} (do not start a second instance)",
        "missed runs: start when available; do not replay every missed interval",
        f"execute: {spec['execute']}",
        f"arguments: {spec['argument_string']}",
        "environment: (none persisted)",
        "one-shot: yes (process exits when harvest completes)",
        "network: no",
        "authority: observation only",
    ]
    text = "\n".join(lines)
    if _HEALTH_RE.search(text):
        raise ValidationError("pulse spec text must not describe scheduler facts as health")
    return text


def format_status_text(facts: dict[str, Any]) -> str:
    installed = bool(facts.get("installed"))
    lines = [
        f"installed: {'yes' if installed else 'no'}",
        f"task name: {facts.get('task_name') or CANONICAL_TASK_NAME}",
    ]
    if installed:
        cadence = facts.get("interval_minutes")
        lines.extend(
            [
                f"cadence: every {cadence} minutes" if cadence is not None else "cadence: unknown",
                f"next run: {facts.get('next_run') or 'unknown'}",
                f"last run: {facts.get('last_run') or 'unknown'}",
                f"last task result: {facts.get('last_task_result') if facts.get('last_task_result') is not None else 'unknown'}",
                f"command identity: {facts.get('command_identity') or 'unknown'}",
                f"overlap: {facts.get('multiple_instances') or 'unknown'}",
            ]
        )
    text = "\n".join(str(item) for item in lines)
    if _HEALTH_RE.search(text):
        raise ValidationError("pulse status must not describe scheduler facts as health")
    return text


def format_plan_text(plan: dict[str, Any]) -> str:
    action = plan.get("action")
    changed = plan.get("changed_fields") or []
    spec = plan.get("spec") or {}
    if action == "unchanged":
        body = f"install plan: unchanged ({spec.get('task_name') or CANONICAL_TASK_NAME})"
    elif action == "create":
        body = f"install plan: create {spec.get('task_name') or CANONICAL_TASK_NAME}"
    elif action == "replace":
        body = (
            f"install plan: replace {spec.get('task_name') or CANONICAL_TASK_NAME}; "
            f"changed fields: {', '.join(changed)}"
        )
    elif action == "absent":
        body = f"remove plan: no task named {plan.get('task_name') or CANONICAL_TASK_NAME}"
    elif action == "remove":
        body = f"remove plan: unregister {plan.get('task_name') or CANONICAL_TASK_NAME}"
    else:
        body = f"plan: {action}"
    if _HEALTH_RE.search(body):
        raise ValidationError("pulse plan text must not describe scheduler facts as health")
    return body
