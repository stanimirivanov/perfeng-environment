#!/usr/bin/env bash
# CI only: selected server/chart/package, not Argo CD or full release readiness.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k a sql migration_snapshot external_secrets role_snapshot > /dev/null || exit 2
k -n perf-platform get deployment control-plane -o json > "$fixture/server-placeholder.json"
jq -e '.spec.replicas==0 and (.spec.template.spec.containers|length)==1 and .spec.template.spec.containers[0].name=="never-started-fixture"' "$fixture/server-placeholder.json" > /dev/null
deployment_uid=$(jq -er .metadata.uid "$fixture/server-placeholder.json")
migration_snapshot "$results/server-schema-before.json"
external_secrets "$fixture/server-external-before.json"
role_snapshot "$fixture/server-roles-before.json"
k -n perf-platform get secrets perfeng-control-plane-database perfeng-s3-auth migration-ci-registry -o json > "$fixture/server-input-secrets.json"
if [[ ${scenario:-} == initial ]]; then
  [[ -s $fixture/schema-install.pull-before.json ]]
  k -n perf-platform get secret perfeng-control-plane-registry -o json > "$fixture/server-pull-current.json"
fi
source_checkout=.ci/perfeng-k8s
PYTHONPATH=. uv run --locked --offline python "$source_dir/server_inputs.py" prepare "$source_checkout" "$fixture"
helm template control-plane "$source_checkout/charts/control-plane" --namespace perf-platform \
  --values "$fixture/server-values.yaml" > "$fixture/server-chart.yaml"
package_path=$(jq -er '.deployment.runtimePackage.path' deployments/local-control-plane.json)
k kustomize "$source_checkout/$package_path" > "$fixture/server-package.yaml"
PYTHONPATH=. uv run --locked --offline python "$source_dir/server_inputs.py" rendered "$source_checkout" "$fixture"
k create -f "$fixture/server-secrets.json"
rm -f "$fixture/server-secrets.json" "$fixture/server-input-secrets.json"
# This sole node belongs to the already owned kind fixture; no ambient selection.
k label node perfeng-local-control-plane workload=control-plane
k apply -f gitops/release-workflows/rbac/readiness
# First apply without a last-applied annotation can merge the old differently
# named container into the selected chart. PUT the complete selected Deployment
# using the observed UID/resourceVersion, preserving identity and rejecting races.
jq '{apiVersion:"v1",kind:"List",items:[.items[] | select(.kind!="Deployment")]}' "$fixture/server-rendered.json" | k apply -f -
jq --slurpfile before "$fixture/server-placeholder.json" '.items[] | select(.kind=="Deployment") | .metadata.uid=$before[0].metadata.uid | .metadata.resourceVersion=$before[0].metadata.resourceVersion' \
  "$fixture/server-rendered.json" | k replace -f -
server_rollout() {
  if ! k -n perf-platform rollout status deployment/control-plane --timeout=180s; then
    k -n perf-platform get pods -l app.kubernetes.io/instance=control-plane -o json \
      | jq '[.items[] | {uid:.metadata.uid,containers:[.status.containerStatuses[]? | {name,image,ready,restartCount,waitingReason:.state.waiting.reason,exitCode:.state.terminated.exitCode}]}]' \
      > "$results/server-rollout-failure.json"
    return 1
  fi
}
server_rollout

server_inputs_snapshot() {
  k -n perf-platform get secrets perfeng-control-plane-runtime perfeng-control-plane-authentication perfeng-control-plane-registry -o json \
    | jq -S '.items | sort_by(.metadata.name)' > "$1"
}
server_config_snapshot() {
  k -n perf-platform get configmaps "$(jq -er .deployment.runtimeResources.runtimeConfigMap deployments/local-control-plane.json)" \
    "$(jq -er .deployment.runtimeResources.registryConfigMap deployments/local-control-plane.json)" -o json \
    | jq -S '.items | sort_by(.metadata.name)' > "$1"
}
server_inputs_snapshot "$fixture/server-inputs-before.json"
server_config_snapshot "$results/server-config-before.json"
server_case() {
  local label=$1 entrypoint=$2 workflow rc
  echo "Selected server case: $label"
  workflow=$(a submit "$source_dir/server-workflow.yaml" --entrypoint "$entrypoint" -o json | jq -er .metadata.name)
  if timeout 450s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow"; then rc=0; else rc=$?; fi
  [[ $rc != 124 && $rc != 137 ]]
  a get "$workflow" -o json > "$results/server-$label.workflow.json"
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" -o json \
    | jq '[.items[] | {uid:.metadata.uid,main:[.status.containerStatuses[]? | select(.name=="main") | {exitCode:.state.terminated.exitCode,waitingReason:.state.waiting.reason}]}]' > "$results/server-$label.worker.json"
  k -n argo-workflows logs -l "workflows.argoproj.io/workflow=$workflow" -c main --tail=20 --limit-bytes=8192 > "$fixture/server-http.log"
  if [[ $entrypoint == observe ]]; then
    [[ $rc == 0 ]]
    jq -e '.status.phase == "Succeeded"' "$results/server-$label.workflow.json" > /dev/null
    grep -Fx 'Selected server operational endpoints verified' "$fixture/server-http.log" > "$results/server-$label.outcome.txt"
    PYTHONPATH=. uv run --locked --offline python "$source_dir/server_inputs.py" observed "$results/server-$label.workflow.json" "$deployment_uid"
    k -n perf-platform get pods -l app.kubernetes.io/instance=control-plane -o json \
      | jq -e '[.items[] | {uid:.metadata.uid,name:.metadata.name,image:.spec.containers[0].image,status:.status.containerStatuses[0]}] | length == 1 and .[0].status.ready == true' > /dev/null
    k -n perf-platform get pods -l app.kubernetes.io/instance=control-plane -o json \
      | jq '[.items[] | {uid:.metadata.uid,name:.metadata.name,image:.spec.containers[0].image}]' > "$results/server-$label.pods.json"
  else
    [[ $rc != 0 ]]
    jq -e '.status.phase == "Failed" or .status.phase == "Error"' "$results/server-$label.workflow.json" > /dev/null
    jq -e 'length == 1 and .[0].main == [{exitCode:2,waitingReason:null}]' "$results/server-$label.worker.json" > /dev/null
    grep -Fx 'Server HTTP refused: operational endpoint failed: livez' "$fixture/server-http.log" > "$results/server-$label.outcome.txt"
  fi
}
server_case first observe
server_case repeat observe
cmp "$results/server-first.pods.json" "$results/server-repeat.pods.json"
server_pod=$(jq -er '.[0].name' "$results/server-first.pods.json")
k -n perf-platform scale deployment control-plane --replicas=0
k -n perf-platform wait --for=delete "pod/$server_pod" --timeout=120s
[[ $(k -n perf-platform get pods -l app.kubernetes.io/instance=control-plane -o json | jq '.items|length') == 0 ]]
server_case stopped http
k -n perf-platform scale deployment control-plane --replicas=1
server_rollout
server_case corrected observe
for label in first repeat corrected; do
  jq -S '.status.nodes[.metadata.name].outputs.parameters[] | select(.name=="service") | .value | fromjson' \
    "$results/server-$label.workflow.json" > "$results/server-$label.service.json"
done
cmp "$results/server-first.service.json" "$results/server-repeat.service.json"
cmp "$results/server-first.service.json" "$results/server-corrected.service.json"
[[ $(jq -er '.[0].uid' "$results/server-first.pods.json") != "$(jq -er '.[0].uid' "$results/server-corrected.pods.json")" ]]
server_inputs_snapshot "$fixture/server-inputs-after.json"
cmp "$fixture/server-inputs-before.json" "$fixture/server-inputs-after.json"
server_config_snapshot "$results/server-config-after.json"
cmp "$results/server-config-before.json" "$results/server-config-after.json"
migration_snapshot "$results/server-schema-after.json"
cmp "$results/server-schema-before.json" "$results/server-schema-after.json"
external_secrets "$fixture/server-external-after.json"
cmp "$fixture/server-external-before.json" "$fixture/server-external-after.json"
role_snapshot "$fixture/server-roles-after.json"
cmp "$fixture/server-roles-before.json" "$fixture/server-roles-after.json"
[[ $(sql -c 'SELECT value FROM public.migration_ci_sentinel WHERE id=1;') == migration-image-parity-v1 ]]
sql -c "SELECT oid::text FROM pg_class WHERE oid='public.migration_ci_sentinel'::regclass;" > "$results/server-sentinel-oid.txt"
cmp "$results/migration-image-sentinel-oid-after.txt" "$results/server-sentinel-oid.txt"
[[ $(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}') == "$postgres_uid" ]]
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
jq -s -e 'length==4 and ([.[].metadata.uid]|unique|length)==4' "$results"/server-*.workflow.json > /dev/null
for label in first repeat stopped corrected; do
  name=$(jq -er .metadata.name "$results/server-$label.workflow.json") uid=$(jq -er .metadata.uid "$results/server-$label.workflow.json")
  [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
done
[[ $(k -n perf-platform get pod "$bucket_server_pod" -o jsonpath='{.metadata.uid}') == "$(jq -er .after "$results/seaweedfs-chart-pod-uids.json")" ]]
echo 'Selected server/native/HTTP parity passed; full readiness and Argo CD remain separate.'
