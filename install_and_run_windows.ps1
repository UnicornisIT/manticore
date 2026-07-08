$ErrorActionPreference = "Stop"

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AppDir

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Test-Python311 {
    param(
        [string]$Exe,
        [string[]]$Args
    )

    try {
        $checkArgs = @()
        $checkArgs += $Args
        $checkArgs += @(
            "-c",
            "import platform, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) and platform.architecture()[0] == '64bit' else 1)"
        )
        & $Exe @checkArgs | Out-Null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Get-Python311 {
    $candidates = @(
        @{ Exe = "py"; Args = @("-3.11") },
        @{ Exe = "python"; Args = @() },
        @{ Exe = "python3.11"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        if (Test-Python311 -Exe $candidate.Exe -Args $candidate.Args) {
            return $candidate
        }
    }

    return $null
}

function Install-Python311 {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "winget was not found. Install Python 3.11 64-bit from https://www.python.org/downloads/windows/ and run start_one_click.bat again."
    }

    Write-Step "Installing Python 3.11 64-bit with winget"
    & winget install --id Python.Python.3.11 --exact --scope user --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.11 installation failed. Install Python 3.11 64-bit manually and run start_one_click.bat again."
    }

    $localPython = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311"
    $env:PATH = "$localPython;$localPython\Scripts;$env:PATH"
}

function Invoke-Checked {
    param(
        [string]$Title,
        [string]$Exe,
        [string[]]$Args
    )

    Write-Step $Title
    & $Exe @Args
    if ($LASTEXITCODE -ne 0) {
        throw "$Title failed with exit code $LASTEXITCODE."
    }
}

$python = Get-Python311
if (-not $python) {
    Install-Python311
    $python = Get-Python311
}

if (-not $python) {
    throw "Python 3.11 64-bit was not found after installation."
}

$venvPython = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    $venvArgs = @()
    $venvArgs += $python.Args
    $venvArgs += @("-m", "venv", ".venv")
    Invoke-Checked -Title "Creating virtual environment" -Exe $python.Exe -Args $venvArgs
}

Invoke-Checked -Title "Upgrading pip" -Exe $venvPython -Args @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Checked -Title "Installing project dependencies" -Exe $venvPython -Args @("-m", "pip", "install", "-r", "requirements.txt")

if (-not (Test-Path -LiteralPath "uploads")) {
    New-Item -ItemType Directory -Path "uploads" | Out-Null
}

if (-not (Test-Path -LiteralPath ".env")) {
    $chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".ToCharArray()
    $secret = -join (1..48 | ForEach-Object { $chars | Get-Random })
    @(
        "SECRET_KEY=$secret",
        "ADMIN_DEFAULT_PASSWORD=123",
        "UPLOAD_FOLDER=uploads",
        "DB_FILENAME=baze.db",
        "DEFAULT_CAMPAIGN_YEAR=2026",
        "LEGACY_CAMPAIGN_YEAR=2025",
        "FLASK_ENV=production"
    ) | Set-Content -Path ".env" -Encoding UTF8
}

$lanIp = $null
try {
    $lanIp = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.IPAddress -notlike "169.254*" } |
        Select-Object -First 1 -ExpandProperty IPAddress
}
catch {
    $lanIp = $null
}

Write-Step "Starting Logins Moodle"
Write-Host "This computer: http://127.0.0.1:5000" -ForegroundColor Green
if ($lanIp) {
    Write-Host "Local network: http://${lanIp}:5000" -ForegroundColor Green
}
Write-Host "If Windows Firewall asks, allow access for private networks." -ForegroundColor Yellow
Write-Host ""

& $venvPython "app.py"
