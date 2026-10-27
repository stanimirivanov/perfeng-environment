"""Validate approved CI interruption after the two native Application pause writes."""

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import maintenance_pause_evidence as maintenance
import yaml

DIRECTORY = Path("gitops/release-workflows")
RESOURCES = ("root", "child")
BARRIER = "partial-pause-barrier"
STOPPED = "Stopped with strategy 'Stop'"


def template(resource: str) -> dict[str, Any]:
    """Keep the published parent intact except for one post-patch suspension."""
    assert resource in RESOURCES
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
    assert resource in RESOURCES
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
    for target in RESOURCES[: RESOURCES.index(resource) + 1]:
        for operation in ("guard", "pause"):
            node = named(workflow, operation + "-" + target)
            assert node["templateRef"] == {
                "name": "perfeng-maintenance-resources-v1",
                "template": ("patch" if operation == "pause" else operation) + "-" + target,
            }
            sequence.append(node)
        actual = next(
            o
            for o in before["applications"]["items"]
            if o["metadata"]["name"]
            == "perfeng-local-" + ("root" if target == "root" else "control-plane")
        )
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
    forbidden = {"guard-deployment", "pause-deployment", "checkpoint", "observe"}
    if resource == "root":
        forbidden.update({"guard-child", "pause-child"})
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


def main() -> None:
    mode, resource, directory, output = sys.argv[1:]
    assert resource in RESOURCES
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
    assert mode in {"barrier", "verify"}
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
        return {
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

    before = state("before")
    workflow = load("workflow" if mode == "verify" else "barrier")
    validate(
        workflow,
        load("workers"),
        resource,
        json.loads((fixture / "parent-parameters.json").read_text()),
        (fixture / "parent-request.json").read_bytes(),
        Path("deployments/local-control-plane.json").read_bytes(),
        before,
        stopped=mode == "verify",
    )
    if mode == "verify":
        suspended = load("barrier")
        assert all(
            workflow["metadata"][k] == suspended["metadata"][k]
            for k in ("uid", "name", "namespace")
        )
        expected_spec = {**suspended["spec"], "shutdown": "Stop"}
        assert workflow["spec"] == expected_spec
        assert load("workers") == load("workers-before")
        after = state("after")
        for key in before:
            if key != "applications":
                # Deployment status is controller bookkeeping; workload/Pod specs
                # and identities are independently retained by root_retained.
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
