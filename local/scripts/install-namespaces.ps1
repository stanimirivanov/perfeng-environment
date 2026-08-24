$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$NamespacesDir = Join-Path $RepoRoot "infra/local/kind/perf-namespaces"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Installing PerfEng Namespaces and RBAC" -ForegroundColor Cyan
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
if (-not (Test-Path $NamespacesDir)) {
    Write-Error "Manifest directory not found: $NamespacesDir"
    exit 1
}

# Apply using kustomize
Write-Host "Applying namespace and RBAC manifests..." -ForegroundColor Green
kubectl apply -k $NamespacesDir

# Wait for namespaces to be ready
Write-Host ""
Write-Host "Verifying namespaces..." -ForegroundColor Yellow
kubectl get namespaces -l perfeng.io/managed-by=perfeng

# Verify RBAC
Write-Host ""
Write-Host "Verifying RBAC..." -ForegroundColor Yellow
kubectl get serviceaccounts -n perf-platform
kubectl get roles -n perf-generators
kubectl get roles -n perf-sut
kubectl get rolebindings -n perf-generators
kubectl get rolebindings -n perf-sut

# Verify quotas
Write-Host ""
Write-Host "Verifying resource quotas..." -ForegroundColor Yellow
kubectl get resourcequotas -n perf-platform
kubectl get resourcequotas -n perf-generators
kubectl get resourcequotas -n perf-sut

Write-Host ""
Write-Host "Namespace installation complete!" -ForegroundColor Green