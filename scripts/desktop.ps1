# Launch ExamOS desktop.
#   default        -> Edge/Chrome application window (verified, zero extra download)
#   -Electron      -> Electron shell if installed under desktop/electron
#   -Fresh         -> use a clean per-user database
param([switch]$Electron, [switch]$Fresh)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "run .\scripts\setup.ps1 first" }

$env:TMP = Join-Path $root ".tmp"
$env:TEMP = $env:TMP
$env:PYTHONIOENCODING = "utf-8"
Set-Location $root

if ($Electron) {
    $electronExe = Join-Path $root "desktop\electron\node_modules\electron\dist\electron.exe"
    if (-not (Test-Path $electronExe)) {
        Write-Host "Electron 未安装：cd desktop\electron; npm install" -ForegroundColor Yellow
        exit 2
    }
    # some agent runtimes set this, which turns electron.exe into plain node
    Remove-Item Env:ELECTRON_RUN_AS_NODE -ErrorAction SilentlyContinue
    & $electronExe (Join-Path $root "desktop\electron")
} else {
    $cliArgs = @((Join-Path $root "desktop\runner.py"))
    if ($Fresh) { $cliArgs += "--fresh" }
    & $python @cliArgs
}
