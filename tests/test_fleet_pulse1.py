"""Fleet Pulse 1: scheduled local harvest spec — no host Task Scheduler mutation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from clankops.cli import main
from clankops.errors import ValidationError
from clankops.pulse import (
    CANONICAL_TASK_NAME,
    DEFAULT_INTERVAL_MINUTES,
    build_task_spec,
    default_db_path,
    format_status_text,
    join_windows_args,
    parse_interval_minutes,
    plan_install,
    plan_remove,
    quote_windows_arg,
    safety_contract,
)

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "clankops-harvest-task.ps1"
HARVEST_SCRIPT = ROOT / "src" / "clankops" / "harvest.py"
TERMINAL_SCRIPT = ROOT / "src" / "clankops" / "terminal.py"


def _spec(**overrides):
    kwargs = {
        "python_exe": r"C:\Program Files\Python 3.14\python.exe",
        "db_path": r"C:\Users\Anil Ganti\.clankops\clankops.db",
        "sys_path_entry": r"C:\Users\Anil Ganti\Clanks\clankops\src",
    }
    kwargs.update(overrides)
    return build_task_spec(**kwargs)


def test_default_cadence_is_ten_minutes() -> None:
    spec = _spec()
    assert spec["interval_minutes"] == DEFAULT_INTERVAL_MINUTES == 10
    assert parse_interval_minutes(DEFAULT_INTERVAL_MINUTES) == 10


def test_custom_cadence_accepted() -> None:
    spec = _spec(interval_minutes=25)
    assert spec["interval_minutes"] == 25
    assert parse_interval_minutes("1440") == 1440
    assert parse_interval_minutes(1) == 1


@pytest.mark.parametrize("value", ["0.5", 0.5, "30s", "30 sec", "500ms"])
def test_sub_minute_cadence_rejected(value) -> None:
    with pytest.raises(ValidationError, match="sub-minute"):
        parse_interval_minutes(value)


@pytest.mark.parametrize("value", [0, -1, 1441, 100000, "0", "99999"])
def test_absurd_cadence_rejected(value) -> None:
    with pytest.raises(ValidationError):
        parse_interval_minutes(value)


def test_exact_resolved_db_and_python() -> None:
    spec = _spec(
        python_exe=r"C:\Python314\python.exe",
        db_path=r"D:\ledger\clankops.db",
    )
    assert spec["python_exe"] == r"C:\Python314\python.exe"
    assert spec["execute"] == r"C:\Python314\python.exe"
    assert spec["db_path"] == r"D:\ledger\clankops.db"
    assert spec["argument_vector"][-4:] == [
        "--db",
        r"D:\ledger\clankops.db",
        "harvest",
        "local-git",
    ]
    assert spec["command_identity"]["argv_tail"] == [
        "--db",
        r"D:\ledger\clankops.db",
        "harvest",
        "local-git",
    ]


def test_clankops_db_env_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CLANKOPS_DB", raising=False)
    home = tmp_path / "Users" / "demo"
    assert default_db_path(environ={}, home=home) == home / ".clankops" / "clankops.db"
    honoured = tmp_path / "custom" / "ops.db"
    spec = _spec(db_path=None, environ={"CLANKOPS_DB": str(honoured)}, home=home)
    assert Path(spec["db_path"]) == honoured.resolve()


def test_spaces_in_python_and_db_paths_survive_quoting() -> None:
    python = r"C:\Program Files\Python 3.14\python.exe"
    db = r"C:\Users\Anil Ganti\.clankops\clankops.db"
    src = r"C:\Users\Anil Ganti\Clanks\clankops\src"
    spec = _spec(python_exe=python, db_path=db, sys_path_entry=src)
    assert spec["execute"] == python
    assert spec["execute"].startswith("C:\\") and spec["execute"][0] != '"'
    quoted_db = quote_windows_arg(db)
    quoted_src = quote_windows_arg(src)
    assert quoted_db.startswith('"') and quoted_db.endswith('"')
    assert quoted_src.startswith('"') and quoted_src.endswith('"')
    assert quoted_db in spec["argument_string"]
    assert quoted_src in spec["argument_string"]
    joined = join_windows_args([python, *spec["argument_vector"]])
    assert "Program Files" in joined
    assert "Anil Ganti" in joined


def test_quote_windows_arg_handles_quotes_and_trailing_backslash() -> None:
    assert quote_windows_arg("abc") == "abc"
    assert quote_windows_arg("") == '""'
    assert quote_windows_arg(r"C:\Program Files\app") == r'"C:\Program Files\app"'
    assert quote_windows_arg("C:\\Program Files\\app\\") == '"C:\\Program Files\\app\\\\"'
    assert r'\"' in quote_windows_arg('say "hi"')


def test_canonical_task_name_stable() -> None:
    spec = _spec()
    assert spec["task_name"] == CANONICAL_TASK_NAME == "ClankOps Fleet Harvest"
    with pytest.raises(ValidationError, match="canonical"):
        _spec(task_name="ClankOps Fleet Harvest TEST extra")


def test_overlap_policy_is_ignore_new() -> None:
    spec = _spec()
    assert spec["multiple_instances"] == "IgnoreNew"
    assert spec["settings"]["MultipleInstances"] == "IgnoreNew"


def test_missed_run_does_not_replay_every_interval() -> None:
    spec = _spec()
    assert spec["start_when_available"] is True
    assert spec["replay_missed_intervals"] is False
    assert spec["settings"]["StartWhenAvailable"] is True
    assert spec["wake_to_run"] is False
    assert spec["disallow_start_if_on_batteries"] is False
    assert spec["stop_if_going_on_batteries"] is False


def test_task_does_not_contain_github_or_git_network() -> None:
    spec = _spec()
    blob = json.dumps(spec).lower()
    for token in ("github", "fetch", "pull", "push", "clone", "ls-remote", "ssh"):
        assert token not in spec["argument_vector"]
        assert f"git {token}" not in blob
    assert spec["argument_vector"][-2:] == ["harvest", "local-git"]
    assert spec["network"] is False
    assert spec["one_shot"] is True


def test_no_secret_shaped_inherited_env_persisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_notarealtoken_abcdefgh")
    monkeypatch.setenv("PASSWORD", "hunter2")
    spec = _spec()
    assert spec["environment"] == []
    dumped = json.dumps(spec)
    assert "GITHUB_TOKEN" not in dumped
    assert "PASSWORD" not in dumped
    assert "ghp_" not in dumped
    assert "hunter2" not in dumped
    assert spec["store_password"] is False


def test_install_plan_idempotent_and_replace_reports_fields() -> None:
    spec = _spec()
    first = plan_install(None, spec)
    assert first["action"] == "create"
    same = plan_install(safety_contract(spec), spec)
    assert same["action"] == "unchanged"
    assert same["changed_fields"] == []
    other = plan_install({**safety_contract(spec), "interval_minutes": 30}, spec)
    assert other["action"] == "replace"
    assert other["changed_fields"] == ["interval_minutes"]


def test_narrow_existing_facts_are_not_unchanged() -> None:
    spec = _spec()
    narrow = plan_install(
        {
            "task_name": spec["task_name"],
            "execute": spec["execute"],
            "argument_string": spec["argument_string"],
            "interval_minutes": spec["interval_minutes"],
            "multiple_instances": spec["multiple_instances"],
        },
        spec,
    )
    assert narrow["action"] == "replace"
    assert "action_count" in narrow["changed_fields"]
    assert "start_when_available" in narrow["changed_fields"]


def test_remove_targets_only_canonical_and_missing_is_benign() -> None:
    assert plan_remove(None) == {"action": "absent", "task_name": CANONICAL_TASK_NAME}
    assert plan_remove({"task_name": CANONICAL_TASK_NAME})["action"] == "remove"
    with pytest.raises(ValidationError, match="non-canonical"):
        plan_remove({"task_name": "Something Else"})
    with pytest.raises(ValidationError, match="non-canonical"):
        plan_install({"task_name": "Windows Update", "execute": "x"}, _spec())


def _matching(spec: dict, **overrides) -> dict:
    data = safety_contract(spec)
    data.update(overrides)
    return data


@pytest.mark.parametrize(
    "field,value",
    [
        ("action_count", 2),
        ("trigger_count", 2),
        ("start_when_available", False),
        ("wake_to_run", True),
        ("disallow_start_if_on_batteries", True),
        ("stop_if_going_on_batteries", True),
        ("run_only_if_network_available", True),
        ("execution_time_limit_minutes", 15),
        ("logon_type", "Password"),
        ("run_level", "Highest"),
        ("principal_kind", "Password/Highest/SYSTEM"),
        ("repetition_indefinite", False),
        ("multiple_instances", "Parallel"),
    ],
)
def test_safety_contract_drift_is_replace_never_unchanged(field: str, value) -> None:
    spec = _spec()
    plan = plan_install(_matching(spec, **{field: value}), spec)
    assert plan["action"] == "replace"
    assert field in plan["changed_fields"]


def test_canonical_complete_task_is_unchanged() -> None:
    spec = _spec()
    plan = plan_install(_matching(spec), spec)
    assert plan["action"] == "unchanged"
    assert plan["changed_fields"] == []
    assert spec["action_count"] == 1
    assert spec["trigger_count"] == 1
    assert spec["repetition_indefinite"] is True
    assert spec["principal_kind"] == "interactive-limited-current-user"


def test_status_does_not_call_anything_health() -> None:
    text = format_status_text(
        {
            "installed": True,
            "task_name": CANONICAL_TASK_NAME,
            "interval_minutes": 10,
            "next_run": "unknown",
            "last_run": "unknown",
            "last_task_result": 0,
            "command_identity": "harvest local-git",
            "multiple_instances": "IgnoreNew",
        }
    )
    assert "health" not in text.lower()
    assert "installed: yes" in text
    missing = format_status_text({"installed": False})
    assert "health" not in missing.lower()
    spec_text = json.dumps(_spec()) + format_status_text({"installed": False})
    assert "health" not in spec_text.lower()


def test_pulse_spec_cli_does_not_create_db(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "does-not-exist.db"
    python = r"C:\Program Files\Python 3.14\python.exe"
    assert (
        main(
            [
                "--db",
                str(db),
                "--json",
                "pulse",
                "spec",
                "--interval",
                "10",
                "--python",
                python,
            ]
        )
        == 0
    )
    assert not db.exists()
    payload = json.loads(capsys.readouterr().out)
    assert payload["interval_minutes"] == 10
    assert payload["python_exe"].endswith("python.exe") or "Python 3.14" in payload["python_exe"]
    assert payload["db_path"]


def test_pulse_spec_cli_rejects_sub_minute(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "ledger.db"
    rc = main(["--db", str(db), "pulse", "spec", "--interval", "0.5"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "sub-minute" in err
    assert not db.exists()


def test_powershell_script_contract_without_host_mutation() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert "Invoke-Expression" not in source
    assert "cmd.exe" not in source.lower()
    assert "Register-ScheduledTask" in source
    assert "Unregister-ScheduledTask" in source
    assert "MaxValue" not in source
    assert "RepetitionInterval" in source
    pre, _, post = source.partition("switch ($Command)")
    assert pre.count("function Get-ClankOpsPulseContext") == 1
    status_remove = post.split('"dry-run"', 1)[0]
    assert "Get-ClankOpsPulseContext" not in status_remove
    assert "Get-ClankOpsPulsePython" not in status_remove
    assert "Get-ClankOpsPulseSpec" not in status_remove
    assert "Register-ClankOpsHarvestTask" not in status_remove
    before_install, _, _ = post.partition('"install"')
    assert "Register-ClankOpsHarvestTask" not in before_install
    assert "ClankOps Fleet Harvest" in source
    assert "IgnoreNew" in source
    assert "StartWhenAvailable" in source
    assert "AllowStartIfOnBatteries" in source
    assert "DontStopIfGoingOnBatteries" in source
    assert "WakeToRun = $false" in source
    assert "LogonType Interactive" in source
    assert "health" not in source.lower()
    assert "git fetch" not in source
    assert "git pull" not in source
    assert "git push" not in source
    assert "api.github.com" not in source.lower()
    assert "gh api" not in source.lower()
    assert "Assert-ClankOpsPulseTaskName" in source
    assert "Test-ClankOpsPulsePythonVersion" in source
    assert "3.14" in source
    assert "Windows Update" not in source


def test_harvest_and_terminal_modules_untouched_by_pulse_semantics() -> None:
    harvest = HARVEST_SCRIPT.read_text(encoding="utf-8")
    terminal = TERMINAL_SCRIPT.read_text(encoding="utf-8")
    assert "Task Scheduler" not in harvest
    assert "pulse" not in harvest.lower()
    assert "Register-ScheduledTask" not in terminal
    assert "Harvest now" not in terminal


def _ps_run(exe: str, args: list[str], *, env: dict[str, str] | None = None, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [exe, *args],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows dry-run probe; CI does not register tasks")
@pytest.mark.skipif(shutil.which("pwsh") is None and shutil.which("powershell") is None, reason="PowerShell not installed")
def test_powershell_dry_run_performs_zero_scheduler_mutation(tmp_path: Path) -> None:
    exe = shutil.which("pwsh") or shutil.which("powershell")
    assert exe
    db = tmp_path / "clankops.db"
    probe = shutil.which("powershell") or exe
    listed = _ps_run(
        probe,
        [
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-ScheduledTask -TaskName 'ClankOps Fleet Harvest' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty TaskName",
        ],
    )
    before = (listed.stdout or "").strip()
    proc = _ps_run(
        exe,
        [
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "dry-run",
            "-Python",
            sys.executable,
            "-Database",
            str(db),
            "-IntervalMinutes",
            "10",
            "-ClankOpsRoot",
            str(ROOT),
        ],
        cwd=str(ROOT),
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout
    assert "ClankOps Fleet Harvest" in out
    assert "every 10 minutes" in out
    assert sys.executable in out or "python" in out.lower()
    assert str(db) in out or "clankops.db" in out
    assert "scheduler mutation: none" in out
    assert "health" not in out.lower()
    after = _ps_run(
        probe,
        [
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-ScheduledTask -TaskName 'ClankOps Fleet Harvest' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty TaskName",
        ],
    )
    assert (after.stdout or "").strip() == before
    assert not db.exists()


def _windows_ps() -> str | None:
    if os.name != "nt":
        return None
    return shutil.which("powershell") or shutil.which("pwsh")


@pytest.mark.skipif(_windows_ps() is None, reason="Windows PowerShell Task Scheduler required")
def test_status_works_without_valid_clankops_root(tmp_path: Path) -> None:
    exe = _windows_ps()
    assert exe
    missing = tmp_path / "no-such-clankops"
    proc = _ps_run(
        exe,
        [
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "status",
            "-ClankOpsRoot",
            str(missing),
            "-Python",
            str(missing / "python.exe"),
        ],
    )
    assert proc.returncode == 0, proc.stderr
    assert "installed:" in proc.stdout
    assert "health" not in proc.stdout.lower()
    assert "ClankOps Fleet Harvest" in proc.stdout


@pytest.mark.skipif(_windows_ps() is None, reason="Windows PowerShell Task Scheduler required")
def test_remove_works_without_valid_clankops_root_or_python(tmp_path: Path) -> None:
    exe = _windows_ps()
    assert exe
    missing = tmp_path / "no-such-clankops"
    proc = _ps_run(
        exe,
        [
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(SCRIPT),
            "remove",
            "-ClankOpsRoot",
            str(missing),
            "-Python",
            str(missing / "python.exe"),
        ],
    )
    assert proc.returncode == 0, proc.stderr
    assert "ClankOps Fleet Harvest" in proc.stdout
    assert "health" not in proc.stdout.lower()


@pytest.mark.skipif(_windows_ps() is None, reason="Windows PowerShell Task Scheduler required")
def test_windows_temporary_task_create_inspect_remove() -> None:
    exe = _windows_ps()
    assert exe
    name = f"ClankOps Fleet Harvest TEST {uuid.uuid4()}"
    prod_before = _ps_run(
        exe,
        [
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            "Get-ScheduledTask -TaskName 'ClankOps Fleet Harvest' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty TaskName",
        ],
    )
    assert (prod_before.stdout or "").strip() == ""
    inspect_cmd = (
        f"$t = Get-ScheduledTask -TaskName '{name}'; "
        "$a = @($t.Actions); $tr = @($t.Triggers); "
        "$rep = $tr[0].Repetition; "
        "$dur = [string]$rep.Duration; "
        "[ordered]@{"
        "action_count=$a.Count; trigger_count=$tr.Count; "
        "execute=[string]$a[0].Execute; arguments=[string]$a[0].Arguments; "
        "interval=[string]$rep.Interval; duration=$dur; "
        "duration_empty=[string]::IsNullOrWhiteSpace($dur); "
        "multiple=[string]$t.Settings.MultipleInstances; "
        "start_when_available=[bool]$t.Settings.StartWhenAvailable; "
        "disallow_battery=[bool]$t.Settings.DisallowStartIfOnBatteries; "
        "stop_battery=[bool]$t.Settings.StopIfGoingOnBatteries; "
        "wake=[bool]$t.Settings.WakeToRun; "
        "network=[bool]$t.Settings.RunOnlyIfNetworkAvailable; "
        "limit=[string]$t.Settings.ExecutionTimeLimit; "
        "logon=[string]$t.Principal.LogonType; "
        "runlevel=[string]$t.Principal.RunLevel"
        "} | ConvertTo-Json -Compress"
    )
    try:
        created = _ps_run(
            exe,
            [
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(SCRIPT),
                "install",
                "-TaskName",
                name,
                "-Python",
                sys.executable,
                "-ClankOpsRoot",
                str(ROOT),
                "-IntervalMinutes",
                "10",
            ],
            cwd=str(ROOT),
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        )
        assert created.returncode == 0, created.stderr + created.stdout
        inspected = _ps_run(exe, ["-NoProfile", "-NonInteractive", "-Command", inspect_cmd])
        assert inspected.returncode == 0, inspected.stderr
        facts = json.loads(inspected.stdout)
        assert facts["action_count"] == 1
        assert facts["trigger_count"] == 1
        assert facts["execute"] == sys.executable or Path(facts["execute"]).resolve() == Path(sys.executable).resolve()
        assert "harvest" in facts["arguments"] and "local-git" in facts["arguments"]
        assert facts["interval"] == "PT10M"
        assert facts["duration_empty"] is True
        assert facts["duration"] in ("", None)
        assert facts["multiple"] == "IgnoreNew"
        assert facts["start_when_available"] is True
        assert facts["disallow_battery"] is False
        assert facts["stop_battery"] is False
        assert facts["wake"] is False
        assert facts["network"] is False
        assert facts["logon"] == "Interactive"
        assert facts["runlevel"] == "Limited"
        limit = str(facts.get("limit") or "")
        assert "1:00" in limit or "PT1H" in limit.upper() or limit.endswith("01:00:00")
        status = _ps_run(
            exe,
            [
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(SCRIPT),
                "status",
                "-TaskName",
                name,
                "-ClankOpsRoot",
                str(ROOT / "missing-root"),
                "-Python",
                str(ROOT / "missing-python.exe"),
            ],
        )
        assert status.returncode == 0, status.stderr
        assert "installed: yes" in status.stdout
        assert name in status.stdout
    finally:
        removed = _ps_run(
            exe,
            [
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(SCRIPT),
                "remove",
                "-TaskName",
                name,
                "-ClankOpsRoot",
                str(ROOT / "missing-root"),
                "-Python",
                str(ROOT / "missing-python.exe"),
            ],
        )
        leftover = _ps_run(
            exe,
            [
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                f"Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty TaskName",
            ],
        )
        prod = _ps_run(
            exe,
            [
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Get-ScheduledTask -TaskName 'ClankOps Fleet Harvest' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty TaskName",
            ],
        )
        assert removed.returncode == 0, removed.stderr
        assert (leftover.stdout or "").strip() == ""
        assert (prod.stdout or "").strip() == ""

