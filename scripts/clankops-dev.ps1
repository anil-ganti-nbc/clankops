# ClankOps development launcher — Foundation 1
# Does NOT start collection or dashboards. Does NOT schedule anything.
# Makes ClankOps Session context visible to a Cursor coding session.

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("resume", "start", "handoff", "env", "brief")]
    [string]$Command = "resume",

    [Parameter(Position = 1)]
    [string]$Target,

    [string]$Objective,
    [ValidateSet("PAUSED", "BLOCKED", "COMPLETED", "ABANDONED")]
    [string]$State = "PAUSED",
    [string]$Completed,
    [string]$Current,
    [string]$Next,
    [string]$Tests,
    [string]$Notes,
    [string]$Actor = $(if ($env:CLANKOPS_ACTOR) { $env:CLANKOPS_ACTOR } else { "cursor" }),
    [string]$Db = $env:CLANKOPS_DB,
    [string]$ClankOpsRoot,
    [switch]$LaunchCursor
)

$ErrorActionPreference = "Stop"

function Write-ClankOpsFailure {
    param([string]$Message, [string]$Recovery)
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
    Write-Host "CLANKOPS INTEGRATION FAILED" -ForegroundColor Red
    Write-Host $Message
    if ($Recovery) { Write-Host "Recovery: $Recovery" }
    Write-Host "Emergency coding is not blocked. Do not export CLANKOPS_SESSION_ID until this is fixed."
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
}

function Get-ClankOpsPython {
    $candidates = @(
        { py -3.14 -c "import sys; print(sys.executable)" },
        { python -c "import sys; print(sys.executable)" }
    )
    foreach ($probe in $candidates) {
        try {
            $exe = & $probe 2>$null
            if ($LASTEXITCODE -eq 0 -and $exe) {
                $ver = & $exe.Trim() -c "import sys; print('%d.%d' % sys.version_info[:2])"
                if ($ver.Trim() -ge "3.14") { return $exe.Trim() }
            }
        } catch { }
    }
    return $null
}

if (-not $ClankOpsRoot) {
    if ($env:CLANKOPS_ROOT) { $ClankOpsRoot = $env:CLANKOPS_ROOT }
    elseif (Test-Path "C:\Users\anil\Clanks\clankops\src\clankops") { $ClankOpsRoot = "C:\Users\anil\Clanks\clankops" }
}
if ($ClankOpsRoot) {
    $env:CLANKOPS_ROOT = $ClankOpsRoot
    $src = Join-Path $ClankOpsRoot "src"
    if (Test-Path $src) {
        if ($env:PYTHONPATH) { $env:PYTHONPATH = "$src;$env:PYTHONPATH" }
        else { $env:PYTHONPATH = $src }
    }
}

$Python = Get-ClankOpsPython
if (-not $Python) {
    Write-ClankOpsFailure "Python 3.14+ with stdlib uuid.uuid7() was not found." "Install Python 3.14 and retry. py -3.14 -c `"import clankops`""
    exit 2
}

try {
    & $Python -c "import clankops, uuid; assert hasattr(uuid,'uuid7')"
    if ($LASTEXITCODE -ne 0) { throw "import failed" }
} catch {
    Write-ClankOpsFailure "ClankOps could not be imported with $Python." "cd C:\Users\anil\Clanks\clankops; $Python -m pip install -e ."
    exit 2
}

$cli = @("-m", "clankops", "--actor", $Actor)
if ($Db) { $cli += @("--db", $Db) }

switch ($Command) {
    "brief" {
        if (-not $Target) { Write-ClankOpsFailure "brief requires a Clank slug." ".\scripts\clankops-dev.ps1 brief oem-radar"; exit 2 }
        & $Python @cli brief $Target
        exit $LASTEXITCODE
    }
    "env" {
        & $Python @cli work env
        exit $LASTEXITCODE
    }
    "start" {
        if (-not $Target -or -not $Objective) {
            Write-ClankOpsFailure "start requires Clank slug and -Objective." ".\scripts\clankops-dev.ps1 start oem-radar -Objective '...' "
            exit 2
        }
        & $Python @cli work start $Target $Objective
        exit $LASTEXITCODE
    }
    "handoff" {
        if (-not $Target) { Write-ClankOpsFailure "handoff requires a Mission id." ".\scripts\clankops-dev.ps1 handoff COPS-000003 -State PAUSED"; exit 2 }
        $h = @("handoff", $Target, "--state", $State)
        if ($Completed) { $h += @("--completed", $Completed) }
        if ($Current) { $h += @("--current", $Current) }
        if ($Next) { $h += @("--next", $Next) }
        if ($Tests) { $h += @("--tests", $Tests) }
        if ($Notes) { $h += @("--notes", $Notes) }
        & $Python @cli @h
        exit $LASTEXITCODE
    }
    default {
        if (-not $Target) { Write-ClankOpsFailure "resume requires a Clank slug or Mission id." ".\scripts\clankops-dev.ps1 resume oem-radar"; exit 2 }
        & $Python @cli work resume $Target
        $code = $LASTEXITCODE
        if ($code -ne 0) { exit $code }
        $ctxDir = if ($env:CLANKOPS_HOME) { $env:CLANKOPS_HOME } else { Join-Path $HOME ".clankops" }
        $envFile = Join-Path $ctxDir "active-context.env"
        if (Test-Path $envFile) {
            Get-Content $envFile | ForEach-Object {
                if ($_ -match "^([^=]+)=(.*)$") {
                    Set-Item -Path "env:$($matches[1])" -Value $matches[2]
                }
            }
            Write-Host "Loaded ClankOps Session from $envFile"
        }
        if ($LaunchCursor) {
            $cursor = Get-Command cursor -ErrorAction SilentlyContinue
            if (-not $cursor) {
                Write-Host "cursor CLI not on PATH. Session env is loaded in this shell; open the Clank folder manually."
                exit 0
            }
        }
        exit $code
    }
}
