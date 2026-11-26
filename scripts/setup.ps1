# ExamOS setup: backend virtualenv + frontend dependencies
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

function Find-Uv {
    $candidate = Join-Path (Split-Path -Parent $root) ".tools\uv.exe"
    if (Test-Path $candidate) { return $candidate }
    $onPath = Get-Command uv -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    return $null
}

Write-Host "==> backend virtualenv" -ForegroundColor Cyan
$python = "python"
$bundled = "C:\Users\$env:USERNAME\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
if (Test-Path $bundled) { $python = $bundled }

$uv = Find-Uv
if (-not (Test-Path "$root\.venv\Scripts\python.exe")) {
    if ($uv) {
        $env:UV_CACHE_DIR = Join-Path $root ".uv-cache"
        & $uv venv "$root\.venv" --python $python
    } else {
        & $python -m venv "$root\.venv"
    }
}
$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if ($uv) {
    $env:UV_CACHE_DIR = Join-Path $root ".uv-cache"
    & $uv pip install --python $venvPython -r (Join-Path $root "backend\requirements.txt")
} else {
    & $venvPython -m pip install -r (Join-Path $root "backend\requirements.txt")
}

Write-Host "==> frontend dependencies" -ForegroundColor Cyan
$env:npm_config_cache = Join-Path $root ".npm-cache"
$env:npm_config_update_notifier = "false"
Push-Location (Join-Path $root "web")
try {
    npm install --no-audit --no-fund
} finally {
    Pop-Location
}

Write-Host "`nDone. Next: powershell -File .\scripts\seed.ps1" -ForegroundColor Green
