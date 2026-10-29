#!/usr/bin/env bash
# Sourced only instead of the initial fixture's first CI-only successful migrator.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial && ${migration_label:-} == first && ${migration_key:-} == migration-url ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k a sql migration_snapshot role_snapshot > /dev/null || exit 2

schema_install_snapshot() {
  local label=$1
  k -n perf-platform get services,deployments,statefulsets,persistentvolumeclaims,configmaps,secrets -o json > "$fixture/schema-install.$label.objects.json"
  k -n perf-platform get pods --field-selector=status.phase=Running -o json > "$fixture/schema-install.$label.pods.json"
  k get persistentvolumes -o json > "$fixture/schema-install.$label.pvs.json"
  k -n argocd get applications perfeng-local-root perfeng-local-control-plane -o json > "$fixture/schema-install.$label.applications.json"
  k get namespace kube-system -o json > "$fixture/schema-install.$label.namespace.json"
  role_snapshot "$fixture/schema-install.$label.roles.json"
}

schema_install_sources() {
  k -n argo-workflows get workflowtemplates perfeng-release-schema-migration-v1 perfeng-schema-migration-job-v1 perfeng-release-preflight-v3 perfeng-release-task-selection-v3 perfeng-verify-release-quiescence-v3 -o json > "$fixture/schema-install.templates-$1.json"
  k -n argo-workflows get configmap perfeng-schema-migration-intent-v1 -o json > "$fixture/schema-install.intent-$1.json"
}

schema_install_retention() {
  schema_contention_retention
  schema_rollback_retention
  local name uid
  name=$(jq -er .metadata.name "$fixture/schema-install.workflow.json")
  uid=$(jq -er .metadata.uid "$fixture/schema-install.workflow.json")
  a get "$name" -o json > "$fixture/schema-install.retained-workflow.json"
  k -n perf-platform get job "perfeng-schema-$uid" -o json > "$fixture/schema-install.retained-job.json"
  k -n perf-platform get secret perfeng-control-plane-registry -o json > "$fixture/schema-install.retained-pull.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_install.py" retained "$fixture" "$results"
}

schema_native_parent() {
  local label=$1 prefix="schema-$1" name uid rc watch_pid watch_rc failed_pod
  [[ $label == install || $label == contention || $label == rollback ]] || return 2
  if [[ $label == install ]]; then start_stage "$prefix-parent"; fi
  timeout --kill-after=5s 1240s kubectl --kubeconfig "$KUBECONFIG" --context kind-perfeng-local -n perf-platform get pods -l batch.kubernetes.io/job-name --watch --output-watch-events -o json > "$fixture/$prefix.watch.json" 2> "$fixture/$prefix.watch.private" &
  watch_pid=$!
  if name=$(a submit --from workflowtemplate/perfeng-release-schema-migration-v1 --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name); then :; else
    kill "$watch_pid" 2> /dev/null || true
    wait "$watch_pid" || true
    return 1
  fi
  if timeout 1230s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$name" > "$fixture/$prefix.wait.private" 2>&1; then rc=0; else rc=$?; fi
  kill "$watch_pid" 2> /dev/null || true
  if wait "$watch_pid"; then watch_rc=0; else watch_rc=$?; fi
  printf '%s\n' "$rc" > "$fixture/$prefix.wait-code.txt"
  printf '%s\n' "$watch_rc" > "$fixture/$prefix.watch-code.txt"
  a get "$name" -o json > "$fixture/$prefix.workflow.json"
  uid=$(jq -er .metadata.uid "$fixture/$prefix.workflow.json")
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$name" -o json > "$fixture/$prefix.workflow-pods.json"
  jq '[.items[]|{uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' "$fixture/$prefix.workflow-pods.json" > "$fixture/$prefix.workers.json"
  k -n perf-platform get job "perfeng-schema-$uid" --ignore-not-found -o json | jq -s '.[0] // null' > "$fixture/$prefix.job.json"
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=perfeng-schema-$uid" -o json > "$fixture/$prefix.job-pods.json"
  if [[ $rc != 0 ]]; then
    while IFS= read -r failed_pod; do k -n argo-workflows logs "$failed_pod" --all-containers=true --tail=30 --limit-bytes=16384 > "$fixture/$prefix.$failed_pod.private" 2>&1 || true; done < <(jq -r '.items[]|select(any(((.status.containerStatuses // []) + (.status.initContainerStatuses // []))[]; (.state.terminated.exitCode // 0)>0))|.metadata.name' "$fixture/$prefix.workflow-pods.json" | head -n 10)
  fi
  if [[ $rc != 0 ]] && jq -e '.!=null' "$fixture/$prefix.job.json" > /dev/null; then
    k -n perf-platform logs "job/perfeng-schema-$uid" -c migrate --tail=20 --limit-bytes=8192 > "$fixture/$prefix.migrator.private" 2>&1 || true
  fi
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_install.py" "diagnose-$label" "$fixture" "$results"
  if [[ $label == install ]]; then [[ $rc == 0 ]]; else [[ $rc != 0 && $rc != 124 && $rc != 137 ]]; fi
}

schema_install_suite() {
  local previous=$current_stage name
  start_stage schema-install-setup
  [[ -z $(k -n perf-platform get secret perfeng-control-plane-registry --ignore-not-found -o name) ]]
  k -n perf-platform get secret migration-ci-registry -o json > "$fixture/schema-install.pull-source.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_install.py" pull "$fixture" "$results"
  k create -f "$fixture/schema-install.pull-create.json" -o json > "$fixture/schema-install.pull-before.json"
  rm -f "$fixture/schema-install.pull-create.json"
  k apply -f gitops/release-workflows/rbac/schema-migration
  k apply -f gitops/release-workflows-bootstrap/schema-migration-application-access.yaml
  for name in schema-migration-intent-v1.yaml schema-migration-job-v1.yaml release-schema-migration-v1.yaml; do k apply -f "gitops/release-workflows/$name"; done
  sql > "$fixture/schema-install.empty.json" <<'SQL'
SELECT json_build_object('schemaAbsent',to_regnamespace('perfeng_control') IS NULL,
 'applicationRelations',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname IN ('public','perfeng_control') AND c.relkind IN ('r','i','S')),
 'runtimeCreate',has_database_privilege('perfeng_runtime','perfeng','CREATE'),
 'migrationCreate',has_database_privilege('perfeng_migration','perfeng','CREATE'));
SQL
  schema_install_snapshot before
  schema_install_sources before
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/schema-install.jobs-before.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_install.py" before "$fixture" "$results"
  complete_stage
  source "$source_dir/schema-migration-contention.sh"
  source "$source_dir/schema-migration-rollback.sh"
  schema_native_parent install
  schema_install_snapshot after
  schema_install_sources after
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/schema-install.jobs-after.json"
  migration_snapshot "$results/migration-image-first.schema.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_install.py" verify "$fixture" "$results"
  # Preserve the old image-parity projection contract; raw parent evidence stays private.
  jq '{metadata:{name:.metadata.name,uid:.metadata.uid,creationTimestamp:.metadata.creationTimestamp},spec:{workflowTemplateRef:.spec.workflowTemplateRef},status:{phase:.status.phase}}' "$fixture/schema-install.workflow.json" > "$results/migration-image-first.workflow.json"
  jq '{metadata:{name:.metadata.name,namespace:.metadata.namespace,uid:.metadata.uid,creationTimestamp:.metadata.creationTimestamp},spec,status}' "$fixture/schema-install.job.json" > "$results/migration-image-first.job.json"
  jq '[.items[]|{uid:.metadata.uid,containers:[.status.containerStatuses[]?|{name,exitCode:.state.terminated.exitCode,waitingReason:.state.waiting.reason}]}]' "$fixture/schema-install.job-pods.json" > "$results/migration-image-first.worker.json"
  complete_stage
  current_stage=$previous
}

schema_install_suite
