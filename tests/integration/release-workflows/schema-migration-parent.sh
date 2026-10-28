#!/usr/bin/env bash
# Current-schema repeat acceptance only; recovery stays behind all evidence gates.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/maintenance-pause.result.json ]] || exit 2
declare -F k a maintenance_snapshot root_state root_persistent_retained migration_snapshot role_snapshot sql > /dev/null || exit 2

schema_parent_snapshot() {
  local prefix=$1
  maintenance_snapshot "$prefix"
  jq -e '.items[]|select(.metadata.name=="perfeng-local-root")' "$prefix.applications.json" > "$prefix.root.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" converged "$prefix.root.json" "$root_uid" "$adoption_root_case" "$root_revision" "$fixture/root-tree.yaml"
  root_state "$prefix" "$fixture/maintenance-no-server.json"
  root_persistent_retained "$fixture/maintenance-paused" "$prefix"
  migration_snapshot "$prefix.schema.json"
  role_snapshot "$prefix.roles.json"
  [[ $(sql -c 'SELECT value FROM public.migration_ci_sentinel WHERE id=1;') == migration-image-parity-v1 ]]
  # Catalog identifiers are quoted by format; every generated command is SELECT.
  # Rows stay private, ordered by complete JSON value, including sentinel/sequence state.
  sql > "$prefix.data.txt" <<'SQL'
SELECT format('SELECT json_build_object(''relation'', %L, ''rows'', coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), ''[]''::jsonb)) FROM %I.%I AS t;', n.nspname||'.'||c.relname, n.nspname, c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE (n.nspname='perfeng_control' AND c.relkind IN ('r','S'))
   OR (n.nspname='public' AND c.relname='migration_ci_sentinel' AND c.relkind='r')
ORDER BY n.nspname,c.relname
\gexec
SQL
}

schema_parent_case() {
  local label=$1 prefix="schema-parent-$1" parameters="$fixture/upgrade-request-parameters.json" workflow uid rc watch_pid= watch_rc=0 failed_pod
  [[ $label == none || $label == first || $label == repeat ]] || return 2
  if [[ $label == none ]]; then parameters="$fixture/migration-none-parameters.json"; fi
  start_stage "$prefix"
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/$prefix.jobs-before.json"
  if [[ $label != none ]]; then
    timeout --kill-after=5s 1240s kubectl --kubeconfig "$KUBECONFIG" --context kind-perfeng-local -n perf-platform get pods -l batch.kubernetes.io/job-name --watch --output-watch-events -o json > "$fixture/$prefix.watch.json" 2> "$fixture/$prefix.watch.private" &
    watch_pid=$!
  fi
  workflow=$(a submit --from workflowtemplate/perfeng-release-schema-migration-v1 --parameter-file "$parameters" -o json | jq -er .metadata.name)
  if timeout 1230s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow" > "$fixture/$prefix.wait.private" 2>&1; then rc=0; else rc=$?; fi
  if [[ -n $watch_pid ]]; then
    kill "$watch_pid" 2> /dev/null || true
    if wait "$watch_pid"; then watch_rc=0; else watch_rc=$?; fi
  fi
  printf '%s\n' "$rc" > "$fixture/$prefix.wait-code.txt"
  printf '%s\n' "$watch_rc" > "$fixture/$prefix.watch-code.txt"
  a get "$workflow" -o json > "$fixture/$prefix.workflow.json"
  uid=$(jq -er .metadata.uid "$fixture/$prefix.workflow.json")
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" -o json > "$fixture/$prefix.workflow-pods.json"
  jq '[.items[]|{uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' "$fixture/$prefix.workflow-pods.json" > "$fixture/$prefix.workers.json"
  k -n perf-platform get job "perfeng-schema-$uid" --ignore-not-found -o json | jq -s '.[0] // null' > "$fixture/$prefix.job.json"
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=perfeng-schema-$uid" -o json > "$fixture/$prefix.job-pods.json"
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/$prefix.jobs-after.json"
  : > "$fixture/$prefix.failures.log"
  if [[ $rc != 0 ]]; then
    while IFS= read -r failed_pod; do
      k -n argo-workflows logs "$failed_pod" --all-containers=true --tail=30 --limit-bytes=16384 >> "$fixture/$prefix.failures.log" 2>&1 || true
    done < <(jq -r '.items[]|select(any(((.status.containerStatuses // []) + (.status.initContainerStatuses // []))[]; (.state.terminated.exitCode // 0)>0))|.metadata.name' "$fixture/$prefix.workflow-pods.json" | head -n 10)
    if jq -e '.!=null' "$fixture/$prefix.job.json" > /dev/null; then
      k -n perf-platform logs "job/perfeng-schema-$uid" -c migrate --tail=30 --limit-bytes=16384 >> "$fixture/$prefix.failures.log" 2>&1 || true
    fi
  fi
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_parent.py" "diagnose-$label" "$fixture" "$results"
  [[ $rc != 124 && $rc != 137 ]]
  if [[ $label == none ]]; then [[ $rc != 0 ]]; else [[ $rc == 0 ]]; fi
  schema_parent_snapshot "$fixture/$prefix"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_parent.py" "$label" "$fixture" "$results"
  complete_stage
}

schema_parent_suite() {
  local previous=$current_stage label name
  local -a schema_parent_workflows=()
  start_stage schema-parent-setup
  k apply -f gitops/release-workflows/rbac/schema-migration
  k apply -f gitops/release-workflows-bootstrap/schema-migration-application-access.yaml
  for name in schema-migration-intent-v1.yaml schema-migration-job-v1.yaml release-schema-migration-v1.yaml; do k apply -f "gitops/release-workflows/$name"; done
  schema_parent_snapshot "$fixture/schema-parent-baseline"
  schema_parent_sources before
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_parent.py" baseline "$fixture" "$results"
  complete_stage
  for label in none first repeat; do schema_parent_case "$label"; done
  start_stage schema-parent-retention
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/schema-parent.retained-jobs.json"
  for label in none first repeat; do
    name=$(jq -er .metadata.name "$fixture/schema-parent-$label.workflow.json")
    schema_parent_workflows+=("$name")
  done
  k -n argo-workflows get workflows "${schema_parent_workflows[@]}" -o json > "$fixture/schema-parent.retained-workflows.json"
  schema_parent_sources after
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_parent.py" complete "$fixture" "$results"
  complete_stage
  current_stage=$previous
}

schema_parent_sources() {
  local label=$1
  k -n argo-workflows get workflowtemplates perfeng-release-schema-migration-v1 perfeng-schema-migration-job-v1 perfeng-release-preflight-v3 perfeng-release-task-selection-v3 perfeng-verify-release-quiescence-v3 -o json > "$fixture/schema-parent.templates-$label.json"
  k -n argo-workflows get configmap perfeng-schema-migration-intent-v1 -o json > "$fixture/schema-parent.intent-$label.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_parent.py" "installed-$label" "$fixture" "$results"
}

schema_parent_suite
