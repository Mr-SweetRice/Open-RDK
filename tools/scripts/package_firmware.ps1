param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern("^[a-z0-9_]+$")]
    [string]$ModuleName,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$modulePath = Join-Path $repoRoot "firmware\esp\modules\$ModuleName"
$buildPath = Join-Path $modulePath "build"
$assetPath = Join-Path $repoRoot "host\main\src\openrdk\firmware\$ModuleName"

if (-not (Test-Path -LiteralPath $modulePath -PathType Container)) {
    throw "Firmware module not found: $modulePath"
}

if (-not $SkipBuild) {
    $powerShellExe = (Get-Process -Id $PID).Path
    & $powerShellExe -NoProfile -ExecutionPolicy Bypass `
        -File (Join-Path $PSScriptRoot "build.ps1") `
        -ModulePath $modulePath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$files = @{
    (Join-Path $buildPath "bootloader\bootloader.bin") = "bootloader.bin"
    (Join-Path $buildPath "partition_table\partition-table.bin") = "partition-table.bin"
    (Join-Path $buildPath "$ModuleName.bin") = "$ModuleName.bin"
}

foreach ($source in $files.Keys) {
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Build artifact not found: $source"
    }
}

New-Item -ItemType Directory -Path $assetPath -Force | Out-Null
foreach ($entry in $files.GetEnumerator()) {
    Copy-Item -LiteralPath $entry.Key -Destination (Join-Path $assetPath $entry.Value) -Force
}

Write-Host "Packaged $ModuleName firmware assets in $assetPath"
