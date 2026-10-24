"""Generate and verify the explicitly adapted complete-parent transport fixture."""

import copy
import json
import sys
from pathlib import Path
from typing import Any

import adoption_parent_evidence as adoption
import ledger_response_proxy as proxy
import readiness_parent_evidence as readiness
import yaml

from tests.release_verifier_support import policy

NAME = "perfeng-adoption-response-ci"
BARRIER = "receipt-response-barrier"


def controller_form(value: Any) -> Any:
    """Ignore only Argo's empty struct fields and false owner-reference default."""
    if isinstance(value, list):
        return [controller_form(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        key: controller_form(item)
        for key, item in value.items()
        if not (
            (key in {"inputs", "outputs", "metadata", "arguments"} and item == {})
            or (key == "setOwnerReference" and item is False)
        )
    }


def template(parent: dict[str, Any], native: dict[str, Any]) -> dict[str, Any]:
    """Preserve parent semantics except a bounded barrier and receipt transport."""
    result = copy.deepcopy(parent)
    result["metadata"] = {"name": NAME, "namespace": "argo-workflows"}
    steps = result["spec"]["templates"][0]["steps"]
    index = next(i for i, group in enumerate(steps) if group[0]["name"] == "create-receipt")
    create = steps[index][0]
    assert create.pop("templateRef") == {
        "name": native["metadata"]["name"],
        "template": "create-receipt",
    }
    create["template"] = "fault-create-receipt"
    steps.insert(index, [{"name": BARRIER, "template": BARRIER}])
    # Reuse the transport proven by the native-ledger fixture, including its
    # deadline, no-owner-reference policy and Pod-level volume declaration.
    resource = proxy.fault_workflow(native, {"metadata": {}, "spec": {"arguments": {}}}, {})[
        "spec"
    ]["templates"][0]
    resource["name"] = "fault-create-receipt"
    resource["resource"]["manifest"] = json.dumps(yaml.safe_load(resource["resource"]["manifest"]))
    # The existing parent deadline bounds suspension. Only explicit resume may
    # release it; a setup delay must not race an automatic suspension expiry.
    result["spec"]["templates"] += [{"name": BARRIER, "suspend": {}}, resource]
    return result


def expected_receipt(workflow: dict[str, Any], raw: bytes) -> dict[str, Any]:
    """Bind transport authorization to the actual plan, input bytes and origin UID."""
    assert workflow["spec"]["workflowTemplateRef"]["name"] == NAME
    assert workflow["spec"].get("shutdown") is None
    barrier = readiness.named(workflow, BARRIER, "Suspend")
    assert barrier["phase"] in {"Running", "Succeeded"}
    health = readiness.outputs(readiness.named(workflow, "readiness", "Steps"))["result"]
    root = adoption.record(workflow, "root")
    assert adoption.record(workflow, "current") is None
    assert adoption.record(workflow, "existing-receipt") is None
    plan = policy.plan_adoption(raw, health, root, None, None)
    adoption.planned(workflow, "plan", plan.copy())
    assert plan["create-receipt"] == "true" and plan["create-pointer"] == "true"
    native = yaml.safe_load(Path("gitops/release-workflows/adoption-records.yaml").read_text())
    manifest = next(t for t in native["spec"]["templates"] if t["name"] == "create-receipt")[
        "resource"
    ]["manifest"]
    for key in ("name", "request-b64", "receipt-b64"):
        manifest = manifest.replace("{{inputs.parameters." + key + "}}", plan[key])
    parameters = {p["name"]: p["value"] for p in workflow["spec"]["arguments"]["parameters"]}
    manifest = manifest.replace("{{workflow.uid}}", workflow["metadata"]["uid"])
    manifest = manifest.replace(
        "{{workflow.parameters.request-revision}}", parameters["request-revision"]
    )
    assert "{{" not in manifest
    return yaml.safe_load(manifest)


def verify(
    workflow: dict[str, Any],
    parameters: dict[str, str],
    preflight: dict[str, Any],
    raw: bytes,
    raw_lock: bytes,
    api: dict[str, Any],
    workers: list[dict[str, Any]],
    events: list[dict[str, Any]],
    expected: dict[str, Any],
) -> dict[str, Any]:
    """Require real readiness and a committed receipt, never timeout-shaped success."""
    parent = yaml.safe_load(
        Path("gitops/release-workflows/release-adopt-existing-v2.yaml").read_text()
    )
    native = yaml.safe_load(Path("gitops/release-workflows/adoption-records.yaml").read_text())
    wanted = template(parent, native)["spec"]
    stored = workflow["status"]["storedWorkflowTemplateSpec"]
    for key in (
        "templates",
        "serviceAccountName",
        "entrypoint",
        "activeDeadlineSeconds",
        "synchronization",
    ):
        assert controller_form(stored[key]) == controller_form(wanted[key])
    readiness.success(
        workflow,
        preflight,
        parameters,
        api,
        raw_lock,
        adoption=True,
        parent_failed=True,
        response_loss=True,
    )
    assert expected == expected_receipt(workflow, raw)
    assert readiness.named(workflow, BARRIER, "Suspend")["phase"] == "Succeeded"
    failed = readiness.named(workflow, "create-receipt")
    assert failed["phase"] in {"Failed", "Error"}
    assert failed["templateName"] == "fault-create-receipt"
    assert all(w["account"] == "release-adoption" and w["uid"] for w in workers)
    nonzero = [w for w in workers if w["exitCode"] not in (None, 0)]
    assert (
        len(nonzero) == 1 and nonzero[0]["nodeID"] == failed["id"] and nonzero[0]["exitCode"] == 1
    )
    assert not failed.get("outputs", {}).get("parameters")
    assert "result" not in readiness.outputs(
        workflow["status"]["nodes"][workflow["metadata"]["name"]]
    )
    assert "result" not in readiness.outputs(workflow["status"])
    downstream = {
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
    receipt = api["receipt"]
    assert api["pointer"] is None and proxy.receipt_matches(expected, receipt)
    assert len(events) == 1
    assert events[0] == {
        "event": "receipt-committed-response-dropped",
        "upstreamStatus": 201,
        "uid": receipt["metadata"]["uid"],
        "contentDigest": proxy.digest(expected),
        "successfulResponsesForwarded": 0,
    }
    return {
        "decision": "complete-parent-receipt-response-loss-observed",
        "workflowUID": workflow["metadata"]["uid"],
        "receiptUID": receipt["metadata"]["uid"],
        "contentDigest": proxy.digest(expected),
    }


if __name__ == "__main__":
    mode, directory, evidence = sys.argv[1:]
    fixture, results = Path(directory), Path(evidence)

    def load(path: Path) -> Any:
        return json.loads(path.read_text())

    if mode == "template":
        parent = yaml.safe_load(
            Path("gitops/release-workflows/release-adopt-existing-v2.yaml").read_text()
        )
        native = yaml.safe_load(Path("gitops/release-workflows/adoption-records.yaml").read_text())
        print(json.dumps(template(parent, native)))
    elif mode == "expected":
        workflow = load(fixture / "adoption-response-barrier.workflow.json")
        assert workflow["status"]["phase"] == "Running"
        assert readiness.named(workflow, BARRIER, "Suspend")["phase"] == "Running"
        assert not any(
            n["displayName"] == "create-receipt" and n["type"] == "Pod"
            for n in workflow["status"]["nodes"].values()
        )
        print(
            json.dumps(expected_receipt(workflow, (fixture / "parent-request.json").read_bytes()))
        )
    elif mode == "verify":
        report = verify(
            load(results / "adoption-parent-response-loss.workflow.json"),
            load(fixture / "parent-parameters.json"),
            load(results / "parent-first.result.json"),
            (fixture / "parent-request.json").read_bytes(),
            Path("deployments/local-control-plane.json").read_bytes(),
            load(fixture / "adoption-parent-api.json"),
            load(results / "adoption-parent-response-loss.workers.json"),
            [
                json.loads(line)
                for line in (fixture / "ledger-response-events.jsonl").read_text().splitlines()
            ],
            load(fixture / "ledger-response-expected.json"),
        )
        (results / "adoption-response-loss.outcome.json").write_text(
            json.dumps(report, sort_keys=True)
        )
    else:
        raise ValueError("Unknown response-loss fixture mode")
