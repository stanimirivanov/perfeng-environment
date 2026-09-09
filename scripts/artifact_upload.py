"""Publish immutable local artifacts through the restricted S3 identity."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster, storage_access
    from . import object_store as store
else:
    import cluster
    import object_store as store
    import storage_access


@dataclass(frozen=True)
class Source:
    """One validated local file and its immutable remote claim."""

    label: str
    path: Path
    reference: dict[str, Any]


def artifact_reference(
    run_id: str,
    path: Path,
    uri: str,
    media_type: str,
    artifact_format: str,
) -> dict[str, Any]:
    """Describe the exact bytes of one caller-validated source file."""
    if (
        re.fullmatch(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}", run_id) is None
        or re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", media_type) is None
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", artifact_format) is None
        or not uri.startswith(f"s3://{store.BUCKET}/runs/{run_id}/")
        or re.fullmatch(r"[A-Za-z0-9._:/-]+", uri) is None
    ):
        raise ValueError("Artifact reference metadata is unsafe or outside the run prefix")
    content = path.read_bytes()
    return {
        "id": str(uuid.uuid4()),
        "runId": run_id,
        "kind": "raw",
        "uri": uri,
        "sha256": hashlib.sha256(content).hexdigest(),
        "sizeBytes": len(content),
        "mediaType": media_type,
        "format": artifact_format,
    }


def upload_pod(
    name: str,
    root: Path,
    *,
    credential_secret: str = storage_access.UPLOADER_SECRET,
) -> dict[str, Any]:
    """Build a bounded transfer Pod using the selected S3 credentials."""
    if re.fullmatch(r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?", credential_secret) is None:
        raise ValueError("S3 credential Secret name is invalid")
    values = yaml.safe_load((root / "charts/seaweedfs/values.yaml").read_text(encoding="utf-8"))
    image = values["clientImage"]
    if (
        not isinstance(image, str)
        or re.fullmatch(r"amazon/aws-cli:[0-9.]+@sha256:[0-9a-f]{64}", image) is None
    ):
        raise ValueError("S3 client image must use the pinned official AWS CLI repository")
    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": name,
            "namespace": store.NAMESPACE,
            "labels": {"app.kubernetes.io/managed-by": store.OWNER},
        },
        "spec": {
            "restartPolicy": "Never",
            "automountServiceAccountToken": False,
            "activeDeadlineSeconds": 600,
            "nodeSelector": {"workload": "control-plane"},
            "tolerations": [
                {
                    "key": "node-role.kubernetes.io/control-plane",
                    "operator": "Exists",
                    "effect": "NoSchedule",
                }
            ],
            "securityContext": {
                "runAsNonRoot": True,
                "runAsUser": 1000,
                "runAsGroup": 1000,
                "fsGroup": 1000,
                "seccompProfile": {"type": "RuntimeDefault"},
            },
            "containers": [
                {
                    "name": "upload",
                    "image": image,
                    "command": ["/bin/sh", "-c", "sleep 600"],
                    "env": [
                        {
                            "name": "AWS_ACCESS_KEY_ID",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": credential_secret,
                                    "key": "access-key",
                                }
                            },
                        },
                        {
                            "name": "AWS_SECRET_ACCESS_KEY",
                            "valueFrom": {
                                "secretKeyRef": {
                                    "name": credential_secret,
                                    "key": "secret-key",
                                }
                            },
                        },
                        {"name": "HOME", "value": "/tmp"},
                        {"name": "AWS_CONFIG_FILE", "value": "/tmp/awsconfig"},
                        {"name": "AWS_DEFAULT_REGION", "value": "us-east-1"},
                        {"name": "AWS_EC2_METADATA_DISABLED", "value": "true"},
                        {"name": "AWS_PAGER", "value": ""},
                        {"name": "AWS_MAX_ATTEMPTS", "value": "2"},
                    ],
                    "securityContext": {
                        "readOnlyRootFilesystem": True,
                        "allowPrivilegeEscalation": False,
                        "capabilities": {"drop": ["ALL"]},
                    },
                    "resources": {
                        "requests": {"cpu": "100m", "memory": "128Mi"},
                        "limits": {"cpu": "500m", "memory": "256Mi"},
                    },
                    "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                }
            ],
            "volumes": [{"name": "tmp", "emptyDir": {}}],
        },
    }


def upload_script(reference: dict[str, Any]) -> str:
    """Build an upload-and-read-back script for one generated reference."""
    key = reference["uri"].split(f"s3://{store.BUCKET}/", 1)[1]
    return f"""set -eu
printf '[default]\\ns3 =\\n    addressing_style = path\\n' > /tmp/awsconfig
aws_s3() {{ aws --endpoint-url "{store.ENDPOINT}" --cli-connect-timeout 10 --cli-read-timeout 60 s3api "$@"; }}
aws_s3 put-object --bucket "{store.BUCKET}" --key "{key}" --body /tmp/source --if-none-match "*" --content-type "{reference["mediaType"]}" --metadata "artifact-id={reference["id"]},sha256={reference["sha256"]},format={reference["format"]}" >/dev/null
aws_s3 get-object --bucket "{store.BUCKET}" --key "{key}" /tmp/download >/dev/null
printf '{reference["sha256"]}  /tmp/download\\n' | sha256sum -c - >/dev/null
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{key}" --query ContentLength --output text)" = "{reference["sizeBytes"]}"
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{key}" --query ContentType --output text)" = "{reference["mediaType"]}"
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{key}" --query 'Metadata.\"artifact-id\"' --output text)" = "{reference["id"]}"
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{key}" --query Metadata.sha256 --output text)" = "{reference["sha256"]}"
test "$(aws_s3 head-object --bucket "{store.BUCKET}" --key "{key}" --query Metadata.format --output text)" = "{reference["format"]}"
rm -f /tmp/source /tmp/download
"""


def source_bytes(run_id: str, source: Source) -> bytes:
    """Verify that a source still matches its immutable reference."""
    content = source.path.read_bytes()
    reference = source.reference
    if (
        reference.get("runId") != run_id
        or reference.get("kind") != "raw"
        or reference.get("sha256") != hashlib.sha256(content).hexdigest()
        or reference.get("sizeBytes") != len(content)
    ):
        raise ValueError(f"Artifact source {source.label} changed after reference creation")
    return content


def publish(
    run_id: str,
    sources: Sequence[Source],
    receipt_path: Path,
    receipt_builder: Callable[[Sequence[Source]], dict[str, Any]],
    *,
    pod_prefix: str,
    subject: str,
    root: Path = cluster.ROOT,
) -> dict[str, Any]:
    """Publish validated sources and exclusively write their caller-owned receipt."""
    if not sources:
        raise ValueError("At least one artifact source is required")
    if re.fullmatch(r"[a-z0-9](?:[-a-z0-9]{0,28}[a-z0-9])?", pod_prefix) is None:
        raise ValueError("Uploader Pod prefix is invalid")
    if receipt_path.is_symlink():
        raise ValueError("Receipt path must not be a symbolic link")
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = receipt_path.open("xb")
    completed = False
    pod_name = f"{pod_prefix}-{uuid.uuid4().hex[:16]}"
    try:
        if len({source.reference.get("id") for source in sources}) != len(sources) or len(
            {source.reference.get("uri") for source in sources}
        ) != len(sources):
            raise ValueError("Artifact source identities and locations must be distinct")
        for source in sources:
            source_bytes(run_id, source)
        document = upload_pod(pod_name, root)
        cluster.verify_local_docker()
        cluster.verify_context(root)
        storage_access.ensure_credentials(root, create=False)
        kube = store.kubectl(root)
        cluster.run([*kube, "rollout", "status", "statefulset/seaweedfs", "--timeout=300s"])
        cluster.run([*kube, "create", "-f", "-"], input_data=json.dumps(document).encode("utf-8"))
        cluster.run([*kube, "wait", "--for=condition=Ready", f"pod/{pod_name}", "--timeout=120s"])
        print(f"Publishing {len(sources)} {subject} artifacts with Pod {pod_name}...", flush=True)
        for source in sources:
            content = source_bytes(run_id, source)
            print(
                f"Uploading {source.label} ({source.reference['sizeBytes']} bytes)...",
                flush=True,
            )
            cluster.run(
                [*kube, "exec", "-i", pod_name, "--", "sh", "-c", "umask 077; cat > /tmp/source"],
                input_data=content,
            )
            cluster.run(
                [*kube, "exec", pod_name, "--", "sh", "-c", upload_script(source.reference)]
            )
        result = receipt_builder(sources)
        receipt.write((json.dumps(result, indent=2) + "\n").encode("utf-8"))
        receipt.close()
        completed = True
        try:
            cluster.run([*kube, "delete", "pod", pod_name, "--wait=true", "--timeout=60s"])
        except ValueError:
            print(f"Artifacts are verified; remove retained Pod {pod_name} manually.", flush=True)
        print(f"Published and verified {len(sources)} {subject} artifacts.", flush=True)
        return result
    finally:
        if not receipt.closed:
            receipt.close()
        if not completed:
            receipt_path.unlink(missing_ok=True)
            print(f"Upload did not complete; inspect Pod {pod_name} if it was created.", flush=True)
