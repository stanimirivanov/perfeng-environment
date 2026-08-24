$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$MonitoringDir = Join-Path $RepoRoot "infra/local/kind/monitoring"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Uninstalling Monitoring Stack" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

kubectl delete -k $MonitoringDir

Write-Host ""
Write-Host "Monitoring stack uninstalled." -ForegroundColor Green