#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
MINIO_DIR="${REPO_ROOT}/infra/local/kind/k6-jobs/minio"
NAMESPACES_DIR="${REPO_ROOT}/infra/local/kind/perf-namespaces"

echo "========================================="
echo "Installing MinIO"
echo "========================================="

# Check if namespace exists
echo "Checking perf-platform namespace..."
if ! kubectl get namespace perf-platform --no-headers 2>/dev/null | grep -q .; then
    echo "perf-platform namespace not found. Installing namespaces first..."
    
    if [ -d "$NAMESPACES_DIR" ]; then
        kubectl apply -k "$NAMESPACES_DIR"
        echo "[OK] Namespaces installed"
    else
        echo "Error: Namespace manifests not found at: $NAMESPACES_DIR"
        echo "Run 'make install-namespaces' first"
        exit 1
    fi
else
    echo "[OK] perf-platform namespace exists"
fi

# Check if minio directory exists
if [ ! -d "$MINIO_DIR" ]; then
    echo "Error: MinIO manifest directory not found: $MINIO_DIR"
    exit 1
fi

# Apply MinIO manifests
echo ""
echo "Applying MinIO manifests..."

for manifest in pvc.yaml deployment.yaml service.yaml; do
    manifest_path="$MINIO_DIR/$manifest"
    
    if [ ! -f "$manifest_path" ]; then
        echo "[WARN] Manifest not found: $manifest_path"
        continue
    fi
    
    echo "  Applying $manifest..."
    
    if kubectl apply -f "$manifest_path"; then
        echo "  [OK] $manifest applied"
    else
        echo "  [FAIL] Failed to apply $manifest"
        exit 1
    fi
done

# Wait for MinIO to be ready
echo ""
echo "Waiting for MinIO to be ready..."

if kubectl wait --for=condition=ready pod \
    -l app=minio \
    -n perf-platform \
    --timeout=120s 2>/dev/null; then
    echo "[OK] MinIO is ready"
else
    echo "[WARN] MinIO may not be ready yet"
fi

echo ""
echo "MinIO installed successfully!"
echo "API: minio.perf-platform.svc.cluster.local:9000"
echo ""
echo "Access MinIO console (port-forward):"
echo "  kubectl port-forward -n perf-platform svc/minio 9001:9001"
echo "  http://localhost:9001"
echo "  Username: perfeng"
echo "  Password: perfeng123"