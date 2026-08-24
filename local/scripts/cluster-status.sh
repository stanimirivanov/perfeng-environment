#!/bin/bash
set -euo pipefail

CLUSTER_NAME="perfeng-local"

echo "========================================="
echo "PerfEng Local Kubernetes Cluster Status"
echo "========================================="

# Check if cluster exists
if ! kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "Cluster '${CLUSTER_NAME}' does not exist"
    exit 1
fi

echo "Cluster exists: ${CLUSTER_NAME}"
echo ""

# Show nodes
echo "=== Nodes ==="
kubectl get nodes -o wide

echo ""
echo "=== Node Labels ==="
kubectl get nodes --show-labels

echo ""
echo "=== Node Taints ==="
kubectl describe nodes | grep -E "^(Name|Taints):"

echo ""
echo "=== Pods (all namespaces) ==="
kubectl get pods --all-namespaces -o wide

echo ""
echo "=== Resource Usage ==="
if kubectl top nodes 2>/dev/null; then
    :
else
    echo "metrics-server not installed yet"
fi

echo ""
echo "=== Kubernetes Version ==="
# Client version
CLIENT_VERSION=$(kubectl version --client -o json 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin)['gitVersion'])" 2>/dev/null || echo "unknown")
echo "Client Version: ${CLIENT_VERSION}"

# Server version (direct API call)
SERVER_VERSION=$(kubectl get --raw /version 2>/dev/null | python3 -c "import sys, json; print(json.load(sys.stdin)['gitVersion'])" 2>/dev/null || echo "unknown")
echo "Server Version: ${SERVER_VERSION}"