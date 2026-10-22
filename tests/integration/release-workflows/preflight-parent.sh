#!/usr/bin/env bash
# Sourced only by the trusted main CI mode after isolated substrate bootstrap.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k a > /dev/null || exit 2

k apply -f gitops/release-workflows/rbac/preflight
cluster_uid=$(k get namespace kube-system -o jsonpath='{.metadata.uid}')
k -n argo-workflows get secret release-git-read -o json | jq -S . > "$fixture/parent-secret-before.json"
uv run --locked --offline python "$source_dir/request_ref.py" publish "$fixture" "$cluster_uid"
cp "$fixture/request-ref.json" "$results/parent-request-ref.json"
cp "$fixture/parent-request.json" "$results/parent-request.json"
cp "$fixture/parent-parameters.json" "$results/parent-parameters.json"

for label in first repeat; do
  echo "Complete production preflight: $label"
  name=$(a submit --from workflowtemplate/perfeng-release-preflight-v2 \
    --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
  if timeout 630s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$name"; then parent_rc=0; else parent_rc=$?; fi
  a get "$name" -o json > "$results/parent-$label.workflow.json"
  [[ $parent_rc == 0 ]]
  jq -e '.status.phase == "Succeeded"' "$results/parent-$label.workflow.json" > /dev/null
  jq -er '.status.nodes[.metadata.name].outputs.parameters[] | select(.name == "result") | .value' \
    "$results/parent-$label.workflow.json" > "$results/parent-$label.result.json"
  uv run --locked --offline python - "$fixture" "$results/parent-$label.result.json" "$cluster_uid" <<'PY'
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
directory, result, cluster = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
value = json.loads(result.read_text())
parameters = json.loads((directory / "parent-parameters.json").read_text())
request = json.loads((directory / "parent-request.json").read_text())
Draft202012Validator(json.loads(Path("deployments/release-preflight-result-v1.schema.json").read_text())).validate(value)
for field, parameter in (("requestRevision", "request-revision"), ("requestDigest", "request-sha256"),
                         ("requestPath", "request-path"), ("selectionRevision", "selection-revision")):
    assert value[field] == parameters[parameter]
assert value["clusterUID"] == request["environment"]["clusterUID"] == cluster
assert value["lockDigests"] == request["selection"]["lockDigests"]
assert value["environment"] == request["environment"]["name"]
assert parameters["request-revision"] != parameters["selection-revision"]
PY
done
cmp "$results/parent-first.result.json" "$results/parent-repeat.result.json"
k -n argo-workflows get secret release-git-read -o json | jq -S . > "$fixture/parent-secret-after.json"
cmp "$fixture/parent-secret-before.json" "$fixture/parent-secret-after.json"
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
for label in first repeat; do
  name=$(jq -er .metadata.name "$results/parent-$label.workflow.json")
  uid=$(jq -er .metadata.uid "$results/parent-$label.workflow.json")
  [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
done
identity=system:serviceaccount:argo-workflows:release-preflight
for permission in 'patch namespaces kube-system' 'create secrets argo-workflows' 'list secrets argo-workflows' \
  'create jobs perf-platform' 'patch deployments perf-platform' 'patch applications argocd'; do
  read -r verb resource namespace <<< "$permission"
  [[ $(k auth can-i "$verb" "$resource" -n "$namespace" --as "$identity" || :) == no ]]
done
[[ $(k auth can-i create pods --subresource=exec -n perf-platform --as "$identity" || :) == no ]]
echo 'Complete preflight passed twice against the actual disposable cluster UID; release composition remains unproven.'
source "$source_dir/maintenance-parent.sh"
