#!/usr/bin/env bash
# CI-only migration image parity. No operational schema admission is introduced.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k a sql role_snapshot external_secrets > /dev/null || exit 2

uv run --locked --offline python - "$fixture" <<'PY'
import json, re, sys
from pathlib import Path
directory = Path(sys.argv[1])
lock = json.loads(Path("deployments/local-control-plane.json").read_text())["controlPlane"]
contract = json.loads(Path("tests/integration/release-workflows/migration-image.json").read_text())
assert contract["schemaVersion"] == 1
assert (contract["image"], contract["sourceRevision"]) == (lock["image"], lock["sourceRevision"])
assert re.fullmatch(r"ghcr.io/stanimirivanov/perfeng-control-plane@sha256:[0-9a-f]{64}", lock["image"])
secret = json.loads((directory / "registry-approved.secret.json").read_text())
secret["metadata"] = {"name": "migration-ci-registry", "namespace": "perf-platform",
    "labels": {"perfeng.io/purpose": "ci-migration-image"}}
(directory / "migration-image.secret.json").write_text(json.dumps(secret))
PY
k create -f "$fixture/migration-image.secret.json"
rm -f "$fixture/migration-image.secret.json"
image=$(jq -er .image "$source_dir/migration-image.json")
external_secrets "$fixture/migration-secrets-before.json"
k -n perf-platform get secret migration-ci-registry -o json | jq -S . > "$fixture/migration-pull-before.json"
role_snapshot "$fixture/migration-roles-before.json"
postgres_uid=$(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}')
[[ $postgres_uid == "$(jq -er .after "$results/postgres-chart-pod-uids.json")" ]]
sql > "$results/migration-image-initial.json" <<'SQL'
SELECT json_build_object('schemaAbsent',to_regnamespace('perfeng_control') IS NULL,
 'applicationRelations',(SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
   WHERE n.nspname IN ('public','perfeng_control') AND c.relkind IN ('r','i','S')),
 'runtimeCreate',has_database_privilege('perfeng_runtime','perfeng','CREATE'),
 'migrationCreate',has_database_privilege('perfeng_migration','perfeng','CREATE'));
SQL
jq -e '. == {schemaAbsent:true,applicationRelations:0,runtimeCreate:false,migrationCreate:true}' "$results/migration-image-initial.json" > /dev/null

migration_snapshot() {
  sql > "$1" <<'SQL'
SELECT json_build_object(
 'schemaOID',(SELECT oid::text FROM pg_namespace WHERE nspname='perfeng_control'),
 'schemaOwner',(SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname='perfeng_control'),
 'relations',(SELECT json_agg(json_build_object('name',c.relname,'oid',c.oid::text,'owner',pg_get_userbyid(c.relowner)) ORDER BY c.relname)
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='perfeng_control' AND c.relkind IN ('r','i','S')),
 'ledger',(SELECT json_agg(json_build_object('version',version,'sha256',sha256,'appliedAt',applied_at) ORDER BY version)
    FROM perfeng_control.schema_migrations));
SQL
}
migration_permission_denied() {
  grep -E 'user=perfeng_runtime database=perfeng app=migration-ci-runtime-role .*permission denied for database "?perfeng"?' "$1" > /dev/null
}
migration_case() {
  migration_label=$1 migration_key=$2
  [[ $migration_key == runtime-url || $migration_key == migration-url ]] || exit 2
  echo "Selected migration image: $migration_label"
  checkpoint=$(a submit --from workflowtemplate/perfeng-release-maintenance-checkpoint-v2 \
    --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
  timeout 930s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$checkpoint"
  a get "$checkpoint" -o json > "$results/migration-image-$migration_label.checkpoint.workflow.json"
  jq -e '.status.phase == "Succeeded"' "$results/migration-image-$migration_label.checkpoint.workflow.json" > /dev/null
  jq -er '.status.nodes[.metadata.name].outputs.parameters[] | select(.name == "result") | .value' \
    "$results/migration-image-$migration_label.checkpoint.workflow.json" > "$results/migration-image-$migration_label.checkpoint.json"
  uv run --locked --offline python - "$fixture/parent-parameters.json" "$results/migration-image-$migration_label.checkpoint.json" "$cluster_uid" <<'PY'
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
parameters, checkpoint = (json.loads(Path(p).read_text()) for p in sys.argv[1:3])
Draft202012Validator(json.loads(Path("deployments/maintenance-checkpoint-v1.schema.json").read_text())).validate(checkpoint)
for field, parameter in (("requestRevision","request-revision"),("requestDigest","request-sha256"),("selectionRevision","selection-revision")):
    assert checkpoint[field] == parameters[parameter]
assert checkpoint["clusterUID"] == sys.argv[3]
PY
  migration_name=$(a submit "$source_dir/migration-image-workflow.yaml" -p "image=$image" \
    -p "credential-key=$migration_key" -p "application-name=migration-ci-$migration_label" -o json | jq -er .metadata.name)
  if timeout 270s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$migration_name"; then migration_rc=0; else migration_rc=$?; fi
  a get "$migration_name" -o json > "$results/migration-image-$migration_label.workflow.json"
  k -n perf-platform get jobs -l perfeng.io/purpose=ci-migration-image -o json \
    | jq -er --arg label "migration-ci-$migration_label" '.items[] | select(any(.spec.template.spec.containers[].env[]; .name == "PGAPPNAME" and .value == $label)) | .metadata.name' > "$fixture/migration-job-name"
  [[ $(wc -l < "$fixture/migration-job-name") == 1 ]]
  job=$(cat "$fixture/migration-job-name")
  k -n perf-platform get job "$job" -o json > "$results/migration-image-$migration_label.job.json"
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=$job" -o json > "$fixture/migration-pods.json"
  jq '[.items[] | {uid:.metadata.uid,containers:[.status.containerStatuses[]? | {name,exitCode:.state.terminated.exitCode,waitingReason:.state.waiting.reason}]}]' \
    "$fixture/migration-pods.json" > "$results/migration-image-$migration_label.worker.json"
  k -n perf-platform logs "job/$job" -c migrate --tail=20 --limit-bytes=8192 > "$fixture/migration-worker.log"
  grep -Fx 'Connecting to PostgreSQL for control-plane migrations...' "$fixture/migration-worker.log" > /dev/null
  grep -Fx 'Checking migration versions and applying pending migrations...' "$fixture/migration-worker.log" > /dev/null
  if [[ $migration_key == runtime-url ]]; then
    [[ $migration_rc != 0 && $migration_rc != 124 && $migration_rc != 137 ]]
    jq -e '.status.phase == "Failed" or .status.phase == "Error"' "$results/migration-image-$migration_label.workflow.json" > /dev/null
    jq -e '.status.failed == 1' "$results/migration-image-$migration_label.job.json" > /dev/null
    jq -e 'length == 1 and .[0].containers == [{name:"migrate",exitCode:1,waitingReason:null}]' "$results/migration-image-$migration_label.worker.json" > /dev/null
    since=$(jq -er .metadata.creationTimestamp "$results/migration-image-$migration_label.workflow.json")
    k -n perf-platform logs "$database_pod" -c postgres --since-time="$since" --tail=120 --limit-bytes=32768 > "$fixture/migration-postgres.log"
    migration_permission_denied "$fixture/migration-postgres.log"
    printf '%s\n' 'runtime role: PostgreSQL denied CREATE on the disposable database' > "$results/migration-image-runtime-role.refusal.txt"
    [[ $(sql -c "SELECT to_regnamespace('perfeng_control') IS NULL;") == t ]]
  else
    [[ $migration_rc == 0 ]]
    jq -e '.status.phase == "Succeeded"' "$results/migration-image-$migration_label.workflow.json" > /dev/null
    jq -e '.status.succeeded == 1' "$results/migration-image-$migration_label.job.json" > /dev/null
    jq -e 'length == 1 and .[0].containers == [{name:"migrate",exitCode:0,waitingReason:null}]' "$results/migration-image-$migration_label.worker.json" > /dev/null
    jq -e --arg job "$job" 'any(.status.nodes[.metadata.name].outputs.parameters[]; .name == "job-name" and .value == $job)' "$results/migration-image-$migration_label.workflow.json" > /dev/null
    grep -Fx 'Control-plane schema is up to date.' "$fixture/migration-worker.log" > "$results/migration-image-$migration_label.success.txt"
    migration_snapshot "$results/migration-image-$migration_label.schema.json"
  fi
}
migration_case runtime-role runtime-url
migration_case first migration-url
sql <<'SQL'
CREATE TABLE public.migration_ci_sentinel(id integer PRIMARY KEY, value text NOT NULL);
INSERT INTO public.migration_ci_sentinel VALUES (1,'migration-image-parity-v1');
SQL
sql -c "SELECT oid::text FROM pg_class WHERE oid = 'public.migration_ci_sentinel'::regclass;" > "$results/migration-image-sentinel-oid-before.txt"
migration_case repeat migration-url
cmp "$results/migration-image-first.schema.json" "$results/migration-image-repeat.schema.json"
sql -c "SELECT oid::text FROM pg_class WHERE oid = 'public.migration_ci_sentinel'::regclass;" > "$results/migration-image-sentinel-oid-after.txt"
cmp "$results/migration-image-sentinel-oid-before.txt" "$results/migration-image-sentinel-oid-after.txt"
[[ $(sql -c 'SELECT value FROM public.migration_ci_sentinel WHERE id=1;') == migration-image-parity-v1 ]]
uv run --locked --offline python "$source_dir/migration_image_evidence.py" "$results" "$source_dir/migration-image.json"
external_secrets "$fixture/migration-secrets-after.json"
cmp "$fixture/migration-secrets-before.json" "$fixture/migration-secrets-after.json"
k -n perf-platform get secret migration-ci-registry -o json | jq -S . > "$fixture/migration-pull-after.json"
cmp "$fixture/migration-pull-before.json" "$fixture/migration-pull-after.json"
role_snapshot "$fixture/migration-roles-after.json"
cmp "$fixture/migration-roles-before.json" "$fixture/migration-roles-after.json"
[[ $(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}') == "$postgres_uid" ]]
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
for label in runtime-role first repeat; do
  for suffix in workflow.json checkpoint.workflow.json; do
    name=$(jq -er .metadata.name "$results/migration-image-$label.$suffix") uid=$(jq -er .metadata.uid "$results/migration-image-$label.$suffix")
    [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
  done
  name=$(jq -er .metadata.name "$results/migration-image-$label.job.json") uid=$(jq -er .metadata.uid "$results/migration-image-$label.job.json")
  [[ $(k -n perf-platform get job "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
done
echo 'Selected image migration parity passed; production migration and real readiness remain separate.'
