# Apply the ExamOS icon to the desktop shortcut (and refresh the Explorer icon cache).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $root "assets\examos.ico"
if (-not (Test-Path $icon)) {
  throw "缺少图标文件：$icon（先运行 assets 生成步骤）"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "ExamOS.lnk"
$ws = New-Object -ComObject WScript.Shell

if (Test-Path $lnkPath) {
  $shortcut = $ws.CreateShortcut($lnkPath)
} else {
  $shortcut = $ws.CreateShortcut($lnkPath)
  $shortcut.TargetPath = Join-Path $root "启动 ExamOS.cmd"
  $shortcut.WorkingDirectory = $root
  $shortcut.Description = "ExamOS 个人定制化考研应试智能体"
}
$shortcut.IconLocation = "$icon,0"
$shortcut.Save()

# refresh Explorer icon cache (harmless if unsupported)
try { & ie4uinit.exe -show 2>$null | Out-Null } catch { }

Write-Host "桌面快捷方式图标已更新：" -ForegroundColor Green
Write-Host "  $lnkPath"
Write-Host "  icon = $icon"
Write-Host "若资源管理器仍显示旧图标：在桌面按 F5，或注销/重新登录一次。" -ForegroundColor DarkGray
