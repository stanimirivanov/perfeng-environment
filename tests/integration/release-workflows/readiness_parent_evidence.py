"""Check complete readiness-parent evidence without cluster or network access."""

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


def outputs(node: dict[str, Any]) -> dict[str, Any]:
    """Decode materialized JSON parameters, ignoring unresolved declarations."""
    return {
        p["name"]: json.loads(p["value"])
        for p in node.get("outputs", {}).get("parameters", [])
        if "value" in p and p["name"] in {"result", "observation"}
    }


def named(workflow: dict[str, Any], name: str, kind: str = "Pod") -> dict[str, Any]:
    """Require exactly one named operation at the expected node boundary."""
    nodes = [
        n
        for n in workflow["status"]["nodes"].values()
        if n["displayName"] == name and n["type"] == kind
    ]
    assert len(nodes) == 1
    return nodes[0]


def success(
    workflow: dict[str, Any],
    preflight: dict[str, Any],
    parameters: dict[str, str],
    api: dict[str, Any],
    raw_lock: bytes,
    *,
    adoption: bool = False,
    parent_failed: bool = False,
) -> dict[str, Any]:
    """Bind parent output to its nested preflight, native children and live API."""
    assert not parent_failed or adoption
    assert workflow["status"]["phase"] in ({"Failed", "Error"} if parent_failed else {"Succeeded"})
    expected_parent = (
        "perfeng-release-adopt-existing-v2" if adoption else "perfeng-release-readiness-v2"
    )
    assert workflow["spec"]["workflowTemplateRef"]["name"] == expected_parent
    assert {
        p["name"]: p["value"] for p in workflow["spec"]["arguments"]["parameters"]
    } == parameters
    nested = named(workflow, "preflight", "Steps")
    assert nested["phase"] == "Succeeded" and outputs(nested)["result"] == preflight
    for field, parameter in (
        ("requestRevision", "request-revision"),
        ("requestDigest", "request-sha256"),
        ("selectionRevision", "selection-revision"),
    ):
        assert preflight[field] == parameters[parameter]
    assert preflight["clusterUID"] == api["clusterUID"]
    assert (
        preflight["lockDigests"]["deployments/local-control-plane.json"]
        == "sha256:" + hashlib.sha256(raw_lock).hexdigest()
    )
    observations = {}
    for name in ("postgres", "seaweedfs", "application", "deployment", "service"):
        node = named(workflow, name)
        assert node["phase"] == "Succeeded"
        observations[name] = outputs(node)["observation"]
    verify = named(workflow, "verify")
    assert verify["phase"] == "Succeeded"
    root = (
        named(workflow, "readiness", "Steps")
        if adoption
        else workflow["status"]["nodes"][workflow["metadata"]["name"]]
    )
    assert root["phase"] == "Succeeded"
    result = outputs(root)["result"]
    assert result == outputs(verify)["result"]
    schema = json.loads(
        Path("deployments/release-readiness-result-v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(result)
    lock = json.loads(raw_lock)
    for field in ("requestRevision", "requestDigest", "selectionRevision", "clusterUID"):
        assert result[field] == preflight[field]
    assert result["deploymentRevision"] == lock["deployment"]["revision"]
    assert result["image"] == lock["controlPlane"]["image"]
    application = observations["application"]
    assert result["applicationUID"] == application["uid"] == api["application"]["metadata"]["uid"]
    assert application["sync"] == "Synced" and application["health"] == "Healthy"
    assert (
        application["sources"] == application["comparedSources"] and not application["conditions"]
    )
    expected = []
    for key, kind, name in (
        ("postgres", "StatefulSet", "postgres"),
        ("seaweedfs", "StatefulSet", "seaweedfs"),
        ("deployment", "Deployment", "control-plane"),
    ):
        actual = next(
            o for o in api["platform"] if o["kind"] == kind and o["metadata"]["name"] == name
        )
        observation = observations[key]
        item = {
            "kind": kind,
            "name": name,
            "namespace": "perf-platform",
            "uid": actual["metadata"]["uid"],
            "generation": actual["metadata"]["generation"],
            "observedGeneration": actual["status"]["observedGeneration"],
        }
        assert all(
            observation[k] == item[k]
            for k in ("name", "namespace", "uid", "generation", "observedGeneration")
        )
        expected.append(item)
    assert result["resources"] == expected
    service = next(o for o in api["platform"] if o["kind"] == "Service")
    assert result["serviceUID"] == observations["service"]["uid"] == service["metadata"]["uid"]
    return result


def refusal(
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    step: str,
    log: str,
    preflight: dict[str, Any],
) -> None:
    """Allow nested preflight evidence while refusing unrelated infrastructure failures."""
    assert step in {"verify-identities", "application"}
    assert workflow["status"]["phase"] in {"Failed", "Error"}
    failed = named(workflow, step)
    assert failed["phase"] in {"Failed", "Error"}
    nonzero = [w for w in workers if w["exitCode"] not in (None, 0)]
    assert len(nonzero) == 1 and nonzero[0]["nodeID"] == failed["id"]
    assert nonzero[0]["exitCode"] == (2 if step == "verify-identities" else 1)
    root = workflow["status"]["nodes"][workflow["metadata"]["name"]]
    assert "result" not in outputs(root)
    nodes = list(workflow["status"]["nodes"].values())
    assert all(outputs(n).get("result", {}).get("kind") != "PerfengReleaseReadiness" for n in nodes)
    active = {n["displayName"] for n in nodes if n["phase"] not in {"Skipped", "Omitted"}}
    if step == "verify-identities":
        assert not active.intersection(
            {"postgres", "seaweedfs", "application", "deployment", "service", "verify"}
        )
        assert all("result" not in outputs(n) for n in nodes)
        assert (
            "Preflight refused: request bytes differ from the submitted digest" in log.splitlines()
        )
    else:
        assert not active.intersection({"deployment", "service", "verify"})
        nested = named(workflow, "preflight", "Steps")
        assert nested["phase"] == "Succeeded" and outputs(nested)["result"] == preflight
        assert all(named(workflow, n)["phase"] == "Succeeded" for n in ("postgres", "seaweedfs"))
        for text in (
            "forbidden",
            "perfeng-local-control-plane",
            "system:serviceaccount:argo-workflows:release-readiness",
            "argocd",
        ):
            assert text in log


if __name__ == "__main__":
    mode, directory, label, *extra = sys.argv[1:]
    fixture = Path(directory)
    results = Path(extra[0])

    def load(path: Path) -> Any:
        return json.loads(path.read_text(encoding="utf-8"))

    workflow = load(results / f"readiness-parent-{label}.workflow.json")
    preflight = load(results / "parent-first.result.json")
    if mode == "success":
        result = success(
            workflow,
            preflight,
            load(fixture / "parent-parameters.json"),
            load(fixture / "readiness-parent-api.json"),
            Path("deployments/local-control-plane.json").read_bytes(),
        )
        (results / f"readiness-parent-{label}.result.json").write_text(
            json.dumps(result, sort_keys=True), encoding="utf-8"
        )
    elif mode == "refusal":
        refusal(
            workflow,
            load(results / f"readiness-parent-{label}.workers.json"),
            extra[1],
            (fixture / "readiness-parent-worker.log").read_text(encoding="utf-8"),
            preflight,
        )
    else:
        raise ValueError("Unknown readiness-parent evidence mode")
