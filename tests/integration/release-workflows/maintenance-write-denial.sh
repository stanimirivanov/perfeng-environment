#!/usr/bin/env bash
# Narrow one existing grant; only verified native refusal can restore captured rules.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/maintenance-recovery.outcome.txt ]] || exit 2
[[ ${adoption_retention_baseline:-} == "$fixture/maintenance-recovered" ]] || exit 2
[[ -n ${root_uid:-} && -n ${adoption_root_case:-} && -n ${root_revision:-} ]] || exit 2
declare -F k a maintenance_snapshot maintenance_authority_k root_state root_retained > /dev/null || exit 2

maintenance_write_snapshot() {
  maintenance_snapshot "$1"
  jq -e '.items[]|select(.metadata.name=="perfeng-local-root")' "$1.applications.json" > "$1.root.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" converged "$1.root.json" "$root_uid" "$adoption_root_case" "$root_revision" "$fixture/root-tree.yaml"
  root_state "$1"
}

maintenance_write_access() {
  local denied=$1 prefix=$2 target namespace object verb answer rc expected deadline=$((SECONDS + 30)) ready
  while ((SECONDS < deadline)); do
    ready=true
    for target in root child deployment; do
      case "$target" in
        root) namespace=argocd; object=applications.argoproj.io/perfeng-local-root ;;
        child) namespace=argocd; object=applications.argoproj.io/perfeng-local-control-plane ;;
        deployment) namespace=perf-platform; object=deployments.apps/control-plane ;;
      esac
      for verb in get patch; do
        expected=yes
        if [[ $target == "$denied" && $verb == patch ]]; then expected=no; fi
        if answer=$(maintenance_authority_k auth can-i "$verb" "$object" -n "$namespace" 2> "$fixture/$prefix.auth.private"); then rc=0; else rc=$?; fi
        [[ ! -s $fixture/$prefix.auth.private && ( ( $rc == 0 && $answer == yes ) || ( $rc == 1 && $answer == no ) ) ]] || return 1
        if [[ $answer != "$expected" ]]; then ready=false; fi
      done
    done
    if [[ $ready == true ]]; then return 0; fi
    sleep 1
  done
  printf '%s\n' 'Maintenance effective authority did not reach the exact expected boundary; no next operation attempted' >&2
  return 1
}

maintenance_write_case() {
  local resource=$1 previous=$current_stage prefix="maintenance-write-denied-$1" namespace workflow rc node pod
  [[ $(k get namespace kube-system --request-timeout=10s -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
  case "$resource" in root|child) namespace=argocd ;; deployment) namespace=perf-platform ;; *) return 2 ;; esac
  start_stage "$prefix"
  maintenance_write_access none "$prefix"
  maintenance_write_snapshot "$fixture/$prefix-before"
  root_retained "$adoption_retention_baseline" "$fixture/$prefix-before"
  k -n "$namespace" get role/release-maintenance-pause --request-timeout=10s -o json > "$fixture/$prefix.role-original.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_write_denial.py" prepare "$resource" "$fixture" "$results"
  k -n "$namespace" patch role/release-maintenance-pause --type=json --patch-file "$fixture/$prefix.narrow-patch.json" --request-timeout=10s -o json > "$fixture/$prefix.role-narrowed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_write_denial.py" narrowed "$resource" "$fixture" "$results"
  maintenance_write_access "$resource" "$prefix"
  workflow=$(a submit --from workflowtemplate/perfeng-release-maintenance-pause-v1 --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
  if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow" > "$fixture/$prefix.wait.private" 2>&1; then rc=0; else rc=$?; fi
  [[ $rc != 0 && $rc != 124 && $rc != 137 ]]
  a get "$workflow" -o json > "$fixture/$prefix.workflow.json"
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" --request-timeout=10s -o json \
    | jq '[.items[]|{name:.metadata.name,uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' > "$fixture/$prefix.workers.json"
  node=$(jq -er --arg step "pause-$resource" '[.status.nodes[]|select(.displayName==$step and .type=="Pod" and (.phase=="Failed" or .phase=="Error"))]|if length==1 then .[0].id else error("expected one denied patch") end' "$fixture/$prefix.workflow.json")
  pod=$(jq -er --arg id "$node" '[.[]|select(.nodeID==$id)]|if length==1 then .[0].name else error("expected one denied worker") end' "$fixture/$prefix.workers.json")
  k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 --request-timeout=10s > "$fixture/$prefix.log"
  maintenance_write_snapshot "$fixture/$prefix-after"
  root_retained "$fixture/$prefix-before" "$fixture/$prefix-after"
  maintenance_write_access "$resource" "$prefix"
  k -n "$namespace" get role/release-maintenance-pause --request-timeout=10s -o json > "$fixture/$prefix.role-current.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_write_denial.py" verify "$resource" "$fixture" "$results"
  # Never restore in an EXIT trap or retry a stale mutation.
  k -n "$namespace" patch role/release-maintenance-pause --type=json --patch-file "$fixture/$prefix.restore-patch.json" --request-timeout=10s -o json > "$fixture/$prefix.role-restored.json"
  maintenance_write_access none "$prefix"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_write_denial.py" restored "$resource" "$fixture" "$results"
  complete_stage
  current_stage=$previous
}

for maintenance_write_resource in root child deployment; do maintenance_write_case "$maintenance_write_resource"; done
