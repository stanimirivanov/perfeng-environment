#!/usr/bin/env bash
# Owned idle SQL session; normal disconnect is gated by exact lock/refusal evidence.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && $cluster_uid =~ ^[0-9a-f-]{36}$ ]] || exit 2
declare -F k a sql runtime_grant_parent grant_snapshot > /dev/null || exit 2

grant_contention_capture() {
  sql -v "holder=perfeng-grant-contention-$cluster_uid" < "$source_dir/grant-contention.sql" > "$1" 2> "$fixture/grant-contention.capture.private"
}

grant_contention_observe() {
  local deadline=$((SECONDS + 1240))
  while ((SECONDS < deadline)); do
    [[ ! -f $fixture/grant-contention.stop ]] || return 0
    grant_contention_capture "$fixture/grant-contention.sample.json"
    cat "$fixture/grant-contention.sample.json" >> "$fixture/grant-contention.watch.ndjson"
    sleep 0.25
  done
  return 1
}

runtime_grant_contention_retention() {
  grant_contention_capture "$fixture/grant-contention.released.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_contention.py" retained "$fixture" "$results"
}

runtime_grant_contention() {
  local previous=$current_stage holder_fd holder_pid observer_pid deadline rc holder_rc observer_rc
  start_stage grant-contention-holder
  mkfifo "$fixture/grant-contention.input"
  exec {holder_fd}<> "$fixture/grant-contention.input"
  timeout --kill-after=5s 1260s kubectl --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --request-timeout=1260s -n perf-platform exec -i "$database_pod" -- env "PGAPPNAME=perfeng-grant-contention-$cluster_uid" psql -X -w -qAt -U postgres -d perfeng -v ON_ERROR_STOP=1 < "$fixture/grant-contention.input" > "$fixture/grant-contention.holder.private" 2>&1 &
  holder_pid=$!
  printf '%s\n' 'SELECT pg_advisory_lock(70657266656);' >&"$holder_fd"
  deadline=$((SECONDS + 30))
  while :; do
    kill -0 "$holder_pid"
    grant_contention_capture "$fixture/grant-contention.candidate.json"
    if PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_contention.py" held "$fixture" "$results"; then break; else rc=$?; fi
    [[ $rc == 3 && $SECONDS -lt $deadline ]]
    sleep 0.25
  done
  complete_stage
  : > "$fixture/grant-contention.watch.ndjson"
  grant_contention_observe &
  observer_pid=$!
  # Explicit status capture preserves errexit in the parent and always stops its observer.
  start_stage grant-contention-parent
  set +e
  (set -e; runtime_grant_parent contention)
  rc=$?
  set -e
  touch "$fixture/grant-contention.stop"
  if wait "$observer_pid"; then observer_rc=0; else observer_rc=$?; fi
  [[ $rc == 0 && $observer_rc == 0 ]]
  complete_stage
  start_stage grant-contention-proof
  grant_contention_capture "$fixture/grant-contention.before-release.json"
  kill -0 "$holder_pid"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_contention.py" release "$fixture" "$results"
  # No signal, backend termination or EXIT repair; only the owned psql gets normal EOF/quit.
  printf '\\q\n' >&"$holder_fd"
  exec {holder_fd}>&-
  if wait "$holder_pid"; then holder_rc=0; else holder_rc=$?; fi
  [[ $holder_rc == 0 ]]
  deadline=$((SECONDS + 30))
  while :; do
    grant_contention_capture "$fixture/grant-contention.released.json"
    if PYTHONPATH=. uv run --locked --offline python "$source_dir/runtime_grants_contention.py" released "$fixture" "$results"; then break; else rc=$?; fi
    [[ $rc == 3 && $SECONDS -lt $deadline ]]
    sleep 0.25
  done
  complete_stage
  current_stage=$previous
}
