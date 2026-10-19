# CI-only parity phase: no independent bootstrap or operator entrypoint.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -n ${fixture:-} && -f $fixture/owned && ${KUBECONFIG:-} == "$fixture/kubeconfig" ]] || exit 2
declare -F k a > /dev/null || exit 2

for grant in serviceaccount workflow-access platform-jobs; do
  k apply -f "gitops/release-workflows/rbac/external-state/$grant.yaml"
done
# Credentials/SQL/password hashes stay only in the private fixture directory.
uv run --locked --offline python - "$fixture" <<'PY'
import base64, json, secrets, sys
from pathlib import Path
directory = Path(sys.argv[1])
admin, migration, runtime = (secrets.token_urlsafe(32) for _ in range(3))
host = "postgres.perf-platform.svc.cluster.local:5432/perfeng?sslmode=disable"
items = []
for name, values in (
    ("perfeng-postgres-auth", {"password": admin}),
    ("perfeng-control-plane-database", {"migration-url": f"postgresql://perfeng_migration:{migration}@{host}",
        "runtime-url": f"postgresql://perfeng_runtime:{runtime}@{host}"}),
):
    labels = {"app.kubernetes.io/managed-by": "perfeng-environment"}
    if name == "perfeng-control-plane-database":
        labels["perfeng.io/purpose"] = "control-plane-database"
    items.append(dict(apiVersion="v1", kind="Secret", metadata=dict(name=name, namespace="perf-platform", labels=labels),
        immutable=True, type="Opaque", data={k: base64.b64encode(v.encode()).decode() for k, v in values.items()}))
(directory / "database-secrets.json").write_text(json.dumps(dict(apiVersion="v1", kind="List", items=items)))
(directory / "database-break-password.sql").write_text("ALTER ROLE perfeng_runtime PASSWORD '" + secrets.token_urlsafe(32) + "';\n")
(directory / "database-restore-password.sql").write_text("ALTER ROLE perfeng_runtime PASSWORD '" + runtime + "';\n")
PY
k create -f "$fixture/database-secrets.json"
k create -f "$source_dir/postgres-pod.yaml"
k create -f "$source_dir/postgres-service.yaml"
k -n perf-platform wait --for=condition=Ready pod/database-ci-postgres --timeout=180s
sql() {
  timeout 30s kubectl --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --request-timeout=20s \
    -n perf-platform exec -i database-ci-postgres -- psql -X -w -qAt -U postgres -d perfeng -v ON_ERROR_STOP=1 "$@"
}
role_snapshot() {
  sql > "$1" <<'SQL'
SELECT coalesce(json_agg(json_build_object('oid',oid::text,'name',rolname,'login',rolcanlogin,
  'superuser',rolsuper,'createDB',rolcreatedb,'createRole',rolcreaterole,'replication',rolreplication,
  'bypassRLS',rolbypassrls,'password',rolpassword,
  'connect',has_database_privilege(oid,'perfeng','CONNECT'),
  'create',has_database_privilege(oid,'perfeng','CREATE')) ORDER BY rolname),'[]'::json)
FROM pg_authid WHERE rolname IN ('perfeng_migration','perfeng_runtime');
SQL
}
secret_snapshot() {
  k -n perf-platform get secrets perfeng-postgres-auth perfeng-control-plane-database -o json \
    | jq -S '.items | sort_by(.metadata.name)' > "$1"
}
database_jobs() {
  k -n perf-platform get jobs -l perfeng.io/purpose=release-external-state -o json \
    | jq -r '.items[].metadata.name | select(startswith("perfeng-prepare-db-v1-"))' | sort
}
database_case() {
  database_label=$1 expected_database=$2 diagnostic=${3:-}
  echo "Database case: $database_label ($expected_database)"
  database_jobs > "$fixture/database-jobs-before"
  database_workflow=$(a submit "$source_dir/database-workflow.yaml" -o json | jq -er .metadata.name)
  if timeout 330s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$database_workflow"; then database_rc=0; else database_rc=$?; fi
  a get "$database_workflow" -o json > "$results/database-$database_label.workflow.json"
  database_jobs > "$fixture/database-jobs-after"
  comm -13 "$fixture/database-jobs-before" "$fixture/database-jobs-after" > "$fixture/database-new-job"
  [[ $(wc -l < "$fixture/database-new-job") == 1 ]]
  database_job=$(cat "$fixture/database-new-job")
  if [[ $expected_database == success ]]; then database_condition=Complete; else database_condition=Failed; fi
  k -n perf-platform wait "--for=condition=$database_condition" "job/$database_job" --timeout=30s
  k -n perf-platform get job "$database_job" -o json > "$results/database-$database_label.job.json"
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=$database_job" -o json > "$fixture/database-pods.json"
  if [[ $expected_database == success ]]; then
    [[ $database_rc == 0 ]]
    jq -e '.status.phase == "Succeeded"' "$results/database-$database_label.workflow.json" > /dev/null
    jq -e '.status.succeeded == 1' "$results/database-$database_label.job.json" > /dev/null
    jq -e --arg job "$database_job" 'any(.status.nodes[.metadata.name].outputs.parameters[];
      .name == "job-name" and .value == $job)' "$results/database-$database_label.workflow.json" > /dev/null
    jq -e 'any(.items[].status.containerStatuses[]?; .name == "psql" and .state.terminated.exitCode == 0)' "$fixture/database-pods.json" > /dev/null
  else
    [[ $expected_database == refusal && $database_rc != 0 && $database_rc != 124 && $database_rc != 137 ]]
    jq -e '.status.phase == "Failed" or .status.phase == "Error"' "$results/database-$database_label.workflow.json" > /dev/null
    jq -e '.status.failed == 1 and any(.status.conditions[]?; .type == "Failed" and .status == "True")' "$results/database-$database_label.job.json" > /dev/null
    jq -e 'any(.items[].status.containerStatuses[]?; .name == "psql" and .state.terminated.exitCode == 1)' "$fixture/database-pods.json" > /dev/null
    k -n perf-platform logs "job/$database_job" -c psql --tail=30 --limit-bytes=8192 \
      | grep -F "$diagnostic" > "$results/database-$database_label.refusal.txt"
  fi
  secret_snapshot "$fixture/database-secrets-after.json"
  cmp "$fixture/database-secrets-before.json" "$fixture/database-secrets-after.json"
}

secret_snapshot "$fixture/database-secrets-before.json"
role_snapshot "$fixture/database-empty.json"
jq -e 'length == 0' "$fixture/database-empty.json"
database_case first success
role_snapshot "$fixture/database-first.json"
jq -e 'length == 2 and all(.[]; .login and .connect and
  (.superuser or .createDB or .createRole or .replication or .bypassRLS | not)) and
  (map({key: .name, value: .create}) | from_entries) == {perfeng_migration:true, perfeng_runtime:false}' "$fixture/database-first.json"
# Never upload password hashes; publish only non-secret role identity/privileges.
jq -S 'map(del(.password))' "$fixture/database-first.json" > "$results/database-roles.json"
database_case repeat success
role_snapshot "$fixture/database-repeat.json"
cmp "$fixture/database-first.json" "$fixture/database-repeat.json"

printf '%s\n' 'ALTER ROLE perfeng_runtime SUPERUSER;' | sql
role_snapshot "$fixture/database-unsafe-before.json"
database_case unsafe-role refusal 'Existing database role has unsafe attributes'
role_snapshot "$fixture/database-unsafe-after.json"
cmp "$fixture/database-unsafe-before.json" "$fixture/database-unsafe-after.json"
printf '%s\n' 'ALTER ROLE perfeng_runtime NOSUPERUSER;' | sql
database_case corrected-role success
role_snapshot "$fixture/database-corrected-role.json"
cmp "$fixture/database-first.json" "$fixture/database-corrected-role.json"

sql < "$fixture/database-break-password.sql"
role_snapshot "$fixture/database-password-before.json"
database_case wrong-login refusal 'password authentication failed for user "perfeng_runtime"'
role_snapshot "$fixture/database-password-after.json"
cmp "$fixture/database-password-before.json" "$fixture/database-password-after.json"
sql < "$fixture/database-restore-password.sql"
database_case corrected-login success
role_snapshot "$fixture/database-final.json"
# PostgreSQL salts password hashes on explicit admin reset: compare all other state.
jq -S 'map(del(.password))' "$fixture/database-final.json" > "$fixture/database-final-public.json"
cmp "$results/database-roles.json" "$fixture/database-final-public.json"
database_jobs > "$results/database-retained-jobs.txt"
[[ $(wc -l < "$results/database-retained-jobs.txt") == 6 ]]
for completed_case in first repeat unsafe-role corrected-role wrong-login corrected-login; do
  original_job=$(jq -r .metadata.name "$results/database-$completed_case.job.json")
  original_uid=$(jq -r .metadata.uid "$results/database-$completed_case.job.json")
  [[ $(k -n perf-platform get job "$original_job" -o jsonpath='{.metadata.uid}') == "$original_uid" ]]
done
database_identity=system:serviceaccount:argo-workflows:release-external-state
for permission in 'create jobs perf-platform yes' 'get jobs perf-platform yes' 'watch jobs perf-platform yes' \
  'delete jobs perf-platform no' 'create secrets perf-platform no' 'patch secrets perf-platform no' \
  'list secrets perf-platform no' 'patch deployments perf-platform no' 'delete persistentvolumeclaims perf-platform no'; do
  read -r verb resource namespace expected_answer <<< "$permission"
  answer=$(k auth can-i "$verb" "$resource" --namespace "$namespace" --as "$database_identity" || :)
  [[ $answer == "$expected_answer" ]] || { echo "Unexpected database workflow authority: $permission" >&2; exit 1; }
done
rm -f "$fixture/database-secrets.json" "$fixture/database-break-password.sql" "$fixture/database-restore-password.sql"
echo 'Database child parity passed; request admission, migration and operational cutover remain separate.'
