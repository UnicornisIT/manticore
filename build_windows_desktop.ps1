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

$Certificate = $null
$SignerSha256 = ''
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
$PyInstallerWorkDirectory = Join-Path $BuildDirectory 'Manticore'
if (Test-Path -LiteralPath $PyInstallerWorkDirectory) {
    $ResolvedWorkDirectory = (Resolve-Path -LiteralPath $PyInstallerWorkDirectory).Path
    $ExpectedWorkDirectory = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'build\Manticore'))
    if ($ResolvedWorkDirectory -ne $ExpectedWorkDirectory -or (Get-Item -LiteralPath $PyInstallerWorkDirectory).Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw 'Unsafe PyInstaller work directory.'
    }
    Get-ChildItem -LiteralPath $PyInstallerWorkDirectory -Force -Recurse | ForEach-Object {
        $_.Attributes = $_.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
    }
    $WorkDirectoryItem = Get-Item -LiteralPath $PyInstallerWorkDirectory -Force
    $WorkDirectoryItem.Attributes = $WorkDirectoryItem.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
    Remove-Item -LiteralPath $PyInstallerWorkDirectory -Recurse -Force
}
$TrustPolicy = [ordered]@{
    github_repository = $Repository
    signer_certificate_sha256 = $SignerSha256
    allow_unsigned_updates = (-not [bool]$CertificateThumbprint)
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
    $ProjectPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $ProjectPython) {
        & $ProjectPython -m venv $VirtualEnvironment
    } else {
        $PyLauncher = Get-Command py -ErrorAction SilentlyContinue
        if ($PyLauncher) {
            & $PyLauncher.Source -3 -m venv $VirtualEnvironment
        }
        if (-not (Test-Path -LiteralPath $Python)) {
            $SystemPython = Get-Command python -ErrorAction SilentlyContinue
            if ($SystemPython) {
                & $SystemPython.Source -m venv $VirtualEnvironment
            }
        }
    }
    if (-not (Test-Path -LiteralPath $Python)) {
        throw 'Не удалось создать Python-окружение для Desktop-сборки. Установите Python 3.11 x64.'
    }
}

if (-not $SkipDependencies) {
    & $Python -m pip install -r (Join-Path $ProjectRoot 'requirements-desktop.lock')
    if ($LASTEXITCODE -ne 0) { throw 'Desktop dependencies installation failed.' }
}

Push-Location $ProjectRoot
try {
    & $Python desktop/release_tools.py --version-resource
    if ($LASTEXITCODE -ne 0) { throw 'Version resource generation failed.' }
    & $Python -m PyInstaller --clean --noconfirm (Join-Path $ProjectRoot 'desktop\Manticore.spec')
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller завершился с ошибкой $LASTEXITCODE."
    }
    Sign-Binary (Join-Path $ProjectRoot 'dist\Manticore.exe')

    if (-not $SkipInstaller) {
        $CompilerCandidates = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
            (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'),
            (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')
        ) | Where-Object { $_ -and (Test-Path -LiteralPath $_) }
        $Compiler = $CompilerCandidates | Select-Object -First 1
        if (-not $Compiler) {
            throw "Inno Setup 6 не найден. Установите его или запустите скрипт с -SkipInstaller."
        }
        $InstallerStage = Join-Path $BuildDirectory ('installer-' + [Guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $InstallerStage | Out-Null
        & $Compiler "/O$InstallerStage" "/DMyAppVersion=$Version" (Join-Path $ProjectRoot 'desktop\Manticore.iss')
        if ($LASTEXITCODE -ne 0) {
            throw "Inno Setup завершился с ошибкой $LASTEXITCODE."
        }
        $StagedInstaller = Join-Path $InstallerStage "Manticore-Setup-$Version.exe"
        Sign-Binary $StagedInstaller
        $InstallerOutput = Join-Path $ProjectRoot 'dist\installer'
        New-Item -ItemType Directory -Force -Path $InstallerOutput | Out-Null
        Move-Item -LiteralPath $StagedInstaller -Destination (Join-Path $InstallerOutput "Manticore-Setup-$Version.exe") -Force
        & $Python desktop/release_tools.py --metadata
        if ($LASTEXITCODE -ne 0) { throw 'Release metadata generation failed.' }
    }
} finally {
    Pop-Location
}

Write-Host "Windows-клиент собран: $ProjectRoot\dist\Manticore.exe"
if (-not $SkipInstaller) {
    Write-Host "Установщик готов: $ProjectRoot\dist\installer\Manticore-Setup-$Version.exe"
}
