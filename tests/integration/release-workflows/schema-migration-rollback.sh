#!/usr/bin/env bash
# Fixture DDL only; native failure proof must precede guarded RESTRICT cleanup.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $results/schema-contention-released.outcome.json ]] || exit 2
declare -F k a sql schema_native_parent schema_install_snapshot schema_install_sources > /dev/null || exit 2

schema_rollback_catalog() {
  sql < "$source_dir/schema-rollback-catalog.sql" > "$fixture/schema-rollback.$1.catalog.json" 2> "$fixture/schema-rollback.catalog.private"
}

schema_rollback_rows() {
  sql -c 'SELECT jsonb_agg(to_jsonb(t) ORDER BY probe) FROM perfeng_control.artifacts t;' > "$fixture/schema-rollback.$1.rows.json" 2> "$fixture/schema-rollback.rows.private"
}

schema_rollback_retention() {
  local name uid
  name=$(jq -er .metadata.name "$fixture/schema-rollback.workflow.json")
  uid=$(jq -er .metadata.uid "$fixture/schema-rollback.workflow.json")
  a get "$name" -o json > "$fixture/schema-rollback.retained-workflow.json"
  k -n perf-platform get job "perfeng-schema-$uid" -o json > "$fixture/schema-rollback.retained-job.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_rollback.py" retained "$fixture" "$results"
}

schema_rollback_suite() {
  local since
  start_stage schema-rollback-setup
  schema_rollback_catalog empty
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_rollback.py" prepare "$fixture" "$results"
  sql < "$fixture/schema-rollback.create.sql" > "$fixture/schema-rollback.create.private" 2>&1
  schema_rollback_catalog fixture
  schema_rollback_rows fixture
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_rollback.py" fixture "$fixture" "$results"
  complete_stage
  start_stage schema-rollback-parent
  schema_native_parent rollback
  start_stage schema-rollback-proof
  schema_rollback_catalog after
  schema_rollback_rows after
  schema_install_snapshot rollback-after
  schema_install_sources rollback
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/schema-rollback.jobs-after.json"
  since=$(jq -er .metadata.creationTimestamp "$fixture/schema-rollback.workflow.json")
  k -n perf-platform logs "$database_pod" -c postgres --since-time="$since" --timestamps=true --tail=200 --limit-bytes=65536 > "$fixture/schema-rollback.postgres.private" 2>&1
  # This mode writes cleanup SQL only after native/collision/rollback/retention proof.
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_rollback.py" cleanup "$fixture" "$results"
  start_stage schema-rollback-cleanup
  sql < "$fixture/schema-rollback.cleanup.sql" > "$fixture/schema-rollback.cleanup.private" 2>&1
  schema_rollback_catalog cleaned
  schema_install_snapshot rollback-cleaned
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_rollback.py" cleaned "$fixture" "$results"
  complete_stage
}

schema_rollback_suite
