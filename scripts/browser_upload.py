"""Publish native browser artifacts through the restricted local S3 identity."""

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import artifact_upload, cluster, raw_result_upload
    from . import object_store as store
else:
    import artifact_upload
    import cluster
    import object_store as store
    import raw_result_upload

RUN_ID_PATTERN = re.compile(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")
ROLE_DETAILS = {
    "diagnostics": (
        "browser-diagnostics.json",
        "application/json",
        "browser-diagnostics/v1",
    ),
    "environment": ("browser-environment.json", "application/json", "browser-environment/v1"),
    "measurements": (
        "playwright-measurements.json",
        "application/json",
        "playwright-measurements/v2",
    ),
    "raw-result": ("raw-result.json", "application/json", "raw-result/v2"),
    "observations": (
        "browser-observations.json",
        "application/json",
        "browser-observations-json",
    ),
    "trace": ("chrome-trace.json.gz", "application/gzip", "chrome-trace-json-gzip"),
    "heap-before": (
        "heap-snapshot-before.json.gz",
        "application/gzip",
        "chrome-heap-snapshot-json-gzip",
    ),
    "heap-after": (
        "heap-snapshot-after.json.gz",
        "application/gzip",
        "chrome-heap-snapshot-json-gzip",
    ),
}


def object_uri(run_id: str, role: str) -> str:
    filename = ROLE_DETAILS[role][0]
    if role == "raw-result":
        return f"s3://{store.BUCKET}/runs/{run_id}/{filename}"
    return f"s3://{store.BUCKET}/runs/{run_id}/raw/browser/{filename}"


def read_json(path: Path, name: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} must be readable UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def input_file(path: Path, name: str) -> Path:
    if path.is_symlink():
        raise ValueError(f"{name} must not be a symbolic link")
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or resolved.stat().st_size == 0:
        raise ValueError(f"{name} must be a non-empty regular file")
    return resolved


def browser_inputs(
    measurements_path: Path,
    environment_path: Path,
    observations_path: Path | None = None,
    trace_path: Path | None = None,
    heap_before_path: Path | None = None,
    heap_after_path: Path | None = None,
) -> tuple[str, list[tuple[str, Path]]]:
    measurements_path = input_file(measurements_path, "measurements")
    environment_path = input_file(environment_path, "environment")
    measurements = read_json(measurements_path, "measurements")
    environment = read_json(environment_path, "environment")
    run_id = measurements.get("runId")
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("Measurements contain an invalid run ID")
    if (
        measurements.get("schemaVersion") != 2
        or measurements.get("kind") != "PlaywrightMeasurements"
    ):
        raise ValueError("Expected PlaywrightMeasurements v2")
    if (
        environment.get("schemaVersion") != 1
        or environment.get("kind") != "BrowserEnvironment"
        or environment.get("runId") != run_id
    ):
        raise ValueError("Browser environment does not belong to the measurement run")

    optional = {
        "observations": observations_path,
        "trace": trace_path,
        "heap-before": heap_before_path,
        "heap-after": heap_after_path,
    }
    mode = measurements.get("diagnosticMode")
    expected = {
        "baseline": set(),
        "lightweight": {"observations"},
        "trace": {"trace"},
        "smoothness": {"trace"},
        "memory": {"heap-before", "heap-after"},
    }.get(mode)
    supplied = {role for role, path in optional.items() if path is not None}
    if expected is None or supplied != expected:
        raise ValueError("Diagnostic files do not match the measurement diagnostic mode")

    inputs = [
        ("environment", environment_path),
        ("measurements", measurements_path),
    ]
    for role, path in optional.items():
        if path is not None:
            resolved = input_file(path, role)
            if role == "observations":
                observations = read_json(resolved, role)
                if (
                    observations.get("schemaVersion") != 1
                    or observations.get("kind") != "BrowserObservations"
                    or observations.get("runId") != run_id
                ):
                    raise ValueError("Browser observations do not belong to the measurement run")
            inputs.append((role, resolved))
    paths = [path for _, path in inputs]
    if len(set(paths)) != len(paths):
        raise ValueError("Browser artifact input paths must be distinct")
    return run_id, inputs


def diagnostic_input(path: Path) -> tuple[str, list[tuple[str, Path]]]:
    path = input_file(path, "diagnostics")
    manifest = read_json(path, "diagnostics")
    run_id = manifest.get("runId")
    if not isinstance(run_id, str) or RUN_ID_PATTERN.fullmatch(run_id) is None:
        raise ValueError("Browser diagnostics contain an invalid run ID")
    if manifest.get("schemaVersion") != 1 or manifest.get("kind") != "BrowserDiagnostics":
        raise ValueError("Expected BrowserDiagnostics v1")

    environment = manifest.get("environment")
    captures = manifest.get("captures")
    if (
        not isinstance(environment, dict)
        or not isinstance(captures, list)
        or not captures
        or any(not isinstance(capture, dict) for capture in captures)
    ):
        raise ValueError("Browser diagnostics do not declare their evidence")
    references = [environment.get("artifact")]
    references.extend(capture.get("artifact") for capture in captures)
    if any(
        not isinstance(reference, dict)
        or reference.get("runId") != run_id
        or reference.get("kind") != "raw"
        for reference in references
    ):
        raise ValueError("Browser diagnostic evidence does not belong to the manifest run")
    return run_id, [("diagnostics", path)]


def raw_result_input(path: Path) -> tuple[str, list[tuple[str, Path]]]:
    run_id, resolved = raw_result_upload.manifest_input(path, {"0.12.0"})
    return run_id, [("raw-result", resolved)]


def artifact_reference(run_id: str, role: str, path: Path) -> dict[str, Any]:
    filename, media_type, artifact_format = ROLE_DETAILS[role]
    return artifact_upload.artifact_reference(
        run_id, path, object_uri(run_id, role), media_type, artifact_format
    )


def upload_pod(name: str, root: Path) -> dict[str, Any]:
    return artifact_upload.upload_pod(name, root)


def upload_script(reference: dict[str, Any]) -> str:
    return artifact_upload.upload_script(reference)


def execute(
    run_id: str,
    inputs: list[tuple[str, Path]],
    receipt_path: Path,
    root: Path = cluster.ROOT,
) -> dict[str, Any]:
    sources = [
        artifact_upload.Source(role, path, artifact_reference(run_id, role, path))
        for role, path in inputs
    ]
    return artifact_upload.publish(
        run_id,
        sources,
        receipt_path,
        lambda items: {
            "schema": "local-browser-upload/v1",
            "runId": run_id,
            "artifacts": [item.reference for item in items],
        },
        pod_prefix="browser-upload",
        subject="browser",
        root=root,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["upload", "upload-diagnostics", "upload-raw-result"])
    parser.add_argument("--measurements", type=Path)
    parser.add_argument("--environment", type=Path)
    parser.add_argument("--diagnostics", type=Path)
    parser.add_argument("--raw-result", type=Path)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--trace", type=Path)
    parser.add_argument("--heap-snapshot-before", type=Path)
    parser.add_argument("--heap-snapshot-after", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "upload":
            if (
                args.measurements is None
                or args.environment is None
                or args.diagnostics is not None
                or args.raw_result is not None
            ):
                raise ValueError("upload requires measurements and environment only")
            run_id, inputs = browser_inputs(
                args.measurements,
                args.environment,
                args.observations,
                args.trace,
                args.heap_snapshot_before,
                args.heap_snapshot_after,
            )
        elif args.action == "upload-diagnostics":
            if (
                args.diagnostics is None
                or args.raw_result is not None
                or args.measurements is not None
                or args.environment is not None
                or any(
                    path is not None
                    for path in (
                        args.observations,
                        args.trace,
                        args.heap_snapshot_before,
                        args.heap_snapshot_after,
                    )
                )
            ):
                raise ValueError("upload-diagnostics requires only a diagnostics manifest")
            run_id, inputs = diagnostic_input(args.diagnostics)
        else:
            if (
                args.raw_result is None
                or args.measurements is not None
                or args.environment is not None
                or args.diagnostics is not None
                or any(
                    path is not None
                    for path in (
                        args.observations,
                        args.trace,
                        args.heap_snapshot_before,
                        args.heap_snapshot_after,
                    )
                )
            ):
                raise ValueError("upload-raw-result requires only a raw-result manifest")
            run_id, inputs = raw_result_input(args.raw_result)
        if args.execute:
            result = execute(run_id, inputs, args.receipt)
            print(json.dumps(result))
        else:
            print(json.dumps(upload_pod("browser-upload-preview", cluster.ROOT), indent=2))
            print(
                f"Preview: publish {len(inputs)} artifacts for {run_id} and write {args.receipt}."
            )
            print("Preview only. Add --execute to create the uploader Pod and immutable objects.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        print(f"Browser upload error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
