"""PowerShell launcher: start/resume load env, LaunchCursor, handoff clears, version compare."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from clankops.cli import main

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "clankops-dev.ps1"


def _powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


def _ps(command: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    exe = _powershell()
    assert exe, "PowerShell is required for these tests"
    return subprocess.run(
        [exe, "-NoProfile", "-NonInteractive", "-Command", command],
        cwd=str(ROOT),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _base_env(home: Path, db: str) -> dict[str, str]:
    env = os.environ.copy()
    env["CLANKOPS_HOME"] = str(home)
    env["CLANKOPS_ROOT"] = str(ROOT)
    env["CLANKOPS_DB"] = db
    src = str(ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src if not existing else f"{src}{os.pathsep}{existing}"
    return env


def _register(db: str, slug: str, path: Path, capsys) -> None:
    assert main(["--db", db, "--actor", "cursor", "register", slug, "--path", str(path)]) == 0
    capsys.readouterr()


@pytest.mark.skipif(_powershell() is None, reason="PowerShell not installed")
def test_python_version_compare_is_numeric() -> None:
    command = r"""
function Test-ClankOpsPythonVersion {
    param([string]$VersionText)
    $parts = @($VersionText.Trim() -split '\.')
    if ($parts.Count -lt 2) { return $false }
    $major = 0
    $minor = 0
    if (-not [int]::TryParse($parts[0], [ref]$major)) { return $false }
    if (-not [int]::TryParse($parts[1], [ref]$minor)) { return $false }
    if ($major -gt 3) { return $true }
    if ($major -eq 3 -and $minor -ge 14) { return $true }
    return $false
}
$results = @(
    [string](Test-ClankOpsPythonVersion '3.9')
    [string](Test-ClankOpsPythonVersion '3.2')
    [string](Test-ClankOpsPythonVersion '3.14')
    [string](Test-ClankOpsPythonVersion '3.15')
) -join ','
Write-Output $results
"""
    proc = _ps(command)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "False,False,True,True"


@pytest.mark.skipif(_powershell() is None, reason="PowerShell not installed")
def test_resume_and_start_load_env_and_launch_cursor_stub(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    oem = tmp_path / "oem-radar"
    oem.mkdir()
    db = str(tmp_path / "ps.db")
    _register(db, "oem-radar", oem, capsys)
    assert main(["--db", db, "--actor", "cursor", "work", "start", "oem-radar", "ps resume"]) == 0
    capsys.readouterr()
    assert main(["--db", db, "--actor", "cursor", "handoff", "COPS-000001", "--state", "PAUSED"]) == 0
    capsys.readouterr()

    launch_log = tmp_path / "launch.txt"
    stub = tmp_path / "cursor-stub.ps1"
    stub.write_text(
        "param($Path)\nSet-Content -Path $env:LAUNCH_LOG -Value $Path\n",
        encoding="utf-8",
    )
    env = _base_env(home, db)
    env["LAUNCH_LOG"] = str(launch_log)
    script = str(SCRIPT)
    command = f"""
$env:LAUNCH_LOG = '{launch_log}'
    . '{script}' -Command resume -Target oem-radar -Database '{db}' -ClankOpsRoot '{ROOT}' -LaunchCursor -CursorCommand '{stub}'
Write-Output ("SID=" + $env:CLANKOPS_SESSION_ID)
Write-Output ("SLUG=" + $env:CLANKOPS_CLANK_SLUG)
Write-Output ("PATH=" + $env:CLANKOPS_CLANK_PATH)
"""
    proc = _ps(command, env=env)
    output = proc.stdout + "\n--- STDERR ---\n" + proc.stderr
    assert proc.returncode == 0, output
    assert "SID=" in proc.stdout, output
    sid_line = [ln for ln in proc.stdout.splitlines() if ln.startswith("SID=")][-1]
    assert sid_line != "SID=", output
    assert "SLUG=oem-radar" in proc.stdout
    assert launch_log.exists()
    launched = launch_log.read_text(encoding="utf-8").strip().strip('"')
    assert Path(launched).resolve() == oem.resolve()
    assert ROOT.resolve() not in {Path(launched).resolve()}

    other = tmp_path / "watch-clank"
    other.mkdir()
    _register(db, "watch-clank", other, capsys)
    start_cmd = f"""
. '{script}' -Command start -Target watch-clank -Objective 'casio' -Database '{db}' -ClankOpsRoot '{ROOT}'
Write-Output ("START_SLUG=" + $env:CLANKOPS_CLANK_SLUG)
Write-Output ("START_SID=" + $env:CLANKOPS_SESSION_ID)
"""
    proc = _ps(start_cmd, env=_base_env(home, db))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "START_SLUG=watch-clank" in proc.stdout
    assert "START_SID=" in proc.stdout
    start_sid = [ln for ln in proc.stdout.splitlines() if ln.startswith("START_SID=")][-1]
    assert start_sid != "START_SID="


@pytest.mark.skipif(_powershell() is None, reason="PowerShell not installed")
def test_handoff_clears_active_env(tmp_path: Path, capsys) -> None:
    home = tmp_path / "home"
    oem = tmp_path / "oem-radar"
    oem.mkdir()
    db = str(tmp_path / "ps.db")
    _register(db, "oem-radar", oem, capsys)
    env = _base_env(home, db)
    script = str(SCRIPT)
    command = f"""
. '{script}' -Command start -Target oem-radar -Objective 'handoff path' -Database '{db}' -ClankOpsRoot '{ROOT}'
$mission = $env:CLANKOPS_MISSION_DISPLAY
. '{script}' -Command handoff -Target $mission -State PAUSED -Database '{db}' -ClankOpsRoot '{ROOT}' -Completed 'done' -Next 'later'
Write-Output ("AFTER_SID=" + $env:CLANKOPS_SESSION_ID)
Write-Output ("AFTER_MISSION=" + $env:CLANKOPS_MISSION_DISPLAY)
& '{sys.executable}' -m clankops --actor cursor --db '{db}' work env oem-radar
Write-Output ("ENV_CODE=" + $LASTEXITCODE)
"""
    proc = _ps(command, env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "AFTER_SID=" in proc.stdout
    after_sid = [ln for ln in proc.stdout.splitlines() if ln.startswith("AFTER_SID=")][-1]
    assert after_sid == "AFTER_SID="
    assert "AFTER_MISSION=" in proc.stdout
    after_mission = [ln for ln in proc.stdout.splitlines() if ln.startswith("AFTER_MISSION=")][-1]
    assert after_mission == "AFTER_MISSION="
    env_code = [ln for ln in proc.stdout.splitlines() if ln.startswith("ENV_CODE=")][-1]
    assert env_code == "ENV_CODE=2"
