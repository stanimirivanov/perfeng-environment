# Sourced only by the already isolated CI harness; not an executable operator CLI.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -n ${fixture:-} && -f $fixture/owned && ${KUBECONFIG:-} == "$fixture/kubeconfig" ]] || exit 2
declare -F k a > /dev/null || exit 2
ledger_namespace=perfeng-release-ledger
k apply -f gitops/release-workflows-bootstrap/ledger-namespace.yaml
k apply -f gitops/release-workflows-bootstrap/ledger-access.yaml
k apply -f gitops/release-workflows/rbac/adoption
k apply -f gitops/release-workflows-bootstrap/adoption-application-access.yaml
root_uid=$(k -n argocd get application perfeng-local-root -o jsonpath='{.metadata.uid}')
child_uid=$(k -n argocd get application perfeng-local-control-plane -o jsonpath='{.metadata.uid}')

# Schema-valid fixture bytes, not a published Git request or accepted release.
uv run --locked --offline python - "$fixture" "$revision" "$cluster_uid" "$root_uid" "$child_uid" <<'PY'
import base64, hashlib, json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
directory, revision, cluster, root, child = sys.argv[1:]
directory = Path(directory)
request = json.loads(Path("tests/fixtures/release-request-v1.json").read_text())
request["environment"]["clusterUID"] = cluster
request["selection"]["revision"] = revision
request["selection"]["lockDigests"] = {
    path: "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()
    for path in request["selection"]["lockDigests"]
}
Draft202012Validator(json.loads(Path("deployments/release-request-v1.schema.json").read_text())).validate(request)
raw = (json.dumps(request, sort_keys=True) + "\n").encode()
digest = "sha256:" + hashlib.sha256(raw).hexdigest()
lock = json.loads(Path("deployments/local-control-plane.json").read_text())
receipt = dict(schemaVersion=1, kind="PerfengReleaseAdoptionReceipt", status="adopted-baseline",
    environment=request["environment"], requestDigest=digest, selectionRevision=revision,
    rootUID=root, controlPlaneApplicationUID=child,
    deploymentRevision=lock["deployment"]["revision"], image=lock["controlPlane"]["image"])
Draft202012Validator(json.loads(Path("deployments/release-adoption-v1.schema.json").read_text())).validate(receipt)
receipt_bytes = json.dumps(receipt, sort_keys=True).encode()
(directory / "ledger-request.json").write_bytes(raw)
(directory / "ledger-receipt.json").write_bytes(receipt_bytes)
parameters = {"request-revision": revision, "name": "receipt." + digest[7:39] + "." + digest[39:],
    "request-b64": base64.b64encode(raw).decode(), "receipt-b64": base64.b64encode(receipt_bytes).decode(),
    "request-digest": digest, "cluster-uid": cluster, "receipt-uid": "not-created"}
(directory / "ledger-parameters.json").write_text(json.dumps(parameters))
PY
receipt_name=$(jq -r .name "$fixture/ledger-parameters.json")
ledger_submit() {
  a submit "$source_dir/ledger-workflow.yaml" --entrypoint "$1" \
    --parameter-file "$fixture/ledger-parameters.json" -o json | jq -er .metadata.name
}
ledger_case() {
  case_label=$1 entrypoint=$2 expected=$3
  echo "Ledger case: $case_label ($expected)"
  ledger_workflow=${4:-}
  if [[ -z $ledger_workflow ]]; then ledger_workflow=$(ledger_submit "$entrypoint"); fi
  if timeout 270s argo --kubeconfig "$KUBECONFIG" --context kind-perfeng-local \
    --namespace argo-workflows wait "$ledger_workflow"; then ledger_rc=0; else ledger_rc=$?; fi
  a get "$ledger_workflow" -o json > "$results/ledger-$case_label.workflow.json"
  ledger_phase=$(jq -r .status.phase "$results/ledger-$case_label.workflow.json")
  if [[ $expected == success || ( $expected == race && $ledger_phase == Succeeded ) ]]; then
    [[ $ledger_rc == 0 && $ledger_phase == Succeeded ]]
  else
    [[ $expected == conflict || $expected == race ]]
    [[ $ledger_rc != 0 && $ledger_rc != 124 && $ledger_rc != 137 && ( $ledger_phase == Failed || $ledger_phase == Error ) ]]
    # An unrelated executor/controller failure is not a create-conflict result.
    k -n argo-workflows logs -l "workflows.argoproj.io/workflow=$ledger_workflow" \
      -c main --tail=30 --limit-bytes=8192 | grep -Ei 'AlreadyExists|already exists' > "$results/ledger-$case_label.conflict.txt"
  fi
}
observations() {
  jq '[.status.nodes[.metadata.name].outputs.parameters[] | {key: .name, value: (.value | fromjson)}] | from_entries' "$1"
}
snapshot() {
  k -n "$ledger_namespace" get configmap "$receipt_name" perfeng-local-current -o json \
    | jq -S '.items | sort_by(.metadata.name)' > "$1"
}
ledger_case missing observe success
observations "$results/ledger-missing.workflow.json" | jq -e '.receipt == null and .pointer == null'
ledger_case receipt receipt-create success
ledger_case partial observe success
observations "$results/ledger-partial.workflow.json" > "$fixture/ledger-partial.json"
jq -e --arg name "$receipt_name" '.receipt.metadata.name == $name and .pointer == null' "$fixture/ledger-partial.json"
receipt_uid=$(jq -er '.receipt.metadata.uid' "$fixture/ledger-partial.json")
jq --arg uid "$receipt_uid" '.["receipt-uid"] = $uid' "$fixture/ledger-parameters.json" > "$fixture/ledger-parameters.next"
mv "$fixture/ledger-parameters.next" "$fixture/ledger-parameters.json"
race_one=$(ledger_submit pointer-create)
race_two=$(ledger_submit pointer-create)
ledger_case pointer-race-one pointer-create race "$race_one"
phase_one=$ledger_phase
ledger_case pointer-race-two pointer-create race "$race_two"
winners=0
if [[ $phase_one == Succeeded ]]; then winners=$((winners + 1)); fi
if [[ $ledger_phase == Succeeded ]]; then winners=$((winners + 1)); fi
[[ $winners == 1 ]]
ledger_case complete observe success
observations "$results/ledger-complete.workflow.json" > "$results/ledger-complete.records.json"
uv run --locked --offline python - "$results/ledger-complete.records.json" "$fixture/ledger-parameters.json" \
  "$results/ledger-receipt.workflow.json" <<'PY'
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
records, expected, created = (json.loads(Path(path).read_text()) for path in sys.argv[1:])
receipt, pointer = records["receipt"], records["pointer"]
assert receipt["immutable"] is True
assert receipt["binaryData"] == {"request.json": expected["request-b64"], "receipt.json": expected["receipt-b64"]}
assert receipt["metadata"]["annotations"]["perfeng.io/first-workflow-uid"] == created["metadata"]["uid"]
assert receipt["metadata"]["annotations"]["perfeng.io/first-request-revision"] == expected["request-revision"]
assert pointer["data"] == dict(requestDigest=expected["request-digest"], clusterUID=expected["cluster-uid"],
    receiptName=expected["name"], receiptUID=receipt["metadata"]["uid"])
assert pointer.get("immutable", False) is False and not pointer.get("binaryData")
for record, purpose in ((receipt, "release-adoption"), (pointer, "current-release")):
    metadata = record["metadata"]
    assert metadata["uid"] and metadata["resourceVersion"]
    assert not metadata.get("ownerReferences") and not metadata.get("finalizers")
    assert "argocd.argoproj.io/tracking-id" not in metadata.get("annotations", {})
    assert metadata["labels"] == {"app.kubernetes.io/managed-by": "perfeng-environment", "perfeng.io/purpose": purpose}
schema = json.loads(Path("deployments/release-adoption-v1.schema.json").read_text())
Draft202012Validator(schema["$defs"]["pointer"]).validate(pointer["data"])
PY
snapshot "$results/ledger-winner.json"
ledger_case receipt-duplicate receipt-create conflict
ledger_case pointer-duplicate pointer-create conflict
# Competing creates use deliberately changed bytes/UID; neither may overwrite the winner.
cp "$fixture/ledger-parameters.json" "$fixture/ledger-original-parameters.json"
jq '.["request-b64"] = "eA==" | .["receipt-uid"] = "competing-receipt-uid"' \
  "$fixture/ledger-parameters.json" > "$fixture/ledger-parameters.next"
mv "$fixture/ledger-parameters.next" "$fixture/ledger-parameters.json"
ledger_case receipt-conflict receipt-create conflict
ledger_case pointer-conflict pointer-create conflict
snapshot "$results/ledger-after-conflicts.json"
cmp "$results/ledger-winner.json" "$results/ledger-after-conflicts.json"
mv "$fixture/ledger-original-parameters.json" "$fixture/ledger-parameters.json"
# Prove immutable-data admission independently from the SA's denied patch authority.
if k -n "$ledger_namespace" patch configmap "$receipt_name" --type=merge \
  -p='{"binaryData":{"request.json":"eA=="}}' > "$fixture/immutable.out" 2> "$fixture/immutable.error"; then
  echo 'Immutable receipt unexpectedly accepted a data change' >&2
  exit 1
fi
grep -F immutable "$fixture/immutable.error" > "$results/ledger-immutable.txt"

k -n argo-workflows delete workflows -l perfeng.io/fixture=ledger --cascade=foreground --wait=true --timeout=90s
snapshot "$results/ledger-retained.json"
cmp "$results/ledger-winner.json" "$results/ledger-retained.json"
ledger_case retained observe success
observations "$results/ledger-retained.workflow.json" | jq -S . > "$results/ledger-retained.records.json"
jq -S . "$results/ledger-complete.records.json" > "$fixture/ledger-complete.sorted.json"
cmp "$fixture/ledger-complete.sorted.json" "$results/ledger-retained.records.json"
ledger_identity=system:serviceaccount:argo-workflows:release-adoption
for permission in 'get configmaps perfeng-release-ledger yes' 'create configmaps perfeng-release-ledger yes' \
  'patch configmaps perfeng-release-ledger no' 'delete configmaps perfeng-release-ledger no' \
  'create configmaps argo-workflows no' 'list secrets argo-workflows no' \
  'patch deployments perf-platform no' 'patch applications.argoproj.io argocd no'; do
  read -r verb resource namespace expected_answer <<< "$permission"
  answer=$(k auth can-i "$verb" "$resource" --namespace "$namespace" --as "$ledger_identity" || :)
  [[ $answer == "$expected_answer" ]] || { echo "Unexpected ledger authority: $permission" >&2; exit 1; }
done
echo 'Native ledger persistence passed; fixture records are not accepted releases.'
