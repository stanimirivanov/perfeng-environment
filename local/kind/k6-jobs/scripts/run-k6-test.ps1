param(
  [Parameter(Mandatory = $false)]
  [string]$TestName,
    
  [Parameter(Mandatory = $false)]
  [ValidateSet("smoke", "average", "regression", "stress", "capacity")]
  [string]$Profile,
    
  [string]$BaseUrl = "http://perf-sut-service.perf-sut:8080",
    
  [string]$OutputDir = "results",
    
  [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"

# Show help if required parameters are missing
if (-not $TestName -or -not $Profile) {
  Write-Host "=========================================" -ForegroundColor Cyan
  Write-Host "Run k6 Test as Kubernetes Job" -ForegroundColor Cyan
  Write-Host "=========================================" -ForegroundColor Cyan
  Write-Host ""
  Write-Host "Usage:" -ForegroundColor Yellow
  Write-Host "  powershell -File run-k6-test.ps1 -TestName <test> -Profile <profile>"
  Write-Host ""
  Write-Host "Parameters:" -ForegroundColor Yellow
  Write-Host "  -TestName     Required. Test scenario name (checkout, search, account)"
  Write-Host "  -Profile      Required. Workload profile (smoke, average, regression, stress, capacity)"
  Write-Host "  -BaseUrl      Optional. Base URL of SUT"
  Write-Host "  -OutputDir    Optional. Output directory (default: results)"
  Write-Host "  -TimeoutSeconds Optional. Job timeout in seconds (default: 900)"
  Write-Host ""
  Write-Host "Examples:" -ForegroundColor Yellow
  Write-Host "  powershell -File run-k6-test.ps1 -TestName checkout -Profile smoke"
  exit 1
}

# Correct path: go up 5 levels from scripts/ to repository root
# scripts/ → k6-jobs/ → kind/ → local/ → infra/ → performance-platform/
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../../../../..")).Path
$K6Dir = Join-Path $RepoRoot "tests/k6"
$OutputDirPath = Join-Path $RepoRoot $OutputDir

Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Running k6 Test as Kubernetes Job" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Test: $TestName"
Write-Host "Profile: $Profile"
Write-Host "Base URL: $BaseUrl"
Write-Host "Repo Root: $RepoRoot"
Write-Host "K6 Dir: $K6Dir"
Write-Host ""

# Generate a unique run ID
$runId = "perf-$(Get-Date -Format 'yyyyMMdd-HHmmss')-$([guid]::NewGuid().ToString('N').Substring(0, 8))"
Write-Host "Run ID: $runId"

# Define paths
$testScript = "tests/$TestName/scenario.js"
$workloadConfig = "workloads/$Profile/$TestName.json"
$configMapName = "k6-test-scripts-$TestName"
$jobName = "k6-test-$TestName-$([guid]::NewGuid().ToString('N').Substring(0, 8))"

# Check if test script exists
$testScriptPath = Join-Path $K6Dir $testScript
if (-not (Test-Path $testScriptPath)) {
  Write-Host "[FAIL] Test script not found: $testScriptPath" -ForegroundColor Red
  Write-Host ""
  Write-Host "Available tests:" -ForegroundColor Yellow
    
  $testsDir = Join-Path $K6Dir "tests"
  if (Test-Path $testsDir) {
    $testDirs = Get-ChildItem $testsDir -Directory | ForEach-Object { $_.Name }
    foreach ($dir in $testDirs) {
      Write-Host "  - $dir"
    }
  }
  else {
    Write-Host "  (tests directory not found)" -ForegroundColor Yellow
  }
  exit 1
}

# Check if workload config exists
$workloadConfigPath = Join-Path $K6Dir $workloadConfig
if (-not (Test-Path $workloadConfigPath)) {
  Write-Host "[FAIL] Workload config not found: $workloadConfigPath" -ForegroundColor Red
  Write-Host ""
  Write-Host "Available profiles for ${TestName}:" -ForegroundColor Yellow
    
  $workloadsDir = Join-Path $K6Dir "workloads"
  if (Test-Path $workloadsDir) {
    $profileDirs = Get-ChildItem $workloadsDir -Directory | ForEach-Object { $_.Name }
    foreach ($dir in $profileDirs) {
      $configFile = Join-Path $K6Dir "workloads/$dir/$TestName.json"
      if (Test-Path $configFile) {
        Write-Host "  - $dir"
      }
    }
  }
  else {
    Write-Host "  (workloads directory not found)" -ForegroundColor Yellow
  }
  exit 1
}

# Check if namespace exists
Write-Host "Checking perf-generators namespace..." -ForegroundColor Yellow

$nsCheck = cmd /c "kubectl get namespace perf-generators 2>&1"
if ($LASTEXITCODE -ne 0) {
  Write-Host "[FAIL] perf-generators namespace not found" -ForegroundColor Red
  Write-Host "Run 'make install-namespaces' first" -ForegroundColor Yellow
  exit 1
}
Write-Host "[OK] perf-generators namespace exists" -ForegroundColor Green

# Create ConfigMap with test scripts
Write-Host ""
Write-Host "Creating ConfigMap with test scripts..." -ForegroundColor Yellow

# Create ConfigMap using kubectl create configmap --from-file
$createCmResult = cmd /c "kubectl create configmap $configMapName -n perf-generators --from-file=`"$testScriptPath`" --from-file=`"$workloadConfigPath`" 2>&1"
$createCmExitCode = $LASTEXITCODE

if ($createCmExitCode -ne 0 -and $createCmResult -notmatch "AlreadyExists") {
  Write-Host "[FAIL] Failed to create ConfigMap" -ForegroundColor Red
  Write-Host $createCmResult
  exit 1
}
Write-Host "[OK] ConfigMap created" -ForegroundColor Green

# Create Job
Write-Host "Creating Kubernetes Job..." -ForegroundColor Yellow

# Build the job YAML using a here-string
$jobYaml = @"
apiVersion: batch/v1
kind: Job
metadata:
  name: $jobName
  namespace: perf-generators
  labels:
    app: k6-test
    perfeng.io/component: test-runner
    perfeng.io/test: "$TestName"
    perfeng.io/profile: "$Profile"
    perfeng.io/run-id: "$runId"
spec:
  backoffLimit: 2
  activeDeadlineSeconds: $TimeoutSeconds
  ttlSecondsAfterFinished: 3600
  template:
    metadata:
      labels:
        app: k6-test
        perfeng.io/test: "$TestName"
        perfeng.io/run-id: "$runId"
    spec:
      restartPolicy: Never
      serviceAccountName: perf-generator
      nodeSelector:
        workload: performance-generator
      containers:
        - name: k6
          image: grafana/k6:2.2.0
          imagePullPolicy: IfNotPresent
          command:
            - /bin/sh
            - -c
            - |
              echo "Running k6 test..."
              echo "Test: $TestName"
              echo "Profile: $Profile"
              echo "Run ID: $runId"
              echo "--- Test data files ---"
              ls -la /test-data/
              echo "--- Scenario file (first 10 lines) ---"
              head -10 /test-data/scenario.js
              echo "--- Workload config ---"
              cat /test-data/workload.json
              echo "--- Starting k6 ---"
              k6 run --config /test-data/workload.json /test-data/scenario.js --out json=/results/results.json --summary-export /results/summary.json
          env:
            - name: BASE_URL
              value: "$BaseUrl"
            - name: PERF_RUN_ID
              value: "$runId"
            - name: PERF_PROFILE
              value: "$Profile"
            - name: PERF_TEST_NAME
              value: "$TestName"
          volumeMounts:
            - name: test-data
              mountPath: /test-data
            - name: results
              mountPath: /results
          resources:
            requests:
              cpu: "100m"
              memory: "256Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
      volumes:
        - name: test-data
          configMap:
            name: $configMapName
            items:
              - key: "scenario.js"
                path: "scenario.js"
              - key: "workload.json"
                path: "workload.json"
        - name: results
          emptyDir: {}
"@

$jobFile = Join-Path $env:TEMP "$jobName.yaml"
$jobYaml | Out-File $jobFile -Encoding UTF8

$applyJobResult = cmd /c "kubectl apply -f `"$jobFile`" 2>&1"
$applyJobExitCode = $LASTEXITCODE

if ($applyJobExitCode -ne 0) {
  Write-Host "[FAIL] Failed to create Job" -ForegroundColor Red
  Write-Host $applyJobResult
  exit 1
}
Write-Host "[OK] Job created: $jobName" -ForegroundColor Green

# Wait for job completion
Write-Host ""
Write-Host "Waiting for job to complete..." -ForegroundColor Yellow

$maxWaitSeconds = $TimeoutSeconds + 120
$elapsed = 0
$jobSucceeded = $false

while ($elapsed -lt $maxWaitSeconds) {
  $jobStatus = cmd /c "kubectl get job $jobName -n perf-generators -o jsonpath='{.status.conditions[0].type}' 2>&1"
    
  if ($jobStatus -match "Complete") {
    Write-Host "[OK] Job completed successfully" -ForegroundColor Green
    $jobSucceeded = $true
    break
  }
    
  if ($jobStatus -match "Failed") {
    Write-Host "[FAIL] Job failed" -ForegroundColor Red
    break
  }
    
  Start-Sleep -Seconds 10
  $elapsed += 10
    
  if ($elapsed % 30 -eq 0) {
    Write-Host "  Waiting... ($elapsed seconds elapsed)"
        
    # Show pod status
    $podStatus = cmd /c "kubectl get pods -n perf-generators -l `"perfeng.io/run-id=$runId`" --no-headers 2>&1"
    if ($podStatus) {
      Write-Host "  Pod status: $podStatus"
    }
  }
}

# Collect results
Write-Host ""
Write-Host "Collecting results..." -ForegroundColor Yellow

$runOutputDir = Join-Path $OutputDirPath $runId
New-Item -ItemType Directory -Path $runOutputDir -Force | Out-Null

$podName = cmd /c "kubectl get pods -n perf-generators -l `"perfeng.io/run-id=$runId`" -o jsonpath='{.items[0].metadata.name}' 2>&1"

if ($podName -and $podName -notmatch "Error") {
  # Copy results from pod - use ${podName} to avoid colon issue
  $resultSource = "perf-generators/${podName}:/results/results.json"
  $summarySource = "perf-generators/${podName}:/results/summary.json"
  $resultDest = "$runOutputDir/results.json"
  $summaryDest = "$runOutputDir/summary.json"
    
  cmd /c "kubectl cp `"$resultSource`" `"$resultDest`" 2>&1" | Out-Null
  cmd /c "kubectl cp `"$summarySource`" `"$summaryDest`" 2>&1" | Out-Null
    
  # Get pod logs
  cmd /c "kubectl logs $podName -n perf-generators > `"$runOutputDir/pod.log`" 2>&1"
    
  Write-Host "[OK] Results saved to: $runOutputDir" -ForegroundColor Green
}
else {
  Write-Host "[WARN] Could not find pod for run $runId" -ForegroundColor Yellow
  Write-Host "Check with: kubectl get pods -n perf-generators" -ForegroundColor Yellow
}

# Clean up
Write-Host ""
Write-Host "Cleaning up..." -ForegroundColor Yellow

cmd /c "kubectl delete configmap $configMapName -n perf-generators 2>&1" | Out-Null
cmd /c "kubectl delete job $jobName -n perf-generators 2>&1" | Out-Null

# Remove temp files
Remove-Item $jobFile -ErrorAction SilentlyContinue

Write-Host ""
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "k6 Test Execution Summary" -ForegroundColor Cyan
Write-Host "=========================================" -ForegroundColor Cyan
Write-Host "Run ID: $runId"
Write-Host "Status: $(if ($jobSucceeded) { 'SUCCESS' } else { 'FAILED' })"
Write-Host "Results: $runOutputDir"
Write-Host "=========================================" -ForegroundColor Cyan