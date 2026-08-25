#!/bin/bash
set -euo pipefail

# Default values
TEST_NAME=""
PROFILE=""
BASE_URL="http://perf-sut-service.perf-sut:8080"
OUTPUT_DIR="results"
TIMEOUT_SECONDS=900

# Show help function
show_help() {
    echo "========================================="
    echo "Run k6 Test as Kubernetes Job"
    echo "========================================="
    echo ""
    echo "Usage:"
    echo "  bash run-k6-test.sh --test-name <test> --profile <profile>"
    echo ""
    echo "Parameters:"
    echo "  --test-name       Required. Test scenario name (checkout, search, account)"
    echo "  --profile         Required. Workload profile (smoke, average, regression, stress, capacity)"
    echo "  --base-url        Optional. Base URL of SUT (default: http://perf-sut-service.perf-sut:8080)"
    echo "  --output-dir      Optional. Output directory (default: results)"
    echo "  --timeout         Optional. Job timeout in seconds (default: 900)"
    echo ""
    echo "Examples:"
    echo "  bash run-k6-test.sh --test-name checkout --profile smoke"
    echo "  bash run-k6-test.sh --test-name search --profile regression"
    echo "  bash run-k6-test.sh --test-name checkout --profile stress --timeout 1800"
    exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --test-name)
            TEST_NAME="$2"
            shift 2
            ;;
        --profile)
            PROFILE="$2"
            shift 2
            ;;
        --base-url)
            BASE_URL="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --timeout)
            TIMEOUT_SECONDS="$2"
            shift 2
            ;;
        --help|-h)
            show_help
            ;;
        *)
            echo "Unknown option: $1"
            show_help
            ;;
    esac
done

# Validate required arguments
if [[ -z "$TEST_NAME" ]]; then
    echo "Error: --test-name is required"
    show_help
fi

if [[ -z "$PROFILE" ]]; then
    echo "Error: --profile is required"
    show_help
fi

# Correct path: go up 5 levels from scripts/ to repository root
# scripts/ → k6-jobs/ → kind/ → local/ → infra/ → performance-platform/
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../../../.." && pwd)"
K6_DIR="${REPO_ROOT}/tests/k6"
OUTPUT_DIR_PATH="${REPO_ROOT}/${OUTPUT_DIR}"

echo "========================================="
echo "Running k6 Test as Kubernetes Job"
echo "========================================="
echo "Test: ${TEST_NAME}"
echo "Profile: ${PROFILE}"
echo "Base URL: ${BASE_URL}"
echo "Repo Root: ${REPO_ROOT}"
echo "K6 Dir: ${K6_DIR}"
echo ""

# Generate unique run ID
RUN_ID="perf-$(date +%Y%m%d-%H%M%S)-$(uuidgen 2>/dev/null | cut -c1-8 || cat /proc/sys/kernel/random/uuid | cut -c1-8)"
echo "Run ID: ${RUN_ID}"

# Define paths
TEST_SCRIPT="tests/${TEST_NAME}/scenario.js"
WORKLOAD_CONFIG="workloads/${PROFILE}/${TEST_NAME}.json"
CONFIG_MAP_NAME="k6-test-scripts-${TEST_NAME}"
JOB_NAME="k6-test-${TEST_NAME}-$(uuidgen 2>/dev/null | cut -c1-8 || date +%s)"

# Check if test script exists
TEST_SCRIPT_PATH="${K6_DIR}/${TEST_SCRIPT}"
if [[ ! -f "${TEST_SCRIPT_PATH}" ]]; then
    echo "[FAIL] Test script not found: ${TEST_SCRIPT_PATH}"
    echo ""
    echo "Available tests:"
    
    TESTS_DIR="${K6_DIR}/tests"
    if [[ -d "${TESTS_DIR}" ]]; then
        for dir in "${TESTS_DIR}"/*/; do
            echo "  - $(basename "$dir")"
        done
    else
        echo "  (tests directory not found)"
    fi
    exit 1
fi

# Check if workload config exists
WORKLOAD_CONFIG_PATH="${K6_DIR}/${WORKLOAD_CONFIG}"
if [[ ! -f "${WORKLOAD_CONFIG_PATH}" ]]; then
    echo "[FAIL] Workload config not found: ${WORKLOAD_CONFIG_PATH}"
    echo ""
    echo "Available profiles for ${TEST_NAME}:"
    
    WORKLOADS_DIR="${K6_DIR}/workloads"
    if [[ -d "${WORKLOADS_DIR}" ]]; then
        for dir in "${WORKLOADS_DIR}"/*/; do
            profile_name=$(basename "$dir")
            config_file="${K6_DIR}/workloads/${profile_name}/${TEST_NAME}.json"
            if [[ -f "${config_file}" ]]; then
                echo "  - ${profile_name}"
            fi
        done
    else
        echo "  (workloads directory not found)"
    fi
    exit 1
fi

# Check if namespace exists
echo "Checking perf-generators namespace..."
if ! kubectl get namespace perf-generators --no-headers 2>/dev/null | grep -q .; then
    echo "[FAIL] perf-generators namespace not found"
    echo "Run 'make install-namespaces' first"
    exit 1
fi
echo "[OK] perf-generators namespace exists"

# Create ConfigMap with test scripts
echo ""
echo "Creating ConfigMap with test scripts..."

# Delete existing ConfigMap if it exists
kubectl delete configmap "${CONFIG_MAP_NAME}" -n perf-generators 2>/dev/null || true

# Create ConfigMap using kubectl create configmap --from-file
if ! kubectl create configmap "${CONFIG_MAP_NAME}" \
    -n perf-generators \
    --from-file="scenario.js=${TEST_SCRIPT_PATH}" \
    --from-file="workload.json=${WORKLOAD_CONFIG_PATH}"; then
    echo "[FAIL] Failed to create ConfigMap"
    exit 1
fi
echo "[OK] ConfigMap created"

# Create Job
echo "Creating Kubernetes Job..."

cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: ${JOB_NAME}
  namespace: perf-generators
  labels:
    app: k6-test
    perfeng.io/component: test-runner
    perfeng.io/test: "${TEST_NAME}"
    perfeng.io/profile: "${PROFILE}"
    perfeng.io/run-id: "${RUN_ID}"
spec:
  backoffLimit: 2
  activeDeadlineSeconds: ${TIMEOUT_SECONDS}
  ttlSecondsAfterFinished: 3600
  template:
    metadata:
      labels:
        app: k6-test
        perfeng.io/test: "${TEST_NAME}"
        perfeng.io/run-id: "${RUN_ID}"
    spec:
      restartPolicy: Never
      serviceAccountName: perf-generator
      nodeSelector:
        workload: performance-generator
      containers:
        - name: k6
          image: grafana/k6:2.2.0
          imagePullPolicy: IfNotPresent
          command:
            - /bin/sh
            - -c
            - |
              echo "Running k6 test..."
              echo "Test: ${TEST_NAME}"
              echo "Profile: ${PROFILE}"
              echo "Run ID: ${RUN_ID}"
              echo "--- Test data files ---"
              ls -la /test-data/
              echo "--- Scenario file (first 10 lines) ---"
              head -10 /test-data/scenario.js
              echo "--- Workload config ---"
              cat /test-data/workload.json
              echo "--- Starting k6 ---"
              k6 run --config /test-data/workload.json /test-data/scenario.js --out json=/results/results.json --summary-export /results/summary.json
          env:
            - name: BASE_URL
              value: "${BASE_URL}"
            - name: PERF_RUN_ID
              value: "${RUN_ID}"
            - name: PERF_PROFILE
              value: "${PROFILE}"
            - name: PERF_TEST_NAME
              value: "${TEST_NAME}"
          volumeMounts:
            - name: test-data
              mountPath: /test-data
            - name: results
              mountPath: /results
          resources:
            requests:
              cpu: "100m"
              memory: "256Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
      volumes:
        - name: test-data
          configMap:
            name: ${CONFIG_MAP_NAME}
            items:
              - key: "scenario.js"
                path: "scenario.js"
              - key: "workload.json"
                path: "workload.json"
        - name: results
          emptyDir: {}
EOF

echo "[OK] Job created: ${JOB_NAME}"

# Wait for job completion
echo ""
echo "Waiting for job to complete..."

MAX_WAIT_SECONDS=$((TIMEOUT_SECONDS + 120))
ELAPSED=0
JOB_SUCCEEDED=false

while [[ ${ELAPSED} -lt ${MAX_WAIT_SECONDS} ]]; do
    JOB_STATUS=$(kubectl get job "${JOB_NAME}" -n perf-generators -o jsonpath='{.status.conditions[0].type}' 2>/dev/null || echo "")
    
    if [[ "${JOB_STATUS}" == "Complete" ]]; then
        echo "[OK] Job completed successfully"
        JOB_SUCCEEDED=true
        break
    fi
    
    if [[ "${JOB_STATUS}" == "Failed" ]]; then
        echo "[FAIL] Job failed"
        break
    fi
    
    sleep 10
    ELAPSED=$((ELAPSED + 10))
    
    if [[ $((ELAPSED % 30)) -eq 0 ]]; then
        echo "  Waiting... (${ELAPSED} seconds elapsed)"
        
        # Show pod status
        POD_STATUS=$(kubectl get pods -n perf-generators -l "perfeng.io/run-id=${RUN_ID}" --no-headers 2>/dev/null || echo "")
        if [[ -n "${POD_STATUS}" ]]; then
            echo "  Pod status: ${POD_STATUS}"
        fi
    fi
done

# Collect results
echo ""
echo "Collecting results..."

RUN_OUTPUT_DIR="${OUTPUT_DIR_PATH}/${RUN_ID}"
mkdir -p "${RUN_OUTPUT_DIR}"

POD_NAME=$(kubectl get pods -n perf-generators \
    -l "perfeng.io/run-id=${RUN_ID}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [[ -n "${POD_NAME}" ]] && [[ "${POD_NAME}" != "Error"* ]]; then
    # Copy results from pod
    kubectl cp "perf-generators/${POD_NAME}:/results/results.json" "${RUN_OUTPUT_DIR}/results.json" 2>/dev/null || true
    kubectl cp "perf-generators/${POD_NAME}:/results/summary.json" "${RUN_OUTPUT_DIR}/summary.json" 2>/dev/null || true
    
    # Get pod logs
    kubectl logs "${POD_NAME}" -n perf-generators > "${RUN_OUTPUT_DIR}/pod.log" 2>/dev/null || true
    
    echo "[OK] Results saved to: ${RUN_OUTPUT_DIR}"
else
    echo "[WARN] Could not find pod for run ${RUN_ID}"
    echo "Check with: kubectl get pods -n perf-generators"
fi

# Clean up
echo ""
echo "Cleaning up..."

kubectl delete configmap "${CONFIG_MAP_NAME}" -n perf-generators 2>/dev/null || true
kubectl delete job "${JOB_NAME}" -n perf-generators 2>/dev/null || true

echo ""
echo "========================================="
echo "k6 Test Execution Summary"
echo "========================================="
echo "Run ID: ${RUN_ID}"
if [[ "${JOB_SUCCEEDED}" == "true" ]]; then
    echo "Status: SUCCESS"
else
    echo "Status: FAILED"
fi
echo "Results: ${RUN_OUTPUT_DIR}"
echo "========================================="