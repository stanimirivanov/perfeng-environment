"""Prepare a reversible parent and validate committed PATCH loss before recovery."""

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import adoption_response as diagnostics
import ledger_response_proxy as ledger
import maintenance_authority_evidence as authority
import maintenance_guard_conflict as guards
import maintenance_partial_pause as partial
import maintenance_patch_proxy as proxy
import yaml

NAME = "perfeng-maintenance-response-ci"
BARRIER = "maintenance-response-barrier"
PREFIX = "maintenance-response"
DIRECTORY = Path("gitops/release-workflows")


def template() -> dict[str, Any]:
    original = yaml.safe_load((DIRECTORY / "release-maintenance-pause-v1.yaml").read_text())
    native = yaml.safe_load((DIRECTORY / "maintenance-resources-v1.yaml").read_text())
    result = deepcopy(original)
    result["metadata"] = {"name": NAME, "namespace": "argo-workflows"}
    steps = result["spec"]["templates"][0]["steps"]
    index = next(i for i, group in enumerate(steps) if group[0]["name"] == "pause-deployment")
    assert steps[index - 1][0]["name"] == "guard-deployment"
    assert steps[index][0].pop("templateRef") == {
        "name": native["metadata"]["name"],
        "template": "patch-deployment",
    }
    steps[index][0]["template"] = "fault-patch-deployment"
    steps.insert(index, [{"name": BARRIER, "template": BARRIER}])
    patch = deepcopy(
        next(t for t in native["spec"]["templates"] if t["name"] == "patch-deployment")
    )
    assert (
        not patch.get("podSpecPatch")
        and not original["spec"].get("onExit")
        and not original["spec"].get("hooks")
    )
    adapted = deepcopy(patch)
    adapted["name"] = "fault-patch-deployment"
    adapted["resource"]["flags"].append("--kubeconfig=/fault/client.json")
    adapted["podSpecPatch"] = json.dumps(
        {
            "volumes": [{"name": "fault", "configMap": {"name": "maintenance-response-proxy"}}],
            "containers": [
                {
                    "name": "main",
                    "volumeMounts": [{"name": "fault", "mountPath": "/fault", "readOnly": True}],
                }
            ],
        }
    )
    result["spec"]["templates"].extend([{"name": BARRIER, "suspend": {}}, adapted])
    # Remove exactly the approved barrier and transport changes, then compare both sources.
    restored = deepcopy(result)
    restored["metadata"] = original["metadata"]
    restored_steps = restored["spec"]["templates"][0]["steps"]
    del restored_steps[index]
    del restored_steps[index][0]["template"]
    restored_steps[index][0]["templateRef"] = {
        "name": native["metadata"]["name"],
        "template": "patch-deployment",
    }
    barrier, transported = restored["spec"]["templates"][-2:]
    assert barrier == {"name": BARRIER, "suspend": {}}
    transported["name"] = "patch-deployment"
    assert transported["resource"]["flags"].pop() == "--kubeconfig=/fault/client.json"
    del transported["podSpecPatch"]
    assert transported == patch
    del restored["spec"]["templates"][-2:]
    assert restored == original
    return result


def ready(
    workflow: dict[str, Any],
    parameters: dict[str, str],
    raw: bytes,
    lock: bytes,
    before: dict[str, Any],
) -> dict[str, Any]:
    assert workflow["metadata"]["namespace"] == "argo-workflows" and workflow["metadata"]["uid"]
    return guards.ready(
        workflow,
        "deployment",
        parameters,
        raw,
        lock,
        before,
        parent_name=NAME,
        barrier_name=BARRIER,
    )


def failed(
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    expected: dict[str, Any],
    events: list[dict[str, Any]],
    after: dict[str, Any],
) -> dict[str, Any]:
    named, outputs = partial.maintenance.readiness.named, partial.maintenance.readiness.outputs
    assert workflow["status"]["phase"] in {"Failed", "Error"} and not workflow["spec"].get(
        "shutdown"
    )
    assert named(workflow, BARRIER, "Suspend")["phase"] == "Succeeded"
    node = named(workflow, "pause-deployment")
    assert node["type"] == "Pod" and node["phase"] in {"Failed", "Error"}
    assert node.get("templateName") == "fault-patch-deployment" and not node.get("templateRef")
    barrier = named(workflow, BARRIER, "Suspend")
    assert (
        datetime.fromisoformat(barrier["finishedAt"])
        <= datetime.fromisoformat(node["startedAt"])
        <= datetime.fromisoformat(node["finishedAt"])
    )
    pods = {n["id"]: n for n in workflow["status"]["nodes"].values() if n["type"] == "Pod"}
    assert len(workers) == len(pods) and {w["nodeID"] for w in workers} == pods.keys()
    for worker in workers:
        assert worker["uid"] and worker["account"] == "release-maintenance-pause"
        assert worker["exitCode"] == (64 if worker["nodeID"] == node["id"] else 0)
        if worker["nodeID"] != node["id"]:
            assert pods[worker["nodeID"]]["phase"] == "Succeeded"
    for other in workflow["status"]["nodes"].values():
        if (
            other["displayName"] in {"checkpoint", "observe"}
            or other.get("templateRef", {}).get("name") == "perfeng-verify-release-quiescence-v3"
        ):
            assert other["phase"] in {"Skipped", "Omitted"} and other["type"] != "Pod"
        if other["displayName"] in {workflow["metadata"]["name"], "pause", "checkpoint", "observe"}:
            assert "result" not in outputs(other)
    assert len(events) == 1
    event = events[0]
    assert (
        event["event"] == "deployment-committed-response-dropped" and event["upstreamStatus"] == 200
    )
    assert event["successfulResponsesForwarded"] == 0 and event["patchDigest"] == ledger.digest(
        expected["patch"]
    )
    assert event["uid"] == expected["before"]["metadata"]["uid"] and event["resourceVersion"]
    assert event["resourceVersion"] != expected["before"]["metadata"]["resourceVersion"]
    assert proxy.committed(expected, after["deployment"])
    return {
        "decision": "maintenance-patch-response-loss-verified",
        "workflowUID": workflow["metadata"]["uid"],
        "deploymentUID": event["uid"],
        "patchDigest": event["patchDigest"],
    }


def main() -> None:
    mode, directory, output = sys.argv[1:]
    fixture, results = Path(directory), Path(output)

    def load(suffix: str) -> Any:
        return json.loads((fixture / f"{PREFIX}.{suffix}.json").read_text())

    def state(label: str) -> dict[str, Any]:
        value = {
            key: json.loads((fixture / f"{PREFIX}-{label}.{key}.json").read_text())
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
        value["serverPod"] = json.loads(
            (fixture / f"{PREFIX}-{label}.server-pods.json").read_text()
        )
        if label == "after":
            value["podList"] = json.loads((fixture / f"{PREFIX}-after.pod-list.json").read_text())
        return value

    if mode == "template":
        print(json.dumps(template()))
        return
    if mode == "installed":
        assert load("installed")["spec"] == template()["spec"]
        assert all(load("installed")["metadata"][k] == v for k, v in template()["metadata"].items())
        return
    if mode == "prepare":
        workflow = load("barrier")
        report = diagnostics.barrier_diagnostics(
            workflow,
            load("barrier-pods"),
            BARRIER,
            (fixture / f"{PREFIX}.state.txt").read_text().strip(),
        )
        (results / f"{PREFIX}.barrier.json").write_text(json.dumps(report))
        assert report["decision"] == "barrier-ready"
        guard = ready(
            workflow,
            json.loads((fixture / "parent-parameters.json").read_text()),
            (fixture / "parent-request.json").read_bytes(),
            Path("deployments/local-control-plane.json").read_bytes(),
            state("before"),
        )
        assert not any(
            n["type"] == "Pod" and n["displayName"] == "pause-deployment"
            for n in workflow["status"]["nodes"].values()
        )
        target = load("target")
        assert (
            target["metadata"]["uid"] == guard["uid"]
            and target["metadata"]["resourceVersion"] == guard["resourceVersion"]
            and target["spec"] == guard["spec"]
        )
        assert target["spec"]["replicas"] == 1
        payload, _ = authority.prepare("deployment", target)
        expected = {"before": target, "patch": payload}
        (fixture / f"{PREFIX}.expected.json").write_text(json.dumps(expected))
        ledger.certificate(fixture, "maintenance-response-proxy.argo-workflows.svc")
        client = {
            "apiVersion": "v1",
            "kind": "Config",
            "clusters": [
                {
                    "name": "fault",
                    "cluster": {"server": proxy.ADDRESS, "certificate-authority": "/fault/ca.crt"},
                }
            ],
            "users": [
                {
                    "name": "caller",
                    "user": {"tokenFile": "/var/run/secrets/kubernetes.io/serviceaccount/token"},
                }
            ],
            "contexts": [
                {
                    "name": "fault",
                    "context": {
                        "cluster": "fault",
                        "user": "caller",
                        "namespace": "argo-workflows",
                    },
                }
            ],
            "current-context": "fault",
        }
        (fixture / f"{PREFIX}.client.json").write_text(json.dumps(client))
        objects = list(
            yaml.safe_load_all(
                Path("tests/integration/release-workflows/ledger-response-proxy.yaml").read_text()
            )
        )
        # Reuse the pinned, unprivileged transport Pod without its ledger route or identity.
        rendered = json.loads(
            json.dumps(objects).replace("ledger-response-proxy", "maintenance-response-proxy")
        )
        (fixture / f"{PREFIX}.proxy.yaml").write_text(yaml.safe_dump_all(rendered))
        return
    assert mode in {"verify", "checkpoint", "recovered"}
    before, after, workflow = state("before"), state("after"), load("workflow")
    guard = ready(
        workflow,
        json.loads((fixture / "parent-parameters.json").read_text()),
        (fixture / "parent-request.json").read_bytes(),
        Path("deployments/local-control-plane.json").read_bytes(),
        before,
    )
    assert workflow["metadata"]["uid"] == load("barrier")["metadata"]["uid"]
    partial.maintenance.retained(before, after, paused=True)
    expected = load("expected")
    assert expected["before"]["metadata"]["resourceVersion"] == guard["resourceVersion"]
    assert expected["patch"] == authority.prepare("deployment", expected["before"])[0]
    assert (
        expected["before"]["metadata"]["uid"] == before["deployment"]["metadata"]["uid"]
        and expected["before"]["spec"] == before["deployment"]["spec"]
    )
    report = failed(
        workflow,
        load("workers"),
        expected,
        [json.loads(line) for line in (fixture / f"{PREFIX}.proxy.log").read_text().splitlines()],
        after,
    )
    if mode in {"checkpoint", "recovered"}:
        checkpoint = partial.checkpoint(
            load("checkpoint-workflow"),
            load("checkpoint-workers"),
            workflow,
            json.loads((fixture / "parent-parameters.json").read_text()),
            Path("deployments/local-control-plane.json").read_bytes(),
            after,
        )
        if mode == "checkpoint":
            (results / f"{PREFIX}.checkpoint.json").write_text(json.dumps(checkpoint))
            return
        partial.maintenance.retained(before, state("recovered"), paused=False)
        report = {**report, "decision": "maintenance-response-recovery-verified"}
    (results / f"{PREFIX}.{'recovered' if mode == 'recovered' else 'outcome'}.json").write_text(
        json.dumps(report)
    )


if __name__ == "__main__":
    main()
