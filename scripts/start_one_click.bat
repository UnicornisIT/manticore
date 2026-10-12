@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_and_run_windows.ps1"
echo.
echo Press any key to close this window.
pause >nul
