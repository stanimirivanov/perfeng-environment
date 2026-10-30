#!/usr/bin/env bash
# CI only: parity for the supported explicit grant transaction, not deployment.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k a sql migration_snapshot external_secrets role_snapshot > /dev/null || exit 2

runtime_probe_outcome() {
  local expected=$1 prefix=$2 log=$3
  if [[ $expected == success ]]; then
    jq -e '.status.phase == "Succeeded"' "$prefix.workflow.json" > /dev/null &&
    jq -e '.status.succeeded == 1' "$prefix.job.json" > /dev/null &&
    jq -e 'length == 1 and .[0].containers == [{name:"probe",exitCode:0,waitingReason:null}]' "$prefix.worker.json" > /dev/null &&
    grep -Fx 'runtime privilege policy verified' "$log" > /dev/null
  else
    jq -e '.status.phase == "Failed" or .status.phase == "Error"' "$prefix.workflow.json" > /dev/null &&
    jq -e '.status.failed == 1' "$prefix.job.json" > /dev/null &&
    jq -e 'length == 1 and .[0].containers == [{name:"probe",exitCode:3,waitingReason:null}]' "$prefix.worker.json" > /dev/null &&
    grep -Ex 'psql:/sql/probe.sql:[0-9]+: ERROR:  runtime privilege matrix mismatch' "$log" > /dev/null
  fi
}
runtime_probe_refuse() {
  local prefix=$1 code
  code=$(jq -r '[.[].containers[]? | select(.name=="probe") | .exitCode][0] // "unknown"' "$prefix.worker.json")
  if [[ $code == 2 ]]; then
    printf '%s\n' '[PREREQUISITE:database-connection] psql connection failed or was lost (exit 2); this is not the expected SQL-policy refusal (exit 3). Inspect runtime-url Secret delivery, libpq connection configuration, PostgreSQL endpoint/authentication/TLS and connection availability. Image migration already passed; do not change registry roles for this failure. Raw database output was withheld.' > "$prefix.failure.txt"
  elif [[ $code == 64 ]]; then
    printf '%s\n' '[PREREQUISITE:database-input] Runtime URL was rejected before psql execution. Verify the runtime-url Secret uses the reviewed local role/host/port/database/sslmode and 43-character URL-safe password. Do not rotate credentials or expose the URL as an implicit repair.' > "$prefix.failure.txt"
  else
    printf '%s\n' '[RUNTIME-POLICY] Unexpected worker or SQL-policy outcome. Inspect the case workflow/job/worker artifacts and the expected SQL refusal boundary; timeout, image-pull and connection failures cannot qualify as policy refusal. Raw database output was withheld.' > "$prefix.failure.txt"
  fi
  cat "$prefix.failure.txt" >&2
  return 1
}
runtime_acl_snapshot() {
  sql > "$1" <<'SQL'
SELECT json_build_object(
 'schemaACL',(SELECT nspacl FROM pg_namespace WHERE nspname='perfeng_control'),
 'tables',(SELECT json_agg(json_build_object('name',c.relname,'acl',c.relacl) ORDER BY c.relname)
   FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname='perfeng_control' AND c.relkind IN ('r','p')));
SQL
}
runtime_data_snapshot() {
  # Identifiers/literals are quoted by PostgreSQL, never interpolated by a shell.
  sql > "$fixture/runtime-data-snapshot.sql" <<'SQL'
SELECT format('SELECT %L, md5(coalesce(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t))::text, %L)) FROM perfeng_control.%I t;',
  c.relname, '[]', c.relname)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='perfeng_control' AND c.relkind IN ('r','p') ORDER BY c.relname;
SQL
  sql < "$fixture/runtime-data-snapshot.sql" > "$1"
}
runtime_probe() {
  local label=$1 expected=$2 workflow job prefix rc
  echo "Runtime grants case: $label"
  prefix="$results/runtime-grants-$label"
  workflow=$(a submit "$source_dir/runtime-grants-workflow.yaml" -o json | jq -er .metadata.name)
  if timeout 180s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow"; then rc=0; else rc=$?; fi
  [[ $rc != 124 && $rc != 137 ]]
  if [[ $expected == success ]]; then [[ $rc == 0 ]]; else [[ $rc != 0 ]]; fi
  a get "$workflow" -o json > "$prefix.workflow.json"
  k -n perf-platform get jobs -l "perfeng.io/workflow=$workflow" -o json > "$fixture/runtime-jobs.json"
  jq -e '.items | length == 1' "$fixture/runtime-jobs.json" > /dev/null
  jq '.items[0]' "$fixture/runtime-jobs.json" > "$prefix.job.json"
  job=$(jq -er .metadata.name "$prefix.job.json")
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=$job" -o json \
    | jq '[.items[] | {uid:.metadata.uid,containers:[.status.containerStatuses[]? | {name,exitCode:.state.terminated.exitCode,waitingReason:.state.waiting.reason}]}]' > "$prefix.worker.json"
  k -n perf-platform logs "job/$job" -c probe --tail=30 --limit-bytes=8192 > "$fixture/runtime-probe.log"
  if ! runtime_probe_outcome "$expected" "$prefix" "$fixture/runtime-probe.log"; then
    runtime_probe_refuse "$prefix"
    return 1
  fi
  # Publish a constant only after exact phase/exit/SQL checks; raw logs stay private.
  printf '%s\n' "$label: $expected at runtime privilege policy" > "$prefix.outcome.txt"
}

[[ $(k -n perf-platform get deployment control-plane -o jsonpath='{.spec.replicas}') == 0 ]]
external_secrets "$fixture/runtime-grants-secrets-before.json"
role_snapshot "$fixture/runtime-grants-roles-before.json"
migration_snapshot "$results/runtime-grants-schema-before.json"
runtime_data_snapshot "$fixture/runtime-grants-data-before.txt"
sql -c "SELECT oid::text FROM pg_class WHERE oid='public.migration_ci_sentinel'::regclass;" > "$results/runtime-grants-sentinel-before.txt"
[[ $(sql -c 'SELECT value FROM public.migration_ci_sentinel WHERE id=1;') == migration-image-parity-v1 ]]
k -n perf-platform create configmap runtime-grants-ci --from-file="probe.sql=$source_dir/runtime-grants-probe.sql"
runtime_probe ungranted failure
if [[ ${scenario:-} == initial ]]; then
  source "$source_dir/runtime-grants-parent.sh"
  runtime_grant_denial
  runtime_grant_contention
  runtime_grant_rollback
  runtime_grant_parent first
else
  sql < "$source_dir/runtime-grants.sql"
fi
runtime_probe first success
runtime_acl_snapshot "$results/runtime-grants-first.acl.json"
if [[ ${scenario:-} == initial ]]; then
  runtime_grant_parent repeat
else
  sql < "$source_dir/runtime-grants.sql"
fi
runtime_probe repeat success
runtime_acl_snapshot "$results/runtime-grants-repeat.acl.json"
cmp "$results/runtime-grants-first.acl.json" "$results/runtime-grants-repeat.acl.json"
# Deliberate fixture-only drift; no rows are removed. Runtime probes cannot repair it.
sql -c 'GRANT DELETE ON perfeng_control.runs TO perfeng_runtime;'
runtime_probe excess-delete failure
if [[ ${scenario:-} == initial ]]; then
  runtime_grant_parent corrected
else
  sql < "$source_dir/runtime-grants.sql"
fi
runtime_probe corrected success
runtime_acl_snapshot "$results/runtime-grants-corrected.acl.json"
cmp "$results/runtime-grants-first.acl.json" "$results/runtime-grants-corrected.acl.json"
migration_snapshot "$results/runtime-grants-schema-after.json"
cmp "$results/runtime-grants-schema-before.json" "$results/runtime-grants-schema-after.json"
runtime_data_snapshot "$fixture/runtime-grants-data-after.txt"
cmp "$fixture/runtime-grants-data-before.txt" "$fixture/runtime-grants-data-after.txt"
external_secrets "$fixture/runtime-grants-secrets-after.json"
cmp "$fixture/runtime-grants-secrets-before.json" "$fixture/runtime-grants-secrets-after.json"
role_snapshot "$fixture/runtime-grants-roles-after.json"
cmp "$fixture/runtime-grants-roles-before.json" "$fixture/runtime-grants-roles-after.json"
sql -c "SELECT oid::text FROM pg_class WHERE oid='public.migration_ci_sentinel'::regclass;" > "$results/runtime-grants-sentinel-after.txt"
cmp "$results/runtime-grants-sentinel-before.txt" "$results/runtime-grants-sentinel-after.txt"
[[ $(sql -c 'SELECT value FROM public.migration_ci_sentinel WHERE id=1;') == migration-image-parity-v1 ]]
[[ $(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}') == "$postgres_uid" ]]
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
[[ $(k -n perf-platform get deployment control-plane -o jsonpath='{.spec.replicas}') == 0 ]]
for suffix in workflow job; do
  jq -s -e 'length == 5 and ([.[].metadata.uid] | unique | length) == 5' "$results"/runtime-grants-*.$suffix.json > /dev/null
  for label in ungranted first repeat excess-delete corrected; do
    name=$(jq -er .metadata.name "$results/runtime-grants-$label.$suffix.json")
    uid=$(jq -er .metadata.uid "$results/runtime-grants-$label.$suffix.json")
    namespace=perf-platform; [[ $suffix == job ]] || namespace=argo-workflows
    [[ $(k -n "$namespace" get "$suffix" "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
  done
done

if [[ ${scenario:-} == initial ]]; then runtime_grant_retention bootstrap; fi
echo 'Runtime grant parity passed; actual server/readiness and production cutover remain separate.'
