#!/usr/bin/env bash
# Approved Deployment interruption, independent checkpoint, then explicit CI recovery.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/maintenance-recovery.outcome.txt ]] || exit 2
[[ ${adoption_retention_baseline:-} == "$fixture/maintenance-recovered" ]] || exit 2
declare -F k a maintenance_partial_case maintenance_snapshot maintenance_partial_workers maintenance_partial_root root_state root_persistent_retained argocd_case server_case adoption_case > /dev/null || exit 2

maintenance_deployment_pods() {
  k get --raw '/api/v1/namespaces/perf-platform/pods?labelSelector=app.kubernetes.io%2Finstance%3Dcontrol-plane%2Capp.kubernetes.io%2Fname%3Dperfeng-control-plane' --request-timeout=10s
}

maintenance_deployment_settled() {
  local prefix=$1 deadline=$((SECONDS + 120)) uid
  uid=$(jq -er '.metadata.uid' "$fixture/$prefix-before.deployment.json")
  while ((SECONDS < deadline)); do
    k -n perf-platform get deployment/control-plane --request-timeout=10s -o json > "$fixture/$prefix.settling-deployment.json"
    maintenance_deployment_pods > "$fixture/$prefix.settling-pods.json"
    jq -e --arg uid "$uid" '.apiVersion=="apps/v1" and .kind=="Deployment" and .metadata.uid==$uid and .metadata.deletionTimestamp==null and .spec.replicas==0' "$fixture/$prefix.settling-deployment.json" > /dev/null
    jq -e '.apiVersion=="v1" and .kind=="PodList" and (.items|type)=="array" and (.metadata.resourceVersion|type)=="string" and .metadata.resourceVersion!="" and (.metadata.continue==null or .metadata.continue=="")' "$fixture/$prefix.settling-pods.json" > /dev/null
    if jq -e '.status.observedGeneration==.metadata.generation and ([.status.replicas//0,.status.readyReplicas//0,.status.updatedReplicas//0,.status.availableReplicas//0]|all(.==0))' "$fixture/$prefix.settling-deployment.json" > /dev/null \
      && jq -e '.items==[]' "$fixture/$prefix.settling-pods.json" > /dev/null; then return 0; fi
    sleep 2
  done
  printf '%s\n' 'Interrupted Deployment did not settle; no checkpoint or recovery attempted' >&2
  return 1
}

deployment_previous_stage=$current_stage
maintenance_partial_case deployment
start_stage maintenance-partial-deployment-checkpoint
deployment_checkpoint=$(a submit --from workflowtemplate/perfeng-release-maintenance-checkpoint-v3 --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
if timeout 930s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$deployment_checkpoint" > "$fixture/maintenance-partial-deployment.checkpoint-wait.log" 2>&1; then deployment_checkpoint_rc=0; else deployment_checkpoint_rc=$?; fi
[[ $deployment_checkpoint_rc == 0 ]]
a get "$deployment_checkpoint" -o json > "$fixture/maintenance-partial-deployment.checkpoint-workflow.json"
maintenance_partial_workers "$deployment_checkpoint" > "$fixture/maintenance-partial-deployment.checkpoint-workers.json"
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_partial_pause.py" checkpoint deployment "$fixture" "$results"
complete_stage

# Only verified Stop, paused retention and an independent checkpoint reach this sync.
start_stage maintenance-partial-deployment-recovery
argocd_case maintenance-partial-deployment-recovery
server_case maintenance-partial-deployment-recovered observe
k -n argocd wait application/perfeng-local-root --for=jsonpath='{.status.sync.status}'=Synced --timeout=120s
k -n argocd wait application/perfeng-local-root --for=jsonpath='{.status.health.status}'=Healthy --timeout=120s
maintenance_snapshot "$fixture/maintenance-partial-deployment-recovered"
maintenance_partial_root "$fixture/maintenance-partial-deployment-recovered"
root_state "$fixture/maintenance-partial-deployment-recovered" "$results/server-maintenance-partial-deployment-recovered.pods.json"
root_persistent_retained "$fixture/maintenance-partial-deployment-before" "$fixture/maintenance-partial-deployment-recovered"
for deployment_field in records secrets config git; do
  cmp "$fixture/maintenance-partial-deployment-before.$deployment_field.json" "$fixture/maintenance-partial-deployment-recovered.$deployment_field.json"
done
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_partial_pause.py" recovered deployment "$fixture" "$results"
# Bash locals let the adoption verifier inspect the candidate baseline without
# advancing the caller's references before the record/result comparisons pass.
maintenance_deployment_adoption() {
  local root_server_pods_file="$results/server-maintenance-partial-deployment-recovered.pods.json"
  local adoption_retention_baseline="$fixture/maintenance-partial-deployment-recovered"
  adoption_case maintenance-partial-deployment-recovered success
  cmp "$fixture/adoption-retained.records.json" "$fixture/adoption-maintenance-partial-deployment-recovered.records.json"
  cmp "$results/adoption-parent-$adoption_baseline.result.json" "$results/adoption-parent-maintenance-partial-deployment-recovered.result.json"
}
maintenance_deployment_adoption
root_server_pods_file="$results/server-maintenance-partial-deployment-recovered.pods.json"
adoption_retention_baseline="$fixture/maintenance-partial-deployment-recovered"
complete_stage
current_stage=$deployment_previous_stage
