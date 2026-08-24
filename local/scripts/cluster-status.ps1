$ErrorActionPreference = "Stop"

$ClusterName = "perfeng-local"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "PerfEng Local Kubernetes Cluster Status" -ForegroundColor Cyan
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

if ($existingClusters -notcontains $ClusterName) {
    Write-Host "Cluster '$ClusterName' does not exist" -ForegroundColor Red
    exit 1
}

Write-Host "Cluster exists: $ClusterName" -ForegroundColor Green
Write-Host ""

# Show nodes
Write-Host "=== Nodes ===" -ForegroundColor Cyan
kubectl get nodes -o wide

Write-Host ""
Write-Host "=== Node Labels ===" -ForegroundColor Cyan
kubectl get nodes --show-labels

Write-Host ""
Write-Host "=== Node Taints ===" -ForegroundColor Cyan
kubectl describe nodes | Select-String "Name:|Taints:"

Write-Host ""
Write-Host "=== Pods (all namespaces) ===" -ForegroundColor Cyan
kubectl get pods --all-namespaces -o wide

Write-Host ""
Write-Host "=== Resource Usage ===" -ForegroundColor Cyan
try {
    $topOutput = kubectl top nodes 2>&1
    if ($LASTEXITCODE -eq 0) {
        $topOutput | Out-Host
    }
    else {
        Write-Host "metrics-server not installed yet" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "metrics-server not installed yet" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Kubernetes Version ===" -ForegroundColor Cyan
# Client version
$clientVersion = kubectl version --client -o json 2>$null | ConvertFrom-Json
if ($clientVersion) {
    Write-Host "Client Version: $($clientVersion.gitVersion)"
}

# Server version (using direct API call to avoid skew warning)
$serverVersionRaw = kubectl get --raw /version 2>$null
if ($serverVersionRaw) {
    $serverVersionObj = $serverVersionRaw | ConvertFrom-Json
    Write-Host "Server Version: $($serverVersionObj.gitVersion)"
}