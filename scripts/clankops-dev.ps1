# ClankOps development launcher — Foundation 1
# Does NOT start collection or dashboards. Does NOT schedule anything.
# Makes ClankOps Session context visible to a Cursor coding session.
# Dot-source to keep CLANKOPS_* in this shell:
#   . .\scripts\clankops-dev.ps1 resume oem-radar
# Use -Database (not -Db): -Db collides with PowerShell -Debug.

param(
    [ValidateSet("resume", "start", "handoff", "env", "brief", "packet", "prepare", "admit")]
    [string]$Command = "resume",

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
    [string]$Database = $env:CLANKOPS_DB,
    [string]$ClankOpsRoot,
    [string]$CursorCommand,
    [string]$Mission,
    [string]$ExpectContext,
    [switch]$Json,
    [switch]$NoGithub,
    [switch]$LaunchCursor
)

$ErrorActionPreference = "Stop"
$script:ClankOpsDotSourced = $MyInvocation.InvocationName -eq '.'

function Write-ClankOpsFailure {
    param([string]$Message, [string]$Recovery)
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
    Write-Host "CLANKOPS INTEGRATION FAILED" -ForegroundColor Red
    Write-Host $Message
    if ($Recovery) { Write-Host "Recovery: $Recovery" }
    Write-Host "Emergency coding is not blocked. Do not export CLANKOPS_SESSION_ID until this is fixed."
    Write-Host "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!" -ForegroundColor Red
}

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
                if (Test-ClankOpsPythonVersion $ver) { return $exe.Trim() }
            }
        } catch { }
    }
    return $null
}

function Get-ClankOpsJsonValue {
    param($Object, [string]$Name)
    if ($null -eq $Object) { return $null }
    if ($Object -is [System.Collections.IDictionary]) {
        if ($Object.Contains($Name)) { return [string]$Object[$Name] }
        return $null
    }
    $prop = $Object.PSObject.Properties[$Name]
    if ($prop -and $null -ne $prop.Value -and [string]$prop.Value -ne "") {
        return [string]$prop.Value
    }
    return $null
}

function Import-ClankOpsPayloadEnv {
    param($Payload)
    $pairs = @{
        CLANKOPS_DB = @(Get-ClankOpsJsonValue $Payload.env "CLANKOPS_DB"; Get-ClankOpsJsonValue $Payload "db")
        CLANKOPS_ACTOR = @(Get-ClankOpsJsonValue $Payload.env "CLANKOPS_ACTOR"; Get-ClankOpsJsonValue $Payload "actor")
        CLANKOPS_CLANK_ID = @(Get-ClankOpsJsonValue $Payload.env "CLANKOPS_CLANK_ID"; Get-ClankOpsJsonValue $Payload "clank_id")
        CLANKOPS_CLANK_SLUG = @(Get-ClankOpsJsonValue $Payload.env "CLANKOPS_CLANK_SLUG"; Get-ClankOpsJsonValue $Payload "clank_slug")
        CLANKOPS_MISSION_ID = @(Get-ClankOpsJsonValue $Payload.env "CLANKOPS_MISSION_ID"; Get-ClankOpsJsonValue $Payload "mission_id")
        CLANKOPS_MISSION_DISPLAY = @(Get-ClankOpsJsonValue $Payload.env "CLANKOPS_MISSION_DISPLAY"; Get-ClankOpsJsonValue $Payload "mission_display")
        CLANKOPS_SESSION_ID = @(Get-ClankOpsJsonValue $Payload.env "CLANKOPS_SESSION_ID"; Get-ClankOpsJsonValue $Payload "session_id")
        CLANKOPS_CLANK_PATH = @(Get-ClankOpsJsonValue $Payload.env "CLANKOPS_CLANK_PATH"; Get-ClankOpsJsonValue $Payload "local_path")
        CLANKOPS_CONTEXT_FILE = @(Get-ClankOpsJsonValue $Payload.env "CLANKOPS_CONTEXT_FILE"; Get-ClankOpsJsonValue $Payload "context_file")
    }
    foreach ($name in $pairs.Keys) {
        $val = $null
        foreach ($candidate in $pairs[$name]) {
            if ($candidate) { $val = $candidate; break }
        }
        if ($val) {
            Set-Item -Path "env:$name" -Value $val
        }
        elseif ($name -eq "CLANKOPS_SESSION_ID") {
            Remove-Item Env:CLANKOPS_SESSION_ID -ErrorAction SilentlyContinue
        }
    }
}

function Clear-ClankOpsActiveEnv {
    foreach ($name in @(
        "CLANKOPS_SESSION_ID",
        "CLANKOPS_MISSION_ID",
        "CLANKOPS_MISSION_DISPLAY",
        "CLANKOPS_CLANK_ID",
        "CLANKOPS_CLANK_SLUG",
        "CLANKOPS_CLANK_PATH",
        "CLANKOPS_CONTEXT_FILE"
    )) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
}

function Invoke-ClankOpsCursor {
    param([string]$TargetPath)
    if (-not $TargetPath) {
        Write-ClankOpsFailure "No canonical local_path for this Clank; refusing to launch Cursor on ClankOps by guess." "Register a local_path ref, then retry."
        return 2
    }
    $resolved = [System.IO.Path]::GetFullPath($TargetPath)
    if ($CursorCommand) {
        & $CursorCommand $resolved
        if ($LASTEXITCODE -ne 0) { return $LASTEXITCODE }
        Write-Host "Launched Cursor command on $resolved"
        return 0
    }
    $cursor = Get-Command cursor -ErrorAction SilentlyContinue
    if (-not $cursor) {
        Write-Host "cursor CLI not on PATH. Session env is loaded in this shell; open $resolved manually."
        return 0
    }
    & $cursor.Source $resolved
    Write-Host "Launched Cursor on $resolved"
    return 0
}

function Invoke-ClankOpsJson {
    param([string[]]$CliArgs)
    $output = & $Python @CliArgs
    return @{ Code = $LASTEXITCODE; Text = ($output | Out-String) }
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
    if ($script:ClankOpsDotSourced) { return } else { exit 2 }
}

try {
    & $Python -c "import clankops, uuid; assert hasattr(uuid,'uuid7')"
    if ($LASTEXITCODE -ne 0) { throw "import failed" }
} catch {
    Write-ClankOpsFailure "ClankOps could not be imported with $Python." "cd C:\Users\anil\Clanks\clankops; $Python -m pip install -e ."
    if ($script:ClankOpsDotSourced) { return } else { exit 2 }
}

$cli = @("-m", "clankops", "--actor", $Actor)
if ($Database) { $cli += @("--db", $Database) }

$code = 0
$githubFlags = @()
if ($NoGithub) { $githubFlags += "--no-github" }

switch ($Command) {
    "brief" {
        if (-not $Target) { Write-ClankOpsFailure "brief requires a Clank slug." ".\scripts\clankops-dev.ps1 brief oem-radar"; $code = 2; break }
        & $Python @cli brief $Target
        $code = $LASTEXITCODE
    }
    "packet" {
        if (-not $Target) { Write-ClankOpsFailure "packet requires a Clank slug." ".\scripts\clankops-dev.ps1 packet oem-radar"; $code = 2; break }
        $p = @("resume-packet", $Target) + $githubFlags
        if ($Json) { $p = @("--json") + $p }
        & $Python @cli @p
        $code = $LASTEXITCODE
    }
    "prepare" {
        if (-not $Target) { Write-ClankOpsFailure "prepare requires a Clank slug." ".\scripts\clankops-dev.ps1 prepare oem-radar"; $code = 2; break }
        $p = @("agent", "prepare", $Target, "--actor", $Actor) + $githubFlags
        if ($Json) { $p = @("--json") + $p }
        & $Python @cli @p
        $code = $LASTEXITCODE
    }
    "admit" {
        if (-not $Target -or -not $Mission) {
            Write-ClankOpsFailure "admit requires a Clank slug and -Mission." ".\scripts\clankops-dev.ps1 admit oem-radar -Mission COPS-000015"
            $code = 2
            break
        }
        $p = @("agent", "admit", $Target, "--mission", $Mission, "--actor", $Actor) + $githubFlags
        if ($ExpectContext) { $p += @("--expect-context", $ExpectContext) }
        $result = Invoke-ClankOpsJson ($cli + @("--json") + $p)
        $code = $result.Code
        if ($code -ne 0) {
            Write-Host $result.Text
            break
        }
        $payload = $result.Text | ConvertFrom-Json
        Import-ClankOpsPayloadEnv $payload
        Write-Host "admitted $($payload.mission_display) session=$($payload.session_id) context=$($payload.context_fingerprint)"
        if (-not $Json) {
            & $Python @cli --json work env $payload.clank_slug
            $code = $LASTEXITCODE
        }
        else {
            Write-Output $result.Text
        }
    }
    "env" {
        $e = @("work", "env")
        if ($Target) { $e += $Target }
        & $Python @cli @e
        $code = $LASTEXITCODE
    }
    "start" {
        if (-not $Target -or -not $Objective) {
            Write-ClankOpsFailure "start requires Clank slug and -Objective." ".\scripts\clankops-dev.ps1 start oem-radar -Objective '...' "
            $code = 2
            break
        }
        $result = Invoke-ClankOpsJson ($cli + @("--json", "work", "start", $Target, $Objective))
        $code = $result.Code
        if ($code -ne 0) {
            Write-Host $result.Text
            break
        }
        $payload = $result.Text | ConvertFrom-Json
        Import-ClankOpsPayloadEnv $payload
        Write-Host "started $($payload.mission_display) session=$($payload.session_id)"
        & $Python @cli --json work env $payload.clank_slug
        $code = $LASTEXITCODE
        if ($code -ne 0) { break }
        if ($LaunchCursor) { $code = Invoke-ClankOpsCursor $payload.local_path }
    }
    "handoff" {
        if (-not $Target) { Write-ClankOpsFailure "handoff requires a Mission id." ".\scripts\clankops-dev.ps1 handoff COPS-000003 -State PAUSED"; $code = 2; break }
        $h = @("handoff", $Target, "--state", $State)
        if ($Completed) { $h += @("--completed", $Completed) }
        if ($Current) { $h += @("--current", $Current) }
        if ($Next) { $h += @("--next", $Next) }
        if ($Tests) { $h += @("--tests", $Tests) }
        if ($Notes) { $h += @("--notes", $Notes) }
        & $Python @cli @h
        $code = $LASTEXITCODE
        if ($code -eq 0) {
            Clear-ClankOpsActiveEnv
            Write-Host "Handoff complete. Closed Session is not exported as active."
        }
    }
    default {
        if (-not $Target) { Write-ClankOpsFailure "resume requires a Clank slug or Mission id." ".\scripts\clankops-dev.ps1 resume oem-radar"; $code = 2; break }
        $result = Invoke-ClankOpsJson ($cli + @("--json", "work", "resume", $Target))
        $code = $result.Code
        if ($code -ne 0) {
            Write-Host $result.Text
            break
        }
        $payload = $result.Text | ConvertFrom-Json
        Import-ClankOpsPayloadEnv $payload
        Write-Host "resumed $($payload.mission_display) session=$($payload.session_id) from $($payload.context_file)"
        & $Python @cli --json work env $payload.clank_slug
        $code = $LASTEXITCODE
        if ($code -ne 0) { break }
        if ($LaunchCursor) { $code = Invoke-ClankOpsCursor $payload.local_path }
    }
}

if (-not $script:ClankOpsDotSourced) {
    Write-Host "Tip: dot-source to keep env in this shell:  . .\scripts\clankops-dev.ps1 $Command $Target"
}

if ($script:ClankOpsDotSourced) {
    return
}
exit $code
