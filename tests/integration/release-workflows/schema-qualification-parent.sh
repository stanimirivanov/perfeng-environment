#!/usr/bin/env bash
# Trusted-main initial only: qualify while paused; failure never starts recovery.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/schema-parent-complete.outcome.json ]] || exit 2
declare -F k a schema_parent_snapshot runtime_acl_snapshot schema_parent_ledger_access > /dev/null || exit 2

qualification_snapshot() {
  local label=$1
  schema_parent_snapshot "$fixture/qualification-$label"
  runtime_acl_snapshot "$fixture/qualification-$label.acl.json"
  k -n argo-workflows get workflowtemplates perfeng-release-schema-qualification-v1 perfeng-schema-qualification-job-v1 perfeng-release-schema-migration-v2 perfeng-release-schema-migration-v1 perfeng-release-preflight-v3 perfeng-release-task-selection-v3 perfeng-verify-release-quiescence-v3 -o json > "$fixture/qualification-$label.templates.json"
  k -n perf-platform get configmap perfeng-schema-qualification-sql-v1 -o json > "$fixture/qualification-$label.sql.json"
  k -n argo-workflows get configmap perfeng-schema-migration-intent-v1 -o json > "$fixture/qualification-$label.intent.json"
  k -n perfeng-release-ledger get role/release-runtime-grants rolebinding/release-runtime-grants -o json > "$fixture/qualification-$label.ledger.json"
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-schema-qualification -o json > "$fixture/qualification-$label.jobs.json"
}

qualification_workers() {
  local name=$1 output=$2
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$name" -o json \
    | jq '[.items[]|{uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' > "$output"
}

qualification_case() {
  local label=$1 name uid rc
  [[ $label == first || $label == repeat ]] || return 2
  start_stage "schema-qualification-$label"
  name=$(a submit --from workflowtemplate/perfeng-release-schema-qualification-v1 --parameter-file "$fixture/upgrade-request-parameters.json" -o json | jq -er .metadata.name)
  if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$name" > "$fixture/qualification-$label.wait.private" 2>&1; then rc=0; else rc=$?; fi
  a get "$name" -o json > "$fixture/qualification-$label.workflow.json"
  qualification_workers "$name" "$fixture/qualification-$label.workers.json"
  if [[ $rc != 0 ]]; then
    printf '%s\n' '[SCHEMA-QUALIFICATION-PARENT] Parent did not succeed; inspect private workflow/wait evidence and stage reports. No later qualification, grant or recovery attempted.' >&2
    return 1
  fi
  uid=$(jq -er .metadata.uid "$fixture/qualification-$label.workflow.json")
  k -n perf-platform get job "perfeng-schema-qualify-$uid" -o json > "$fixture/qualification-$label.job.json"
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=perfeng-schema-qualify-$uid" -o json > "$fixture/qualification-$label.job-pods.json"
  k -n perf-platform logs "job/perfeng-schema-qualify-$uid" -c qualify --tail=20 --limit-bytes=8192 --request-timeout=10s > "$fixture/qualification-$label.worker.private"
  qualification_snapshot "$label"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_qualification_parent.py" "$label" "$fixture" "$results"
  complete_stage
}

qualification_retention() {
  local label name uid phase=${1:-retained} cohort=${2:-standalone}
  [[ $phase == retained || $phase == final ]] || return 2
  [[ $cohort == standalone || $cohort == preparation ]] || return 2
  start_stage "schema-qualification-$phase"
  for label in first repeat; do
    name=$(jq -er .metadata.name "$fixture/qualification-$label.workflow.json")
    uid=$(jq -er .metadata.uid "$fixture/qualification-$label.workflow.json")
    a get "$name" -o json > "$fixture/qualification-$label.retained-workflow.json"
    qualification_workers "$name" "$fixture/qualification-$label.retained-workers.json"
    k -n perf-platform get job "perfeng-schema-qualify-$uid" -o json > "$fixture/qualification-$label.retained-job.json"
    k -n perf-platform get pods -l "batch.kubernetes.io/job-name=perfeng-schema-qualify-$uid" -o json > "$fixture/qualification-$label.retained-job-pods.json"
  done
  qualification_snapshot "$phase"
  if [[ $cohort == preparation || $phase == final ]]; then preparation_inventory qualify "$fixture/qualification-$phase.jobs.json"; fi
  schema_parent_ledger_access release-runtime-grants
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_qualification_parent.py" "$phase" "$fixture" "$results"
  complete_stage
}

qualification_suite() {
  local previous=$current_stage file label
  start_stage schema-qualification-setup
  for file in schema-qualification-sql-v1.yaml schema-qualification-job-v1.yaml release-schema-qualification-v1.yaml; do
    k apply -f "gitops/release-workflows/$file"
  done
  schema_parent_ledger_access release-runtime-grants
  qualification_snapshot before
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_qualification_parent.py" before "$fixture" "$results"
  complete_stage
  for label in first repeat; do qualification_case "$label"; done
  qualification_retention
  current_stage=$previous
}

qualification_suite
