#!/usr/bin/env bash
# Complete unchanged composition only on the already adopted, paused initial leg.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $fixture/grant-adopted.verified.json && -f $results/schema-qualification-retained.outcome.json ]] || exit 2
declare -F k a schema_parent_snapshot runtime_acl_snapshot qualification_workers schema_parent_ledger_access runtime_grant_access > /dev/null || exit 2

preparation_snapshot() {
  local label=$1
  schema_parent_snapshot "$fixture/preparation-$label"
  runtime_acl_snapshot "$fixture/preparation-$label.acl.json"
  k -n argo-workflows get workflowtemplates perfeng-release-database-prepare-v1 perfeng-release-schema-migration-v2 perfeng-release-schema-qualification-v1 perfeng-release-runtime-grants-v2 perfeng-release-schema-migration-v1 perfeng-schema-migration-job-v1 perfeng-schema-qualification-job-v1 perfeng-runtime-grants-job-v1 perfeng-release-preflight-v3 perfeng-release-task-selection-v3 perfeng-verify-release-quiescence-v3 -o json > "$fixture/preparation-$label.templates.json"
  k -n argo-workflows get configmaps perfeng-schema-migration-intent-v1 perfeng-release-verifiers-v3 -o json > "$fixture/preparation-$label.config-argo.json"
  k -n perf-platform get configmaps perfeng-schema-qualification-sql-v1 perfeng-runtime-grants-sql-v1 -o json > "$fixture/preparation-$label.config-platform.json"
  jq -s '{items: [.[].items[]]}' "$fixture/preparation-$label.templates.json" "$fixture/preparation-$label.config-argo.json" "$fixture/preparation-$label.config-platform.json" > "$fixture/preparation-$label.sources.json"
  k -n argo-workflows get serviceaccount/release-runtime-grants role/release-runtime-grants rolebinding/release-runtime-grants -o json > "$fixture/preparation-$label.auth-argo.json"
  k -n perf-platform get role/release-runtime-grants rolebinding/release-runtime-grants -o json > "$fixture/preparation-$label.auth-platform.json"
  k -n argocd get role/release-runtime-grants rolebinding/release-runtime-grants -o json > "$fixture/preparation-$label.auth-applications.json"
  k -n perfeng-release-ledger get role/release-runtime-grants rolebinding/release-runtime-grants -o json > "$fixture/preparation-$label.auth-ledger.json"
  k get clusterrole/perfeng-release-runtime-grants-kube-system clusterrolebinding/perfeng-release-runtime-grants-kube-system -o json > "$fixture/preparation-$label.auth-cluster.json"
  jq -s '{items: [.[].items[]]}' "$fixture/preparation-$label.auth-argo.json" "$fixture/preparation-$label.auth-platform.json" "$fixture/preparation-$label.auth-applications.json" "$fixture/preparation-$label.auth-ledger.json" "$fixture/preparation-$label.auth-cluster.json" > "$fixture/preparation-$label.authority.json"
  k -n perf-platform get jobs -l 'app.kubernetes.io/name in (perfeng-control-plane-migration,perfeng-schema-qualification,perfeng-runtime-grants)' -o json > "$fixture/preparation-$label.jobs.json"
}

preparation_jobs() {
  local label=$1 suffix=$2 uid=$3 phase name
  for phase in migrate qualify grant; do
    case "$phase" in
      migrate) name="perfeng-schema-$uid" ;;
      qualify) name="perfeng-schema-qualify-$uid" ;;
      grant) name="perfeng-runtime-grants-$uid" ;;
    esac
    k -n perf-platform get job "$name" --ignore-not-found -o json | jq -s '.[0] // null' > "$fixture/preparation-$label.$phase.${suffix}job.json"
    k -n perf-platform get pods -l "batch.kubernetes.io/job-name=$name" -o json > "$fixture/preparation-$label.$phase.${suffix}pods.json"
  done
}

preparation_case() {
  local label=$1 name uid rc parameters="$fixture/upgrade-request-parameters.json"
  [[ $label == null || $label == denied || $label == first || $label == repeat ]] || return 2
  if [[ $label == null ]]; then parameters="$fixture/parent-parameters.json"; fi
  start_stage "database-preparation-$label"
  name=$(a submit --from workflowtemplate/perfeng-release-database-prepare-v1 --parameter-file "$parameters" -o json | jq -er .metadata.name)
  if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$name" > "$fixture/preparation-$label.wait.private" 2>&1; then rc=0; else rc=$?; fi
  a get "$name" -o json > "$fixture/preparation-$label.workflow.json"
  qualification_workers "$name" "$fixture/preparation-$label.workers.json"
  uid=$(jq -er .metadata.uid "$fixture/preparation-$label.workflow.json")
  preparation_jobs "$label" '' "$uid"
  if [[ ( ( $label == null || $label == denied ) && ( $rc == 0 || $rc == 124 || $rc == 137 ) ) || ( $label != null && $label != denied && $rc != 0 ) ]]; then
    printf '%s\n' '{"decision":"database-preparation-unexpected-parent-outcome"}' > "$results/database-preparation-$label.failure.json"
    printf '%s\n' '[DATABASE-PREPARATION] Unexpected parent outcome; inspect the stage and private Workflow/worker/Job observations. No later preparation, probe or recovery attempted.' >&2
    return 1
  fi
  if [[ $label == denied ]]; then
    local node pod
    node=$(jq -er '[.status.nodes[]|select(.displayName=="migrate" and .type=="Pod" and (.phase=="Failed" or .phase=="Error"))]|if length==1 then .[0].id else error("expected one denied migration create") end' "$fixture/preparation-denied.workflow.json")
    k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$name" -o json > "$fixture/preparation-denied.workflow-pods.json"
    pod=$(jq -er --arg id "$node" '[.items[]|select(.metadata.annotations["workflows.argoproj.io/node-id"]==$id)]|if length==1 then .[0].metadata.name else error("expected one denied resource worker") end' "$fixture/preparation-denied.workflow-pods.json")
    k -n argo-workflows get pod "$pod" -o json > "$fixture/preparation-denied.diagnostic-pod.json"
    k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 --request-timeout=10s > "$fixture/preparation-denied.worker.private"
  elif [[ $label != null ]]; then
    k -n perf-platform logs "job/perfeng-schema-qualify-$uid" -c qualify --tail=20 --limit-bytes=8192 --request-timeout=10s > "$fixture/preparation-$label.qualify.worker.private"
  fi
  preparation_snapshot "$label"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/database_preparation.py" "$label" "$fixture" "$results"
  complete_stage
}

preparation_denial() {
  start_stage database-preparation-denied-authority
  runtime_grant_access false preparation-denied
  k -n perf-platform get role/release-runtime-grants -o json > "$fixture/preparation-denied.role-original.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/database_preparation.py" role-prepare "$fixture" "$results"
  k -n perf-platform patch role/release-runtime-grants --type=json --patch-file "$fixture/preparation-denied.narrow-patch.json" --request-timeout=10s -o json > "$fixture/preparation-denied.role-narrowed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/database_preparation.py" role-narrowed "$fixture" "$results"
  runtime_grant_access true preparation-denied
  complete_stage
  preparation_case denied
  # No restoration may be planned until exact refusal and retained state qualify.
  start_stage database-preparation-denied-restoration
  runtime_grant_access true preparation-denied
  k -n perf-platform get role/release-runtime-grants -o json > "$fixture/preparation-denied.role-current.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/database_preparation.py" role-restore "$fixture" "$results"
  k -n perf-platform patch role/release-runtime-grants --type=json --patch-file "$fixture/preparation-denied.restore-patch.json" --request-timeout=10s -o json > "$fixture/preparation-denied.role-restored.json"
  runtime_grant_access false preparation-denied
  PYTHONPATH=. uv run --locked --offline python "$source_dir/database_preparation.py" role-restored "$fixture" "$results"
  complete_stage
}

preparation_retention() {
  local phase=${1:-retained} label name uid
  [[ $phase == retained || $phase == final ]] || return 2
  start_stage "database-preparation-$phase"
  for label in null denied first repeat; do
    name=$(jq -er .metadata.name "$fixture/preparation-$label.workflow.json")
    uid=$(jq -er .metadata.uid "$fixture/preparation-$label.workflow.json")
    a get "$name" -o json > "$fixture/preparation-$label.current-workflow.json"
    qualification_workers "$name" "$fixture/preparation-$label.current-workers.json"
    preparation_jobs "$label" current- "$uid"
  done
  preparation_snapshot "$phase"
  schema_parent_ledger_access release-runtime-grants
  k -n perf-platform get role/release-runtime-grants -o json > "$fixture/preparation-denied.role-final.json"
  runtime_grant_access false preparation-denied
  name=$(jq -er .metadata.name "$results/runtime-grants-adopted.workflow.json")
  a get "$name" -o json > "$fixture/preparation.probe-workflow.json"
  name=$(jq -er .metadata.name "$results/runtime-grants-adopted.job.json")
  k -n perf-platform get job "$name" -o json > "$fixture/preparation.probe-job.json"
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=$name" -o json | jq '[.items[]|{uid:.metadata.uid,containers:[.status.containerStatuses[]?|{name,exitCode:.state.terminated.exitCode,waitingReason:.state.waiting.reason}]}]' > "$fixture/preparation.probe-worker.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/database_preparation.py" "$phase" "$fixture" "$results"
  preparation_retention_phase=$phase
  complete_stage
}

preparation_inventory() {
  local kind=$1 path=$2
  PYTHONPATH=. uv run --locked --offline python "$source_dir/database_preparation.py" partition "$fixture" "$results" "${preparation_retention_phase:?Complete preparation retention must pass first}" "$kind" "$path"
}

preparation_suite() {
  local previous=$current_stage label
  start_stage database-preparation-setup
  k apply -f gitops/release-workflows/release-database-prepare-v1.yaml
  schema_parent_ledger_access release-runtime-grants
  preparation_snapshot before
  PYTHONPATH=. uv run --locked --offline python "$source_dir/database_preparation.py" before "$fixture" "$results"
  complete_stage
  preparation_case null
  preparation_denial
  for label in first repeat; do preparation_case "$label"; done
  start_stage database-preparation-complete
  PYTHONPATH=. uv run --locked --offline python "$source_dir/database_preparation.py" complete "$fixture" "$results"
  complete_stage
  current_stage=$previous
}

preparation_suite
