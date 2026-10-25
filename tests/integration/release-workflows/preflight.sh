# CI-only Git/schema/identity child parity; the complete parent must reject this UID.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -n ${fixture:-} && -f $fixture/owned && ${KUBECONFIG:-} == "$fixture/kubeconfig" ]] || exit 2
declare -F k a > /dev/null || exit 2

k apply -f gitops/release-workflows/rbac/preflight
k -n argo-workflows get secret release-git-read -o json | jq -S . > "$fixture/preflight-secret-before.json"
uv run --locked --offline python - "$fixture" "$revision" "$cluster_uid" <<'PY'
import hashlib, json, sys
from pathlib import Path
directory, revision, cluster = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
path = "deployments/release-requests/ci-preflight-fixture-v1.json"
raw = Path(path).read_bytes()
request = json.loads(raw)
assert request["environment"]["clusterUID"] == "00000000-0000-0000-0000-000000000000" != cluster
assert request["selection"]["revision"] != revision
parameters = {"request-revision": revision, "request-path": path,
    "request-sha256": "sha256:" + hashlib.sha256(raw).hexdigest(),
    "selection-revision": request["selection"]["revision"]}
(directory / "preflight-parameters.json").write_text(json.dumps(parameters))
(directory / "preflight-wrong-digest.json").write_text(json.dumps({**parameters,
    "request-sha256": "sha256:" + "0" * 64}))
parameters["request-path"] = "deployments/release-requests/../local-control-plane.json"
(directory / "preflight-unsafe-path.json").write_text(json.dumps(parameters))
PY

preflight_case() {
  preflight_label=$1 preflight_input=$2 preflight_step=$3 preflight_refusal=$4 preflight_source=$5
  echo "Git preflight case: $preflight_label"
  if [[ $preflight_source == parent ]]; then
    preflight_workflow=$(a submit --from workflowtemplate/perfeng-release-preflight-v3 \
      --parameter-file "$preflight_input" -o json | jq -er .metadata.name)
  else
    [[ $preflight_source == fixture ]]
    preflight_workflow=$(a submit "$source_dir/preflight-workflow.yaml" \
      --parameter-file "$preflight_input" -o json | jq -er .metadata.name)
  fi
  if timeout 630s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$preflight_workflow"; then preflight_rc=0; else preflight_rc=$?; fi
  a get "$preflight_workflow" -o json > "$results/preflight-$preflight_label.workflow.json"
  if [[ $preflight_refusal == success ]]; then
    [[ $preflight_rc == 0 ]]
    jq -e '.status.phase == "Succeeded"' "$results/preflight-$preflight_label.workflow.json" > /dev/null
    jq -e --arg uid "$cluster_uid" 'any(.status.nodes[.metadata.name].outputs.parameters[];
      .name == "actual-cluster-uid" and .value == $uid)' "$results/preflight-$preflight_label.workflow.json" > /dev/null
    jq -er '.status.nodes[.metadata.name].outputs.parameters[] | select(.name == "result") | .value' \
      "$results/preflight-$preflight_label.workflow.json" > "$results/preflight-$preflight_label.result.json"
    uv run --locked --offline python - "$results/preflight-$preflight_label.result.json" "$fixture/preflight-parameters.json" <<'PY'
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
value, parameters = (json.loads(Path(p).read_text()) for p in sys.argv[1:])
request = json.loads(Path(parameters["request-path"]).read_text())
Draft202012Validator(json.loads(Path("deployments/release-preflight-result-v1.schema.json").read_text())).validate(value)
for field, parameter in (("requestRevision", "request-revision"), ("requestDigest", "request-sha256"),
                         ("requestPath", "request-path"), ("selectionRevision", "selection-revision")):
    assert value[field] == parameters[parameter]
assert value["clusterUID"] == request["environment"]["clusterUID"]
assert value["lockDigests"] == request["selection"]["lockDigests"]
PY
  else
    [[ $preflight_rc != 0 && $preflight_rc != 124 && $preflight_rc != 137 ]]
    jq -e --arg step "$preflight_step" '(.status.phase == "Failed" or .status.phase == "Error") and
      ([.status.nodes[] | select(.displayName == $step and .type == "Pod" and
        (.phase == "Failed" or .phase == "Error"))] | length == 1) and
      ([.status.nodes[] | .outputs.parameters[]? | select(.name == "result" and has("value"))] | length == 0)' \
      "$results/preflight-$preflight_label.workflow.json" > /dev/null
    k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$preflight_workflow" -o json \
      | jq -e '[.items[].status.containerStatuses[]? | select(.name == "main" and .state.terminated.exitCode == 2)] | length == 1' > /dev/null
    k -n argo-workflows logs -l "workflows.argoproj.io/workflow=$preflight_workflow" \
      -c main --tail=20 --limit-bytes=8192 > "$fixture/preflight-worker.log"
    grep -Fx "$preflight_refusal" "$fixture/preflight-worker.log" > "$results/preflight-$preflight_label.refusal.txt"
  fi
}
preflight_case first "$fixture/preflight-parameters.json" verify-identities success fixture
preflight_case repeat "$fixture/preflight-parameters.json" verify-identities success fixture
cmp "$results/preflight-first.result.json" "$results/preflight-repeat.result.json"
preflight_case wrong-digest "$fixture/preflight-wrong-digest.json" verify-identities \
  'Preflight refused: request bytes differ from the submitted digest' fixture
preflight_case unsafe-path "$fixture/preflight-unsafe-path.json" validate-schema 'Invalid request name' fixture
preflight_case wrong-cluster "$fixture/preflight-parameters.json" verify-identities \
  'Preflight refused: target cluster UID differs from the request' parent
preflight_case corrected "$fixture/preflight-parameters.json" verify-identities success fixture
cmp "$results/preflight-first.result.json" "$results/preflight-corrected.result.json"

k -n argo-workflows get secret release-git-read -o json | jq -S . > "$fixture/preflight-secret-after.json"
cmp "$fixture/preflight-secret-before.json" "$fixture/preflight-secret-after.json"
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
for preflight_case_name in first repeat wrong-digest unsafe-path wrong-cluster corrected; do
  retained_name=$(jq -er .metadata.name "$results/preflight-$preflight_case_name.workflow.json")
  retained_uid=$(jq -er .metadata.uid "$results/preflight-$preflight_case_name.workflow.json")
  [[ $(k -n argo-workflows get workflow "$retained_name" -o jsonpath='{.metadata.uid}') == "$retained_uid" ]]
done
preflight_identity=system:serviceaccount:argo-workflows:release-preflight
for permission in 'patch namespaces kube-system' 'create secrets argo-workflows' 'list secrets argo-workflows' \
  'create jobs perf-platform' 'patch deployments perf-platform' 'patch applications argocd'; do
  read -r verb resource namespace <<< "$permission"
  [[ $(k auth can-i "$verb" "$resource" -n "$namespace" --as "$preflight_identity" || :) == no ]]
done
[[ $(k auth can-i create pods --subresource=exec -n perf-platform --as "$preflight_identity" || :) == no ]]
echo 'Git/schema/identity child parity passed; complete production preflight rejected the synthetic cluster UID.'
