# Check 11: Network Policies
Write-Host ""
Write-Host "Checking Network Policies..." -ForegroundColor Yellow

$npResult = Invoke-KubectlCommand -Arguments @("get", "networkpolicy", "default-deny-ingress", "-n", "perf-generators")
if ($npResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-generators NetworkPolicy exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-generators NetworkPolicy missing" -ForegroundColor Yellow
}

$npResult = Invoke-KubectlCommand -Arguments @("get", "networkpolicy", "default-deny-ingress", "-n", "perf-sut")
if ($npResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-sut NetworkPolicy exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-sut NetworkPolicy missing" -ForegroundColor Yellow
}

# Check 12: Monitoring namespace
Write-Host ""
Write-Host "Checking monitoring namespace..." -ForegroundColor Yellow

$monitoringNs = Invoke-KubectlCommand -Arguments @("get", "namespace", "monitoring")
if ($monitoringNs.ExitCode -eq 0) {
    Write-Host "[OK] monitoring namespace exists" -ForegroundColor Green
    
    # Check 13: Monitoring components
    Write-Host ""
    Write-Host "Checking monitoring components..." -ForegroundColor Yellow
    
    $promResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "monitoring", "-l", "app=prometheus", "--no-headers")
    if ($promResult.ExitCode -eq 0 -and $promResult.Output -match "Running") {
        Write-Host "[OK] Prometheus is running" -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] Prometheus not running (run 'make monitoring-up')" -ForegroundColor Yellow
    }
    
    $grafanaResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "monitoring", "-l", "app=grafana", "--no-headers")
    if ($grafanaResult.ExitCode -eq 0 -and $grafanaResult.Output -match "Running") {
        Write-Host "[OK] Grafana is running" -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] Grafana not running (run 'make monitoring-up')" -ForegroundColor Yellow
    }
    
    $ksmResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "monitoring", "-l", "app=kube-state-metrics", "--no-headers")
    if ($ksmResult.ExitCode -eq 0 -and $ksmResult.Output -match "Running") {
        Write-Host "[OK] kube-state-metrics is running" -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] kube-state-metrics not running (run 'make monitoring-up')" -ForegroundColor Yellow
    }
    
    $nodeExporterResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "monitoring", "-l", "app=node-exporter", "--no-headers")
    if ($nodeExporterResult.ExitCode -eq 0 -and $nodeExporterResult.Output -match "Running") {
        Write-Host "[OK] node-exporter is running" -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] node-exporter not running (run 'make monitoring-up')" -ForegroundColor Yellow
    }
}
else {
    Write-Host "[WARN] monitoring namespace not created (run 'make monitoring-up')" -ForegroundColor Yellow
}

# Check 11: Network Policies
Write-Host ""
Write-Host "Checking Network Policies..." -ForegroundColor Yellow

$npResult = Invoke-KubectlCommand -Arguments @("get", "networkpolicy", "default-deny-ingress", "-n", "perf-generators")
if ($npResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-generators NetworkPolicy exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-generators NetworkPolicy missing" -ForegroundColor Yellow
}

$npResult = Invoke-KubectlCommand -Arguments @("get", "networkpolicy", "default-deny-ingress", "-n", "perf-sut")
if ($npResult.ExitCode -eq 0) {
    Write-Host "[OK] perf-sut NetworkPolicy exists" -ForegroundColor Green
}
else {
    Write-Host "[WARN] perf-sut NetworkPolicy missing" -ForegroundColor Yellow
}

# Check 12: Monitoring namespace
Write-Host ""
Write-Host "Checking monitoring namespace..." -ForegroundColor Yellow

$monitoringNs = Invoke-KubectlCommand -Arguments @("get", "namespace", "monitoring")
if ($monitoringNs.ExitCode -eq 0) {
    Write-Host "[OK] monitoring namespace exists" -ForegroundColor Green
    
    # Check 13: Monitoring components
    Write-Host ""
    Write-Host "Checking monitoring components..." -ForegroundColor Yellow
    
    $promResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "monitoring", "-l", "app=prometheus", "--no-headers")
    if ($promResult.ExitCode -eq 0 -and $promResult.Output -match "Running") {
        Write-Host "[OK] Prometheus is running" -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] Prometheus not running (run 'make monitoring-up')" -ForegroundColor Yellow
    }
    
    $grafanaResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "monitoring", "-l", "app=grafana", "--no-headers")
    if ($grafanaResult.ExitCode -eq 0 -and $grafanaResult.Output -match "Running") {
        Write-Host "[OK] Grafana is running" -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] Grafana not running (run 'make monitoring-up')" -ForegroundColor Yellow
    }
    
    $ksmResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "monitoring", "-l", "app=kube-state-metrics", "--no-headers")
    if ($ksmResult.ExitCode -eq 0 -and $ksmResult.Output -match "Running") {
        Write-Host "[OK] kube-state-metrics is running" -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] kube-state-metrics not running (run 'make monitoring-up')" -ForegroundColor Yellow
    }
    
    $nodeExporterResult = Invoke-KubectlCommand -Arguments @("get", "pods", "-n", "monitoring", "-l", "app=node-exporter", "--no-headers")
    if ($nodeExporterResult.ExitCode -eq 0 -and $nodeExporterResult.Output -match "Running") {
        Write-Host "[OK] node-exporter is running" -ForegroundColor Green
    }
    else {
        Write-Host "[WARN] node-exporter not running (run 'make monitoring-up')" -ForegroundColor Yellow
    }
}
else {
    Write-Host "[WARN] monitoring namespace not created (run 'make monitoring-up')" -ForegroundColor Yellow
}