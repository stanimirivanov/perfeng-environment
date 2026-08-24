#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MONITORING_DIR="${REPO_ROOT}/infra/local/kind/monitoring"

echo "========================================="
echo "Installing Monitoring Stack"
echo "========================================="

# Check if cluster exists
if ! kind get clusters 2>/dev/null | grep -q "^perfeng-local$"; then
    echo "Error: Cluster 'perfeng-local' does not exist. Run 'make cluster-up' first."
    exit 1
fi

# Check if manifest directory exists
if [ ! -d "$MONITORING_DIR" ]; then
    echo "Error: Manifest directory not found: $MONITORING_DIR"
    exit 1
fi

# Apply monitoring stack
echo "Applying monitoring manifests..."
if ! kubectl apply -k "$MONITORING_DIR"; then
    echo ""
    echo "Failed to apply monitoring manifests."
    echo "Check the error above and fix any YAML issues."
    exit 1
fi

# Wait for pods to be ready
echo ""
echo "Waiting for monitoring pods to be ready..."

PODS_TO_WAIT=("prometheus" "kube-state-metrics" "node-exporter" "grafana")

for pod in "${PODS_TO_WAIT[@]}"; do
    echo "  Waiting for $pod..."
    if kubectl wait --for=condition=ready pod \
        -l app="$pod" \
        -n monitoring \
        --timeout=180s 2>/dev/null; then
        echo "  [OK] $pod is ready"
    else
        echo "  [WARN] $pod may not be ready"
    fi
done

echo ""
echo "Verifying monitoring stack..."
kubectl get pods -n monitoring -o wide

echo ""
echo "Monitoring stack installed successfully!"
echo ""
echo "Access Grafana at: http://localhost:30300"
echo "  Username: admin"
echo "  Password: admin"
echo ""
echo "Prometheus is available via port-forward:"
echo "  Run: kubectl port-forward -n monitoring svc/prometheus 9090:9090"