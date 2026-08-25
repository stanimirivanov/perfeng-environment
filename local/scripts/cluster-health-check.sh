#!/bin/bash
set -euo pipefail

CLUSTER_NAME="perfeng-local"
FAILED=0

echo "========================================="
echo "PerfEng Cluster Health Check"
echo "========================================="

# Check 1: Cluster exists
echo ""
echo "Checking cluster existence..."
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "[OK] Cluster '${CLUSTER_NAME}' exists"
else
    echo "[FAIL] Cluster '${CLUSTER_NAME}' does not exist"
    FAILED=1
    exit $FAILED
fi

# Check 2: All nodes ready
echo ""
echo "Checking node readiness..."
if NODES=$(kubectl get nodes --no-headers 2>/dev/null); then
    TOTAL_NODES=$(echo "$NODES" | wc -l | tr -d ' ')
    READY_NODES=$(echo "$NODES" | grep -c " Ready " || true)
    echo "Ready nodes: ${READY_NODES}/${TOTAL_NODES}"
    
    if [ "$READY_NODES" -lt "$TOTAL_NODES" ]; then
        echo "[FAIL] Not all nodes are ready"
        FAILED=1
    else
        echo "[OK] All nodes are ready"
    fi
else
    echo "[FAIL] Cannot get node list"
    FAILED=1
fi

# Check 3: Node labels
echo ""
echo "Checking node labels..."
if kubectl get nodes -l workload=performance-generator --no-headers 2>/dev/null | grep -q .; then
    echo "[OK] Generator node label present"
else
    echo "[FAIL] Generator node label missing"
    FAILED=1
fi

if kubectl get nodes -l workload=sut --no-headers 2>/dev/null | grep -q .; then
    echo "[OK] SUT node label present"
else
    echo "[FAIL] SUT node label missing"
    FAILED=1
fi

# Check 4: Control plane
echo ""
echo "Checking control plane..."
if kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers 2>/dev/null | grep -q " Ready "; then
    echo "[OK] Control plane is ready"
else
    echo "[FAIL] Control plane not ready"
    FAILED=1
fi

# Check 5: CoreDNS
echo ""
echo "Checking CoreDNS..."
if kubectl get pods -n kube-system -l k8s-app=kube-dns --no-headers 2>/dev/null | grep -q "Running"; then
    echo "[OK] CoreDNS is running"
else
    echo "[FAIL] CoreDNS not running"
    FAILED=1
fi

# Check 6: API server
echo ""
echo "Checking API server..."
if HEALTHZ=$(kubectl get --raw /healthz 2>/dev/null) && [ "$HEALTHZ" = "ok" ]; then
    echo "[OK] API server is healthy"
else
    echo "[FAIL] API server not accessible"
    FAILED=1
fi

# Check 7: Metrics server
echo ""
echo "Checking metrics-server..."
if kubectl get pods -n kube-system -l k8s-app=metrics-server --no-headers 2>/dev/null | grep -q "Running"; then
    echo "[OK] metrics-server is running"
else
    echo "[WARN] metrics-server not installed (run 'make infra-install')"
fi

# Check 8: PerfEng namespaces
echo ""
echo "Checking PerfEng namespaces..."

for ns in perf-platform perf-generators perf-sut monitoring; do
    if kubectl get namespace "$ns" --no-headers 2>/dev/null | grep -q .; then
        echo "[OK] $ns namespace exists"
    else
        echo "[WARN] $ns namespace not created (run 'make infra-install')"
    fi
done

# Check 9: ServiceAccounts
echo ""
echo "Checking ServiceAccounts..."

if kubectl get serviceaccount perf-orchestrator -n perf-platform --no-headers 2>/dev/null | grep -q .; then
    echo "[OK] perf-orchestrator ServiceAccount exists"
else
    echo "[WARN] perf-orchestrator ServiceAccount missing"
fi

if kubectl get serviceaccount perf-generator -n perf-generators --no-headers 2>/dev/null | grep -q .; then
    echo "[OK] perf-generator ServiceAccount exists"
else
    echo "[WARN] perf-generator ServiceAccount missing"
fi

# Check 10: Resource Quotas
echo ""
echo "Checking Resource Quotas..."

for ns in perf-platform perf-generators perf-sut; do
    quota_name="${ns}-quota"
    if kubectl get resourcequota "$quota_name" -n "$ns" --no-headers 2>/dev/null | grep -q .; then
        echo "[OK] $quota_name ResourceQuota exists"
    else
        echo "[WARN] $quota_name ResourceQuota missing"
    fi
done

# Check 11: Network Policies
echo ""
echo "Checking Network Policies..."

if kubectl get networkpolicy default-deny-ingress -n perf-generators --no-headers 2>/dev/null | grep -q .; then
    echo "[OK] perf-generators NetworkPolicy exists"
else
    echo "[WARN] perf-generators NetworkPolicy missing"
fi

if kubectl get networkpolicy default-deny-ingress -n perf-sut --no-headers 2>/dev/null | grep -q .; then
    echo "[OK] perf-sut NetworkPolicy exists"
else
    echo "[WARN] perf-sut NetworkPolicy missing"
fi

# Check 12: Monitoring components
echo ""
echo "Checking monitoring components..."

if kubectl get pods -n monitoring -l app=prometheus --no-headers 2>/dev/null | grep -q "Running"; then
    echo "[OK] Prometheus is running"
else
    echo "[WARN] Prometheus not running (run 'make infra-install')"
fi

if kubectl get pods -n monitoring -l app=grafana --no-headers 2>/dev/null | grep -q "Running"; then
    echo "[OK] Grafana is running"
else
    echo "[WARN] Grafana not running (run 'make infra-install')"
fi

if kubectl get pods -n monitoring -l app=kube-state-metrics --no-headers 2>/dev/null | grep -q "Running"; then
    echo "[OK] kube-state-metrics is running"
else
    echo "[WARN] kube-state-metrics not running"
fi

if kubectl get pods -n monitoring -l app=node-exporter --no-headers 2>/dev/null | grep -q "Running"; then
    echo "[OK] node-exporter is running"
else
    echo "[WARN] node-exporter not running"
fi

if kubectl get pods -n perf-platform -l app=minio --no-headers 2>/dev/null | grep -q "Running"; then
    echo "[OK] MinIO is running"
else
    echo "[WARN] MinIO not running"
fi

# Summary
echo ""
echo "========================================="
if [ "$FAILED" -eq 0 ]; then
    echo "All required health checks passed!"
    echo "Review [WARN] items for optional components."
else
    echo "Some health checks failed"
fi
echo "========================================="

exit $FAILED