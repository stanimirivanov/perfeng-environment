$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$MonitoringDir = Join-Path $RepoRoot "infra/local/kind/monitoring"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Installing Monitoring Stack" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# Check if cluster exists
$existingClusters = @()
$kindOutput = cmd /c "kind get clusters 2>&1"
if ($kindOutput) {
    foreach ($line in $kindOutput) {
        $trimmed = $line.Trim()
        if ($trimmed -ne "" -and $trimmed -ne "No kind clusters found." -and $trimmed -notmatch "^No kind") {
            $existingClusters += $trimmed
        }
    }
}

if ($existingClusters -notcontains "perfeng-local") {
    Write-Error "Cluster 'perfeng-local' does not exist. Run 'make cluster-up' first."
    exit 1
}

# Check if manifest directory exists
if (-not (Test-Path $MonitoringDir)) {
    Write-Error "Manifest directory not found: $MonitoringDir"
    exit 1
}

# Apply monitoring stack
Write-Host "Applying monitoring manifests..." -ForegroundColor Green

$applyOutput = cmd /c "kubectl apply -k `"$MonitoringDir`" 2>&1"
$applyExitCode = $LASTEXITCODE

if ($applyOutput) {
    $applyOutput | Out-Host
}

if ($applyExitCode -ne 0) {
    Write-Host ""
    Write-Host "Failed to apply monitoring manifests." -ForegroundColor Red
    Write-Host "Check the error above and fix any YAML issues." -ForegroundColor Yellow
    exit 1
}

# Wait for pods to be ready
Write-Host ""
Write-Host "Waiting for monitoring pods to be ready..." -ForegroundColor Yellow

$podsToWait = @(
    @{Name = "prometheus"; Label = "app=prometheus" },
    @{Name = "kube-state-metrics"; Label = "app=kube-state-metrics" },
    @{Name = "node-exporter"; Label = "app=node-exporter" },
    @{Name = "grafana"; Label = "app=grafana" }
)

foreach ($pod in $podsToWait) {
    Write-Host "  Waiting for $($pod.Name)..." -ForegroundColor Yellow
    
    $waitOutput = cmd /c "kubectl wait --for=condition=ready pod -l $($pod.Label) -n monitoring --timeout=180s 2>&1"
    $waitExitCode = $LASTEXITCODE
    
    if ($waitExitCode -eq 0) {
        Write-Host "  [OK] $($pod.Name) is ready" -ForegroundColor Green
    }
    else {
        Write-Host "  [WARN] $($pod.Name) may not be ready" -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Verifying monitoring stack..." -ForegroundColor Yellow
kubectl get pods -n monitoring -o wide

Write-Host ""
Write-Host "Monitoring stack installed successfully!" -ForegroundColor Green
Write-Host ""
Write-Host "Access Grafana at: http://localhost:30300" -ForegroundColor Cyan
Write-Host "  Username: admin" -ForegroundColor Cyan
Write-Host "  Password: admin" -ForegroundColor Cyan
Write-Host ""
Write-Host "Prometheus is available via port-forward:" -ForegroundColor Cyan
Write-Host "  Run: kubectl port-forward -n monitoring svc/prometheus 9090:9090" -ForegroundColor Cyan