#!/usr/bin/env bash
# Complete unchanged parent, then explicit test-only recovery in the owned initial leg.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $fixture/adoption-retained.records.json ]] || exit 2
declare -F k a root_state root_retained root_persistent_retained adoption_records argocd_case server_case > /dev/null || exit 2

# This is not an EXIT trap: failed/partial pauses never silently resume.
maintenance_stage=$current_stage
start_stage maintenance-pause
k apply -f gitops/release-workflows/maintenance-resources-v1.yaml
k apply -f gitops/release-workflows/release-maintenance-pause-v1.yaml
k apply -f gitops/release-workflows/rbac/maintenance-pause
k apply -f gitops/release-workflows-bootstrap/maintenance-pause-application-access.yaml
k apply -f gitops/release-workflows-bootstrap/maintenance-pause-ledger-access.yaml

maintenance_snapshot() {
  local prefix=$1
  k -n argocd get application perfeng-local-root perfeng-local-control-plane -o json > "$prefix.applications.json"
  k -n perf-platform get deployment control-plane -o json > "$prefix.deployment.json"
  k -n perf-platform get pods -l app.kubernetes.io/instance=control-plane,app.kubernetes.io/name=perfeng-control-plane -o json > "$prefix.server-pods.json"
  k get namespace kube-system -o json > "$prefix.namespace.json"
  adoption_records "$prefix.records.json"
  root_child_snapshot "$prefix.children.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" children \
    "$prefix.children.json" "$fixture/root-tree.yaml" "$fixture/root-children-before.json"
  server_inputs_snapshot "$prefix.secrets.json"
  server_config_snapshot "$prefix.config.json"
  k -n argo-workflows get secret release-git-read -o json | jq -S . > "$prefix.git.json"
}
maintenance_snapshot "$fixture/maintenance-before"
root_state "$fixture/maintenance-before"
root_retained "$fixture/adoption-before" "$fixture/maintenance-before"
cmp "$fixture/adoption-retained.records.json" "$fixture/maintenance-before.records.json"

maintenance_name=$(a submit --from workflowtemplate/perfeng-release-maintenance-pause-v1 \
  --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
  --namespace argo-workflows wait "$maintenance_name"; then maintenance_rc=0; else maintenance_rc=$?; fi
a get "$maintenance_name" -o json > "$results/maintenance-pause.workflow.json"
k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$maintenance_name" -o json \
  | jq '[.items[]|{uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],
    account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' \
  > "$results/maintenance-pause.workers.json"
[[ $maintenance_rc == 0 ]]
maintenance_snapshot "$fixture/maintenance-paused"
# An empty reference excludes only the server Pod from this stopped-state snapshot.
printf '%s\n' '[]' > "$fixture/maintenance-no-server.json"
root_state "$fixture/maintenance-paused" "$fixture/maintenance-no-server.json"
root_persistent_retained "$fixture/maintenance-before" "$fixture/maintenance-paused"
for field in records secrets config git; do
  cmp "$fixture/maintenance-before.$field.json" "$fixture/maintenance-paused.$field.json"
done
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_pause_evidence.py" paused "$fixture" "$results"
complete_stage

# Only a proved successful pause may reach this independently authorized CI recovery.
start_stage maintenance-recovery
argocd_case maintenance-recovery
server_case maintenance-recovered observe
maintenance_snapshot "$fixture/maintenance-recovered"
root_state "$fixture/maintenance-recovered" "$results/server-maintenance-recovered.pods.json"
root_persistent_retained "$fixture/maintenance-before" "$fixture/maintenance-recovered"
for field in records secrets config git; do
  cmp "$fixture/maintenance-before.$field.json" "$fixture/maintenance-recovered.$field.json"
done
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_pause_evidence.py" recovered "$fixture" "$results"
k -n argocd wait application/perfeng-local-root --for=jsonpath='{.status.sync.status}'=Synced --timeout=120s
k -n argocd wait application/perfeng-local-root --for=jsonpath='{.status.health.status}'=Healthy --timeout=120s
# Original evidence is immutable. Advance references only after all recovery checks.
root_server_pods_file="$results/server-maintenance-recovered.pods.json"
adoption_retention_baseline="$fixture/maintenance-recovered"
adoption_case maintenance-recovered success
cmp "$fixture/adoption-retained.records.json" "$fixture/adoption-maintenance-recovered.records.json"
cmp "$results/adoption-parent-$adoption_baseline.result.json" "$results/adoption-parent-maintenance-recovered.result.json"
printf '%s\n' 'Complete pause and manual recovery accepted; only the control-plane Pod was replaced' > "$results/maintenance-recovery.outcome.txt"
complete_stage
current_stage=$maintenance_stage

maintenance_receipt_refusal() {
  local label=$1 previous=$current_stage workflow rc node_id pod field
  [[ $mode == preflight-parent && $scenario == initial && -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || return 2
  [[ $label == missing-receipt || $label == replaced-receipt ]] || return 2
  [[ ${adoption_retention_baseline:-} == "$fixture/maintenance-recovered" && -f $results/maintenance-recovery.outcome.txt ]] || return 2
  start_stage "maintenance-$label"
  maintenance_snapshot "$fixture/maintenance-$label-before"
  root_state "$fixture/maintenance-$label-before"
  workflow=$(a submit --from workflowtemplate/perfeng-release-maintenance-pause-v1 \
    --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
  if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$workflow"; then rc=0; else rc=$?; fi
  a get "$workflow" -o json > "$results/maintenance-$label.workflow.json"
  [[ $rc != 0 && $rc != 124 && $rc != 137 ]]
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" -o json \
    | jq '[.items[]|{name:.metadata.name,uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],
      account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' \
    > "$results/maintenance-$label.workers.json"
  node_id=$(jq -er '.status.nodes[]|select(.displayName=="baseline" and .type=="Pod" and (.phase=="Failed" or .phase=="Error"))|.id' "$results/maintenance-$label.workflow.json")
  pod=$(jq -er --arg id "$node_id" '.[]|select(.nodeID==$id)|.name' "$results/maintenance-$label.workers.json")
  k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 > "$fixture/maintenance-$label.worker.log"
  maintenance_snapshot "$fixture/maintenance-$label-after"
  root_state "$fixture/maintenance-$label-after"
  root_retained "$fixture/maintenance-$label-before" "$fixture/maintenance-$label-after"
  for field in records secrets config git; do
    cmp "$fixture/maintenance-$label-before.$field.json" "$fixture/maintenance-$label-after.$field.json"
  done
  adoption_retained "maintenance-$label"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_pause_evidence.py" "$label" "$fixture" "$results"
  complete_stage
  current_stage=$previous
}
