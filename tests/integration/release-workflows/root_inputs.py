"""Bounded root evidence for disposable CI; never a deployment entrypoint."""

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path("gitops/bootstrap/local-root-application.yaml")
SKIP = "argocd.argoproj.io/skip-reconcile"
CHILDREN = {
    f"perfeng-local-{name}"
    for name in (
        "postgres",
        "seaweedfs",
        "sample-sut",
        "native-search",
        "resource-metrics",
        "benchmark-authority",
    )
}
CONTROL_PLANE = "perfeng-local-control-plane"


def tree(path: Path) -> list[dict[str, Any]]:
    """Refuse inventory expansion before creating any skipped child."""
    objects = list(yaml.safe_load_all(path.read_text(encoding="utf-8")))
    inventory = {(o["kind"], o["metadata"]["name"]) for o in objects}
    expected = {
        *(("Application", name) for name in CHILDREN | {CONTROL_PLANE}),
        *(
            ("Namespace", name)
            for name in ("perf-platform", "perf-generators", "perf-sut", "monitoring")
        ),
        *(
            ("AppProject", name)
            for name in (
                "perfeng-local",
                "perfeng-local-resource-metrics",
                "perfeng-local-benchmark-authority",
            )
        ),
    }
    assert len(objects) == len(inventory) == 14 and inventory == expected
    for obj in objects:
        if obj["kind"] == "Namespace":
            assert obj["apiVersion"] == "v1" and not obj["metadata"].get("namespace")
        else:
            assert obj["apiVersion"] == "argoproj.io/v1alpha1"
            assert obj["metadata"]["namespace"] == "argocd"
    return objects


def root_spec() -> dict[str, Any]:
    """Retain canonical main source; only automatic sync is disabled in CI."""
    spec = yaml.safe_load(ROOT.read_text(encoding="utf-8"))["spec"]
    assert spec["source"] == {
        "repoURL": "https://github.com/stanimirivanov/perfeng-environment.git",
        "targetRevision": "main",
        "path": "gitops/environments/local",
    }
    spec["syncPolicy"]["automated"] = {"enabled": False}
    return spec


def children(value: dict[str, Any], objects: list[dict[str, Any]], before: dict[str, Any]) -> None:
    """Root may restore committed child specs, but must preserve every skip and UID."""
    expected = {o["metadata"]["name"]: o for o in objects if o["kind"] == "Application"}
    current = {o["metadata"]["name"]: o for o in value["items"]}
    previous = {o["metadata"]["name"]: o for o in before["items"]}
    assert current.keys() == previous.keys() == expected.keys()
    for name, app in current.items():
        old = previous[name]
        assert app["metadata"]["uid"] == old["metadata"]["uid"]
        assert app["spec"] == expected[name]["spec"] and not app.get("operation")
        assert not app["metadata"].get("deletionTimestamp")
        skip = app["metadata"].get("annotations", {}).get(SKIP)
        if name in CHILDREN:
            assert skip == "true"
            assert app.get("status", {}).get("operationState") == old.get("status", {}).get(
                "operationState"
            )
        else:
            assert skip is None and app["spec"] == old["spec"]


def converged(
    value: dict[str, Any], uid: str, case: str, revision: str, objects: list[dict[str, Any]]
) -> None:
    """Require actual complete root-object convergence, not full child-tree health."""
    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    assert value["metadata"]["uid"] == uid and value["spec"] == root_spec()
    assert not value["metadata"].get("deletionTimestamp")
    assert SKIP not in value["metadata"].get("annotations", {})
    assert not value.get("operation")
    status = value["status"]
    assert not status.get("conditions")
    assert status["sync"]["status"] == "Synced" and status["health"]["status"] == "Healthy"
    assert status["sync"]["revision"] == revision
    assert status["sync"]["comparedTo"]["source"] == root_spec()["source"]
    assert status["sync"]["comparedTo"]["destination"] == root_spec()["destination"]
    state = status["operationState"]
    assert state["phase"] == "Succeeded" and state["syncResult"]["revision"] == revision
    assert state["syncResult"]["source"] == root_spec()["source"]
    assert state["operation"]["info"] == [{"name": "ci-case", "value": case}]
    assert state["operation"]["sync"] == {"revision": revision, "syncStrategy": {"apply": {}}}
    expected = {
        (o["kind"], o["metadata"].get("namespace", ""), o["metadata"]["name"]) for o in objects
    }
    actual = {(o["kind"], o.get("namespace", ""), o["name"]) for o in status["resources"]}
    assert actual == expected and len(status["resources"]) == len(expected)
    assert all(o["status"] == "Synced" for o in status["resources"])


if __name__ == "__main__":
    mode, path, *args = sys.argv[1:]
    if mode == "prepare":
        for obj in tree(Path(path)):
            if obj["kind"] == "Application" and obj["metadata"]["name"] in CHILDREN:
                obj["metadata"].setdefault("annotations", {})[SKIP] = "true"
                Path(args[0], obj["metadata"]["name"] + ".json").write_text(
                    json.dumps(obj), encoding="utf-8"
                )
    elif mode == "children":
        children(
            json.loads(Path(path).read_text(encoding="utf-8")),
            tree(Path(args[0])),
            json.loads(Path(args[1]).read_text(encoding="utf-8")),
        )
    elif mode == "converged":
        converged(
            json.loads(Path(path).read_text(encoding="utf-8")),
            args[0],
            args[1],
            args[2],
            tree(Path(args[3])),
        )
    else:
        raise ValueError("Unknown bounded root evidence operation")
