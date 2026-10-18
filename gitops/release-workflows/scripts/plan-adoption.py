"""Validate adoption observations and plan records; Argo alone performs Kubernetes operations."""

import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

NAMESPACE = "perfeng-release-ledger"
POINTER = "perfeng-local-current"
OWNER = "perfeng-environment"


def require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = {}
    for key, item in pairs:
        require(key not in value, "duplicate JSON key")
        value[key] = item
    return value


def labels(purpose: str) -> dict[str, str]:
    return {"app.kubernetes.io/managed-by": OWNER, "perfeng.io/purpose": purpose}


def owned(value: dict[str, Any], name: str, purpose: str) -> None:
    metadata = value["metadata"]
    require(
        value["apiVersion"] == "v1"
        and value["kind"] == "ConfigMap"
        and metadata["namespace"] == NAMESPACE
        and metadata["name"] == name
        and metadata["labels"] == labels(purpose)
        and isinstance(metadata.get("uid"), str)
        and metadata["uid"]
        and isinstance(metadata.get("resourceVersion"), str)
        and metadata["resourceVersion"]
        and not metadata.get("ownerReferences")
        and not metadata.get("deletionTimestamp")
        and not metadata.get("finalizers")
        and "argocd.argoproj.io/tracking-id" not in metadata.get("annotations", {}),
        "foreign or incomplete ledger record",
    )


def plan(
    raw: bytes,
    readiness: dict[str, Any],
    root: dict[str, Any],
    receipt: dict[str, Any] | None,
    pointer: dict[str, Any] | None,
    root_after: dict[str, Any] | None = None,
) -> dict[str, Any]:
    require(0 < len(raw) <= 65536, "request exceeds ledger profile")
    request = json.loads(raw, object_pairs_hook=unique)
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()
    require(
        request["schemaVersion"] == 1
        and request["kind"] == "PerfengReleaseRequest"
        and request["environment"]["name"] == "perfeng-local"
        and request["promotion"]["activation"] == "manual"
        and request["promotion"]["expectedCurrent"] is None
        and readiness["schemaVersion"] == 1
        and readiness["kind"] == "PerfengReleaseReadiness"
        and readiness["scope"] == "control-plane-foundation"
        and readiness["status"] == "readiness-verified"
        and readiness["requestDigest"] == digest
        and readiness["clusterUID"] == request["environment"]["clusterUID"]
        and readiness["selectionRevision"] == request["selection"]["revision"],
        "request/readiness identity differs or adoption is not initial",
    )
    metadata, spec, status = root["metadata"], root["spec"], root["status"]
    source = {
        "repoURL": "https://github.com/stanimirivanov/perfeng-environment.git",
        "targetRevision": "main",
        "path": "gitops/environments/local",
    }
    require(
        root["apiVersion"] == "argoproj.io/v1alpha1"
        and root["kind"] == "Application"
        and metadata["name"] == "perfeng-local-root"
        and metadata["namespace"] == "argocd"
        and metadata.get("labels", {}).get("app.kubernetes.io/managed-by") == OWNER
        and metadata.get("uid")
        and metadata.get("resourceVersion")
        and not metadata.get("ownerReferences")
        and not metadata.get("deletionTimestamp")
        and spec["project"] == "default"
        and "sources" not in spec
        and spec["source"] == source
        and spec["destination"]
        == {"server": "https://kubernetes.default.svc", "namespace": "argocd"}
        and not root.get("operation")
        and status.get("operationState", {}).get("phase") in (None, "Succeeded")
        and status["sync"]["status"] == "Synced"
        and status["health"]["status"] == "Healthy"
        and not status.get("conditions")
        and status["sync"]["revision"] == readiness["selectionRevision"]
        and status["sync"]["comparedTo"]["source"] == source,
        "root is foreign, stale, syncing or not at the reviewed selection",
    )
    if root_after is not None:
        require(root_after == root, "root changed during adoption; inspect and resubmit")
    name = "receipt." + digest[7:39] + "." + digest[39:]
    record = {
        "schemaVersion": 1,
        "kind": "PerfengReleaseAdoptionReceipt",
        "status": "adopted-baseline",
        "environment": request["environment"],
        "requestDigest": digest,
        "selectionRevision": readiness["selectionRevision"],
        "rootUID": metadata["uid"],
        "controlPlaneApplicationUID": readiness["applicationUID"],
        "deploymentRevision": readiness["deploymentRevision"],
        "image": readiness["image"],
    }
    binary = {
        "request.json": base64.b64encode(raw).decode("ascii"),
        "receipt.json": base64.b64encode(json.dumps(record, sort_keys=True).encode()).decode(
            "ascii"
        ),
    }
    if receipt is not None:
        owned(receipt, name, "release-adoption")
        provenance = receipt["metadata"].get("annotations", {})
        require(
            receipt.get("immutable") is True
            and not receipt.get("data")
            and receipt["binaryData"] == binary
            and re.fullmatch(
                r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
                provenance.get("perfeng.io/first-workflow-uid", ""),
            )
            and re.fullmatch(
                r"[0-9a-f]{40}", provenance.get("perfeng.io/first-request-revision", "")
            ),
            "immutable receipt content differs",
        )
    data = {
        "requestDigest": digest,
        "receiptName": name,
        "clusterUID": request["environment"]["clusterUID"],
        "receiptUID": receipt["metadata"]["uid"] if receipt is not None else "",
    }
    if pointer is not None:
        owned(pointer, POINTER, "current-release")
        require(
            receipt is not None
            and pointer.get("immutable", False) is False
            and not pointer.get("binaryData")
            and pointer["data"] == data,
            "current pointer conflicts or references a missing/replaced receipt",
        )
    return {
        "name": name,
        "request-b64": binary["request.json"],
        "receipt-b64": binary["receipt.json"],
        "create-receipt": str(receipt is None).lower(),
        "create-pointer": str(pointer is None).lower(),
        "receipt-uid": data["receiptUID"],
        "cluster-uid": data["clusterUID"],
        "request-digest": digest,
        "result": {
            "schemaVersion": 1,
            "kind": "PerfengReleaseAdoptionResult",
            "status": "adopted-baseline",
            "requestDigest": digest,
            "selectionRevision": readiness["selectionRevision"],
            "clusterUID": data["clusterUID"],
            "receiptName": name,
            "receiptUID": data["receiptUID"],
            "pointerUID": pointer["metadata"]["uid"] if pointer is not None else "",
        },
    }


def main() -> None:
    root = Path("/inputs/request")
    revision = os.environ["REQUEST_REVISION"]
    path = os.environ["REQUEST_PATH"]
    require(
        re.fullmatch(r"[0-9a-f]{40}", revision)
        and re.fullmatch(
            r"deployments/release-requests/[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.json", path
        ),
        "invalid reviewed request location",
    )
    require(
        (root / ".git/HEAD").read_text(encoding="ascii").strip() == revision,
        "request checkout differs",
    )
    file = root / path
    require(
        not file.is_symlink()
        and file.resolve().is_relative_to(root.resolve())
        and file.stat().st_size <= 65536,
        "invalid request path or size",
    )
    inputs = {
        key: json.loads(os.environ[key], object_pairs_hook=unique)
        for key in ("READINESS", "ROOT", "RECEIPT", "POINTER", "ROOT_AFTER")
    }
    value = plan(
        file.read_bytes(),
        inputs["READINESS"],
        inputs["ROOT"],
        inputs["RECEIPT"],
        inputs["POINTER"],
        inputs["ROOT_AFTER"],
    )
    require(value["request-digest"] == os.environ["REQUEST_DIGEST"], "request digest differs")
    phase = os.environ["PHASE"]
    require(phase in {"plan", "pointer", "finish"}, "invalid ledger phase")
    if phase != "plan":
        require(
            inputs["RECEIPT"] is not None and inputs["ROOT_AFTER"] is not None,
            "receipt or fresh root observation missing",
        )
    if phase == "finish":
        require(inputs["POINTER"] is not None, "stored pointer missing")
    for key, item in value.items():
        if key == "result" and phase != "finish":
            item = None
        Path("/tmp/" + key).write_text(
            json.dumps(item) if isinstance(item, dict) or item is None else item, encoding="utf-8"
        )
    print("Adoption observations verified; no workload or promotion operation performed")


def cli() -> int:
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError):
        print(
            "Adoption refused: inspect reviewed input, root/readiness and ledger conflict; retain records",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
