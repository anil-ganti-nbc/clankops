# ClankOps Fleet Pulse 1 — Windows Task Scheduler wrapper
# Explicit operator action only. Does not interpret Missions.
# Does not start collection, Terminal, CI, or GitHub polling.
# Dry-run makes ZERO Task Scheduler changes.
#
#   .\scripts\clankops-harvest-task.ps1 dry-run
#   .\scripts\clankops-harvest-task.ps1 install
#   .\scripts\clankops-harvest-task.ps1 status
#   .\scripts\clankops-harvest-task.ps1 remove
#
# status and remove query Task Scheduler only. They do not import ClankOps.

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("install", "status", "remove", "dry-run")]
    [string]$Command = "dry-run",

    [int]$IntervalMinutes = 10,
    [string]$Python,
    [string]$Database,
    [string]$ClankOpsRoot,
    [string]$TaskName = "ClankOps Fleet Harvest",
    [switch]$Json
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:CanonicalTaskName = "ClankOps Fleet Harvest"

function Test-ClankOpsWindowsPulseHost {
    if ($PSVersionTable.PSVersion.Major -lt 5) {
        throw "Fleet Pulse requires Windows PowerShell 5+ or PowerShell 7+ on Windows."
    }
    if ($PSVersionTable.PSEdition -eq "Core" -and (Get-Variable -Name IsWindows -ErrorAction SilentlyContinue) -and -not $IsWindows) {
        throw "Fleet Pulse harvest-task installation requires Windows Task Scheduler."
    }
}

function Test-ClankOpsPulsePythonVersion {
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

function Assert-ClankOpsPulseTaskName {
    param([string]$Name)
    if ($Name -eq $script:CanonicalTaskName) { return }
    if ($Name -match '^ClankOps Fleet Harvest TEST [0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$') {
        return
    }
    throw "refusing to mutate non-canonical task '$Name'"
}

function Test-ClankOpsPulseTestTaskName {
    param([string]$Name)
    return [bool]($Name -match '^ClankOps Fleet Harvest TEST [0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')
}

function Test-ScheduledTasksAvailable {
    if (-not (Get-Command Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        throw "Windows ScheduledTasks cmdlets are required (Get-ScheduledTask not found)."
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
    $src = Join-Path $Root "src"
    $versionProbe = "import sys; print('%d.%d' % sys.version_info[:2])"
    $importProbe = "import sys; sys.path.insert(0, sys.argv[1]); import clankops; print(sys.executable)"
    if ($Override) {
        if (-not (Test-Path -LiteralPath $Override)) {
            throw "Python executable not found: $Override"
        }
        $resolved = (Resolve-Path -LiteralPath $Override).Path
        $ver = & $resolved -c $versionProbe
        if ($LASTEXITCODE -ne 0 -or -not (Test-ClankOpsPulsePythonVersion $ver)) {
            throw "Python >= 3.14 is required for Fleet Pulse (got $ver from $resolved)."
        }
        $imported = & $resolved -c $importProbe $src
        if ($LASTEXITCODE -ne 0 -or -not $imported) {
            throw "Python $resolved cannot import this ClankOps checkout."
        }
        return $imported.ToString().Trim()
    }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        try {
            $ver = & py -3.14 -c $versionProbe
            if ($LASTEXITCODE -eq 0 -and (Test-ClankOpsPulsePythonVersion $ver)) {
                $exe = & py -3.14 -c $importProbe $src
                if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.ToString().Trim() }
            }
        } catch { }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        try {
            $ver = & python -c $versionProbe
            if ($LASTEXITCODE -eq 0 -and (Test-ClankOpsPulsePythonVersion $ver)) {
                $exe = & python -c $importProbe $src
                if ($LASTEXITCODE -eq 0 -and $exe) { return $exe.ToString().Trim() }
            }
        } catch { }
    }
    throw "Could not resolve Python >= 3.14 that imports this ClankOps checkout. Pass -Python."
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
    param($Iso)
    if ($null -eq $Iso) { return $null }
    $text = [string]$Iso
    if (-not $text) { return $null }
    if ($text -match '^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$') {
        $hours = if ($Matches[1]) { [int]$Matches[1] } else { 0 }
        $mins = if ($Matches[2]) { [int]$Matches[2] } else { 0 }
        $secs = if ($Matches[3]) { [int]$Matches[3] } else { 0 }
        if ($secs -ne 0) {
            throw "harvest pulse does not support sub-minute scheduling (found $text)"
        }
        return ($hours * 60) + $mins
    }
    return $null
}

function ConvertFrom-ExecutionLimitMinutes {
    param($Limit)
    if ($null -eq $Limit) { return $null }
    if ($Limit -is [TimeSpan]) { return [int]$Limit.TotalMinutes }
    $text = [string]$Limit
    if (-not $text) { return $null }
    $fromIso = ConvertFrom-IsoIntervalMinutes $text
    if ($null -ne $fromIso) { return $fromIso }
    $span = [TimeSpan]::Zero
    if ([TimeSpan]::TryParse($text, [ref]$span)) {
        return [int]$span.TotalMinutes
    }
    return $null
}

function Test-ClankOpsPulseIndefiniteDuration {
    param($Duration)
    if ($null -eq $Duration) { return $true }
    $text = [string]$Duration
    if ([string]::IsNullOrWhiteSpace($text)) { return $true }
    return $false
}

function Get-ClankOpsExistingTaskFacts {
    param([string]$Name)
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if (-not $task) { return $null }
    $actions = @($task.Actions)
    $triggers = @($task.Triggers)
    $action = if ($actions.Count -gt 0) { $actions[0] } else { $null }
    $trigger = if ($triggers.Count -gt 0) { $triggers[0] } else { $null }
    $interval = $null
    $indefinite = $false
    if ($trigger -and $trigger.Repetition) {
        $interval = ConvertFrom-IsoIntervalMinutes $trigger.Repetition.Interval
        $indefinite = Test-ClankOpsPulseIndefiniteDuration $trigger.Repetition.Duration
    }
    $principal = $task.Principal
    $logon = [string]$principal.LogonType
    $level = [string]$principal.RunLevel
    $user = [string]$principal.UserId
    $current = [string]$env:USERNAME
    $principalKind = "$logon/$level/$user"
    if ($logon -eq "Interactive" -and $level -eq "Limited" -and $user -and ($user -eq $current -or $user.EndsWith("\$current") -or $user.EndsWith("/$current"))) {
        $principalKind = "interactive-limited-current-user"
    }
    return [ordered]@{
        task_name = $task.TaskName
        action_count = $actions.Count
        trigger_count = $triggers.Count
        execute = if ($action) { [string]$action.Execute } else { $null }
        argument_string = if ($action) { [string]$action.Arguments } else { $null }
        interval_minutes = $interval
        repetition_indefinite = $indefinite
        repetition_duration = if ($trigger -and $trigger.Repetition) { [string]$trigger.Repetition.Duration } else { $null }
        multiple_instances = [string]$task.Settings.MultipleInstances
        start_when_available = [bool]$task.Settings.StartWhenAvailable
        disallow_start_if_on_batteries = [bool]$task.Settings.DisallowStartIfOnBatteries
        stop_if_going_on_batteries = [bool]$task.Settings.StopIfGoingOnBatteries
        wake_to_run = [bool]$task.Settings.WakeToRun
        run_only_if_network_available = [bool]$task.Settings.RunOnlyIfNetworkAvailable
        execution_time_limit_minutes = ConvertFrom-ExecutionLimitMinutes $task.Settings.ExecutionTimeLimit
        allow_demand_start = [bool]$task.Settings.AllowDemandStart
        principal_kind = $principalKind
        logon_type = $logon
        run_level = $level
        store_password = $false
        user_id = $user
    }
}

function Register-ClankOpsHarvestTask {
    param($Spec, [string]$Name)
    Test-ScheduledTasksAvailable
    Assert-ClankOpsPulseTaskName $Name
    $start = Get-Date
    # Omit Repetition.Duration. On this host, a registered trigger then has an
    # empty Duration, which Task Scheduler treats as indefinite.
    $trigger = New-ScheduledTaskTrigger -Once -At $start -RepetitionInterval (New-TimeSpan -Minutes ([int]$Spec.interval_minutes))
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
        -TaskName $Name `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force | Out-Null
}

function Unregister-ClankOpsHarvestTask {
    param([string]$Name)
    Test-ScheduledTasksAvailable
    Assert-ClankOpsPulseTaskName $Name
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false
}

function Format-ClankOpsPulseStatus {
    param($Facts)
    $lines = New-Object System.Collections.Generic.List[string]
    $installed = [bool]$Facts.installed
    $lines.Add("installed: $(if ($installed) { 'yes' } else { 'no' })") | Out-Null
    $lines.Add("task name: $($Facts.task_name)") | Out-Null
    if ($installed) {
        if ($null -ne $Facts.interval_minutes) {
            $lines.Add("cadence: every $($Facts.interval_minutes) minutes") | Out-Null
        } else {
            $lines.Add("cadence: unknown") | Out-Null
        }
        $lines.Add("next run: $(if ($Facts.next_run) { $Facts.next_run } else { 'unknown' })") | Out-Null
        $lines.Add("last run: $(if ($Facts.last_run) { $Facts.last_run } else { 'unknown' })") | Out-Null
        $result = if ($null -ne $Facts.last_task_result) { $Facts.last_task_result } else { "unknown" }
        $lines.Add("last task result: $result") | Out-Null
        $lines.Add("command identity: $(if ($Facts.command_identity) { $Facts.command_identity } else { 'unknown' })") | Out-Null
        $lines.Add("overlap: $(if ($Facts.multiple_instances) { $Facts.multiple_instances } else { 'unknown' })") | Out-Null
    }
    $text = $lines -join "`n"
    return $text
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

function Get-ClankOpsPulseContext {
    $root = Get-ClankOpsPulseRoot -Override $ClankOpsRoot
    $pythonExe = Get-ClankOpsPulsePython -Override $Python -Root $root
    $dbPath = Get-ClankOpsPulseDatabase -Override $Database
    $spec = Get-ClankOpsPulseSpec -PythonExe $pythonExe -Root $root -DatabasePath $dbPath -Minutes $IntervalMinutes
    return [ordered]@{
        root = $root
        python = $pythonExe
        db = $dbPath
        spec = $spec
    }
}

Test-ClankOpsWindowsPulseHost
Assert-ClankOpsPulseTaskName $TaskName

switch ($Command) {
    "status" {
        Test-ScheduledTasksAvailable
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $task) {
            $facts = [ordered]@{
                installed = $false
                task_name = $TaskName
            }
        } else {
            $info = Get-ScheduledTaskInfo -TaskName $TaskName
            $existing = Get-ClankOpsExistingTaskFacts -Name $TaskName
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
                action_count = $existing.action_count
                trigger_count = $existing.trigger_count
                repetition_indefinite = $existing.repetition_indefinite
            }
        }
        Write-ClankOpsPulseOutput -Object $facts -Text (Format-ClankOpsPulseStatus $facts)
    }
    "remove" {
        Test-ScheduledTasksAvailable
        $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if (-not $existing) {
            $plan = [ordered]@{ action = "absent"; task_name = $TaskName }
            Write-ClankOpsPulseOutput -Object $plan -Text "removed: no task named $TaskName"
        } else {
            Unregister-ClankOpsHarvestTask -Name $TaskName
            $plan = [ordered]@{ action = "remove"; task_name = $TaskName }
            Write-ClankOpsPulseOutput -Object $plan -Text "removed: $TaskName"
        }
    }
    "dry-run" {
        $ctx = Get-ClankOpsPulseContext
        $text = Invoke-ClankOpsPulseCli -PythonExe $ctx.python -Root $ctx.root -DatabasePath $ctx.db -PulseArgs @(
            "pulse", "spec",
            "--interval", "$IntervalMinutes",
            "--python", $ctx.python
        )
        if ($Json) {
            $ctx.spec | ConvertTo-Json -Depth 8
        } else {
            Write-Output $text
            Write-Output "scheduler mutation: none (dry-run)"
        }
    }
    "install" {
        Test-ScheduledTasksAvailable
        $ctx = Get-ClankOpsPulseContext
        $spec = $ctx.spec
        if (Test-ClankOpsPulseTestTaskName $TaskName) {
            Register-ClankOpsHarvestTask -Spec $spec -Name $TaskName
            $plan = [ordered]@{ action = "create"; task_name = $TaskName }
            Write-ClankOpsPulseOutput -Object $plan -Text "installed: created $TaskName"
            break
        }
        $existing = Get-ClankOpsExistingTaskFacts -Name $TaskName
        $existingFile = Write-ClankOpsTempJson $(if ($existing) { $existing } else { $null })
        try {
            $planRaw = Invoke-ClankOpsPulseCli -PythonExe $ctx.python -Root $ctx.root -DatabasePath $ctx.db -PulseArgs @(
                "--json", "pulse", "plan",
                "--interval", "$IntervalMinutes",
                "--python", $ctx.python,
                "--existing-json", $existingFile
            )
            $plan = ConvertFrom-ClankOpsPulseJson $planRaw
            if ($plan.action -eq "unchanged") {
                Write-ClankOpsPulseOutput -Object $plan -Text "installed: already present (unchanged)"
            } elseif ($plan.action -eq "create") {
                Register-ClankOpsHarvestTask -Spec $spec -Name $TaskName
                Write-ClankOpsPulseOutput -Object $plan -Text "installed: created $TaskName"
            } elseif ($plan.action -eq "replace") {
                $fields = @($plan.changed_fields) -join ", "
                Register-ClankOpsHarvestTask -Spec $spec -Name $TaskName
                Write-ClankOpsPulseOutput -Object $plan -Text "installed: replaced $TaskName; changed fields: $fields"
            } else {
                throw "unexpected install plan action: $($plan.action)"
            }
        } finally {
            Remove-Item -LiteralPath $existingFile -ErrorAction SilentlyContinue
        }
    }
}
