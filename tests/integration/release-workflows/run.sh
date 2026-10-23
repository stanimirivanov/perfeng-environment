#!/usr/bin/env bash
# CI fixture only. This is not an operator installation/deployment command.
set +x
set -euo pipefail

refuse() { echo "Refusing: $1" >&2; exit 2; }
if [[ ${GITHUB_ACTIONS:-} != true || ${RUNNER_ENVIRONMENT:-} != github-hosted || ${RUNNER_OS:-} != Linux ]]; then
  refuse 'requires an isolated GitHub-hosted Linux runner.'
fi
[[ -n ${RUNNER_TEMP:-} && -d $RUNNER_TEMP && -n ${GITHUB_TOKEN:-} ]] || refuse 'runner temporary directory/read token unavailable.'
mode=${RELEASE_KIND_MODE:-boundaries}
[[ $mode == boundaries || $mode == preflight-parent ]] || refuse 'unknown CI mode.'
if [[ $mode == preflight-parent ]]; then
  [[ ${GITHUB_REF:-} == refs/heads/main && ${GITHUB_REPOSITORY:-} == stanimirivanov/perfeng-environment ]] || refuse 'parent acceptance requires trusted main.'
  [[ ${GITHUB_EVENT_NAME:-} == push || ${GITHUB_EVENT_NAME:-} == workflow_dispatch ]] || refuse 'parent acceptance event is not trusted.'
fi
[[ -z ${DOCKER_HOST:-}${DOCKER_CONTEXT:-} ]] || refuse 'Docker endpoint/context override is not allowed.'
[[ $(docker context show) == default ]] || refuse 'requires the default local Docker context.'
[[ $(docker context inspect default --format '{{.Endpoints.docker.Host}}') == unix:///var/run/docker.sock ]] || refuse 'requires the local Unix Docker socket.'
umask 077
fixture=$(mktemp -d "$RUNNER_TEMP/perfeng-release-kind.XXXXXXXX")
export KUBECONFIG="$fixture/kubeconfig"
export PATH="$fixture/bin:$PATH"
source_dir=tests/integration/release-workflows
results="$RUNNER_TEMP/release-workflows-results"
mkdir -p "$fixture/bin" "$results"
k() { kubectl --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --request-timeout=20s "$@"; }
a() { argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local --namespace argo-workflows "$@"; }
finish() {
  rc=$?
  trap - EXIT
  set +e
  if [[ $mode == preflight-parent && -f $fixture/request-ref.json ]]; then
    uv run --locked --offline python "$source_dir/request_ref.py" cleanup "$fixture"
    ref_rc=$?
    cp "$fixture/request-ref.json" "$results/parent-request-ref.json"
    if [[ -f $fixture/request-ref-cleanup.json ]]; then
      cp "$fixture/request-ref-cleanup.json" "$results/parent-request-ref-cleanup.json"
    fi
    if [[ $ref_rc != 0 ]]; then rc=$ref_rc; fi
  fi
  if [[ -f $fixture/owned ]]; then
    k -n argo-workflows get workflows -o json > "$results/workflows.json"
    k -n argo-workflows get pods -o wide > "$results/pods.txt"
    timeout 120s kind delete cluster --name perfeng-local
    cleanup_rc=$?
    if [[ $rc == 0 && $cleanup_rc != 0 ]]; then rc=$cleanup_rc; fi
  fi
  rm -f "$fixture/token" "$fixture/username" "$fixture/registry-approved.secret.json" "$fixture/registry-denied.secret.json"
  if [[ $mode == preflight-parent ]]; then
    rm -f "$RUNNER_TEMP/perfeng-argocd-token" "$fixture/argocd-repository-secret.json" "$fixture/argocd-environment-repository-secret.json"
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

download() {
  curl --fail --location --retry 2 --max-time 120 --max-filesize 268435456 --proto '=https' "$1" --output "$2"
  printf '%s  %s\n' "$3" "$2" | sha256sum --check
}
for tool in kind kubectl argo; do
  url=$(jq -r --arg tool "$tool" '.[$tool].url' "$source_dir/toolchain.json")
  if [[ $tool == argo ]]; then
    checksum=$(tr -d '\r\n' < .argo-linux-amd64.sha256)
  else
    checksum=$(jq -r --arg tool "$tool" '.[$tool].sha256' "$source_dir/toolchain.json")
  fi
  download "$url" "$fixture/$tool.download" "$checksum"
  if [[ $tool == argo ]]; then
    gzip -dc "$fixture/$tool.download" > "$fixture/bin/$tool"
  else
    cp "$fixture/$tool.download" "$fixture/bin/$tool"
  fi
  chmod 700 "$fixture/bin/$tool"
done
if [[ $mode == preflight-parent ]]; then
  download https://get.helm.sh/helm-v4.1.4-linux-amd64.tar.gz "$fixture/helm.tar.gz" \
    "$(tr -d '\r\n' < .helm-linux-amd64.sha256)"
  tar -xzf "$fixture/helm.tar.gz" -C "$fixture/bin" --strip-components=1 linux-amd64/helm
  chmod 700 "$fixture/bin/helm"
fi
argo lint --offline --kinds=workflows,workflowtemplates gitops/release-workflows "$source_dir/workflow.yaml" "$source_dir/ledger-workflow.yaml" "$source_dir/database-workflow.yaml" "$source_dir/bucket-workflow.yaml" "$source_dir/registry-workflow.yaml" "$source_dir/readiness-workflow.yaml" "$source_dir/observer-workflow.yaml" "$source_dir/preflight-workflow.yaml" "$source_dir/migration-image-workflow.yaml" "$source_dir/runtime-grants-workflow.yaml" "$source_dir/server-workflow.yaml" "$source_dir/adoption-lock-workflow.yaml"
# Refuse any pre-existing kind cluster; cleanup is armed only after this check.
clusters=$(kind get clusters)
[[ -z $clusters ]] || refuse 'pre-existing kind clusters are not owned by this test.'
touch "$fixture/owned"
timeout 360s kind create cluster --name perfeng-local --kubeconfig "$KUBECONFIG" \
  --image "$(jq -r .compatibility.nodeImage deployments/local-infrastructure.json)" --wait 120s
for namespace in argo-workflows argocd perf-platform; do k create namespace "$namespace"; done
download "$(jq -r .workflows.url "$source_dir/toolchain.json")" "$fixture/namespace-install.yaml" \
  "$(jq -r .workflows.sha256 "$source_dir/toolchain.json")"
k -n argo-workflows apply --server-side -f "$fixture/namespace-install.yaml"
k -n argo-workflows rollout status deployment/workflow-controller --timeout=300s
k -n argo-workflows rollout status deployment/argo-server --timeout=300s
k apply -f "$source_dir/application-crd.yaml"
k wait --for=condition=Established crd/applications.argoproj.io --timeout=60s

# Select the actual rendered package and child; do not maintain fixture copies.
k kustomize gitops/release-workflows | uv run --locked --offline python -c '
import json, sys, yaml
names = {("ConfigMap", "perfeng-release-verifiers-v2"), ("WorkflowTemplate", "perfeng-verify-release-quiescence-v2"), ("WorkflowTemplate", "perfeng-release-maintenance-checkpoint-v2"), ("WorkflowTemplate", "perfeng-release-task-selection-v2"), ("WorkflowTemplate", "perfeng-release-external-state-v1"), ("WorkflowTemplate", "perfeng-adoption-records-v1"), ("ConfigMap", "perfeng-external-state-scripts-v1"), ("WorkflowTemplate", "perfeng-prepare-database-roles-v1"), ("WorkflowTemplate", "perfeng-prepare-bucket-v1"), ("WorkflowTemplate", "perfeng-prepare-execution-registry-v1"), ("WorkflowTemplate", "perfeng-verify-release-readiness-v2"), ("WorkflowTemplate", "perfeng-observe-release-readiness-v1"), ("WorkflowTemplate", "perfeng-release-preflight-v2")}
names.add(("WorkflowTemplate", "perfeng-release-readiness-v2"))
names.update(("WorkflowTemplate", name) for name in ("perfeng-plan-release-adoption-v2", "perfeng-release-adopt-existing-v2"))
items = [o for o in yaml.safe_load_all(sys.stdin) if (o["kind"], o["metadata"]["name"]) in names]
assert len(items) == len(names)
print(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}))
' | k apply -f -
k apply -f gitops/release-workflows/rbac/maintenance
k apply -f gitops/release-workflows-bootstrap/maintenance-application-access.yaml
printf '%s' x-access-token > "$fixture/username"
printf '%s' "$GITHUB_TOKEN" > "$fixture/token"
k -n argo-workflows create secret generic release-git-read \
  --from-file="username=$fixture/username" --from-file="password=$fixture/token"
source "$source_dir/registry.sh"
if [[ $mode == preflight-parent ]]; then
  registry_credentials
  rm -f "$fixture/token" "$fixture/username"
  source "$source_dir/preflight-parent.sh"
  exit 0
fi
registry_credentials
rm -f "$fixture/token" "$fixture/username"
unset GITHUB_TOKEN
for application in gitops/bootstrap/local-root-application.yaml gitops/environments/local/control-plane-application.yaml; do
  uv run --locked --offline python -c 'import json,sys,yaml; print(json.dumps(yaml.safe_load(open(sys.argv[1]))))' "$application" \
    | jq '.spec.syncPolicy.automated = {"enabled": false}' | k create -f -
done
k -n perf-platform create serviceaccount control-plane
k create -f "$source_dir/deployment.yaml"
k -n perf-platform rollout status deployment/control-plane --timeout=60s
revision=$(git rev-parse HEAD)
cluster_uid=$(k get namespace kube-system -o jsonpath='{.metadata.uid}')
lock_digest="sha256:$(sha256sum deployments/local-control-plane.json | cut -d ' ' -f 1)"
jq -n --arg revision "$revision" --arg uid "$cluster_uid" --arg digest "$lock_digest" '{
  schemaVersion: 1, kind: "PerfengReleasePreflight", status: "identity-verified",
  environment: "perfeng-local", selectionRevision: $revision, requestRevision: $revision,
  requestDigest: ("sha256:" + ("a" * 64)), clusterUID: $uid,
  lockDigests: {"deployments/local-control-plane.json": $digest}
}' > "$fixture/preflight.json"

run_case() {
  label=$1 expected=$2 input=$3
  echo "Fixture case: $label ($expected)"
  name=$(a submit "$source_dir/workflow.yaml" -p "selection-revision=$revision" \
    -p "preflight-result=$(cat "$input")" -o json | jq -r .metadata.name)
  # The CLI has no timeout flag in v4.1.5; bound its native wait externally.
  if timeout 330s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$name"; then wait_rc=0; else wait_rc=$?; fi
  a get "$name" -o json > "$results/$label.json"
  phase=$(jq -r .status.phase "$results/$label.json")
  if [[ $expected == success ]]; then
    [[ $wait_rc == 0 && $phase == Succeeded ]]
    jq -er '.status.nodes[.metadata.name].outputs.parameters[] | select(.name == "result") | .value' \
      "$results/$label.json" > "$results/$label.checkpoint.json"
    uv run --locked --offline python -c '
import json, sys
from jsonschema import Draft202012Validator
value = json.load(open(sys.argv[1]))
Draft202012Validator(json.load(open("deployments/maintenance-checkpoint-v1.schema.json"))).validate(value)
assert value["clusterUID"] == sys.argv[2] and value["selectionRevision"] == sys.argv[3]
' "$results/$label.checkpoint.json" "$cluster_uid" "$revision"
  else
    [[ $wait_rc != 0 && ( $phase == Failed || $phase == Error ) ]]
    # Artifact-fetch/controller errors must not masquerade as predicate refusal.
    k -n argo-workflows get pods -l "workflows.argoproj.io/workflow=$name" -o json \
      | jq -e 'any(.items[].status.containerStatuses[]?; .name == "main" and .state.terminated.exitCode == 2)' > /dev/null
    jq -e '[.status.nodes[].outputs.parameters[]? | select(.name == "result") | .value | fromjson? | select(.status == "quiescence-observed")] | length == 0' \
      "$results/$label.json" > /dev/null
  fi
}
run_case first success "$fixture/preflight.json"
run_case repeat success "$fixture/preflight.json"
for label in first repeat; do
  jq -S '{clusterUID, selectionRevision, requestDigest, resources: [.resources[] | {kind, name, uid}]}' \
    "$results/$label.checkpoint.json" > "$fixture/$label.identities.json"
done
cmp "$fixture/first.identities.json" "$fixture/repeat.identities.json"
jq '.clusterUID = "wrong-cluster-uid"' "$fixture/preflight.json" > "$fixture/wrong-uid.json"
run_case wrong-uid failure "$fixture/wrong-uid.json"
k -n argocd patch application perfeng-local-root --type=json \
  -p='[{"op":"replace","path":"/spec/syncPolicy/automated/enabled","value":true}]'
run_case unpaused failure "$fixture/preflight.json"
k -n argocd patch application perfeng-local-root --type=json \
  -p='[{"op":"replace","path":"/spec/syncPolicy/automated/enabled","value":false}]'
# This isolated matching Pod needs only to exist; absence must be refused even while Pending.
uv run --locked --offline python -c '
import json, yaml
deployment = yaml.safe_load(open("tests/integration/release-workflows/deployment.yaml"))
pod = deployment["spec"]["template"]
pod["metadata"].update(name="maintenance-ci-blocker", namespace="perf-platform")
print(json.dumps(dict(apiVersion="v1", kind="Pod", **pod)))
' | k create -f -
run_case matching-pod failure "$fixture/preflight.json"
k -n perf-platform delete pod maintenance-ci-blocker --wait=true --timeout=60s
k -n argocd delete rolebinding release-maintenance-checkpoint
run_case missing-read-grant failure "$fixture/preflight.json"
k apply -f gitops/release-workflows-bootstrap/maintenance-application-access.yaml
run_case recovered success "$fixture/preflight.json"

identity=system:serviceaccount:argo-workflows:release-maintenance-checkpoint
for permission in 'patch deployments perf-platform' 'delete pods perf-platform' 'create jobs perf-platform' \
  'patch applications.argoproj.io argocd' 'list secrets argo-workflows' 'patch configmaps perfeng-release-ledger'; do
  read -r verb resource namespace <<< "$permission"
  answer=$(k auth can-i "$verb" "$resource" --namespace "$namespace" --as "$identity" || :)
  [[ $answer == no ]] || { echo "Denial not proven: $permission" >&2; exit 1; }
done
echo 'Maintenance child integration passed; synthetic input is not release acceptance.'
# Reuse this exact private cluster/context; the sourced phase has no bootstrap CLI.
source "$source_dir/ledger.sh"
source "$source_dir/database.sh"
source "$source_dir/bucket.sh"
registry_cases
source "$source_dir/readiness.sh"
source "$source_dir/observer.sh"
source "$source_dir/preflight.sh"
