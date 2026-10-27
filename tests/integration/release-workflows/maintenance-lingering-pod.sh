#!/usr/bin/env bash
# One gated matching Pod blocks the unchanged checkpoint before guarded cleanup.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/maintenance-partial-deployment.outcome.json ]] || exit 2
declare -F k a maintenance_snapshot maintenance_deployment_pods maintenance_partial_workers root_state root_persistent_retained maintenance_deployment_settled > /dev/null || exit 2

start_stage maintenance-lingering-pod
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_lingering_pod.py" manifest "$fixture" "$results" > "$fixture/maintenance-lingering.manifest.json"
k create -f "$fixture/maintenance-lingering.manifest.json" --request-timeout=10s -o json > "$fixture/maintenance-lingering.created.json"

maintenance_lingering_snapshot() {
  local prefix="$fixture/maintenance-lingering-$1"
  maintenance_snapshot "$prefix"
  maintenance_deployment_pods > "$prefix.pod-list.json"
  root_state "$prefix" "$fixture/maintenance-no-server.json"
  root_persistent_retained "$fixture/maintenance-partial-deployment-before" "$prefix"
}
maintenance_lingering_snapshot before
lingering_checkpoint=$(a submit --from workflowtemplate/perfeng-release-maintenance-checkpoint-v3 --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
if timeout 930s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$lingering_checkpoint" > "$fixture/maintenance-lingering.wait.log" 2>&1; then lingering_rc=0; else lingering_rc=$?; fi
[[ $lingering_rc != 0 && $lingering_rc != 124 && $lingering_rc != 137 ]]
a get "$lingering_checkpoint" -o json > "$fixture/maintenance-lingering.workflow.json"
maintenance_partial_workers "$lingering_checkpoint" > "$fixture/maintenance-lingering.workers.json"
lingering_worker=$(jq -er '[.status.nodes[]|select(.displayName=="observe" and .type=="Pod")]|if length==1 then .[0].id else error("expected one observer") end' "$fixture/maintenance-lingering.workflow.json")
lingering_pod=$(k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$lingering_checkpoint" --request-timeout=10s -o json | jq -er --arg node "$lingering_worker" '[.items[]|select(.metadata.annotations["workflows.argoproj.io/node-id"]==$node)]|if length==1 then .[0].metadata.name else error("expected one worker") end')
k -n argo-workflows logs "$lingering_pod" -c main --request-timeout=10s > "$fixture/maintenance-lingering.log" 2>&1
maintenance_lingering_snapshot after
PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_lingering_pod.py" refusal "$fixture" "$results"
# No EXIT repair: a failed assertion or stale DELETE stops before checkpoint/recovery.
k delete --raw /api/v1/namespaces/perf-platform/pods/perfeng-ci-maintenance-lingering -f "$fixture/maintenance-lingering.delete.json" --request-timeout=10s > "$fixture/maintenance-lingering.deleted.json"
k -n perf-platform wait --for=delete pod/perfeng-ci-maintenance-lingering --timeout=60s
maintenance_deployment_settled maintenance-partial-deployment
complete_stage
