# ClankOps Desktop Alpha — Windows ONEDIR build
# Does not install Pulse, harvest, or mutate the ledger.
#
#   .\scripts\build-desktop.ps1

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Test-ClankOpsWindowsDesktopHost {
    if ($PSVersionTable.PSEdition -eq "Core" -and (Get-Variable -Name IsWindows -ErrorAction SilentlyContinue) -and -not $IsWindows) {
        throw "ClankOps Desktop Alpha packaging requires Windows."
    }
}

function Test-ClankOpsDesktopPythonVersion {
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

Test-ClankOpsWindowsDesktopHost

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = (Get-Command python -ErrorAction Stop).Source
$PyVersion = & $Python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])"
if (-not (Test-ClankOpsDesktopPythonVersion $PyVersion)) {
    throw "ClankOps Desktop Alpha requires Python 3.14+. Found $PyVersion ($Python)"
}

& $Python -c "import importlib.metadata as m; v=m.version('pywebview'); assert v=='6.2.1', v"
if ($LASTEXITCODE -ne 0) {
    throw "pywebview 6.2.1 is required. Install with: python -m pip install -e `".[desktop]`""
}

$PyInstallerVersion = & $Python -c "import importlib.metadata as m; print(m.version('pyinstaller'))"
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is required. Install with: python -m pip install -e `".[desktop-build]`""
}
$PyParts = @($PyInstallerVersion.Trim() -split '\.')
$PyMajor = [int]$PyParts[0]
$PyMinor = [int]$PyParts[1]
if ($PyMajor -ne 6 -or $PyMinor -lt 22) {
    throw "PyInstaller 6.22.x (less than 7) is required. Found $PyInstallerVersion"
}

$Spec = Join-Path $Root "packaging\ClankOps.spec"
if (-not (Test-Path $Spec)) {
    throw "missing spec: $Spec"
}

$DistApp = Join-Path $Root "dist\ClankOps"
$BuildApp = Join-Path $Root "build\ClankOps"
$WorkPath = Join-Path $Root "build\ClankOps"

if (Test-Path $DistApp) {
    Remove-Item -LiteralPath $DistApp -Recurse -Force
}
if (Test-Path $BuildApp) {
    Remove-Item -LiteralPath $BuildApp -Recurse -Force
}

Write-Host "Building ClankOps Desktop ONEDIR with PyInstaller $PyInstallerVersion"
& $Python -m PyInstaller --noconfirm --clean --workpath $WorkPath --distpath (Join-Path $Root "dist") $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit $LASTEXITCODE"
}

$Exe = Join-Path $DistApp "ClankOps.exe"
if (-not (Test-Path $Exe)) {
    throw "expected EXE was not produced: $Exe"
}

$Item = Get-Item -LiteralPath $Exe
$Hash = (Get-FileHash -LiteralPath $Exe -Algorithm SHA256).Hash
Write-Host "EXE    $Exe"
Write-Host "SIZE   $($Item.Length) bytes"
Write-Host "SHA256 $Hash"
Write-Host "PYTHON $Python ($PyVersion)"
Write-Host "WEBVIEW pywebview 6.2.1 / system WebView2 Runtime"
