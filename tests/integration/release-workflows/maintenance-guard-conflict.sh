#!/usr/bin/env bash
# Approved fixture metadata writes only; no automatic cleanup, repair or recovery.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/maintenance-recovery.outcome.txt ]] || exit 2
[[ ${adoption_retention_baseline:-} == "$fixture/maintenance-recovered" ]] || exit 2
declare -F k a maintenance_snapshot root_state root_retained adoption_retained > /dev/null || exit 2

maintenance_guard_case() {
  local resource=$1 previous=$current_stage prefix workflow deadline state rc node pod field namespace target
  prefix="maintenance-stale-$resource"
  case "$resource" in
    root) namespace=argocd; target=application/perfeng-local-root ;;
    child) namespace=argocd; target=application/perfeng-local-control-plane ;;
    deployment) namespace=perf-platform; target=deployment/control-plane ;;
    *) return 2 ;;
  esac
  start_stage "$prefix"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_guard_conflict.py" template "$resource" "$fixture" "$results" > "$fixture/$prefix.template.json"
  argo lint --offline --kinds=workflowtemplates gitops/release-workflows "$fixture/$prefix.template.json"
  k create -f "$fixture/$prefix.template.json" -o json > "$fixture/$prefix.installed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_guard_conflict.py" installed "$resource" "$fixture" "$results"
  workflow=$(a submit --from "workflowtemplate/perfeng-maintenance-stale-$resource-ci" --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
  deadline=$((SECONDS + 240)); state=timeout
  while ((SECONDS < deadline)); do
    if ! k -n argo-workflows get workflow "$workflow" --request-timeout=10s -o json > "$fixture/$prefix.barrier.json" 2> "$fixture/$prefix.barrier.error.log"; then
      printf '%s\n' null > "$fixture/$prefix.barrier.json"; state=api-error; break
    fi
    if jq -e '.status.phase=="Running" and any(.status.nodes[]?; .displayName=="stale-guard-barrier" and .type=="Suspend" and .phase=="Running")' "$fixture/$prefix.barrier.json" > /dev/null; then state=ready; break; fi
    if ! jq -e '.status.phase==null or .status.phase=="" or .status.phase=="Pending" or .status.phase=="Running"' "$fixture/$prefix.barrier.json" > /dev/null; then state=terminal; break; fi
    sleep 2
  done
  printf '%s\n' "$state" > "$fixture/$prefix.state.txt"
  if ! k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" --request-timeout=10s -o json > "$fixture/$prefix.barrier-pods.json" 2> "$fixture/$prefix.barrier-pods.error.log"; then
    printf '%s\n' null > "$fixture/$prefix.barrier-pods.json"
  fi
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_guard_conflict.py" barrier "$resource" "$fixture" "$results"
  maintenance_snapshot "$fixture/$prefix-before"
  root_state "$fixture/$prefix-before"
  k -n "$namespace" get "$target" --request-timeout=10s -o json > "$fixture/$prefix.target.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_guard_conflict.py" prepare "$resource" "$fixture" "$results"
  k -n "$namespace" patch "$target" --type=json --patch-file "$fixture/$prefix.add-patch.json" --request-timeout=10s -o json > "$fixture/$prefix.added.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_guard_conflict.py" remove "$resource" "$fixture" "$results"
  k -n "$namespace" patch "$target" --type=json --patch-file "$fixture/$prefix.remove-patch.json" --request-timeout=10s -o json > "$fixture/$prefix.restored.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_guard_conflict.py" resume "$resource" "$fixture" "$results"
  a resume "$workflow"
  if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow"; then rc=0; else rc=$?; fi
  a get "$workflow" -o json > "$fixture/$prefix.workflow.json"
  cp "$fixture/$prefix.workflow.json" "$results/$prefix.workflow.json"
  [[ $rc != 0 && $rc != 124 && $rc != 137 ]]
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" -o json \
    | jq '[.items[]|{name:.metadata.name,uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' > "$fixture/$prefix.workers.json"
  cp "$fixture/$prefix.workers.json" "$results/$prefix.workers.json"
  node=$(jq -er --arg step "pause-$resource" '.status.nodes[]|select(.displayName==$step and .type=="Pod" and (.phase=="Failed" or .phase=="Error"))|.id' "$fixture/$prefix.workflow.json")
  pod=$(jq -er --arg id "$node" '.[]|select(.nodeID==$id)|.name' "$fixture/$prefix.workers.json")
  k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 > "$fixture/$prefix.log"
  maintenance_snapshot "$fixture/$prefix-after"
  root_state "$fixture/$prefix-after"
  root_retained "$fixture/$prefix-before" "$fixture/$prefix-after"
  for field in records secrets config git; do cmp "$fixture/$prefix-before.$field.json" "$fixture/$prefix-after.$field.json"; done
  adoption_retained "$prefix"
  k -n argo-workflows get "workflowtemplate/perfeng-maintenance-stale-$resource-ci" -o json > "$fixture/$prefix.installed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_guard_conflict.py" installed "$resource" "$fixture" "$results"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_guard_conflict.py" verify "$resource" "$fixture" "$results"
  complete_stage
  current_stage=$previous
}

for maintenance_guard_resource in root child deployment; do maintenance_guard_case "$maintenance_guard_resource"; done
