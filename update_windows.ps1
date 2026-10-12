param(
    [switch]$StartAfterUpdate,
    [string]$AppDir = $PSScriptRoot
)

$scriptPath = Join-Path $PSScriptRoot "scripts\update_windows.ps1"
& $scriptPath -StartAfterUpdate:$StartAfterUpdate -AppDir $AppDir
exit $LASTEXITCODE
