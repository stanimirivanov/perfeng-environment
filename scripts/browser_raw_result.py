"""Assemble raw-result/v2 from verified native browser publication receipts."""

import argparse
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

if __package__:
    from . import browser_diagnostics, browser_upload
else:
    import browser_diagnostics
    import browser_upload

CONTRACTS_VERSION = "0.12.0"
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
RESOURCE_ID_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
SHA_PATTERN = re.compile(r"[a-f0-9]{64}")
GIT_SHA_PATTERN = re.compile(r"[a-f0-9]{40}")


def producer(value: object, measurements: dict[str, Any]) -> dict[str, Any]:
    receipt = browser_diagnostics.mapping(value, "runner receipt")
    browser_diagnostics.exact_keys(
        receipt,
        "runner receipt",
        {"schema", "runId", "testId", "producer", "artifacts"},
    )
    if (
        receipt["schema"] != "playwright-runner-receipt/v2"
        or receipt["runId"] != measurements["runId"]
        or receipt["testId"] != measurements["testId"]
    ):
        raise ValueError("Runner receipt does not belong to the measurement execution")
    value = browser_diagnostics.mapping(receipt["producer"], "runner producer")
    browser_diagnostics.exact_keys(value, "runner producer", {"name", "version", "artifact"})
    runtime = browser_diagnostics.mapping(measurements.get("runtime"), "measurements.runtime")
    version = value.get("version")
    if (
        value.get("name") != "playwright"
        or not isinstance(version, str)
        or VERSION_PATTERN.fullmatch(version) is None
        or version != runtime.get("playwrightVersion")
    ):
        raise ValueError("Runner producer does not match the measured Playwright runtime")

    artifact = browser_diagnostics.mapping(value["artifact"], "runner producer artifact")
    browser_diagnostics.exact_keys(
        artifact,
        "runner producer artifact",
        {"kind", "repository", "gitSha", "dependencyLock"},
    )
    repository = artifact.get("repository")
    git_sha = artifact.get("gitSha")
    location = urlsplit(repository) if isinstance(repository, str) else None
    if (
        artifact.get("kind") != "source-checkout"
        or location is None
        or location.scheme != "https"
        or not location.hostname
        or location.username is not None
        or location.password is not None
        or location.query
        or location.fragment
        or not isinstance(git_sha, str)
        or GIT_SHA_PATTERN.fullmatch(git_sha) is None
    ):
        raise ValueError("Runner source-checkout provenance is invalid")
    dependency_lock = browser_diagnostics.mapping(
        artifact["dependencyLock"], "runner dependency lock"
    )
    browser_diagnostics.exact_keys(dependency_lock, "runner dependency lock", {"path", "sha256"})
    lock_hash = dependency_lock.get("sha256")
    if (
        dependency_lock.get("path") != "pnpm-lock.yaml"
        or not isinstance(lock_hash, str)
        or SHA_PATTERN.fullmatch(lock_hash) is None
    ):
        raise ValueError("Runner dependency-lock provenance is invalid")
    return {"name": "playwright", "version": version, "artifact": dict(artifact)}


def diagnostic_reference(
    path: Path,
    receipt_value: object,
    run_id: str,
    evidence: list[dict[str, Any]],
    measurements: dict[str, Any],
) -> dict[str, Any]:
    manifest = browser_upload.read_json(path, "browser diagnostics")
    workload = measurements["workload"]
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("kind") != "BrowserDiagnostics"
        or manifest.get("runId") != run_id
        or manifest.get("testId") != measurements["testId"]
        or manifest.get("workload") != workload
    ):
        raise ValueError("Browser diagnostics do not match the measurement execution")
    environment = browser_diagnostics.mapping(
        manifest.get("environment"), "browser diagnostics environment"
    )
    captures = manifest.get("captures")
    if not isinstance(captures, list) or not captures:
        raise ValueError("Browser diagnostics contain no capture evidence")
    declared = [environment.get("artifact")]
    declared.extend(
        browser_diagnostics.mapping(capture, "browser diagnostic capture").get("artifact")
        for capture in captures
    )
    expected = [
        reference for reference in evidence if reference["format"] != "playwright-measurements/v2"
    ]
    if len(declared) != len(expected) or {
        json.dumps(reference, sort_keys=True) for reference in declared
    } != {json.dumps(reference, sort_keys=True) for reference in expected}:
        raise ValueError("Browser diagnostics do not declare the complete verified evidence set")
    references = browser_diagnostics.upload_references(
        receipt_value,
        run_id,
        [("diagnostics", browser_upload.input_file(path, "browser diagnostics"))],
    )
    return references["diagnostics"]


def assemble(
    inputs: list[tuple[str, Path]],
    runner_receipt_value: object,
    upload_receipt_value: object,
    diagnostics_path: Path | None,
    diagnostics_receipt_value: object | None,
    created_at: str,
) -> dict[str, Any]:
    paths = dict(inputs)
    measurements = browser_upload.read_json(paths["measurements"], "measurements")
    run_id = measurements["runId"]
    browser_diagnostics.runner_artifacts(runner_receipt_value, measurements, paths)
    raw_producer = producer(runner_receipt_value, measurements)
    references = browser_diagnostics.upload_references(upload_receipt_value, run_id, inputs)
    artifacts = list(references.values())
    mode = measurements.get("diagnosticMode")
    if mode == "baseline":
        if diagnostics_path is not None or diagnostics_receipt_value is not None:
            raise ValueError("Baseline raw results must not include browser diagnostics")
    else:
        if diagnostics_path is None or diagnostics_receipt_value is None:
            raise ValueError("Diagnostic raw results require a published diagnostics manifest")
        artifacts.append(
            diagnostic_reference(
                diagnostics_path,
                diagnostics_receipt_value,
                run_id,
                artifacts,
                measurements,
            )
        )

    ids = [artifact["id"] for artifact in artifacts]
    uris = [artifact["uri"] for artifact in artifacts]
    if len(set(ids)) != len(ids) or len(set(uris)) != len(uris):
        raise ValueError("Raw result contains duplicate artifact identity or location")
    workload = browser_diagnostics.mapping(measurements.get("workload"), "measurements.workload")
    browser_diagnostics.exact_keys(
        workload, "measurements.workload", {"id", "version", "profile", "sha256"}
    )
    if (
        not isinstance(workload.get("id"), str)
        or RESOURCE_ID_PATTERN.fullmatch(workload["id"]) is None
        or not isinstance(workload.get("version"), str)
        or VERSION_PATTERN.fullmatch(workload["version"]) is None
        or not isinstance(workload.get("sha256"), str)
        or SHA_PATTERN.fullmatch(workload["sha256"]) is None
    ):
        raise ValueError("Measurements contain invalid workload provenance")
    test_id = measurements.get("testId")
    if not isinstance(test_id, str) or RESOURCE_ID_PATTERN.fullmatch(test_id) is None:
        raise ValueError("Measurements contain an invalid test ID")
    window = browser_diagnostics.mapping(
        measurements.get("measurementWindow"), "measurements.measurementWindow"
    )
    browser_diagnostics.exact_keys(window, "measurements.measurementWindow", {"start", "end"})
    start_text, start = browser_diagnostics.timestamp(window["start"], "measurement start")
    end_text, end = browser_diagnostics.timestamp(window["end"], "measurement end")
    created_text, created = browser_diagnostics.timestamp(created_at, "raw-result creation time")
    if start >= end or created < end:
        raise ValueError("Raw-result timestamps are not chronological")
    return {
        "schemaVersion": 2,
        "kind": "RawResult",
        "contractsVersion": CONTRACTS_VERSION,
        "runId": run_id,
        "testId": test_id,
        "workload": {key: workload[key] for key in ("id", "version", "sha256")},
        "producer": raw_producer,
        "measurementWindow": {"start": start_text, "end": end_text},
        "createdAt": created_text,
        "artifacts": artifacts,
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
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--diagnostics-upload-receipt", type=Path)
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
        diagnostics_receipt = (
            None
            if args.diagnostics_upload_receipt is None
            else browser_upload.read_json(
                args.diagnostics_upload_receipt, "diagnostics upload receipt"
            )
        )
        manifest = assemble(
            inputs,
            browser_upload.read_json(args.runner_receipt, "runner receipt"),
            browser_upload.read_json(args.upload_receipt, "upload receipt"),
            args.diagnostics,
            diagnostics_receipt,
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
        print(f"Browser raw-result error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
