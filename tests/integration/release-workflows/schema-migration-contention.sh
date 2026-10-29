#!/usr/bin/env bash
# Owned idle psql session, exact read-only lock proof, then normal stdin disconnect.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && $cluster_uid =~ ^[0-9a-f-]{36}$ ]] || exit 2
declare -F k a sql schema_native_parent schema_install_snapshot schema_install_sources > /dev/null || exit 2

schema_contention_capture() {
  sql -v "holder=perfeng-schema-contention-$cluster_uid" < "$source_dir/schema-contention.sql" > "$1" 2> "$fixture/schema-contention.capture.private"
}

schema_contention_observe() {
  local deadline=$((SECONDS + 1240))
  while ((SECONDS < deadline)); do
    [[ ! -f $fixture/schema-contention.stop ]] || return 0
    schema_contention_capture "$fixture/schema-contention.sample.json"
    cat "$fixture/schema-contention.sample.json" >> "$fixture/schema-contention.watch.ndjson"
    sleep 1
  done
  return 1
}

schema_contention_retention() {
  local name uid
  name=$(jq -er .metadata.name "$fixture/schema-contention.workflow.json")
  uid=$(jq -er .metadata.uid "$fixture/schema-contention.workflow.json")
  a get "$name" -o json > "$fixture/schema-contention.retained-workflow.json"
  k -n perf-platform get job "perfeng-schema-$uid" -o json > "$fixture/schema-contention.retained-job.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_contention.py" retained "$fixture" "$results"
}

schema_contention_suite() {
  local holder_fd holder_pid observer_pid deadline rc holder_rc observer_rc
  start_stage schema-contention-holder
  mkfifo "$fixture/schema-contention.input"
  exec {holder_fd}<> "$fixture/schema-contention.input"
  timeout --kill-after=5s 1260s kubectl --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --request-timeout=1260s -n perf-platform exec -i "$database_pod" -- env "PGAPPNAME=perfeng-schema-contention-$cluster_uid" psql -X -w -qAt -U postgres -d perfeng -v ON_ERROR_STOP=1 < "$fixture/schema-contention.input" > "$fixture/schema-contention.holder.private" 2>&1 &
  holder_pid=$!
  printf '%s\n' 'SELECT pg_advisory_lock(70657266656);' >&"$holder_fd"
  deadline=$((SECONDS + 30))
  while :; do
    kill -0 "$holder_pid"
    schema_contention_capture "$fixture/schema-contention.candidate.json"
    if PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_contention.py" held "$fixture" "$results"; then break; else rc=$?; fi
    [[ $rc == 3 && $SECONDS -lt $deadline ]]
    sleep 0.25
  done
  complete_stage
  : > "$fixture/schema-contention.watch.ndjson"
  schema_contention_observe &
  observer_pid=$!
  # Capture the runner status explicitly so its read-only observer always stops.
  # Running in a subshell preserves errexit inside the shared native coordinator.
  start_stage schema-contention-parent
  set +e
  (set -e; schema_native_parent contention)
  rc=$?
  set -e
  touch "$fixture/schema-contention.stop"
  if wait "$observer_pid"; then observer_rc=0; else observer_rc=$?; fi
  [[ $rc == 0 && $observer_rc == 0 ]]
  start_stage schema-contention-proof
  schema_install_snapshot contention-after
  schema_install_sources contention
  schema_contention_capture "$fixture/schema-contention.before-release.json"
  k -n perf-platform get jobs -l app.kubernetes.io/name=perfeng-control-plane-migration -o json > "$fixture/schema-contention.jobs-after.json"
  kill -0 "$holder_pid"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_contention.py" release "$fixture" "$results"
  # Normal release only after all proof. Never signal a database backend or repair on EXIT.
  printf '\\q\n' >&"$holder_fd"
  exec {holder_fd}>&-
  if wait "$holder_pid"; then holder_rc=0; else holder_rc=$?; fi
  [[ $holder_rc == 0 ]]
  deadline=$((SECONDS + 30))
  while :; do
    schema_contention_capture "$fixture/schema-contention.released.json"
    if PYTHONPATH=. uv run --locked --offline python "$source_dir/schema_migration_contention.py" released "$fixture" "$results"; then break; else rc=$?; fi
    [[ $rc == 3 && $SECONDS -lt $deadline ]]
    sleep 0.25
  done
  complete_stage
}

schema_contention_suite
