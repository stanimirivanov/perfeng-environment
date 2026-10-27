#!/usr/bin/env bash
# Exact Deployment PATCH commits once; fresh inspection/checkpoint precedes recovery.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
[[ ${adoption_retention_baseline:-} == "$fixture/maintenance-partial-deployment-recovered" && -f $results/maintenance-partial-deployment.recovered.json ]] || exit 2
declare -F k a maintenance_snapshot maintenance_partial_root maintenance_partial_workers maintenance_deployment_settled maintenance_deployment_pods root_state root_retained root_persistent_retained argocd_case server_case adoption_case > /dev/null || exit 2
maintenance_response_previous_stage=$current_stage
start_stage maintenance-response-prepare
maintenance_snapshot "$fixture/maintenance-response-before"
maintenance_partial_root "$fixture/maintenance-response-before"
root_state "$fixture/maintenance-response-before"
root_retained "$adoption_retention_baseline" "$fixture/maintenance-response-before"
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_patch_response.py" template "$fixture" "$results" > "$fixture/maintenance-response.template.json"
argo lint --offline --kinds=workflowtemplates gitops/release-workflows "$fixture/maintenance-response.template.json"
k create -f "$fixture/maintenance-response.template.json" -o json > "$fixture/maintenance-response.installed.json"
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_patch_response.py" installed "$fixture" "$results"
maintenance_response_workflow=$(a submit --from workflowtemplate/perfeng-maintenance-response-ci --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
maintenance_response_deadline=$((SECONDS + 240)); maintenance_response_state=timeout
while ((SECONDS < maintenance_response_deadline)); do
  if ! k -n argo-workflows get workflow "$maintenance_response_workflow" --request-timeout=10s -o json > "$fixture/maintenance-response.barrier.json" 2> "$fixture/maintenance-response.barrier.private"; then
    printf '%s\n' null > "$fixture/maintenance-response.barrier.json"; maintenance_response_state=api-error; break
  fi
  if jq -e '.status.phase=="Running" and any(.status.nodes[]?; .displayName=="maintenance-response-barrier" and .type=="Suspend" and .phase=="Running")' "$fixture/maintenance-response.barrier.json" > /dev/null; then maintenance_response_state=ready; break; fi
  if ! jq -e '.status.phase==null or .status.phase=="" or .status.phase=="Pending" or .status.phase=="Running"' "$fixture/maintenance-response.barrier.json" > /dev/null; then maintenance_response_state=terminal; break; fi
  sleep 2
done
printf '%s\n' "$maintenance_response_state" > "$fixture/maintenance-response.state.txt"
k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$maintenance_response_workflow" --request-timeout=10s -o json > "$fixture/maintenance-response.barrier-pods.json"
k -n perf-platform get deployment/control-plane --request-timeout=10s -o json > "$fixture/maintenance-response.target.json"
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_patch_response.py" prepare "$fixture" "$results"
k -n argo-workflows create configmap maintenance-response-proxy \
  --from-file="proxy.py=$source_dir/maintenance_patch_proxy.py" --from-file="ledger_response_proxy.py=$source_dir/ledger_response_proxy.py" \
  --from-file="expected.json=$fixture/maintenance-response.expected.json" --from-file="client.json=$fixture/maintenance-response.client.json" --from-file="ca.crt=$fixture/ledger-response.crt"
k -n argo-workflows create secret tls maintenance-response-proxy-tls --cert="$fixture/ledger-response.crt" --key="$fixture/ledger-response.key"
k create -f "$fixture/maintenance-response.proxy.yaml"
k -n argo-workflows wait pod/maintenance-response-proxy --for=condition=Ready --timeout=90s
a resume "$maintenance_response_workflow"
if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$maintenance_response_workflow" > "$fixture/maintenance-response.wait.private" 2>&1; then maintenance_response_rc=0; else maintenance_response_rc=$?; fi
[[ $maintenance_response_rc != 0 && $maintenance_response_rc != 124 && $maintenance_response_rc != 137 ]]
a get "$maintenance_response_workflow" -o json > "$fixture/maintenance-response.workflow.json"
maintenance_partial_workers "$maintenance_response_workflow" > "$fixture/maintenance-response.workers.json"
k -n argo-workflows logs maintenance-response-proxy -c proxy --request-timeout=10s > "$fixture/maintenance-response.proxy.log"
maintenance_deployment_settled maintenance-response
maintenance_snapshot "$fixture/maintenance-response-after"
maintenance_deployment_pods > "$fixture/maintenance-response-after.pod-list.json"
root_state "$fixture/maintenance-response-after" "$fixture/maintenance-no-server.json"
root_persistent_retained "$fixture/maintenance-response-before" "$fixture/maintenance-response-after"
k -n argo-workflows get workflowtemplate/perfeng-maintenance-response-ci -o json > "$fixture/maintenance-response.installed.json"
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_patch_response.py" installed "$fixture" "$results"
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_patch_response.py" verify "$fixture" "$results"
complete_stage

start_stage maintenance-response-checkpoint
maintenance_response_checkpoint=$(a submit --from workflowtemplate/perfeng-release-maintenance-checkpoint-v3 --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
if timeout 930s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$maintenance_response_checkpoint" > "$fixture/maintenance-response.checkpoint-wait.private" 2>&1; then maintenance_response_rc=0; else maintenance_response_rc=$?; fi
[[ $maintenance_response_rc == 0 ]]
a get "$maintenance_response_checkpoint" -o json > "$fixture/maintenance-response.checkpoint-workflow.json"
maintenance_partial_workers "$maintenance_response_checkpoint" > "$fixture/maintenance-response.checkpoint-workers.json"
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_patch_response.py" checkpoint "$fixture" "$results"
complete_stage

start_stage maintenance-response-recovery
argocd_case maintenance-response-recovery
server_case maintenance-response-recovered observe
k -n argocd wait application/perfeng-local-root --for=jsonpath='{.status.sync.status}'=Synced --timeout=120s
k -n argocd wait application/perfeng-local-root --for=jsonpath='{.status.health.status}'=Healthy --timeout=120s
maintenance_snapshot "$fixture/maintenance-response-recovered"
maintenance_partial_root "$fixture/maintenance-response-recovered"
root_state "$fixture/maintenance-response-recovered" "$results/server-maintenance-response-recovered.pods.json"
root_persistent_retained "$fixture/maintenance-response-before" "$fixture/maintenance-response-recovered"
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_patch_response.py" recovered "$fixture" "$results"
maintenance_response_adoption() {
  local root_server_pods_file="$results/server-maintenance-response-recovered.pods.json"
  local adoption_retention_baseline="$fixture/maintenance-response-recovered"
  adoption_case maintenance-response-recovered success
  cmp "$fixture/adoption-retained.records.json" "$fixture/adoption-maintenance-response-recovered.records.json"
  cmp "$results/adoption-parent-$adoption_baseline.result.json" "$results/adoption-parent-maintenance-response-recovered.result.json"
}
maintenance_response_adoption
root_server_pods_file="$results/server-maintenance-response-recovered.pods.json"
adoption_retention_baseline="$fixture/maintenance-response-recovered"
complete_stage
current_stage=$maintenance_response_previous_stage
