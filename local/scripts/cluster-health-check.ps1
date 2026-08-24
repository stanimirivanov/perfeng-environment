$ErrorActionPreference = "Stop"

$ClusterName = "perfeng-local"
$Failed = 0

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "PerfEng Cluster Health Check" -ForegroundColor Cyan
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

# Helper function to run kubectl commands without stderr errors
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

# Check 1: Cluster exists
Write-Host ""
Write-Host "Checking cluster existence..." -ForegroundColor Yellow

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

if ($existingClusters -contains $ClusterName) {
    Write-Host "[OK] Cluster '$ClusterName' exists" -ForegroundColor Green
}
else {
    Write-Host "[FAIL] Cluster '$ClusterName' does not exist" -ForegroundColor Red
    $Failed = 1
    Write-Host ""
    Write-Host "Cluster health check failed!" -ForegroundColor Red
    exit $Failed
}

# Check 2: All nodes ready
Write-Host ""
Write-Host "Checking node readiness..." -ForegroundColor Yellow

$nodeResult = Invoke-KubectlCommand -Arguments @("get", "nodes", "--no-headers")
$nodes = $nodeResult.Output

if ($nodes -and $nodeResult.ExitCode -eq 0) {
    $nodeLines = @($nodes | Where-Object { $_.Trim() -ne "" })
    $totalNodes = $nodeLines.Count
    $readyNodes = @($nodeLines | Where-Object { $_ -match " Ready " }).Count
    Write-Host "Ready nodes: $readyNodes/$totalNodes"
    
    if ($readyNodes -lt $totalNodes) {
        Write-Host "[FAIL] Not all nodes are ready" -ForegroundColor Red
        $Failed = 1
    }
    else {
        Write-Host "[OK] All nodes are ready" -ForegroundColor Green
    }
}
else {
    Write-Host "[FAIL] Cannot get node list" -ForegroundColor Red
    $Failed = 1
}

# Check 3: Node labels present
Write-Host ""
Write-Host "Checking node labels..." -ForegroundColor Yellow

$generatorResult = Invoke-KubectlCommand -Arguments @("get", "nodes", "-l", "workload=performance-generator", "--no-headers")
if ($generatorResult.ExitCode -eq 0 -and $generatorResult.Output) {
    Write-Host "[OK] Generator node label present" -ForegroundColor Green
}
else {
    Write-Host "[FAIL] Generator node label missing" -ForegroundColor Red
    $Failed = 1
}

$sutResult = Invoke-KubectlCommand -Arguments @("get", "nodes", "-l", "workload=sut", "--no-headers")
if ($sutResult.ExitCode -eq 0 -and $sutResult.Output) {
    Write-Host "[OK] SUT node label present" -ForegroundColor Green
}
else {
    Write-Host "[FAIL] SUT node label missing" -ForegroundColor Red
    $Failed = 1
}

# Check 4: Control plane ready
Write-Host ""
Write-Host "Checking control plane..." -ForegroundColor Yellow

$controlPlaneResult = Invoke-KubectlCommand -Arguments @("get", "nodes", "-l", "node-role.kubernetes.io/control-plane", "--no-headers")
if ($controlPlaneResult.ExitCode -eq 0 -and $controlPlaneResult.Output -match " Ready ") {
    Write-Host "[OK] Control plane is ready" -ForegroundColor Green
}
else {
    Write-Host "[FAIL] Control plane not ready" -ForegroundColor Red
    $Failed = 1
}

# Check 5: CoreDNS running
Write-Host ""
Write-Host "Checking CoreDNS..." -ForegroundColor Yellow

$corednsResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "kube-system", "-l", "k8s-app=kube-dns", "--no-headers")
if ($corednsResult.ExitCode -eq 0 -and $corednsResult.Output -match "Running") {
    Write-Host "[OK] CoreDNS is running" -ForegroundColor Green
}
else {
    Write-Host "[FAIL] CoreDNS not running" -ForegroundColor Red
    $Failed = 1
}

# Check 6: API server accessible
Write-Host ""
Write-Host "Checking API server..." -ForegroundColor Yellow

$healthzResult = Invoke-KubectlCommand -Arguments @("get", "--raw", "/healthz")
if ($healthzResult.ExitCode -eq 0 -and $healthzResult.Output -eq "ok") {
    Write-Host "[OK] API server is healthy" -ForegroundColor Green
}
else {
    Write-Host "[FAIL] API server not accessible" -ForegroundColor Red
    $Failed = 1
}

# Check 7: Metrics server (if installed)
Write-Host ""
Write-Host "Checking metrics-server..." -ForegroundColor Yellow

$metricsResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "kube-system", "-l", "k8s-app=metrics-server", "--no-headers")
if ($metricsResult.ExitCode -eq 0 -and $metricsResult.Output -match "Running") {
    Write-Host "[OK] metrics-server is running" -ForegroundColor Green
}
else {
    Write-Host "[WARN] metrics-server not installed (optional)" -ForegroundColor Yellow
}

# Check 8: PerfEng namespaces
Write-Host ""
Write-Host "Checking PerfEng namespaces..." -ForegroundColor Yellow

$nsResult = Invoke-KubectlCommand -Arguments @("get", "namespace", "perf-platform")
if ($nsResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-platform namespace exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-platform namespace not created (run 'make install-namespaces')" -ForegroundColor Yellow
}

$nsResult = Invoke-KubectlCommand -Arguments @("get", "namespace", "perf-generators")
if ($nsResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-generators namespace exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-generators namespace not created (run 'make install-namespaces')" -ForegroundColor Yellow
}

$nsResult = Invoke-KubectlCommand -Arguments @("get", "namespace", "perf-sut")
if ($nsResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-sut namespace exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-sut namespace not created (run 'make install-namespaces')" -ForegroundColor Yellow
}

# Check 9: ServiceAccounts
Write-Host ""
Write-Host "Checking ServiceAccounts..." -ForegroundColor Yellow

$saResult = Invoke-KubectlCommand -Arguments @("get", "serviceaccount", "perf-orchestrator", "-n", "perf-platform")
if ($saResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-orchestrator ServiceAccount exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-orchestrator ServiceAccount missing (run 'make install-namespaces')" -ForegroundColor Yellow
}

$saResult = Invoke-KubectlCommand -Arguments @("get", "serviceaccount", "perf-generator", "-n", "perf-generators")
if ($saResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-generator ServiceAccount exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-generator ServiceAccount missing (run 'make install-namespaces')" -ForegroundColor Yellow
}

# Check 10: Resource Quotas
Write-Host ""
Write-Host "Checking Resource Quotas..." -ForegroundColor Yellow

$quotaResult = Invoke-KubectlCommand -Arguments @("get", "resourcequota", "perf-platform-quota", "-n", "perf-platform")
if ($quotaResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-platform ResourceQuota exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-platform ResourceQuota missing (run 'make install-namespaces')" -ForegroundColor Yellow
}

$quotaResult = Invoke-KubectlCommand -Arguments @("get", "resourcequota", "perf-generators-quota", "-n", "perf-generators")
if ($quotaResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-generators ResourceQuota exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-generators ResourceQuota missing (run 'make install-namespaces')" -ForegroundColor Yellow
}

$quotaResult = Invoke-KubectlCommand -Arguments @("get", "resourcequota", "perf-sut-quota", "-n", "perf-sut")
if ($quotaResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-sut ResourceQuota exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-sut ResourceQuota missing (run 'make install-namespaces')" -ForegroundColor Yellow
}

# Summary
Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
if ($Failed -eq 0) {
    Write-Host "All required health checks passed!" -ForegroundColor Green
    Write-Host "Review [WARN] items for optional components." -ForegroundColor Yellow
}
else {
    Write-Host "Some health checks failed" -ForegroundColor Red
}
Write-Host "=========================================" -ForegroundColor Cyan

exit $Failed