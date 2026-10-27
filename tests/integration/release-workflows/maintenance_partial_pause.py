"""Validate approved CI maintenance interruption and explicit Deployment recovery."""

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import maintenance_pause_evidence as maintenance
import yaml
from jsonschema import Draft202012Validator

DIRECTORY = Path("gitops/release-workflows")
RESOURCES = ("root", "child")
TARGETS = (*RESOURCES, "deployment")
BARRIER = "partial-pause-barrier"
STOPPED = "Stopped with strategy 'Stop'"


def template(resource: str) -> dict[str, Any]:
    """Keep the published parent intact except for one post-patch suspension."""
    assert resource in TARGETS
    original = yaml.safe_load((DIRECTORY / "release-maintenance-pause-v1.yaml").read_text())
    result = deepcopy(original)
    result["metadata"] = {
        "name": "perfeng-maintenance-partial-" + resource + "-ci",
        "namespace": "argo-workflows",
    }
    assert not result["spec"].get("onExit") and not result["spec"].get("hooks")
    steps = result["spec"]["templates"][0]["steps"]
    index = next(i for i, group in enumerate(steps) if group[0]["name"] == "pause-" + resource)
    steps.insert(index + 1, [{"name": BARRIER, "template": BARRIER}])
    result["spec"]["templates"].append({"name": BARRIER, "suspend": {}})
    restored = deepcopy(result)
    restored["metadata"] = original["metadata"]
    del restored["spec"]["templates"][0]["steps"][index + 1]
    restored["spec"]["templates"].pop()
    assert restored == original
    return result


def validate(
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    resource: str,
    parameters: dict[str, str],
    raw: bytes,
    lock: bytes,
    before: dict[str, Any],
    *,
    stopped: bool,
) -> None:
    """Bind the specific stop to real readiness, ledger planning and completed patches."""
    assert resource in TARGETS
    assert workflow["metadata"]["namespace"] == "argo-workflows" and workflow["metadata"]["uid"]
    assert workflow["spec"]["workflowTemplateRef"] == {
        "name": template(resource)["metadata"]["name"]
    }
    assert workflow["spec"].get("shutdown") == ("Stop" if stopped else None)
    assert workflow["status"]["phase"] == ("Failed" if stopped else "Running")
    if stopped:
        assert workflow["status"]["message"] == STOPPED
    normalized = deepcopy(workflow)
    normalized["spec"]["workflowTemplateRef"] = {"name": "perfeng-release-maintenance-pause-v1"}
    named, outputs = maintenance.readiness.named, maintenance.readiness.outputs
    preflight = outputs(named(workflow, "preflight", "Steps"))["result"]
    health, _ = maintenance.running(normalized, parameters, preflight, lock, before)

    def observe(name: str) -> Any:
        return outputs(named(workflow, name))["observation"]

    assert (
        sorted([observe("receipt"), observe("pointer")], key=lambda o: o["metadata"]["name"])
        == before["records"]
    )
    planned = maintenance.policy.plan_adoption(
        raw, health, observe("root"), observe("receipt"), observe("pointer"), observe("root-after")
    )
    assert planned["create-receipt"] == planned["create-pointer"] == "false"
    baseline = named(workflow, "baseline")
    assert baseline["templateRef"] == {
        "name": "perfeng-plan-release-adoption-v3",
        "template": "run",
    }
    assert {
        p["name"]: json.loads(p["value"]) if p["name"] == "result" else p["value"]
        for p in baseline["outputs"]["parameters"]
        if "value" in p
    } == planned
    sequence = [named(workflow, "root-after"), baseline]
    for target in TARGETS[: TARGETS.index(resource) + 1]:
        for operation in ("guard", "pause"):
            node = named(workflow, operation + "-" + target)
            assert node["templateRef"] == {
                "name": "perfeng-maintenance-resources-v1",
                "template": ("patch" if operation == "pause" else operation) + "-" + target,
            }
            sequence.append(node)
        actual = (
            before["deployment"]
            if target == "deployment"
            else next(
                o
                for o in before["applications"]["items"]
                if o["metadata"]["name"]
                == "perfeng-local-" + ("root" if target == "root" else "control-plane")
            )
        )
        if target == "deployment":
            assert actual["spec"]["replicas"] == 1
        else:
            assert actual["spec"]["syncPolicy"]["automated"]["enabled"] is False
        guard = observe("guard-" + target)
        assert guard["uid"] == actual["metadata"]["uid"] and guard["spec"] == actual["spec"]
    barrier = named(workflow, BARRIER, "Suspend")
    assert barrier["phase"] == ("Failed" if stopped else "Running")
    if stopped:
        assert barrier["message"] == STOPPED
        assert datetime.fromisoformat(barrier["startedAt"]) <= datetime.fromisoformat(
            barrier["finishedAt"]
        )
    for node in sequence:
        assert node["phase"] == "Succeeded"
        assert datetime.fromisoformat(node["startedAt"]) <= datetime.fromisoformat(
            node["finishedAt"]
        )
    for earlier, later in zip(sequence, sequence[1:] + [barrier], strict=True):
        assert datetime.fromisoformat(earlier["finishedAt"]) <= datetime.fromisoformat(
            later["startedAt"]
        )
    pods = {n["id"]: n for n in workflow["status"]["nodes"].values() if n["type"] == "Pod"}
    assert len(workers) == len(pods) and {w["nodeID"] for w in workers} == pods.keys()
    for worker in workers:
        assert worker["account"] == "release-maintenance-pause" and worker["uid"]
        assert worker["exitCode"] == 0 and pods[worker["nodeID"]]["phase"] == "Succeeded"
    later = TARGETS[TARGETS.index(resource) + 1 :]
    forbidden = {"checkpoint", "observe"} | {
        op + "-" + target for target in later for op in ("guard", "pause")
    }
    allowed_writes = {n["displayName"] for n in sequence[2:]}
    for node in workflow["status"]["nodes"].values():
        ref = node.get("templateRef", {})
        native = ref.get("name") == "perfeng-maintenance-resources-v1" and ref.get(
            "template", ""
        ).startswith(("patch-", "guard-"))
        if (
            node["displayName"] in forbidden
            or ref.get("name") == "perfeng-verify-release-quiescence-v3"
            or (native and node["displayName"] not in allowed_writes)
        ):
            assert node["phase"] in {"Skipped", "Omitted"} and node["type"] != "Pod"
        result = outputs(node).get("result")
        assert not isinstance(result, dict) or result.get("kind") != "PerfengMaintenanceCheckpoint"
        if node["displayName"] in {workflow["metadata"]["name"], "pause", "checkpoint", "observe"}:
            assert "result" not in outputs(node)


def stopped_identity(
    workflow: dict[str, Any], suspended: dict[str, Any], workers: Any, previous_workers: Any
) -> None:
    """Require exactly the previously inspected Workflow and worker set."""
    assert all(
        workflow["metadata"][k] == suspended["metadata"][k] for k in ("uid", "name", "namespace")
    )
    assert workflow["spec"] == {**suspended["spec"], "shutdown": "Stop"}
    assert workers == previous_workers


def checkpoint(
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    interrupted: dict[str, Any],
    parameters: dict[str, str],
    lock: bytes,
    state: dict[str, Any],
) -> dict[str, Any]:
    """Require a fresh, independent unchanged read-only parent and real quiescence."""
    named, outputs = maintenance.readiness.named, maintenance.readiness.outputs
    assert (
        workflow["metadata"]["uid"]
        and workflow["metadata"]["uid"] != interrupted["metadata"]["uid"]
    )
    assert workflow["metadata"]["namespace"] == "argo-workflows"
    assert workflow["spec"]["workflowTemplateRef"] == {
        "name": "perfeng-release-maintenance-checkpoint-v3"
    }
    assert not workflow["spec"].get("shutdown") and workflow["status"]["phase"] == "Succeeded"
    assert {
        p["name"]: p["value"] for p in workflow["spec"]["arguments"]["parameters"]
    } == parameters
    preflight = named(workflow, "preflight", "Steps")
    preflight_result = outputs(preflight)["result"]
    assert preflight_result == outputs(named(interrupted, "preflight", "Steps"))["result"]
    observe = named(workflow, "observe")
    assert preflight["phase"] == observe["phase"] == "Succeeded"
    assert observe["templateRef"] == {
        "name": "perfeng-verify-release-quiescence-v3",
        "template": "run",
    }
    assert datetime.fromisoformat(interrupted["status"]["finishedAt"]) <= datetime.fromisoformat(
        preflight["startedAt"]
    )
    assert (
        datetime.fromisoformat(preflight["finishedAt"])
        <= datetime.fromisoformat(observe["startedAt"])
        <= datetime.fromisoformat(observe["finishedAt"])
    )
    pods = {n["id"]: n for n in workflow["status"]["nodes"].values() if n["type"] == "Pod"}
    assert len(workers) == len(pods) and {w["nodeID"] for w in workers} == pods.keys()
    for worker in workers:
        assert (
            worker["account"] == "release-maintenance-checkpoint"
            and worker["uid"]
            and worker["exitCode"] == 0
        )
        assert pods[worker["nodeID"]]["phase"] == "Succeeded"
    result = outputs(workflow["status"]["nodes"][workflow["metadata"]["name"]])["result"]
    assert result == outputs(observe)["result"]
    Draft202012Validator(
        json.loads(Path("deployments/maintenance-checkpoint-v1.schema.json").read_text())
    ).validate(result)
    resources = [
        next(o for o in state["applications"]["items"] if o["metadata"]["name"] == name)
        for name in ("perfeng-local-root", "perfeng-local-control-plane")
    ] + [state["deployment"]]
    expected = maintenance.policy.verify_quiescence(
        preflight_result,
        lock,
        parameters["selection-revision"],
        resources,
        state["podList"],
        resources,
        state["namespace"],
    )

    # Native verification and the independent API snapshot sample at different
    # times. Compare their full contract except fresh API resourceVersions.
    def identity(value: dict[str, Any]) -> dict[str, Any]:
        return {
            **value,
            "podListResourceVersion": "sampled",
            "resources": [
                {k: v for k, v in r.items() if k != "resourceVersion"} for r in value["resources"]
            ],
        }

    assert identity(result) == identity(expected)
    return result


def main() -> None:
    mode, resource, directory, output = sys.argv[1:]
    assert resource in TARGETS
    fixture, results = Path(directory), Path(output)
    prefix = "maintenance-partial-" + resource

    def load(suffix: str) -> Any:
        return json.loads((fixture / f"{prefix}.{suffix}.json").read_text())

    if mode == "template":
        print(json.dumps(template(resource)))
        return
    if mode == "installed":
        expected, installed = template(resource), load("installed")
        assert installed["spec"] == expected["spec"]
        assert all(installed["metadata"][k] == v for k, v in expected["metadata"].items())
        return
    assert mode in {"barrier", "stopped", "verify", "checkpoint", "recovered"}
    assert resource == "deployment" or mode in {"barrier", "verify"}
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
                "Partial pause barrier failed: "
                + report["decision"]
                + "; inspect the safe barrier report"
            )

    def state(label: str) -> dict[str, Any]:
        value = {
            key: json.loads((fixture / f"{prefix}-{label}.{key}.json").read_text())
            for key in (
                "applications",
                "deployment",
                "namespace",
                "server",
                "workloads",
                "pods",
                "records",
                "secrets",
                "config",
                "git",
            )
        }
        if resource == "deployment":
            value["serverPod"] = json.loads(
                (fixture / f"{prefix}-{label}.server-pods.json").read_text()
            )
            if label == "after":
                value["podList"] = json.loads(
                    (fixture / f"{prefix}-{label}.pod-list.json").read_text()
                )
        return value

    before = state("before")
    workflow = load("barrier" if mode == "barrier" else "workflow")
    validate(
        workflow,
        load("workers"),
        resource,
        json.loads((fixture / "parent-parameters.json").read_text()),
        (fixture / "parent-request.json").read_bytes(),
        Path("deployments/local-control-plane.json").read_bytes(),
        before,
        stopped=mode != "barrier",
    )
    if mode != "barrier":
        suspended = load("barrier")
        stopped_identity(workflow, suspended, load("workers"), load("workers-before"))
        if mode == "stopped":
            return
        after = state("after")
        if resource == "deployment":
            maintenance.retained(before, after, paused=True)
            deployment = after["deployment"]
            assert deployment["metadata"]["uid"] == before["deployment"]["metadata"]["uid"]
            assert deployment["spec"] == {**before["deployment"]["spec"], "replicas": 0}
            assert (
                deployment["status"]["observedGeneration"] == deployment["metadata"]["generation"]
            )
            assert all(
                deployment["status"].get(field, 0) == 0
                for field in ("replicas", "readyReplicas", "updatedReplicas", "availableReplicas")
            )
            report = None
            if mode in {"checkpoint", "recovered"}:
                report = checkpoint(
                    load("checkpoint-workflow"),
                    load("checkpoint-workers"),
                    workflow,
                    json.loads((fixture / "parent-parameters.json").read_text()),
                    Path("deployments/local-control-plane.json").read_bytes(),
                    after,
                )
            if mode == "checkpoint":
                (results / f"{prefix}.checkpoint.json").write_text(json.dumps(report))
                return
            if mode == "recovered":
                maintenance.retained(before, state("recovered"), paused=False)
                (results / f"{prefix}.recovered.json").write_text(
                    json.dumps(
                        {
                            "decision": "maintenance-deployment-recovery-verified",
                            "workflowUID": workflow["metadata"]["uid"],
                        }
                    )
                )
                return
        if resource != "deployment":
            for key in before:
                if key != "applications":
                    # Deployment status is controller bookkeeping; workload/Pod
                    # specs and identities are also retained by root_retained.
                    if key == "deployment":
                        assert before[key]["spec"] == after[key]["spec"]
                        assert before[key]["metadata"]["uid"] == after[key]["metadata"]["uid"]
                    else:
                        assert before[key] == after[key]
        for old, new in zip(
            sorted(before["applications"]["items"], key=lambda o: o["metadata"]["name"]),
            sorted(after["applications"]["items"], key=lambda o: o["metadata"]["name"]),
            strict=True,
        ):
            assert old["spec"] == new["spec"]
            ignored = {"resourceVersion", "managedFields"}
            if old["metadata"]["name"] == "perfeng-local-root":
                # ADR 0036 permits this bookkeeping after both fresh root
                # validations in maintenance_partial_root; spec/ownership stay exact.
                ignored.add("generation")
            assert {k: v for k, v in old["metadata"].items() if k not in ignored} == {
                k: v for k, v in new["metadata"].items() if k not in ignored
            }
        (results / f"{prefix}.outcome.json").write_text(
            json.dumps(
                {
                    "decision": "maintenance-partial-pause-stopped",
                    "resource": resource,
                    "workflowUID": workflow["metadata"]["uid"],
                }
            )
        )


if __name__ == "__main__":
    main()
