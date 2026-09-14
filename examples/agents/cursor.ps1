# Thin ClankOps adapter for Cursor.
# Admission classification lives in Python (`clankctl agent launch`).
# Does not modify Cursor. No credentials.
#   .\examples\agents\cursor.ps1 oem-radar -AgentCommand python, -c, "..."
#   .\examples\agents\cursor.ps1 oem-radar -Command prepare

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Clank,

    [ValidateSet("packet", "prepare", "admit", "launch")]
    [string]$Command = "launch",

    [string]$Mission,
    [string]$ExpectContext,
    [string]$Database = $env:CLANKOPS_DB,
    [string[]]$AgentCommand,
    [switch]$NoGithub
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dev = Join-Path $root "scripts\clankops-dev.ps1"
$invoke = @{
    Command      = $Command
    Target       = $Clank
    Actor        = "cursor"
    Launcher     = "cursor"
    Json         = $true
    ClankOpsRoot = $root
}
if ($Mission) { $invoke.Mission = $Mission }
if ($ExpectContext) { $invoke.ExpectContext = $ExpectContext }
if ($Database) { $invoke.Database = $Database }
if ($NoGithub) { $invoke.NoGithub = $true }
if ($AgentCommand) { $invoke.AgentCommand = $AgentCommand }
& $dev @invoke
exit $LASTEXITCODE
