# Thin ClankOps adapter for Grok.
# Consumes clankctl JSON. Does not modify Grok. No credentials.

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Clank,

    [ValidateSet("packet", "prepare", "admit")]
    [string]$Command = "prepare",

    [string]$Mission,
    [string]$ExpectContext,
    [string]$Database = $env:CLANKOPS_DB,
    [switch]$NoGithub
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$dev = Join-Path $root "scripts\clankops-dev.ps1"
$invoke = @{
    Command      = $Command
    Target       = $Clank
    Actor        = "grok"
    Json         = $true
    ClankOpsRoot = $root
}
if ($Mission) { $invoke.Mission = $Mission }
if ($ExpectContext) { $invoke.ExpectContext = $ExpectContext }
if ($Database) { $invoke.Database = $Database }
if ($NoGithub) { $invoke.NoGithub = $true }
& $dev @invoke
exit $LASTEXITCODE
