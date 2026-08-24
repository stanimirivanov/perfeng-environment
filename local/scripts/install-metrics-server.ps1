$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$MetricsServerDir = Join-Path $RepoRoot "infra/local/kind/metrics-server"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Installing metrics-server" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# Helper function to run kind commands using cmd /c
function Invoke-KindCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )
    
    $argString = $Arguments -join " "
    $command = "kind $argString 2>&1"
    
    $output = cmd /c $command
    $exitCode = $LASTEXITCODE
    
    return @{
        Output   = $output
        ExitCode = $exitCode
    }
}

# Check if cluster exists
$existingClusters = @()
$kindResult = Invoke-KindCommand -Arguments @("get", "clusters")
$kindOutput = $kindResult.Output

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
if (-not (Test-Path $MetricsServerDir)) {
    Write-Error "Manifest directory not found: $MetricsServerDir"
    exit 1
}

# Apply using kustomize
Write-Host "Applying metrics-server manifests using kustomize..." -ForegroundColor Green
kubectl apply -k $MetricsServerDir

# Wait for metrics-server to be ready
Write-Host ""
Write-Host "Waiting for metrics-server to be ready..." -ForegroundColor Yellow
kubectl wait --for=condition=ready pod `
    -l k8s-app=metrics-server `
    -n kube-system `
    --timeout=120s

# Verify installation
Write-Host ""
Write-Host "Verifying metrics-server..." -ForegroundColor Yellow
kubectl get apiservice v1beta1.metrics.k8s.io

Write-Host ""
Write-Host "Metrics-server installed successfully!" -ForegroundColor Green

# Test metrics
Write-Host ""
Write-Host "Testing node metrics..." -ForegroundColor Yellow
Start-Sleep -Seconds 15
kubectl top nodes