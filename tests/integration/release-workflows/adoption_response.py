"""Generate and verify bounded complete-parent create-fault fixtures."""

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
CONFLICTS = {"conflict", "pointer-conflict"}
POINTERS = {"pointer", "pointer-conflict"}


def barrier_diagnostics(workflow: Any, pods: Any, barrier: str, observation: str) -> dict[str, Any]:
    """Project private API observations into bounded fixed-vocabulary diagnostics."""

    def mapping(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def vocabulary(value: Any, allowed: set[str]) -> str:
        return value if isinstance(value, str) and value in allowed else "unknown"

    def signals(value: Any) -> list[str]:
        message = str(value)[:16384].lower()
        return [
            name
            for name, markers in (
                ("forbidden", ("forbidden",)),
                ("unauthorized", ("unauthorized",)),
                ("not-found", ("notfound", "not found")),
                ("deadline", ("deadline",)),
                ("image-pull", ("imagepull",)),
                ("template-resolution", ("failed to resolve",)),
            )
            if any(marker in message for marker in markers)
        ]

    phases = {"Pending", "Running", "Succeeded", "Failed", "Error", "Skipped", "Omitted"}
    steps = {BARRIER, POINTER_BARRIER, "fault-create-receipt", "fault-create-pointer"}
    for path in Path("gitops/release-workflows").glob("*.yaml"):
        resource = yaml.safe_load(path.read_text())
        for item in resource.get("spec", {}).get("templates", []):
            steps.add(item["name"])
            for group in item.get("steps", []):
                steps.update(step["name"] for step in group)
    status = mapping(mapping(workflow).get("status"))
    nodes = mapping(status.get("nodes"))
    ready = status.get("phase") == "Running" and any(
        mapping(node).get("displayName") == barrier
        and mapping(node).get("type") == "Suspend"
        and mapping(node).get("phase") == "Running"
        for node in nodes.values()
    )
    decision = {
        "terminal": "terminal-parent",
        "timeout": "barrier-timeout",
        "api-error": "api-read-failed",
    }.get(observation, "invalid-observation")
    if observation == "ready" and ready:
        decision = "barrier-ready"
    items = mapping(pods).get("items")
    pod_list = items[:256] if isinstance(items, list) else []
    records = []
    for node_id, raw_node in list(nodes.items())[:256]:
        node = mapping(raw_node)
        containers = []
        for raw_pod in pod_list:
            pod = mapping(raw_pod)
            annotations = mapping(mapping(pod.get("metadata")).get("annotations"))
            if annotations.get("workflows.argoproj.io/node-id") != node_id:
                continue
            pod_status = mapping(pod.get("status"))
            for field in ("initContainerStatuses", "containerStatuses"):
                statuses = pod_status.get(field)
                for raw_container in statuses[:16] if isinstance(statuses, list) else []:
                    container = mapping(raw_container)
                    state = mapping(container.get("state"))
                    kind = next(
                        (key for key in ("waiting", "running", "terminated") if key in state),
                        "unknown",
                    )
                    detail = mapping(state.get(kind))
                    code = detail.get("exitCode")
                    containers.append(
                        {
                            "name": vocabulary(container.get("name"), {"main", "wait", "init"}),
                            "state": kind,
                            "reason": vocabulary(
                                detail.get("reason"),
                                {
                                    "Completed",
                                    "Error",
                                    "OOMKilled",
                                    "ContainerCreating",
                                    "PodInitializing",
                                    "ErrImagePull",
                                    "ImagePullBackOff",
                                    "CreateContainerConfigError",
                                    "CrashLoopBackOff",
                                },
                            ),
                            "exitCode": code if type(code) is int and 0 <= code <= 255 else None,
                        }
                    )
        records.append(
            {
                "step": vocabulary(node.get("displayName"), steps),
                "type": vocabulary(
                    node.get("type"),
                    {"Pod", "Steps", "StepGroup", "Suspend", "Retry", "DAG", "Skipped"},
                ),
                "phase": vocabulary(node.get("phase"), phases),
                "signals": signals(node.get("message", "")),
                "containers": containers[:16],
            }
        )
    return {
        "decision": decision,
        "workflowPhase": vocabulary(status.get("phase"), phases),
        "workflowSignals": signals(status.get("message", "")),
        "podsObserved": isinstance(items, list),
        "nodesTruncated": len(nodes) > 256,
        "nodes": records,
    }


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
    """Add a bounded barrier; adapt transport only for the response-loss cases."""
    assert operation in {"receipt", "pointer", *CONFLICTS}
    barrier = POINTER_BARRIER if operation in POINTERS else BARRIER
    create_name = "create-" + ("pointer" if operation in POINTERS else "receipt")
    result = copy.deepcopy(parent)
    result["metadata"] = {"name": NAME, "namespace": "argo-workflows"}
    steps = result["spec"]["templates"][0]["steps"]
    index = next(i for i, group in enumerate(steps) if group[0]["name"] == create_name)
    if operation in CONFLICTS:
        steps.insert(index, [{"name": barrier, "template": barrier}])
        result["spec"]["templates"].append({"name": barrier, "suspend": {}})
        return result
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
    """Bind the expected create to the actual plan, input bytes and origin UID."""
    assert operation in {"receipt", "pointer", *CONFLICTS}
    operation = "pointer" if operation in POINTERS else "receipt"
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
    *,
    winner: dict[str, Any] | None = None,
    diagnostic: str = "",
) -> dict[str, Any]:
    """Require real readiness, the exact create fault and preserved committed state."""
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
    barrier = POINTER_BARRIER if operation in POINTERS else BARRIER
    assert readiness.named(workflow, barrier, "Suspend")["phase"] == "Succeeded"
    kind = "pointer" if operation in POINTERS else "receipt"
    failed = readiness.named(workflow, "create-" + kind)
    assert failed["phase"] in {"Failed", "Error"}
    if operation in CONFLICTS:
        assert failed["templateRef"] == {
            "name": native["metadata"]["name"],
            "template": "create-" + kind,
        }
    else:
        assert failed["templateName"] == "fault-create-" + operation
    assert all(w["account"] == "release-adoption" and w["uid"] for w in workers)
    nonzero = [w for w in workers if w["exitCode"] not in (None, 0)]
    assert (
        len(nonzero) == 1
        and nonzero[0]["nodeID"] == failed["id"]
        and nonzero[0]["exitCode"] == (64 if operation in CONFLICTS else 1)
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
    if operation in POINTERS:
        downstream -= {"stored-receipt", "root-after", "pointer-plan", "create-pointer"}
    assert not any(
        n["displayName"] in downstream and n["type"] == "Pod"
        for n in workflow["status"]["nodes"].values()
    )
    receipt = api["receipt"]
    if operation in POINTERS:
        assert receipt == adoption.record(workflow, "stored-receipt")
        committed = api["pointer"]
    else:
        assert api["pointer"] is None
        committed = receipt
    assert proxy.record_matches(expected, committed)
    if operation in CONFLICTS:
        assert events == [] and winner == committed
        wanted_error = (
            'Error from server (AlreadyExists): error when creating "/tmp/manifest.yaml": '
            f'configmaps "{expected["metadata"]["name"]}" already exists'
        )
        # This is the raw error emitted by the pinned Argo executor, observed in
        # qualified native-ledger runs; structured log copies also escape quotes.
        assert wanted_error in diagnostic
        assert wanted_error in failed["message"]
    else:
        assert len(events) == 1
        assert events[0] == {
            "event": operation + "-committed-response-dropped",
            "upstreamStatus": 201,
            "uid": committed["metadata"]["uid"],
            "contentDigest": proxy.digest(expected),
            "successfulResponsesForwarded": 0,
        }
    report = {
        "decision": (
            "complete-parent-" + kind + "-create-conflict-observed"
            if operation in CONFLICTS
            else "complete-parent-" + operation + "-response-loss-observed"
        ),
        "workflowUID": workflow["metadata"]["uid"],
        "receiptUID": receipt["metadata"]["uid"],
        "contentDigest": proxy.digest(expected),
    }
    if operation in POINTERS:
        report["pointerUID"] = committed["metadata"]["uid"]
    return report


if __name__ == "__main__":
    mode, directory, evidence = sys.argv[1:4]
    operation = sys.argv[4] if len(sys.argv) == 5 else "receipt"
    assert operation in {"receipt", "pointer", *CONFLICTS}
    label = (
        "pointer-create-conflict"
        if operation == "pointer-conflict"
        else "receipt-create-conflict"
        if operation == "conflict"
        else "pointer-response-loss"
        if operation == "pointer"
        else "response-loss"
    )
    barrier = POINTER_BARRIER if operation in POINTERS else BARRIER
    fixture, results = Path(directory), Path(evidence)

    def load(path: Path) -> Any:
        return json.loads(path.read_text())

    if mode == "template":
        parent = yaml.safe_load(
            Path("gitops/release-workflows/release-adopt-existing-v2.yaml").read_text()
        )
        native = yaml.safe_load(Path("gitops/release-workflows/adoption-records.yaml").read_text())
        print(json.dumps(template(parent, native, operation)))
    elif mode == "barrier":

        def observation_file(name: str) -> Any:
            try:
                return load(fixture / name)
            except (OSError, ValueError):
                return None

        report = barrier_diagnostics(
            observation_file("adoption-response-barrier.workflow.json"),
            observation_file("adoption-response-barrier.pods.json"),
            barrier,
            (fixture / "adoption-response-barrier.state.txt").read_text().strip(),
        )
        (results / f"adoption-{label}.barrier.json").write_text(json.dumps(report, sort_keys=True))
        if report["decision"] != "barrier-ready":
            print(
                "Adoption barrier failed: "
                + report["decision"]
                + "; inspect adoption-"
                + label
                + ".barrier.json before retrying",
                file=sys.stderr,
            )
            sys.exit(1)
    elif mode == "expected":
        workflow = load(fixture / "adoption-response-barrier.workflow.json")
        assert workflow["status"]["phase"] == "Running"
        assert readiness.named(workflow, barrier, "Suspend")["phase"] == "Running"
        assert not any(
            n["displayName"] == "create-" + ("pointer" if operation in POINTERS else "receipt")
            and n["type"] == "Pod"
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
            []
            if operation in CONFLICTS
            else [
                json.loads(line)
                for line in (fixture / "ledger-response-events.jsonl").read_text().splitlines()
            ],
            load(fixture / "ledger-response-expected.json"),
            operation,
            winner=load(fixture / "adoption-conflict-winner.json")
            if operation in CONFLICTS
            else None,
            diagnostic=(fixture / "adoption-parent-worker.log").read_text()
            if operation in CONFLICTS
            else "",
        )
        (results / f"adoption-{label}.outcome.json").write_text(json.dumps(report, sort_keys=True))
    else:
        raise ValueError("Unknown create-fault fixture mode")
