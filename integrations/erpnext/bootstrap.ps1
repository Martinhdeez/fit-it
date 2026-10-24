# Run from any directory. Credentials must already exist in the host shell.
$ErrorActionPreference = 'Stop'
foreach ($name in @('ERPNEXT_DB_PASSWORD', 'ERPNEXT_ADMIN_PASSWORD')) {
    if (-not [Environment]::GetEnvironmentVariable($name)) {
        throw "Set $name in your shell before running this script. See README.md."
    }
}
$composeFile = Join-Path $PSScriptRoot 'compose.yaml'
Write-Host 'Starting ERPNext. First site creation can take several minutes.'
& docker compose -f $composeFile up -d
if ($LASTEXITCODE -ne 0) { throw 'ERPNext startup failed. Inspect docker compose logs create-site.' }
& docker compose -f $composeFile run --rm -T bootstrap
if ($LASTEXITCODE -ne 0) { throw 'ERPNext seed or verification failed. See output above.' }
$port = if ($env:ERPNEXT_HOST_PORT) { $env:ERPNEXT_HOST_PORT } else { '8080' }
Write-Host "Ready: http://localhost:$port/desk/selling/fitit-overview (user: Administrator)"
