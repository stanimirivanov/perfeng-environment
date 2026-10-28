"""Prove one native child enable-to-disable transition in owned disposable CI."""

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import maintenance_authority_evidence as authority
import maintenance_write_denial as denial
import readiness_parent_evidence as readiness
import yaml

PREFIX = "maintenance-child-transition"
NAME = "perfeng-maintenance-child-transition-ci"
KEYS = (
    "applications",
    "deployment",
    "namespace",
    "server",
    "workloads",
    "pods",
    "server-pods",
    "records",
    "secrets",
    "config",
    "git",
)


def template() -> dict[str, Any]:
    source = yaml.safe_load(
        Path("gitops/release-workflows/release-maintenance-pause-v1.yaml").read_text()
    )
    return {
        "apiVersion": source["apiVersion"],
        "kind": "WorkflowTemplate",
        "metadata": {"name": NAME, "namespace": "argo-workflows"},
        "spec": {
            "serviceAccountName": source["spec"]["serviceAccountName"],
            "synchronization": source["spec"]["synchronization"],
            "activeDeadlineSeconds": 180,
            "entrypoint": "transition",
            "arguments": {"parameters": [{"name": "snapshot"}]},
            "templates": [
                {
                    "name": "transition",
                    "steps": [
                        [
                            {
                                "name": "guard-child",
                                "templateRef": {
                                    "name": "perfeng-maintenance-resources-v1",
                                    "template": "guard-child",
                                },
                                "arguments": {
                                    "parameters": [
                                        {
                                            "name": "snapshot",
                                            "value": "{{workflow.parameters.snapshot}}",
                                        }
                                    ]
                                },
                            }
                        ],
                        [
                            {
                                "name": "pause-child",
                                "templateRef": {
                                    "name": "perfeng-maintenance-resources-v1",
                                    "template": "patch-child",
                                },
                                "arguments": {
                                    "parameters": [
                                        {
                                            "name": "observation",
                                            "value": "{{steps.guard-child.outputs.parameters.observation}}",
                                        }
                                    ]
                                },
                            }
                        ],
                    ],
                }
            ],
        },
    }


def child(state: dict[str, Any]) -> dict[str, Any]:
    values = [
        o
        for o in state["applications"]["items"]
        if o["metadata"]["name"] == "perfeng-local-control-plane"
    ]
    assert len(values) == 1
    return values[0]


def healthy(obj: dict[str, Any]) -> None:
    authority.prepare("child", obj)
    assert obj["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "perfeng-environment"
    assert not obj.get("operation") and not obj["status"].get("conditions")
    assert obj["status"].get("operationState", {}).get("phase", "Succeeded") == "Succeeded"
    assert obj["status"]["sync"]["status"] == "Synced"
    assert obj["status"]["health"]["status"] == "Healthy"
    assert obj["spec"]["syncPolicy"]["automated"] == {
        "enabled": False,
        "prune": False,
        "selfHeal": False,
    }


def enable(original: dict[str, Any]) -> list[dict[str, Any]]:
    healthy(original)
    return [
        *(
            {"op": "test", "path": "/metadata/" + k, "value": original["metadata"][k]}
            for k in ("uid", "resourceVersion")
        ),
        {"op": "test", "path": "/spec", "value": original["spec"]},
        {"op": "replace", "path": "/spec/syncPolicy/automated/enabled", "value": True},
    ]


def enabled(original: dict[str, Any], current: dict[str, Any]) -> None:
    healthy(original)
    normalized = deepcopy(current)
    assert normalized["spec"]["syncPolicy"]["automated"]["enabled"] is True
    normalized["spec"]["syncPolicy"]["automated"]["enabled"] = False
    healthy(normalized)
    assert normalized["spec"] == original["spec"]
    assert current["metadata"]["resourceVersion"] != original["metadata"]["resourceVersion"]
    assert current["metadata"]["generation"] == original["metadata"]["generation"] + 1
    ignored = {"generation", "resourceVersion", "managedFields"}
    assert {k: v for k, v in current["metadata"].items() if k not in ignored} == {
        k: v for k, v in original["metadata"].items() if k not in ignored
    }
    assert current["status"]["sync"] == original["status"]["sync"]
    assert current["status"]["health"] == original["status"]["health"]


def verify(
    before: dict[str, Any],
    target: dict[str, Any],
    after: dict[str, Any],
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
) -> dict[str, Any]:
    original, restored = child(before), child(after)
    enabled(original, target)
    healthy(restored)
    assert restored["metadata"]["generation"] == original["metadata"]["generation"] + 2
    assert restored["metadata"]["resourceVersion"] != target["metadata"]["resourceVersion"]
    normalized = deepcopy(after)
    child(normalized)["metadata"]["generation"] = original["metadata"]["generation"]
    denial.retained(before, normalized)
    assert workflow["metadata"]["namespace"] == "argo-workflows" and workflow["metadata"]["uid"]
    assert workflow["spec"]["workflowTemplateRef"] == {"name": NAME}
    assert not workflow["spec"].get("shutdown")
    assert workflow["status"]["phase"] == "Succeeded"
    assert workflow["spec"]["arguments"]["parameters"] == [
        {"name": "snapshot", "value": json.dumps(target)}
    ]
    guard, patch = (readiness.named(workflow, n) for n in ("guard-child", "pause-child"))
    assert {n["id"] for n in workflow["status"]["nodes"].values() if n["type"] == "Pod"} == {
        guard["id"],
        patch["id"],
    }
    observation = readiness.outputs(guard)["observation"]
    assert guard["inputs"]["parameters"] == [{"name": "snapshot", "value": json.dumps(target)}]
    assert json.loads(patch["inputs"]["parameters"][0]["value"]) == observation
    assert patch["inputs"]["parameters"][0]["name"] == "observation"
    assert len(patch["inputs"]["parameters"]) == 1
    assert observation["uid"] == target["metadata"]["uid"] and observation["spec"] == target["spec"]
    assert isinstance(observation["resourceVersion"], str) and observation["resourceVersion"]
    _, expected = authority.prepare("child", target)
    assert observation["syncPolicy"] == expected["syncPolicy"]
    assert datetime.fromisoformat(guard["finishedAt"]) <= datetime.fromisoformat(patch["startedAt"])
    assert len(workers) == 2 and len({w["uid"] for w in workers}) == 2
    for node, leaf in ((guard, "guard-child"), (patch, "patch-child")):
        assert node["phase"] == "Succeeded"
        assert node["templateRef"] == {"name": "perfeng-maintenance-resources-v1", "template": leaf}
        matching = [w for w in workers if w["nodeID"] == node["id"]]
        assert len(matching) == 1 and matching[0]["uid"]
        assert (
            matching[0]["account"] == "release-maintenance-pause" and matching[0]["exitCode"] == 0
        )
    return {
        "decision": "maintenance-child-transition-verified",
        "workflowUID": workflow["metadata"]["uid"],
        "applicationUID": target["metadata"]["uid"],
    }


def main() -> None:
    mode, directory, output = sys.argv[1:]
    fixture, results = Path(directory), Path(output)

    def load(suffix: str) -> Any:
        return json.loads((fixture / f"{PREFIX}.{suffix}.json").read_text())

    def state(label: str) -> dict[str, Any]:
        return {k: json.loads((fixture / f"{PREFIX}-{label}.{k}.json").read_text()) for k in KEYS}

    if mode == "template":
        print(json.dumps(template()))
    elif mode == "installed":
        obj, expected = load("installed"), template()
        assert obj["spec"] == expected["spec"]
        assert all(obj["metadata"][k] == v for k, v in expected["metadata"].items())
    elif mode == "prepare":
        original = child(state("before"))
        (fixture / f"{PREFIX}.enable.json").write_text(json.dumps(enable(original)))
    elif mode == "enabled":
        target = load("enabled")
        enabled(child(state("before")), load("enable-response"))
        enabled(child(state("before")), target)
        (fixture / f"{PREFIX}.parameters.json").write_text(
            json.dumps({"snapshot": json.dumps(target)})
        )
    else:
        assert mode == "verify"
        result = verify(
            state("before"), load("enabled"), state("after"), load("workflow"), load("workers")
        )
        (results / f"{PREFIX}.outcome.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main()
