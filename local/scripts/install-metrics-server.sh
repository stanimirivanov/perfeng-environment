#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
METRICS_SERVER_DIR="${REPO_ROOT}/infra/local/kind/metrics-server"

echo "========================================="
echo "Installing metrics-server"
echo "========================================="

# Check if cluster exists
if ! kind get clusters 2>/dev/null | grep -q "^perfeng-local$"; then
    echo "Error: Cluster 'perfeng-local' does not exist. Run 'make cluster-up' first."
    exit 1
fi

# Check if manifest directory exists
if [ ! -d "$METRICS_SERVER_DIR" ]; then
    echo "Error: Manifest directory not found: $METRICS_SERVER_DIR"
    exit 1
fi

# Apply using kustomize
echo "Applying metrics-server manifests using kustomize..."
kubectl apply -k "$METRICS_SERVER_DIR"

# Wait for metrics-server to be ready
echo ""
echo "Waiting for metrics-server to be ready..."
kubectl wait --for=condition=ready pod \
    -l k8s-app=metrics-server \
    -n kube-system \
    --timeout=120s

# Verify installation
echo ""
echo "Verifying metrics-server..."
kubectl get apiservice v1beta1.metrics.k8s.io

echo ""
echo "Metrics-server installed successfully!"

# Test metrics
echo ""
echo "Testing node metrics..."
sleep 15
kubectl top nodes