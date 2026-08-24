$ErrorActionPreference = "Stop"

$ClusterName = "perfeng-local"

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Stopping PerfEng Local Kubernetes Cluster" -ForegroundColor Cyan
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
    Write-Host "Cluster '$ClusterName' does not exist" -ForegroundColor Yellow
    exit 0
}

# Delete cluster
Write-Host "Deleting cluster '$ClusterName'..." -ForegroundColor Yellow
$deleteResult = Invoke-KindCommand -Arguments @("delete", "cluster", "--name", $ClusterName)

if ($deleteResult.ExitCode -ne 0) {
    Write-Warning "Failed to delete cluster. You may need to delete it manually:"
    Write-Warning "  kind delete cluster --name $ClusterName"
    exit 1
}

Write-Host ""
Write-Host "Cluster deleted successfully!" -ForegroundColor Green