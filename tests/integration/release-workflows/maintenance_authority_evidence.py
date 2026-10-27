"""Prepare exact native dry-run patches and classify maintenance API authority."""

import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

SUBJECT = "system:serviceaccount:argo-workflows:release-maintenance-pause"
TARGETS = {
    "root": ("argoproj.io/v1alpha1", "Application", "applications", "argocd", "perfeng-local-root"),
    "child": (
        "argoproj.io/v1alpha1",
        "Application",
        "applications",
        "argocd",
        "perfeng-local-control-plane",
    ),
    "deployment": ("apps/v1", "Deployment", "deployments", "perf-platform", "control-plane"),
}


def handle(api: str, kind: str, namespace: str, name: str) -> dict[str, Any]:
    return {"apiVersion": api, "kind": kind, "metadata": {"namespace": namespace, "name": name}}


def prepare(resource: str, obj: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Render the published patch, including every native precondition."""
    api, kind, _, namespace, name = TARGETS[resource]
    assert obj["apiVersion"] == api and obj["kind"] == kind
    assert obj["metadata"]["namespace"] == namespace and obj["metadata"]["name"] == name
    assert all(
        isinstance(obj["metadata"][k], str) and obj["metadata"][k]
        for k in ("uid", "resourceVersion")
    )
    assert not obj["metadata"].get("deletionTimestamp") and not obj["metadata"].get(
        "ownerReferences"
    )
    expected = deepcopy(obj["spec"])
    policy = deepcopy(obj["spec"].get("syncPolicy", {}))
    policy["automated"] = {**policy.get("automated", {}), "enabled": False}
    if resource == "deployment":
        expected["replicas"] = 0
    else:
        expected["syncPolicy"] = policy
    values = {
        "uid": obj["metadata"]["uid"],
        "resourceVersion": obj["metadata"]["resourceVersion"],
        "spec": obj["spec"],
        "syncPolicy": policy,
    }
    native = yaml.safe_load(
        Path("gitops/release-workflows/maintenance-resources-v1.yaml").read_text()
    )
    manifest = next(t for t in native["spec"]["templates"] if t["name"] == "patch-" + resource)[
        "resource"
    ]["manifest"]
    payload = json.loads(
        re.sub(
            r'\{\{=toJson\(jsonpath\(inputs.parameters.observation, "\$\.([a-zA-Z]+)"\)\)\}\}',
            lambda match: json.dumps(values[match[1]]),
            manifest,
        )
    )
    assert [p["path"] for p in payload[:3]] == [
        "/metadata/uid",
        "/metadata/resourceVersion",
        "/spec",
    ]
    assert all(p["op"] == "test" for p in payload[:3])
    return payload, expected


def allowed(resource: str, original: dict[str, Any], result: dict[str, Any], rc: int) -> None:
    assert rc == 0
    _, expected = prepare(resource, original)
    assert result["apiVersion"] == original["apiVersion"] and result["kind"] == original["kind"]
    assert all(
        result["metadata"].get(k) == original["metadata"].get(k)
        for k in (
            "name",
            "namespace",
            "uid",
            "labels",
            "annotations",
            "ownerReferences",
            "finalizers",
            "deletionTimestamp",
        )
    )
    assert result["spec"] == expected


def refresh(original: dict[str, Any], current: dict[str, Any], rc: int, message: str) -> None:
    """Permit a fresh read-only attempt only after a demonstrated stale version."""
    assert rc == 1 and "The request is invalid:" in message
    assert "the server rejected our request due to an error in our request" in message
    assert original["apiVersion"] == current["apiVersion"] and original["kind"] == current["kind"]
    assert current["spec"] == original["spec"]
    assert all(
        current["metadata"].get(k) == original["metadata"].get(k)
        for k in (
            "name",
            "namespace",
            "uid",
            "labels",
            "annotations",
            "ownerReferences",
            "finalizers",
            "deletionTimestamp",
        )
    )
    assert current["metadata"]["resourceVersion"] != original["metadata"]["resourceVersion"]
    prepare(
        "deployment"
        if original["kind"] == "Deployment"
        else "root"
        if original["metadata"]["name"] == "perfeng-local-root"
        else "child",
        current,
    )


def denied(
    verb: str, resource: str, group: str, namespace: str, name: str, rc: int, message: str
) -> None:
    """Require a real named/collection Forbidden, not arbitrary client failure."""
    assert rc == 1 and "Error from server (Forbidden):" in message
    qualified = resource.split("/", 1)[0] + ("." + group if group else "")
    named = re.escape(qualified) + (
        r'(?: "' + re.escape(name) + r'")?' if verb == "create" else r' "' + re.escape(name) + r'"'
    )
    assert re.search(named + r" is forbidden:", message)
    expected = f'User "{SUBJECT}" cannot {verb} resource "{resource}" in API group "{group}"'
    assert expected in message
    assert f'in the namespace "{namespace}"' in message or f'in namespace "{namespace}"' in message


def plan(directory: Path) -> list[tuple[str, str, str, str, str, str, str]]:
    """Use handles so kubectl tests PATCH/DELETE rather than a preliminary GET."""
    records = json.loads((directory / "maintenance-before.records.json").read_text())
    receipt = next(o["metadata"]["name"] for o in records if o.get("immutable") is True)
    assert re.fullmatch(r"receipt\.[0-9a-f]{32}\.[0-9a-f]{32}", receipt)
    probes = [
        (
            "other-app",
            "patch",
            "argoproj.io/v1alpha1",
            "Application",
            "applications",
            "argocd",
            "ci-maintenance-denied",
        ),
        (
            "app-namespace",
            "patch",
            "argoproj.io/v1alpha1",
            "Application",
            "applications",
            "default",
            "perfeng-local-root",
        ),
        (
            "other-deployment",
            "patch",
            "apps/v1",
            "Deployment",
            "deployments",
            "perf-platform",
            "ci-maintenance-denied",
        ),
        (
            "deployment-namespace",
            "patch",
            "apps/v1",
            "Deployment",
            "deployments",
            "default",
            "control-plane",
        ),
        (
            "postgres-patch",
            "patch",
            "apps/v1",
            "StatefulSet",
            "statefulsets",
            "perf-platform",
            "postgres",
        ),
        ("service-patch", "patch", "v1", "Service", "services", "perf-platform", "control-plane"),
        (
            "seaweedfs-patch",
            "patch",
            "apps/v1",
            "StatefulSet",
            "statefulsets",
            "perf-platform",
            "seaweedfs",
        ),
        (
            "app-create",
            "create",
            "argoproj.io/v1alpha1",
            "Application",
            "applications",
            "argocd",
            "ci-maintenance-denied",
        ),
        (
            "deployment-create",
            "create",
            "apps/v1",
            "Deployment",
            "deployments",
            "perf-platform",
            "ci-maintenance-denied",
        ),
        (
            "git-secret-patch",
            "patch",
            "v1",
            "Secret",
            "secrets",
            "argo-workflows",
            "release-git-read",
        ),
    ]
    for resource, (api, kind, plural, namespace, name) in TARGETS.items():
        probes.append((resource + "-delete", "delete", api, kind, plural, namespace, name))
    for label, name in (("receipt", receipt), ("pointer", "perfeng-local-current")):
        for verb in ("patch", "delete"):
            probes.append(
                (
                    label + "-" + verb,
                    verb,
                    "v1",
                    "ConfigMap",
                    "configmaps",
                    "perfeng-release-ledger",
                    name,
                )
            )
    rows = []
    for label, verb, api, kind, plural, namespace, name in probes:
        (directory / f"maintenance-authority-{label}.handle.json").write_text(
            json.dumps(handle(api, kind, namespace, name))
        )
        rows.append(
            (
                label,
                verb,
                plural,
                api.split("/")[0] if "/" in api else "core",
                namespace,
                name,
                "handle",
            )
        )
    for label, plural, group, namespace in (
        ("configmap", "configmaps", "core", "argo-workflows"),
        ("secret", "secrets", "core", "argo-workflows"),
        ("job", "jobs", "batch", "perf-platform"),
    ):
        rows.append(
            (label + "-create", "create", plural, group, namespace, "ci-adoption-denied", label)
        )
    return rows


def main() -> None:
    mode, directory, *args = sys.argv[1:]
    fixture = Path(directory)
    if mode == "plan":
        for row in plan(fixture):
            print("\t".join(row))
    elif mode == "prepare":
        resource = args[0]
        obj = json.loads((fixture / f"maintenance-authority-{resource}.original.json").read_text())
        payload, _ = prepare(resource, obj)
        (fixture / f"maintenance-authority-{resource}.patch.json").write_text(json.dumps(payload))
        (fixture / f"maintenance-authority-{resource}.handle.json").write_text(
            json.dumps(handle(*TARGETS[resource][:2], *TARGETS[resource][3:]))
        )
    elif mode == "allowed":
        resource, rc = args
        allowed(
            resource,
            json.loads((fixture / f"maintenance-authority-{resource}.original.json").read_text()),
            json.loads((fixture / f"maintenance-authority-{resource}.response.json").read_text()),
            int(rc),
        )
    elif mode == "refresh":
        resource, rc = args
        refresh(
            json.loads((fixture / f"maintenance-authority-{resource}.original.json").read_text()),
            json.loads((fixture / f"maintenance-authority-{resource}.fresh.json").read_text()),
            int(rc),
            (fixture / f"maintenance-authority-{resource}.private").read_text(),
        )
    elif mode == "denied":
        label, verb, plural, group, namespace, name, rc = args
        denied(
            verb,
            plural,
            "" if group == "core" else group,
            namespace,
            name,
            int(rc),
            (fixture / f"maintenance-authority-{label}.private").read_text(),
        )
    else:
        raise ValueError("Unknown maintenance authority mode")


if __name__ == "__main__":
    main()
