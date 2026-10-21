# CI-only installed verifier/HTTP parity with explicitly synthetic observations.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -n ${fixture:-} && -f $fixture/owned && ${KUBECONFIG:-} == "$fixture/kubeconfig" ]] || exit 2
declare -F k a > /dev/null || exit 2

k apply -f gitops/release-workflows/rbac/readiness
k -n perf-platform create configmap readiness-ci-http --from-file="$source_dir/readiness_server.py"
k create -f "$source_dir/readiness-pod.yaml"
k create -f "$source_dir/readiness-service.yaml"
k -n perf-platform wait --for=condition=Ready pod/readiness-ci-http --timeout=90s
readiness_pod_uid=$(k -n perf-platform get pod readiness-ci-http -o jsonpath='{.metadata.uid}')
readiness_service_uid=$(k -n perf-platform get service control-plane -o jsonpath='{.metadata.uid}')

# Reuse existing policy fixtures; these are not observations of deployed workloads.
uv run --locked --offline python - "$fixture" "$revision" <<'PY'
import json, sys
from pathlib import Path
directory, revision = Path(sys.argv[1]), sys.argv[2]
observations = json.loads(Path("tests/fixtures/release-readiness-observations.json").read_text())
parameters = {name: json.dumps(value) for name, value in observations.items()}
parameters.update({"selection-revision": revision,
    "preflight-result": (directory / "preflight.json").read_text()})
(directory / "readiness-parameters.json").write_text(json.dumps(parameters))
observations["deployment"]["images"] = ["unreviewed:latest"]
parameters["deployment"] = json.dumps(observations["deployment"])
(directory / "readiness-wrong-image.json").write_text(json.dumps(parameters))
PY

readiness_admin() {
  # Only fixture administration has exec rights; mode changes are loopback-only.
  k -n perf-platform exec readiness-ci-http -c http -- python -c '
import sys, urllib.request
mode = sys.argv[1]
request = urllib.request.Request("http://127.0.0.1:8080/fixture-mode/" + mode, method="POST")
with urllib.request.urlopen(request, timeout=5) as response:
    assert response.status == 200
' "$1"
}
readiness_case() {
  readiness_label=$1 readiness_mode=$2 readiness_input=$3 readiness_refusal=$4 readiness_counts=$5
  echo "Readiness case: $readiness_label"
  readiness_admin "$readiness_mode"
  readiness_workflow=$(a submit "$source_dir/readiness-workflow.yaml" \
    --parameter-file "$readiness_input" -o json | jq -er .metadata.name)
  if timeout 330s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$readiness_workflow"; then readiness_rc=0; else readiness_rc=$?; fi
  a get "$readiness_workflow" -o json > "$results/readiness-$readiness_label.workflow.json"
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$readiness_workflow" -o json \
    > "$fixture/readiness-pods.json"
  # Save only counts, with no response bodies, URLs or private Git logs.
  k -n perf-platform exec readiness-ci-http -c http -- python -c '
import urllib.request
with urllib.request.urlopen("http://127.0.0.1:8080/fixture-state", timeout=5) as response:
    print(response.read(4096).decode())
' > "$results/readiness-$readiness_label.counts.json"
  jq -e --argjson expected "$readiness_counts" '. == $expected' \
    "$results/readiness-$readiness_label.counts.json" > /dev/null
  if [[ $readiness_refusal == success ]]; then
    [[ $readiness_rc == 0 ]]
    jq -e '.status.phase == "Succeeded"' "$results/readiness-$readiness_label.workflow.json" > /dev/null
    jq -er '.status.nodes[.metadata.name].outputs.parameters[] | select(.name == "result") | .value' \
      "$results/readiness-$readiness_label.workflow.json" > "$results/readiness-$readiness_label.result.json"
    uv run --locked --offline python - "$results/readiness-$readiness_label.result.json" "$fixture/preflight.json" <<'PY'
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
result, preflight = (json.loads(Path(p).read_text()) for p in sys.argv[1:])
Draft202012Validator(json.loads(Path("deployments/release-readiness-result-v1.schema.json").read_text())).validate(result)
for key in ("requestDigest", "requestRevision", "selectionRevision", "clusterUID"):
    assert result[key] == preflight[key]
PY
  else
    [[ $readiness_rc != 0 && $readiness_rc != 124 && $readiness_rc != 137 ]]
    jq -e '.status.phase == "Failed" or .status.phase == "Error"' \
      "$results/readiness-$readiness_label.workflow.json" > /dev/null
    jq -e '[.items[].status.containerStatuses[]? | select(.name == "main")] |
      length == 1 and .[0].state.terminated.exitCode == 2' "$fixture/readiness-pods.json" > /dev/null
    jq -e '[.status.nodes[] | .outputs.parameters[]? | select(.name == "result")] | length == 0' \
      "$results/readiness-$readiness_label.workflow.json" > /dev/null
    k -n argo-workflows logs -l "workflows.argoproj.io/workflow=$readiness_workflow" \
      -c main --tail=20 --limit-bytes=8192 > "$fixture/readiness-worker.log"
    grep -Fx "Readiness refused: $readiness_refusal" "$fixture/readiness-worker.log" \
      > "$results/readiness-$readiness_label.refusal.txt"
  fi
}
all_probes='{"/livez":1,"/readyz":1,"/metrics":1,"/redirect-target":0}'
no_probes='{"/livez":0,"/readyz":0,"/metrics":0,"/redirect-target":0}'
readiness_case first healthy "$fixture/readiness-parameters.json" success "$all_probes"
readiness_case repeat healthy "$fixture/readiness-parameters.json" success "$all_probes"
cmp "$results/readiness-first.result.json" "$results/readiness-repeat.result.json"
readiness_case wrong-image healthy "$fixture/readiness-wrong-image.json" \
  'Deployment differs from the selected runtime' "$no_probes"
readiness_case unavailable readyz-unavailable "$fixture/readiness-parameters.json" \
  'operational endpoint failed: readyz' '{"/livez":1,"/readyz":1,"/metrics":0,"/redirect-target":0}'
readiness_case redirect metrics-redirect "$fixture/readiness-parameters.json" \
  'operational endpoint failed: metrics' "$all_probes"
readiness_case invalid-metrics metrics-invalid "$fixture/readiness-parameters.json" \
  'operational endpoint failed: metrics' "$all_probes"
readiness_case corrected healthy "$fixture/readiness-parameters.json" success "$all_probes"
cmp "$results/readiness-first.result.json" "$results/readiness-corrected.result.json"
[[ $(k -n perf-platform get pod readiness-ci-http -o jsonpath='{.metadata.uid}') == "$readiness_pod_uid" ]]
[[ $(k -n perf-platform get service control-plane -o jsonpath='{.metadata.uid}') == "$readiness_service_uid" ]]
k -n argo-workflows get workflows -l perfeng.io/fixture=readiness -o json \
  | jq -e '.items | length == 7 and ([.[].metadata.uid] | unique | length == 7)' > /dev/null
readiness_identity=system:serviceaccount:argo-workflows:release-readiness
for permission in 'patch deployments perf-platform' 'create jobs perf-platform' 'create secrets perf-platform' \
  'list secrets argo-workflows' 'patch applications argocd'; do
  read -r verb resource namespace <<< "$permission"
  [[ $(k auth can-i "$verb" "$resource" -n "$namespace" --as "$readiness_identity" || :) == no ]]
done
[[ $(k auth can-i create pods --subresource=exec -n perf-platform --as "$readiness_identity" || :) == no ]]
echo 'Readiness child runtime/HTTP parity passed; observations are synthetic and full release acceptance remains separate.'
