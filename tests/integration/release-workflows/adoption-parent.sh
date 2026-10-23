#!/usr/bin/env bash
# Full production adoption parent; records belong only to this owned CI cluster.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
[[ ${scenario:-} == initial || ${scenario:-} == receipt-only ]] || exit 2
declare -F k a root_state root_retained root_child_snapshot > /dev/null || exit 2

k apply -f gitops/release-workflows-bootstrap/ledger-namespace.yaml
k apply -f gitops/release-workflows-bootstrap/ledger-access.yaml
k apply -f gitops/release-workflows/rbac/adoption
k apply -f gitops/release-workflows-bootstrap/adoption-application-access.yaml
adoption_namespace=perfeng-release-ledger
adoption_digest=$(jq -er '."request-sha256"' "$fixture/parent-parameters.json")
adoption_hex=${adoption_digest#sha256:}
adoption_receipt="receipt.${adoption_hex:0:32}.${adoption_hex:32}"
[[ -z $(k -n "$adoption_namespace" get configmaps "$adoption_receipt" perfeng-local-current --ignore-not-found -o name) ]]
root_state "$fixture/adoption-before"
adoption_root_case=root-corrected
adoption_labels=()
adoption_baseline=first
adoption_origin=first

adoption_records() {
  k -n "$adoption_namespace" get configmaps "$adoption_receipt" perfeng-local-current --ignore-not-found -o json \
    | jq -S '.items|sort_by(.metadata.name)' > "$1"
}
adoption_retained() {
  local label=$1 root_mode=${2:-converged}
  root_state "$fixture/adoption-$label"
  root_retained "$fixture/adoption-before" "$fixture/adoption-$label"
  root_child_snapshot "$fixture/adoption-children.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" children \
    "$fixture/adoption-children.json" "$fixture/root-tree.yaml" "$fixture/root-children-before.json"
  k -n argocd get application perfeng-local-root -o json > "$fixture/adoption-root.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" "$root_mode" \
    "$fixture/adoption-root.json" "$root_uid" "$adoption_root_case" "$root_revision" "$fixture/root-tree.yaml"
}
adoption_case() {
  local label=$1 expected=$2 workflow=${3:-} rc node_id pod step=root root_mode=converged
  local parameters="$fixture/parent-parameters.json" requested_digest requested_hex
  case "$expected" in success|refusal|root-drift|receipt-only|missing-receipt|replaced-receipt|changed-request|upgrade-request) ;; *) return 2 ;; esac
  if [[ $expected == *-request ]]; then parameters="$fixture/$label-parameters.json"; fi
  echo "Complete release adoption: $label"
  if [[ -z $workflow ]]; then
    workflow=$(a submit --from workflowtemplate/perfeng-release-adopt-existing-v2 \
      --parameter-file "$parameters" -o json | jq -er .metadata.name)
  fi
  if timeout 1830s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$workflow"; then rc=0; else rc=$?; fi
  [[ $rc != 124 && $rc != 137 ]]
  a get "$workflow" -o json > "$results/adoption-parent-$label.workflow.json"
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" -o json \
    | jq '[.items[]|{name:.metadata.name,uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],
      account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]?|select(.name=="main")|.state.terminated.exitCode][0])}]' \
    > "$results/adoption-parent-$label.workers.json"
  jq -e 'length>0 and all(.[]; .account=="release-adoption" and .uid!=null and .nodeID!=null)' \
    "$results/adoption-parent-$label.workers.json" > /dev/null
  adoption_records "$fixture/adoption-parent-records.json"
  if [[ $expected == success || $expected == receipt-only || $expected == *-receipt || $expected == *-request ]]; then
    printf '%s\n' null > "$fixture/adoption-requested-receipt.json"
    if [[ $expected == *-request ]]; then
      requested_digest=$(jq -er '."request-sha256"' "$parameters")
      requested_hex=${requested_digest#sha256:}
      k -n "$adoption_namespace" get configmap "receipt.${requested_hex:0:32}.${requested_hex:32}" --ignore-not-found -o json \
        | jq -s '.[0] // null' > "$fixture/adoption-requested-receipt.json"
    fi
    k -n argocd get application perfeng-local-control-plane -o json > "$fixture/adoption-parent-application.json"
    k -n perf-platform get statefulset/postgres statefulset/seaweedfs deployment/control-plane service/control-plane -o json > "$fixture/adoption-parent-platform.json"
    jq -s --arg uid "$cluster_uid" '{application:.[0],platform:.[1].items,clusterUID:$uid,
      receipt:([.[2][]|select(.immutable==true)][0]),pointer:([.[2][]|select(.metadata.name=="perfeng-local-current")][0]),requestedReceipt:.[3]}' \
      "$fixture/adoption-parent-application.json" "$fixture/adoption-parent-platform.json" "$fixture/adoption-parent-records.json" \
      "$fixture/adoption-requested-receipt.json" > "$fixture/adoption-parent-api.json"
  fi
  if [[ $expected == success ]]; then
    [[ $rc == 0 ]]
    jq -e 'all(.[]; .exitCode==0)' "$results/adoption-parent-$label.workers.json" > /dev/null
    PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_parent_evidence.py" success "$fixture" "$results" "$label"
  else
    [[ $rc != 0 ]]
    if [[ $expected == root-drift ]]; then step=plan; root_mode=drifted; fi
    if [[ $expected == receipt-only ]]; then step=create-pointer; fi
    if [[ $expected == *-receipt || $expected == *-request ]]; then step=plan; fi
    node_id=$(jq -er --arg step "$step" '.status.nodes[]|select(.displayName==$step and .type=="Pod" and (.phase=="Failed" or .phase=="Error"))|.id' \
      "$results/adoption-parent-$label.workflow.json")
    pod=$(jq -er --arg id "$node_id" '.[]|select(.nodeID==$id)|.name' "$results/adoption-parent-$label.workers.json")
    k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 > "$fixture/adoption-parent-worker.log"
    if [[ $expected == *-receipt || $expected == *-request ]]; then
      PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_identity_evidence.py" "$expected" "$fixture" "$results" "$label"
    else
      PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_parent_evidence.py" "$expected" "$fixture" "$results" "$label"
    fi
    printf '%s\n' "Expected adoption refusal at $step after successful readiness; stored state verified" > "$results/adoption-parent-$label.outcome.txt"
  fi
  adoption_records "$fixture/adoption-$label.records.json"
  adoption_retained "$label" "$root_mode"
  adoption_labels+=("$label")
}
if [[ $scenario == receipt-only ]]; then
  # Count only the namespace's root CA plus one receipt; pointer admission must fail.
  k -n "$adoption_namespace" wait --for=create configmap/kube-root-ca.crt --timeout=60s
  k -n "$adoption_namespace" get configmaps -o json \
    | jq -e '([.items[].metadata.name]|sort)==["kube-root-ca.crt"]' > /dev/null
  k create -f "$source_dir/adoption-receipt-quota.yaml" -o json > "$fixture/adoption-quota-created.json"
  k -n "$adoption_namespace" wait resourcequota/ci-adoption-receipt-only \
    --for=jsonpath='{.status.hard.count/configmaps}'=2 --timeout=60s
  k -n "$adoption_namespace" wait resourcequota/ci-adoption-receipt-only \
    --for=jsonpath='{.status.used.count/configmaps}'=1 --timeout=60s
  adoption_case receipt-only receipt-only
  [[ -z $(k -n "$adoption_namespace" get configmap perfeng-local-current --ignore-not-found -o name) ]]
  k -n "$adoption_namespace" wait resourcequota/ci-adoption-receipt-only \
    --for=jsonpath='{.status.used.count/configmaps}'=2 --timeout=60s
  k -n "$adoption_namespace" get resourcequota ci-adoption-receipt-only -o json > "$fixture/adoption-quota-current.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_parent_evidence.py" quota-patch \
    "$fixture" "$results" receipt-only > "$fixture/adoption-quota-patch.json"
  k -n "$adoption_namespace" patch resourcequota ci-adoption-receipt-only --type=json --patch-file "$fixture/adoption-quota-patch.json"
  k -n "$adoption_namespace" wait resourcequota/ci-adoption-receipt-only \
    --for=jsonpath='{.status.hard.count/configmaps}'=3 --timeout=60s
  adoption_case recovered success
  jq -S '.[]|select(.immutable==true)' "$fixture/adoption-receipt-only.records.json" > "$fixture/adoption-partial.receipt.json"
  jq -S '.[]|select(.immutable==true)' "$fixture/adoption-recovered.records.json" > "$fixture/adoption-recovered.receipt.json"
  cmp "$fixture/adoption-partial.receipt.json" "$fixture/adoption-recovered.receipt.json"
  k -n "$adoption_namespace" wait resourcequota/ci-adoption-receipt-only \
    --for=jsonpath='{.status.used.count/configmaps}'=3 --timeout=60s
  k -n "$adoption_namespace" get resourcequota ci-adoption-receipt-only -o json \
    | jq '{uid:.metadata.uid,hard:.status.hard,used:.status.used}' > "$results/adoption-quota-recovered.json"
  adoption_baseline=recovered
  adoption_origin=receipt-only
else
  adoption_case first success
fi
adoption_case repeat success
cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-repeat.records.json"
cmp "$results/adoption-parent-$adoption_baseline.result.json" "$results/adoption-parent-repeat.result.json"

# Keep the control-plane read so nested readiness succeeds; only root access is revoked.
adoption_identity=system:serviceaccount:argo-workflows:release-adoption
k -n argocd patch role release-adoption --type=json \
  -p '[{"op":"test","path":"/rules/0/resourceNames","value":["perfeng-local-root","perfeng-local-control-plane"]},{"op":"replace","path":"/rules/0/resourceNames","value":["perfeng-local-control-plane"]}]'
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-root -n argocd --as "$adoption_identity" || :) == no ]]
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-control-plane -n argocd --as "$adoption_identity" || :) == yes ]]
adoption_case missing-root-read refusal
cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-missing-root-read.records.json"
k apply -f gitops/release-workflows-bootstrap/adoption-application-access.yaml
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-root -n argocd --as "$adoption_identity" || :) == yes ]]
adoption_case corrected success
cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-corrected.records.json"
cmp "$results/adoption-parent-$adoption_baseline.result.json" "$results/adoption-parent-corrected.result.json"

# A successful readiness check cannot authorize adoption while the real root drifts.
k patch namespace perf-sut --type=json \
  -p '[{"op":"test","path":"/metadata/labels/perfeng.io~1environment","value":"local"},{"op":"replace","path":"/metadata/labels/perfeng.io~1environment","value":"ci-drift"}]'
k -n argocd annotate application perfeng-local-root argocd.argoproj.io/refresh=hard --overwrite
k -n argocd wait application/perfeng-local-root --for=jsonpath='{.status.sync.status}'=OutOfSync --timeout=180s
adoption_case root-drift root-drift
[[ $(k get namespace perf-sut -o jsonpath='{.metadata.labels.perfeng\.io/environment}') == ci-drift ]]
cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-root-drift.records.json"
argocd_sync perfeng-local-root adoption-root-corrected "$root_revision"
adoption_root_case=adoption-root-corrected
[[ $(k get namespace perf-sut -o jsonpath='{.metadata.labels.perfeng\.io/environment}') == local ]]
adoption_case root-corrected success
cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-root-corrected.records.json"
cmp "$results/adoption-parent-$adoption_baseline.result.json" "$results/adoption-parent-root-corrected.result.json"

# Hold the real mutex before submitting both retries, making contention observable.
holder=$(a submit "$source_dir/adoption-lock-workflow.yaml" -o json | jq -er .metadata.name)
k -n argo-workflows wait "workflow/$holder" --for="jsonpath={.status.nodes.$holder.phase}=Running" --timeout=90s
concurrent_a=$(a submit --from workflowtemplate/perfeng-release-adopt-existing-v2 \
  --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
concurrent_b=$(a submit --from workflowtemplate/perfeng-release-adopt-existing-v2 \
  --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
for workflow in "$concurrent_a" "$concurrent_b"; do
  k -n argo-workflows wait "workflow/$workflow" \
    --for=jsonpath='{.status.synchronization.mutex.waiting[0].mutex}'=argo-workflows/mutex/perfeng-local-external-state --timeout=60s
done
a get "$holder" -o json > "$results/adoption-lock-held.workflow.json"
k -n argo-workflows get workflows "$concurrent_a" "$concurrent_b" -o json \
  | jq '.items' > "$results/adoption-lock-queued.workflows.json"
k -n argo-workflows get pods -l "workflows.argoproj.io/workflow in ($holder,$concurrent_a,$concurrent_b)" -o json \
  | jq '[.items[]|{uid:.metadata.uid}]' > "$results/adoption-lock-queued.workers.json"
PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_parent_evidence.py" waiting "$fixture" "$results" concurrent
adoption_records "$fixture/adoption-queued.records.json"
cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-queued.records.json"
adoption_retained queued
a resume "$holder"
timeout 90s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$holder"
a get "$holder" -o json > "$results/adoption-lock-holder.workflow.json"
adoption_case concurrent-a success "$concurrent_a"
adoption_case concurrent-b success "$concurrent_b"
for label in concurrent-a concurrent-b; do
  cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-$label.records.json"
  cmp "$results/adoption-parent-$adoption_baseline.result.json" "$results/adoption-parent-$label.result.json"
done
PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_parent_evidence.py" serialized "$fixture" "$results" concurrent

cp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-identity-baseline.json"
for label in changed-request upgrade-request; do
  cp "$fixture/$label-request.json" "$results/$label-request.json"
  cp "$fixture/$label-parameters.json" "$results/$label-parameters.json"
done
adoption_case changed-request changed-request
cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-changed-request.records.json"
adoption_case upgrade-request upgrade-request
cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-upgrade-request.records.json"

source "$source_dir/adoption-authority.sh"
verify_adoption_authority "$adoption_receipt" adoption_records
for label in "${adoption_labels[@]}"; do
  name=$(jq -er .metadata.name "$results/adoption-parent-$label.workflow.json")
  uid=$(jq -er .metadata.uid "$results/adoption-parent-$label.workflow.json")
  [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
done
jq -s --argjson expected "${#adoption_labels[@]}" -e 'length==$expected and (map(.metadata.uid)|unique|length)==$expected' "$results"/adoption-parent-*.workflow.json > /dev/null
name=$(jq -er .metadata.name "$results/adoption-parent-$adoption_origin.workflow.json")
k -n argo-workflows delete workflow "$name" --cascade=foreground --wait=true --timeout=90s
adoption_records "$fixture/adoption-retained.records.json"
cmp "$fixture/adoption-$adoption_baseline.records.json" "$fixture/adoption-retained.records.json"
adoption_retained retained
printf '%s\n' 'Original receipt/pointer bytes, UIDs and provenance survived original Workflow deletion' > "$results/adoption-parent-retention.outcome.txt"
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
# Owner-approved destructive faults start only after the completed retention proof.
cp "$fixture/adoption-retained.records.json" "$fixture/adoption-identity-baseline.json"
adoption_records "$fixture/adoption-identity-current.json"
PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_identity_evidence.py" prepare "$fixture" "$results" "$adoption_baseline"
k delete --raw "/api/v1/namespaces/perfeng-release-ledger/configmaps/$adoption_receipt" \
  -f "$fixture/adoption-receipt-delete.json" > "$fixture/adoption-receipt-deleted.json"
k -n "$adoption_namespace" wait --for=delete "configmap/$adoption_receipt" --timeout=60s
adoption_records "$fixture/adoption-missing-before.records.json"
adoption_case missing-receipt missing-receipt
cmp "$fixture/adoption-missing-before.records.json" "$fixture/adoption-missing-receipt.records.json"
if [[ $scenario == receipt-only ]]; then
  k -n "$adoption_namespace" wait resourcequota/ci-adoption-receipt-only \
    --for=jsonpath='{.status.used.count/configmaps}'=2 --timeout=60s
fi
k create -f "$fixture/adoption-receipt-recreate.json" -o json > "$fixture/adoption-receipt-recreated.json"
adoption_records "$fixture/adoption-replaced-before.records.json"
adoption_case replaced-receipt replaced-receipt
cmp "$fixture/adoption-replaced-before.records.json" "$fixture/adoption-replaced-receipt.records.json"
jq -s --argjson expected "${#adoption_labels[@]}" -e 'length==$expected and (map(.metadata.uid)|unique|length)==$expected' "$results"/adoption-parent-*.workflow.json > /dev/null
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]

echo 'Complete adoption identity refusals accepted; promotion, recovery and retirement remain separate.'
