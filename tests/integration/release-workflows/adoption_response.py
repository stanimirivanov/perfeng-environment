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
POINTER_BARRIER = "pointer-response-barrier"


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


def template(
    parent: dict[str, Any], native: dict[str, Any], operation: str = "receipt"
) -> dict[str, Any]:
    """Preserve parent semantics except a bounded barrier and one create transport."""
    assert operation in {"receipt", "pointer"}
    barrier = POINTER_BARRIER if operation == "pointer" else BARRIER
    create_name = "create-" + operation
    result = copy.deepcopy(parent)
    result["metadata"] = {"name": NAME, "namespace": "argo-workflows"}
    steps = result["spec"]["templates"][0]["steps"]
    index = next(i for i, group in enumerate(steps) if group[0]["name"] == create_name)
    create = steps[index][0]
    assert create.pop("templateRef") == {
        "name": native["metadata"]["name"],
        "template": create_name,
    }
    create["template"] = "fault-" + create_name
    steps.insert(index, [{"name": barrier, "template": barrier}])
    # Reuse the transport proven by the native-ledger fixture, including its
    # deadline, no-owner-reference policy and Pod-level volume declaration.
    resource = proxy.fault_workflow(
        native, {"metadata": {}, "spec": {"arguments": {}}}, {}, operation
    )["spec"]["templates"][0]
    resource["name"] = "fault-" + create_name
    resource["resource"]["manifest"] = json.dumps(yaml.safe_load(resource["resource"]["manifest"]))
    # The existing parent deadline bounds suspension. Only explicit resume may
    # release it; a setup delay must not race an automatic suspension expiry.
    result["spec"]["templates"] += [{"name": barrier, "suspend": {}}, resource]
    return result


def expected_record(
    workflow: dict[str, Any], raw: bytes, operation: str = "receipt"
) -> dict[str, Any]:
    """Bind transport authorization to the actual plan, input bytes and origin UID."""
    assert operation in {"receipt", "pointer"}
    barrier_name = POINTER_BARRIER if operation == "pointer" else BARRIER
    assert workflow["spec"]["workflowTemplateRef"]["name"] == NAME
    assert workflow["spec"].get("shutdown") is None
    barrier = readiness.named(workflow, barrier_name, "Suspend")
    assert barrier["phase"] in {"Running", "Succeeded"}
    health = readiness.outputs(readiness.named(workflow, "readiness", "Steps"))["result"]
    root = adoption.record(workflow, "root")
    assert adoption.record(workflow, "current") is None
    assert adoption.record(workflow, "existing-receipt") is None
    plan = policy.plan_adoption(raw, health, root, None, None)
    adoption.planned(workflow, "plan", plan.copy())
    assert plan["create-receipt"] == "true" and plan["create-pointer"] == "true"
    parameters = {p["name"]: p["value"] for p in workflow["spec"]["arguments"]["parameters"]}
    if operation == "pointer":
        receipt = adoption.record(workflow, "stored-receipt")
        assert readiness.named(workflow, "create-receipt")["phase"] == "Succeeded"
        assert receipt["metadata"]["annotations"] == {
            "perfeng.io/first-workflow-uid": workflow["metadata"]["uid"],
            "perfeng.io/first-request-revision": parameters["request-revision"],
        }
        plan = policy.plan_adoption(
            raw, health, root, receipt, None, adoption.record(workflow, "root-after")
        )
        adoption.planned(workflow, "pointer-plan", plan.copy())
        assert plan["create-receipt"] == "false" and plan["create-pointer"] == "true"
    native = yaml.safe_load(Path("gitops/release-workflows/adoption-records.yaml").read_text())
    manifest = next(t for t in native["spec"]["templates"] if t["name"] == "create-" + operation)[
        "resource"
    ]["manifest"]
    keys = (
        ("name", "request-digest", "cluster-uid", "receipt-uid")
        if operation == "pointer"
        else ("name", "request-b64", "receipt-b64")
    )
    for key in keys:
        manifest = manifest.replace("{{inputs.parameters." + key + "}}", plan[key])
    manifest = manifest.replace("{{workflow.uid}}", workflow["metadata"]["uid"])
    manifest = manifest.replace(
        "{{workflow.parameters.request-revision}}", parameters["request-revision"]
    )
    assert "{{" not in manifest
    record = yaml.safe_load(manifest)
    assert proxy.record_kind(record) == operation
    return record


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
    operation: str = "receipt",
) -> dict[str, Any]:
    """Require real readiness and an exact committed record after response loss."""
    parent = yaml.safe_load(
        Path("gitops/release-workflows/release-adopt-existing-v2.yaml").read_text()
    )
    native = yaml.safe_load(Path("gitops/release-workflows/adoption-records.yaml").read_text())
    wanted = template(parent, native, operation)["spec"]
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
    assert expected == expected_record(workflow, raw, operation)
    barrier = POINTER_BARRIER if operation == "pointer" else BARRIER
    assert readiness.named(workflow, barrier, "Suspend")["phase"] == "Succeeded"
    failed = readiness.named(workflow, "create-" + operation)
    assert failed["phase"] in {"Failed", "Error"}
    assert failed["templateName"] == "fault-create-" + operation
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
    if operation == "pointer":
        downstream -= {"stored-receipt", "root-after", "pointer-plan", "create-pointer"}
    assert not any(
        n["displayName"] in downstream and n["type"] == "Pod"
        for n in workflow["status"]["nodes"].values()
    )
    receipt = api["receipt"]
    if operation == "pointer":
        assert receipt == adoption.record(workflow, "stored-receipt")
        committed = api["pointer"]
    else:
        assert api["pointer"] is None
        committed = receipt
    assert proxy.record_matches(expected, committed)
    assert len(events) == 1
    assert events[0] == {
        "event": operation + "-committed-response-dropped",
        "upstreamStatus": 201,
        "uid": committed["metadata"]["uid"],
        "contentDigest": proxy.digest(expected),
        "successfulResponsesForwarded": 0,
    }
    report = {
        "decision": "complete-parent-" + operation + "-response-loss-observed",
        "workflowUID": workflow["metadata"]["uid"],
        "receiptUID": receipt["metadata"]["uid"],
        "contentDigest": proxy.digest(expected),
    }
    if operation == "pointer":
        report["pointerUID"] = committed["metadata"]["uid"]
    return report


if __name__ == "__main__":
    mode, directory, evidence = sys.argv[1:4]
    operation = sys.argv[4] if len(sys.argv) == 5 else "receipt"
    assert operation in {"receipt", "pointer"}
    label = "pointer-response-loss" if operation == "pointer" else "response-loss"
    barrier = POINTER_BARRIER if operation == "pointer" else BARRIER
    fixture, results = Path(directory), Path(evidence)

    def load(path: Path) -> Any:
        return json.loads(path.read_text())

    if mode == "template":
        parent = yaml.safe_load(
            Path("gitops/release-workflows/release-adopt-existing-v2.yaml").read_text()
        )
        native = yaml.safe_load(Path("gitops/release-workflows/adoption-records.yaml").read_text())
        print(json.dumps(template(parent, native, operation)))
    elif mode == "expected":
        workflow = load(fixture / "adoption-response-barrier.workflow.json")
        assert workflow["status"]["phase"] == "Running"
        assert readiness.named(workflow, barrier, "Suspend")["phase"] == "Running"
        assert not any(
            n["displayName"] == "create-" + operation and n["type"] == "Pod"
            for n in workflow["status"]["nodes"].values()
        )
        print(
            json.dumps(
                expected_record(workflow, (fixture / "parent-request.json").read_bytes(), operation)
            )
        )
    elif mode == "verify":
        report = verify(
            load(results / f"adoption-parent-{label}.workflow.json"),
            load(fixture / "parent-parameters.json"),
            load(results / "parent-first.result.json"),
            (fixture / "parent-request.json").read_bytes(),
            Path("deployments/local-control-plane.json").read_bytes(),
            load(fixture / "adoption-parent-api.json"),
            load(results / f"adoption-parent-{label}.workers.json"),
            [
                json.loads(line)
                for line in (fixture / "ledger-response-events.jsonl").read_text().splitlines()
            ],
            load(fixture / "ledger-response-expected.json"),
            operation,
        )
        (results / f"adoption-{label}.outcome.json").write_text(json.dumps(report, sort_keys=True))
    else:
        raise ValueError("Unknown response-loss fixture mode")
