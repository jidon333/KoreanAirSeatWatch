$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.runtime\Scripts\python.exe')) {
    py -3.12 -m venv .runtime
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
}
& '.\.runtime\Scripts\python.exe' -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
Write-Host 'Ready. Run Start-SeatWatch.cmd.'
