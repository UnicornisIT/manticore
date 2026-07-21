@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_windows.ps1"
echo.
echo Press any key to close this window.
pause >nul
