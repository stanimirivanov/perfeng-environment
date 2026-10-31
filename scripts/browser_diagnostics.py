"""Assemble browser-diagnostics/v1 from verified browser capture evidence."""

import argparse
import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__:
    from . import browser_environment, browser_upload
else:
    import browser_environment
    import browser_upload

REFERENCE_KEYS = {"id", "runId", "kind", "uri", "sha256", "sizeBytes", "mediaType", "format"}
INTEGRITY_KEYS = {"sha256", "sizeBytes"}


def mapping(value: object, name: str) -> dict[str, Any]:
    return browser_environment.mapping(value, name)


def exact_keys(value: dict[str, Any], name: str, expected: set[str]) -> None:
    browser_environment.exact_keys(value, name, expected)


def timestamp(value: object, name: str) -> tuple[str, datetime]:
    return browser_environment.timestamp(value, name)


def file_integrity(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    return {"sha256": hashlib.sha256(content).hexdigest(), "sizeBytes": len(content)}


def validate_integrity(
    value: object,
    name: str,
    path: Path,
    extra_keys: set[str] | None = None,
) -> dict[str, Any]:
    integrity = mapping(value, name)
    exact_keys(integrity, name, INTEGRITY_KEYS | (extra_keys or set()))
    expected = file_integrity(path)
    if any(integrity.get(key) != expected[key] for key in INTEGRITY_KEYS):
        raise ValueError(f"{name} does not match the local artifact bytes")
    return integrity


def validate_reference(
    value: object,
    run_id: str,
    role: str,
    path: Path,
) -> dict[str, Any]:
    reference = mapping(value, f"upload artifact {role}")
    exact_keys(reference, f"upload artifact {role}", REFERENCE_KEYS)
    try:
        artifact_id = str(uuid.UUID(str(reference["id"])))
    except (ValueError, AttributeError, TypeError):
        raise ValueError(f"upload artifact {role} has an invalid ID") from None
    _, media_type, artifact_format = browser_upload.ROLE_DETAILS[role]
    expected = {
        "id": artifact_id,
        "runId": run_id,
        "kind": "raw",
        "uri": browser_upload.object_uri(run_id, role),
        **file_integrity(path),
        "mediaType": media_type,
        "format": artifact_format,
    }
    if reference != expected:
        raise ValueError(f"upload artifact {role} does not match its verified local input")
    return dict(reference)


def upload_references(
    value: object,
    run_id: str,
    inputs: list[tuple[str, Path]],
) -> dict[str, dict[str, Any]]:
    receipt = mapping(value, "upload receipt")
    exact_keys(receipt, "upload receipt", {"schema", "runId", "artifacts"})
    if receipt["schema"] != "local-browser-upload/v1" or receipt["runId"] != run_id:
        raise ValueError("Upload receipt does not belong to the measurement run")
    artifacts = receipt["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(inputs):
        raise ValueError("Upload receipt does not contain the complete browser artifact set")
    by_uri = {}
    for artifact in artifacts:
        candidate = mapping(artifact, "upload receipt artifact")
        uri = candidate.get("uri")
        if not isinstance(uri, str) or uri in by_uri:
            raise ValueError("Upload receipt contains an invalid or duplicate artifact URI")
        by_uri[uri] = candidate
    result = {}
    for role, path in inputs:
        uri = browser_upload.object_uri(run_id, role)
        if uri not in by_uri:
            raise ValueError(f"Upload receipt is missing the {role} artifact")
        result[role] = validate_reference(by_uri[uri], run_id, role, path)
    identities = [reference["id"] for reference in result.values()]
    if len(set(identities)) != len(identities):
        raise ValueError("Upload receipt contains duplicate artifact IDs")
    return result


def runner_artifacts(
    value: object,
    measurements: dict[str, Any],
    paths: dict[str, Path],
) -> dict[str, Any]:
    receipt = mapping(value, "runner receipt")
    exact_keys(receipt, "runner receipt", {"schema", "runId", "testId", "producer", "artifacts"})
    if (
        receipt["schema"] != "playwright-runner-receipt/v2"
        or receipt["runId"] != measurements["runId"]
        or receipt["testId"] != measurements["testId"]
    ):
        raise ValueError("Runner receipt does not belong to the measurement execution")
    artifacts = mapping(receipt["artifacts"], "runner receipt artifacts")
    mode = measurements["diagnosticMode"]
    expected_keys = {
        "baseline": {"measurements"},
        "lightweight": {"measurements", "observations"},
        "trace": {"measurements", "trace"},
        "smoothness": {"measurements", "trace"},
        "memory": {"measurements", "memory"},
    }[mode]
    exact_keys(artifacts, "runner receipt artifacts", expected_keys)
    validate_integrity(artifacts["measurements"], "runner measurements", paths["measurements"])
    if mode == "lightweight":
        validate_integrity(artifacts["observations"], "runner observations", paths["observations"])
    elif mode in {"trace", "smoothness"}:
        trace = validate_integrity(
            artifacts["trace"],
            "runner trace",
            paths["trace"],
            {"iteration", "format", "mediaType", "dataLossOccurred", "startedAt", "finishedAt"},
        )
        if (
            not isinstance(trace["iteration"], int)
            or isinstance(trace["iteration"], bool)
            or not isinstance(trace["dataLossOccurred"], bool)
            or trace["format"] != "chrome-trace-json-gzip"
            or trace["mediaType"] != "application/gzip"
        ):
            raise ValueError("Runner trace metadata is invalid")
        _, start = timestamp(trace["startedAt"], "runner trace start")
        _, end = timestamp(trace["finishedAt"], "runner trace end")
        if start >= end:
            raise ValueError("Runner trace start must precede its end")
    elif mode == "memory":
        memory = mapping(artifacts["memory"], "runner memory")
        exact_keys(memory, "runner memory", {"captureIterations", "before", "after"})
        iterations = memory["captureIterations"]
        if (
            not isinstance(iterations, list)
            or len(iterations) < 2
            or any(not isinstance(item, int) or isinstance(item, bool) for item in iterations)
            or iterations != sorted(set(iterations))
        ):
            raise ValueError("Runner memory capture iterations are invalid")
        for role, key in (("heap-before", "before"), ("heap-after", "after")):
            snapshot = validate_integrity(
                memory[key],
                f"runner memory {key}",
                paths[role],
                {"capturedAt", "format", "mediaType", "uncompressedSizeBytes", "census"},
            )
            if (
                snapshot["format"] != "chrome-heap-snapshot-json-gzip"
                or snapshot["mediaType"] != "application/gzip"
                or not isinstance(snapshot["uncompressedSizeBytes"], int)
                or isinstance(snapshot["uncompressedSizeBytes"], bool)
                or snapshot["uncompressedSizeBytes"] <= 0
                or not isinstance(snapshot["census"], dict)
            ):
                raise ValueError(f"Runner memory {key} metadata is invalid")
            timestamp(snapshot["capturedAt"], f"runner memory {key} capture time")
        before = timestamp(memory["before"]["capturedAt"], "runner memory before capture time")[1]
        after = timestamp(memory["after"]["capturedAt"], "runner memory after capture time")[1]
        if before >= after:
            raise ValueError("Runner memory captures are not chronologically ordered")
    return artifacts


def assemble(
    inputs: list[tuple[str, Path]],
    runner_receipt_value: object,
    upload_receipt_value: object,
    created_at: str,
) -> dict[str, Any]:
    paths = dict(inputs)
    measurements = browser_upload.read_json(paths["measurements"], "measurements")
    environment = browser_upload.read_json(paths["environment"], "environment")
    mode = measurements.get("diagnosticMode")
    if mode not in {"lightweight", "trace", "memory", "smoothness"}:
        raise ValueError("Browser diagnostics require a diagnostic measurement mode")
    run_id = measurements["runId"]
    test_id = measurements.get("testId")
    if not isinstance(test_id, str) or not test_id:
        raise ValueError("Measurements contain an invalid test ID")
    workload = mapping(measurements.get("workload"), "measurements.workload")
    exact_keys(workload, "measurements.workload", {"id", "version", "profile", "sha256"})
    scenario = mapping(measurements.get("scenario"), "measurements.scenario")
    exact_keys(
        scenario,
        "measurements.scenario",
        {
            "cacheProfile",
            "contextReuse",
            "pageReuse",
            "warmupIterations",
            "measurementIterations",
        },
    )
    context_reuse = scenario.get("contextReuse")
    page_reuse = scenario.get("pageReuse")
    if context_reuse not in {"per-iteration", "per-run"} or page_reuse not in {
        "per-iteration",
        "per-run",
    }:
        raise ValueError("Measurements contain an invalid browser lifetime")
    measurement_iterations = scenario["measurementIterations"]
    if (
        not isinstance(measurement_iterations, int)
        or isinstance(measurement_iterations, bool)
        or measurement_iterations < 1
    ):
        raise ValueError("Measurements contain an invalid iteration count")
    measurement_environment = mapping(measurements.get("environment"), "measurements.environment")
    exact_keys(measurement_environment, "measurements.environment", {"profile", "fingerprint"})
    if environment.get("profile") != measurement_environment.get("profile") or environment.get(
        "fingerprint"
    ) != measurement_environment.get("fingerprint"):
        raise ValueError("Browser environment identity does not match measurements")
    references = upload_references(upload_receipt_value, run_id, inputs)
    artifacts = runner_artifacts(runner_receipt_value, measurements, paths)

    if mode == "lightweight":
        observations = browser_upload.read_json(paths["observations"], "observations")
        if (
            observations.get("testId") != test_id
            or observations.get("workload") != workload
            or observations.get("environment") != measurement_environment
        ):
            raise ValueError("Browser observations identity does not match measurements")
        execution = mapping(observations.get("execution"), "observations.execution")
        if (
            execution.get("mode") != "lightweight"
            or execution.get("contextReuse") != context_reuse
            or execution.get("pageReuse") != page_reuse
        ):
            raise ValueError("Observation execution does not match measurements")
        iterations = execution.get("captureIterations")
        window = mapping(observations.get("captureWindow"), "observations.captureWindow")
        capture_window = {"start": window.get("start"), "end": window.get("end")}
        captures = [
            {
                "type": "observations",
                "scope": {"kind": "run"},
                "status": "COMPLETE",
                "reasons": [],
                "sensitive": False,
                "artifact": references["observations"],
            }
        ]
        sources = ["playwright", "web-performance"]
    elif mode in {"trace", "smoothness"}:
        trace = mapping(artifacts["trace"], "runner trace")
        iterations = [trace["iteration"]]
        capture_window = {"start": trace["startedAt"], "end": trace["finishedAt"]}
        data_loss = trace["dataLossOccurred"]
        captures = [
            {
                "type": "trace",
                "scope": {"kind": "iteration", "iteration": trace["iteration"]},
                "status": "INCOMPLETE" if data_loss else "COMPLETE",
                "reasons": ["Chrome reported trace-buffer data loss."] if data_loss else [],
                "sensitive": True,
                "dataLossOccurred": data_loss,
                "artifact": references["trace"],
            }
        ]
        sources = ["playwright", "cdp"]
    else:
        memory = mapping(artifacts["memory"], "runner memory")
        iterations = memory["captureIterations"]
        capture_window = {
            "start": memory["before"]["capturedAt"],
            "end": memory["after"]["capturedAt"],
        }
        captures = [
            {
                "type": f"heap-snapshot-{key}",
                "scope": {"kind": "run"},
                "status": "COMPLETE",
                "reasons": [],
                "sensitive": True,
                "artifact": references[f"heap-{key}"],
            }
            for key in ("before", "after")
        ]
        sources = ["playwright", "cdp"]

    if (
        not isinstance(iterations, list)
        or not iterations
        or any(
            not isinstance(item, int) or isinstance(item, bool) or item < 1 for item in iterations
        )
        or iterations != sorted(set(iterations))
        or iterations[-1] > measurement_iterations
    ):
        raise ValueError("Diagnostic capture iterations are invalid")
    start_text, start = timestamp(capture_window["start"], "diagnostic capture start")
    end_text, end = timestamp(capture_window["end"], "diagnostic capture end")
    created_text, created = timestamp(created_at, "diagnostic creation time")
    measurement_window = mapping(
        measurements.get("measurementWindow"), "measurements.measurementWindow"
    )
    exact_keys(measurement_window, "measurements.measurementWindow", {"start", "end"})
    _, measurement_start = timestamp(
        measurement_window["start"], "measurements.measurementWindow.start"
    )
    _, measurement_end = timestamp(measurement_window["end"], "measurements.measurementWindow.end")
    if start >= end or measurement_start > start or end > measurement_end or created < end:
        raise ValueError("Diagnostic capture and creation timestamps are not chronological")
    profile = mapping(measurement_environment.get("profile"), "measurements.environment.profile")
    exact_keys(profile, "measurements.environment.profile", {"id", "version"})
    fingerprint = measurement_environment.get("fingerprint")
    if not isinstance(fingerprint, str) or re.fullmatch(r"[a-f0-9]{64}", fingerprint) is None:
        raise ValueError("Measurements contain an invalid environment fingerprint")
    return {
        "schemaVersion": 1,
        "kind": "BrowserDiagnostics",
        "runId": run_id,
        "testId": test_id,
        "workload": dict(workload),
        "environment": {
            "profile": dict(profile),
            "fingerprint": fingerprint,
            "artifact": references["environment"],
        },
        "execution": {
            "mode": mode,
            "contextReuse": context_reuse,
            "pageReuse": page_reuse,
            "captureIterations": iterations,
        },
        "sources": sources,
        "captureWindow": {"start": start_text, "end": end_text},
        "captures": captures,
        "createdAt": created_text,
    }


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["assemble"])
    parser.add_argument("--measurements", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--runner-receipt", type=Path, required=True)
    parser.add_argument("--upload-receipt", type=Path, required=True)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--heap-snapshot-before", type=Path)
    parser.add_argument("--heap-snapshot-after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        _, inputs = browser_upload.browser_inputs(
            args.measurements,
            args.environment,
            args.observations,
            args.trace,
            args.heap_snapshot_before,
            args.heap_snapshot_after,
        )
        manifest = assemble(
            inputs,
            browser_upload.read_json(args.runner_receipt, "runner receipt"),
            browser_upload.read_json(args.upload_receipt, "upload receipt"),
            now(),
        )
        content = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("xb") as output:
            output.write(content)
        print(
            json.dumps(
                {
                    "path": str(args.output),
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "sizeBytes": len(content),
                }
            )
        )
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"Browser diagnostics error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
