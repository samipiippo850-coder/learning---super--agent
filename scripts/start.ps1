# Start ExamOS.
#
#   powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1              # foreground console
#   powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 -Background  # background + browser
#   powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1 -NoBrowser
param(
  [int]$Port = 8000,
  [switch]$NoBrowser,
  [switch]$Background
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$backend = Join-Path $root "backend"
$webOut = Join-Path $root "web\out"
$varDir = Join-Path $backend "var"
$pidFile = Join-Path $varDir "server.pid"
$logFile = Join-Path $varDir "server.log"

function Test-Health([int]$p, [int]$TimeoutSeconds = 60) {
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    try {
      $r = Invoke-WebRequest "http://127.0.0.1:$p/health" -TimeoutSec 3 -UseBasicParsing
      if ($r.StatusCode -eq 200) { return $true }
    } catch { Start-Sleep -Milliseconds 400 }
  }
  return $false
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host " ExamOS 启动器" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# 0) already running? ------------------------------------------------------
if (Test-Health $Port 2) {
  Write-Host "ExamOS 已经在 http://127.0.0.1:$Port/ 运行。" -ForegroundColor Green
  if (-not $NoBrowser) { Start-Process "http://127.0.0.1:$Port/" }
  exit 0
}

# 1) port check ------------------------------------------------------------
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) {
  Write-Host "端口 $Port 已被其他程序占用（进程 $($busy[0].OwningProcess)）。" -ForegroundColor Yellow
  Write-Host "换端口重试：-Port 8001" -ForegroundColor Yellow
  exit 1
}

# 2) backend env -----------------------------------------------------------
if (-not (Test-Path $python)) {
  Write-Host "首次运行：安装后端依赖（约 2-3 分钟）…" -ForegroundColor Yellow
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup.ps1")
  if ($LASTEXITCODE -ne 0) { throw "依赖安装失败" }
  $python = Join-Path $root ".venv\Scripts\python.exe"
}

# 3) frontend build --------------------------------------------------------
if (-not (Test-Path (Join-Path $webOut "index.html"))) {
  Write-Host "首次运行：构建前端看板（约 1-2 分钟）…" -ForegroundColor Yellow
  $env:npm_config_cache = Join-Path $root ".npm-cache"
  Push-Location (Join-Path $root "web")
  try {
    npm install --no-audit --no-fund
    npm run build
  } finally { Pop-Location }
}

# 4) environment -----------------------------------------------------------
New-Item -ItemType Directory -Force -Path $varDir | Out-Null
$env:TMP = Join-Path $root ".tmp"
$env:TEMP = $env:TMP
New-Item -ItemType Directory -Force -Path $env:TMP | Out-Null
$env:PYTHONPATH = $backend
$env:EXAMOS_VAR_DIR = $varDir
$env:EXAMOS_WEB_OUT = $webOut
$env:EXAMOS_PORT = "$Port"
$env:PYTHONIOENCODING = "utf-8"

# 5) seed on first run -----------------------------------------------------
Push-Location $backend
try {
  & $python -c @"
from app.db import init_db, session_scope
from sqlalchemy import func, select
from app.models import Question
init_db()
with session_scope() as db:
    if (db.scalar(select(func.count(Question.id))) or 0) == 0:
        from app.seed.generator import seed_demo
        r = seed_demo(db, force=True)
        print('已写入演示数据: ', {k: v for k, v in r.items() if k != 'note'})
    else:
        print('数据库已就绪')
"@
  if ($LASTEXITCODE -ne 0) { throw "数据库初始化失败" }
} finally { Pop-Location }

$url = "http://127.0.0.1:$Port/"

if ($Background) {
  # launch through a tiny VBS so the server is fully detached (hidden, no console,
  # and this PowerShell process can exit immediately)
  Write-Host "正在后台启动 ExamOS …" -ForegroundColor Yellow
  $vbs = Join-Path $varDir "serve-hidden.vbs"
  $vbsBody = @"
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "$backend"
sh.Environment("PROCESS")("PYTHONPATH") = "$backend"
sh.Environment("PROCESS")("EXAMOS_VAR_DIR") = "$varDir"
sh.Environment("PROCESS")("EXAMOS_WEB_OUT") = "$webOut"
sh.Environment("PROCESS")("EXAMOS_PORT") = "$Port"
sh.Environment("PROCESS")("PYTHONIOENCODING") = "utf-8"
sh.Run """$python"" -m uvicorn app.main:app --host 127.0.0.1 --port $Port --log-level warning", 0, False
"@
  Set-Content -Path $vbs -Value $vbsBody -Encoding ASCII
  & wscript.exe $vbs
  if (-not (Test-Health $Port 90)) {
    Write-Host "启动失败，日志：$logFile" -ForegroundColor Red
    Get-Content (Join-Path $varDir "server.error.log") -Tail 20 -ErrorAction SilentlyContinue
    exit 1
  }
  # remember the real listener PID (venv python re-execs the base interpreter)
  $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($listener) {
    $rootPid = (Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)").ParentProcessId
    Set-Content -Path $pidFile -Value $rootPid -Encoding ascii
  }
  Write-Host ""
  Write-Host "ExamOS 已在后台运行：$url" -ForegroundColor Green
  Write-Host "停止服务：双击 停止 ExamOS.cmd（或运行 scripts\stop.ps1）" -ForegroundColor DarkGray
  if (-not $NoBrowser) { Start-Process $url }
  exit 0
}

# 6) foreground mode: keep this console open ---------------------------------
if (-not $NoBrowser) {
  Start-Job -ScriptBlock {
    param($u)
    for ($i = 0; $i -lt 120; $i++) {
      try {
        $r = Invoke-WebRequest "$u/health" -TimeoutSec 2 -UseBasicParsing
        if ($r.StatusCode -eq 200) { Start-Process $u; break }
      } catch { Start-Sleep -Milliseconds 500 }
    }
  } -ArgumentList $url | Out-Null
}

Write-Host ""
Write-Host "看板地址: $url" -ForegroundColor Green
Write-Host "API 文档: ${url}docs" -ForegroundColor Green
Write-Host "关闭本窗口即停止 ExamOS。" -ForegroundColor DarkGray
Write-Host ""
Push-Location $backend
try {
  & $python -m uvicorn app.main:app --host 127.0.0.1 --port $Port --log-level info
} finally { Pop-Location }
