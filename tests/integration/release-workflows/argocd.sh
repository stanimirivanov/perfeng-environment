#!/usr/bin/env bash
# Trusted-main CI only: controller-produced convergence, no retained deployment.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" ]] || exit 2
declare -F k a migration_snapshot external_secrets role_snapshot > /dev/null || exit 2

echo 'Argo CD: install selected bootstrap and reconcile the selected private sources'
bootstrap=$(PYTHONPATH=. uv run --locked --offline python "$source_dir/argocd_inputs.py" bootstrap .ci/k8s-infrastructure)
# The root is deliberately excluded: it must never activate other Applications.
k -n argocd annotate application perfeng-local-root argocd.argoproj.io/skip-reconcile=true
root_uid=$(k -n argocd get application perfeng-local-root -o jsonpath='{.metadata.uid}')
application_uid=$(k -n argocd get application perfeng-local-control-plane -o jsonpath='{.metadata.uid}')
k -n argocd get application perfeng-local-control-plane -o json > "$fixture/argocd-application-before.json"
uv run --locked --offline python - "$fixture/argocd-application-before.json" > "$fixture/argocd-selection-patch.json" <<'PY'
import json, sys, yaml
from pathlib import Path
before = json.loads(Path(sys.argv[1]).read_text())
spec = yaml.safe_load(Path('gitops/environments/local/control-plane-application.yaml').read_text())['spec']
paused = json.loads(json.dumps(spec))
paused['syncPolicy']['automated'] = {'enabled': False}
assert before['spec'] == paused and not before.get('status') and not before.get('operation')
print(json.dumps([
 {'op':'test','path':'/metadata/uid','value':before['metadata']['uid']},
 {'op':'test','path':'/metadata/resourceVersion','value':before['metadata']['resourceVersion']},
 {'op':'replace','path':'/spec','value':spec}]))
PY
k -n argocd patch application perfeng-local-control-plane --type=json --patch-file "$fixture/argocd-selection-patch.json"
PYTHONPATH=. uv run --locked --offline python "$source_dir/argocd_inputs.py" secret \
  "$RUNNER_TEMP/perfeng-argocd-token" "$fixture/argocd-repository-secret.json"
k create -f "$fixture/argocd-repository-secret.json"
rm -f "$RUNNER_TEMP/perfeng-argocd-token" "$fixture/argocd-repository-secret.json"
k -n argocd get secret ci-perfeng-k8s-repository -o json | jq -S . > "$fixture/argocd-repository-before.json"
k apply --server-side -k "$bootstrap"
k wait --for=condition=Established crd/applications.argoproj.io crd/appprojects.argoproj.io --timeout=90s
k -n argocd rollout status deployment/argocd-redis --timeout=240s
k -n argocd rollout status deployment/argocd-repo-server --timeout=240s
k -n argocd rollout status statefulset/argocd-application-controller --timeout=240s
k apply -f gitops/environments/local/project.yaml
k apply -f gitops/release-workflows-bootstrap/readiness-application-access.yaml
# Require an actual comparison after project/repository discovery before sync.
selected_revision=$(jq -er .deployment.revision deployments/local-control-plane.json)
k -n argocd wait application/perfeng-local-control-plane \
  --for="jsonpath={.status.sync.revisions[1]}=$selected_revision" --timeout=240s
k -n argocd get application perfeng-local-control-plane -o json \
  | jq -e '(.status.conditions // [] | length)==0 and (.status.sync.status=="Synced" or .status.sync.status=="OutOfSync")' > /dev/null

argocd_snapshot() {
  # Credentials stay private; metadata tracking changes are expected during adoption.
  k -n perf-platform get deployments,services,serviceaccounts,statefulsets,pvc,configmaps,secrets -o json > "$fixture/argocd-platform.json"
  k -n perf-platform get "pod/$database_pod" "pod/$bucket_server_pod" \
    "pod/$(jq -er '.[0].name' "$results/server-corrected.pods.json")" -o json > "$fixture/argocd-pods.json"
  k -n perf-generators get role/control-plane-job-controller rolebinding/control-plane-job-controller -o json > "$fixture/argocd-rbac.json"
  k get pv -o json > "$fixture/argocd-volumes.json"
  jq -S -s '[.[].items[] | {kind,namespace:.metadata.namespace,name:.metadata.name,uid:.metadata.uid,data,immutable,
    volume:.spec.volumeName,claim:.spec.claimRef.uid}] | sort_by(.kind,.namespace,.name)' \
    "$fixture/argocd-platform.json" "$fixture/argocd-pods.json" "$fixture/argocd-rbac.json" "$fixture/argocd-volumes.json" > "$1"
}
argocd_snapshot "$fixture/argocd-resources-before.json"
migration_snapshot "$results/argocd-schema-before.json"
role_snapshot "$fixture/argocd-roles-before.json"
external_secrets "$fixture/argocd-external-before.json"

argocd_case() {
  local label=$1 workflow rc
  echo "Argo CD convergence case: $label"
  k -n argocd get application perfeng-local-control-plane -o json \
    | jq --arg label "$label" '{metadata:{resourceVersion:.metadata.resourceVersion},operation:{
      initiatedBy:{username:"disposable-ci"},info:[{name:"ci-case",value:$label}],
      sync:{revisions:[.spec.sources[].targetRevision],syncStrategy:{apply:{}}}}}' > "$fixture/argocd-sync.json"
  k -n argocd patch application perfeng-local-control-plane --type=merge --patch-file "$fixture/argocd-sync.json"
  # Wait for this operation, not a previous Succeeded status with the same revision.
  if ! k -n argocd wait application/perfeng-local-control-plane \
    --for="jsonpath={.status.operationState.operation.info[0].value}=$label" --timeout=240s \
    || ! k -n argocd wait application/perfeng-local-control-plane \
      --for=jsonpath='{.status.operationState.phase}'=Succeeded --timeout=240s \
    || ! k -n argocd wait application/perfeng-local-control-plane \
      --for=jsonpath='{.status.sync.status}'=Synced --timeout=120s \
    || ! k -n argocd wait application/perfeng-local-control-plane \
      --for=jsonpath='{.status.health.status}'=Healthy --timeout=180s; then
    k -n argocd get application perfeng-local-control-plane -o json \
      | jq '{sync:.status.sync.status,health:.status.health.status,operation:.status.operationState.phase,conditions:[.status.conditions[]?.type]}' > "$results/argocd-$label.failure.json"
    return 1
  fi
  k -n argocd get application perfeng-local-control-plane -o json > "$fixture/argocd-current.json"
  PYTHONPATH=. uv run --locked --offline python "$source_dir/argocd_inputs.py" converged \
    "$fixture/argocd-current.json" "$application_uid" "$label"
  jq '{uid:.metadata.uid,spec,sync:.status.sync,health:.status.health,phase:.status.operationState.phase,case:.status.operationState.operation.info}' \
    "$fixture/argocd-current.json" > "$results/argocd-$label.application.json"
  workflow=$(a submit "$source_dir/observer-workflow.yaml" --entrypoint application -o json | jq -er .metadata.name)
  if timeout 360s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows wait "$workflow"; then rc=0; else rc=$?; fi
  a get "$workflow" -o json > "$results/argocd-$label.workflow.json"
  [[ $rc == 0 ]]
  jq -e '.status.phase=="Succeeded"' "$results/argocd-$label.workflow.json" > /dev/null
  jq -er '.status.nodes[.metadata.name].outputs.parameters[] | select(.name=="application") | .value' \
    "$results/argocd-$label.workflow.json" > "$results/argocd-$label.observation.json"
  jq -e --arg uid "$application_uid" '.uid==$uid and .sync=="Synced" and .health=="Healthy" and .sources==.comparedSources and .conditions==[] and .operation=="Succeeded"' \
    "$results/argocd-$label.observation.json" > /dev/null
}
argocd_case first
argocd_case repeat
for label in first repeat; do
  jq -S 'del(.generation)' "$results/argocd-$label.observation.json" > "$fixture/argocd-$label.stable.json"
done
cmp "$fixture/argocd-first.stable.json" "$fixture/argocd-repeat.stable.json"
# Real live drift with automated sync disabled; only the following manual sync repairs it.
k -n perf-platform patch service control-plane --type=json \
  -p '[{"op":"test","path":"/spec/ports/0/targetPort","value":"http"},{"op":"replace","path":"/spec/ports/0/targetPort","value":8081}]'
k -n argocd annotate application perfeng-local-control-plane argocd.argoproj.io/refresh=hard --overwrite
k -n argocd wait application/perfeng-local-control-plane --for=jsonpath='{.status.sync.status}'=OutOfSync --timeout=180s
k -n argocd get application perfeng-local-control-plane -o json \
  | jq -e '.spec.syncPolicy.automated=={enabled:false,prune:false,selfHeal:false} and .operation==null and
    ([.status.resources[] | select(.kind=="Service" and .name=="control-plane" and .status=="OutOfSync")]|length)==1' > /dev/null
[[ $(k -n perf-platform get service control-plane -o jsonpath='{.spec.ports[0].targetPort}') == 8081 ]]
printf '%s\n' 'Controller observed Service drift; automatic sync remains disabled' > "$results/argocd-drift.outcome.txt"
argocd_case corrected
[[ $(k -n perf-platform get service control-plane -o jsonpath='{.spec.ports[0].targetPort}') == http ]]
argocd_snapshot "$fixture/argocd-resources-after.json"
cmp "$fixture/argocd-resources-before.json" "$fixture/argocd-resources-after.json"
migration_snapshot "$results/argocd-schema-after.json"
cmp "$results/argocd-schema-before.json" "$results/argocd-schema-after.json"
role_snapshot "$fixture/argocd-roles-after.json"
cmp "$fixture/argocd-roles-before.json" "$fixture/argocd-roles-after.json"
external_secrets "$fixture/argocd-external-after.json"
cmp "$fixture/argocd-external-before.json" "$fixture/argocd-external-after.json"
k -n argocd get secret ci-perfeng-k8s-repository -o json | jq -S . > "$fixture/argocd-repository-after.json"
cmp "$fixture/argocd-repository-before.json" "$fixture/argocd-repository-after.json"
s3 default get-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin /tmp/argocd-retained.bin > "$fixture/argocd-download.json"
observer cat /tmp/argocd-retained.bin > "$fixture/argocd-retained.bin"
cmp "$fixture/external-sentinel.bin" "$fixture/argocd-retained.bin"
[[ $(sql -c 'SELECT value FROM public.migration_ci_sentinel WHERE id=1;') == migration-image-parity-v1 ]]
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
k -n argocd get application perfeng-local-root -o json \
  | jq -e --arg uid "$root_uid" '.metadata.uid==$uid and .metadata.annotations["argocd.argoproj.io/skip-reconcile"]=="true" and .spec.syncPolicy.automated.enabled==false and .status.operationState==null and .operation==null and ([.status.resources[]?]|length)==0' > /dev/null
echo 'Actual Argo CD convergence and explicit drift correction passed; complete readiness/adoption remain separate.'
