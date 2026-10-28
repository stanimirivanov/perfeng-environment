#!/usr/bin/env bash
# Real child policy transition only; never enable root or recover a failed case.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/maintenance-recovery.outcome.txt ]] || exit 2
[[ ${adoption_retention_baseline:-} == "$fixture/maintenance-recovered" ]] || exit 2
declare -F k a maintenance_write_snapshot maintenance_partial_workers root_retained adoption_case > /dev/null || exit 2

maintenance_child_transition_case() {
  local previous=$current_stage prefix=maintenance-child-transition workflow
  start_stage "$prefix"
  maintenance_write_snapshot "$fixture/$prefix-before"
  root_retained "$adoption_retention_baseline" "$fixture/$prefix-before"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_child_transition.py" template "$fixture" "$results" > "$fixture/$prefix.template.json"
  argo lint --offline --kinds=workflowtemplates gitops/release-workflows "$fixture/$prefix.template.json"
  k create -f "$fixture/$prefix.template.json" -o json > "$fixture/$prefix.installed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_child_transition.py" installed "$fixture" "$results"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_child_transition.py" prepare "$fixture" "$results"
  k -n argocd patch application perfeng-local-control-plane --type=json \
    --patch-file "$fixture/$prefix.enable.json" --request-timeout=10s -o json > "$fixture/$prefix.enable-response.json"
  # No write retry: an ambiguous enable response stops for disposable teardown.
  k -n argocd get application perfeng-local-control-plane --request-timeout=10s -o json > "$fixture/$prefix.enabled.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_child_transition.py" enabled "$fixture" "$results"
  workflow=$(a submit --from workflowtemplate/perfeng-maintenance-child-transition-ci \
    --parameter-file "$fixture/$prefix.parameters.json" -o json | jq -er .metadata.name)
  timeout 210s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow" > "$fixture/$prefix.wait.private" 2>&1
  a get "$workflow" -o json > "$fixture/$prefix.workflow.json"
  maintenance_partial_workers "$workflow" > "$fixture/$prefix.workers.json"
  # Root may compare during enablement. Wait only after the native disable succeeded.
  k -n argocd wait application/perfeng-local-root --for=jsonpath='{.status.sync.status}'=Synced --timeout=120s
  maintenance_write_snapshot "$fixture/$prefix-after"
  root_retained "$fixture/$prefix-before" "$fixture/$prefix-after"
  k -n argo-workflows get workflowtemplate/perfeng-maintenance-child-transition-ci -o json > "$fixture/$prefix.installed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_child_transition.py" installed "$fixture" "$results"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_child_transition.py" verify "$fixture" "$results"
  adoption_case "$prefix-checked" success
  cmp "$fixture/adoption-retained.records.json" "$fixture/adoption-$prefix-checked.records.json"
  cmp "$results/adoption-parent-$adoption_baseline.result.json" "$results/adoption-parent-$prefix-checked.result.json"
  complete_stage
  current_stage=$previous
}

maintenance_child_transition_case
