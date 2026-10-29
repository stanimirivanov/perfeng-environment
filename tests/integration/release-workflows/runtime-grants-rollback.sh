#!/usr/bin/env bash
# Fixture-only DDL; cleanup is generated only after the real rollback is proved.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k a sql runtime_grant_parent grant_snapshot > /dev/null || exit 2

grant_rollback_capture() {
  sql < "$source_dir/grant-rollback-catalog.sql" > "$1" 2> "$fixture/grant-rollback.capture.private"
}

runtime_grant_rollback_retention() {
  grant_rollback_capture "$fixture/grant-rollback.catalog-retained.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_rollback.py" retained "$fixture" "$results"
}

runtime_grant_rollback() {
  local previous=$current_stage
  start_stage grant-rollback-fixture
  grant_rollback_capture "$fixture/grant-rollback.catalog-before.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_rollback.py" prepare "$fixture" "$results"
  sql < "$fixture/grant-rollback.create.sql" > "$fixture/grant-rollback.create.private" 2>&1
  grant_rollback_capture "$fixture/grant-rollback.catalog-installed.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_rollback.py" installed "$fixture" "$results"
  complete_stage
  runtime_grant_parent rollback
  start_stage grant-rollback-proof
  grant_rollback_capture "$fixture/grant-rollback.catalog-failed.json"
  grant_rollback_capture "$fixture/grant-rollback.catalog-current.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_rollback.py" cleanup "$fixture" "$results"
  # No EXIT cleanup, CASCADE or replacement: only verified original fixture objects.
  sql < "$fixture/grant-rollback.cleanup.sql" > "$fixture/grant-rollback.cleanup.private" 2>&1
  grant_rollback_capture "$fixture/grant-rollback.catalog-cleaned.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_rollback.py" cleaned "$fixture" "$results"
  complete_stage
  current_stage=$previous
}
