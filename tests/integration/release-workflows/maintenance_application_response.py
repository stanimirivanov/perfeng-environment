"""Bind root/child no-op PATCH response loss to unchanged running-state retention."""

import json
import sys
from pathlib import Path
from typing import Any

import maintenance_patch_response as response
import maintenance_write_denial as denial


def main() -> None:
    mode, resource, directory, output = sys.argv[1:]
    assert resource in {"root", "child"}
    fixture, results = Path(directory), Path(output)
    prefix = "maintenance-" + resource + "-response"

    def load(suffix: str) -> Any:
        return json.loads((fixture / f"{prefix}.{suffix}.json").read_text())

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

    if mode == "template":
        print(json.dumps(response.template(resource)))
        return
    if mode == "installed":
        expected, installed = response.template(resource), load("installed")
        assert installed["spec"] == expected["spec"]
        assert all(installed["metadata"][k] == v for k, v in expected["metadata"].items())
        return
    assert mode in {"prepare", "verify"}
    before = state("before")
    workflow = load("barrier" if mode == "prepare" else "workflow")
    if mode == "prepare":
        report = response.diagnostics.barrier_diagnostics(
            workflow,
            load("barrier-pods"),
            response.BARRIER,
            (fixture / f"{prefix}.state.txt").read_text().strip(),
        )
        (results / f"{prefix}.barrier.json").write_text(json.dumps(report))
        assert report["decision"] == "barrier-ready"
        assert not any(
            n["type"] == "Pod" and n["displayName"] == "pause-" + resource
            for n in workflow["status"]["nodes"].values()
        )
    guard = response.ready(
        workflow,
        json.loads((fixture / "parent-parameters.json").read_text()),
        (fixture / "parent-request.json").read_bytes(),
        Path("deployments/local-control-plane.json").read_bytes(),
        before,
        resource=resource,
    )
    target = load("target")
    assert (
        target["metadata"]["uid"] == guard["uid"]
        and target["metadata"]["resourceVersion"] == guard["resourceVersion"]
        and target["spec"] == guard["spec"]
    )
    payload, desired = response.authority.prepare(resource, target)
    assert desired == target["spec"] and response.proxy.target({"before": target}) == resource
    expected = {"before": target, "patch": payload}
    if mode == "prepare":
        (fixture / f"{prefix}.expected.json").write_text(json.dumps(expected))
        response.assets(fixture, prefix, resource)
        return
    assert workflow["metadata"]["uid"] == load("barrier")["metadata"]["uid"]
    assert load("expected") == expected
    after = state("after")
    denial.retained(before, after)
    report = response.failed(
        workflow,
        load("workers"),
        expected,
        [json.loads(line) for line in (fixture / f"{prefix}.proxy.log").read_text().splitlines()],
        after,
        resource=resource,
    )
    (results / f"{prefix}.outcome.json").write_text(json.dumps(report))


if __name__ == "__main__":
    main()
