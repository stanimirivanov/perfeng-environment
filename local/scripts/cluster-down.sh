#!/bin/bash
set -euo pipefail

CLUSTER_NAME="perfeng-local"

echo "========================================="
echo "Stopping PerfEng Local Kubernetes Cluster"
echo "========================================="

# Check if cluster exists
if ! kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
    echo "Cluster '${CLUSTER_NAME}' does not exist"
    exit 0
fi

# Delete cluster
echo "Deleting cluster '${CLUSTER_NAME}'..."
kind delete cluster --name "${CLUSTER_NAME}"

echo ""
echo "Cluster deleted successfully!"