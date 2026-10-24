#!/usr/bin/env bash
# Owned CI only. Every mutating probe is server dry-run, even after a regression.
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

authority_begin() {
  authority_report="$results/$1.tsv"
  authority_failures=0
  printf 'case\tdecision\texit\tmarker\tsubject\tverb\tresource\tgroup\tnamespace\n' > "$authority_report"
}

authority_denied() {
  local label=$1 verb=$2 resource=$3 namespace=$4 group=$5 rc=0 message
  local marker=0 subject=0 operation=0 kind=0 api_group=0 target=0 decision=refusal-mismatch
  shift 5
  local diagnostic="$fixture/authority-$label.private"
  authority_k "$@" > "$diagnostic" 2>&1 || rc=$?
  message=$(< "$diagnostic")
  [[ $message != *'Error from server (Forbidden)'* ]] || marker=1
  [[ $message != *'User "system:serviceaccount:argo-workflows:release-adoption"'* ]] || subject=1
  [[ $message != *"cannot $verb resource "* ]] || operation=1
  [[ $message != *"resource \"$resource\""* ]] || kind=1
  [[ $message != *"in API group \"$group\""* ]] || api_group=1
  [[ $message != *"in the namespace \"$namespace\""* && $message != *"in namespace \"$namespace\""* ]] || target=1
  if [[ $rc == 1 && $marker$subject$operation$kind$api_group$target == 111111 ]]; then
    decision=forbidden
  else
    [[ $rc != 0 ]] || decision=unexpected-allow
    authority_failures=$((authority_failures + 1))
    echo "Adoption authority: $label decision=$decision exit=$rc matches=$marker$subject$operation$kind$api_group$target (marker,subject,verb,resource,group,namespace)" >&2
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$label" "$decision" "$rc" "$marker" "$subject" "$operation" "$kind" "$api_group" "$target" >> "$authority_report"
  # Collect independent safe probes; the suite gate blocks all downstream work.
}

authority_review() {
  local label=$1 expected=$2 rc=0 answer decision=review-mismatch
  shift 2
  answer=$(authority_k auth can-i "$@" 2> "$fixture/authority-$label.private") || rc=$?
  if [[ $expected == yes && $answer == yes && $rc == 0 ]]; then decision=allowed-review;
  elif [[ $expected == no && $answer == no && $rc == 1 ]]; then decision=denied-review;
  else
    authority_failures=$((authority_failures + 1))
    echo "Adoption authority: $label decision=review-mismatch exit=$rc" >&2
  fi
  printf '%s\t%s\t%s\n' "$label" "$decision" "$rc" >> "$authority_report"
}

authority_create_probes() {
  local spec name resource namespace group
  for spec in 'configmap configmaps argo-workflows core' 'secret secrets argo-workflows core' 'job jobs perf-platform batch'; do
    read -r name resource namespace group <<< "$spec"
    [[ $group != core ]] || group=''
    authority_denied "$name-create" create "$resource" "$namespace" "$group" \
      -n "$namespace" create -f "$source_dir/authority-$name.yaml" \
      --validate=false --field-manager=kubectl-create --dry-run=server -o name
  done
  authority_denied secret-list list secrets argo-workflows '' -n argo-workflows get secrets -o name
}

authority_finish() {
  if [[ $authority_failures != 0 ]]; then
    printf 'suite\tfailed\n' >> "$authority_report"
    echo "Adoption authority failed: $authority_failures cases; inspect ${authority_report##*/}" >&2
    return 1
  fi
  printf 'suite\tverified\n' >> "$authority_report"
}

verify_adoption_create_authority() {
  authority_begin adoption-authority-smoke
  authority_create_probes
  authority_finish
}

verify_adoption_authority() {
  local receipt=$1 snapshot_function=$2 record
  [[ $receipt =~ ^receipt\.[0-9a-f]{32}\.[0-9a-f]{32}$ && -n ${cluster_uid:-} ]] || return 2
  declare -F "$snapshot_function" > /dev/null || return 2
  [[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}' 2> "$fixture/authority-cluster.private") == "$cluster_uid" ]] || return 2
  "$snapshot_function" "$fixture/authority-before.json" > "$fixture/authority-snapshot.private" 2>&1
  authority_begin adoption-authority
  authority_review ledger-get yes get configmaps -n perfeng-release-ledger
  authority_review ledger-create yes create configmaps -n perfeng-release-ledger
  for record in "$receipt" perfeng-local-current; do
    authority_denied "$record-patch" patch configmaps perfeng-release-ledger '' \
      -n perfeng-release-ledger patch configmap "$record" --type=merge \
      -p '{"metadata":{"labels":{"perfeng.io/permission-probe":"dry-run"}}}' --dry-run=server -o name
    authority_denied "$record-delete" delete configmaps perfeng-release-ledger '' \
      -n perfeng-release-ledger delete configmap "$record" --dry-run=server --wait=false -o name
  done
  authority_create_probes
  authority_denied deployment-patch patch deployments perf-platform apps \
    -n perf-platform patch deployment control-plane --type=merge \
    -p '{"metadata":{"labels":{"perfeng.io/permission-probe":"dry-run"}}}' --dry-run=server -o name
  for record in perfeng-local-root perfeng-local-control-plane; do
    authority_denied "$record-patch" patch applications argocd argoproj.io \
      -n argocd patch application "$record" --type=merge \
      -p '{"metadata":{"labels":{"perfeng.io/permission-probe":"dry-run"}}}' --dry-run=server -o name
  done
  authority_review pod-exec no create pods --subresource=exec -n perf-platform
  "$snapshot_function" "$fixture/authority-after.json" > "$fixture/authority-snapshot.private" 2>&1
  if ! cmp -s "$fixture/authority-before.json" "$fixture/authority-after.json"; then
    printf 'ledger-snapshot\tchanged\n' >> "$authority_report"
    authority_failures=$((authority_failures + 1))
  fi
  [[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}' 2> "$fixture/authority-cluster.private") == "$cluster_uid" ]] || return 2
  authority_finish
  echo 'Adoption API refusals verified; unchanged selected ledger and no exec connection.'
}
