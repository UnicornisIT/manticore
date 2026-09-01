param(
    [switch]$SkipDependencies,
    [switch]$SkipInstaller,
    [string]$CertificateThumbprint,
    [switch]$UnsignedDevelopmentBuild,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$VirtualEnvironment = Join-Path $ProjectRoot '.venv-desktop'
$Python = Join-Path $VirtualEnvironment 'Scripts\python.exe'
$Version = (Get-Content (Join-Path $ProjectRoot 'VERSION') -Raw).Trim().TrimStart('v', 'V')
$Repository = 'UnicornisIT/manticore'

if ($Version -notmatch '^\d+\.\d+\.\d+([+-][0-9A-Za-z.-]+)?$') {
    throw "Файл VERSION должен содержать версию в формате 1.2.3."
}
if (-not $CertificateThumbprint -and -not $UnsignedDevelopmentBuild) {
    throw "Для безопасной сборки укажите -CertificateThumbprint. Для тестового EXE без обновлений явно укажите -UnsignedDevelopmentBuild."
}

$Certificate = $null
$SignerSha256 = '0' * 64
$SignTool = $null
if ($CertificateThumbprint) {
    $NormalizedThumbprint = $CertificateThumbprint.Replace(' ', '').ToUpperInvariant()
    $Certificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $NormalizedThumbprint } |
        Select-Object -First 1
    if (-not $Certificate) {
        throw "Сертификат подписи $NormalizedThumbprint не найден в хранилище Windows."
    }
    $Sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $SignerSha256 = ([BitConverter]::ToString($Sha.ComputeHash($Certificate.RawData))).Replace('-', '').ToLowerInvariant()
    } finally {
        $Sha.Dispose()
    }
    $SignToolCommand = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($SignToolCommand) {
        $SignTool = $SignToolCommand.Source
    } else {
        $WindowsKitsBin = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
        if (Test-Path -LiteralPath $WindowsKitsBin) {
            $SignTool = Get-ChildItem $WindowsKitsBin -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' } |
                Sort-Object FullName -Descending |
                Select-Object -ExpandProperty FullName -First 1
        }
    }
    if (-not $SignTool) {
        throw "signtool.exe не найден. Установите Windows SDK с Signing Tools."
    }
}

$BuildDirectory = Join-Path $ProjectRoot 'build'
New-Item -ItemType Directory -Force -Path $BuildDirectory | Out-Null
$TrustPolicy = [ordered]@{
    github_repository = $Repository
    signer_certificate_sha256 = $SignerSha256
}
$TrustPolicy | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $BuildDirectory 'trusted_update.json') -Encoding UTF8

function Sign-Binary([string]$Path) {
    if (-not $CertificateThumbprint) {
        return
    }
    & $SignTool sign /sha1 $Certificate.Thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Не удалось подписать $Path."
    }
}

if (-not (Test-Path -LiteralPath $Python)) {
    $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($PyLauncher) {
        & $PyLauncher.Source -3 -m venv $VirtualEnvironment
    } else {
        $SystemPython = Get-Command python -ErrorAction Stop
        & $SystemPython.Source -m venv $VirtualEnvironment
    }
}

if (-not $SkipDependencies) {
    & $Python -m pip install --upgrade pip
    & $Python -m pip install -r (Join-Path $ProjectRoot 'requirements-desktop.txt')
}

Push-Location $ProjectRoot
try {
    & $Python -m PyInstaller --clean --noconfirm (Join-Path $ProjectRoot 'desktop\Manticore.spec')
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller завершился с ошибкой $LASTEXITCODE."
    }
    Sign-Binary (Join-Path $ProjectRoot 'dist\Manticore.exe')

    if (-not $SkipInstaller) {
        $CompilerCandidates = @(
            (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
            (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
        $Compiler = $CompilerCandidates | Select-Object -First 1
        if (-not $Compiler) {
            throw "Inno Setup 6 не найден. Установите его или запустите скрипт с -SkipInstaller."
        }
        & $Compiler "/DMyAppVersion=$Version" (Join-Path $ProjectRoot 'desktop\Manticore.iss')
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup завершился с ошибкой $LASTEXITCODE."
        }
        Sign-Binary (Join-Path $ProjectRoot "dist\installer\Manticore-Setup-$Version.exe")
    }
} finally {
    Pop-Location
}

Write-Host "Windows-клиент собран: $ProjectRoot\dist\Manticore.exe"
if (-not $SkipInstaller) {
    Write-Host "Установщик готов: $ProjectRoot\dist\installer\Manticore-Setup-$Version.exe"
}
