# Start the ExamOS API with auto-reload (development)
param([int]$Port = 8000)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$env:TMP = Join-Path $root ".tmp"
$env:TEMP = $env:TMP
$env:PYTHONPATH = Join-Path $root "backend"
$env:EXAMOS_VAR_DIR = Join-Path $root "backend\var"
$env:PYTHONIOENCODING = "utf-8"
Set-Location (Join-Path $root "backend")
& $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --reload
