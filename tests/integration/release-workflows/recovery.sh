# Test-only recovery phase, sourced after the ledger helpers in the owned CI cluster.
set -euo pipefail
[[ ${BASH_SOURCE[0]} != "$0" ]] || exit 2
[[ ${GITHUB_ACTIONS:-} == true && ${RUNNER_ENVIRONMENT:-} == github-hosted && ${RUNNER_OS:-} == Linux ]] || exit 2
[[ -n ${fixture:-} && -f $fixture/owned && ${KUBECONFIG:-} == "$fixture/kubeconfig" ]] || exit 2
declare -F k a ledger_case observations snapshot > /dev/null || exit 2

# The fake Application CRD has no controller; explicitly seed synthetic healthy status.
jq -n --arg revision "$revision" --slurpfile source <(k -n argocd get application perfeng-local-root -o json) \
  '{status: {sync: {status: "Synced", revision: $revision, comparedTo: {source: $source[0].spec.source}},
    health: {status: "Healthy"}, operationState: {phase: "Succeeded"}}}' > "$fixture/recovery-root-status.json"
k -n argocd patch application perfeng-local-root --type=merge --patch-file "$fixture/recovery-root-status.json"

recovery_check() {
  uv run --locked --offline python - "$1" "$2" "$fixture" "${3:-$2}" > "$results/ledger-recovery-$1.json" <<'PY'
import json, sys
from pathlib import Path
from jsonschema import Draft202012Validator
from tests.release_verifier_support import policy

case, observed, directory, after = sys.argv[1:]
directory = Path(directory)
records = json.loads(Path(observed).read_text())
root_after = json.loads(Path(after).read_text())["root"]
raw = (directory / "ledger-request.json").read_bytes()
payload = json.loads((directory / "ledger-receipt.json").read_text())
# Synthetic planner inputs only: no readiness endpoint or full-parent claim.
readiness = dict(schemaVersion=1, kind="PerfengReleaseReadiness", scope="control-plane-foundation",
    status="readiness-verified", requestDigest=payload["requestDigest"],
    clusterUID=payload["environment"]["clusterUID"], selectionRevision=payload["selectionRevision"],
    applicationUID=payload["controlPlaneApplicationUID"], deploymentRevision=payload["deploymentRevision"], image=payload["image"])
refusals = {"changed-root": "root changed during adoption", "dangling": "current pointer conflicts",
    "replaced": "current pointer conflicts"}
try:
    plan = policy.plan_adoption(raw, readiness, records["root"], records["receipt"], records["pointer"], root_after)
except policy.ReadinessRefused as error:
    assert case in refusals and refusals[case] in str(error), (case, str(error))
    report = dict(case=case, decision="refused", reason=refusals[case])
else:
    assert case in {"partial", "complete", "retained"}, case
    assert plan["create-receipt"] == "false"
    assert plan["create-pointer"] == ("true" if case == "partial" else "false")
    assert plan["receipt-uid"] == records["receipt"]["metadata"]["uid"]
    if case != "partial":
        schema = json.loads(Path("deployments/release-adoption-v1.schema.json").read_text())
        Draft202012Validator(schema["$defs"]["result"]).validate(plan["result"])
        assert plan["result"]["pointerUID"] == records["pointer"]["metadata"]["uid"]
    report = dict(case=case, decision="matching-observations", createReceipt=False,
        createPointer=case == "partial", receiptUID=plan["receipt-uid"])
# Deliberately emit a test decision report, never an accepted adoption result.
print(json.dumps(report, sort_keys=True))
PY
}

recovery_refusals() {
  # A fresh API sample must invalidate a previously healthy root observation.
  k -n argocd patch application perfeng-local-root --type=merge -p='{"status":{"health":{"status":"Degraded"}}}'
  ledger_case changed-root observe success
  observations "$results/ledger-changed-root.workflow.json" > "$fixture/ledger-changed-root.json"
  recovery_check changed-root "$results/ledger-retained.records.json" "$fixture/ledger-changed-root.json"
  k -n argocd patch application perfeng-local-root --type=merge --patch-file "$fixture/recovery-root-status.json"

  # Only the fixture administrator destroys/recreates test data, after retention was proven.
  k -n "$ledger_namespace" get configmap "$receipt_name" -o json \
    | jq 'del(.metadata.uid, .metadata.resourceVersion, .metadata.creationTimestamp, .metadata.managedFields)' \
    > "$fixture/recovery-recreate.json"
  k -n "$ledger_namespace" get configmap perfeng-local-current -o json | jq -S . > "$fixture/recovery-pointer-before.json"
  k -n "$ledger_namespace" delete configmap "$receipt_name" --wait=true --timeout=30s
  ledger_case dangling observe success
  observations "$results/ledger-dangling.workflow.json" > "$fixture/ledger-dangling.json"
  jq -e '.receipt == null and .pointer != null' "$fixture/ledger-dangling.json"
  recovery_check dangling "$fixture/ledger-dangling.json"

  k create -f "$fixture/recovery-recreate.json"
  ledger_case replaced observe success
  observations "$results/ledger-replaced.workflow.json" > "$fixture/ledger-replaced.json"
  jq -e --arg original "$receipt_uid" '.receipt.metadata.uid != $original and .pointer.data.receiptUID == $original' "$fixture/ledger-replaced.json"
  recovery_check replaced "$fixture/ledger-replaced.json"
  k -n "$ledger_namespace" get configmap perfeng-local-current -o json | jq -S . > "$fixture/recovery-pointer-after.json"
  cmp "$fixture/recovery-pointer-before.json" "$fixture/recovery-pointer-after.json"
}
