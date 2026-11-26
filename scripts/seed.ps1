# Seed the ExamOS demo dataset (idempotent; -Force rebuilds)
param([switch]$Force)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$env:TMP = Join-Path $root ".tmp"
$env:TEMP = $env:TMP
$env:PYTHONPATH = Join-Path $root "backend"
$env:EXAMOS_VAR_DIR = Join-Path $root "backend\var"
$env:PYTHONIOENCODING = "utf-8"
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null
Set-Location (Join-Path $root "backend")

$forceFlag = if ($Force) { "True" } else { "False" }
& $python -c @"
from app.db import init_db, session_scope
init_db()
from app.seed.generator import seed_demo
with session_scope() as db:
    result = seed_demo(db, force=$forceFlag)
    print({k: v for k, v in result.items() if k != 'note'})
"@
