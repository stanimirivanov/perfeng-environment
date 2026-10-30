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
  local label=$1 prefix="schema-parent-$1" parameters="$fixture/upgrade-request-parameters.json" workflow uid rc watch_pid= watch_rc=0 failed_pod node pod
  local template=perfeng-release-schema-migration-v1 wait_limit=1230s watch_limit=1240s
  [[ $label == none || $label == denied || $label == current-null || $label == first || $label == repeat ]] || return 2
  if [[ $label == none ]]; then parameters="$fixture/migration-none-parameters.json"; fi
  if [[ $label == current-null || $label == first || $label == repeat ]]; then
    template=perfeng-release-schema-migration-v2; wait_limit=1830s; watch_limit=1840s
  fi
  if [[ $label == current-null ]]; then parameters="$fixture/parent-parameters.json"; fi
  start_stage "$prefix"
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/$prefix.jobs-before.json"
  if [[ $label != none && $label != current-null ]]; then
    timeout --kill-after=5s "$watch_limit" kubectl --kubeconfig "$KUBECONFIG" --context kind-perfeng-local -n perf-platform get pods -l batch.kubernetes.io/job-name --watch --output-watch-events -o json > "$fixture/$prefix.watch.json" 2> "$fixture/$prefix.watch.private" &
    watch_pid=$!
  fi
  workflow=$(a submit --from "workflowtemplate/$template" --parameter-file "$parameters" -o json | jq -er .metadata.name)
  if timeout "$wait_limit" argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow" > "$fixture/$prefix.wait.private" 2>&1; then rc=0; else rc=$?; fi
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
  if [[ $label == none || $label == denied || $label == current-null ]]; then [[ $rc != 0 ]]; else [[ $rc == 0 ]]; fi
  if [[ $label == denied ]]; then
    node=$(jq -er '[.status.nodes[]|select(.displayName=="migrate" and .type=="Pod" and (.phase=="Failed" or .phase=="Error"))]|if length==1 then .[0].id else error("expected one denied create") end' "$fixture/$prefix.workflow.json")
    pod=$(jq -er --arg id "$node" '[.items[]|select(.metadata.annotations["workflows.argoproj.io/node-id"]==$id)]|if length==1 then .[0].metadata.name else error("expected one denied worker") end' "$fixture/$prefix.workflow-pods.json")
    k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 --request-timeout=10s > "$fixture/$prefix.denied.log"
  fi
  schema_parent_snapshot "$fixture/$prefix"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_parent.py" "$label" "$fixture" "$results"
  complete_stage
}

schema_parent_access() {
  local denied=$1 verb expected answer rc ready deadline=$((SECONDS + 30))
  while ((SECONDS < deadline)); do
    ready=true
    for verb in create get watch; do
      expected=yes
      if [[ $denied == true && $verb == create ]]; then expected=no; fi
      if answer=$(k --as system:serviceaccount:argo-workflows:release-schema-migration --as-group system:serviceaccounts --as-group system:serviceaccounts:argo-workflows --as-group system:authenticated auth can-i "$verb" jobs.batch -n perf-platform 2> "$fixture/schema-parent-denied.auth.private"); then rc=0; else rc=$?; fi
      [[ ! -s $fixture/schema-parent-denied.auth.private && ( ( $rc == 0 && $answer == yes ) || ( $rc == 1 && $answer == no ) ) ]] || return 1
      if [[ $answer != "$expected" ]]; then ready=false; fi
    done
    if [[ $ready == true ]]; then return 0; fi
    sleep 1
  done
  printf '%s\n' 'Schema migration effective Job authority did not reach the expected boundary; no next operation attempted' >&2
  return 1
}

schema_parent_ledger_access() {
  local digest name answer rc ready identity=${1:-release-schema-migration} deadline=$((SECONDS + 30))
  [[ $identity == release-schema-migration || $identity == release-runtime-grants ]] || return 2
  digest=$(jq -er '.["request-sha256"]|select(test("^sha256:[0-9a-f]{64}$"))' "$fixture/parent-parameters.json")
  while ((SECONDS < deadline)); do
    ready=true
    for name in perfeng-local-current "receipt.${digest:7:32}.${digest:39}"; do
      if answer=$(k --as "system:serviceaccount:argo-workflows:$identity" --as-group system:serviceaccounts --as-group system:serviceaccounts:argo-workflows --as-group system:authenticated auth can-i get "configmaps/$name" -n perfeng-release-ledger --request-timeout=10s 2> "$fixture/$identity.ledger-auth.private"); then rc=0; else rc=$?; fi
      if [[ -s $fixture/$identity.ledger-auth.private || ! ( ( $rc == 0 && $answer == yes ) || ( $rc == 1 && $answer == no ) ) ]]; then
        printf '%s\n' "Release ledger permission inspection failed for $identity; inspect the private fixture diagnostic; no next operation attempted" >&2
        return 1
      fi
      if [[ $answer != yes ]]; then ready=false; fi
    done
    if [[ $ready == true ]]; then return 0; fi
    sleep 1
  done
  printf '%s\n' "Release ledger get authority is not ready for $identity; verify the installed get Role/Binding; no next operation attempted" >&2
  return 1
}

schema_parent_denial() {
  start_stage schema-parent-denied-authority
  schema_parent_access false
  k -n perf-platform get role/release-schema-migration -o json > "$fixture/schema-parent-denied.role-original.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_denial.py" prepare "$fixture"
  k -n perf-platform patch role/release-schema-migration --type=json --patch-file "$fixture/schema-parent-denied.narrow-patch.json" --request-timeout=10s -o json > "$fixture/schema-parent-denied.role-narrowed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_denial.py" narrowed "$fixture"
  schema_parent_access true
  schema_parent_case denied
  # Refusal and all private retention checks above must pass before any restoration.
  start_stage schema-parent-denied-restoration
  schema_parent_access true
  k -n perf-platform get role/release-schema-migration -o json > "$fixture/schema-parent-denied.role-current.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_denial.py" restore "$fixture"
  k -n perf-platform patch role/release-schema-migration --type=json --patch-file "$fixture/schema-parent-denied.restore-patch.json" --request-timeout=10s -o json > "$fixture/schema-parent-denied.role-restored.json"
  schema_parent_access false
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_denial.py" restored "$fixture"
  complete_stage
}

schema_parent_suite() {
  local previous=$current_stage name
  start_stage schema-parent-setup
  k apply -f gitops/release-workflows/rbac/schema-migration
  k apply -f gitops/release-workflows-bootstrap/schema-migration-application-access.yaml
  k apply -f gitops/release-workflows-bootstrap/schema-migration-ledger-access.yaml
  for name in schema-migration-intent-v1.yaml schema-migration-job-v1.yaml release-schema-migration-v1.yaml release-schema-migration-v2.yaml; do k apply -f "gitops/release-workflows/$name"; done
  schema_parent_snapshot "$fixture/schema-parent-baseline"
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/schema-parent-baseline.jobs.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_install.py" baseline-retained "$fixture" "$results"
  schema_parent_sources before
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_parent.py" baseline "$fixture" "$results"
  complete_stage
  schema_parent_case none
  schema_parent_denial
  schema_parent_case current-null
  for label in first repeat; do schema_parent_case "$label"; done
  schema_parent_retention
  current_stage=$previous
}

schema_parent_retention() {
  local label name phase=${1:-complete}
  [[ $phase == complete || $phase == final ]] || return 2
  local -a schema_parent_workflows=()
  if [[ $phase == final ]]; then start_stage schema-parent-final-retention; else start_stage schema-parent-retention; fi
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/schema-parent.retained-jobs.json"
  if [[ $phase == final ]]; then preparation_inventory migrate "$fixture/schema-parent.retained-jobs.json"; fi
  for label in none denied current-null first repeat; do
    name=$(jq -er .metadata.name "$fixture/schema-parent-$label.workflow.json")
    schema_parent_workflows+=("$name")
  done
  k -n argo-workflows get workflows "${schema_parent_workflows[@]}" -o json > "$fixture/schema-parent.retained-workflows.json"
  schema_parent_sources after
  k -n perf-platform get role/release-schema-migration -o json > "$fixture/schema-parent-denied.role-final.json"
  schema_parent_access false
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_denial.py" final "$fixture"
  schema_install_retention
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_parent.py" "$phase" "$fixture" "$results"
  complete_stage
}

schema_parent_sources() {
  local label=$1
  k -n argo-workflows get workflowtemplates perfeng-release-schema-migration-v1 perfeng-release-schema-migration-v2 perfeng-schema-migration-job-v1 perfeng-release-preflight-v3 perfeng-release-task-selection-v3 perfeng-verify-release-quiescence-v3 -o json > "$fixture/schema-parent.templates-$label.json"
  k -n argo-workflows get configmap perfeng-schema-migration-intent-v1 -o json > "$fixture/schema-parent.intent-$label.json"
  k -n perfeng-release-ledger get role/release-schema-migration rolebinding/release-schema-migration -o json > "$fixture/schema-parent.ledger-$label.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_parent.py" "installed-$label" "$fixture" "$results"
  schema_parent_ledger_access
}

schema_parent_suite
