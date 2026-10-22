#!/usr/bin/env bash
# Complete production parent against actual selected workloads and Argo CD.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" && ${mode:-} == preflight-parent ]] || exit 2
[[ -f $fixture/owned && $KUBECONFIG == "$fixture/kubeconfig" && -f $fixture/parent-parameters.json ]] || exit 2
declare -F k a argocd_snapshot migration_snapshot external_secrets role_snapshot > /dev/null || exit 2

argocd_snapshot "$fixture/readiness-parent-resources-before.json"
migration_snapshot "$results/readiness-parent-schema-before.json"
external_secrets "$fixture/readiness-parent-external-before.json"
role_snapshot "$fixture/readiness-parent-roles-before.json"
k -n argo-workflows get secret release-git-read -o json | jq -S . > "$fixture/readiness-parent-git-before.json"
k -n argocd get secret ci-perfeng-k8s-repository -o json | jq -S . > "$fixture/readiness-parent-repository-before.json"

readiness_parent_case() {
  local label=$1 input=$2 expected=$3 workflow rc node_id pod
  echo "Complete release readiness: $label"
  workflow=$(a submit --from workflowtemplate/perfeng-release-readiness-v2 \
    --parameter-file "$input" -o json | jq -er .metadata.name)
  if timeout 1260s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$workflow"; then rc=0; else rc=$?; fi
  [[ $rc != 124 && $rc != 137 ]]
  a get "$workflow" -o json > "$results/readiness-parent-$label.workflow.json"
  k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$workflow" -o json \
    | jq '[.items[] | {name:.metadata.name,uid:.metadata.uid,nodeID:.metadata.annotations["workflows.argoproj.io/node-id"],
      account:.spec.serviceAccountName,exitCode:([.status.containerStatuses[]? | select(.name=="main") | .state.terminated.exitCode][0])}]' \
    > "$results/readiness-parent-$label.workers.json"
  jq -e 'length>0 and all(.[]; .account=="release-readiness" and .uid!=null and .nodeID!=null)' \
    "$results/readiness-parent-$label.workers.json" > /dev/null
  if [[ $expected == success ]]; then
    [[ $rc == 0 ]]
    jq -e 'all(.[]; .exitCode==0)' "$results/readiness-parent-$label.workers.json" > /dev/null
    k -n argocd get application perfeng-local-control-plane -o json > "$fixture/readiness-parent-application.json"
    k -n perf-platform get statefulset/postgres statefulset/seaweedfs deployment/control-plane service/control-plane -o json > "$fixture/readiness-parent-platform.json"
    jq -s --arg uid "$cluster_uid" '{application:.[0],platform:.[1].items,clusterUID:$uid}' \
      "$fixture/readiness-parent-application.json" "$fixture/readiness-parent-platform.json" > "$fixture/readiness-parent-api.json"
    uv run --locked --offline python "$source_dir/readiness_parent_evidence.py" success "$fixture" "$label" "$results"
    node_id=$(jq -er '.status.nodes[] | select(.displayName=="verify" and .type=="Pod" and .phase=="Succeeded") | .id' "$results/readiness-parent-$label.workflow.json")
    pod=$(jq -er --arg id "$node_id" '.[] | select(.nodeID==$id) | .name' "$results/readiness-parent-$label.workers.json")
    k -n argo-workflows logs "$pod" -c main --tail=20 --limit-bytes=8192 > "$fixture/readiness-parent-worker.log"
    grep -Fx 'Selected control-plane and foundation readiness verified' "$fixture/readiness-parent-worker.log" > "$results/readiness-parent-$label.outcome.txt"
  else
    [[ $rc != 0 ]]
    node_id=$(jq -er --arg step "$expected" '.status.nodes[] | select(.displayName==$step and .type=="Pod" and (.phase=="Failed" or .phase=="Error")) | .id' \
      "$results/readiness-parent-$label.workflow.json")
    pod=$(jq -er --arg id "$node_id" '.[] | select(.nodeID==$id) | .name' "$results/readiness-parent-$label.workers.json")
    k -n argo-workflows logs "$pod" -c main --tail=30 --limit-bytes=16384 > "$fixture/readiness-parent-worker.log"
    uv run --locked --offline python "$source_dir/readiness_parent_evidence.py" refusal "$fixture" "$label" "$results" "$expected"
    printf '%s\n' "Expected readiness refusal at $expected" > "$results/readiness-parent-$label.outcome.txt"
  fi
}
readiness_parent_case first "$fixture/parent-parameters.json" success
readiness_parent_case repeat "$fixture/parent-parameters.json" success
cmp "$results/readiness-parent-first.result.json" "$results/readiness-parent-repeat.result.json"
jq '."request-sha256"=("sha256:"+("0"*64))' "$fixture/parent-parameters.json" > "$fixture/readiness-parent-wrong-digest.json"
readiness_parent_case wrong-digest "$fixture/readiness-parent-wrong-digest.json" verify-identities

identity=system:serviceaccount:argo-workflows:release-readiness
k -n argocd delete rolebinding release-readiness
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-control-plane -n argocd --as "$identity" || :) == no ]]
readiness_parent_case missing-read-grant "$fixture/parent-parameters.json" application
k apply -f gitops/release-workflows-bootstrap/readiness-application-access.yaml
[[ $(k auth can-i get applications.argoproj.io/perfeng-local-control-plane -n argocd --as "$identity" || :) == yes ]]
readiness_parent_case corrected "$fixture/parent-parameters.json" success
cmp "$results/readiness-parent-first.result.json" "$results/readiness-parent-corrected.result.json"

argocd_snapshot "$fixture/readiness-parent-resources-after.json"
cmp "$fixture/readiness-parent-resources-before.json" "$fixture/readiness-parent-resources-after.json"
migration_snapshot "$results/readiness-parent-schema-after.json"
cmp "$results/readiness-parent-schema-before.json" "$results/readiness-parent-schema-after.json"
external_secrets "$fixture/readiness-parent-external-after.json"
cmp "$fixture/readiness-parent-external-before.json" "$fixture/readiness-parent-external-after.json"
role_snapshot "$fixture/readiness-parent-roles-after.json"
cmp "$fixture/readiness-parent-roles-before.json" "$fixture/readiness-parent-roles-after.json"
k -n argo-workflows get secret release-git-read -o json | jq -S . > "$fixture/readiness-parent-git-after.json"
cmp "$fixture/readiness-parent-git-before.json" "$fixture/readiness-parent-git-after.json"
k -n argocd get secret ci-perfeng-k8s-repository -o json | jq -S . > "$fixture/readiness-parent-repository-after.json"
cmp "$fixture/readiness-parent-repository-before.json" "$fixture/readiness-parent-repository-after.json"
s3 default get-object --bucket perfeng-artifacts --key ci/parent-sentinel.bin /tmp/readiness-parent-retained.bin > "$fixture/readiness-parent-download.json"
observer cat /tmp/readiness-parent-retained.bin > "$fixture/readiness-parent-retained.bin"
cmp "$fixture/external-sentinel.bin" "$fixture/readiness-parent-retained.bin"
[[ $(sql -c 'SELECT value FROM public.migration_ci_sentinel WHERE id=1;') == migration-image-parity-v1 ]]
[[ $(k get namespace kube-system -o jsonpath='{.metadata.uid}') == "$cluster_uid" ]]
for label in first repeat wrong-digest missing-read-grant corrected; do
  name=$(jq -er .metadata.name "$results/readiness-parent-$label.workflow.json")
  uid=$(jq -er .metadata.uid "$results/readiness-parent-$label.workflow.json")
  [[ $(k -n argo-workflows get workflow "$name" -o jsonpath='{.metadata.uid}') == "$uid" ]]
done
jq -s -e 'length==5 and (map(.metadata.uid)|unique|length)==5' "$results"/readiness-parent-*.workflow.json > /dev/null
for permission in 'patch applications.argoproj.io argocd' 'create secrets argo-workflows' 'list secrets argo-workflows' \
  'create jobs perf-platform' 'patch deployments perf-platform' 'delete pods perf-platform' 'patch statefulsets perf-platform'; do
  read -r verb resource namespace <<< "$permission"
  [[ $(k auth can-i "$verb" "$resource" -n "$namespace" --as "$identity" || :) == no ]]
done
[[ $(k auth can-i create pods --subresource=exec -n perf-platform --as "$identity" || :) == no ]]
echo 'Complete release readiness passed; adoption, promotion, recovery and script retirement remain separate.'
