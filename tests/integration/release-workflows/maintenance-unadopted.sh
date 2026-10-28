#!/usr/bin/env bash
# Naturally absent records, not a destructive ledger fault or a pause operation.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ( ${scenario:-} == initial || ${scenario:-} == receipt-only ) ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k a root_state root_retained adoption_records > /dev/null || exit 2
source "$source_dir/maintenance-support.sh"

maintenance_unadopted_snapshot() {
  maintenance_snapshot "$1"
  jq -e '.items[]|select(.metadata.name=="perfeng-local-root")' "$1.applications.json" > "$1.root.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" converged \
    "$1.root.json" "$root_uid" "$adoption_root_case" "$root_revision" "$fixture/root-tree.yaml"
  root_state "$1"
  if [[ $scenario == initial ]]; then
    jq -e '.==[]' "$1.records.json" > /dev/null
  else
    jq -e 'length==1 and .[0].immutable==true' "$1.records.json" > /dev/null
    cmp "$fixture/adoption-receipt-only.records.json" "$1.records.json"
  fi
}

maintenance_unadopted_case() {
  local previous=$current_stage prefix=maintenance-unadopted workflow rc node pod
  if [[ $scenario == receipt-only ]]; then prefix=maintenance-receipt-only; fi
  start_stage "$prefix"
  maintenance_install
  maintenance_unadopted_snapshot "$fixture/$prefix-before"
  root_retained "$fixture/adoption-before" "$fixture/$prefix-before"
  workflow=$(a submit --from workflowtemplate/perfeng-release-maintenance-pause-v1 \
    --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
  if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow" > "$fixture/$prefix.wait.private" 2>&1; then rc=0; else rc=$?; fi
  [[ $rc != 0 && $rc != 124 && $rc != 137 ]]
  a get "$workflow" -o json > "$fixture/$prefix.workflow.json"
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" -o json \
    | jq '[.items[]|{name:.metadata.name,uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' > "$fixture/$prefix.workers.json"
  node=$(jq -er '.status.nodes[]|select(.displayName=="baseline" and .type=="Pod" and (.phase=="Failed" or .phase=="Error"))|.id' "$fixture/$prefix.workflow.json")
  pod=$(jq -er --arg id "$node" '.[]|select(.nodeID==$id)|.name' "$fixture/$prefix.workers.json")
  k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 > "$fixture/$prefix.worker.log"
  maintenance_unadopted_snapshot "$fixture/$prefix-after"
  root_retained "$fixture/$prefix-before" "$fixture/$prefix-after"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_unadopted.py" "$fixture" "$results" "$scenario"
  complete_stage
  current_stage=$previous
}

maintenance_unadopted_case
