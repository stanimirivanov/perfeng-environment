"""Publish a completed raw-result/v2 manifest at its stable discovery key."""

from __future__ import annotations

import argparse
import json
import re
import uuid
from collections.abc import Collection, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

if __package__:
    from . import artifact_upload, cluster
    from . import object_store as store
else:
    import artifact_upload
    import cluster
    import object_store as store

RUN_ID_PATTERN = re.compile(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")
SUPPORTED_CONTRACTS = frozenset({"0.12.0", "0.13.0"})
ARTIFACT_FIELDS = {
    "id",
    "runId",
    "kind",
    "uri",
    "sha256",
    "sizeBytes",
    "mediaType",
    "format",
}


def valid_artifact(artifact: Any, run_id: str) -> bool:
    """Report whether one source claim has the stable artifact/v1 shape."""
    if (
        not isinstance(artifact, dict)
        or set(artifact) != ARTIFACT_FIELDS
        or any(
            not isinstance(artifact[field], str)
            for field in ("id", "runId", "kind", "uri", "sha256", "mediaType", "format")
        )
    ):
        return False
    try:
        identity = uuid.UUID(artifact["id"])
        location = urlsplit(artifact["uri"])
    except (AttributeError, TypeError, ValueError):
        return False
    size = artifact["sizeBytes"]
    return (
        str(identity) == artifact["id"].lower()
        and artifact["runId"] == run_id
        and artifact["kind"] == "raw"
        and location.scheme in {"s3", "https"}
        and location.hostname is not None
        and location.username is None
        and location.password is None
        and not location.query
        and not location.fragment
        and "\\" not in artifact["uri"]
        and re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is not None
        and isinstance(size, int)
        and not isinstance(size, bool)
        and size >= 0
        and re.fullmatch(
            r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*",
            artifact["mediaType"],
        )
        is not None
        and re.fullmatch(r"[a-z0-9][a-z0-9./_-]*", artifact["format"]) is not None
    )


def read_object(path: Path) -> tuple[Path, dict[str, Any]]:
    """Read a non-empty regular UTF-8 JSON object without following a file link."""
    if path.is_symlink():
        raise ValueError("Raw result must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
        if not resolved.is_file() or resolved.stat().st_size == 0:
            raise ValueError("Raw result must be a non-empty regular file")
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Raw result must be readable UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError("Raw result must contain a JSON object")
    return resolved, value


def manifest_input(
    path: Path,
    contracts_versions: Collection[str] = SUPPORTED_CONTRACTS,
) -> tuple[str, Path]:
    """Validate the discovery and source-reference invariants of a raw manifest."""
    resolved, manifest = read_object(path)
    run_id = manifest.get("runId")
    if (
        manifest.get("schemaVersion") != 2
        or manifest.get("kind") != "RawResult"
        or manifest.get("contractsVersion") not in contracts_versions
        or not isinstance(run_id, str)
        or RUN_ID_PATTERN.fullmatch(run_id) is None
    ):
        raise ValueError("Expected a supported RawResult v2 manifest")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("Raw result must declare run-scoped raw evidence")
    identities: set[str] = set()
    locations: set[str] = set()
    for artifact in artifacts:
        if not valid_artifact(artifact, run_id):
            raise ValueError("Raw result must declare run-scoped raw evidence")
        identity = artifact["id"].lower()
        location = artifact["uri"]
        if identity in identities or location in locations:
            raise ValueError("Raw result contains duplicate artifact identity or location")
        identities.add(identity)
        locations.add(location)
    return run_id, resolved


def source(run_id: str, path: Path) -> artifact_upload.Source:
    """Create the immutable claim for the stable discovery object."""
    uri = f"s3://{store.BUCKET}/runs/{run_id}/raw-result.json"
    reference = artifact_upload.artifact_reference(
        run_id, path, uri, "application/json", "raw-result/v2"
    )
    return artifact_upload.Source("raw-result", path, reference)


def receipt(sources: Sequence[artifact_upload.Source]) -> dict[str, Any]:
    """Describe the single verified discovery object."""
    if len(sources) != 1:
        raise ValueError("Raw-result publication requires exactly one source")
    return {
        "schema": "local-raw-result-upload/v1",
        "runId": sources[0].reference["runId"],
        "artifact": sources[0].reference,
    }


def execute(
    run_id: str,
    raw_source: artifact_upload.Source,
    receipt_path: Path,
    root: Path = cluster.ROOT,
) -> dict[str, Any]:
    """Publish one manifest and exclusively write its verified reference."""
    return artifact_upload.publish(
        run_id,
        [raw_source],
        receipt_path,
        receipt,
        pod_prefix="raw-result-upload",
        subject="raw-result",
        root=root,
    )


def parser() -> argparse.ArgumentParser:
    """Build the preview-first command-line interface."""
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("raw_result", type=Path)
    value.add_argument("--receipt", type=Path, required=True)
    value.add_argument("--execute", action="store_true")
    return value


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate locally by default and publish only when explicitly requested."""
    args = parser().parse_args(arguments)
    try:
        run_id, path = manifest_input(args.raw_result)
        raw_source = source(run_id, path)
        if args.execute:
            result = execute(run_id, raw_source, args.receipt)
            print(json.dumps(result))
        else:
            print(
                json.dumps(
                    artifact_upload.upload_pod("raw-result-upload-preview", cluster.ROOT),
                    indent=2,
                )
            )
            print(f"Preview: publish raw-result/v2 for {run_id} and write {args.receipt}.")
            print("Preview only. Add --execute to create the uploader Pod and immutable object.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        print(f"Raw-result upload error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
