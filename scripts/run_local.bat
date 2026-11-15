@echo off
REM Double-click launcher for Windows: delegates to the PowerShell script.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_local.ps1"
pause
