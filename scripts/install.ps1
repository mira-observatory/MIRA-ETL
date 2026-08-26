$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
& .\.venv\Scripts\python.exe -m pip install -e .

if (-not (Test-Path .\.env)) {
    Copy-Item .\.env.example .\.env
    Write-Host "Created $ProjectDir\.env from .env.example"
} else {
    Write-Host ".env already exists; leaving it unchanged"
}

Write-Host "MIRA ETL installed in $ProjectDir\.venv"
