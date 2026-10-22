# Shared CI fixture registration only; setup functions must be called explicitly.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -n ${fixture:-} && -f $fixture/owned && ${KUBECONFIG:-} == "$fixture/kubeconfig" ]] || exit 2
declare -F k a > /dev/null || exit 2

database_setup() {
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
}
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

bucket_setup() {
  for grant in serviceaccount workflow-access platform-jobs; do
    k apply -f "gitops/release-workflows/rbac/external-state/$grant.yaml"
  done
  uv run --locked --offline python - "$fixture" <<'PY'
import base64, json, secrets, sys
from pathlib import Path
directory = Path(sys.argv[1])
access, secret, denied_access, denied_secret = secrets.token_hex(16), secrets.token_hex(32), secrets.token_hex(16), secrets.token_hex(32)
def config(access, secret):
    return {"identities": [{"name": "local-bootstrap", "credentials": [{"accessKey": access, "secretKey": secret}],
        "actions": ["Admin", "Read", "Write", "List"]}]}
def document(name, values):
    return dict(apiVersion="v1", kind="Secret", type="Opaque", immutable=True,
        metadata=dict(name=name, namespace="perf-platform", labels={"app.kubernetes.io/managed-by": "perfeng-environment"}),
        data={k: base64.b64encode(v.encode()).decode() for k, v in values.items()})
profiles = f"[default]\naws_access_key_id={access}\naws_secret_access_key={secret}\n[denied]\naws_access_key_id={denied_access}\naws_secret_access_key={denied_secret}\n"
server = document("bucket-ci-auth", {"s3.json": json.dumps(config(access, secret)), "credentials": profiles})
(directory / "bucket-server-auth.json").write_text(json.dumps(server))
for label, key, password in (("approved", access, secret), ("denied", denied_access, denied_secret)):
    value = document("perfeng-s3-auth", {"access-key": key, "secret-key": password, "s3.json": json.dumps(config(key, password))})
    (directory / f"bucket-client-{label}.json").write_text(json.dumps(value))
PY
  k create -f "$fixture/bucket-server-auth.json"
  k create -f "$fixture/bucket-client-denied.json"
  for manifest in seaweedfs-pod seaweedfs-service bucket-observer; do k create -f "$source_dir/$manifest.yaml"; done
  k -n perf-platform wait --for=condition=Ready pod/bucket-ci-seaweedfs pod/bucket-ci-observer --timeout=180s
}
observer() {
  timeout 90s kubectl --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --request-timeout=20s \
    -n perf-platform exec -i bucket-ci-observer -- "$@"
}
s3() {
  observer env "AWS_PROFILE=$1" aws --endpoint-url http://seaweedfs.perf-platform.svc.cluster.local:8333 \
    --cli-connect-timeout 10 --cli-read-timeout 30 s3api "${@:2}"
}
bucket_secrets() {
  k -n perf-platform get secrets perfeng-s3-auth bucket-ci-auth -o json \
    | jq -S '.items | sort_by(.metadata.name)' > "$1"
}
bucket_jobs() {
  k -n perf-platform get jobs -l perfeng.io/purpose=release-external-state -o json \
    | jq -r '.items[].metadata.name | select(startswith("perfeng-prepare-bucket-v1-"))' | sort
}
bucket_absence() {
  if s3 default head-bucket --bucket perfeng-artifacts > "$fixture/bucket-head.out" 2> "$fixture/bucket-head.error"; then
    echo 'Expected an absent fixture bucket' >&2; exit 1
  fi
  grep -Eq '\(404\)|Not Found' "$fixture/bucket-head.error"
}
bucket_allocation_diagnostics() {
  # Emit only known allocation signals/counts, never raw server logs or keys.
  k -n perf-platform logs bucket-ci-seaweedfs -c seaweedfs --tail=120 --limit-bytes=32768 2>/dev/null \
    | grep -Eo 'No writable volumes|no free volumes|No matching data node found|Not enough data nodes found|create [0-9]+ volume, created [0-9]+' \
    | sort -u > "$results/bucket-allocation.txt" || :
}
