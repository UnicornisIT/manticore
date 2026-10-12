$scriptPath = Join-Path $PSScriptRoot "scripts\install_and_run_windows.ps1"
& $scriptPath @args
exit $LASTEXITCODE
