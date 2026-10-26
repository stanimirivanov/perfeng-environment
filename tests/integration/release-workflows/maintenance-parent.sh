#!/usr/bin/env bash
# Complete production composition; reuse the one run-owned request after preflight.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $fixture/parent-parameters.json ]] || exit 2
declare -F k a > /dev/null || exit 2

for application in gitops/bootstrap/local-root-application.yaml gitops/environments/local/control-plane-application.yaml; do
  uv run --locked --offline python -c 'import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))' "$application" \
    | jq '.spec.syncPolicy.automated = {"enabled": false}' | k create -f -
done
k -n perf-platform create serviceaccount control-plane
k create -f "$source_dir/deployment.yaml"
k -n perf-platform rollout status deployment/control-plane --timeout=60s
k -n argo-workflows get secret release-git-read -o json | jq -S . > "$fixture/maintenance-parent-secret-before.json"
jq '."request-sha256" = ("sha256:" + ("0" * 64))' "$fixture/parent-parameters.json" > "$fixture/maintenance-parent-wrong-digest.json"

maintenance_parent_snapshot() {
  k -n argocd get applications perfeng-local-root perfeng-local-control-plane -o json \
    | jq '[.items[] | {kind, namespace: .metadata.namespace, name: .metadata.name, uid: .metadata.uid, resourceVersion: .metadata.resourceVersion}]' \
    > "$fixture/maintenance-parent-applications.json"
  k -n perf-platform get deployment control-plane -o json \
    | jq '[{kind, namespace: .metadata.namespace, name: .metadata.name, uid: .metadata.uid, resourceVersion: .metadata.resourceVersion}]' \
    > "$fixture/maintenance-parent-deployment.json"
  jq -s '.[0] + .[1] | sort_by(.kind, .name)' "$fixture/maintenance-parent-applications.json" "$fixture/maintenance-parent-deployment.json" > "$1"
}

maintenance_parent_case() {
  maintenance_label=$1 maintenance_input=$2 maintenance_step=$3 maintenance_refusal=$4
  echo "Complete maintenance checkpoint: $maintenance_label"
  maintenance_parent_snapshot "$results/maintenance-parent-$maintenance_label.before.json"
  maintenance_name=$(a submit --from workflowtemplate/perfeng-release-maintenance-checkpoint-v3 \
    --parameter-file "$maintenance_input" -o json | jq -er .metadata.name)
  if timeout 930s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$maintenance_name"; then maintenance_rc=0; else maintenance_rc=$?; fi
  a get "$maintenance_name" -o json > "$results/maintenance-parent-$maintenance_label.workflow.json"
  if [[ $maintenance_step == success ]]; then
    [[ $maintenance_rc == 0 ]]
    jq -e '.status.phase == "Succeeded"' "$results/maintenance-parent-$maintenance_label.workflow.json" > /dev/null
    jq -er '.status.nodes[.metadata.name].outputs.parameters[] | select(.name == "result") | .value' \
      "$results/maintenance-parent-$maintenance_label.workflow.json" > "$results/maintenance-parent-$maintenance_label.result.json"
  else
    [[ $maintenance_rc != 0 && $maintenance_rc != 124 && $maintenance_rc != 137 ]]
    jq -e --arg step "$maintenance_step" -f "$source_dir/maintenance-parent-refusal.jq" \
      "$results/maintenance-parent-$maintenance_label.workflow.json" > /dev/null
    k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$maintenance_name" -o json \
      | jq -e '[.items[].status.containerStatuses[]? | select(.name == "main" and .state.terminated.exitCode == 2)] | length == 1' > /dev/null
    k -n argo-workflows logs -l "workflows.argoproj.io/workflow=$maintenance_name" \
      -c main --tail=20 --limit-bytes=8192 > "$fixture/maintenance-parent-worker.log"
    grep -Fx "$maintenance_refusal" "$fixture/maintenance-parent-worker.log" > "$results/maintenance-parent-$maintenance_label.refusal.txt"
  fi
  if [[ $maintenance_step != verify-identities ]]; then
    jq -er '.status.nodes[] | select(.displayName == "preflight" and .type == "Steps" and .phase == "Succeeded") |
      .outputs.parameters[] | select(.name == "result") | .value' \
      "$results/maintenance-parent-$maintenance_label.workflow.json" > "$results/maintenance-parent-$maintenance_label.preflight.json"
    cmp "$results/parent-first.result.json" "$results/maintenance-parent-$maintenance_label.preflight.json"
  fi
  maintenance_parent_snapshot "$results/maintenance-parent-$maintenance_label.after.json"
  cmp "$results/maintenance-parent-$maintenance_label.before.json" "$results/maintenance-parent-$maintenance_label.after.json"
  if [[ $maintenance_step == success ]]; then
    uv run --locked --offline python - "$fixture/parent-parameters.json" "$results/maintenance-parent-$maintenance_label.result.json" \
      "$results/maintenance-parent-$maintenance_label.after.json" "$cluster_uid" <<'PY'
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
parameters, result, observations = (json.loads(Path(p).read_text()) for p in sys.argv[1:4])
Draft202012Validator(json.loads(Path("deployments/maintenance-checkpoint-v1.schema.json").read_text())).validate(result)
for field, parameter in (("requestRevision", "request-revision"), ("requestDigest", "request-sha256"),
                         ("selectionRevision", "selection-revision")):
    assert result[field] == parameters[parameter]
assert result["clusterUID"] == sys.argv[4]
assert sorted(result["resources"], key=lambda value: (value["kind"], value["name"])) == observations
PY
  fi
}

maintenance_parent_case first "$fixture/parent-parameters.json" success ''
maintenance_parent_case repeat "$fixture/parent-parameters.json" success ''
maintenance_parent_case wrong-digest "$fixture/maintenance-parent-wrong-digest.json" verify-identities \
  'Preflight refused: request bytes differ from the submitted digest'
k -n argocd patch application perfeng-local-root --type=json \
  -p='[{"op":"replace","path":"/spec/syncPolicy/automated/enabled","value":true}]'
maintenance_parent_case unpaused "$fixture/parent-parameters.json" observe \
  'Quiescence refused: inspect reviewed inputs, ownership and observations; no success evidence'
k -n argocd patch application perfeng-local-root --type=json \
  -p='[{"op":"replace","path":"/spec/syncPolicy/automated/enabled","value":false}]'
k -n argocd delete rolebinding release-maintenance-checkpoint
identity=system:serviceaccount:argo-workflows:release-maintenance-checkpoint
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-root -n argocd --as "$identity" || :) == no ]]
maintenance_parent_case missing-read-grant "$fixture/parent-parameters.json" observe \
  'Quiescence refused: invalid selection, observations or operational response'
k apply -f gitops/release-workflows-bootstrap/maintenance-application-access.yaml
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-root -n argocd --as "$identity" || :) == yes ]]
maintenance_parent_case corrected "$fixture/parent-parameters.json" success ''

for label in first repeat wrong-digest unpaused missing-read-grant corrected; do
  name=$(jq -er .metadata.name "$results/maintenance-parent-$label.workflow.json")
  uid=$(jq -er .metadata.uid "$results/maintenance-parent-$label.workflow.json")
  [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
  jq '[.[] | del(.resourceVersion)]' "$results/maintenance-parent-$label.after.json" > "$fixture/maintenance-parent-$label.identities.json"
  cmp "$fixture/maintenance-parent-first.identities.json" "$fixture/maintenance-parent-$label.identities.json"
done
jq -s -e 'length == 6 and (map(.metadata.uid) | unique | length) == 6 and
  (map(.metadata.name) | unique | length) == 6' "$results"/maintenance-parent-*.workflow.json > /dev/null
k -n argo-workflows get secret release-git-read -o json | jq -S . > "$fixture/maintenance-parent-secret-after.json"
cmp "$fixture/maintenance-parent-secret-before.json" "$fixture/maintenance-parent-secret-after.json"
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
for permission in 'patch deployments perf-platform' 'delete pods perf-platform' 'create jobs perf-platform' \
  'patch applications.argoproj.io argocd' 'list secrets argo-workflows' 'patch configmaps perfeng-release-ledger'; do
  read -r verb resource namespace <<< "$permission"
  [[ $(k auth can-i "$verb" "$resource" -n "$namespace" --as "$identity" || :) == no ]]
done
[[ $(k auth can-i create pods --subresource=exec -n perf-platform --as "$identity" || :) == no ]]
echo 'Complete maintenance parent composition passed; fixture Applications are not operational release acceptance.'
