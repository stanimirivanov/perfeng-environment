"""Inspect an approved contract distribution entirely from local inputs."""

import argparse
import gzip
import hashlib
import io
import json
import posixpath
import re
import subprocess
import sys
import tarfile
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from jsonschema import Draft202012Validator

from scripts.contracts_distribution_tools import (
    checked_bytes,
    local_bytes,
    stage_verifier,
    verify_attestation,
)
from scripts.kubernetes_benchmark_contract import finite_float, invalid_number
from scripts.workspace import unique_object

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts/distribution-manifest.schema.json"
SCHEMA_SHA256 = "4501e3a7840f1ba3c4130cf8fac515973f9cac0d5319529d2d83348054fa8d8d"
REPOSITORY = "stanimirivanov/perfeng-contracts"
MEMBER_URI = "https://perfeng.dev/"
METADATA_LIMIT = 4 * 1024 * 1024
ARCHIVE_LIMIT = 72 * 1024 * 1024
TAR_LIMIT = 80 * 1024 * 1024
TOTAL_LIMIT = 64 * 1024 * 1024
MEMBER_LIMIT = 16 * 1024 * 1024
MEMBER_COUNT = 4096


def json_data(content: bytes) -> Any:
    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=invalid_number,
            parse_float=finite_float,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError("Invalid distribution JSON") from exc


def publisher_policy(path: Path) -> dict[str, str]:
    """Read explicit reviewed consumer-side pins separate from release metadata."""
    policy = json_data(local_bytes(path, limit=METADATA_LIMIT))
    patterns = {
        "manifestSha256": r"[a-f0-9]{64}",
        "sourceDigest": r"[a-f0-9]{40}",
        "sourceRef": r"refs/heads/main",
        "signerWorkflow": rf"{re.escape(REPOSITORY)}/\.github/workflows/[A-Za-z0-9_.-]+\.ya?ml",
        "signerDigest": r"[a-f0-9]{40}",
    }
    if (
        not isinstance(policy, dict)
        or policy.keys() != patterns.keys()
        or any(
            not isinstance(policy[key], str) or not re.fullmatch(pattern, policy[key])
            for key, pattern in patterns.items()
        )
    ):
        raise ValueError("Invalid independent publisher policy")
    return policy


def verify_publisher(
    verifier: Path, manifest: bytes, bundle: bytes, policy: dict[str, str]
) -> None:
    with tempfile.TemporaryDirectory(prefix="perfeng-contract-inspect-") as tmp:
        directory = Path(tmp)
        executable, trust_root = stage_verifier(verifier, directory)
        artifact_path, bundle_path = directory / "manifest.json", directory / "bundle.jsonl"
        artifact_path.write_bytes(manifest)
        bundle_path.write_bytes(bundle)
        if not verify_attestation(
            executable, trust_root, artifact_path, bundle_path, {**policy, "repository": REPOSITORY}
        ):
            raise ValueError("Offline publisher attestation failed")


def validate_members(members: dict[str, str]) -> None:
    paths = {path.lower() for path in members}
    if (
        len(members) > MEMBER_COUNT
        or len(paths) != len(members)
        or any(str(parent) in paths for name in paths for parent in Path(name).parents)
    ):
        raise ValueError("Manifest contains conflicting or excessive member paths")


def archive_members(compressed: bytes, expected: dict[str, str]) -> dict[str, bytes]:
    validate_members(expected)
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
            expanded = stream.read(TAR_LIMIT + 1)
        if len(expanded) > TAR_LIMIT:
            raise ValueError("Archive exceeds expanded size limit")
        contents: dict[str, bytes] = {}
        total = 0
        with tarfile.open(fileobj=io.BytesIO(expanded), mode="r:") as archive:
            for entry in archive:
                name = entry.name
                if (
                    entry.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                    or entry.sparse is not None
                ):
                    raise ValueError("Archive entries must be regular files")
                if name not in expected:
                    raise ValueError("Archive contains undeclared member")
                if name in contents:
                    raise ValueError("Archive contains duplicate member")
                total += entry.size
                if not 0 <= entry.size <= MEMBER_LIMIT or total > TOTAL_LIMIT:
                    raise ValueError("Archive member size exceeds limits")
                stream = archive.extractfile(entry)
                if stream is None:
                    raise ValueError("Archive member content is unavailable")
                with stream:
                    content = stream.read()
                if (
                    len(content) != entry.size
                    or hashlib.sha256(content).hexdigest() != expected[name]
                ):
                    raise ValueError("Archive member digest mismatch")
                contents[name] = content
            if contents.keys() != expected.keys():
                raise ValueError("Archive is missing declared members")
            trailer = expanded[archive.offset :]
            if len(trailer) < 1024 or len(trailer) % 512 or any(trailer):
                raise ValueError("Invalid archive trailing data or end markers")
        return contents
    except (OSError, EOFError, tarfile.TarError, RecursionError, zlib.error) as exc:
        raise ValueError("Invalid or truncated distribution archive") from exc


def resolve_reference(path: str, ref: object, documents: dict[str, Any]) -> None:
    if not isinstance(ref, str) or any(char.isspace() or ord(char) < 32 for char in ref):
        raise ValueError("Invalid JSON reference")
    location = ref.partition("#")[0]
    uri = urlsplit(ref)
    if "?" in location or any(char in uri.path for char in ("%", "\\")):
        raise ValueError("External or noncanonical JSON reference")
    if uri.scheme or uri.netloc or uri.path.startswith("/"):
        target = uri.path.removeprefix("/")
        if location != MEMBER_URI + target or target != posixpath.normpath(target):
            raise ValueError("External or noncanonical JSON reference")
        document = documents.get(target)
        if not isinstance(document, dict) or document.get("$id") != location:
            raise ValueError("Undeclared JSON reference target")
    else:
        target = (
            posixpath.normpath(posixpath.join(posixpath.dirname(path), uri.path))
            if uri.path
            else path
        )
        if target not in documents:
            raise ValueError("Undeclared JSON reference target")
    node = documents[target]
    pointer = unquote(uri.fragment, errors="strict")
    if pointer and not pointer.startswith("/"):
        raise ValueError("JSON reference must use a JSON Pointer")
    for token in pointer.split("/")[1:]:
        if re.search(r"~(?![01])", token):
            raise ValueError("Invalid JSON reference escape")
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict) and key in node:
            node = node[key]
        elif (
            isinstance(node, list)
            and re.fullmatch(r"0|[1-9][0-9]{0,7}", key)
            and int(key) < len(node)
        ):
            node = node[int(key)]
        else:
            raise ValueError("Unresolved JSON reference pointer")


def check_references(documents: dict[str, Any]) -> None:
    """Check references against the authenticated archive without fetching URIs."""
    for path, root in documents.items():
        if isinstance(root, dict) and "$id" in root and root["$id"] != MEMBER_URI + path:
            raise ValueError("Root resource identifier does not match the member URI")
        pending = [root]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                if node is not root and "$id" in node:
                    raise ValueError(
                        "Nested resource identifiers are unsupported for JSON references"
                    )
                for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                    if key in node:
                        resolve_reference(path, node[key], documents)
                pending.extend(node.values())
            elif isinstance(node, list):
                pending.extend(node)


@dataclass(frozen=True)
class VerifiedArchive:
    """Exact member bytes captured during authenticated inspection."""

    source_revision: str
    members: dict[str, bytes]


def inspect_verified(
    manifest_path: Path, archive_path: Path, bundle_path: Path, policy_path: Path, verifier: Path
) -> VerifiedArchive:
    """Capture the verified bytes for a caller without reopening any input."""
    policy = publisher_policy(policy_path)
    manifest_bytes = checked_bytes(manifest_path, policy["manifestSha256"], limit=METADATA_LIMIT)
    manifest = json_data(manifest_bytes)
    schema = json_data(checked_bytes(SCHEMA, SCHEMA_SHA256, limit=METADATA_LIMIT))
    if not Draft202012Validator(schema).is_valid(manifest):
        raise ValueError("Invalid distribution manifest")
    if manifest["kind"] != "promoted":
        raise ValueError("Inspection for adoption requires a promoted manifest")
    bundle = local_bytes(bundle_path, limit=METADATA_LIMIT)
    verify_publisher(verifier, manifest_bytes, bundle, policy)
    compressed = checked_bytes(archive_path, manifest["archiveSha256"], limit=ARCHIVE_LIMIT)
    members: dict[str, str] = manifest["members"]
    contents = archive_members(compressed, members)
    check_references(
        {name: json_data(data) for name, data in contents.items() if name.endswith(".json")}
    )
    return VerifiedArchive(manifest["source"]["revision"], contents)


def inspect(
    manifest_path: Path, archive_path: Path, bundle_path: Path, policy_path: Path, verifier: Path
) -> dict[str, str]:
    """Report only the verified member digests."""
    result = inspect_verified(manifest_path, archive_path, bundle_path, policy_path, verifier)
    return {name: hashlib.sha256(data).hexdigest() for name, data in sorted(result.members.items())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "archive", "bundle", "policy", "verifier"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = inspect(args.manifest, args.archive, args.bundle, args.policy, args.verifier)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"contracts-inspect: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
