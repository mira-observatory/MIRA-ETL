param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("guatemala_guatecompras", "costa_rica_sicop", "nicaragua_siscae")]
    [string]$Source,

    [ValidatePattern("^\d{6}(\s*-\s*\d{6})?$")]
    [string]$Period,

    [ValidateRange(1, [int]::MaxValue)]
    [Nullable[int]]$Limit,

    [string]$LocalZip
)

$ErrorActionPreference = "Stop"
$ProjectDir = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectDir

if ($Source -eq "nicaragua_siscae" -and $LocalZip) {
    throw "Nicaragua reads current HTML and does not accept a local ZIP."
}
if ($Source -ne "nicaragua_siscae" -and -not $Period) {
    throw "Period is required for historical ZIP/JSON sources."
}
if ($Period -match "-" -and $LocalZip) {
    throw "A local ZIP cannot be used with a period range."
}
if (-not (Test-Path .\.venv\Scripts\mira-etl.exe)) {
    throw "Missing .venv. Run scripts\install.ps1 first."
}

$Arguments = @("run", "--source", $Source)
if ($Period) { $Arguments += @("--period", $Period) }
if ($null -ne $Limit) { $Arguments += @("--limit", $Limit) }
if ($LocalZip) { $Arguments += @("--local-zip", $LocalZip) }

& .\.venv\Scripts\mira-etl.exe @Arguments
