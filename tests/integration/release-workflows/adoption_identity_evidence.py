"""Guard disposable receipt faults and verify complete-parent identity refusals."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import adoption_parent_evidence as adoption
import readiness_parent_evidence as readiness

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
    return {
        "decision": case + "-refused",
        "workflowUID": workflow["metadata"]["uid"],
        "pointerUID": pointer["metadata"]["uid"],
        "originalReceiptUID": original_receipt["metadata"]["uid"],
        "observedReceiptUID": receipt["metadata"]["uid"] if receipt else None,
    }


if __name__ == "__main__":
    mode, directory, evidence_directory, label = sys.argv[1:]
    fixture, results = Path(directory), Path(evidence_directory)

    def load(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    raw = (fixture / "parent-request.json").read_bytes()
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
