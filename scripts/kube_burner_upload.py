"""Publish a completed kube-burner capture to local artifact storage."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import artifact_upload, cluster
    from . import object_store as store
else:
    import artifact_upload
    import cluster
    import object_store as store

RUN_ID_PATTERN = re.compile(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")
TEST_ID = "pod-startup-k8s"
ARTIFACT_TYPES = {
    "jobSummary.json": "kube-burner-job-summary-json",
    "podLatencyMeasurement-pod-startup-smoke.json": "kube-burner-pod-latency-json",
    "podLatencyQuantilesMeasurement-pod-startup-smoke.json": (
        "kube-burner-pod-latency-quantiles-json"
    ),
}


def read_object(path: Path, name: str) -> dict[str, Any]:
    """Read one UTF-8 JSON object without accepting symbolic links."""
    if path.is_symlink():
        raise ValueError(f"{name} must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} must be readable UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def measurement_claims(execution: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Index the exact three measurement claims from a successful receipt."""
    measurements = execution.get("measurements")
    if not isinstance(measurements, list) or len(measurements) != len(ARTIFACT_TYPES):
        raise ValueError("Execution receipt must declare exactly three measurements")
    claims: dict[str, Mapping[str, Any]] = {}
    for claim in measurements:
        if not isinstance(claim, dict) or set(claim) != {"path", "sha256", "sizeBytes"}:
            raise ValueError("Execution receipt contains an invalid measurement claim")
        name = claim.get("path")
        if not isinstance(name, str) or name not in ARTIFACT_TYPES or name in claims:
            raise ValueError("Execution receipt contains an unsupported measurement path")
        claims[name] = claim
    if set(claims) != set(ARTIFACT_TYPES):
        raise ValueError("Execution receipt does not declare the required measurements")
    return claims


def capture_sources(capture_root: Path) -> tuple[str, list[artifact_upload.Source]]:
    """Bind a successful execution receipt to its unchanged measurement bytes."""
    root = capture_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Capture root must be a directory")
    execution = read_object(root / "execution.json", "execution receipt")
    run_id = execution.get("runId")
    tool = execution.get("tool")
    workload = execution.get("workload")
    if (
        execution.get("schemaVersion") != 1
        or execution.get("kind") != "KubernetesBenchmarkExecution"
        or execution.get("contractsVersion") != "0.13.0"
        or not isinstance(run_id, str)
        or RUN_ID_PATTERN.fullmatch(run_id) is None
        or execution.get("benchmark") != "pod-startup"
        or execution.get("exitCode") != 0
        or execution.get("namespaceRemoved") is not True
        or not isinstance(tool, dict)
        or tool.get("name") != "kube-burner"
        or not isinstance(tool.get("artifact"), dict)
        or tool["artifact"].get("kind") != "release-archive"
        or not isinstance(workload, dict)
        or workload.get("id") != "pod-startup-smoke"
    ):
        raise ValueError("Execution receipt is not a successful supported kube-burner capture")

    claims = measurement_claims(execution)
    actual_json = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*.json")
        if path.name != "execution.json"
    }
    if actual_json != set(ARTIFACT_TYPES):
        raise ValueError("Capture directory does not contain exactly the required measurements")
    sources = []
    for name, artifact_format in ARTIFACT_TYPES.items():
        path = root / name
        if path.is_symlink():
            raise ValueError(f"Measurement {name} must not be a symbolic link")
        resolved = path.resolve(strict=True)
        if resolved.parent != root or not resolved.is_file():
            raise ValueError(f"Measurement {name} must be a contained regular file")
        content = resolved.read_bytes()
        claim = claims[name]
        if (
            not content
            or claim.get("sha256") != hashlib.sha256(content).hexdigest()
            or claim.get("sizeBytes") != len(content)
        ):
            raise ValueError(f"Measurement {name} does not match the execution receipt")
        uri = f"s3://{store.BUCKET}/runs/{run_id}/raw/kubernetes/{name}"
        reference = artifact_upload.artifact_reference(
            run_id, resolved, uri, "application/json", artifact_format
        )
        sources.append(artifact_upload.Source(name, resolved, reference))
    return run_id, sources


def manifest_request(sources: Sequence[artifact_upload.Source]) -> dict[str, Any]:
    """Build the exact request accepted by the perfeng-k8s manifest assembler."""
    return {
        "testId": TEST_ID,
        "receipts": [{"file": source.label, "artifact": source.reference} for source in sources],
    }


def execute(
    run_id: str,
    sources: Sequence[artifact_upload.Source],
    receipt_path: Path,
    root: Path = cluster.ROOT,
) -> dict[str, Any]:
    """Publish the capture and write an assembler-ready receipt document."""
    return artifact_upload.publish(
        run_id,
        sources,
        receipt_path,
        manifest_request,
        pod_prefix="kube-burner-upload",
        subject="kube-burner",
        root=root,
    )


def parser() -> argparse.ArgumentParser:
    """Build the preview-first command-line interface."""
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("capture_root", type=Path)
    value.add_argument("--receipt", type=Path, required=True)
    value.add_argument("--execute", action="store_true")
    return value


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate locally by default and publish only when explicitly requested."""
    args = parser().parse_args(arguments)
    try:
        run_id, sources = capture_sources(args.capture_root)
        if args.execute:
            result = execute(run_id, sources, args.receipt)
            print(json.dumps(result))
        else:
            print(
                json.dumps(
                    artifact_upload.upload_pod("kube-burner-upload-preview", cluster.ROOT),
                    indent=2,
                )
            )
            print(
                f"Preview: publish {len(sources)} kube-burner artifacts for {run_id} "
                f"and write {args.receipt}."
            )
            print("Preview only. Add --execute to create the uploader Pod and immutable objects.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        print(f"Kube-burner upload error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
