$ErrorActionPreference = "Stop"
$serviceRoot = $PSScriptRoot
$venvPython = Join-Path $serviceRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv (Join-Path $serviceRoot ".venv")
}
& $venvPython -m pip install --disable-pip-version-check -e $serviceRoot
& $venvPython -m control_hub_service @args
