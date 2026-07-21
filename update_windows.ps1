param(
    [switch]$StartAfterUpdate,
    [string]$AppDir = (Split-Path -Parent $MyInvocation.MyCommand.Path)
)

$ErrorActionPreference = "Stop"

Set-Location $AppDir

function Find-UpdatePython {
    $venvPython = Join-Path $AppDir ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        return @{ Exe = $venvPython; Args = @() }
    }

    $candidates = @(
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        try {
            $checkArgs = @()
            $checkArgs += $candidate.Args
            $checkArgs += @("-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)")
            & $candidate.Exe @checkArgs | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return $candidate
            }
        }
        catch {
            continue
        }
    }

    throw "Python was not found. Run start_one_click.bat once, or install Python 3.11."
}

$python = Find-UpdatePython
$updateScript = Join-Path $AppDir "update_app.py"
if (-not (Test-Path -LiteralPath $updateScript)) {
    throw "update_app.py was not found in $AppDir"
}

$updateArgs = @()
$updateArgs += $python.Args
$updateArgs += @($updateScript, $AppDir, "--requirements", "requirements.txt")

& $python.Exe @updateArgs
if ($LASTEXITCODE -ne 0) {
    throw "Update failed."
}

if ($StartAfterUpdate) {
    $startScript = Join-Path $AppDir "install_and_run_windows.ps1"
    if (-not (Test-Path -LiteralPath $startScript)) {
        throw "install_and_run_windows.ps1 was not found in $AppDir"
    }
    & $startScript
}
