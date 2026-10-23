"""Bind complete adoption-parent evidence to native observations and stored bytes."""

import base64
import json
import sys
from pathlib import Path
from typing import Any

import readiness_parent_evidence as readiness
from jsonschema import Draft202012Validator

from tests.release_verifier_support import policy


def record(workflow: dict[str, Any], name: str) -> Any:
    """Read one successful native observation, including an explicit missing record."""
    node = readiness.named(workflow, name)
    assert node["phase"] == "Succeeded"
    return readiness.outputs(node)["observation"]


def success(
    workflow: dict[str, Any],
    parameters: dict[str, str],
    preflight: dict[str, Any],
    raw: bytes,
    raw_lock: bytes,
    api: dict[str, Any],
    first: bool,
) -> dict[str, Any]:
    """Require fresh readiness, exact planner phases, native persistence and UID binding."""
    health = readiness.success(workflow, preflight, parameters, api, raw_lock, adoption=True)
    root = record(workflow, "root")
    initial_receipt = record(workflow, "existing-receipt")
    initial_pointer = record(workflow, "current")
    receipt = record(workflow, "stored-receipt")
    pointer = record(workflow, "stored-pointer")
    assert receipt == record(workflow, "final-receipt") == api["receipt"]
    assert pointer == api["pointer"]
    assert (initial_receipt is None) == (initial_pointer is None) == first
    if not first:
        assert initial_receipt == receipt and initial_pointer == pointer
    provenance = receipt["metadata"]["annotations"]
    if first:
        assert provenance["perfeng.io/first-workflow-uid"] == workflow["metadata"]["uid"]
        assert provenance["perfeng.io/first-request-revision"] == parameters["request-revision"]
    assert root["spec"]["syncPolicy"]["automated"]["enabled"] is False
    assert "argocd.argoproj.io/skip-reconcile" not in root["metadata"].get("annotations", {})
    schema = json.loads(
        Path("deployments/release-adoption-v1.schema.json").read_text(encoding="utf-8")
    )
    for phase, node_name, r, p, after in (
        ("plan", "plan", initial_receipt, initial_pointer, None),
        ("pointer", "pointer-plan", receipt, initial_pointer, record(workflow, "root-after")),
        ("finish", "finish", receipt, pointer, record(workflow, "final-root")),
    ):
        expected = policy.plan_adoption(raw, health, root, r, p, after)
        if phase != "finish":
            expected["result"] = None
        node = readiness.named(workflow, node_name)
        assert node["phase"] == "Succeeded"
        actual = {
            o["name"]: json.loads(o["value"]) if o["name"] == "result" else o["value"]
            for o in node["outputs"]["parameters"]
            if "value" in o
        }
        assert actual == expected
    for name in ("create-receipt", "create-pointer"):
        nodes = [n for n in workflow["status"]["nodes"].values() if n["displayName"] == name]
        assert len(nodes) == 1 and nodes[0]["phase"] == ("Succeeded" if first else "Skipped")
        assert nodes[0]["type"] == ("Pod" if first else "Skipped")
    result = readiness.outputs(workflow["status"]["nodes"][workflow["metadata"]["name"]])["result"]
    assert result == expected["result"]
    Draft202012Validator(schema["$defs"]["result"]).validate(result)
    Draft202012Validator(schema["$defs"]["pointer"]).validate(pointer["data"])
    Draft202012Validator(schema).validate(
        json.loads(base64.b64decode(receipt["binaryData"]["receipt.json"]))
    )
    assert base64.b64decode(receipt["binaryData"]["request.json"], validate=True) == raw
    return result


def refusal(workflow: dict[str, Any], workers: list[dict[str, Any]], log: str) -> None:
    """Require root-specific Forbidden after readiness, before any ledger operation."""
    assert workflow["status"]["phase"] in {"Failed", "Error"}
    assert workflow["spec"]["workflowTemplateRef"]["name"] == "perfeng-release-adopt-existing-v2"
    assert readiness.named(workflow, "readiness", "Steps")["phase"] == "Succeeded"
    failed = readiness.named(workflow, "root")
    assert failed["phase"] in {"Failed", "Error"}
    failed_workers = [w for w in workers if w["exitCode"] not in (None, 0)]
    assert len(failed_workers) == 1
    assert failed_workers[0]["nodeID"] == failed["id"] and failed_workers[0]["exitCode"] == 1
    for text in ("forbidden", "perfeng-local-root", "release-adoption", "applications", "argocd"):
        assert text in log
    assert "result" not in readiness.outputs(
        workflow["status"]["nodes"][workflow["metadata"]["name"]]
    )
    downstream = {
        "current",
        "existing-receipt",
        "plan",
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


if __name__ == "__main__":
    mode, directory, evidence_directory, label = sys.argv[1:]
    fixture, results = Path(directory), Path(evidence_directory)

    def load(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    workflow = load(results / f"adoption-parent-{label}.workflow.json")
    if mode == "success":
        result = success(
            workflow,
            load(fixture / "parent-parameters.json"),
            load(results / "parent-first.result.json"),
            (fixture / "parent-request.json").read_bytes(),
            Path("deployments/local-control-plane.json").read_bytes(),
            load(fixture / "adoption-parent-api.json"),
            label == "first",
        )
        (results / f"adoption-parent-{label}.result.json").write_text(
            json.dumps(result, sort_keys=True), encoding="utf-8"
        )
    elif mode == "refusal":
        refusal(
            workflow,
            load(results / f"adoption-parent-{label}.workers.json"),
            (fixture / "adoption-parent-worker.log").read_text(encoding="utf-8"),
        )
    else:
        raise ValueError("Unknown adoption evidence mode")
