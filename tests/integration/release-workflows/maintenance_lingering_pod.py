"""Bind a disposable gated Pod to real checkpoint refusal and conditional cleanup."""

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import maintenance_partial_pause as partial

PREFIX = "maintenance-lingering"
NAME = "perfeng-ci-maintenance-lingering"
GATE = "perfeng.io/ci-maintenance-lingering"
REFUSAL = (
    "Quiescence refused: inspect reviewed inputs, ownership and observations; no success evidence"
)


def manifest(deployment: dict[str, Any], owner: str) -> dict[str, Any]:
    """Use selected image identity without running a container or mounting credentials."""
    assert owner and deployment["spec"]["replicas"] == 0
    assert deployment["metadata"]["name"] == "control-plane"
    assert deployment["metadata"]["namespace"] == "perf-platform"
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": NAME,
            "namespace": "perf-platform",
            # Match the checkpoint selector but no Deployment ReplicaSet's hash
            # selector, so scale-zero controllers cannot adopt/delete the fixture.
            "labels": {
                **partial.maintenance.policy.WORKLOAD_LABELS,
                "pod-template-hash": "perfeng-ci-lingering",
            },
            "annotations": {GATE: owner},
        },
        "spec": {
            "schedulingGates": [{"name": GATE}],
            "automountServiceAccountToken": False,
            "restartPolicy": "Never",
            "containers": [
                {
                    "name": "never-run",
                    "image": deployment["spec"]["template"]["spec"]["containers"][0]["image"],
                    "command": ["/not-executed"],
                    "resources": {"requests": {"cpu": "1m", "memory": "1Mi"}},
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": 10001,
                        "allowPrivilegeEscalation": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {"drop": ["ALL"]},
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                }
            ],
        },
    }


def pod_identity(pod: dict[str, Any], expected: dict[str, Any]) -> dict[str, str]:
    """Reject adoption, scheduling, execution, deletion or altered fixture ownership."""
    assert pod["apiVersion"] == "v1" and pod["kind"] == "Pod"
    metadata, spec = pod["metadata"], pod["spec"]
    for key, value in expected["metadata"].items():
        assert metadata[key] == value
    assert metadata["uid"] and metadata["resourceVersion"]
    assert not metadata.get("ownerReferences") and not metadata.get("deletionTimestamp")
    assert not metadata.get("finalizers")
    for key, value in expected["spec"].items():
        if key == "containers":
            assert len(spec[key]) == 1
            assert all(spec[key][0][k] == v for k, v in value[0].items())
        else:
            assert spec[key] == value
    assert not spec.get("nodeName") and not spec.get("volumes")
    assert not spec.get("initContainers") and not spec.get("ephemeralContainers")
    status = pod.get("status", {})
    assert status.get("phase") in {None, "Pending"}
    assert not status.get("containerStatuses") and not status.get("initContainerStatuses")
    return {k: metadata[k] for k in ("name", "namespace", "uid", "resourceVersion")}


def refusal(
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    interrupted: dict[str, Any],
    parameters: dict[str, str],
    lock: bytes,
    states: list[dict[str, Any]],
    expected: dict[str, Any],
    created: dict[str, Any],
    log: str,
) -> dict[str, Any]:
    """Combine native generic policy refusal with a precise, bracketed counterfactual."""
    named, outputs = partial.maintenance.readiness.named, partial.maintenance.readiness.outputs
    assert workflow["metadata"]["namespace"] == "argo-workflows"
    assert (
        workflow["metadata"]["uid"]
        and workflow["metadata"]["uid"] != interrupted["metadata"]["uid"]
    )
    assert workflow["spec"]["workflowTemplateRef"] == {
        "name": "perfeng-release-maintenance-checkpoint-v3"
    }
    assert not workflow["spec"].get("shutdown") and workflow["status"]["phase"] in {
        "Failed",
        "Error",
    }
    assert {
        p["name"]: p["value"] for p in workflow["spec"]["arguments"]["parameters"]
    } == parameters
    preflight, observe = named(workflow, "preflight", "Steps"), named(workflow, "observe")
    assert preflight["phase"] == "Succeeded" and observe["phase"] in {"Failed", "Error"}
    assert (
        outputs(preflight)["result"] == outputs(named(interrupted, "preflight", "Steps"))["result"]
    )
    assert observe["type"] == "Pod" and observe["templateRef"] == {
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
    assert {w["nodeID"] for w in workers} == pods.keys() and len(workers) == len(pods)
    for worker in workers:
        assert worker["uid"] and worker["account"] == "release-maintenance-checkpoint"
        assert worker["exitCode"] == (2 if worker["nodeID"] == observe["id"] else 0)
        if worker["nodeID"] != observe["id"]:
            assert pods[worker["nodeID"]]["phase"] == "Succeeded"
    assert REFUSAL in log.splitlines()
    assert "result" not in outputs(observe)
    assert "result" not in outputs(workflow["status"]["nodes"][workflow["metadata"]["name"]])
    identity = pod_identity(created, expected)
    assert len(states) == 2
    resources = None
    for state in states:
        listing = state["podList"]
        assert listing["apiVersion"] == "v1" and listing["kind"] == "PodList"
        assert len(listing["items"]) == 1 and not listing["metadata"].get("continue")
        current = pod_identity(listing["items"][0], expected)
        assert all(current[k] == identity[k] for k in ("uid", "name", "namespace"))
        observed = [
            next(o for o in state["applications"]["items"] if o["metadata"]["name"] == name)
            for name in partial.maintenance.policy.NAMES
        ] + [state["deployment"]]
        if resources is not None:
            assert [(o["metadata"]["uid"], o["spec"]) for o in observed] == resources
        resources = [(o["metadata"]["uid"], o["spec"]) for o in observed]
        args = (outputs(preflight)["result"], lock, parameters["selection-revision"], observed)
        try:
            partial.maintenance.policy.verify_quiescence(
                *args, listing, observed, state["namespace"]
            )
        except partial.maintenance.policy.ReadinessRefused as error:
            assert str(error) == "Pods remain or absence is unproven"
        else:
            raise AssertionError("Lingering Pod unexpectedly accepted")
        absent = {**listing, "items": []}
        partial.maintenance.policy.verify_quiescence(*args, absent, observed, state["namespace"])
    return {
        "decision": "lingering-pod-refusal-verified",
        "workflowUID": workflow["metadata"]["uid"],
        "podUID": identity["uid"],
    }


def fresh_checkpoint(
    rejected: dict[str, Any], fresh: dict[str, Any], report: dict[str, Any]
) -> None:
    """Do not reuse a rejected execution or successful evidence from before cleanup."""
    assert report["decision"] == "lingering-pod-refusal-verified"
    assert report["workflowUID"] == rejected["metadata"]["uid"]
    assert fresh["metadata"]["uid"] and fresh["metadata"]["uid"] != rejected["metadata"]["uid"]
    preflight = partial.maintenance.readiness.named(fresh, "preflight", "Steps")
    assert datetime.fromisoformat(rejected["status"]["finishedAt"]) <= datetime.fromisoformat(
        preflight["startedAt"]
    )


def main() -> None:
    mode, directory, output = sys.argv[1:]
    fixture, results = Path(directory), Path(output)

    def load(name: str) -> Any:
        return json.loads((fixture / f"{name}.json").read_text())

    expected = manifest(
        load("maintenance-partial-deployment-after.deployment"),
        load("maintenance-partial-deployment.workflow")["metadata"]["uid"],
    )
    if mode == "manifest":
        print(json.dumps(expected))
        return
    if mode == "fresh":
        fresh_checkpoint(
            load(f"{PREFIX}.workflow"),
            load("maintenance-partial-deployment.checkpoint-workflow"),
            json.loads((results / f"{PREFIX}.refusal.json").read_text()),
        )
        return
    assert mode == "refusal"
    states = []
    for label in ("before", "after"):
        prefix = f"{PREFIX}-{label}"
        state = {
            key: load(f"{prefix}.{key}")
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
        state["podList"] = load(f"{prefix}.pod-list")
        # root_state explicitly samples only retained foundation Pods. Check the
        # sole selector-matching exception separately, never erase it from API evidence.
        state["serverPod"] = {"items": []}
        baseline = deepcopy(state)
        baseline.update(
            {
                key: load(f"maintenance-partial-deployment-before.{key}")
                for key in state
                if key not in {"serverPod", "podList"}
            }
        )
        baseline["serverPod"] = load("maintenance-partial-deployment-before.server-pods")
        partial.maintenance.retained(baseline, state, paused=True)
        states.append(state)
    report = refusal(
        load(f"{PREFIX}.workflow"),
        load(f"{PREFIX}.workers"),
        load("maintenance-partial-deployment.workflow"),
        load("parent-parameters"),
        Path("deployments/local-control-plane.json").read_bytes(),
        states,
        expected,
        load(f"{PREFIX}.created"),
        (fixture / f"{PREFIX}.log").read_text(),
    )
    current = pod_identity(states[-1]["podList"]["items"][0], expected)
    delete = {
        "apiVersion": "v1",
        "kind": "DeleteOptions",
        "preconditions": {k: current[k] for k in ("uid", "resourceVersion")},
    }
    (fixture / f"{PREFIX}.delete.json").write_text(json.dumps(delete))
    (results / f"{PREFIX}.refusal.json").write_text(json.dumps(report))


if __name__ == "__main__":
    main()
