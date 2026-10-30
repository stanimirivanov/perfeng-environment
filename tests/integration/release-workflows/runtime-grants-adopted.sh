#!/usr/bin/env bash
# Real adopted/paused acceptance; no additional recovery or workload write.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/schema-parent-complete.outcome.json ]] || exit 2
declare -F k a schema_parent_snapshot grant_sources runtime_acl_snapshot runtime_probe runtime_grant_parent schema_parent_ledger_access > /dev/null || exit 2

adopted_grant_snapshot() {
  local label=$1
  schema_parent_snapshot "$fixture/grant-adopted-$label"
  runtime_acl_snapshot "$fixture/grant-adopted-$label.acl.json"
  grant_sources "adopted-$label" adopted
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-runtime-grants -o json > "$fixture/grant-adopted-$label.jobs.json"
}

adopted_grant_retention() {
  local name
  name=$(jq -er .metadata.name "$fixture/grant-adopted-null.workflow.json")
  a get "$name" -o json > "$fixture/grant-adopted-null.current-workflow.json"
  name=$(jq -er .metadata.name "$fixture/grant-adopted.workflow.json")
  a get "$name" -o json > "$fixture/grant-adopted.current-workflow.json"
  name=$(jq -er .metadata.name "$fixture/grant-adopted.job.json")
  k -n perf-platform get job "$name" -o json > "$fixture/grant-adopted.current-job.json"
  name=$(jq -er .metadata.name "$results/runtime-grants-adopted.workflow.json")
  a get "$name" -o json > "$fixture/grant-adopted.probe-retained-workflow.json"
  name=$(jq -er .metadata.name "$results/runtime-grants-adopted.job.json")
  k -n perf-platform get job "$name" -o json > "$fixture/grant-adopted.probe-retained-job.json"
  grant_sources adopted-retained adopted
  schema_parent_ledger_access release-runtime-grants
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_adopted.py" retained "$fixture" "$results"
}

adopted_grant_suite() {
  local previous=$current_stage
  start_stage grant-adopted-v2-setup
  k apply -f gitops/release-workflows-bootstrap/runtime-grants-ledger-access.yaml
  k apply -f gitops/release-workflows/release-runtime-grants-v2.yaml
  schema_parent_ledger_access release-runtime-grants
  complete_stage
  source "$source_dir/schema-qualification-parent.sh"
  runtime_grant_parent adopted-null
  runtime_grant_parent adopted
  start_stage grant-adopted-runtime
  runtime_probe adopted success
  cp "$fixture/runtime-probe.log" "$fixture/grant-adopted.probe.private"
  adopted_grant_snapshot observed
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_adopted.py" complete "$fixture" "$results"
  adopted_grant_retention
  complete_stage
  current_stage=$previous
}

adopted_grant_suite
