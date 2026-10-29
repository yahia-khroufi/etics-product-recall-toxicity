param([switch]$ArrierePlan)
$ErrorActionPreference = 'Stop'
$automationPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $automationPython)) {
    throw 'Environnement .venv absent. Installez requirements.txt avant de lancer le processus.'
}
if ($ArrierePlan) {
    $automationLogs = Join-Path $PSScriptRoot 'runtime'
    New-Item -ItemType Directory -Path $automationLogs -Force | Out-Null
    $automationLogName = 'worker-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + ([guid]::NewGuid().ToString('N').Substring(0,8))
    $automationProcess = Start-Process -FilePath $automationPython -ArgumentList @('-m','src.automation') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $automationLogs ($automationLogName + '.out.log')) -RedirectStandardError (Join-Path $automationLogs ($automationLogName + '.err.log')) -PassThru
    Write-Output ('Processus ETICS lance, PID ' + $automationProcess.Id + '. Suivi dans le dashboard > Nouveau rappel.')
} else {
    $automationPreviousDirectory = Get-Location
    try {
        Set-Location -LiteralPath $PSScriptRoot
        & $automationPython -m src.automation
        if ($LASTEXITCODE -ne 0) { throw 'Le processus ETICS a rencontre une erreur. Consultez le message ci-dessus.' }
    } finally {
        Set-Location -LiteralPath $automationPreviousDirectory.Path
    }
}
