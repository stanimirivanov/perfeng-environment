"""Offline-checkable CI evidence; no client, orchestration or production helper."""

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

TASKS = {
    "bucket": ("perf-platform", "bucket-job"),
    "database": ("perf-platform", "database-job"),
    "registry": ("perf-generators", "registry-job"),
}
STEPS = {"bucket": "bucket", "database": "database-roles", "registry": "execution-registry"}


def check(directory: Path, label: str, failure: str, parameters: dict, cluster: str) -> None:
    """Bind successful outputs and reject failures that executed a forbidden stage."""

    def load(suffix):
        return json.loads((directory / f"external-parent-{label}{suffix}").read_text())

    workflow = load(".workflow.json")
    nodes = list(workflow["status"]["nodes"].values())
    root = workflow["status"]["nodes"][workflow["metadata"]["name"]]

    def outputs(node):
        values = [p for p in node.get("outputs", {}).get("parameters", []) if "value" in p]
        assert len({p["name"] for p in values}) == len(values)
        return {p["name"]: p["value"] for p in values}

    def owning(step, phase, kind="Pod"):
        matches = [n for n in nodes if n.get("displayName") == step and n.get("type") == kind]
        assert len(matches) == 1 and matches[0]["phase"] in phase
        return matches[0]

    def absent(*steps):
        assert not any(
            n.get("displayName") in steps and n["phase"] not in {"Skipped", "Omitted"}
            for n in nodes
        )

    tasks = {
        "success": set(TASKS),
        "preflight": set(),
        "bucket": {"bucket"},
        "database": {"bucket", "database"},
    }[failure]
    inventory = (directory / f"external-parent-{label}.jobs.txt").read_text().splitlines()
    jobs = {task: load(f"-{task}.job.json") for task in tasks}
    assert len(inventory) == len(set(inventory)) == len(tasks)
    assert set(inventory) == {
        f"{j['metadata']['namespace']}/{j['metadata']['name']}" for j in jobs.values()
    }
    for task, job in jobs.items():
        assert job["metadata"]["namespace"] == TASKS[task][0] and job["metadata"]["uid"]
        assert job["metadata"]["name"].startswith(
            {
                "bucket": "perfeng-prepare-bucket-v1-",
                "database": "perfeng-prepare-db-v1-",
                "registry": "perfeng-prepare-registry-v1-",
            }[task]
        )
        if task != failure:
            assert job["status"]["succeeded"] == 1

    result = outputs(root)
    if failure == "success":
        assert workflow["status"]["phase"] == root["phase"] == "Succeeded"
        assert set(result) == {"preflight-result", "bucket-job", "database-job", "registry-job"}
        for step in STEPS.values():
            owning(step, {"Succeeded"})
    else:
        assert workflow["status"]["phase"] in {"Failed", "Error"}
        failed_step = {
            "preflight": "verify-identities",
            "bucket": "bucket",
            "database": "database-roles",
        }[failure]
        failed = owning(failed_step, {"Failed", "Error"})
        if failure == "preflight":
            assert "result" not in outputs(failed) and not result
            absent("task-selection", *STEPS.values())
            return
        absent(*[step for task, step in STEPS.items() if task not in tasks])
        assert set(result) <= {"preflight-result", *(TASKS[task][1] for task in tasks)}
        if failure == "database":
            owning("bucket", {"Succeeded"})

    preflight = json.loads(outputs(owning("preflight", {"Succeeded"}, "Steps"))["result"])
    Draft202012Validator(
        json.loads(Path("deployments/release-preflight-result-v1.schema.json").read_text())
    ).validate(preflight)
    for field, parameter in (
        ("requestRevision", "request-revision"),
        ("requestDigest", "request-sha256"),
        ("selectionRevision", "selection-revision"),
        ("requestPath", "request-path"),
    ):
        assert preflight[field] == parameters[parameter]
    assert preflight["clusterUID"] == cluster
    assert preflight == json.loads((directory / "parent-first.result.json").read_text())
    owning("task-selection", {"Succeeded"})
    if "preflight-result" in result:
        assert json.loads(result["preflight-result"]) == preflight
    for task, job in jobs.items():
        if TASKS[task][1] in result:
            assert result[TASKS[task][1]] == job["metadata"]["name"]


if __name__ == "__main__":
    try:
        check(
            Path(sys.argv[1]),
            sys.argv[2],
            sys.argv[3],
            json.loads(Path(sys.argv[4]).read_text()),
            sys.argv[5],
        )
    except (AssertionError, ValueError, KeyError, TypeError) as error:
        print(f"External parent evidence refused ({type(error).__name__})", file=sys.stderr)
        sys.exit(1)
