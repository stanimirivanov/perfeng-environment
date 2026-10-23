#!/usr/bin/env bash
# Full production adoption parent; records belong only to this owned CI cluster.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
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

adoption_records() {
  k -n "$adoption_namespace" get configmaps "$adoption_receipt" perfeng-local-current -o json \
    | jq -S '.items|sort_by(.metadata.name)' > "$1"
}
adoption_retained() {
  local label=$1
  root_state "$fixture/adoption-$label"
  root_retained "$fixture/adoption-before" "$fixture/adoption-$label"
  root_child_snapshot "$fixture/adoption-children.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" children \
    "$fixture/adoption-children.json" "$fixture/root-tree.yaml" "$fixture/root-children-before.json"
  k -n argocd get application perfeng-local-root -o json > "$fixture/adoption-root.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" converged \
    "$fixture/adoption-root.json" "$root_uid" root-corrected "$root_revision" "$fixture/root-tree.yaml"
}
adoption_case() {
  local label=$1 expected=$2 workflow rc node_id pod
  echo "Complete release adoption: $label"
  workflow=$(a submit --from workflowtemplate/perfeng-release-adopt-existing-v2 \
    --parameter-file "$fixture/parent-parameters.json" -o json | jq -er .metadata.name)
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
  if [[ $expected == success ]]; then
    [[ $rc == 0 ]]
    jq -e 'all(.[]; .exitCode==0)' "$results/adoption-parent-$label.workers.json" > /dev/null
    k -n argocd get application perfeng-local-control-plane -o json > "$fixture/adoption-parent-application.json"
    k -n perf-platform get statefulset/postgres statefulset/seaweedfs deployment/control-plane service/control-plane -o json > "$fixture/adoption-parent-platform.json"
    adoption_records "$fixture/adoption-parent-records.json"
    jq -s --arg uid "$cluster_uid" '{application:.[0],platform:.[1].items,clusterUID:$uid,
      receipt:(.[2][]|select(.immutable==true)),pointer:(.[2][]|select(.metadata.name=="perfeng-local-current"))}' \
      "$fixture/adoption-parent-application.json" "$fixture/adoption-parent-platform.json" "$fixture/adoption-parent-records.json" > "$fixture/adoption-parent-api.json"
    PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_parent_evidence.py" success "$fixture" "$results" "$label"
  else
    [[ $rc != 0 ]]
    node_id=$(jq -er '.status.nodes[]|select(.displayName=="root" and .type=="Pod" and (.phase=="Failed" or .phase=="Error"))|.id' \
      "$results/adoption-parent-$label.workflow.json")
    pod=$(jq -er --arg id "$node_id" '.[]|select(.nodeID==$id)|.name' "$results/adoption-parent-$label.workers.json")
    k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 > "$fixture/adoption-parent-worker.log"
    PYTHONPATH=. uv run --locked --offline python "$source_dir/adoption_parent_evidence.py" refusal "$fixture" "$results" "$label"
    printf '%s\n' 'Expected root-read Forbidden after successful readiness; ledger untouched' > "$results/adoption-parent-$label.outcome.txt"
  fi
  adoption_records "$fixture/adoption-$label.records.json"
  adoption_retained "$label"
}
adoption_case first success
adoption_case repeat success
cmp "$fixture/adoption-first.records.json" "$fixture/adoption-repeat.records.json"
cmp "$results/adoption-parent-first.result.json" "$results/adoption-parent-repeat.result.json"

# Keep the control-plane read so nested readiness succeeds; only root access is revoked.
adoption_identity=system:serviceaccount:argo-workflows:release-adoption
k -n argocd patch role release-adoption --type=json \
  -p '[{"op":"test","path":"/rules/0/resourceNames","value":["perfeng-local-root","perfeng-local-control-plane"]},{"op":"replace","path":"/rules/0/resourceNames","value":["perfeng-local-control-plane"]}]'
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-root -n argocd --as "$adoption_identity" || :) == no ]]
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-control-plane -n argocd --as "$adoption_identity" || :) == yes ]]
adoption_case missing-root-read refusal
cmp "$fixture/adoption-first.records.json" "$fixture/adoption-missing-root-read.records.json"
k apply -f gitops/release-workflows-bootstrap/adoption-application-access.yaml
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-root -n argocd --as "$adoption_identity" || :) == yes ]]
adoption_case corrected success
cmp "$fixture/adoption-first.records.json" "$fixture/adoption-corrected.records.json"
cmp "$results/adoption-parent-first.result.json" "$results/adoption-parent-corrected.result.json"

for permission in 'patch configmaps perfeng-release-ledger' 'delete configmaps perfeng-release-ledger' \
  'create configmaps argo-workflows' 'create jobs perf-platform' 'list secrets argo-workflows' 'create secrets argo-workflows' \
  'patch deployments perf-platform' 'patch applications.argoproj.io argocd'; do
  read -r verb resource namespace <<< "$permission"
  [[ $(k auth can-i "$verb" "$resource" -n "$namespace" --as "$adoption_identity" || :) == no ]]
done
[[ $(k auth can-i create pods --subresource=exec -n perf-platform --as "$adoption_identity" || :) == no ]]
for label in first repeat missing-root-read corrected; do
  name=$(jq -er .metadata.name "$results/adoption-parent-$label.workflow.json")
  uid=$(jq -er .metadata.uid "$results/adoption-parent-$label.workflow.json")
  [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
done
jq -s -e 'length==4 and (map(.metadata.uid)|unique|length)==4' "$results"/adoption-parent-*.workflow.json > /dev/null
name=$(jq -er .metadata.name "$results/adoption-parent-first.workflow.json")
k -n argo-workflows delete workflow "$name" --cascade=foreground --wait=true --timeout=90s
adoption_records "$fixture/adoption-retained.records.json"
cmp "$fixture/adoption-first.records.json" "$fixture/adoption-retained.records.json"
adoption_retained retained
printf '%s\n' 'Original receipt/pointer bytes, UIDs and provenance survived original Workflow deletion' > "$results/adoption-parent-retention.outcome.txt"
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
echo 'Complete existing-release adoption accepted; promotion, recovery and retirement remain separate.'
