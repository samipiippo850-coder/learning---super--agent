# Build a standalone ExamOS.exe (PyInstaller, onedir, Edge/Chrome app window)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$uv = Join-Path (Split-Path -Parent $root) ".tools\uv.exe"
$env:UV_CACHE_DIR = Join-Path $root ".uv-cache"

if (-not (Test-Path $python)) { throw "run .\scripts\setup.ps1 first" }

Write-Host "==> ensuring pyinstaller" -ForegroundColor Cyan
if (Test-Path $uv) {
    & $uv pip install --python $python pyinstaller
} else {
    & $python -m pip install pyinstaller
}

if (-not (Test-Path (Join-Path $root "web\out\index.html"))) {
    $env:npm_config_cache = Join-Path $root ".npm-cache"
    Push-Location (Join-Path $root "web")
    try { npm run build } finally { Pop-Location }
}

$entry = Join-Path $root "desktop\runner.py"
$icon = Join-Path $root "assets\examos.ico"
$sep = ";"
$buildArgs = @(
    "--noconfirm", "--clean", "--windowed",
    "--name", "ExamOS",
    "--icon", $icon,
    "--distpath", (Join-Path $root "dist"),
    "--workpath", (Join-Path $root ".build"),
    "--specpath", (Join-Path $root ".build"),
    "--paths", (Join-Path $root "backend"),
    "--add-data", ((Join-Path $root "web\out") + $sep + "web/out"),
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan.on",
    "--collect-submodules", "app",
    "--collect-submodules", "langgraph",
    "--collect-submodules", "langchain_core",
    $entry
)
Push-Location $root
try {
    & $python -m PyInstaller @buildArgs
    Write-Host "`nBuilt: $root\dist\ExamOS\ExamOS.exe" -ForegroundColor Green
} finally {
    Pop-Location
}
