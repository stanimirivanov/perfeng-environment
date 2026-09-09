"""Preview and publish exact reviewed policy bytes to local SeaweedFS."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Never

import yaml

if __package__:
    from . import artifact_upload, cluster
    from . import object_store as store
else:
    import artifact_upload
    import cluster
    import object_store as store

MAXIMUM_DOCUMENT_BYTES = 1 << 20
IDENTITY_PATTERN = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
VERSION_PATTERN = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")
HASH_PATTERN = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class PolicySource:
    """Exact bytes and reviewed bundle claims for one policy version."""

    content: bytes
    bundle_policy_path: str
    contracts_version: str
    bundle_manifest_sha256: str
    policy_id: str
    version: str
    mode: str

    @property
    def sha256(self) -> str:
        """Return the digest of the frozen source bytes."""
        return hashlib.sha256(self.content).hexdigest()

    @property
    def key(self) -> str:
        """Return the reporting template's stable policy object key."""
        return f"policies/{self.policy_id}/{self.version}/performance-policy.json"


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting duplicate member names."""
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON contains duplicate field {key!r}")
        value[key] = item
    return value


def reject_non_finite(constant: str) -> Never:
    """Reject JSON numeric extensions that are not valid contract numbers."""
    raise ValueError(f"JSON contains non-finite number {constant}")


def read_document(path: Path, label: str) -> tuple[bytes, dict[str, Any]]:
    """Read one bounded, regular, strict UTF-8 JSON object."""
    if path.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link")
    try:
        resolved = path.resolve(strict=True)
        if resolved != path or not resolved.is_file():
            raise ValueError(f"{label} must be a regular file inside the bundle")
        content = resolved.read_bytes()
        if not content or len(content) > MAXIMUM_DOCUMENT_BYTES:
            raise ValueError(f"{label} must contain 1 through {MAXIMUM_DOCUMENT_BYTES} bytes")
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_constant=reject_non_finite,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{label} must be readable strict UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return content, value


def bundle_file(root: Path, relative: str) -> Path:
    """Resolve one portable bundle-relative regular file without following links."""
    path = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or path.is_absolute()
        or path == PurePosixPath(".")
        or ".." in path.parts
    ):
        raise ValueError("Registry policy path is invalid")
    candidate = root.joinpath(*path.parts)
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError):
        raise ValueError("Registry policy path leaves the bundle or does not exist") from None
    if resolved != candidate or candidate.is_symlink() or not candidate.is_file():
        raise ValueError("Registry policy path must identify an unredirected regular file")
    return candidate


def policy_identity(document: dict[str, Any]) -> tuple[str, str, str]:
    """Read the identity needed for publication after bundle validation."""
    metadata = document.get("metadata")
    spec = document.get("spec")
    if (
        document.get("apiVersion") != "performance.perfeng.io/v1"
        or document.get("kind") != "PerformancePolicy"
        or not isinstance(metadata, dict)
        or not isinstance(spec, dict)
    ):
        raise ValueError("Bundle policy is not a PerformancePolicy v1 object")
    policy_id = metadata.get("name")
    version = metadata.get("version")
    mode = spec.get("mode")
    if (
        not isinstance(policy_id, str)
        or IDENTITY_PATTERN.fullmatch(policy_id) is None
        or not isinstance(version, str)
        or VERSION_PATTERN.fullmatch(version) is None
        or mode not in {"observe", "inform"}
    ):
        raise ValueError("Bundle policy has invalid publication identity")
    return policy_id, version, mode


def resolve_source(bundle_root: Path, policy_id: str, version: str) -> PolicySource:
    """Resolve one unambiguous policy declared by a reviewed runtime bundle."""
    if IDENTITY_PATTERN.fullmatch(policy_id) is None or VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError("Policy identity and version are invalid")
    if bundle_root.is_symlink():
        raise ValueError("Bundle root must not be a symbolic link")
    try:
        root = bundle_root.resolve(strict=True)
    except OSError as error:
        raise ValueError("Bundle root does not exist") from error
    if not root.is_dir():
        raise ValueError("Bundle root must be a directory")
    manifest_content, manifest = read_document(root / "registry.json", "Bundle registry")
    entries = manifest.get("entries")
    if manifest.get("schemaVersion") != 1 or not isinstance(entries, list) or not entries:
        raise ValueError("Bundle registry must contain version-one entries")

    matches: list[PolicySource] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Bundle registry entries must be objects")
        relative = entry.get("policy")
        contracts_version = entry.get("contractsVersion")
        if not isinstance(relative, str) or not isinstance(contracts_version, str):
            raise ValueError("Bundle entry must declare policy and contractsVersion")
        path = bundle_file(root, relative)
        content, document = read_document(path, f"Bundle policy {relative}")
        identity = policy_identity(document)
        if identity[:2] == (policy_id, version):
            matches.append(
                PolicySource(
                    content=content,
                    bundle_policy_path=relative,
                    contracts_version=contracts_version,
                    bundle_manifest_sha256=hashlib.sha256(manifest_content).hexdigest(),
                    policy_id=identity[0],
                    version=identity[1],
                    mode=identity[2],
                )
            )
    if len(matches) != 1:
        raise ValueError("Bundle must declare exactly one matching policy identity and version")
    return matches[0]


def receipt(source: PolicySource) -> dict[str, Any]:
    """Describe the exact reviewed policy object and its bundle provenance."""
    return {
        "schema": "local-policy-publication/v1",
        "contractsVersion": source.contracts_version,
        "bundleManifestSha256": source.bundle_manifest_sha256,
        "bundlePolicyPath": source.bundle_policy_path,
        "policy": {
            "id": source.policy_id,
            "version": source.version,
            "mode": source.mode,
            "uri": f"s3://{store.BUCKET}/{source.key}",
            "sha256": source.sha256,
            "sizeBytes": len(source.content),
            "mediaType": "application/json",
            "format": "policy/v1",
        },
    }


def upload_script(source: PolicySource) -> str:
    """Build the conditional policy upload and complete read-back verification."""
    return f"""set -eu
printf '[default]\\ns3 =\\n    addressing_style = path\\n' > /tmp/awsconfig
aws_s3() {{ aws --endpoint-url "{store.ENDPOINT}" --cli-connect-timeout 10 --cli-read-timeout 60 s3api "$@"; }}
aws_s3 put-object --bucket "{store.BUCKET}" --key "{source.key}" --body /tmp/source --if-none-match "*" --content-type "application/json" --metadata "policy-id={source.policy_id},policy-version={source.version},sha256={source.sha256},format=policy/v1" >/dev/null
aws_s3 get-object --bucket "{store.BUCKET}" --key "{source.key}" /tmp/download >/dev/null
printf '{source.sha256}  /tmp/download\\n' | sha256sum -c - >/dev/null
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{source.key}" --query ContentLength --output text)" = "{len(source.content)}"
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{source.key}" --query ContentType --output text)" = "application/json"
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{source.key}" --query 'Metadata.\"policy-id\"' --output text)" = "{source.policy_id}"
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{source.key}" --query 'Metadata.\"policy-version\"' --output text)" = "{source.version}"
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{source.key}" --query Metadata.sha256 --output text)" = "{source.sha256}"
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{source.key}" --query Metadata.format --output text)" = "policy/v1"
rm -f /tmp/source /tmp/download
"""


def execute(source: PolicySource, receipt_path: Path, root: Path = cluster.ROOT) -> dict[str, Any]:
    """Create one policy object and exclusively write its verified receipt."""
    if receipt_path.is_symlink():
        raise ValueError("Receipt path must not be a symbolic link")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    output = receipt_path.open("xb")
    completed = False
    created = False
    pod_name = f"policy-publish-{uuid.uuid4().hex[:16]}"
    try:
        document = artifact_upload.upload_pod(pod_name, root, credential_secret=store.SECRET)
        cluster.verify_local_docker()
        cluster.verify_context(root)
        store.ensure_secret(root, create=False)
        kube = store.kubectl(root)
        cluster.run([*kube, "rollout", "status", "statefulset/seaweedfs", "--timeout=300s"])
        cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(document).encode())
        created = True
        cluster.run([*kube, "wait", "--for=condition=Ready", f"pod/{pod_name}", "--timeout=120s"])
        print(
            f"Publishing policy {source.policy_id} {source.version} with Pod {pod_name}...",
            flush=True,
        )
        cluster.run(
            [*kube, "exec", "-i", pod_name, "--", "sh", "-c", "umask 077; cat > /tmp/source"],
            input_data=source.content,
        )
        cluster.run([*kube, "exec", pod_name, "--", "sh", "-c", upload_script(source)])
        result = receipt(source)
        output.write((json.dumps(result, indent=2) + "\n").encode())
        output.close()
        completed = True
        try:
            cluster.run([*kube, "delete", "pod", pod_name, "--wait=true", "--timeout=60s"])
        except ValueError:
            print(f"Policy is verified; remove retained Pod {pod_name} manually.", flush=True)
        print("Published and verified the immutable policy object.", flush=True)
        return result
    finally:
        if not output.closed:
            output.close()
        if not completed:
            receipt_path.unlink(missing_ok=True)
            if created:
                print(f"Publication did not complete; inspect Pod {pod_name}.", flush=True)


def parser() -> argparse.ArgumentParser:
    """Build the preview-first policy publication interface."""
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("bundle_root", type=Path)
    value.add_argument("--policy-id", required=True)
    value.add_argument("--version", required=True)
    value.add_argument("--expected-sha256")
    value.add_argument("--receipt", type=Path, required=True)
    value.add_argument("--execute", action="store_true")
    return value


def main(arguments: Sequence[str] | None = None) -> int:
    """Validate and preview by default; publish only with an exact hash and --execute."""
    args = parser().parse_args(arguments)
    try:
        source = resolve_source(args.bundle_root, args.policy_id, args.version)
        if args.expected_sha256 is not None and (
            HASH_PATTERN.fullmatch(args.expected_sha256) is None
            or args.expected_sha256 != source.sha256
        ):
            raise ValueError("Expected policy SHA-256 does not match the reviewed bytes")
        if args.execute:
            if args.expected_sha256 is None:
                raise ValueError("Execution requires --expected-sha256 from a reviewed preview")
            print(json.dumps(execute(source, args.receipt)))
        else:
            print(json.dumps(receipt(source), indent=2))
            print(
                json.dumps(
                    artifact_upload.upload_pod(
                        "policy-publish-preview",
                        cluster.ROOT,
                        credential_secret=store.SECRET,
                    ),
                    indent=2,
                )
            )
            print(f"Preview only. Review SHA-256 {source.sha256} before publication.")
            print("Add the matching --expected-sha256 and --execute to publish once.")
        return 0
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        print(f"Policy publication error: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
