$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../..")).Path
$ClusterConfig = Join-Path $RepoRoot "infra/local/kind/cluster-config.yaml"
$ClusterName = "perfeng-local"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Starting PerfEng Local Kubernetes Cluster" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan

# Check if kind is installed
if (-not (Get-Command kind -ErrorAction SilentlyContinue)) {
    Write-Error "kind is not installed. Install from: https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
    exit 1
}

# Check if Docker is running
try {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Docker is not running. Please start Docker Desktop."
        exit 1
    }
}
catch {
    Write-Error "Docker is not running. Please start Docker Desktop."
    exit 1
}

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

# Check if cluster already exists
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
    Write-Host "Cluster '$ClusterName' already exists" -ForegroundColor Yellow
    Write-Host "Use 'make cluster-down' to delete it first" -ForegroundColor Yellow
    exit 0
}

# Clean up any partial cluster from previous failed attempts
Write-Host "Cleaning up any previous partial cluster..." -ForegroundColor Yellow
$deleteResult = Invoke-KindCommand -Arguments @("delete", "cluster", "--name", $ClusterName)
# Ignore delete errors - cluster may not exist

# Create cluster
Write-Host "Creating kind cluster '$ClusterName'..." -ForegroundColor Green

$createResult = Invoke-KindCommand -Arguments @("create", "cluster", "--name", $ClusterName, "--config", $ClusterConfig, "--wait", "300s")

if ($createResult.ExitCode -ne 0) {
    Write-Host ""
    Write-Host "Failed to create cluster." -ForegroundColor Red
    Write-Host ""
    Write-Host "Troubleshooting tips:" -ForegroundColor Yellow
    Write-Host "1. Ensure Docker Desktop is running" -ForegroundColor Yellow
    Write-Host "2. Check Docker Desktop has enough resources (Settings > Resources)" -ForegroundColor Yellow
    Write-Host "   - CPUs: at least 4" -ForegroundColor Yellow
    Write-Host "   - Memory: at least 8GB" -ForegroundColor Yellow
    Write-Host "3. Try restarting Docker Desktop" -ForegroundColor Yellow
    Write-Host "4. Run 'kind delete cluster --name $ClusterName' and try again" -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "Cluster created successfully!" -ForegroundColor Green

# Wait for nodes to be ready
Write-Host "Waiting for nodes to be ready..." -ForegroundColor Yellow
try {
    kubectl wait --for=condition=Ready nodes --all --timeout=300s 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Some nodes may not be ready. Check with 'kubectl get nodes'"
    }
}
catch {
    Write-Warning "Error waiting for nodes. Check with 'kubectl get nodes'"
}

# Display cluster info
Write-Host ""
Write-Host "Cluster nodes:" -ForegroundColor Cyan
kubectl get nodes -o wide 2>&1 | Out-Host

Write-Host ""
Write-Host "Cluster setup complete!" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Yellow
Write-Host "  1. Install metrics-server: make install-metrics" -ForegroundColor Yellow
Write-Host "  2. Check cluster health: make cluster-health" -ForegroundColor Yellow