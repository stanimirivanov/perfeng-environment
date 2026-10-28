"""Bind complete-parent refusal to an empty or receipt-only adoption ledger."""

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
    *,
    receipt_only: bool = False,
) -> dict[str, Any]:
    assert before["records"] == after["records"]
    if receipt_only:
        assert len(before["records"]) == 1 and before["records"][0]["immutable"] is True
        receipt = before["records"][0]
    else:
        assert before["records"] == []
        receipt = None
    denial.retained(before, after)
    health, _ = maintenance.running(workflow, parameters, preflight, lock, before)

    def observe(name: str) -> Any:
        return maintenance.readiness.outputs(maintenance.readiness.named(workflow, name))[
            "observation"
        ]

    assert observe("receipt") == receipt and observe("pointer") is None
    values = {
        "readiness": health,
        "root": observe("root"),
        "receipt": receipt,
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
    # Planning permits the missing creates; finish must refuse before any output.
    planned = maintenance.policy.plan_adoption(raw, **values)
    assert planned["create-receipt"] == ("false" if receipt_only else "true")
    assert planned["create-pointer"] == "true"
    with (
        patch.object(runtime, "request_bytes", return_value=raw),
        patch.object(runtime, "inputs", return_value=values),
        patch.dict(runtime.os.environ, {"PHASE": "finish"}),
        patch.object(runtime, "output") as output,
    ):
        try:
            runtime.run_adoption()
        except maintenance.policy.ReadinessRefused as error:
            assert str(error) == (
                "stored pointer missing"
                if receipt_only
                else "receipt or fresh root observation missing"
            )
        else:
            raise AssertionError("Finish accepted an unadopted baseline")
        output.assert_not_called()
    maintenance.baseline_refused(workflow, workers, log)
    result = {
        "decision": "maintenance-receipt-only-refused"
        if receipt_only
        else "maintenance-unadopted-refused",
        "workflowUID": workflow["metadata"]["uid"],
        "clusterUID": preflight["clusterUID"],
    }
    if receipt_only:
        assert receipt is not None
        result["receiptUID"] = receipt["metadata"]["uid"]
    return result


def main() -> None:
    directory, output, *modes = sys.argv[1:]
    assert len(modes) <= 1
    mode = modes[0] if modes else "initial"
    assert mode in {"initial", "receipt-only"}
    prefix = PREFIX if mode == "initial" else "maintenance-receipt-only"
    fixture, results = Path(directory), Path(output)

    def load(path: Path) -> Any:
        return json.loads(path.read_text())

    def state(label: str) -> dict[str, Any]:
        return {
            key: load(fixture / f"{prefix}-{label}.{key}.json")
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
    if mode == "receipt-only":
        assert before["records"] == load(fixture / "adoption-receipt-only.records.json")
    result = verify(
        load(fixture / f"{prefix}.workflow.json"),
        load(fixture / f"{prefix}.workers.json"),
        load(fixture / "parent-parameters.json"),
        load(results / "parent-first.result.json"),
        (fixture / "parent-request.json").read_bytes(),
        Path("deployments/local-control-plane.json").read_bytes(),
        before,
        after,
        (fixture / f"{prefix}.worker.log").read_text(),
        receipt_only=mode == "receipt-only",
    )
    (results / f"{prefix}.outcome.json").write_text(json.dumps(result) + "\n")


if __name__ == "__main__":
    main()
