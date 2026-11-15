@echo off
REM WiFi-Doctor one-click launcher for Windows. Double-click me.
title WiFi-Doctor
cd /d "%~dp0.."
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -NoExit -File "%~dp0run_local.ps1"
pause
