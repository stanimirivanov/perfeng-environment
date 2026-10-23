"""Guard disposable receipt faults and verify complete-parent identity refusals."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import adoption_parent_evidence as adoption
import readiness_parent_evidence as readiness
from jsonschema import Draft202012Validator

from tests.release_verifier_support import policy


def recreate(receipt: dict[str, Any]) -> dict[str, Any]:
    """Preserve payload and provenance, excluding server-assigned object identity."""
    return {
        "apiVersion": receipt["apiVersion"],
        "kind": receipt["kind"],
        "metadata": {
            key: receipt["metadata"][key] for key in ("name", "namespace", "labels", "annotations")
        },
        "immutable": receipt["immutable"],
        "binaryData": receipt["binaryData"],
    }


def prepare(
    baseline: list[dict[str, Any]],
    current: list[dict[str, Any]],
    raw: bytes,
    health: dict[str, Any],
    root: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build an exact conditional DELETE and create-only replacement after retention."""
    assert len(baseline) == 2 and current == baseline
    pointer, receipt = sorted(current, key=lambda obj: obj["metadata"]["name"])
    plan = policy.plan_adoption(raw, health, root, receipt, pointer)
    assert plan["create-receipt"] == plan["create-pointer"] == "false"
    metadata = receipt["metadata"]
    assert metadata["uid"] and metadata["resourceVersion"]
    return (
        {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {
                "uid": metadata["uid"],
                "resourceVersion": metadata["resourceVersion"],
            },
        },
        recreate(receipt),
    )


def refusal(
    case: str,
    workflow: dict[str, Any],
    parameters: dict[str, str],
    preflight: dict[str, Any],
    raw: bytes,
    raw_lock: bytes,
    api: dict[str, Any],
    workers: list[dict[str, Any]],
    log: str,
    baseline: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind fresh readiness and API observations to the precise planner failure."""
    assert case in {"missing-receipt", "replaced-receipt"} and len(baseline) == 2
    original_pointer, original_receipt = sorted(baseline, key=lambda o: o["metadata"]["name"])
    health = readiness.success(
        workflow, preflight, parameters, api, raw_lock, adoption=True, parent_failed=True
    )
    assert parameters["request-sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    root = adoption.record(workflow, "root")
    receipt = adoption.record(workflow, "existing-receipt")
    pointer = adoption.record(workflow, "current")
    assert pointer == api["pointer"] == original_pointer
    assert receipt == api["receipt"]
    # Establish that the original baseline still matches fresh readiness/root state.
    assert policy.plan_adoption(raw, health, root, original_receipt, pointer)["result"]
    assert root["spec"]["syncPolicy"]["automated"] == {"enabled": False}
    assert "argocd.argoproj.io/skip-reconcile" not in root["metadata"].get("annotations", {})
    if case == "missing-receipt":
        assert receipt is None
    else:
        assert receipt is not None and receipt["metadata"]["uid"]
        assert receipt["metadata"]["uid"] != original_receipt["metadata"]["uid"]
        assert recreate(receipt) == recreate(original_receipt)
    reason = "current pointer conflicts or references a missing/replaced receipt"
    try:
        policy.plan_adoption(raw, health, root, receipt, pointer)
    except policy.ReadinessRefused as error:
        assert str(error) == reason
    else:
        raise AssertionError("Injected ledger state was accepted")
    plan_refused(workflow, workers, log, reason)
    return {
        "decision": case + "-refused",
        "workflowUID": workflow["metadata"]["uid"],
        "pointerUID": pointer["metadata"]["uid"],
        "originalReceiptUID": original_receipt["metadata"]["uid"],
        "observedReceiptUID": receipt["metadata"]["uid"] if receipt else None,
    }


def plan_refused(
    workflow: dict[str, Any], workers: list[dict[str, Any]], log: str, reason: str
) -> None:
    """Require the sole owning planner failure, without later workers or success output."""
    failed = readiness.named(workflow, "plan")
    assert failed["phase"] in {"Failed", "Error"}
    nonzero = [w for w in workers if w["exitCode"] not in (None, 0)]
    assert len(nonzero) == 1 and nonzero[0]["nodeID"] == failed["id"]
    assert nonzero[0]["exitCode"] == 2 and reason in log
    assert "result" not in readiness.outputs(failed)
    assert "result" not in readiness.outputs(
        workflow["status"]["nodes"][workflow["metadata"]["name"]]
    )
    downstream = {
        "create-receipt",
        "stored-receipt",
        "root-after",
        "pointer-plan",
        "create-pointer",
        "stored-pointer",
        "final-receipt",
        "final-root",
        "finish",
    }
    assert not any(
        n["displayName"] in downstream and n["type"] == "Pod"
        for n in workflow["status"]["nodes"].values()
    )


def request_refusal(
    case: str,
    workflow: dict[str, Any],
    parameters: dict[str, str],
    raw: bytes,
    original: bytes,
    raw_lock: bytes,
    api: dict[str, Any],
    workers: list[dict[str, Any]],
    log: str,
    baseline: list[dict[str, Any]],
) -> dict[str, Any]:
    """Verify changed-byte and upgrade envelopes cannot reuse the accepted adoption."""
    assert case in {"changed-request", "upgrade-request"} and len(baseline) == 2
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    original_digest = "sha256:" + hashlib.sha256(original).hexdigest()
    assert digest != original_digest and parameters["request-sha256"] == digest
    request, accepted = json.loads(raw), json.loads(original)
    if case == "changed-request":
        assert request == accepted
        reason = "current pointer conflicts or references a missing/replaced receipt"
    else:
        assert request == {
            **accepted,
            "promotion": {**accepted["promotion"], "expectedCurrent": original_digest},
        }
        reason = "request/readiness identity differs or adoption is not initial"
    preflight = readiness.outputs(readiness.named(workflow, "preflight", "Steps"))["result"]
    Draft202012Validator(
        json.loads(
            Path("deployments/release-preflight-result-v1.schema.json").read_text(encoding="utf-8")
        )
    ).validate(preflight)
    assert preflight["requestPath"] == parameters["request-path"]
    assert preflight["environment"] == request["environment"]["name"]
    assert preflight["lockDigests"] == request["selection"]["lockDigests"]
    health = readiness.success(
        workflow, preflight, parameters, api, raw_lock, adoption=True, parent_failed=True
    )
    root = adoption.record(workflow, "root")
    policy.application_identity(root, policy.NAMES[0])
    assert root["status"]["sync"]["status"] == "Synced"
    assert root["status"]["sync"]["revision"] == health["selectionRevision"]
    assert root["status"]["health"]["status"] == "Healthy"
    assert not root["status"].get("conditions")
    assert root["status"].get("operationState", {}).get("phase") in (None, "Succeeded")
    assert root["status"]["sync"]["comparedTo"]["source"] == policy.ROOT_SOURCE
    assert root["spec"]["syncPolicy"]["automated"] == {"enabled": False}
    assert "argocd.argoproj.io/skip-reconcile" not in root["metadata"].get("annotations", {})
    pointer, receipt = sorted(baseline, key=lambda o: o["metadata"]["name"])
    assert api["pointer"] == adoption.record(workflow, "current") == pointer
    assert api["receipt"] == receipt and pointer["data"]["requestDigest"] == original_digest
    assert adoption.record(workflow, "existing-receipt") is None
    assert api["requestedReceipt"] is None
    try:
        policy.plan_adoption(raw, health, root, None, pointer)
    except policy.ReadinessRefused as error:
        assert str(error) == reason
    else:
        raise AssertionError("Request crossed the adoption boundary")
    plan_refused(workflow, workers, log, reason)
    return {
        "decision": case + "-refused",
        "workflowUID": workflow["metadata"]["uid"],
        "requestDigest": digest,
        "acceptedDigest": original_digest,
        "pointerUID": pointer["metadata"]["uid"],
    }


if __name__ == "__main__":
    mode, directory, evidence_directory, label = sys.argv[1:]
    fixture, results = Path(directory), Path(evidence_directory)

    def load(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    request_case = mode in {"changed-request", "upgrade-request"}
    prefix = label if request_case else "parent"
    raw = (fixture / f"{prefix}-request.json").read_bytes()
    baseline = load(fixture / "adoption-identity-baseline.json")
    workflow = load(results / f"adoption-parent-{label}.workflow.json")
    if mode == "prepare":
        delete, replacement = prepare(
            baseline,
            load(fixture / "adoption-identity-current.json"),
            raw,
            readiness.outputs(readiness.named(workflow, "readiness", "Steps"))["result"],
            adoption.record(workflow, "root"),
        )
        for name, value in (("delete", delete), ("recreate", replacement)):
            (fixture / f"adoption-receipt-{name}.json").write_text(
                json.dumps(value), encoding="utf-8"
            )
    elif request_case:
        assert label == mode
        report = request_refusal(
            mode,
            workflow,
            load(fixture / f"{label}-parameters.json"),
            raw,
            (fixture / "parent-request.json").read_bytes(),
            Path("deployments/local-control-plane.json").read_bytes(),
            load(fixture / "adoption-parent-api.json"),
            load(results / f"adoption-parent-{label}.workers.json"),
            (fixture / "adoption-parent-worker.log").read_text(encoding="utf-8"),
            baseline,
        )
        (results / f"adoption-parent-{label}.outcome.json").write_text(
            json.dumps(report, sort_keys=True), encoding="utf-8"
        )
    else:
        assert mode in {"missing-receipt", "replaced-receipt"} and label == mode
        report = refusal(
            mode,
            workflow,
            load(fixture / "parent-parameters.json"),
            load(results / "parent-first.result.json"),
            raw,
            Path("deployments/local-control-plane.json").read_bytes(),
            load(fixture / "adoption-parent-api.json"),
            load(results / f"adoption-parent-{label}.workers.json"),
            (fixture / "adoption-parent-worker.log").read_text(encoding="utf-8"),
            baseline,
        )
        (results / f"adoption-parent-{label}.outcome.json").write_text(
            json.dumps(report, sort_keys=True), encoding="utf-8"
        )
