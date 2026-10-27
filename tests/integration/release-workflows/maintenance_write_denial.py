"""Guard disposable Role narrowing and prove unchanged-parent PATCH refusal."""

import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import maintenance_authority_evidence as authority
import maintenance_pause_evidence as maintenance
import yaml

TARGETS = ("root", "child", "deployment")


def source_role(resource: str) -> dict[str, Any]:
    assert resource in TARGETS
    path = (
        "gitops/release-workflows/rbac/maintenance-pause/platform-access.yaml"
        if resource == "deployment"
        else "gitops/release-workflows-bootstrap/maintenance-pause-application-access.yaml"
    )
    return next(obj for obj in yaml.safe_load_all(Path(path).read_text()) if obj["kind"] == "Role")


def narrowed_rules(resource: str) -> list[dict[str, Any]]:
    """Preserve every GET and all PATCH grants except the one named target."""
    source = source_role(resource)
    name = authority.TARGETS[resource][4]
    result = []
    changed = 0
    for rule in source["rules"]:
        if "patch" not in rule["verbs"] or name not in rule["resourceNames"]:
            result.append(deepcopy(rule))
            continue
        changed += 1
        result.append({**deepcopy(rule), "verbs": [v for v in rule["verbs"] if v != "patch"]})
        remaining = [n for n in rule["resourceNames"] if n != name]
        if remaining:
            result.append({**deepcopy(rule), "verbs": ["patch"], "resourceNames": remaining})
    assert changed == 1
    return result


def role_identity(resource: str, obj: dict[str, Any]) -> None:
    expected = source_role(resource)
    assert obj["apiVersion"] == expected["apiVersion"] and obj["kind"] == "Role"
    for field in ("namespace", "name"):
        assert obj["metadata"][field] == expected["metadata"][field]
    assert all(
        isinstance(obj["metadata"][k], str) and obj["metadata"][k]
        for k in ("uid", "resourceVersion")
    )
    assert not any(
        obj["metadata"].get(k) for k in ("deletionTimestamp", "ownerReferences", "finalizers")
    )


def role_state(
    resource: str, original: dict[str, Any], current: dict[str, Any], *, narrowed: bool
) -> None:
    role_identity(resource, original)
    role_identity(resource, current)
    assert original["rules"] == source_role(resource)["rules"]
    ignored = {"resourceVersion", "managedFields"}
    assert {k: v for k, v in current["metadata"].items() if k not in ignored} == {
        k: v for k, v in original["metadata"].items() if k not in ignored
    }
    assert current["rules"] == (narrowed_rules(resource) if narrowed else original["rules"])


def role_patch(
    resource: str, original: dict[str, Any], current: dict[str, Any], *, restore: bool
) -> list[dict[str, Any]]:
    role_state(resource, original, current, narrowed=restore)
    return [
        *(
            {"op": "test", "path": "/metadata/" + k, "value": current["metadata"][k]}
            for k in ("uid", "resourceVersion")
        ),
        {"op": "test", "path": "/rules", "value": current["rules"]},
        {
            "op": "replace",
            "path": "/rules",
            "value": original["rules"] if restore else narrowed_rules(resource),
        },
    ]


def retained(before: dict[str, Any], after: dict[str, Any]) -> None:
    """No Pod replacement or workload recovery is permitted by these cases."""
    for key in (
        "server",
        "workloads",
        "pods",
        "server-pods",
        "records",
        "secrets",
        "config",
        "git",
    ):
        assert before[key] == after[key]
    assert before["namespace"]["metadata"]["uid"] == after["namespace"]["metadata"]["uid"]
    assert before["deployment"]["metadata"]["uid"] == after["deployment"]["metadata"]["uid"]
    assert before["deployment"]["spec"] == after["deployment"]["spec"]
    assert after["deployment"]["spec"]["replicas"] == 1
    for name in ("perfeng-local-root", "perfeng-local-control-plane"):
        old, new = (
            next(o for o in s["applications"]["items"] if o["metadata"]["name"] == name)
            for s in (before, after)
        )
        assert (
            old["spec"] == new["spec"]
            and old["spec"]["syncPolicy"]["automated"]["enabled"] is False
        )
        assert not new.get("operation") and not new["metadata"].get("deletionTimestamp")
        ignored = {"resourceVersion", "managedFields"}
        if name == "perfeng-local-root":
            ignored.add("generation")  # ADR 0036; shell validates both captured roots.
        assert {k: v for k, v in old["metadata"].items() if k not in ignored} == {
            k: v for k, v in new["metadata"].items() if k not in ignored
        }


def refusal(
    resource: str,
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    parameters: dict[str, str],
    raw: bytes,
    lock: bytes,
    before: dict[str, Any],
    diagnostic: str,
) -> dict[str, Any]:
    """Require real readiness/adopted ledger, completed prefix and exact native Forbidden."""
    assert (
        resource in TARGETS
        and workflow["metadata"]["namespace"] == "argo-workflows"
        and workflow["metadata"]["uid"]
    )
    assert workflow["spec"]["workflowTemplateRef"] == {
        "name": "perfeng-release-maintenance-pause-v1"
    }
    assert not workflow["spec"].get("shutdown") and workflow["status"]["phase"] in {
        "Failed",
        "Error",
    }
    named, outputs = maintenance.readiness.named, maintenance.readiness.outputs
    preflight = outputs(named(workflow, "preflight", "Steps"))["result"]
    health, _ = maintenance.running(workflow, parameters, preflight, lock, before)

    def observe(name: str) -> Any:
        return outputs(named(workflow, name))["observation"]

    assert (
        sorted([observe("receipt"), observe("pointer")], key=lambda o: o["metadata"]["name"])
        == before["records"]
    )
    plan = maintenance.policy.plan_adoption(
        raw, health, observe("root"), observe("receipt"), observe("pointer"), observe("root-after")
    )
    assert plan["create-receipt"] == plan["create-pointer"] == "false"
    baseline = named(workflow, "baseline")
    assert baseline["templateRef"] == {
        "name": "perfeng-plan-release-adoption-v3",
        "template": "run",
    }
    assert {
        p["name"]: json.loads(p["value"]) if p["name"] == "result" else p["value"]
        for p in baseline["outputs"]["parameters"]
        if "value" in p
    } == plan
    sequence = [named(workflow, "root-after"), baseline]
    for target in TARGETS[: TARGETS.index(resource) + 1]:
        guard = named(workflow, "guard-" + target)
        assert guard["templateRef"] == {
            "name": "perfeng-maintenance-resources-v1",
            "template": "guard-" + target,
        }
        expected = (
            before["deployment"]
            if target == "deployment"
            else next(
                o
                for o in before["applications"]["items"]
                if o["metadata"]["name"] == authority.TARGETS[target][4]
            )
        )
        snapshot = observe("guard-" + target)
        assert (
            snapshot["uid"] == expected["metadata"]["uid"]
            and snapshot["spec"] == expected["spec"]
            and snapshot["resourceVersion"]
        )
        sequence.append(guard)
        if target == resource:
            break
        patch = named(workflow, "pause-" + target)
        assert patch["templateRef"] == {
            "name": "perfeng-maintenance-resources-v1",
            "template": "patch-" + target,
        }
        sequence.append(patch)
    assert all(n["phase"] == "Succeeded" for n in sequence)
    failed = named(workflow, "pause-" + resource)
    assert failed["type"] == "Pod" and failed["phase"] in {"Failed", "Error"}
    assert failed["templateRef"] == {
        "name": "perfeng-maintenance-resources-v1",
        "template": "patch-" + resource,
    }
    for earlier, later in zip(sequence, sequence[1:] + [failed], strict=True):
        assert (
            datetime.fromisoformat(earlier["startedAt"])
            <= datetime.fromisoformat(earlier["finishedAt"])
            <= datetime.fromisoformat(later["startedAt"])
        )
    assert datetime.fromisoformat(failed["startedAt"]) <= datetime.fromisoformat(
        failed["finishedAt"]
    )
    _, _, plural, namespace, name = authority.TARGETS[resource]
    group = "apps" if resource == "deployment" else "argoproj.io"
    authority.denied("patch", plural, group, namespace, name, 1, diagnostic)
    pods = {n["id"]: n for n in workflow["status"]["nodes"].values() if n["type"] == "Pod"}
    assert len(pods) == len(workers) and {w["nodeID"] for w in workers} == pods.keys()
    for worker in workers:
        assert worker["uid"] and worker["account"] == "release-maintenance-pause"
        assert worker["exitCode"] == (64 if worker["nodeID"] == failed["id"] else 0)
        if worker["nodeID"] != failed["id"]:
            assert pods[worker["nodeID"]]["phase"] == "Succeeded"
    allowed = {n["displayName"] for n in sequence} | {"pause-" + resource}
    for node in workflow["status"]["nodes"].values():
        ref = node.get("templateRef", {})
        native = ref.get("name") == "perfeng-maintenance-resources-v1" and ref.get(
            "template", ""
        ).startswith(("guard-", "patch-"))
        if (
            (native and node["displayName"] not in allowed)
            or node["displayName"] in {"checkpoint", "observe"}
            or ref.get("name") == "perfeng-verify-release-quiescence-v3"
        ):
            assert node["phase"] in {"Skipped", "Omitted"} and node["type"] != "Pod"
        result = outputs(node).get("result")
        assert not isinstance(result, dict) or result.get("kind") != "PerfengMaintenanceCheckpoint"
        if node["displayName"] in {workflow["metadata"]["name"], "pause", "checkpoint", "observe"}:
            assert "result" not in outputs(node)
    return {
        "decision": "maintenance-write-denied",
        "resource": resource,
        "workflowUID": workflow["metadata"]["uid"],
    }


def main() -> None:
    mode, resource, directory, output = sys.argv[1:]
    fixture, results = Path(directory), Path(output)
    prefix = "maintenance-write-denied-" + resource

    def load(suffix: str) -> Any:
        return json.loads((fixture / f"{prefix}.{suffix}.json").read_text())

    def save(suffix: str, value: Any) -> None:
        (fixture / f"{prefix}.{suffix}.json").write_text(json.dumps(value))

    original = load("role-original")
    if mode == "prepare":
        save("narrow-patch", role_patch(resource, original, original, restore=False))
        return
    if mode == "narrowed":
        role_state(resource, original, load("role-narrowed"), narrowed=True)
        return
    if mode == "restored":
        role_state(resource, original, load("role-restored"), narrowed=False)
        proof = json.loads((results / f"{prefix}.refusal.json").read_text())
        assert proof == {
            "decision": "maintenance-write-denied",
            "resource": resource,
            "workflowUID": load("workflow")["metadata"]["uid"],
        }
        (results / f"{prefix}.outcome.json").write_text(
            json.dumps({**proof, "restoredRoleUID": original["metadata"]["uid"]})
        )
        return
    assert mode == "verify"

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
                "server-pods",
                "records",
                "secrets",
                "config",
                "git",
            )
        }

    before, after = state("before"), state("after")
    retained(before, after)
    report = refusal(
        resource,
        load("workflow"),
        load("workers"),
        json.loads((fixture / "parent-parameters.json").read_text()),
        (fixture / "parent-request.json").read_bytes(),
        Path("deployments/local-control-plane.json").read_bytes(),
        before,
        (fixture / f"{prefix}.log").read_text(),
    )
    role_state(resource, original, load("role-narrowed"), narrowed=True)
    save("restore-patch", role_patch(resource, original, load("role-current"), restore=True))
    (results / f"{prefix}.refusal.json").write_text(json.dumps(report))


if __name__ == "__main__":
    main()
