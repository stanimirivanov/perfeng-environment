"""Restricted in-cluster API/storage transport; credentials never enter host memory."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from scripts import artifact_upload, cluster, control_plane_smoke, postgres
from scripts.kubernetes_acceptance import api_client
from scripts.kubernetes_acceptance.errors import AcceptanceError
from scripts.kubernetes_benchmark_contract import document

POD = re.compile(r"m04-acceptance-[a-f0-9]{16}")
RUN = re.compile(r"perf-[0-9]{8}-[0-9]{6}-[a-f0-9]{8}")
READ_OBJECT = r"""set -eu
umask 077
printf '[default]\ns3 =\n    addressing_style = path\n' > /tmp/awsconfig
aws_s3() { aws --endpoint-url http://seaweedfs.perf-platform.svc.cluster.local:8333 --cli-connect-timeout 5 --cli-read-timeout 15 s3api "$@"; }
aws_s3 head-object --bucket perfeng-artifacts --key "$1" --query '{sizeBytes:ContentLength,mediaType:ContentType,metadata:Metadata}' --output json
aws_s3 get-object --bucket perfeng-artifacts --key "$1" --range "bytes=0-$2" /tmp/evidence >/dev/null
base64 /tmp/evidence
"""


def client_pod(name: str, root: Path) -> dict[str, Any]:
    """Compose existing pinned clients without Kubernetes tokens or host credentials."""
    if POD.fullmatch(name) is None:
        raise AcceptanceError("Invalid acceptance client identity")
    pod = control_plane_smoke.client_pod(
        "control-plane-smoke-" + name[-16:], {}, "smoke-" + "0" * 32
    )
    pod["metadata"]["name"] = name
    pod["metadata"]["labels"]["perfeng.io/purpose"] = "m04-acceptance"
    spec = pod["spec"]
    spec["activeDeadlineSeconds"] = 3600
    api = spec["containers"][0]
    api["command"] = ["python", "-c", "import time; time.sleep(3500)"]
    api["env"] = [
        item
        for item in api["env"]
        if item["name"] in {"PERFENG_OPERATOR_TOKEN", "HOME", "PYTHONDONTWRITEBYTECODE"}
    ]
    reader = copy.deepcopy(artifact_upload.upload_pod(name, root)["spec"]["containers"][0])
    reader["name"] = "evidence-reader"
    reader["command"] = ["/bin/sh", "-c", "sleep 3500"]
    reader["volumeMounts"] = [{"name": "reader-tmp", "mountPath": "/tmp"}]
    spec["containers"].append(reader)
    spec["volumes"].append({"name": "reader-tmp", "emptyDir": {"sizeLimit": "16Mi"}})
    return pod


def reference(value: dict[str, Any], run_id: str) -> str:
    """Validate a stable, bounded, run-scoped object reference before passing it to AWS CLI."""
    uri = value.get("uri")
    prefix = f"s3://perfeng-artifacts/runs/{run_id}/"
    if (
        RUN.fullmatch(run_id) is None
        or value.get("runId") != run_id
        or not isinstance(uri, str)
        or not uri.startswith(prefix)
        or re.fullmatch(r"[A-Za-z0-9._/-]+", uri[len("s3://") :]) is None
        or any(part in {"", ".", ".."} for part in uri[len("s3://") :].split("/"))
        or type(value.get("sizeBytes")) is not int
        or not 0 < value["sizeBytes"] <= 8 << 20
        or not isinstance(value.get("sha256"), str)
        or re.fullmatch(r"[a-f0-9]{64}", value["sha256"]) is None
    ):
        raise AcceptanceError("Invalid or foreign acceptance artifact reference")
    UUID(value["id"])
    return uri.removeprefix("s3://perfeng-artifacts/")


class Session:
    """One retained, UID-bound client Pod with independently scoped container credentials."""

    def __init__(self, root: Path, name: str) -> None:
        """Build the expected client specification without contacting Kubernetes."""
        self.root = root
        self.name = name
        self.expected = client_pod(name, root)
        self.uid: str | None = None
        self.kube = postgres.kubectl(root)

    def create(self) -> None:
        """Create once and wait boundedly; retain partial state instead of deleting it."""
        actual = document(
            cluster.run(
                [*self.kube, "create", "-f", "-", "-o", "json"],
                input_data=json.dumps(self.expected).encode(),
            ).encode()
        )
        self.uid = str(UUID(actual["metadata"]["uid"]))
        cluster.run(
            [*self.kube, "wait", "--for=condition=Ready", f"pod/{self.name}", "--timeout=180s"]
        )
        self.verify()

    def verify(self) -> None:
        """Reject replacement, changed images/credentials/commands and foreign clients before exec."""
        actual = document(cluster.run([*self.kube, "get", "pod", self.name, "-o", "json"]).encode())
        meta, spec = actual["metadata"], actual["spec"]
        if (
            self.uid is None
            or meta.get("uid") != self.uid
            or meta.get("name") != self.name
            or meta.get("namespace") != postgres.NAMESPACE
            or meta.get("labels") != self.expected["metadata"]["labels"]
            or meta.get("deletionTimestamp") is not None
            or meta.get("ownerReferences")
            or actual.get("status", {}).get("phase") != "Running"
            or spec.get("automountServiceAccountToken") is not False
            or len(spec.get("containers", [])) != 2
            or spec.get("ephemeralContainers")
            or spec.get("initContainers")
            or any(
                spec.get(key, False)
                for key in ("hostNetwork", "hostPID", "hostIPC", "shareProcessNamespace")
            )
        ):
            raise AcceptanceError("Acceptance client identity changed; do not exec or replace it")
        for container, expected in zip(
            spec["containers"], self.expected["spec"]["containers"], strict=True
        ):
            if (
                any(container.get(key) != expected[key] for key in expected)
                or container.get("envFrom")
                or container.get("args")
            ):
                raise AcceptanceError("Acceptance client container differs from reviewed inputs")
        for key in (
            "volumes",
            "nodeSelector",
            "securityContext",
            "activeDeadlineSeconds",
            "enableServiceLinks",
        ):
            if spec.get(key) != self.expected["spec"][key]:
                raise AcceptanceError("Acceptance client isolation differs")

    def call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        *,
        key: str | None = None,
        statuses: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        """Send exactly one API operation and expose only classified failure information."""
        self.verify()
        print(f"M04 API: {method} {path}", flush=True)
        output = cluster.run(
            [
                *self.kube,
                "exec",
                "-i",
                self.name,
                "-c",
                "api-client",
                "--",
                "python",
                "-c",
                Path(api_client.__file__).read_text(encoding="utf-8"),
            ],
            input_data=json.dumps(
                {"method": method, "path": path, "body": body, "key": key}
            ).encode(),
        )
        value = document(output.encode())
        if value.get("status") not in statuses or not isinstance(value.get("body"), dict):
            code = value.get("body", {}).get("code", "UNEXPECTED_RESPONSE")
            if not isinstance(code, str) or re.fullmatch(r"[A-Z][A-Z_]{0,63}", code) is None:
                code = "UNEXPECTED_RESPONSE"
            raise AcceptanceError(f"Acceptance API operation failed ({code}); state retained")
        return value

    def read(self, claim: dict[str, Any], run_id: str) -> bytes:
        """Read at most the claimed bound plus one byte and verify bytes and S3 metadata."""
        key = reference(claim, run_id)
        self.verify()
        print(f"M04 artifact read-back: {run_id} {claim['format']}", flush=True)
        output = cluster.run(
            [
                *self.kube,
                "exec",
                self.name,
                "-c",
                "evidence-reader",
                "--",
                "sh",
                "-c",
                READ_OBJECT,
                "read-evidence",
                key,
                str(claim["sizeBytes"]),
            ],
        )
        metadata, end = json.JSONDecoder().raw_decode(output.lstrip())
        payload = base64.b64decode("".join(output.lstrip()[end:].split()), validate=True)
        if (
            metadata
            != {
                "sizeBytes": claim["sizeBytes"],
                "mediaType": claim["mediaType"],
                "metadata": {
                    "artifact-id": claim["id"],
                    "sha256": claim["sha256"],
                    "format": claim["format"],
                },
            }
            or len(payload) != claim["sizeBytes"]
            or hashlib.sha256(payload).hexdigest() != claim["sha256"]
        ):
            raise AcceptanceError("Artifact bytes or metadata differ from the registered claim")
        return payload
