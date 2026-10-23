#!/usr/bin/env bash
# Trusted-main only: root-owned objects, with six child controllers excluded.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F argocd_sync postgres_application_state seaweedfs_application_state > /dev/null || exit 2

echo 'Root: reconcile selected objects with automatic sync disabled and six children skipped'
root_revision=$(jq -er '."selection-revision"' "$fixture/parent-parameters.json")
k kustomize gitops/environments/local > "$fixture/root-tree.yaml"
uv run --locked --offline python "$source_dir/root_inputs.py" prepare "$fixture/root-tree.yaml" "$fixture"
root_children=(perfeng-local-postgres perfeng-local-seaweedfs perfeng-local-sample-sut
  perfeng-local-native-search perfeng-local-resource-metrics perfeng-local-benchmark-authority)
for child in "${root_children[@]}"; do
  if k -n argocd get application "$child" --ignore-not-found -o name | grep -q .; then
    k -n argocd annotate application "$child" argocd.argoproj.io/skip-reconcile=true --overwrite
  else
    # Create with skip already present: there must be no activation window.
    k create -f "$fixture/$child.json"
  fi
done
root_child_snapshot() {
  k -n argocd get application perfeng-local-control-plane "${root_children[@]}" -o json > "$1"
}
root_child_snapshot "$fixture/root-children-before.json"
jq -e 'all(.items[]; .operation==null and (.status.operationState.phase!="Running") and
  (.status.operationState.phase!="Terminating") and
  (if .metadata.name=="perfeng-local-control-plane" then .metadata.annotations["argocd.argoproj.io/skip-reconcile"]==null
   else .metadata.annotations["argocd.argoproj.io/skip-reconcile"]=="true" end))' "$fixture/root-children-before.json" > /dev/null

root_state() {
  local prefix=$1
  postgres_application_state "$prefix.postgres"
  seaweedfs_application_state "$prefix.seaweedfs"
  k -n perf-platform get deployment/control-plane service/control-plane -o json \
    | jq -S '[.items[]|{kind,uid:.metadata.uid,spec}]|sort_by(.kind)' > "$prefix.server.json"
  k -n perf-platform get "pod/$database_pod" "pod/$bucket_server_pod" \
    "pod/$(jq -er '.[0].name' "$results/server-corrected.pods.json")" -o json \
    | jq -S '[.items[]|{uid:.metadata.uid,spec}]|sort_by(.uid)' > "$prefix.pods.json"
  # Detect activation in any namespace, including the newly root-created ones.
  k get deployments,statefulsets,daemonsets,cronjobs -A -o json \
    | jq -S '[.items[]|{kind,namespace:.metadata.namespace,name:.metadata.name,uid:.metadata.uid,spec}]|sort_by(.kind,.namespace,.name)' > "$prefix.workloads.json"
}
root_state "$fixture/root-before"
k -n argocd get application perfeng-local-root -o json \
  | jq -e --arg uid "$root_uid" '.metadata.uid==$uid and .spec.syncPolicy.automated=={enabled:false} and .operation==null' > /dev/null
k -n argocd annotate application perfeng-local-root argocd.argoproj.io/skip-reconcile-
# main remains canonical. Refuse a moving main rather than syncing a newer commit.
k -n argocd wait application/perfeng-local-root --for="jsonpath={.status.sync.revision}=$root_revision" --timeout=240s
for label in root-first root-repeat root-corrected; do
  if [[ $label == root-corrected ]]; then
    k patch namespace perf-sut --type=json \
      -p '[{"op":"test","path":"/metadata/labels/perfeng.io~1environment","value":"local"},{"op":"replace","path":"/metadata/labels/perfeng.io~1environment","value":"ci-drift"}]'
    k -n argocd annotate application perfeng-local-root argocd.argoproj.io/refresh=hard --overwrite
    k -n argocd wait application/perfeng-local-root --for=jsonpath='{.status.sync.status}'=OutOfSync --timeout=180s
    k -n argocd get application perfeng-local-root -o json \
      | jq -e '.spec.syncPolicy.automated=={enabled:false} and .operation==null and
        any(.status.resources[]; .kind=="Namespace" and .name=="perf-sut" and .status=="OutOfSync")' > /dev/null
    [[ $(k get namespace perf-sut -o jsonpath='{.metadata.labels.perfeng\.io/environment}') == ci-drift ]]
    printf '%s\n' 'Root Namespace label drift persisted until explicit manual sync' > "$results/root-drift.outcome.txt"
  fi
  argocd_sync perfeng-local-root "$label" "$root_revision"
  k -n argocd get application perfeng-local-root -o json > "$fixture/root-current.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" converged \
    "$fixture/root-current.json" "$root_uid" "$label" "$root_revision" "$fixture/root-tree.yaml"
  root_child_snapshot "$fixture/root-children-current.json"
  uv run --locked --offline python "$source_dir/root_inputs.py" children \
    "$fixture/root-children-current.json" "$fixture/root-tree.yaml" "$fixture/root-children-before.json"
  jq '{uid:.metadata.uid,spec,sync:.status.sync,health:.status.health,resources:.status.resources,
    phase:.status.operationState.phase,case:.status.operationState.operation.info}' "$fixture/root-current.json" > "$results/$label.application.json"
  jq '[.items[]|{name:.metadata.name,uid:.metadata.uid,skip:.metadata.annotations["argocd.argoproj.io/skip-reconcile"]}]' \
    "$fixture/root-children-current.json" > "$results/$label.children.json"
  root_state "$fixture/$label"
  for field in storage pv service statefulset external server repository roles acl schema; do
    cmp "$fixture/root-before.postgres.$field.json" "$fixture/$label.postgres.$field.json"
  done
  for field in canary sentinel; do
    cmp "$fixture/root-before.postgres.$field.txt" "$fixture/$label.postgres.$field.txt"
  done
  for field in storage pv service proxy statefulset credentials object; do
    cmp "$fixture/root-before.seaweedfs.$field.json" "$fixture/$label.seaweedfs.$field.json"
  done
  cmp "$fixture/root-before.seaweedfs.bytes" "$fixture/$label.seaweedfs.bytes"
  for field in server pods workloads; do
    cmp "$fixture/root-before.$field.json" "$fixture/$label.$field.json"
  done
done
[[ $(k get namespace perf-sut -o jsonpath='{.metadata.labels.perfeng\.io/environment}') == local ]]
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
echo 'Bounded root convergence and data retention passed; six skipped children do not establish full-tree health.'
