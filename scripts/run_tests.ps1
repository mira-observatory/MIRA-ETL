$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

if (-not (Test-Path .\.venv\Scripts\python.exe)) {
    throw "Missing .venv. Run scripts\install.ps1 first."
}

& .\.venv\Scripts\python.exe -m pytest tests -q
exit $LASTEXITCODE
