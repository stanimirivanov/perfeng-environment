#!/usr/bin/env bash
# Verify existing authority in owned CI. Mutations are server dry-run only.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ ${mode:-} == boundaries || ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k > /dev/null || exit 2

authority_k() {
  k --as=system:serviceaccount:argo-workflows:release-adoption \
    --as-group=system:serviceaccounts --as-group=system:serviceaccounts:argo-workflows \
    --as-group=system:authenticated "$@"
}

authority_denied() {
  local label=$1 verb=$2 resource=$3 namespace=$4 group=$5 rc message
  shift 5
  local diagnostic="$fixture/authority-$label.private"
  if authority_k "$@" > "$diagnostic" 2>&1; then
    echo "Adoption authority unexpectedly allowed: $label (server dry-run where applicable)" >&2
    return 1
  else rc=$?; fi
  message=$(< "$diagnostic")
  if [[ $rc != 1 || $message != *'Error from server (Forbidden)'* \
    || $message != *'User "system:serviceaccount:argo-workflows:release-adoption"'* \
    || $message != *"cannot $verb resource \"$resource\""* \
    || $message != *"in API group \"$group\""* \
    || ( $message != *"in namespace \"$namespace\""* && $message != *"in the namespace \"$namespace\""* ) ]]; then
    echo "Adoption authority did not prove the expected Forbidden boundary: $label; private diagnostic retained" >&2
    return 1
  fi
  printf '%s\tforbidden\n' "$label" >> "$results/adoption-authority.tsv"
}

verify_adoption_authority() {
  local receipt=$1 snapshot_function=$2 answer record review_rc
  [[ $receipt =~ ^receipt\.[0-9a-f]{32}\.[0-9a-f]{32}$ && -n ${cluster_uid:-} ]] || return 2
  declare -F "$snapshot_function" > /dev/null || return 2
  [[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}' 2> "$fixture/authority-cluster.private") == "$cluster_uid" ]] || return 2
  "$snapshot_function" "$fixture/authority-before.json" > "$fixture/authority-snapshot.private" 2>&1
  printf 'case\tdecision\n' > "$results/adoption-authority.tsv"
  for verb in get create; do
    review_rc=0
    answer=$(authority_k auth can-i "$verb" configmaps -n perfeng-release-ledger 2> "$fixture/authority-review.private") || review_rc=$?
    [[ $review_rc == 0 && $answer == yes ]] || { echo "Expected ledger authority unavailable: $verb" >&2; return 1; }
    printf 'ledger-%s\tallowed-review\n' "$verb" >> "$results/adoption-authority.tsv"
  done
  for record in "$receipt" perfeng-local-current; do
    authority_denied "$record-patch" patch configmaps perfeng-release-ledger '' \
      -n perfeng-release-ledger patch configmap "$record" --type=merge \
      -p '{"metadata":{"labels":{"perfeng.io/permission-probe":"dry-run"}}}' --dry-run=server -o name
    authority_denied "$record-delete" delete configmaps perfeng-release-ledger '' \
      -n perfeng-release-ledger delete configmap "$record" --dry-run=server --wait=false -o name
  done
  authority_denied workflow-config-create create configmaps argo-workflows '' \
    -n argo-workflows create configmap ci-adoption-denied --from-literal=probe=dry-run --dry-run=server -o name
  authority_denied secret-create create secrets argo-workflows '' \
    -n argo-workflows create secret generic ci-adoption-denied --from-literal=probe=non-secret --dry-run=server -o name
  # Name-only output prevents Secret values reaching a file even if access regresses.
  authority_denied secret-list list secrets argo-workflows '' -n argo-workflows get secrets -o name
  authority_denied job-create create jobs perf-platform batch \
    -n perf-platform create job ci-adoption-denied --image=python:3.13.9-slim-trixie@sha256:326df678c20c78d465db501563f3492d17c42a4afe33a1f2bf5406a1d56b0e86 \
    --dry-run=server -o name
  authority_denied deployment-patch patch deployments perf-platform apps \
    -n perf-platform patch deployment control-plane --type=merge \
    -p '{"metadata":{"labels":{"perfeng.io/permission-probe":"dry-run"}}}' --dry-run=server -o name
  for record in perfeng-local-root perfeng-local-control-plane; do
    authority_denied "$record-patch" patch applications argocd argoproj.io \
      -n argocd patch application "$record" --type=merge \
      -p '{"metadata":{"labels":{"perfeng.io/permission-probe":"dry-run"}}}' --dry-run=server -o name
  done
  # Connect subresources have no equivalent safe dry-run: review, never execute.
  review_rc=0
  answer=$(authority_k auth can-i create pods --subresource=exec -n perf-platform 2> "$fixture/authority-review.private") || review_rc=$?
  [[ $review_rc == 1 && $answer == no ]] || { echo 'Pod exec authority unexpectedly available or unverified' >&2; return 1; }
  printf 'pod-exec\tdenied-review\n' >> "$results/adoption-authority.tsv"
  "$snapshot_function" "$fixture/authority-after.json" > "$fixture/authority-snapshot.private" 2>&1
  cmp -s "$fixture/authority-before.json" "$fixture/authority-after.json" \
    || { echo 'Selected ledger changed across authority probes' >&2; return 1; }
  [[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}' 2> "$fixture/authority-cluster.private") == "$cluster_uid" ]] || return 2
  printf 'suite\tverified\n' >> "$results/adoption-authority.tsv"
  echo 'Adoption API refusals verified; server dry-run and unchanged ledger, no exec connection.'
}
