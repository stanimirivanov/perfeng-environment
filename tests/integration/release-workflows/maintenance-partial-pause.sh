#!/usr/bin/env bash
# Stop only an approved suspended CI copy; never repair or advance the baseline.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/maintenance-recovery.outcome.txt ]] || exit 2
[[ ${adoption_retention_baseline:-} == "$fixture/maintenance-recovered" ]] || exit 2
declare -F k a maintenance_snapshot root_state root_retained adoption_retained > /dev/null || exit 2
[[ -n ${root_uid:-} && -n ${adoption_root_case:-} && -n ${root_revision:-} ]] || exit 2

maintenance_partial_root() {
  jq -e '.items[]|select(.metadata.name=="perfeng-local-root")' "$1.applications.json" > "$1.root.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" converged \
    "$1.root.json" "$root_uid" "$adoption_root_case" "$root_revision" "$fixture/root-tree.yaml"
}

maintenance_partial_workers() {
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$1" -o json \
    | jq '[.items[]|{uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}] | sort_by(.nodeID)'
}

maintenance_partial_case() {
  local resource=$1 previous=$current_stage prefix workflow deadline state rc field
  prefix="maintenance-partial-$resource"
  [[ $resource == root || $resource == child ]] || return 2
  start_stage "$prefix"
  maintenance_snapshot "$fixture/$prefix-before"
  maintenance_partial_root "$fixture/$prefix-before"
  root_state "$fixture/$prefix-before"
  adoption_retained "$prefix-before"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_partial_pause.py" template "$resource" "$fixture" "$results" > "$fixture/$prefix.template.json"
  argo lint --offline --kinds=workflowtemplates gitops/release-workflows "$fixture/$prefix.template.json"
  k create -f "$fixture/$prefix.template.json" -o json > "$fixture/$prefix.installed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_partial_pause.py" installed "$resource" "$fixture" "$results"
  workflow=$(a submit --from "workflowtemplate/perfeng-maintenance-partial-$resource-ci" --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
  deadline=$((SECONDS + 240)); state=timeout
  while ((SECONDS < deadline)); do
    if ! k -n argo-workflows get workflow "$workflow" --request-timeout=10s -o json > "$fixture/$prefix.barrier.json" 2> "$fixture/$prefix.barrier.error.log"; then
      printf '%s\n' null > "$fixture/$prefix.barrier.json"; state=api-error; break
    fi
    if jq -e '.status.phase=="Running" and any(.status.nodes[]?; .displayName=="partial-pause-barrier" and .type=="Suspend" and .phase=="Running")' "$fixture/$prefix.barrier.json" > /dev/null; then state=ready; break; fi
    if ! jq -e '.status.phase==null or .status.phase=="" or .status.phase=="Pending" or .status.phase=="Running"' "$fixture/$prefix.barrier.json" > /dev/null; then state=terminal; break; fi
    sleep 2
  done
  printf '%s\n' "$state" > "$fixture/$prefix.state.txt"
  if ! k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" --request-timeout=10s -o json > "$fixture/$prefix.barrier-pods.json" 2> "$fixture/$prefix.barrier-pods.error.log"; then
    printf '%s\n' null > "$fixture/$prefix.barrier-pods.json"
  fi
  maintenance_partial_workers "$workflow" > "$fixture/$prefix.workers.json"
  cp "$fixture/$prefix.workers.json" "$fixture/$prefix.workers-before.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_partial_pause.py" barrier "$resource" "$fixture" "$results"
  # Exact generated name/UID and completed real writes were validated above.
  timeout 30s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows stop "$workflow" > "$fixture/$prefix.stop.log" 2>&1
  if timeout 90s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow" > "$fixture/$prefix.wait.log" 2>&1; then rc=0; else rc=$?; fi
  [[ $rc != 0 && $rc != 124 && $rc != 137 ]]
  a get "$workflow" -o json > "$fixture/$prefix.workflow.json"
  maintenance_partial_workers "$workflow" > "$fixture/$prefix.workers.json"
  maintenance_snapshot "$fixture/$prefix-after"
  maintenance_partial_root "$fixture/$prefix-after"
  root_state "$fixture/$prefix-after"
  root_retained "$fixture/$prefix-before" "$fixture/$prefix-after"
  for field in records secrets config git; do cmp "$fixture/$prefix-before.$field.json" "$fixture/$prefix-after.$field.json"; done
  adoption_retained "$prefix-after"
  k -n argo-workflows get "workflowtemplate/perfeng-maintenance-partial-$resource-ci" -o json > "$fixture/$prefix.installed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_partial_pause.py" installed "$resource" "$fixture" "$results"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_partial_pause.py" verify "$resource" "$fixture" "$results"
  complete_stage
  current_stage=$previous
}

for maintenance_partial_resource in root child; do maintenance_partial_case "$maintenance_partial_resource"; done
