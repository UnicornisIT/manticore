param(
    [switch]$SkipDependencies,
    [switch]$SkipInstaller,
    [switch]$SkipExecutableBuild,
    [switch]$SkipSigning,
    [switch]$SkipReleaseMetadata,
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

if ($SkipExecutableBuild -and $SkipInstaller) {
    throw 'Nothing to build: both executable and installer builds are disabled.'
}

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
    if (-not $SkipSigning) {
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
}

function Remove-SafeBuildDirectory([string]$Path, [string]$ExpectedPath) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $ResolvedPath = (Resolve-Path -LiteralPath $Path).Path
    $ExpectedFullPath = [IO.Path]::GetFullPath($ExpectedPath)
    $Item = Get-Item -LiteralPath $Path -Force
    if ($ResolvedPath -ne $ExpectedFullPath -or $Item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Unsafe build cleanup target: $ResolvedPath"
    }
    Get-ChildItem -LiteralPath $Path -Force -Recurse | ForEach-Object {
        $_.Attributes = $_.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
    }
    $Item.Attributes = $Item.Attributes -band (-bnot [IO.FileAttributes]::ReadOnly)
    Remove-Item -LiteralPath $Path -Recurse -Force
}

$BuildDirectory = Join-Path $ProjectRoot 'build'
$DistributionDirectory = Join-Path $ProjectRoot 'dist'
if (-not $SkipExecutableBuild) {
    Remove-SafeBuildDirectory $BuildDirectory (Join-Path $ProjectRoot 'build')
    Remove-SafeBuildDirectory $DistributionDirectory (Join-Path $ProjectRoot 'dist')
}
New-Item -ItemType Directory -Force -Path $BuildDirectory | Out-Null
$TrustPolicy = [ordered]@{
    github_repository = $Repository
    signer_certificate_sha256 = $SignerSha256
    allow_unsigned_updates = (-not [bool]$CertificateThumbprint)
}
$TrustPolicy | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $BuildDirectory 'trusted_update.json') -Encoding UTF8

function Sign-Binary([string]$Path) {
    if ($SkipSigning -or -not $CertificateThumbprint) {
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

$SchemaVersion = (& $Python -c "from db_safety import CURRENT_SCHEMA_VERSION; print(CURRENT_SCHEMA_VERSION)").Trim()
if ($LASTEXITCODE -ne 0 -or $SchemaVersion -notmatch '^\d+$') {
    throw 'Could not determine CURRENT_SCHEMA_VERSION for build metadata.'
}
$Commit = (& git -C $ProjectRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $Commit -notmatch '^[0-9a-f]{40}$') {
    throw 'Could not determine the Git commit for build metadata.'
}
[ordered]@{
    version = $Version
    schema_version = [int]$SchemaVersion
    commit = $Commit
} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $BuildDirectory 'build-metadata.json') -Encoding UTF8

Push-Location $ProjectRoot
try {
    if (-not $SkipExecutableBuild) {
        & $Python desktop/release_tools.py --version-resource
        if ($LASTEXITCODE -ne 0) { throw 'Version resource generation failed.' }
        & $Python -m PyInstaller --clean --noconfirm (Join-Path $ProjectRoot 'desktop\Manticore.spec')
        if ($LASTEXITCODE -ne 0) {
            throw "PyInstaller завершился с ошибкой $LASTEXITCODE."
        }
        Sign-Binary (Join-Path $ProjectRoot 'dist\Manticore.exe')
    } elseif (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'dist\Manticore.exe') -PathType Leaf)) {
        throw 'Cannot build installer because dist\Manticore.exe does not exist.'
    }

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
        if (-not $SkipReleaseMetadata) {
            & $Python desktop/release_tools.py --metadata
            if ($LASTEXITCODE -ne 0) { throw 'Release metadata generation failed.' }
        }
    }
} finally {
    Pop-Location
}

if (-not $SkipExecutableBuild) {
    Write-Host "Windows-клиент собран: $ProjectRoot\dist\Manticore.exe"
}
if (-not $SkipInstaller) {
    Write-Host "Установщик готов: $ProjectRoot\dist\installer\Manticore-Setup-$Version.exe"
}
