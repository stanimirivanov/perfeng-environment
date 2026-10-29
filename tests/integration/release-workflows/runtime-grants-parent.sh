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
  k -n perf-platform get jobs -o json > "$fixture/grant-$label.jobs.json"
  sql -c "SELECT c.oid::text,s.value FROM public.migration_ci_sentinel s CROSS JOIN pg_class c WHERE s.id=1 AND c.oid='public.migration_ci_sentinel'::regclass;" > "$fixture/grant-$label.sentinel.txt"
}

runtime_grant_parent() {
  local label=$1 previous=$current_stage name uid rc
  [[ $label == denied || $label == contention || $label == first || $label == repeat ]] || return 2
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
  if [[ $label == denied ]]; then
    [[ $rc != 0 && $rc != 124 && $rc != 137 ]]
    local node pod
    node=$(jq -er '[.status.nodes[]|select(.displayName=="grant" and .type=="Pod" and (.phase=="Failed" or .phase=="Error"))]|if length==1 then .[0].id else error("expected one denied create") end' "$fixture/grant-denied.workflow.json")
    pod=$(jq -er --arg id "$node" '[.items[]|select(.metadata.annotations["workflows.argoproj.io/node-id"]==$id)]|if length==1 then .[0].metadata.name else error("expected one denied worker") end' "$fixture/grant-denied.workflow-pods.json")
    k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 --request-timeout=10s > "$fixture/grant-denied.log"
  elif [[ $label == contention ]]; then
    [[ $rc != 0 && $rc != 124 && $rc != 137 ]]
    k -n perf-platform logs "job/perfeng-runtime-grants-$uid" -c grant --tail=20 --limit-bytes=8192 > "$fixture/grant-contention.worker.private"
    local since
    since=$(jq -er .observedAt "$fixture/grant-contention.held.json")
    k -n perf-platform logs "$database_pod" -c postgres --since-time="$since" --timestamps=true --tail=200 --limit-bytes=65536 > "$fixture/grant-contention.postgres.private"
  elif [[ $rc != 0 ]]; then
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
  for label in denied contention first repeat; do
    name=$(jq -er .metadata.name "$fixture/grant-$label.workflow.json")
    a get "$name" -o json > "$fixture/grant-$label.retained-workflow.json"
  done
  grant_sources retained
  k -n perf-platform get role/release-runtime-grants -o json > "$fixture/grant-denied.role-final.json"
  runtime_grant_access false
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_denial.py" final "$fixture"
  runtime_grant_contention_retention
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_parent.py" retained "$fixture" "$results"
}

runtime_grant_access() {
  local denied=$1 verb expected answer rc ready deadline=$((SECONDS + 30))
  while ((SECONDS < deadline)); do
    ready=true
    for verb in create get watch; do
      expected=yes
      if [[ $denied == true && $verb == create ]]; then expected=no; fi
      if answer=$(k --as system:serviceaccount:argo-workflows:release-runtime-grants --as-group system:serviceaccounts --as-group system:serviceaccounts:argo-workflows --as-group system:authenticated auth can-i "$verb" jobs.batch -n perf-platform 2> "$fixture/grant-denied.auth.private"); then rc=0; else rc=$?; fi
      [[ ! -s $fixture/grant-denied.auth.private && ( ( $rc == 0 && $answer == yes ) || ( $rc == 1 && $answer == no ) ) ]] || return 1
      if [[ $answer != "$expected" ]]; then ready=false; fi
    done
    if [[ $ready == true ]]; then return 0; fi
    sleep 1
  done
  printf '%s\n' 'Runtime grant effective Job authority did not reach the expected boundary; no next operation attempted' >&2
  return 1
}

runtime_grant_denial() {
  local previous=$current_stage
  start_stage grant-denied-authority
  runtime_grant_access false
  k -n perf-platform get role/release-runtime-grants -o json > "$fixture/grant-denied.role-original.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_denial.py" prepare "$fixture"
  k -n perf-platform patch role/release-runtime-grants --type=json --patch-file "$fixture/grant-denied.narrow-patch.json" --request-timeout=10s -o json > "$fixture/grant-denied.role-narrowed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_denial.py" narrowed "$fixture"
  runtime_grant_access true
  complete_stage
  runtime_grant_parent denied
  # Exact refusal and all private state checks must pass before restoration.
  start_stage grant-denied-restoration
  runtime_grant_access true
  k -n perf-platform get role/release-runtime-grants -o json > "$fixture/grant-denied.role-current.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_denial.py" restore "$fixture"
  k -n perf-platform patch role/release-runtime-grants --type=json --patch-file "$fixture/grant-denied.restore-patch.json" --request-timeout=10s -o json > "$fixture/grant-denied.role-restored.json"
  runtime_grant_access false
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_denial.py" restored "$fixture"
  complete_stage
  current_stage=$previous
}

k apply -f gitops/release-workflows/rbac/runtime-grants
k apply -f gitops/release-workflows-bootstrap/runtime-grants-application-access.yaml
for grant_file in runtime-grants-sql-v1.yaml runtime-grants-job-v1.yaml release-runtime-grants-v1.yaml; do
  k apply -f "gitops/release-workflows/$grant_file"
done
source "$source_dir/runtime-grants-contention.sh"
