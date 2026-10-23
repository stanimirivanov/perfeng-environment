"""Bind complete adoption-parent evidence to native observations and stored bytes."""

import base64
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import readiness_parent_evidence as readiness
import yaml
from jsonschema import Draft202012Validator

from tests.release_verifier_support import policy


def record(workflow: dict[str, Any], name: str) -> Any:
    """Read one successful native observation, including an explicit missing record."""
    node = readiness.named(workflow, name)
    assert node["phase"] == "Succeeded"
    return readiness.outputs(node)["observation"]


def planned(workflow: dict[str, Any], name: str, expected: dict[str, Any]) -> None:
    """Match every planner output, permitting a published result only at finish."""
    if name != "finish":
        expected["result"] = None
    node = readiness.named(workflow, name)
    assert node["phase"] == "Succeeded"
    actual = {
        o["name"]: json.loads(o["value"]) if o["name"] == "result" else o["value"]
        for o in node["outputs"]["parameters"]
        if "value" in o
    }
    assert actual == expected


def success(
    workflow: dict[str, Any],
    parameters: dict[str, str],
    preflight: dict[str, Any],
    raw: bytes,
    raw_lock: bytes,
    api: dict[str, Any],
    first: bool,
    *,
    receipt_only: bool = False,
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
    assert not (first and receipt_only)
    assert (initial_receipt is None) == first
    assert (initial_pointer is None) == (first or receipt_only)
    if not first:
        assert initial_receipt == receipt
    if not first and not receipt_only:
        assert initial_pointer == pointer
    provenance = receipt["metadata"]["annotations"]
    if first:
        assert provenance["perfeng.io/first-workflow-uid"] == workflow["metadata"]["uid"]
        assert provenance["perfeng.io/first-request-revision"] == parameters["request-revision"]
    if receipt_only:
        assert provenance["perfeng.io/first-workflow-uid"] != workflow["metadata"]["uid"]
    assert root["spec"]["syncPolicy"]["automated"]["enabled"] is False
    assert "argocd.argoproj.io/skip-reconcile" not in root["metadata"].get("annotations", {})
    schema = json.loads(
        Path("deployments/release-adoption-v1.schema.json").read_text(encoding="utf-8")
    )
    for node_name, r, p, after in (
        ("plan", initial_receipt, initial_pointer, None),
        ("pointer-plan", receipt, initial_pointer, record(workflow, "root-after")),
        ("finish", receipt, pointer, record(workflow, "final-root")),
    ):
        expected = policy.plan_adoption(raw, health, root, r, p, after)
        planned(workflow, node_name, expected)
    for name in ("create-receipt", "create-pointer"):
        nodes = [n for n in workflow["status"]["nodes"].values() if n["displayName"] == name]
        created = first or (receipt_only and name == "create-pointer")
        assert len(nodes) == 1 and nodes[0]["phase"] == ("Succeeded" if created else "Skipped")
        assert nodes[0]["type"] == ("Pod" if created else "Skipped")
    result = readiness.outputs(workflow["status"]["nodes"][workflow["metadata"]["name"]])["result"]
    assert result == expected["result"]
    Draft202012Validator(schema["$defs"]["result"]).validate(result)
    Draft202012Validator(schema["$defs"]["pointer"]).validate(pointer["data"])
    Draft202012Validator(schema).validate(
        json.loads(base64.b64decode(receipt["binaryData"]["receipt.json"]))
    )
    assert base64.b64decode(receipt["binaryData"]["request.json"], validate=True) == raw
    return result


def refusal(
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    log: str,
    *,
    step: str = "root",
    records: list[dict[str, Any]] | None = None,
) -> None:
    """Require the owning refusal boundary; reads may succeed but no later write may run."""
    assert step in {"root", "plan"}
    assert workflow["status"]["phase"] in {"Failed", "Error"}
    assert workflow["spec"]["workflowTemplateRef"]["name"] == "perfeng-release-adopt-existing-v2"
    assert readiness.named(workflow, "readiness", "Steps")["phase"] == "Succeeded"
    failed = readiness.named(workflow, step)
    assert failed["phase"] in {"Failed", "Error"}
    failed_workers = [w for w in workers if w["exitCode"] not in (None, 0)]
    assert len(failed_workers) == 1
    assert failed_workers[0]["nodeID"] == failed["id"]
    assert failed_workers[0]["exitCode"] == (1 if step == "root" else 2)
    if step == "root":
        for text in (
            "forbidden",
            "perfeng-local-root",
            "release-adoption",
            "applications",
            "argocd",
        ):
            assert text in log
    else:
        assert "root is stale, syncing or not at the reviewed selection" in log
        root = record(workflow, "root")
        policy.application_identity(root, policy.NAMES[0])
        health = readiness.outputs(readiness.named(workflow, "readiness", "Steps"))["result"]
        assert root["status"]["sync"]["status"] == "OutOfSync"
        assert root["status"]["sync"]["revision"] == health["selectionRevision"]
        assert root["status"]["health"]["status"] == "Healthy" and not root["status"].get(
            "conditions"
        )
        assert root["status"]["sync"]["comparedTo"]["source"] == policy.ROOT_SOURCE
        assert root["spec"]["syncPolicy"]["automated"] == {"enabled": False}
        assert "argocd.argoproj.io/skip-reconcile" not in root["metadata"].get("annotations", {})
        differences = [o for o in root["status"]["resources"] if o["status"] != "Synced"]
        assert len(differences) == 1
        assert differences[0]["kind"] == "Namespace" and differences[0]["name"] == "perf-sut"
        assert not differences[0].get("namespace") and differences[0]["status"] == "OutOfSync"
        assert records is not None and len(records) == 2
        observed = [record(workflow, "current"), record(workflow, "existing-receipt")]
        assert sorted(observed, key=lambda o: o["metadata"]["name"]) == records
    assert "result" not in readiness.outputs(failed)
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
    if step == "plan":
        downstream -= {"current", "existing-receipt", "plan"}
    assert not any(
        n["displayName"] in downstream and n["type"] == "Pod"
        for n in workflow["status"]["nodes"].values()
    )


def partial(
    workflow: dict[str, Any],
    parameters: dict[str, str],
    preflight: dict[str, Any],
    raw: bytes,
    raw_lock: bytes,
    api: dict[str, Any],
    workers: list[dict[str, Any]],
    log: str,
) -> None:
    """Require an actual committed receipt followed by a quota-blocked pointer create."""
    health = readiness.success(
        workflow, preflight, parameters, api, raw_lock, adoption=True, parent_failed=True
    )
    root = record(workflow, "root")
    assert record(workflow, "current") is None and record(workflow, "existing-receipt") is None
    receipt = record(workflow, "stored-receipt")
    assert receipt == api["receipt"] and api["pointer"] is None
    assert (
        receipt["metadata"]["annotations"]["perfeng.io/first-workflow-uid"]
        == workflow["metadata"]["uid"]
    )
    assert (
        receipt["metadata"]["annotations"]["perfeng.io/first-request-revision"]
        == parameters["request-revision"]
    )
    planned(workflow, "plan", policy.plan_adoption(raw, health, root, None, None))
    planned(
        workflow,
        "pointer-plan",
        policy.plan_adoption(raw, health, root, receipt, None, record(workflow, "root-after")),
    )
    assert readiness.named(workflow, "create-receipt")["phase"] == "Succeeded"
    failed = readiness.named(workflow, "create-pointer")
    assert failed["phase"] in {"Failed", "Error"}
    nonzero = [w for w in workers if w["exitCode"] not in (None, 0)]
    assert (
        len(nonzero) == 1 and nonzero[0]["nodeID"] == failed["id"] and nonzero[0]["exitCode"] == 1
    )
    for text in (
        "forbidden",
        "exceeded quota: ci-adoption-receipt-only",
        "count/configmaps",
        "perfeng-local-current",
    ):
        assert text in log.lower()
    assert "result" not in readiness.outputs(failed)
    assert "result" not in readiness.outputs(
        workflow["status"]["nodes"][workflow["metadata"]["name"]]
    )
    assert not any(
        n["displayName"] in {"stored-pointer", "final-receipt", "final-root", "finish"}
        and n["type"] == "Pod"
        for n in workflow["status"]["nodes"].values()
    )


def quota_patch(original: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    """Raise only this run's initialized fixture quota, with atomic identity/version tests."""
    metadata = current["metadata"]
    assert current["apiVersion"] == "v1" and current["kind"] == "ResourceQuota"
    assert (
        metadata["name"] == "ci-adoption-receipt-only" and metadata["namespace"] == policy.NAMESPACE
    )
    assert (
        metadata["uid"] == original["metadata"]["uid"]
        and metadata["uid"]
        and metadata["resourceVersion"]
    )
    assert metadata["labels"] == {"perfeng.io/fixture": "receipt-only"}
    assert not metadata.get("deletionTimestamp") and not metadata.get("ownerReferences")
    hard = {"count/configmaps": "2"}
    assert original["spec"] == current["spec"] == {"hard": hard}
    assert current["status"]["hard"] == current["status"]["used"] == hard
    return [
        {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
        {"op": "test", "path": "/spec/hard", "value": hard},
        {"op": "replace", "path": "/spec/hard", "value": {"count/configmaps": "3"}},
    ]


def waiting(
    holder: dict[str, Any],
    workflows: list[dict[str, Any]],
    workers: list[dict[str, Any]],
    parameters: dict[str, str],
) -> None:
    """Prove both actual production parents queue behind this native suspended holder."""
    spec = yaml.safe_load(
        Path("gitops/release-workflows/release-adopt-existing-v2.yaml").read_text(encoding="utf-8")
    )["spec"]
    synchronization = spec["synchronization"]
    assert holder["metadata"]["namespace"] == "argo-workflows"
    assert holder["spec"]["synchronization"] == synchronization
    assert holder["spec"]["serviceAccountName"] == spec["serviceAccountName"]
    assert 0 < holder["spec"]["activeDeadlineSeconds"] <= 300
    assert holder["status"]["phase"] == "Running"
    node = holder["status"]["nodes"][holder["metadata"]["name"]]
    assert node["type"] == "Suspend" and node["phase"] == "Running"
    holding = holder["status"]["synchronization"]["mutex"]["holding"]
    key = "argo-workflows/mutex/" + synchronization["mutexes"][0]["name"]
    assert len(holding) == 1 and holding[0]["mutex"] == key and holding[0]["holder"]
    assert not workers and len(workflows) == 2
    assert all(w["metadata"]["uid"] for w in [holder, *workflows])
    assert len({w["metadata"]["name"] for w in [holder, *workflows]}) == 3
    assert len({w["metadata"]["uid"] for w in [holder, *workflows]}) == 3
    for workflow in workflows:
        assert workflow["metadata"]["namespace"] == "argo-workflows"
        assert workflow["spec"]["workflowTemplateRef"] == {
            "name": "perfeng-release-adopt-existing-v2"
        }
        assert {
            p["name"]: p["value"] for p in workflow["spec"]["arguments"]["parameters"]
        } == parameters
        status = workflow["status"]
        assert status["phase"] == "Pending"
        assert status["storedWorkflowTemplateSpec"]["synchronization"] == synchronization
        mutex = status["synchronization"]["mutex"]
        assert mutex["waiting"] == holding and not mutex.get("holding")
        assert not workflow["spec"].get("suspend")
        assert not any(n["type"] == "Pod" for n in status.get("nodes", {}).values())
        assert not readiness.outputs(status)
        assert all(not readiness.outputs(n) for n in status.get("nodes", {}).values())


def serialized(
    held: dict[str, Any],
    holder: dict[str, Any],
    queued: list[dict[str, Any]],
    completed: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind queued UIDs to successful, disjoint controller entrypoint intervals."""
    assert holder["metadata"]["uid"] == held["metadata"]["uid"]
    assert holder["metadata"]["name"] == held["metadata"]["name"]
    assert holder["status"]["phase"] == "Succeeded"

    def interval(workflow: dict[str, Any], kind: str) -> tuple[datetime, datetime]:
        node = workflow["status"]["nodes"][workflow["metadata"]["name"]]
        assert node["type"] == kind and node["phase"] == "Succeeded"
        times = tuple(datetime.fromisoformat(node[k]) for k in ("startedAt", "finishedAt"))
        assert all(t.utcoffset() == timedelta(0) for t in times) and times[0] <= times[1]
        return times[0], times[1]

    released = interval(holder, "Suspend")[1]
    expected = {(w["metadata"]["name"], w["metadata"]["uid"]) for w in queued}
    assert len(queued) == len(completed) == len(expected) == 2
    assert len({w["metadata"]["uid"] for w in queued}) == 2
    assert {(w["metadata"]["name"], w["metadata"]["uid"]) for w in completed} == expected
    assert all(w["status"]["phase"] == "Succeeded" for w in completed)
    intervals = sorted(interval(w, "Steps") for w in completed)
    assert released <= intervals[0][0] and intervals[0][1] <= intervals[1][0]
    return {
        "decision": "serialized-exact-retries",
        "holderUID": holder["metadata"]["uid"],
        "workflowUIDs": sorted(w["metadata"]["uid"] for w in completed),
    }


if __name__ == "__main__":
    mode, directory, evidence_directory, label = sys.argv[1:]
    fixture, results = Path(directory), Path(evidence_directory)

    def load(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    if mode == "quota-patch":
        print(
            json.dumps(
                quota_patch(
                    load(fixture / "adoption-quota-created.json"),
                    load(fixture / "adoption-quota-current.json"),
                )
            )
        )
        sys.exit(0)
    if mode == "waiting":
        waiting(
            load(results / "adoption-lock-held.workflow.json"),
            load(results / "adoption-lock-queued.workflows.json"),
            load(results / "adoption-lock-queued.workers.json"),
            load(fixture / "parent-parameters.json"),
        )
        sys.exit(0)
    if mode == "serialized":
        result = serialized(
            load(results / "adoption-lock-held.workflow.json"),
            load(results / "adoption-lock-holder.workflow.json"),
            load(results / "adoption-lock-queued.workflows.json"),
            [
                load(results / f"adoption-parent-concurrent-{suffix}.workflow.json")
                for suffix in ("a", "b")
            ],
        )
        (results / "adoption-lock.result.json").write_text(
            json.dumps(result, sort_keys=True), encoding="utf-8"
        )
        sys.exit(0)
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
            receipt_only=label == "recovered",
        )
        (results / f"adoption-parent-{label}.result.json").write_text(
            json.dumps(result, sort_keys=True), encoding="utf-8"
        )
    elif mode == "receipt-only":
        partial(
            workflow,
            load(fixture / "parent-parameters.json"),
            load(results / "parent-first.result.json"),
            (fixture / "parent-request.json").read_bytes(),
            Path("deployments/local-control-plane.json").read_bytes(),
            load(fixture / "adoption-parent-api.json"),
            load(results / f"adoption-parent-{label}.workers.json"),
            (fixture / "adoption-parent-worker.log").read_text(encoding="utf-8"),
        )
    elif mode in {"refusal", "root-drift"}:
        refusal(
            workflow,
            load(results / f"adoption-parent-{label}.workers.json"),
            (fixture / "adoption-parent-worker.log").read_text(encoding="utf-8"),
            step="plan" if mode == "root-drift" else "root",
            records=load(fixture / "adoption-parent-records.json")
            if mode == "root-drift"
            else None,
        )
    else:
        raise ValueError("Unknown adoption evidence mode")
