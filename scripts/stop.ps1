# Stop the background ExamOS server started by `start.ps1 -Background`.
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
$varDir = Join-Path $root "backend\var"
$pidFile = Join-Path $varDir "server.pid"

$stopped = $false
if (Test-Path $pidFile) {
  $serverPid = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ($serverPid) {
    try {
      $proc = Get-Process -Id ([int]$serverPid) -ErrorAction Stop
      taskkill /PID $proc.Id /T /F 2>$null | Out-Null
      $stopped = $true
      Write-Host "已停止 ExamOS（PID $serverPid）。" -ForegroundColor Green
    } catch { }
  }
  Remove-Item -Force $pidFile -ErrorAction SilentlyContinue
}

# fallback: kill whatever still listens on 8000
$listeners = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
foreach ($listener in $listeners) {
  try {
    taskkill /PID $listener.OwningProcess /T /F 2>$null | Out-Null
    $stopped = $true
  } catch { }
}

if (-not $stopped) {
  Write-Host "没有发现正在运行的 ExamOS。" -ForegroundColor Yellow
}
