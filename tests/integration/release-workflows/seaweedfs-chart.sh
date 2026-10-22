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
