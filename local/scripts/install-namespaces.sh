#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
NAMESPACES_DIR="${REPO_ROOT}/infra/local/kind/perf-namespaces"

echo "========================================="
echo "Installing PerfEng Namespaces and RBAC"
echo "========================================="

# Check if cluster exists
if ! kind get clusters 2>/dev/null | grep -q "^perfeng-local$"; then
    echo "Error: Cluster 'perfeng-local' does not exist. Run 'make cluster-up' first."
    exit 1
fi

# Check if manifest directory exists
if [ ! -d "$NAMESPACES_DIR" ]; then
    echo "Error: Manifest directory not found: $NAMESPACES_DIR"
    exit 1
fi

# Apply using kustomize
echo "Applying namespace and RBAC manifests..."
kubectl apply -k "$NAMESPACES_DIR"

# Wait for namespaces to be ready
echo ""
echo "Verifying namespaces..."
kubectl get namespaces -l perfeng.io/managed-by=perfeng

# Verify RBAC
echo ""
echo "Verifying RBAC..."
kubectl get serviceaccounts -n perf-platform
kubectl get roles -n perf-generators
kubectl get roles -n perf-sut
kubectl get rolebindings -n perf-generators
kubectl get rolebindings -n perf-sut

# Verify quotas
echo ""
echo "Verifying resource quotas..."
kubectl get resourcequotas -n perf-platform
kubectl get resourcequotas -n perf-generators
kubectl get resourcequotas -n perf-sut

echo ""
echo "Namespace installation complete!"