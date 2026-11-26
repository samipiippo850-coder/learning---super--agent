@echo off
chcp 65001 >nul
title ExamOS
cd /d "%~dp0"
echo.
echo   Starting ExamOS in background ...
echo   Dashboard: http://127.0.0.1:8000/
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start.ps1" -Background
if errorlevel 1 (
  echo.
  echo Startup failed. Please send the error above to the developer.
  pause
) else (
  timeout /t 2 >nul
)
