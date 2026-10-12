@echo off
call "%~dp0scripts\update_and_run_windows.bat" %*
exit /b %errorlevel%
