# Run backend tests and frontend typecheck
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$env:TMP = Join-Path $root ".tmp"
$env:TEMP = $env:TMP
$env:PYTHONPATH = Join-Path $root "backend"
$env:PYTHONIOENCODING = "utf-8"

Write-Host "==> pytest" -ForegroundColor Cyan
Push-Location (Join-Path $root "backend")
try {
    & $python -m pytest -q
    $testExit = $LASTEXITCODE
} finally {
    Pop-Location
}

Write-Host "==> tsc --noEmit" -ForegroundColor Cyan
$node = "node"
$bundledNode = "C:\Users\$env:USERNAME\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\node\bin\node.exe"
if (-not (Get-Command node -ErrorAction SilentlyContinue) -and (Test-Path $bundledNode)) { $node = $bundledNode }
Push-Location (Join-Path $root "web")
try {
    & $node "node_modules\typescript\bin\tsc" --noEmit
    $tscExit = $LASTEXITCODE
} finally {
    Pop-Location
}

if ($testExit -ne 0 -or $tscExit -ne 0) { exit 1 }
Write-Host "All checks passed." -ForegroundColor Green
