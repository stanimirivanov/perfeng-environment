"""Owned CI barriers and evidence for stale native maintenance JSON Patch tests."""

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import maintenance_pause_evidence as maintenance
import yaml

DIRECTORY = Path("gitops/release-workflows")
RESOURCES = ("root", "child", "deployment")
MARKER = "perfeng.io/ci-maintenance-guard"
BARRIER = "stale-guard-barrier"


def template(resource: str) -> dict[str, Any]:
    """Insert one suspension; reversing it must reproduce the complete source."""
    assert resource in RESOURCES
    original = yaml.safe_load((DIRECTORY / "release-maintenance-pause-v1.yaml").read_text())
    result = deepcopy(original)
    result["metadata"] = {
        "name": "perfeng-maintenance-stale-" + resource + "-ci",
        "namespace": "argo-workflows",
    }
    steps = result["spec"]["templates"][0]["steps"]
    index = next(i for i, group in enumerate(steps) if group[0]["name"] == "pause-" + resource)
    assert steps[index - 1][0]["name"] == "guard-" + resource
    steps.insert(index, [{"name": BARRIER, "template": BARRIER}])
    result["spec"]["templates"].append({"name": BARRIER, "suspend": {}})
    restored = deepcopy(result)
    restored["metadata"] = original["metadata"]
    del restored["spec"]["templates"][0]["steps"][index]
    restored["spec"]["templates"].pop()
    assert restored == original
    return result


def normalized(workflow: dict[str, Any], resource: str) -> dict[str, Any]:
    assert workflow["spec"]["workflowTemplateRef"] == {
        "name": template(resource)["metadata"]["name"]
    }
    assert not workflow["spec"].get("shutdown")
    result = deepcopy(workflow)
    result["spec"]["workflowTemplateRef"] = {"name": "perfeng-release-maintenance-pause-v1"}
    return result


def ready(
    workflow: dict[str, Any],
    resource: str,
    parameters: dict[str, str],
    raw: bytes,
    lock: bytes,
    before: dict[str, Any],
) -> dict[str, Any]:
    """Require actual selected readiness, existing baseline and the completed guard."""
    workflow = normalized(workflow, resource)
    preflight = maintenance.readiness.outputs(
        maintenance.readiness.named(workflow, "preflight", "Steps")
    )["result"]
    health, observations = maintenance.running(workflow, parameters, preflight, lock, before)

    def observe(name: str) -> Any:
        return maintenance.readiness.outputs(maintenance.readiness.named(workflow, name))[
            "observation"
        ]

    assert (
        sorted([observe("receipt"), observe("pointer")], key=lambda o: o["metadata"]["name"])
        == before["records"]
    )
    expected = maintenance.policy.plan_adoption(
        raw, health, observe("root"), observe("receipt"), observe("pointer"), observe("root-after")
    )
    assert expected["create-receipt"] == expected["create-pointer"] == "false"
    baseline = maintenance.readiness.named(workflow, "baseline")
    assert baseline["phase"] == "Succeeded"
    assert baseline["templateRef"] == {
        "name": "perfeng-plan-release-adoption-v3",
        "template": "run",
    }
    assert datetime.fromisoformat(
        maintenance.readiness.named(workflow, "root-after")["finishedAt"]
    ) <= datetime.fromisoformat(baseline["startedAt"])
    assert {
        p["name"]: json.loads(p["value"]) if p["name"] == "result" else p["value"]
        for p in baseline["outputs"]["parameters"]
        if "value" in p
    } == expected
    order = ["baseline"]
    for name in RESOURCES:
        order.append("guard-" + name)
        if name == resource:
            break
        order.append("pause-" + name)
    for name in order:
        node = maintenance.readiness.named(workflow, name)
        assert node["phase"] == "Succeeded"
        if name != "baseline":
            operation, target = name.split("-", 1)
            assert node["templateRef"] == {
                "name": "perfeng-maintenance-resources-v1",
                "template": ("patch" if operation == "pause" else operation) + "-" + target,
            }
    for left, right in zip(order, order[1:], strict=False):
        assert datetime.fromisoformat(
            maintenance.readiness.named(workflow, left)["finishedAt"]
        ) <= datetime.fromisoformat(maintenance.readiness.named(workflow, right)["startedAt"])
    guard = observe("guard-" + resource)
    assert datetime.fromisoformat(
        maintenance.readiness.named(workflow, "guard-" + resource)["finishedAt"]
    ) <= datetime.fromisoformat(
        maintenance.readiness.named(workflow, BARRIER, "Suspend")["startedAt"]
    )
    target = (
        before["deployment"]
        if resource == "deployment"
        else next(
            o
            for o in before["applications"]["items"]
            if o["metadata"]["name"]
            == "perfeng-local-" + ("root" if resource == "root" else "control-plane")
        )
    )
    assert guard["uid"] == target["metadata"]["uid"] and guard["spec"] == target["spec"]
    snapshot = (
        observe("root-after")
        if resource == "root"
        else observations["application" if resource == "child" else "deployment"]["snapshot"]
    )
    for field in ("labels", "annotations", "finalizers", "ownerReferences"):
        assert target["metadata"].get(field) == snapshot["metadata"].get(field)
    assert guard["resourceVersion"]
    return guard


def identity(resource: str, obj: dict[str, Any]) -> None:
    assert resource in RESOURCES
    kind, namespace, name = (
        ("Deployment", "perf-platform", "control-plane")
        if resource == "deployment"
        else (
            "Application",
            "argocd",
            "perfeng-local-" + ("root" if resource == "root" else "control-plane"),
        )
    )
    assert obj["kind"] == kind
    assert obj["metadata"]["namespace"] == namespace and obj["metadata"]["name"] == name
    assert obj["metadata"]["uid"] and obj["metadata"]["resourceVersion"]
    assert not obj["metadata"].get("deletionTimestamp")
    assert not obj["metadata"].get("ownerReferences")


def metadata(obj: dict[str, Any]) -> dict[str, Any]:
    # Server bookkeeping may change during comparisons; ownership must not.
    return {
        k: v for k, v in obj["metadata"].items() if k not in {"resourceVersion", "managedFields"}
    }


def marker_patch(
    resource: str, original: dict[str, Any], current: dict[str, Any], uid: str, remove: bool
) -> list[dict[str, Any]]:
    """Guard each fixture write; restore the original top-level metadata map."""
    identity(resource, original)
    identity(resource, current)
    field = "labels" if resource == "deployment" else "annotations"
    assert uid and MARKER not in original["metadata"].get(field, {})
    assert current["spec"] == original["spec"]
    wanted = deepcopy(metadata(original))
    marked = {**original["metadata"].get(field, {}), MARKER: uid}
    if remove:
        wanted[field] = marked
        assert current["metadata"]["resourceVersion"] != original["metadata"]["resourceVersion"]
    assert metadata(current) == wanted
    tests = [
        {"op": "test", "path": "/metadata/" + key, "value": current["metadata"][key]}
        for key in ("uid", "resourceVersion")
    ]
    tests.append({"op": "test", "path": "/spec", "value": current["spec"]})
    if remove and field not in original["metadata"]:
        return tests + [{"op": "remove", "path": "/metadata/" + field}]
    return tests + [
        {
            "op": "add",
            "path": "/metadata/" + field,
            "value": original["metadata"].get(field, {}) if remove else marked,
        }
    ]


def restored(
    resource: str,
    original: dict[str, Any],
    added: dict[str, Any],
    current: dict[str, Any],
    guard: dict[str, Any],
    uid: str,
) -> None:
    marker_patch(resource, original, added, uid, True)
    identity(resource, current)
    assert metadata(current) == metadata(original) and current["spec"] == original["spec"]
    assert current["metadata"]["resourceVersion"] not in {
        guard["resourceVersion"],
        added["metadata"]["resourceVersion"],
    }
    assert current["metadata"]["uid"] == guard["uid"] and current["spec"] == guard["spec"]


def refused(
    workflow: dict[str, Any], workers: list[dict[str, Any]], resource: str, diagnostic: str
) -> None:
    """Require native Invalid rejection, not a timeout, permission or setup failure."""
    normalized(workflow, resource)
    assert workflow["status"]["phase"] in {"Failed", "Error"}
    barrier = maintenance.readiness.named(workflow, BARRIER, "Suspend")
    assert barrier["phase"] == "Succeeded"
    failed = maintenance.readiness.named(workflow, "pause-" + resource)
    assert failed["phase"] in {"Failed", "Error"} and failed["templateRef"] == {
        "name": "perfeng-maintenance-resources-v1",
        "template": "patch-" + resource,
    }
    assert datetime.fromisoformat(barrier["finishedAt"]) <= datetime.fromisoformat(
        failed["startedAt"]
    )
    # Kubernetes 1.35 maps JSON Patch Apply failures to Invalid/422. Its public
    # message is generic; guard/API identity and the unchanged payload bind cause.
    assert "the server rejected our request due to an error in our request" in diagnostic
    assert not any(
        s in diagnostic.lower()
        for s in ("forbidden", "unauthorized", "timed out", "connection refused", "notfound")
    )
    pods = {n["id"]: n for n in workflow["status"]["nodes"].values() if n["type"] == "Pod"}
    assert len(workers) == len(pods) and {w["nodeID"] for w in workers} == pods.keys()
    for worker in workers:
        assert worker["account"] == "release-maintenance-pause" and worker["uid"]
        node = pods[worker["nodeID"]]
        if node["id"] == failed["id"]:
            assert worker["exitCode"] == 64
        else:
            assert node["phase"] == "Succeeded" and worker["exitCode"] == 0
    later = RESOURCES[RESOURCES.index(resource) + 1 :]
    forbidden = (
        {"guard-" + n for n in later} | {"pause-" + n for n in later} | {"checkpoint", "observe"}
    )
    for node in workflow["status"]["nodes"].values():
        ref = node.get("templateRef", {})
        if ref.get("name") == "perfeng-maintenance-resources-v1" and ref.get(
            "template", ""
        ).startswith(("guard-", "patch-")):
            assert node["displayName"] == ref["template"].replace("patch-", "pause-", 1)
        if ref.get("name") == "perfeng-verify-release-quiescence-v3":
            assert node["phase"] in {"Skipped", "Omitted"} and node["type"] != "Pod"
        if node["displayName"] in forbidden:
            assert node["phase"] in {"Skipped", "Omitted"} and node["type"] != "Pod"
        result = maintenance.readiness.outputs(node).get("result")
        assert not isinstance(result, dict) or result.get("kind") != "PerfengMaintenanceCheckpoint"
        assert "result" not in maintenance.readiness.outputs(node) or node["displayName"] not in {
            workflow["metadata"]["name"],
            "pause",
            "checkpoint",
            "observe",
        }


def main() -> None:
    mode, resource, directory, output = sys.argv[1:]
    fixture, results = Path(directory), Path(output)
    prefix = "maintenance-stale-" + resource

    def load(suffix: str) -> Any:
        return json.loads((fixture / f"{prefix}.{suffix}.json").read_text())

    def save(suffix: str, value: Any) -> None:
        (fixture / f"{prefix}.{suffix}.json").write_text(json.dumps(value))

    if mode == "template":
        print(json.dumps(template(resource)))
        return
    if mode == "installed":
        installed = load("installed")
        expected = template(resource)
        assert installed["spec"] == expected["spec"]
        assert all(installed["metadata"][k] == v for k, v in expected["metadata"].items())
        return
    if mode == "barrier":
        from adoption_response import barrier_diagnostics

        report = barrier_diagnostics(
            load("barrier"),
            load("barrier-pods"),
            BARRIER,
            (fixture / f"{prefix}.state.txt").read_text().strip(),
        )
        (results / f"{prefix}.barrier.json").write_text(json.dumps(report))
        if report["decision"] != "barrier-ready":
            raise SystemExit(
                "Maintenance guard barrier failed: "
                + report["decision"]
                + "; inspect the safe barrier report"
            )
        return
    workflow = load("barrier" if mode in {"prepare", "remove", "resume"} else "workflow")
    uid = workflow["metadata"]["uid"]
    parameters = json.loads((fixture / "parent-parameters.json").read_text())

    def state(label: str) -> dict[str, Any]:
        return {
            key: json.loads((fixture / f"{prefix}-{label}.{suffix}.json").read_text())
            for key, suffix in (
                ("applications", "applications"),
                ("deployment", "deployment"),
                ("namespace", "namespace"),
                ("server", "server"),
                ("workloads", "workloads"),
                ("pods", "pods"),
                ("records", "records"),
                ("secrets", "secrets"),
                ("config", "config"),
                ("git", "git"),
            )
        }

    before = state("before")
    guard = ready(
        workflow,
        resource,
        parameters,
        (fixture / "parent-request.json").read_bytes(),
        Path("deployments/local-control-plane.json").read_bytes(),
        before,
    )
    original = load("target")
    assert original["metadata"]["uid"] == guard["uid"] and original["spec"] == guard["spec"]
    captured = (
        before["deployment"]
        if resource == "deployment"
        else next(
            o
            for o in before["applications"]["items"]
            if o["metadata"]["name"] == original["metadata"]["name"]
        )
    )
    assert metadata(original) == metadata(captured)
    if mode in {"prepare", "remove", "resume"}:
        assert workflow["status"]["phase"] == "Running"
        assert maintenance.readiness.named(workflow, BARRIER, "Suspend")["phase"] == "Running"
        assert not any(
            n["type"] == "Pod" and n["displayName"] == "pause-" + resource
            for n in workflow["status"]["nodes"].values()
        )
    if mode == "prepare":
        save("add-patch", marker_patch(resource, original, original, uid, False))
    elif mode == "remove":
        save("remove-patch", marker_patch(resource, original, load("added"), uid, True))
    elif mode in {"resume", "verify"}:
        restored(resource, original, load("added"), load("restored"), guard, uid)
        if mode == "verify":
            after = state("after")
            target = (
                after["deployment"]
                if resource == "deployment"
                else next(
                    o
                    for o in after["applications"]["items"]
                    if o["metadata"]["name"] == original["metadata"]["name"]
                )
            )
            identity(resource, target)
            assert target["spec"] == original["spec"] and metadata(target) == metadata(original)
            for key in ("server", "workloads", "pods", "records", "secrets", "config", "git"):
                assert before[key] == after[key]
            refused(workflow, load("workers"), resource, (fixture / f"{prefix}.log").read_text())
            (results / f"{prefix}.outcome.json").write_text(
                json.dumps(
                    {
                        "decision": "stale-maintenance-patch-refused",
                        "resource": resource,
                        "workflowUID": uid,
                        "resourceUID": guard["uid"],
                    }
                )
            )
    else:
        raise ValueError("Unknown guard fixture mode")


if __name__ == "__main__":
    main()
