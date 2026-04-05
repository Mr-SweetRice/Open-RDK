param(
    [string]$ModulePath = "firmware/esp/modules/traction_module",
    [string]$Port = "",
    [string]$Target = "esp32c3"
)

$ErrorActionPreference = "Stop"

function Resolve-ProjectPath {
    param([string]$InputPath)

    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    if ([System.IO.Path]::IsPathRooted($InputPath)) {
        return (Resolve-Path $InputPath).Path
    }

    return (Resolve-Path (Join-Path $repoRoot $InputPath)).Path
}

function Normalize-Path {
    param([string]$Path)

    return [System.IO.Path]::GetFullPath($Path).Replace("\", "/")
}

function Ensure-Idf {
    if (Get-Command idf.py -ErrorAction SilentlyContinue) {
        return
    }

    if ($env:IDF_PATH) {
        $exportScript = Join-Path $env:IDF_PATH "export.ps1"
        if (Test-Path $exportScript) {
            . $exportScript *> $null
        }
    }

    if (-not (Get-Command idf.py -ErrorAction SilentlyContinue)) {
        throw "idf.py not found. Set IDF_PATH or open an ESP-IDF shell first."
    }
}

function Remove-StaleBuild {
    param([string]$ProjectDir)

    $buildDir = Join-Path $ProjectDir "build"
    $descriptionFile = Join-Path $buildDir "project_description.json"

    if (-not (Test-Path $descriptionFile)) {
        return
    }

    $description = Get-Content $descriptionFile | ConvertFrom-Json
    $expectedProject = Normalize-Path $ProjectDir
    $expectedConfig = Normalize-Path (Join-Path $ProjectDir "sdkconfig")

    if ($description.project_path -ne $expectedProject -or $description.config_file -ne $expectedConfig) {
        Write-Host "Removing stale build directory: $buildDir"
        Remove-Item -LiteralPath $buildDir -Recurse -Force
    }
}

function Detect-Port {
    param(
        [string]$ProjectDir,
        [string]$RequestedPort
    )

    if ($RequestedPort) {
        return $RequestedPort
    }

    $settingsFile = Join-Path $ProjectDir ".vscode\settings.json"
    if (Test-Path $settingsFile) {
        try {
            $settings = Get-Content $settingsFile | ConvertFrom-Json
            if ($settings.'idf.portWin') {
                return [string]$settings.'idf.portWin'
            }
        } catch {
        }
    }

    $serialPort = Get-CimInstance Win32_SerialPort | Select-Object -First 1 -ExpandProperty DeviceID
    if ($serialPort) {
        return $serialPort
    }

    throw "No serial port found. Pass -Port COMx."
}

$projectDir = Resolve-ProjectPath $ModulePath
if (-not (Test-Path $projectDir)) {
    throw "Module directory not found: $projectDir"
}

Ensure-Idf
Set-Location $projectDir
Remove-StaleBuild -ProjectDir $projectDir

if (-not (Test-Path (Join-Path $projectDir "sdkconfig"))) {
    Write-Host "sdkconfig not found. Bootstrapping target $Target."
    & idf.py set-target $Target
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$resolvedPort = Detect-Port -ProjectDir $projectDir -RequestedPort $Port
Write-Host "Using module: $projectDir"
Write-Host "Using serial port: $resolvedPort"

& idf.py build
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& idf.py -p $resolvedPort flash monitor
exit $LASTEXITCODE
