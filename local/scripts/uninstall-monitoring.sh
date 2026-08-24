#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MONITORING_DIR="${REPO_ROOT}/infra/local/kind/monitoring"

echo "========================================="
echo "Uninstalling Monitoring Stack"
echo "========================================="

if [ -d "$MONITORING_DIR" ]; then
    kubectl delete -k "$MONITORING_DIR" || true
fi

echo ""
echo "Monitoring stack uninstalled."