# CI-only native resource observations; no application health or release approval.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -n ${fixture:-} && -f $fixture/owned && ${KUBECONFIG:-} == "$fixture/kubeconfig" ]] || exit 2
declare -F k a > /dev/null || exit 2

k apply -f gitops/release-workflows-bootstrap/readiness-application-access.yaml
for workload in postgres seaweedfs; do
  uv run --locked --offline python - "$source_dir/observer-statefulset.yaml" "$workload" > "$fixture/observer-statefulset.json" <<'PY'
import json, sys, yaml
from pathlib import Path
value = yaml.safe_load(Path(sys.argv[1]).read_text())
name = sys.argv[2]
assert name in {"postgres", "seaweedfs"}
value["metadata"]["name"] = value["spec"]["serviceName"] = name
for labels in (value["spec"]["selector"]["matchLabels"], value["spec"]["template"]["metadata"]["labels"]):
    labels["perfeng.io/observer-workload"] = name
print(json.dumps(value))
PY
  k create -f "$fixture/observer-statefulset.json"
  k -n perf-platform rollout status "statefulset/$workload" --timeout=90s
done
k -n perf-platform scale deployment/control-plane --replicas=1
k -n perf-platform rollout status deployment/control-plane --timeout=90s
observer_health() {
  k -n argocd get application perfeng-local-control-plane -o json \
    | jq --arg health "$1" '{status:{sync:{status:"Synced", revisions:[.spec.sources[].targetRevision],
      comparedTo:{sources:.spec.sources}},health:{status:$health},operationState:{phase:"Succeeded"}}}' \
    > "$fixture/observer-application-status.json"
  # The minimal CRD has no status subresource and no Argo CD controller.
  k -n argocd patch application perfeng-local-control-plane --type=merge \
    --patch-file "$fixture/observer-application-status.json"
}
observer_health Healthy
observer_submit() {
  a submit "$source_dir/observer-workflow.yaml" --entrypoint "$1" -o json | jq -er .metadata.name
}
observer_finish() {
  observer_label=$1 observer_workflow=$2 observer_expected=$3
  echo "Observer case: $observer_label ($observer_expected)"
  if timeout 630s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$observer_workflow"; then observer_rc=0; else observer_rc=$?; fi
  a get "$observer_workflow" -o json > "$results/observer-$observer_label.workflow.json"
  if [[ $observer_expected == success ]]; then
    [[ $observer_rc == 0 ]]
    jq -e '.status.phase == "Succeeded"' "$results/observer-$observer_label.workflow.json" > /dev/null
    jq -e 'reduce (.status.nodes[.metadata.name].outputs.parameters[]) as $p ({}; .[$p.name] = ($p.value | fromjson))' \
      "$results/observer-$observer_label.workflow.json" > "$results/observer-$observer_label.observations.json"
  else
    [[ $observer_rc != 0 && $observer_rc != 124 && $observer_rc != 137 ]]
    jq -e '(.status.phase == "Failed" or .status.phase == "Error") and
      ([.status.nodes[] | select(.displayName == "application" and .type == "Pod" and
        (.phase == "Failed" or .phase == "Error"))] | length == 1) and
      ([.status.nodes[.metadata.name].outputs.parameters[]? | select(has("value"))] | length == 0)' \
      "$results/observer-$observer_label.workflow.json" > /dev/null
    k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$observer_workflow" -o json \
      | jq -e '[.items[].status.containerStatuses[]? | select(.name == "main" and
        .state.terminated.exitCode != null and .state.terminated.exitCode != 0)] | length == 1' > /dev/null
    k -n argo-workflows logs -l "workflows.argoproj.io/workflow=$observer_workflow" \
      -c main --tail=30 --limit-bytes=16384 > "$fixture/observer-worker.log"
    if [[ $observer_expected == degraded ]]; then
      grep -Eq 'failure condition .*status.health.status.* evaluated true' "$fixture/observer-worker.log"
    else
      [[ $observer_expected == forbidden ]]
      grep -qi forbidden "$fixture/observer-worker.log"
      grep -q 'release-readiness' "$fixture/observer-worker.log"
      grep -q 'applications' "$fixture/observer-worker.log"
    fi
    printf '%s\n' "$observer_expected" > "$results/observer-$observer_label.refusal.txt"
  fi
}
observer_verify() {
  k -n argocd get application perfeng-local-control-plane -o json > "$fixture/observer-application.json"
  k -n perf-platform get statefulset/postgres statefulset/seaweedfs deployment/control-plane service/control-plane \
    -o json > "$fixture/observer-platform.json"
  uv run --locked --offline python - "$results/observer-$1.observations.json" "$fixture" <<'PY'
import json, sys
from pathlib import Path
observed, directory = json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2])
application = json.loads((directory / "observer-application.json").read_text())
platform = json.loads((directory / "observer-platform.json").read_text())["items"]
keys = {("StatefulSet", "postgres"): "postgres", ("StatefulSet", "seaweedfs"): "seaweedfs",
        ("Deployment", "control-plane"): "deployment", ("Service", "control-plane"): "service"}
objects = {keys[(o["kind"], o["metadata"]["name"])]: o for o in platform}
objects["application"] = application
assert set(observed) in ({"deployment"}, set(objects))
for key, value in observed.items():
    actual = objects[key]
    for field in ("name", "namespace", "uid", "generation"):
        assert value[field] == actual["metadata"].get(field)
    if key in {"postgres", "seaweedfs", "deployment"}:
        assert value["generation"] == value["observedGeneration"]
        assert all(value[f] == 1 for f in ("desired", "replicas", "ready", "updated"))
        if key != "deployment":
            assert value["currentRevision"] == value["updateRevision"] == actual["status"]["currentRevision"]
        else:
            assert value["available"] == 1 and value["unavailable"] == 0
            assert value["images"] == [c["image"] for c in actual["spec"]["template"]["spec"]["containers"]]
            assert all(value[f] == [] for f in ("runtimeConfigMaps", "runtimeSecrets", "registryConfigMaps", "authenticationSecrets", "imagePullSecrets"))
    elif key == "application":
        assert value["health"] == "Healthy" and value["sync"] == "Synced" and not value["conditions"]
        assert value["operation"] == "Succeeded"
        assert value["project"] == actual["spec"]["project"] and value["destination"] == actual["spec"]["destination"]
        assert value["sources"] == value["comparedSources"]
        assert value["revisions"] == [s["targetRevision"] for s in actual["spec"]["sources"]]
        for source, selected in zip(value["sources"], actual["spec"]["sources"], strict=True):
            assert source["repository"] == selected["repoURL"] and source["path"] == selected["path"]
            assert source["revision"] == selected["targetRevision"]
            for field in ("image", "runtimeConfigMap", "registryConfigMap"):
                assert source[field] == selected.get("helm", {}).get("valuesObject", {}).get(field)
    else:
        assert all(value[f] == actual["spec"][f] for f in ("type", "selector", "ports"))
PY
}
observer_finish first "$(observer_submit observe)" success
observer_verify first
observer_finish repeat "$(observer_submit observe)" success
observer_verify repeat
cmp "$results/observer-first.observations.json" "$results/observer-repeat.observations.json"
observer_health Degraded
observer_finish degraded "$(observer_submit observe)" degraded
observer_health Healthy
# Only the fixture administrator removes/restores this disposable binding.
k -n argocd delete rolebinding release-readiness --wait=true --timeout=30s
observer_finish forbidden "$(observer_submit observe)" forbidden
k apply -f gitops/release-workflows-bootstrap/readiness-application-access.yaml
observer_finish corrected "$(observer_submit observe)" success
observer_verify corrected

observer_pending_seen() {
  grep -Eq 'success condition evaluated.*matched[" :=]+false|matched[" :=]+false.*success condition evaluated' "$1"
}
k -n perf-platform scale deployment/control-plane --replicas=0
k -n perf-platform rollout status deployment/control-plane --timeout=90s
observer_pending=$(observer_submit deployment)
observer_deadline=$((SECONDS + 90))
while :; do
  [[ $SECONDS -lt $observer_deadline ]] || { echo 'Native pending condition was not observed' >&2; exit 1; }
  k -n argo-workflows logs -l "workflows.argoproj.io/workflow=$observer_pending" \
    -c main --tail=30 --limit-bytes=16384 > "$fixture/observer-pending.log" 2> "$fixture/observer-pending-errors.log" || :
  if observer_pending_seen "$fixture/observer-pending.log"; then break; fi
  sleep 2
done
[[ $(k -n perf-platform get deployment control-plane -o jsonpath='{.spec.replicas}') == 0 ]]
a get "$observer_pending" -o json | jq -e '.status.phase == "Running"' > /dev/null
printf '%s\n' 'Native false condition observed before explicit fixture scale-up' > "$results/observer-pending.txt"
k -n perf-platform scale deployment/control-plane --replicas=1
k -n perf-platform rollout status deployment/control-plane --timeout=90s
observer_finish resumed "$observer_pending" success
observer_verify resumed
[[ $(jq -r .deployment.uid "$results/observer-first.observations.json") == \
   $(jq -r .deployment.uid "$results/observer-resumed.observations.json") ]]

k -n argo-workflows get workflows -l perfeng.io/fixture=observer -o json > "$results/observer-retained.workflows.json"
for observer_case in first repeat degraded forbidden corrected resumed; do
  observer_name=$(jq -er .metadata.name "$results/observer-$observer_case.workflow.json")
  observer_uid=$(jq -er .metadata.uid "$results/observer-$observer_case.workflow.json")
  jq -e --arg name "$observer_name" --arg uid "$observer_uid" 'any(.items[]; .metadata.name == $name and .metadata.uid == $uid)' \
    "$results/observer-retained.workflows.json" > /dev/null
done
for resource in application postgres seaweedfs deployment service; do
  [[ $(jq -r --arg key "$resource" '.[$key].uid' "$results/observer-first.observations.json") == \
     $(jq -r --arg key "$resource" '.[$key].uid' "$results/observer-corrected.observations.json") ]]
done
observer_identity=system:serviceaccount:argo-workflows:release-readiness
for permission in 'list statefulsets perf-platform' 'watch deployments perf-platform' 'list applications argocd' \
  'patch deployments perf-platform' 'patch applications argocd' 'create jobs perf-platform' 'create secrets perf-platform'; do
  read -r verb resource namespace <<< "$permission"
  [[ $(k auth can-i "$verb" "$resource" -n "$namespace" --as "$observer_identity" || :) == no ]]
done
echo 'Native readiness observation parity passed; synthetic workloads/status are not actual release health or parent acceptance.'
