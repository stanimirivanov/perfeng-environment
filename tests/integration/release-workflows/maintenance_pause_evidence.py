"""Offline evidence checks for the owned CI pause and explicit manual recovery."""

import json
import re
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import readiness_parent_evidence as readiness
from jsonschema import Draft202012Validator

from tests.release_verifier_support import policy


def replacement(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Permit one same-ReplicaSet Pod replacement and its admission token-volume name."""
    assert len(before["items"]) == len(after["items"]) == 1
    old, new = before["items"][0], after["items"][0]
    assert old["metadata"]["uid"] != new["metadata"]["uid"]
    assert old["metadata"]["name"] != new["metadata"]["name"]
    assert old["metadata"]["namespace"] == new["metadata"]["namespace"] == "perf-platform"
    for field in ("labels", "ownerReferences"):
        assert old["metadata"][field] == new["metadata"][field]
    owners = new["metadata"]["ownerReferences"]
    assert len(owners) == 1 and owners[0]["kind"] == "ReplicaSet" and owners[0]["controller"]
    assert owners[0]["uid"]
    for pod in (old, new):
        assert not pod["metadata"].get("deletionTimestamp")
        assert pod["status"]["phase"] == "Running"
        assert any(
            c["type"] == "Ready" and c["status"] == "True" for c in pod["status"]["conditions"]
        )

    def spec(pod: dict[str, Any]) -> dict[str, Any]:
        value = deepcopy(pod["spec"])
        assert value["serviceAccountName"] == "control-plane"
        tokens = [
            v for v in value["volumes"] if re.fullmatch(r"kube-api-access-[a-z0-9]+", v["name"])
        ]
        assert len(tokens) == 1 and "projected" in tokens[0]
        name = tokens[0]["name"]
        tokens[0]["name"] = "kube-api-access-CI"
        for container in value.get("containers", []) + value.get("initContainers", []):
            for mount in container.get("volumeMounts", []):
                if mount["name"] == name:
                    assert mount["mountPath"] == "/var/run/secrets/kubernetes.io/serviceaccount"
                    mount["name"] = "kube-api-access-CI"
        return value

    assert spec(old) == spec(new)


def retained(before: dict[str, Any], after: dict[str, Any], *, paused: bool) -> None:
    """Compare full fixture workload specs, allowing exactly scale-down or one new Pod."""
    old_uid = before["serverPod"]["items"][0]["metadata"]["uid"]
    assert len(before["serverPod"]["items"]) == 1
    expected = deepcopy(before["server"])
    workloads = deepcopy(before["workloads"])
    if paused:
        assert after["serverPod"]["items"] == []
        deployment = next(o for o in expected if o["kind"] == "Deployment")
        assert deployment["spec"]["replicas"] == 1
        deployment["spec"]["replicas"] = 0
        matching = [
            o
            for o in workloads
            if (o["kind"], o["namespace"], o["name"])
            == ("Deployment", "perf-platform", "control-plane")
        ]
        assert len(matching) == 1 and matching[0]["uid"] == deployment["uid"]
        matching[0]["spec"]["replicas"] = 0
    else:
        replacement(before["serverPod"], after["serverPod"])
    assert after["server"] == expected and after["workloads"] == workloads
    old = [o for o in before["pods"] if o["uid"] != old_uid]
    assert len(old) == 2 and len(before["pods"]) == 3
    if paused:
        assert after["pods"] == old
    else:
        pod = after["serverPod"]["items"][0]
        assert len(after["pods"]) == 3
        assert {"uid": pod["metadata"]["uid"], "spec": pod["spec"]} in after["pods"]
        assert [o for o in after["pods"] if o["uid"] != pod["metadata"]["uid"]] == old
    for key in ("records", "secrets", "config", "git"):
        assert before[key] == after[key]
    assert len(before["records"]) == 2
    old_apps = {a["metadata"]["name"]: a for a in before["applications"]["items"]}
    new_apps = {a["metadata"]["name"]: a for a in after["applications"]["items"]}
    assert (
        old_apps.keys() == new_apps.keys() == {"perfeng-local-root", "perfeng-local-control-plane"}
    )
    for name, app in new_apps.items():
        assert app["metadata"]["uid"] == old_apps[name]["metadata"]["uid"]
        assert not app["metadata"].get("deletionTimestamp") and not app.get("operation")
        assert app["spec"] == old_apps[name]["spec"]
        assert app["spec"]["syncPolicy"]["automated"]["enabled"] is False
    assert before["namespace"]["metadata"]["uid"] == after["namespace"]["metadata"]["uid"]


def running(
    workflow: dict[str, Any],
    parameters: dict[str, str],
    preflight: dict[str, Any],
    raw_lock: bytes,
    before: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind fresh selected readiness and native snapshots to this parent's API baseline."""
    assert workflow["spec"]["workflowTemplateRef"]["name"] == "perfeng-release-maintenance-pause-v1"
    assert {
        p["name"]: p["value"] for p in workflow["spec"]["arguments"]["parameters"]
    } == parameters
    assert readiness.outputs(readiness.named(workflow, "preflight", "Steps"))["result"] == preflight
    for field, parameter in (
        ("requestRevision", "request-revision"),
        ("requestDigest", "request-sha256"),
        ("selectionRevision", "selection-revision"),
    ):
        assert preflight[field] == parameters[parameter]
    assert preflight["clusterUID"] == before["namespace"]["metadata"]["uid"]
    observations = {
        name: readiness.outputs(readiness.named(workflow, name))["observation"]
        for name in ("application", "deployment", "postgres", "seaweedfs", "service")
    }
    health = policy.verify_readiness(
        preflight, raw_lock, observations, parameters["selection-revision"]
    )
    assert readiness.outputs(readiness.named(workflow, "readiness"))["result"] == health
    applications = {o["metadata"]["name"]: o for o in before["applications"]["items"]}
    for snapshot, actual in (
        (
            readiness.outputs(readiness.named(workflow, "root"))["observation"],
            applications["perfeng-local-root"],
        ),
        (observations["application"]["snapshot"], applications["perfeng-local-control-plane"]),
        (observations["deployment"]["snapshot"], before["deployment"]),
    ):
        assert snapshot["metadata"]["uid"] == actual["metadata"]["uid"]
        assert snapshot["spec"] == actual["spec"]
    sequence = [readiness.named(workflow, "preflight", "Steps")] + [
        readiness.named(workflow, name)
        for name in (
            "postgres",
            "seaweedfs",
            "application",
            "deployment",
            "service",
            "readiness",
            "root",
            "pointer",
            "receipt",
            "root-after",
        )
    ]
    for node in sequence:
        assert node["phase"] == "Succeeded"
        assert datetime.fromisoformat(node["startedAt"]) <= datetime.fromisoformat(
            node["finishedAt"]
        )
    for earlier, later in zip(sequence, sequence[1:], strict=False):
        assert datetime.fromisoformat(earlier["finishedAt"]) <= datetime.fromisoformat(
            later["startedAt"]
        )
    return health, observations


def success(
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    parameters: dict[str, str],
    preflight: dict[str, Any],
    raw: bytes,
    raw_lock: bytes,
    before: dict[str, Any],
    stopped: dict[str, Any],
) -> dict[str, Any]:
    """Require actual native ordering, selected readiness and adopted records before writes."""
    assert workflow["status"]["phase"] == "Succeeded"
    health, observations = running(workflow, parameters, preflight, raw_lock, before)

    def observe(name: str) -> Any:
        return readiness.outputs(readiness.named(workflow, name))["observation"]

    assert (
        sorted([observe("receipt"), observe("pointer")], key=lambda o: o["metadata"]["name"])
        == before["records"]
    )
    expected = policy.plan_adoption(
        raw, health, observe("root"), observe("receipt"), observe("pointer"), observe("root-after")
    )
    assert expected["create-receipt"] == expected["create-pointer"] == "false"
    baseline = readiness.named(workflow, "baseline")
    assert {
        p["name"]: json.loads(p["value"]) if p["name"] == "result" else p["value"]
        for p in baseline["outputs"]["parameters"]
        if "value" in p
    } == expected
    for name, snapshot in (
        ("root", observe("root-after")),
        ("child", observations["application"]["snapshot"]),
        ("deployment", observations["deployment"]["snapshot"]),
    ):
        guard = observe("guard-" + name)
        assert guard["uid"] == snapshot["metadata"]["uid"] and guard["spec"] == snapshot["spec"]
        assert guard["resourceVersion"]
    sequence = [readiness.named(workflow, "preflight", "Steps")] + [
        readiness.named(workflow, n)
        for n in (
            "postgres",
            "seaweedfs",
            "application",
            "deployment",
            "service",
            "readiness",
            "root",
            "pointer",
            "receipt",
            "root-after",
            "baseline",
            "guard-root",
            "pause-root",
            "guard-child",
            "pause-child",
            "guard-deployment",
            "pause-deployment",
        )
    ]
    for node in sequence:
        assert node["phase"] == "Succeeded"
        assert datetime.fromisoformat(node["startedAt"]) <= datetime.fromisoformat(
            node["finishedAt"]
        )
    for earlier, later in zip(sequence, sequence[1:], strict=False):
        assert datetime.fromisoformat(earlier["finishedAt"]) <= datetime.fromisoformat(
            later["startedAt"]
        )
    for operation in ("guard", "pause"):
        for resource in ("root", "child", "deployment"):
            node = readiness.named(workflow, operation + "-" + resource)
            assert node["templateRef"] == {
                "name": "perfeng-maintenance-resources-v1",
                "template": ("patch" if operation == "pause" else "guard") + "-" + resource,
            }
    pods = {n["id"]: n for n in workflow["status"]["nodes"].values() if n["type"] == "Pod"}
    assert len(workers) == len(pods) and {w["nodeID"] for w in workers} == pods.keys()
    for worker in workers:
        assert worker["account"] == "release-maintenance-pause" and worker["uid"]
        node = pods[worker["nodeID"]]
        if node["phase"] == "Succeeded":
            assert worker["exitCode"] == 0
        else:
            assert (
                node["displayName"] == "observe"
                and node["phase"] == "Failed"
                and worker["exitCode"] == 2
            )
    attempts = [n for n in pods.values() if n["displayName"] == "observe"]
    assert 1 <= len(attempts) <= 5
    result = readiness.outputs(workflow["status"]["nodes"][workflow["metadata"]["name"]])["result"]
    Draft202012Validator(
        json.loads(Path("deployments/maintenance-checkpoint-v1.schema.json").read_text())
    ).validate(result)
    assert any(
        n["phase"] == "Succeeded" and readiness.outputs(n).get("result") == result for n in attempts
    )
    assert all(
        datetime.fromisoformat(sequence[-1]["finishedAt"]) <= datetime.fromisoformat(n["startedAt"])
        for n in attempts
    )
    for field in ("requestRevision", "requestDigest", "selectionRevision", "clusterUID"):
        assert result[field] == preflight[field]
    actual = stopped["applications"]["items"] + [stopped["deployment"]]
    assert {(o["kind"], o["metadata"]["name"], o["metadata"]["uid"]) for o in actual} == {
        (o["kind"], o["name"], o["uid"]) for o in result["resources"]
    }
    deployment = stopped["deployment"]
    assert deployment["status"]["observedGeneration"] == deployment["metadata"]["generation"]
    assert deployment["spec"]["replicas"] == 0
    assert all(
        deployment["status"].get(k, 0) == 0
        for k in ("replicas", "readyReplicas", "updatedReplicas", "availableReplicas")
    )
    assert stopped["serverPod"]["items"] == []
    return result


def refusal(
    case: str,
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    parameters: dict[str, str],
    preflight: dict[str, Any],
    raw: bytes,
    raw_lock: bytes,
    before: dict[str, Any],
    baseline: list[dict[str, Any]],
    log: str,
) -> dict[str, Any]:
    """Bind a redacted baseline refusal to the exact live receipt fault, before any write."""
    assert case in {"missing-receipt", "replaced-receipt"}
    assert workflow["status"]["phase"] in {"Failed", "Error"}
    health, _ = running(workflow, parameters, preflight, raw_lock, before)

    def observe(name: str) -> Any:
        return readiness.outputs(readiness.named(workflow, name))["observation"]

    assert len(baseline) == 2
    pointer, original = sorted(baseline, key=lambda o: o["metadata"]["name"])
    receipt = observe("receipt")
    assert observe("pointer") == pointer
    assert before["records"] == sorted(
        [pointer] + ([receipt] if receipt else []), key=lambda o: o["metadata"]["name"]
    )
    if case == "missing-receipt":
        assert receipt is None
    else:
        assert receipt and receipt["metadata"]["uid"] != original["metadata"]["uid"]
        assert receipt["metadata"]["uid"]
        assert {k: v for k, v in receipt.items() if k != "metadata"} == {
            k: v for k, v in original.items() if k != "metadata"
        }
        for key in ("name", "namespace", "labels", "annotations"):
            assert receipt["metadata"][key] == original["metadata"][key]
    root, root_after = observe("root"), observe("root-after")
    valid = policy.plan_adoption(raw, health, root, original, pointer, root_after)
    assert valid["create-receipt"] == valid["create-pointer"] == "false"
    try:
        policy.plan_adoption(raw, health, root, receipt, pointer, root_after)
    except policy.ReadinessRefused as error:
        assert str(error) == "current pointer conflicts or references a missing/replaced receipt"
    else:
        raise AssertionError("Maintenance accepted the injected ledger fault")
    failed = readiness.named(workflow, "baseline")
    assert failed["phase"] in {"Failed", "Error"} and failed["templateRef"] == {
        "name": "perfeng-plan-release-adoption-v3",
        "template": "run",
    }
    assert (
        datetime.fromisoformat(readiness.named(workflow, "root-after")["finishedAt"])
        <= datetime.fromisoformat(failed["startedAt"])
        <= datetime.fromisoformat(failed["finishedAt"])
    )
    nodes = list(workflow["status"]["nodes"].values())
    pods = {n["id"]: n for n in nodes if n["type"] == "Pod"}
    assert len(workers) == len(pods) and {w["nodeID"] for w in workers} == pods.keys()
    for worker in workers:
        node = pods[worker["nodeID"]]
        assert worker["account"] == "release-maintenance-pause" and worker["uid"]
        assert worker["exitCode"] == (2 if node["id"] == failed["id"] else 0)
        assert node["phase"] in (
            {"Failed", "Error"} if node["id"] == failed["id"] else {"Succeeded"}
        )
    # The published runtime deliberately redacts policy details. Fresh policy replay
    # above establishes the precise cause; unrelated runtime failures cannot qualify.
    assert log.splitlines() == [
        "Adoption refused: inspect reviewed inputs, ownership and observations; no success evidence"
    ]
    forbidden = {
        "guard-root",
        "pause-root",
        "guard-child",
        "pause-child",
        "guard-deployment",
        "pause-deployment",
        "checkpoint",
        "observe",
    }
    for node in nodes:
        ref = node.get("templateRef", {})
        write = ref.get("name") == "perfeng-maintenance-resources-v1" and ref.get(
            "template", ""
        ).startswith(("guard-", "patch-"))
        if (
            node["displayName"] in forbidden
            or write
            or ref.get("name") == "perfeng-verify-release-quiescence-v3"
        ):
            assert node["phase"] in {"Skipped", "Omitted"} and node["type"] != "Pod"
        result = readiness.outputs(node).get("result")
        assert not isinstance(result, dict) or result.get("kind") != "PerfengMaintenanceCheckpoint"
    assert "result" not in readiness.outputs(failed)
    assert "result" not in readiness.outputs(
        workflow["status"]["nodes"][workflow["metadata"]["name"]]
    )
    return {
        "decision": "maintenance-" + case + "-refused",
        "workflowUID": workflow["metadata"]["uid"],
        "pointerUID": pointer["metadata"]["uid"],
        "originalReceiptUID": original["metadata"]["uid"],
        "observedReceiptUID": receipt["metadata"]["uid"] if receipt else None,
    }


def main() -> None:
    """Read private snapshots; publish only the validated checkpoint/decision."""
    mode, directory, evidence_directory = sys.argv[1:]
    assert mode in {"paused", "recovered", "missing-receipt", "replaced-receipt"}
    fixture, results = Path(directory), Path(evidence_directory)

    def load(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    def state(label: str) -> dict[str, Any]:
        prefix = fixture / f"maintenance-{label}"
        return {
            key: load(Path(f"{prefix}.{suffix}.json"))
            for key, suffix in (
                ("serverPod", "server-pods"),
                ("pods", "pods"),
                ("server", "server"),
                ("workloads", "workloads"),
                ("records", "records"),
                ("secrets", "secrets"),
                ("config", "config"),
                ("git", "git"),
                ("applications", "applications"),
                ("deployment", "deployment"),
                ("namespace", "namespace"),
            )
        }

    if mode in {"missing-receipt", "replaced-receipt"}:
        before, after = state(mode + "-before"), state(mode + "-after")
        for key in ("server", "workloads", "pods", "records", "secrets", "config", "git"):
            assert before[key] == after[key]
        report = refusal(
            mode,
            load(results / f"maintenance-{mode}.workflow.json"),
            load(results / f"maintenance-{mode}.workers.json"),
            load(fixture / "parent-parameters.json"),
            load(results / "parent-first.result.json"),
            (fixture / "parent-request.json").read_bytes(),
            Path("deployments/local-control-plane.json").read_bytes(),
            before,
            load(fixture / "adoption-identity-baseline.json"),
            (fixture / f"maintenance-{mode}.worker.log").read_text(encoding="utf-8"),
        )
        (results / f"maintenance-{mode}.outcome.json").write_text(
            json.dumps(report, sort_keys=True) + "\n", encoding="utf-8"
        )
        return
    before, after = state("before"), state("paused" if mode == "paused" else "recovered")
    retained(before, after, paused=mode == "paused")
    if mode == "paused":
        result = success(
            load(results / "maintenance-pause.workflow.json"),
            load(results / "maintenance-pause.workers.json"),
            load(fixture / "parent-parameters.json"),
            load(results / "parent-first.result.json"),
            (fixture / "parent-request.json").read_bytes(),
            Path("deployments/local-control-plane.json").read_bytes(),
            before,
            after,
        )
        (results / "maintenance-pause.result.json").write_text(
            json.dumps(result, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()
