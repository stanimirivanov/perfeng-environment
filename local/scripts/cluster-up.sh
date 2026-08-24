#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CLUSTER_CONFIG="${REPO_ROOT}/infra/local/kind/cluster-config.yaml"
CLUSTER_NAME="perfeng-local"

echo "========================================="
echo "Starting PerfEng Local Kubernetes Cluster"
echo "========================================="

# Check if kind is installed
if ! command -v kind &> /dev/null; then
    echo "Error: kind is not installed"
    echo "Install from: https://kind.sigs.k8s.io/docs/user/quick-start/#installation"
    exit 1
fi

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo "Error: Docker is not running"
    exit 1
fi

# Check if cluster already exists
if kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "Cluster '${CLUSTER_NAME}' already exists"
    echo "Use 'make cluster-down' to delete it first"
    exit 0
fi

# Clean up any partial cluster from previous failed attempts
echo "Cleaning up any previous partial cluster..."
kind delete cluster --name "${CLUSTER_NAME}" 2>/dev/null || true

# Create cluster
echo "Creating kind cluster '${CLUSTER_NAME}'..."
if ! kind create cluster \
    --name "${CLUSTER_NAME}" \
    --config "${CLUSTER_CONFIG}" \
    --wait 300s; then
    
    echo ""
    echo "Failed to create cluster."
    echo ""
    echo "Troubleshooting tips:"
    echo "1. Ensure Docker Desktop is running"
    echo "2. Check Docker Desktop has enough resources (Settings > Resources)"
    echo "   - CPUs: at least 4"
    echo "   - Memory: at least 8GB"
    echo "3. Try restarting Docker Desktop"
    echo "4. Run 'kind delete cluster --name ${CLUSTER_NAME}' and try again"
    exit 1
fi

echo ""
echo "Cluster created successfully!"

# Wait for nodes to be ready
echo "Waiting for nodes to be ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=300s || {
    echo "Warning: Some nodes may not be ready"
}

# Display cluster info
echo ""
echo "Cluster nodes:"
kubectl get nodes -o wide

echo ""
echo "Cluster setup complete!"
echo ""
echo "Next steps:"
echo "  1. Install metrics-server: make install-metrics"
echo "  2. Check cluster health: make cluster-health"