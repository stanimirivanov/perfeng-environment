#!/usr/bin/env bash
# No-op Application PATCH response loss; preserve the adopted running baseline.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/maintenance-recovery.outcome.txt ]] || exit 2
[[ ${adoption_retention_baseline:-} == "$fixture/maintenance-recovered" ]] || exit 2
declare -F k a maintenance_write_snapshot maintenance_partial_workers root_retained adoption_case > /dev/null || exit 2

maintenance_application_response_case() {
  local resource=$1 previous=$current_stage prefix="maintenance-$1-response" proxy="maintenance-$1-response-proxy" name workflow deadline state rc
  case "$resource" in root) name=perfeng-local-root ;; child) name=perfeng-local-control-plane ;; *) return 2 ;; esac
  start_stage "$prefix"
  maintenance_write_snapshot "$fixture/$prefix-before"
  root_retained "$adoption_retention_baseline" "$fixture/$prefix-before"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_application_response.py" template "$resource" "$fixture" "$results" > "$fixture/$prefix.template.json"
  argo lint --offline --kinds=workflowtemplates gitops/release-workflows "$fixture/$prefix.template.json"
  k create -f "$fixture/$prefix.template.json" -o json > "$fixture/$prefix.installed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_application_response.py" installed "$resource" "$fixture" "$results"
  workflow=$(a submit --from "workflowtemplate/perfeng-maintenance-$resource-response-ci" --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
  deadline=$((SECONDS + 240)); state=timeout
  while ((SECONDS < deadline)); do
    if ! k -n argo-workflows get workflow "$workflow" --request-timeout=10s -o json > "$fixture/$prefix.barrier.json" 2> "$fixture/$prefix.barrier.private"; then
      printf '%s\n' null > "$fixture/$prefix.barrier.json"; state=api-error; break
    fi
    if jq -e '.status.phase=="Running" and any(.status.nodes[]?; .displayName=="maintenance-response-barrier" and .type=="Suspend" and .phase=="Running")' "$fixture/$prefix.barrier.json" > /dev/null; then state=ready; break; fi
    if ! jq -e '.status.phase==null or .status.phase=="" or .status.phase=="Pending" or .status.phase=="Running"' "$fixture/$prefix.barrier.json" > /dev/null; then state=terminal; break; fi
    sleep 2
  done
  printf '%s\n' "$state" > "$fixture/$prefix.state.txt"
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" --request-timeout=10s -o json > "$fixture/$prefix.barrier-pods.json"
  k -n argocd get "application/$name" --request-timeout=10s -o json > "$fixture/$prefix.target.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_application_response.py" prepare "$resource" "$fixture" "$results"
  k -n argo-workflows create configmap "$proxy" \
    --from-file="proxy.py=$source_dir/maintenance_patch_proxy.py" --from-file="ledger_response_proxy.py=$source_dir/ledger_response_proxy.py" \
    --from-file="expected.json=$fixture/$prefix.expected.json" --from-file="client.json=$fixture/$prefix.client.json" --from-file="ca.crt=$fixture/$prefix-tls/ledger-response.crt"
  k -n argo-workflows create secret tls "$proxy-tls" --cert="$fixture/$prefix-tls/ledger-response.crt" --key="$fixture/$prefix-tls/ledger-response.key"
  k create -f "$fixture/$prefix.proxy.yaml"
  k -n argo-workflows wait "pod/$proxy" --for=condition=Ready --timeout=90s
  a resume "$workflow"
  if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow" > "$fixture/$prefix.wait.private" 2>&1; then rc=0; else rc=$?; fi
  [[ $rc != 0 && $rc != 124 && $rc != 137 ]]
  a get "$workflow" -o json > "$fixture/$prefix.workflow.json"
  maintenance_partial_workers "$workflow" > "$fixture/$prefix.workers.json"
  k -n argo-workflows logs "$proxy" -c proxy --request-timeout=10s > "$fixture/$prefix.proxy.log"
  maintenance_write_snapshot "$fixture/$prefix-after"
  root_retained "$fixture/$prefix-before" "$fixture/$prefix-after"
  k -n argo-workflows get "workflowtemplate/perfeng-maintenance-$resource-response-ci" -o json > "$fixture/$prefix.installed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_application_response.py" installed "$resource" "$fixture" "$results"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_application_response.py" verify "$resource" "$fixture" "$results"
  # Fresh published adoption, not a resumed maintenance parent or workload recovery.
  adoption_case "$prefix-checked" success
  cmp "$fixture/adoption-retained.records.json" "$fixture/adoption-$prefix-checked.records.json"
  cmp "$results/adoption-parent-$adoption_baseline.result.json" "$results/adoption-parent-$prefix-checked.result.json"
  complete_stage
  current_stage=$previous
}

for maintenance_application_resource in root child; do maintenance_application_response_case "$maintenance_application_resource"; done
