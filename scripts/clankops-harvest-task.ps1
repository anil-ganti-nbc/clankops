# ClankOps Fleet Pulse 1 — Windows Task Scheduler wrapper
# Explicit operator action only. Does not interpret Missions.
# Does not start collection, Terminal, CI, or GitHub polling.
# Dry-run makes ZERO Task Scheduler changes.
#
#   .\scripts\clankops-harvest-task.ps1 dry-run
#   .\scripts\clankops-harvest-task.ps1 install
#   .\scripts\clankops-harvest-task.ps1 status
#   .\scripts\clankops-harvest-task.ps1 remove

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "status", "remove", "dry-run")]
    [string]$Command = "dry-run",

    [int]$IntervalMinutes = 10,
    [string]$Python,
    [string]$Database,
    [string]$ClankOpsRoot,
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-ClankOpsWindowsPulseHost {
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        throw "Fleet Pulse requires Windows PowerShell 5+ or PowerShell 7+ on Windows."
    }
    if ($PSVersionTable.PSEdition -eq "Core" -and (Get-Variable -Name IsWindows -ErrorAction SilentlyContinue) -and -not $IsWindows) {
        throw "Fleet Pulse harvest-task installation requires Windows Task Scheduler."
    }
}

function Get-ClankOpsPulseRoot {
    param([string]$Override)
    if ($Override) { return (Resolve-Path -LiteralPath $Override).Path }
    if ($env:CLANKOPS_ROOT) { return (Resolve-Path -LiteralPath $env:CLANKOPS_ROOT).Path }
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Get-ClankOpsPulsePython {
    param([string]$Override, [string]$Root)
    if ($Override) {
        if (-not (Test-Path -LiteralPath $Override)) {
            throw "Python executable not found: $Override"
        }
        return (Resolve-Path -LiteralPath $Override).Path
    }
    $src = Join-Path $Root "src"
    $probe = "import sys; sys.path.insert(0, sys.argv[1]); import clankops; print(sys.executable)"
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $exe = & py -3.14 -c $probe $src
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.ToString().Trim() }
        } catch { }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        try {
            $exe = & python -c $probe $src
            if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.ToString().Trim() }
        } catch { }
    }
    throw "Could not resolve a Python interpreter that imports this ClankOps checkout. Pass -Python."
}

function Write-ClankOpsTempJson {
    param($Object)
    $tmp = [System.IO.Path]::GetTempFileName()
    $text = if ($null -eq $Object) { "null" } elseif ($Object -is [string]) { $Object } else { $Object | ConvertTo-Json -Compress -Depth 8 }
    [System.IO.File]::WriteAllText($tmp, $text)
    return $tmp
}

function Get-ClankOpsPulseDatabase {
    param([string]$Override)
    if ($Override) {
        return [System.IO.Path]::GetFullPath($Override)
    }
    if ($env:CLANKOPS_DB) {
        return [System.IO.Path]::GetFullPath($env:CLANKOPS_DB)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $env:USERPROFILE ".clankops\clankops.db"))
}

function Invoke-ClankOpsPulseCli {
    param(
        [string]$PythonExe,
        [string]$Root,
        [string]$DatabasePath,
        [string[]]$PulseArgs
    )
    $src = Join-Path $Root "src"
    $env:PYTHONPATH = $src
    $argv = @("-m", "clankops", "--db", $DatabasePath) + $PulseArgs
    $output = & $PythonExe @argv
    if ($LASTEXITCODE -ne 0) {
        throw "clankops pulse failed (exit $LASTEXITCODE)."
    }
    return $output
}

function ConvertFrom-ClankOpsPulseJson {
    param($Raw)
    $text = if ($Raw -is [System.Array]) { $Raw -join "`n" } else { [string]$Raw }
    return $text | ConvertFrom-Json
}

function Get-ClankOpsPulseSpec {
    param(
        [string]$PythonExe,
        [string]$Root,
        [string]$DatabasePath,
        [int]$Minutes
    )
    $raw = Invoke-ClankOpsPulseCli -PythonExe $PythonExe -Root $Root -DatabasePath $DatabasePath -PulseArgs @(
        "--json", "pulse", "spec",
        "--interval", "$Minutes",
        "--python", $PythonExe
    )
    return ConvertFrom-ClankOpsPulseJson $raw
}

function ConvertFrom-IsoIntervalMinutes {
    param([string]$Iso)
    if (-not $Iso) { return $null }
    if ($Iso -match '^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$') {
        $hours = if ($Matches[1]) { [int]$Matches[1] } else { 0 }
        $mins = if ($Matches[2]) { [int]$Matches[2] } else { 0 }
        $secs = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
        if ($secs -ne 0) {
            throw "harvest pulse does not support sub-minute scheduling (found $Iso)"
        }
        return ($hours * 60) + $mins
    }
    return $null
}

function Get-ClankOpsExistingTaskFacts {
    param([string]$TaskName)
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) { return $null }
    $action = @($task.Actions)[0]
    $trigger = @($task.Triggers)[0]
    $interval = $null
    if ($trigger -and $trigger.Repetition -and $trigger.Repetition.Interval) {
        $interval = ConvertFrom-IsoIntervalMinutes ([string]$trigger.Repetition.Interval)
    }
    $multi = [string]$task.Settings.MultipleInstances
    return [ordered]@{
        task_name = $task.TaskName
        execute = [string]$action.Execute
        argument_string = [string]$action.Arguments
        interval_minutes = $interval
        multiple_instances = $multi
    }
}

function Assert-CanonicalTaskName {
    param([string]$Name)
    if ($Name -ne "ClankOps Fleet Harvest") {
        throw "refusing to mutate non-canonical task '$Name'"
    }
}

function Test-ScheduledTasksAvailable {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        throw "Windows ScheduledTasks cmdlets are required (Get-ScheduledTask not found)."
    }
}

function Register-ClankOpsHarvestTask {
    param($Spec)
    Test-ScheduledTasksAvailable
    Assert-CanonicalTaskName $Spec.task_name
    $start = Get-Date
    $trigger = New-ScheduledTaskTrigger -Once -At $start
    $trigger.Repetition.Interval = "PT$($Spec.interval_minutes)M"
    $trigger.Repetition.Duration = ([TimeSpan]::MaxValue).ToString()
    $trigger.Repetition.StopAtDurationEnd = $false
    $settings = New-ScheduledTaskSettingsSet `
        -MultipleInstances IgnoreNew `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
        -DontStopOnIdleEnd
    $settings.WakeToRun = $false
    $settings.RunOnlyIfNetworkAvailable = $false
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    $action = New-ScheduledTaskAction -Execute ([string]$Spec.execute) -Argument ([string]$Spec.argument_string)
    Register-ScheduledTask `
        -TaskName ([string]$Spec.task_name) `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null
}

function Unregister-ClankOpsHarvestTask {
    param([string]$TaskName)
    Test-ScheduledTasksAvailable
    Assert-CanonicalTaskName $TaskName
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

function Write-ClankOpsPulseOutput {
    param($Object, [string]$Text)
    if ($Json) {
        if ($Object -is [string]) {
            Write-Output $Object
        } else {
            $Object | ConvertTo-Json -Depth 8
        }
    } else {
        Write-Output $Text
    }
}

Test-ClankOpsWindowsPulseHost
$root = Get-ClankOpsPulseRoot -Override $ClankOpsRoot
$pythonExe = Get-ClankOpsPulsePython -Override $Python -Root $root
$dbPath = Get-ClankOpsPulseDatabase -Override $Database
$spec = Get-ClankOpsPulseSpec -PythonExe $pythonExe -Root $root -DatabasePath $dbPath -Minutes $IntervalMinutes

switch ($Command) {
    "dry-run" {
        $text = Invoke-ClankOpsPulseCli -PythonExe $pythonExe -Root $root -DatabasePath $dbPath -PulseArgs @(
            "pulse", "spec",
            "--interval", "$IntervalMinutes",
            "--python", $pythonExe
        )
        if ($Json) {
            $spec | ConvertTo-Json -Depth 8
        } else {
            Write-Output $text
            Write-Output "scheduler mutation: none (dry-run)"
        }
    }
    "install" {
        Test-ScheduledTasksAvailable
        $existing = Get-ClankOpsExistingTaskFacts -TaskName ([string]$spec.task_name)
        $existingFile = Write-ClankOpsTempJson $(if ($existing) { $existing } else { $null })
        try {
        $planRaw = Invoke-ClankOpsPulseCli -PythonExe $pythonExe -Root $root -DatabasePath $dbPath -PulseArgs @(
            "--json", "pulse", "plan",
            "--interval", "$IntervalMinutes",
            "--python", $pythonExe,
            "--existing-json", $existingFile
        )
        $plan = ConvertFrom-ClankOpsPulseJson $planRaw
        if ($plan.action -eq "unchanged") {
            Write-ClankOpsPulseOutput -Object $plan -Text "installed: already present (unchanged)"
        } elseif ($plan.action -eq "create") {
            Register-ClankOpsHarvestTask -Spec $spec
            Write-ClankOpsPulseOutput -Object $plan -Text "installed: created $($spec.task_name)"
        } elseif ($plan.action -eq "replace") {
            $fields = @($plan.changed_fields) -join ", "
            Register-ClankOpsHarvestTask -Spec $spec
            Write-ClankOpsPulseOutput -Object $plan -Text "installed: replaced $($spec.task_name); changed fields: $fields"
        } else {
            throw "unexpected install plan action: $($plan.action)"
        }
        } finally {
            Remove-Item -LiteralPath $existingFile -ErrorAction SilentlyContinue
        }
    }
    "remove" {
        Test-ScheduledTasksAvailable
        $existing = Get-ClankOpsExistingTaskFacts -TaskName "ClankOps Fleet Harvest"
        $existingFile = Write-ClankOpsTempJson $(if ($existing) { $existing } else { $null })
        try {
        $planRaw = Invoke-ClankOpsPulseCli -PythonExe $pythonExe -Root $root -DatabasePath $dbPath -PulseArgs @(
            "--json", "pulse", "remove-plan",
            "--existing-json", $existingFile
        )
        $plan = ConvertFrom-ClankOpsPulseJson $planRaw
        if ($plan.action -eq "absent") {
            Write-ClankOpsPulseOutput -Object $plan -Text "removed: no task named ClankOps Fleet Harvest"
        } elseif ($plan.action -eq "remove") {
            Unregister-ClankOpsHarvestTask -TaskName "ClankOps Fleet Harvest"
            Write-ClankOpsPulseOutput -Object $plan -Text "removed: ClankOps Fleet Harvest"
        } else {
            throw "unexpected remove plan action: $($plan.action)"
        }
        } finally {
            Remove-Item -LiteralPath $existingFile -ErrorAction SilentlyContinue
        }
    }
    "status" {
        Test-ScheduledTasksAvailable
        $task = Get-ScheduledTask -TaskName "ClankOps Fleet Harvest" -ErrorAction SilentlyContinue
        if (-not $task) {
            $facts = [ordered]@{
                installed = $false
                task_name = "ClankOps Fleet Harvest"
            }
        } else {
            $info = Get-ScheduledTaskInfo -TaskName "ClankOps Fleet Harvest"
            $existing = Get-ClankOpsExistingTaskFacts -TaskName "ClankOps Fleet Harvest"
            $next = $null
            $last = $null
            if ($info) {
                if ($info.NextRunTime -and $info.NextRunTime.Year -gt 1899) { $next = [string]$info.NextRunTime }
                if ($info.LastRunTime -and $info.LastRunTime.Year -gt 1899) { $last = [string]$info.LastRunTime }
            }
            $facts = [ordered]@{
                installed = $true
                task_name = $task.TaskName
                interval_minutes = $existing.interval_minutes
                next_run = $next
                last_run = $last
                last_task_result = if ($info) { $info.LastTaskResult } else { $null }
                command_identity = "$($existing.execute) $($existing.argument_string)"
                multiple_instances = $existing.multiple_instances
            }
        }
        $factsFile = Write-ClankOpsTempJson $facts
        try {
        $text = Invoke-ClankOpsPulseCli -PythonExe $pythonExe -Root $root -DatabasePath $dbPath -PulseArgs @(
            "pulse", "status",
            "--existing-json", $factsFile
        )
        Write-ClankOpsPulseOutput -Object $facts -Text $text
        } finally {
            Remove-Item -LiteralPath $factsFile -ErrorAction SilentlyContinue
        }
    }
}
