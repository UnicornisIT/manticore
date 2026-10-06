param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$CertificateThumbprint,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)

$ErrorActionPreference = 'Stop'
$ResolvedPath = (Resolve-Path -LiteralPath $Path).Path
$NormalizedThumbprint = $CertificateThumbprint.Replace(' ', '').ToUpperInvariant()
$Certificate = Get-ChildItem Cert:\CurrentUser\My, Cert:\LocalMachine\My -ErrorAction SilentlyContinue |
    Where-Object { $_.Thumbprint -eq $NormalizedThumbprint } |
    Select-Object -First 1
if (-not $Certificate) {
    throw "Code-signing certificate $NormalizedThumbprint was not found."
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
    throw 'signtool.exe was not found. Install Windows SDK Signing Tools.'
}

& $SignTool sign /sha1 $Certificate.Thumbprint /fd SHA256 /tr $TimestampUrl /td SHA256 $ResolvedPath
if ($LASTEXITCODE -ne 0) {
    throw "Could not sign $ResolvedPath."
}
Write-Host "Signed binary: $ResolvedPath"
