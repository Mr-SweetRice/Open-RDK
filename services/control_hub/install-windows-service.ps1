$ErrorActionPreference = "Stop"
$serviceRoot = $PSScriptRoot
$venvRoot = Join-Path $serviceRoot ".venv"
$venvPython = Join-Path $venvRoot "Scripts\python.exe"
$venvPythonw = Join-Path $venvRoot "Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    python -m venv $venvRoot
}
& $venvPython -m pip install --disable-pip-version-check -e $serviceRoot
$action = New-ScheduledTaskAction -Execute $venvPythonw -Argument "-m control_hub_service" -WorkingDirectory $serviceRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "OpenRDK Control Hub Service" -Action $action -Trigger $trigger -Settings $settings -Description "Independent Open-RDK control module service" -Force | Out-Null
Start-ScheduledTask -TaskName "OpenRDK Control Hub Service"
Write-Host "Servico instalado e iniciado em http://127.0.0.1:8770"
