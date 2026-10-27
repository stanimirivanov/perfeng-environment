#!/usr/bin/env bash
# No persisted writes, including if the audited role becomes overprivileged.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent && ${scenario:-} == initial ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -n ${cluster_uid:-} ]] || exit 2
declare -F k maintenance_snapshot root_state root_retained > /dev/null || exit 2

maintenance_authority_k() {
  k --as=system:serviceaccount:argo-workflows:release-maintenance-pause \
    --as-group=system:serviceaccounts --as-group=system:serviceaccounts:argo-workflows \
    --as-group=system:authenticated --request-timeout=10s "$@"
}
maintenance_authority_verify() {
  local resource namespace target rc label verb plural group name source file attempt passed previous=$current_stage
  [[ $(k get namespace kube-system --request-timeout=10s -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
  start_stage maintenance-authority
  printf 'case\tdecision\n' > "$results/maintenance-authority.tsv"
  for resource in root child deployment; do
    case "$resource" in
      root) namespace=argocd; target=application/perfeng-local-root ;;
      child) namespace=argocd; target=application/perfeng-local-control-plane ;;
      deployment) namespace=perf-platform; target=deployment/control-plane ;;
    esac
    passed=false
    for attempt in 1 2 3; do
      k -n "$namespace" get "$target" --request-timeout=10s -o json > "$fixture/maintenance-authority-$resource.original.json"
      PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_authority_evidence.py" prepare "$fixture" "$resource"
      rc=0
      maintenance_authority_k -n "$namespace" patch -f "$fixture/maintenance-authority-$resource.handle.json" \
        --type=json --patch-file "$fixture/maintenance-authority-$resource.patch.json" --dry-run=server -o json \
        > "$fixture/maintenance-authority-$resource.response.json" 2> "$fixture/maintenance-authority-$resource.private" || rc=$?
      if PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_authority_evidence.py" allowed "$fixture" "$resource" "$rc" > "$fixture/maintenance-authority-check.private" 2>&1; then passed=true; break; fi
      [[ $rc == 1 && $attempt != 3 ]] || break
      k -n "$namespace" get "$target" --request-timeout=10s -o json > "$fixture/maintenance-authority-$resource.fresh.json"
      if ! PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_authority_evidence.py" refresh "$fixture" "$resource" "$rc" > "$fixture/maintenance-authority-check.private" 2>&1; then break; fi
      printf '%s\tstale-read-refreshed\n' "$resource-patch" >> "$results/maintenance-authority.tsv"
    done
    if [[ $passed != true ]]; then
      echo "Maintenance authority failed: $resource native patch dry-run; inspect private diagnostic and pinned ownership/readiness" >&2; return 1
    fi
    printf '%s\tallowed-dry-run\n' "$resource-patch" >> "$results/maintenance-authority.tsv"
  done
  PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_authority_evidence.py" plan "$fixture" > "$fixture/maintenance-authority-plan.tsv"
  while IFS=$'\t' read -r label verb plural group namespace name source; do
    [[ $label =~ ^[a-z-]+$ && ( $verb == patch || $verb == delete || $verb == create ) ]] || return 2
    file="$fixture/maintenance-authority-$label.handle.json"
    if [[ $source != handle ]]; then
      [[ $source == configmap || $source == secret || $source == job ]] || return 2
      file="$source_dir/authority-$source.yaml"
    fi
    rc=0
    case "$verb" in
      patch) maintenance_authority_k -n "$namespace" patch -f "$file" --type=merge -p '{"metadata":{"labels":{"perfeng.io/permission-probe":"dry-run"}}}' --dry-run=server -o name > "$fixture/maintenance-authority-$label.private" 2>&1 || rc=$? ;;
      delete) maintenance_authority_k -n "$namespace" delete -f "$file" --dry-run=server --wait=false -o name > "$fixture/maintenance-authority-$label.private" 2>&1 || rc=$? ;;
      create) maintenance_authority_k -n "$namespace" create -f "$file" --validate=false --dry-run=server -o name > "$fixture/maintenance-authority-$label.private" 2>&1 || rc=$? ;;
    esac
    if ! PYTHONPATH=. uv run --locked --offline python "$source_dir/maintenance_authority_evidence.py" denied "$fixture" "$label" "$verb" "$plural" "$group" "$namespace" "$name" "$rc" > "$fixture/maintenance-authority-check.private" 2>&1; then
      echo "Maintenance authority failed: $label did not prove exact Forbidden; inspect private diagnostic" >&2; return 1
    fi
    printf '%s\tforbidden\n' "$label" >> "$results/maintenance-authority.tsv"
  done < "$fixture/maintenance-authority-plan.tsv"
  rc=0
  target=$(maintenance_authority_k auth can-i create pods --subresource=exec -n perf-platform 2> "$fixture/maintenance-authority-exec.private") || rc=$?
  [[ $target == no && $rc == 1 ]] || { echo 'Maintenance authority failed: pod exec review mismatch' >&2; return 1; }
  printf 'pod-exec\tdenied-review\n' >> "$results/maintenance-authority.tsv"
  maintenance_snapshot "$fixture/maintenance-authority-after"
  root_state "$fixture/maintenance-authority-after"
  root_retained "$fixture/maintenance-before" "$fixture/maintenance-authority-after"
  for file in records secrets config git; do cmp "$fixture/maintenance-before.$file.json" "$fixture/maintenance-authority-after.$file.json"; done
  [[ $(k get namespace kube-system --request-timeout=10s -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
  printf 'suite\tverified\n' >> "$results/maintenance-authority.tsv"
  complete_stage
  current_stage=$previous
}
maintenance_authority_verify
