# Main-only chart/proxy/PVC fixture. Registration performs no cluster setup.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && ${bucket_server_pod:-} == seaweedfs-0 ]] || exit 2
declare -F k a s3 observer > /dev/null || exit 2

seaweedfs_chart_observe() {
  local label=$1 workflow
  workflow=$(a submit "$source_dir/observer-workflow.yaml" --entrypoint seaweedfs -o json | jq -er .metadata.name)
  timeout 630s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow"
  a get "$workflow" -o json > "$results/seaweedfs-chart-$label.workflow.json"
  jq -e '.status.phase=="Succeeded"' "$results/seaweedfs-chart-$label.workflow.json" > /dev/null
  jq -er '.status.nodes[.metadata.name].outputs.parameters[] | select(.name=="seaweedfs") | .value' \
    "$results/seaweedfs-chart-$label.workflow.json" > "$results/seaweedfs-chart-$label.observation.json"
  jq -e '.name=="seaweedfs" and .namespace=="perf-platform" and (.uid|length)>0 and
    (.generation|type)=="number" and .generation>=1 and .generation==.observedGeneration and
    .desired==1 and .replicas==1 and .ready==1 and .updated==1 and
    (.currentRevision|length)>0 and .currentRevision==.updateRevision' "$results/seaweedfs-chart-$label.observation.json" > /dev/null
}
seaweedfs_chart_peer() {
  local label=$1 address job rc
  address=$(k -n perf-platform get pod "$bucket_server_pod" -o jsonpath='{.status.podIP}')
  uv run --locked --offline python - "$source_dir/seaweedfs-peer.yaml" "$address" > "$fixture/seaweedfs-peer.json" <<'PY'
import ipaddress, json, sys, yaml
from pathlib import Path
job = yaml.safe_load(Path(sys.argv[1]).read_text())
job['spec']['template']['spec']['containers'][0]['env'] = [{'name':'TARGET_IP','value':str(ipaddress.ip_address(sys.argv[2]))}]
print(json.dumps(job))
PY
  job=$(k create -f "$fixture/seaweedfs-peer.json" -o json | jq -er .metadata.name)
  if k -n perf-platform wait --for=condition=Complete "job/$job" --timeout=90s; then rc=0; else rc=$?; fi
  k -n perf-platform get job "$job" -o json > "$results/seaweedfs-chart-$label.peer.json"
  k -n perf-platform get pods -l "batch.kubernetes.io/job-name=$job" -o json \
    | jq '[.items[] | {uid:.metadata.uid,containers:[.status.containerStatuses[]? | {name,exitCode:.state.terminated.exitCode,waitingReason:.state.waiting.reason}]}]' \
    > "$results/seaweedfs-chart-$label.peer-worker.json"
  k -n perf-platform logs "job/$job" -c peer --tail=30 --limit-bytes=8192 > "$fixture/seaweedfs-peer.log" 2>/dev/null || :
  grep -Eo 'Private listener was reachable: [0-9]{1,5}|TimeoutError|ConnectionRefusedError|ValueError' \
    "$fixture/seaweedfs-peer.log" | sort -u > "$results/seaweedfs-chart-$label.peer-signals.txt" || :
  [[ $rc == 0 ]]
  jq -e '.status.succeeded==1' "$results/seaweedfs-chart-$label.peer.json" > /dev/null
  jq -e 'length==1 and .[0].containers==[{name:"peer",exitCode:0,waitingReason:null}]' "$results/seaweedfs-chart-$label.peer-worker.json" > /dev/null
  grep -Fx 'Selected proxy reachable; private listeners refused' "$fixture/seaweedfs-peer.log" \
    > "$results/seaweedfs-chart-$label.peer-outcome.txt"
}
seaweedfs_chart_storage() {
  k -n perf-platform get pvc data-seaweedfs-0 -o json \
    | jq -e '{uid:.metadata.uid,volume:.spec.volumeName,phase:.status.phase,class:.spec.storageClassName} |
      select(.phase=="Bound" and .class=="standard" and (.uid|length)>0 and (.volume|length)>0)' > "$1"
}
seaweedfs_chart_setup() {
  echo 'SeaweedFS chart: private backend, actual S3 proxy and retained PVC'
  k get storageclass standard -o json \
    | jq -e '.provisioner=="rancher.io/local-path" and .volumeBindingMode=="WaitForFirstConsumer"' > /dev/null
  jq '.metadata.name="perfeng-s3-server-auth-v3" | .data={"s3.json":.data["s3.json"]}' \
    "$fixture/bucket-server-auth.json" > "$fixture/seaweedfs-chart-auth.json"
  k create -f "$fixture/seaweedfs-chart-auth.json"
  rm -f "$fixture/seaweedfs-chart-auth.json"
  helm template seaweedfs charts/seaweedfs --namespace perf-platform > "$fixture/seaweedfs-chart.yaml"
  uv run --locked --offline python - "$fixture/seaweedfs-chart.yaml" <<'PY'
import sys, yaml
from pathlib import Path
objects = list(yaml.safe_load_all(Path(sys.argv[1]).read_text()))
assert len(objects)==3 and {(o['kind'],o['metadata']['namespace'],o['metadata']['name']) for o in objects} == {
 ('StatefulSet','perf-platform','seaweedfs'),('Service','perf-platform','seaweedfs'),('ConfigMap','perf-platform','seaweedfs-proxy')}
PY
  k create -f "$fixture/seaweedfs-chart.yaml"
  k -n perf-platform rollout status statefulset/seaweedfs --timeout=360s
  k -n perf-platform wait --for=condition=Ready "pod/$bucket_server_pod" --timeout=120s
  k apply -f gitops/release-workflows/rbac/readiness
  seaweedfs_chart_observe first
  seaweedfs_chart_peer first
}
seaweedfs_chart_retention() {
  local before_uid after_uid pv name uid
  echo 'SeaweedFS chart: retain the external-state sentinel across native restart'
  before_uid=$(k -n perf-platform get pod "$bucket_server_pod" -o jsonpath='{.metadata.uid}')
  seaweedfs_chart_storage "$results/seaweedfs-chart-first.storage.json"
  pv=$(jq -er .volume "$results/seaweedfs-chart-first.storage.json")
  k get pv "$pv" -o json | jq '{uid:.metadata.uid,name:.metadata.name,claim:.spec.claimRef.uid}' > "$results/seaweedfs-chart-first.pv.json"
  k -n perf-platform get service seaweedfs -o json | jq -S . > "$results/seaweedfs-chart-first.service.json"
  k -n perf-platform get configmap seaweedfs-proxy -o json | jq -S . > "$results/seaweedfs-chart-first.proxy.json"
  k -n perf-platform get secrets perfeng-s3-server-auth-v3 perfeng-s3-auth bucket-ci-auth -o json | jq -S '.items|sort_by(.metadata.name)' > "$fixture/seaweedfs-secrets-before.json"
  s3 default head-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin > "$results/seaweedfs-chart-first.object.json"
  k -n perf-platform rollout restart statefulset/seaweedfs
  k -n perf-platform rollout status statefulset/seaweedfs --timeout=360s
  k -n perf-platform wait --for=condition=Ready "pod/$bucket_server_pod" --timeout=120s
  after_uid=$(k -n perf-platform get pod "$bucket_server_pod" -o jsonpath='{.metadata.uid}')
  [[ -n $before_uid && -n $after_uid && $before_uid != "$after_uid" ]]
  seaweedfs_chart_observe restarted
  seaweedfs_chart_peer restarted
  jq -s -e '.[0].uid==.[1].uid and .[1].generation>.[0].generation and .[0].currentRevision!=.[1].currentRevision' \
    "$results/seaweedfs-chart-first.observation.json" "$results/seaweedfs-chart-restarted.observation.json" > /dev/null
  seaweedfs_chart_storage "$results/seaweedfs-chart-restarted.storage.json"
  cmp "$results/seaweedfs-chart-first.storage.json" "$results/seaweedfs-chart-restarted.storage.json"
  k get pv "$pv" -o json | jq '{uid:.metadata.uid,name:.metadata.name,claim:.spec.claimRef.uid}' > "$results/seaweedfs-chart-restarted.pv.json"
  cmp "$results/seaweedfs-chart-first.pv.json" "$results/seaweedfs-chart-restarted.pv.json"
  k -n perf-platform get service seaweedfs -o json | jq -S . > "$results/seaweedfs-chart-restarted.service.json"
  cmp "$results/seaweedfs-chart-first.service.json" "$results/seaweedfs-chart-restarted.service.json"
  k -n perf-platform get configmap seaweedfs-proxy -o json | jq -S . > "$results/seaweedfs-chart-restarted.proxy.json"
  cmp "$results/seaweedfs-chart-first.proxy.json" "$results/seaweedfs-chart-restarted.proxy.json"
  k -n perf-platform get secrets perfeng-s3-server-auth-v3 perfeng-s3-auth bucket-ci-auth -o json | jq -S '.items|sort_by(.metadata.name)' > "$fixture/seaweedfs-secrets-after.json"
  cmp "$fixture/seaweedfs-secrets-before.json" "$fixture/seaweedfs-secrets-after.json"
  s3 default head-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin > "$results/seaweedfs-chart-restarted.object.json"
  cmp "$results/seaweedfs-chart-first.object.json" "$results/seaweedfs-chart-restarted.object.json"
  s3 default get-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin /tmp/seaweedfs-retained.bin > "$results/seaweedfs-chart-download.json"
  observer cat /tmp/seaweedfs-retained.bin > "$results/seaweedfs-chart-retained.bin"
  cmp "$fixture/external-sentinel.bin" "$results/seaweedfs-chart-retained.bin"
  jq -n --arg before "$before_uid" --arg after "$after_uid" '{before:$before,after:$after}' > "$results/seaweedfs-chart-pod-uids.json"
  for label in first restarted; do
    name=$(jq -er .metadata.name "$results/seaweedfs-chart-$label.workflow.json") uid=$(jq -er .metadata.uid "$results/seaweedfs-chart-$label.workflow.json")
    [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
  done
  echo 'SeaweedFS proxy/PVC retention passed; production IAM and recovery remain separate.'
}

seaweedfs_application_state() {
  local prefix=$1 pv
  seaweedfs_chart_storage "$prefix.storage.json"
  pv=$(jq -er .volume "$prefix.storage.json")
  k get pv "$pv" -o json | jq -S '{uid:.metadata.uid,name:.metadata.name,claim:.spec.claimRef.uid}' > "$prefix.pv.json"
  k -n perf-platform get service seaweedfs -o json | jq -S '{uid:.metadata.uid,spec}' > "$prefix.service.json"
  k -n perf-platform get configmap seaweedfs-proxy -o json \
    | jq -S '{uid:.metadata.uid,data,binaryData,immutable}' > "$prefix.proxy.json"
  k -n perf-platform get statefulset seaweedfs -o json \
    | jq -S '{uid:.metadata.uid,selector:.spec.selector,service:.spec.serviceName,claims:.spec.volumeClaimTemplates,
      labels:.spec.template.metadata.labels,podSpec:.spec.template.spec}' > "$prefix.statefulset.json"
  external_secrets "$prefix.credentials.json"
  s3 default head-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin > "$prefix.object.json"
  s3 default get-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin /tmp/seaweedfs-application-retained.bin > "$prefix.download.json"
  observer cat /tmp/seaweedfs-application-retained.bin > "$prefix.bytes"
  k -n argocd get applications perfeng-local-root perfeng-local-control-plane perfeng-local-postgres -o json \
    | jq -S '[.items[] | {uid:.metadata.uid,spec,skip:.metadata.annotations["argocd.argoproj.io/skip-reconcile"]}] | sort_by(.uid)' > "$prefix.other-applications.json"
}

seaweedfs_application_acceptance() {
  local revision application_uid label field first_pod before_pod name uid
  declare -F argocd_sync external_secrets postgres_application_state > /dev/null || return 2
  echo 'SeaweedFS Application: adopt the prepared proxy and retained object store'
  revision=$(jq -er '."selection-revision"' "$fixture/parent-parameters.json")
  PYTHONPATH=. uv run --locked --offline python "$source_dir/argocd_inputs.py" seaweedfs "$revision" "$fixture/seaweedfs-application.json"
  postgres_application_state "$fixture/seaweedfs-application-postgres-before"
  [[ $(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}') == "$(jq -er .after "$results/postgres-application-pod-uids.json")" ]]
  seaweedfs_application_state "$fixture/seaweedfs-application-before"
  cmp "$fixture/external-sentinel.bin" "$fixture/seaweedfs-application-before.bytes"
  before_pod=$(k -n perf-platform get pod "$bucket_server_pod" -o jsonpath='{.metadata.uid}')
  k create -f "$fixture/seaweedfs-application.json"
  application_uid=$(k -n argocd get application perfeng-local-seaweedfs -o jsonpath='{.metadata.uid}')
  k -n argocd wait application/perfeng-local-seaweedfs --for="jsonpath={.status.sync.revision}=$revision" --timeout=240s
  k -n argocd get application perfeng-local-seaweedfs -o json \
    | jq -e '(.status.conditions // [] | length)==0 and (.status.sync.status=="Synced" or .status.sync.status=="OutOfSync")' > /dev/null
  for label in first repeat corrected; do
    if [[ $label == corrected ]]; then
      k -n perf-platform patch service seaweedfs --type=json \
        -p '[{"op":"test","path":"/spec/ports/0/name","value":"s3"},{"op":"replace","path":"/spec/ports/0/name","value":"s3-ci-drift"}]'
      k -n argocd annotate application perfeng-local-seaweedfs argocd.argoproj.io/refresh=hard --overwrite
      k -n argocd wait application/perfeng-local-seaweedfs --for=jsonpath='{.status.sync.status}'=OutOfSync --timeout=180s
      k -n argocd get application perfeng-local-seaweedfs -o json \
        | jq -e '.spec.syncPolicy.automated=={enabled:false,prune:false,selfHeal:false} and .operation==null and
          ([.status.resources[] | select(.kind=="Service" and .name=="seaweedfs" and .status=="OutOfSync")]|length)==1' > /dev/null
      [[ $(k -n perf-platform get service seaweedfs -o jsonpath='{.spec.ports[0].name}') == s3-ci-drift ]]
      printf '%s\n' 'SeaweedFS Service drift observed without automatic repair' > "$results/seaweedfs-application-drift.outcome.txt"
    fi
    argocd_sync perfeng-local-seaweedfs "$label"
    k -n argocd get application perfeng-local-seaweedfs -o json > "$fixture/seaweedfs-application-current.json"
    PYTHONPATH=. uv run --locked --offline python "$source_dir/argocd_inputs.py" seaweedfs-converged \
      "$fixture/seaweedfs-application-current.json" "$application_uid" "$label" "$revision"
    jq '{uid:.metadata.uid,spec,sync:.status.sync,health:.status.health,phase:.status.operationState.phase,case:.status.operationState.operation.info}' \
      "$fixture/seaweedfs-application-current.json" > "$results/seaweedfs-application-$label.json"
    k -n perf-platform rollout status statefulset/seaweedfs --timeout=360s
    k -n perf-platform wait --for=condition=Ready "pod/$bucket_server_pod" --timeout=120s
    seaweedfs_chart_observe "adopt-$label"
    seaweedfs_chart_peer "adopt-$label"
    seaweedfs_application_state "$fixture/seaweedfs-application-$label"
    for field in storage pv service proxy statefulset credentials object other-applications; do
      cmp "$fixture/seaweedfs-application-before.$field.json" "$fixture/seaweedfs-application-$label.$field.json"
    done
    cmp "$fixture/seaweedfs-application-before.bytes" "$fixture/seaweedfs-application-$label.bytes"
    if [[ $label == first ]]; then
      first_pod=$(k -n perf-platform get pod "$bucket_server_pod" -o jsonpath='{.metadata.uid}')
    else
      [[ $(k -n perf-platform get pod "$bucket_server_pod" -o jsonpath='{.metadata.uid}') == "$first_pod" ]]
    fi
  done
  postgres_application_state "$fixture/seaweedfs-application-postgres-after"
  for field in storage pv service statefulset external server repository roles acl schema other-applications; do
    cmp "$fixture/seaweedfs-application-postgres-before.$field.json" "$fixture/seaweedfs-application-postgres-after.$field.json"
  done
  for field in canary sentinel; do
    cmp "$fixture/seaweedfs-application-postgres-before.$field.txt" "$fixture/seaweedfs-application-postgres-after.$field.txt"
  done
  [[ $(k -n perf-platform get pod "$database_pod" -o jsonpath='{.metadata.uid}') == "$(jq -er .after "$results/postgres-application-pod-uids.json")" ]]
  jq -n --arg before "$before_pod" --arg after "$first_pod" '{before:$before,after:$after}' > "$results/seaweedfs-application-pod-uids.json"
  for label in first repeat corrected; do
    name=$(jq -er .metadata.name "$results/seaweedfs-chart-adopt-$label.workflow.json")
    uid=$(jq -er .metadata.uid "$results/seaweedfs-chart-adopt-$label.workflow.json")
    [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
    name=$(jq -er .metadata.name "$results/seaweedfs-chart-adopt-$label.peer.json")
    uid=$(jq -er .metadata.uid "$results/seaweedfs-chart-adopt-$label.peer.json")
    [[ $(k -n perf-platform get job "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
  done
  k -n argocd get application perfeng-local-root -o json \
    | jq -e '.metadata.annotations["argocd.argoproj.io/skip-reconcile"]=="true" and .spec.syncPolicy.automated.enabled==false and .operation==null' > /dev/null
  [[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
  echo 'SeaweedFS Application adoption, proxy/private peers and storage/object retention passed; root remains skipped.'
}
