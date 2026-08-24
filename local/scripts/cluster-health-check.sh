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
    echo ""
    echo "Cluster health check failed!"
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

# Check 3: Node labels present
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

# Check 4: Control plane ready
echo ""
echo "Checking control plane..."
if kubectl get nodes -l node-role.kubernetes.io/control-plane --no-headers 2>/dev/null | grep -q " Ready "; then
    echo "[OK] Control plane is ready"
else
    echo "[FAIL] Control plane not ready"
    FAILED=1
fi

# Check 5: CoreDNS running
echo ""
echo "Checking CoreDNS..."
if kubectl get pods -n kube-system -l k8s-app=kube-dns --no-headers 2>/dev/null | grep -q "Running"; then
    echo "[OK] CoreDNS is running"
else
    echo "[FAIL] CoreDNS not running"
    FAILED=1
fi

# Check 6: API server accessible
echo ""
echo "Checking API server..."
if HEALTHZ=$(kubectl get --raw /healthz 2>/dev/null) && [ "$HEALTHZ" = "ok" ]; then
    echo "[OK] API server is healthy"
else
    echo "[FAIL] API server not accessible"
    FAILED=1
fi

# Check 7: Metrics server (if installed)
echo ""
echo "Checking metrics-server..."
if kubectl get pods -n kube-system -l k8s-app=metrics-server --no-headers 2>/dev/null | grep -q "Running"; then
    echo "[OK] metrics-server is running"
else
    echo "[WARN] metrics-server not installed (optional)"
fi

# Summary
echo ""
echo "========================================="
if [ "$FAILED" -eq 0 ]; then
    echo "All health checks passed!"
else
    echo "Some health checks failed"
fi
echo "========================================="

exit $FAILED