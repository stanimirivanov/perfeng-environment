$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$MinioDir = Join-Path $RepoRoot "infra/local/kind/k6-jobs/minio"
$NamespacesDir = Join-Path $RepoRoot "infra/local/kind/perf-namespaces"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Installing MinIO" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# Helper function to run kubectl commands using cmd /c
function Invoke-KubectlCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )
    
    $argString = $Arguments -join " "
    $command = "kubectl $argString 2>&1"
    
    $output = cmd /c $command
    $exitCode = $LASTEXITCODE
    
    return @{
        Output   = $output
        ExitCode = $exitCode
    }
}

# Check if namespace exists
Write-Host "Checking perf-platform namespace..." -ForegroundColor Yellow

$nsResult = Invoke-KubectlCommand -Arguments @("get", "namespace", "perf-platform")

if ($nsResult.ExitCode -ne 0) {
    Write-Host "perf-platform namespace not found. Installing namespaces first..." -ForegroundColor Yellow
    
    if (Test-Path $NamespacesDir) {
        $applyResult = Invoke-KubectlCommand -Arguments @("apply", "-k", $NamespacesDir)
        
        if ($applyResult.ExitCode -ne 0) {
            Write-Host "Failed to install namespaces:" -ForegroundColor Red
            $applyResult.Output | Out-Host
            exit 1
        }
        
        Write-Host "[OK] Namespaces installed" -ForegroundColor Green
    }
    else {
        Write-Host "Namespace manifests not found at: $NamespacesDir" -ForegroundColor Red
        Write-Host "Run 'make install-namespaces' first" -ForegroundColor Yellow
        exit 1
    }
}
else {
    Write-Host "[OK] perf-platform namespace exists" -ForegroundColor Green
}

# Check if minio directory exists
if (-not (Test-Path $MinioDir)) {
    Write-Error "MinIO manifest directory not found: $MinioDir"
    exit 1
}

# Apply MinIO manifests individually (in order)
Write-Host ""
Write-Host "Applying MinIO manifests..." -ForegroundColor Green

$manifestFiles = @(
    "pvc.yaml",
    "deployment.yaml",
    "service.yaml"
)

$allSucceeded = $true

foreach ($manifest in $manifestFiles) {
    $manifestPath = Join-Path $MinioDir $manifest
    
    if (-not (Test-Path $manifestPath)) {
        Write-Host "[WARN] Manifest not found: $manifestPath" -ForegroundColor Yellow
        continue
    }
    
    Write-Host "  Applying $manifest..." -ForegroundColor Yellow
    
    $applyResult = Invoke-KubectlCommand -Arguments @("apply", "-f", $manifestPath)
    
    if ($applyResult.ExitCode -eq 0) {
        Write-Host "  [OK] $manifest applied" -ForegroundColor Green
    }
    else {
        Write-Host "  [FAIL] Failed to apply $manifest" -ForegroundColor Red
        $applyResult.Output | Out-Host
        $allSucceeded = $false
    }
}

if (-not $allSucceeded) {
    Write-Host ""
    Write-Host "Some manifests failed to apply." -ForegroundColor Red
    exit 1
}

# Wait for MinIO to be ready
Write-Host ""
Write-Host "Waiting for MinIO to be ready..." -ForegroundColor Yellow

$waitResult = Invoke-KubectlCommand -Arguments @(
    "wait", "--for=condition=ready", "pod",
    "-l", "app=minio",
    "-n", "perf-platform",
    "--timeout=120s"
)

if ($waitResult.ExitCode -eq 0) {
    Write-Host "[OK] MinIO is ready" -ForegroundColor Green
}
else {
    Write-Host "[WARN] MinIO may not be ready yet" -ForegroundColor Yellow
    Write-Host "Check status with: kubectl get pods -n perf-platform" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "MinIO installed successfully!" -ForegroundColor Green
Write-Host "API: minio.perf-platform.svc.cluster.local:9000" -ForegroundColor Cyan
Write-Host ""
Write-Host "Access MinIO console (port-forward):" -ForegroundColor Cyan
Write-Host "  kubectl port-forward -n perf-platform svc/minio 9001:9001" -ForegroundColor Cyan
Write-Host "  http://localhost:9001" -ForegroundColor Cyan
Write-Host "  Username: perfeng" -ForegroundColor Cyan
Write-Host "  Password: perfeng123" -ForegroundColor Cyan