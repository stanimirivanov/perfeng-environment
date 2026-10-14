"""Validate a locally pinned, offline Kubernetes benchmark evidence schema."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Never

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry

from scripts.control_plane_release import revision_reader
from scripts.workspace import unique_object

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/stanimirivanov/perfeng-contracts.git"
REVISION = "3b86afa18b468d831c7c2c176618180db18b2764"
SOURCE = "schemas/kubernetes-benchmark-evidence/v1/kubernetes-benchmark-evidence.schema.json"
DIGEST = "aae5daa05c2be154c67227bddde99da3f4d9ad9c5b406c10395140d4d65737a1"


def invalid_number(value: str) -> Never:
    """Refuse non-JSON numeric constants without exposing input payloads."""
    raise ValueError("Non-finite JSON numbers are not allowed")


def finite_float(value: str) -> float:
    """Reject float overflow rather than admitting infinite thresholds or samples."""
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("Non-finite JSON numbers are not allowed")
    return result


def document(content: bytes) -> dict[str, Any]:
    """Decode one bounded UTF-8 object; reject duplicate keys and non-finite numbers."""
    if len(content) > 8 * 1024 * 1024:
        raise ValueError("JSON observation exceeds the 8 MiB limit")
    value = json.loads(
        content,
        object_pairs_hook=unique_object,
        parse_constant=invalid_number,
        parse_float=finite_float,
    )
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON object")
    return value


def schema(root: Path = ROOT) -> dict[str, Any]:
    """Check the local snapshot and lock identity before using any schema keywords."""
    lock = document((root / "contracts/kubernetes-benchmark-lock.json").read_bytes())
    expected = {
        "schemaVersion": 1,
        "repository": REPOSITORY,
        "revision": REVISION,
        "contractsVersion": "0.22.0",
        "sourcePath": SOURCE,
        "sha256": DIGEST,
    }
    if lock != expected or type(lock.get("schemaVersion")) is not int:
        raise ValueError("Kubernetes benchmark contract lock differs from the reviewed selection")
    content = (root / "contracts/kubernetes-benchmark-evidence.schema.json").read_bytes()
    if hashlib.sha256(content).hexdigest() != DIGEST:
        raise ValueError("Kubernetes benchmark schema checksum differs from the contract lock")
    result = document(content)
    if result.get("$id") != f"https://perfeng.dev/{SOURCE}":
        raise ValueError("Kubernetes benchmark schema has unexpected identity")
    Draft202012Validator.check_schema(result)
    return result


def validate(evidence: dict[str, Any], root: Path = ROOT) -> None:
    """Validate exact contract structure with remote schema resolution disabled."""

    def no_remote(uri: str) -> Never:
        raise ValueError("Remote schema resolution is forbidden")

    formats = FormatChecker()

    @formats.checks("date-time")
    def date_time(value: object) -> bool:
        if not isinstance(value, str):
            return True
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
        except ValueError:
            return False

    validator = Draft202012Validator(
        schema(root), registry=Registry(retrieve=no_remote), format_checker=formats
    )
    if next(iter(validator.iter_errors(evidence)), None) is not None:
        raise ValueError("Generated Kubernetes evidence violates the pinned contract")


def validate_source(contracts_root: Path, root: Path = ROOT) -> None:
    """Compare the vendored bytes with the exact reviewed Git revision, without network access."""
    schema(root)
    read = revision_reader(contracts_root, REPOSITORY, REVISION)
    if read(SOURCE) != (root / "contracts/kubernetes-benchmark-evidence.schema.json").read_bytes():
        raise ValueError("Kubernetes benchmark snapshot differs from its pinned source")
