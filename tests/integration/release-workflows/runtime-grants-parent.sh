#!/usr/bin/env bash
# CI only: unchanged owner parent on the paused, installed bootstrap baseline.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k a sql schema_install_snapshot runtime_data_snapshot runtime_acl_snapshot > /dev/null || exit 2

grant_sources() {
  k -n argo-workflows get workflowtemplates perfeng-release-runtime-grants-v1 perfeng-runtime-grants-job-v1 perfeng-release-schema-migration-v1 perfeng-schema-migration-job-v1 perfeng-release-preflight-v3 perfeng-release-task-selection-v3 perfeng-verify-release-quiescence-v3 -o json > "$fixture/grant-$1.templates.json"
  k -n perf-platform get configmap perfeng-runtime-grants-sql-v1 -o json > "$fixture/grant-$1.sql.json"
  k -n argo-workflows get configmap perfeng-schema-migration-intent-v1 -o json > "$fixture/grant-$1.intent.json"
}
grant_snapshot() {
  local label=$1
  schema_install_snapshot "grant-$label"
  grant_sources "$label"
  migration_snapshot "$fixture/grant-$label.schema.json"
  runtime_data_snapshot "$fixture/grant-$label.data.txt"
  runtime_acl_snapshot "$fixture/grant-$label.acl.json"
  sql -c "SELECT c.oid::text,s.value FROM public.migration_ci_sentinel s CROSS JOIN pg_class c WHERE s.id=1 AND c.oid='public.migration_ci_sentinel'::regclass;" > "$fixture/grant-$label.sentinel.txt"
}

runtime_grant_parent() {
  local label=$1 previous=$current_stage name uid rc
  [[ $label == first || $label == repeat ]] || return 2
  start_stage "grant-parent-$label"
  grant_snapshot "$label-before"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_parent.py" "before-$label" "$fixture" "$results"
  name=$(a submit --from workflowtemplate/perfeng-release-runtime-grants-v1 --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
  if timeout 1230s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$name" > "$fixture/grant-$label.wait.private" 2>&1; then rc=0; else rc=$?; fi
  a get "$name" -o json > "$fixture/grant-$label.workflow.json"
  uid=$(jq -er .metadata.uid "$fixture/grant-$label.workflow.json")
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$name" -o json > "$fixture/grant-$label.workflow-pods.json"
  jq '[.items[]|{uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' "$fixture/grant-$label.workflow-pods.json" > "$fixture/grant-$label.workers.json"
  k -n perf-platform get job "perfeng-runtime-grants-$uid" --ignore-not-found -o json | jq -s '.[0] // null' > "$fixture/grant-$label.job.json"
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=perfeng-runtime-grants-$uid" -o json > "$fixture/grant-$label.pods.json"
  if [[ $rc != 0 ]]; then
    printf '%s\n' '[RUNTIME-GRANT-PARENT] Parent did not succeed. Inspect private grant case evidence and stage reports; no retry, correction or activation was performed.' >&2
    return 1
  fi
  grant_snapshot "$label-after"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_parent.py" "verify-$label" "$fixture" "$results"
  complete_stage
  current_stage=$previous
}

runtime_grant_retention() {
  local label name
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-runtime-grants -o json > "$fixture/grant-retained.jobs.json"
  for label in first repeat; do
    name=$(jq -er .metadata.name "$fixture/grant-$label.workflow.json")
    a get "$name" -o json > "$fixture/grant-$label.retained-workflow.json"
  done
  grant_sources retained
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_parent.py" retained "$fixture" "$results"
}

k apply -f gitops/release-workflows/rbac/runtime-grants
k apply -f gitops/release-workflows-bootstrap/runtime-grants-application-access.yaml
for grant_file in runtime-grants-sql-v1.yaml runtime-grants-job-v1.yaml release-runtime-grants-v1.yaml; do
  k apply -f "gitops/release-workflows/$grant_file"
done
