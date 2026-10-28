"""Bind complete-parent refusal to the naturally empty initial adoption ledger."""

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import maintenance_pause_evidence as maintenance
import maintenance_write_denial as denial

from tests.release_verifier_support import runtime

PREFIX = "maintenance-unadopted"


def verify(
    workflow: dict[str, Any],
    workers: list[dict[str, Any]],
    parameters: dict[str, str],
    preflight: dict[str, Any],
    raw: bytes,
    lock: bytes,
    before: dict[str, Any],
    after: dict[str, Any],
    log: str,
) -> dict[str, Any]:
    assert before["records"] == after["records"] == []
    denial.retained(before, after)
    health, _ = maintenance.running(workflow, parameters, preflight, lock, before)

    def observe(name: str) -> Any:
        return maintenance.readiness.outputs(maintenance.readiness.named(workflow, name))[
            "observation"
        ]

    assert observe("receipt") is None and observe("pointer") is None
    values = {
        "readiness": health,
        "root": observe("root"),
        "receipt": None,
        "pointer": None,
        "root_after": observe("root-after"),
    }
    node = maintenance.readiness.named(workflow, "baseline")
    expected = {
        "phase": "finish",
        **{k.replace("_", "-"): v for k, v in values.items()},
    }
    actual = node["inputs"]["parameters"]
    assert len(actual) == len(expected)
    assert {
        p["name"]: p["value"] if p["name"] == "phase" else json.loads(p["value"]) for p in actual
    } == expected
    # Replay the immutable runtime with observed inputs; redirect only its private I/O.
    # The planner accepts a new adoption, but finish must refuse before any output.
    planned = maintenance.policy.plan_adoption(raw, **values)
    assert planned["create-receipt"] == planned["create-pointer"] == "true"
    with (
        patch.object(runtime, "request_bytes", return_value=raw),
        patch.object(runtime, "inputs", return_value=values),
        patch.dict(runtime.os.environ, {"PHASE": "finish"}),
        patch.object(runtime, "output") as output,
    ):
        try:
            runtime.run_adoption()
        except maintenance.policy.ReadinessRefused as error:
            assert str(error) == "receipt or fresh root observation missing"
        else:
            raise AssertionError("Finish accepted an unadopted baseline")
        output.assert_not_called()
    maintenance.baseline_refused(workflow, workers, log)
    return {
        "decision": "maintenance-unadopted-refused",
        "workflowUID": workflow["metadata"]["uid"],
        "clusterUID": preflight["clusterUID"],
    }


def main() -> None:
    directory, output = sys.argv[1:]
    fixture, results = Path(directory), Path(output)

    def load(path: Path) -> Any:
        return json.loads(path.read_text())

    def state(label: str) -> dict[str, Any]:
        return {
            key: load(fixture / f"{PREFIX}-{label}.{key}.json")
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

    result = verify(
        load(fixture / f"{PREFIX}.workflow.json"),
        load(fixture / f"{PREFIX}.workers.json"),
        load(fixture / "parent-parameters.json"),
        load(results / "parent-first.result.json"),
        (fixture / "parent-request.json").read_bytes(),
        Path("deployments/local-control-plane.json").read_bytes(),
        state("before"),
        state("after"),
        (fixture / f"{PREFIX}.worker.log").read_text(),
    )
    (results / f"{PREFIX}.outcome.json").write_text(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
