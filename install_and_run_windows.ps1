$ErrorActionPreference = "Stop"

$AppDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $AppDir

$LogPath = Join-Path $AppDir "install_run.log"
$script:TranscriptStarted = $false
try {
    Start-Transcript -Path $LogPath -Force | Out-Null
    $script:TranscriptStarted = $true
}
catch {
    $script:TranscriptStarted = $false
}

trap {
    Write-Host ""
    Write-Host "Installation or launch failed." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Log file: $LogPath" -ForegroundColor Yellow
    Write-Host "Send this file if you need help finding the exact reason." -ForegroundColor Yellow
    if ($script:TranscriptStarted) {
        Stop-Transcript | Out-Null
    }
    exit 1
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "== $Message ==" -ForegroundColor Cyan
}

function Write-AdminPasswordNotice {
    param([string]$Password)

    $restartTranscript = $false
    if ($script:TranscriptStarted) {
        Stop-Transcript | Out-Null
        $script:TranscriptStarted = $false
        $restartTranscript = $true
    }

    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host "ВАЖНО / IMPORTANT" -ForegroundColor Red
    Write-Host "Локальный администратор: admin" -ForegroundColor Yellow
    Write-Host "Admin password: $Password" -ForegroundColor Green
    Write-Host "RU: Сохраните этот пароль. При первом входе обязательно смените пароль администратора." -ForegroundColor Yellow
    Write-Host "EN: Save this password. You must change the admin password after the first login." -ForegroundColor Yellow
    Write-Host "============================================================" -ForegroundColor Yellow
    Write-Host ""

    if ($restartTranscript) {
        try {
            Start-Transcript -Path $LogPath -Append | Out-Null
            $script:TranscriptStarted = $true
        }
        catch {
            $script:TranscriptStarted = $false
        }
    }
}

function Write-CommandLine {
    param(
        [string]$Exe,
        [string[]]$ArgumentList
    )

    Write-Host ("Command: {0} {1}" -f $Exe, ($ArgumentList -join " ")) -ForegroundColor DarkGray
}

function Test-Python311 {
    param(
        [string]$Exe,
        [string[]]$ArgumentList
    )

    try {
        $checkArgs = @()
        $checkArgs += $ArgumentList
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
        if (Test-Python311 -Exe $candidate.Exe -ArgumentList $candidate.Args) {
            return $candidate
        }
    }

    return $null
}

function Install-Python311 {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.11 64-bit was not found, and winget is not available for automatic installation. Install Python 3.11 64-bit from https://www.python.org/downloads/windows/ with the Add Python to PATH checkbox, then run start_one_click.bat again."
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
        [string[]]$ArgumentList
    )

    Write-Step $Title
    Write-CommandLine -Exe $Exe -ArgumentList $ArgumentList
    & $Exe @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "$Title failed with exit code $LASTEXITCODE."
    }
}

function Remove-BrokenVenv {
    param([string]$VenvDir)

    if (-not (Test-Path -LiteralPath $VenvDir)) {
        return
    }

    $resolvedAppDir = (Resolve-Path -LiteralPath $AppDir).Path.TrimEnd("\")
    $resolvedVenvDir = (Resolve-Path -LiteralPath $VenvDir).Path

    if (-not $resolvedVenvDir.StartsWith($resolvedAppDir, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove virtual environment outside the project folder: $resolvedVenvDir"
    }

    Write-Step "Removing incomplete virtual environment"
    Write-Host "Folder: $resolvedVenvDir" -ForegroundColor Yellow
    Remove-Item -LiteralPath $resolvedVenvDir -Recurse -Force
}

$python = Get-Python311
if (-not $python) {
    Write-Host ""
    Write-Host "Python 3.11 64-bit was not found. Trying automatic installation..." -ForegroundColor Yellow
    Install-Python311
    $python = Get-Python311
}

if (-not $python) {
    throw "Python 3.11 64-bit was not found after installation."
}

Write-Step "Using Python 3.11"
$pythonInfoArgs = @()
$pythonInfoArgs += $python.Args
$pythonInfoArgs += @(
    "-c",
    "import platform, sys; print(sys.executable); print(sys.version.split()[0]); print(platform.architecture()[0])"
)
Write-CommandLine -Exe $python.Exe -ArgumentList $pythonInfoArgs
& $python.Exe @pythonInfoArgs
if ($LASTEXITCODE -ne 0) {
    throw "Could not read Python 3.11 information."
}

$venvDir = Join-Path $AppDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

if ((Test-Path -LiteralPath $venvDir) -and -not (Test-Path -LiteralPath $venvPython)) {
    Write-Host ""
    Write-Host "Found .venv folder, but .venv\Scripts\python.exe is missing." -ForegroundColor Yellow
    Write-Host "This usually means the previous virtual environment creation was interrupted." -ForegroundColor Yellow
    Remove-BrokenVenv -VenvDir $venvDir
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    $venvArgs = @()
    $venvArgs += $python.Args
    $venvArgs += @("-m", "venv", $venvDir)
    Invoke-Checked -Title "Creating virtual environment" -Exe $python.Exe -ArgumentList $venvArgs
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Virtual environment was not created. Delete the .venv folder, reinstall Python 3.11 64-bit, and run start_one_click.bat again."
}

Invoke-Checked -Title "Checking virtual environment" -Exe $venvPython -ArgumentList @("-c", "import sys; print(sys.executable)")
Invoke-Checked -Title "Upgrading pip" -Exe $venvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip")
Invoke-Checked -Title "Installing project dependencies" -Exe $venvPython -ArgumentList @("-m", "pip", "install", "-r", "requirements.txt")

if (-not (Test-Path -LiteralPath "uploads")) {
    New-Item -ItemType Directory -Path "uploads" | Out-Null
}

if (-not (Test-Path -LiteralPath ".env")) {
    $chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789".ToCharArray()
    $secret = -join (1..48 | ForEach-Object { $chars | Get-Random })
    $adminPassword = -join (1..20 | ForEach-Object { $chars | Get-Random })
    @(
        "SECRET_KEY=$secret",
        "ADMIN_DEFAULT_PASSWORD=$adminPassword",
        "UPLOAD_FOLDER=uploads",
        "DB_FILENAME=baze.db",
        "DEFAULT_CAMPAIGN_YEAR=2026",
        "LEGACY_CAMPAIGN_YEAR=2025",
        "FLASK_ENV=production",
        "APP_HOST=0.0.0.0",
        "APP_PORT=5000",
        "APP_DEBUG=false"
    ) | Set-Content -Path ".env" -Encoding UTF8
    Write-Host "Generated local admin password and saved it in .env." -ForegroundColor Yellow
    Write-AdminPasswordNotice -Password $adminPassword
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

Write-Step "Starting manticore"
Write-Host "This computer: http://127.0.0.1:5000" -ForegroundColor Green
if ($lanIp) {
    Write-Host "Local network: http://${lanIp}:5000" -ForegroundColor Green
}
Write-Host "If Windows Firewall asks, allow access for private networks." -ForegroundColor Yellow
Write-Host ""

& $venvPython "app.py"

if ($script:TranscriptStarted) {
    Stop-Transcript | Out-Null
}
