"""Pure release predicates shared by the v2 preflight, health and ledger boundaries."""

import base64
import hashlib
import json
import re
from typing import Any

API = "https://kubernetes.default.svc"
NAMES = ("perfeng-local-root", "perfeng-local-control-plane")
NAMESPACE = "perfeng-release-ledger"
POINTER = "perfeng-local-current"
OWNER = "perfeng-environment"
WORKLOAD_LABELS = {
    "app.kubernetes.io/name": "perfeng-control-plane",
    "app.kubernetes.io/instance": "control-plane",
}
ROOT_SOURCE = {
    "repoURL": "https://github.com/stanimirivanov/perfeng-environment.git",
    "targetRevision": "main",
    "path": "gitops/environments/local",
}


class ReadinessRefused(ValueError):
    """A fixed diagnostic that contains no resource values or response bodies."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise ReadinessRefused(message)


def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def identity(value: dict[str, Any], kind: str, namespace: str, name: str) -> dict[str, str]:
    metadata = value["metadata"]
    require(
        value["kind"] == kind
        and value["apiVersion"]
        == {
            "Application": "argoproj.io/v1alpha1",
            "Deployment": "apps/v1",
            "Namespace": "v1",
            "ConfigMap": "v1",
        }[kind]
        and metadata["name"] == name
        and metadata.get("namespace", "") == namespace
        and isinstance(metadata.get("uid"), str)
        and metadata["uid"]
        and isinstance(metadata.get("resourceVersion"), str)
        and metadata["resourceVersion"]
        and not metadata.get("deletionTimestamp")
        and not metadata.get("ownerReferences"),
        "resource identity or ownership differs",
    )
    return {
        "kind": kind,
        **{key: metadata.get(key, "") for key in ("namespace", "name", "uid", "resourceVersion")},
    }


def application_identity(value: dict[str, Any], name: str) -> dict[str, str]:
    result = identity(value, "Application", "argocd", name)
    spec = value["spec"]
    require(
        value["metadata"].get("labels", {}).get("app.kubernetes.io/managed-by") == OWNER
        and spec["project"] == ("default" if name == NAMES[0] else "perfeng-local")
        and spec["destination"]
        == {"server": API, "namespace": "argocd" if name == NAMES[0] else "perf-platform"}
        and not value.get("operation")
        and value.get("status", {}).get("operationState", {}).get("phase")
        not in {"Running", "Terminating"},
        "Application is foreign or syncing",
    )
    if name == NAMES[0]:
        require("sources" not in spec and spec["source"] == ROOT_SOURCE, "root source differs")
    return result


def selected_lock(preflight: dict[str, Any], raw: bytes, revision: str) -> dict[str, Any]:
    require(
        preflight.get("schemaVersion") == 1
        and preflight["kind"] == "PerfengReleasePreflight"
        and preflight["status"] == "identity-verified"
        and preflight["environment"] == "perfeng-local"
        and preflight["selectionRevision"] == revision,
        "preflight identity differs",
    )
    require(
        "sha256:" + hashlib.sha256(raw).hexdigest()
        == preflight["lockDigests"]["deployments/local-control-plane.json"],
        "selected deployment lock differs from preflight",
    )
    lock = json.loads(raw, object_pairs_hook=unique)
    require(lock.get("schemaVersion") == 1, "unsupported deployment lock version")
    deployment = lock["deployment"]
    require(
        re.fullmatch(r"[0-9a-f]{40}", deployment["revision"])
        and deployment["repository"] == "https://github.com/stanimirivanov/perfeng-k8s.git",
        "unsupported deployment source",
    )
    return lock


def verify_readiness(
    preflight: dict[str, Any],
    raw_lock: bytes,
    observations: dict[str, Any],
    selection_revision: str,
) -> dict[str, Any]:
    lock = selected_lock(preflight, raw_lock, selection_revision)
    deployment = lock["deployment"]
    image = lock["controlPlane"]["image"]
    require(
        re.fullmatch(r"ghcr.io/stanimirivanov/perfeng-control-plane@sha256:[0-9a-f]{64}", image),
        "unsupported deployment identity",
    )
    runtime = deployment["runtimeResources"]
    sources = [
        {
            "repository": deployment["repository"],
            "revision": deployment["revision"],
            "path": path,
            "image": image if chart else None,
            "runtimeConfigMap": runtime["runtimeConfigMap"] if chart else None,
            "registryConfigMap": runtime["registryConfigMap"] if chart else None,
        }
        for path, chart in (
            (deployment["chart"]["path"], True),
            (deployment["runtimePackage"]["path"], False),
        )
    ]
    app = observations["application"]
    require(
        app["name"] == "perfeng-local-control-plane"
        and app["namespace"] == "argocd"
        and app["uid"]
        and app["project"] == "perfeng-local"
        and app["destination"]
        == {"server": "https://kubernetes.default.svc", "namespace": "perf-platform"}
        and app["sources"] == sources
        and app["comparedSources"] == sources
        and app["revisions"] == [deployment["revision"], deployment["revision"]]
        and app["sync"] == "Synced"
        and app["health"] == "Healthy"
        and not app["conditions"]
        and app["operation"] in (None, "Succeeded"),
        "Application does not report the selected converged release",
    )
    resources = []
    for key, kind, name in (
        ("postgres", "StatefulSet", "postgres"),
        ("seaweedfs", "StatefulSet", "seaweedfs"),
        ("deployment", "Deployment", "control-plane"),
    ):
        observed = observations[key]
        require(
            observed["name"] == name
            and observed["namespace"] == "perf-platform"
            and observed["uid"]
            and type(observed["generation"]) is int
            and observed["generation"] > 0
            and observed["observedGeneration"] == observed["generation"]
            and all(observed[field] == 1 for field in ("desired", "replicas", "ready", "updated")),
            f"{name} is stale or not fully ready",
        )
        if kind == "StatefulSet":
            require(
                observed["currentRevision"]
                and observed["currentRevision"] == observed["updateRevision"],
                f"{name} has not completed its StatefulSet revision",
            )
        else:
            require(
                observed["available"] == 1
                and observed["unavailable"] == 0
                and observed["images"] == [image]
                and observed["runtimeConfigMaps"] == [runtime["runtimeConfigMap"]]
                and observed["registryConfigMaps"] == [runtime["registryConfigMap"]]
                and observed["runtimeSecrets"] == ["perfeng-control-plane-runtime"]
                and observed["imagePullSecrets"] == ["perfeng-control-plane-registry"]
                and observed["authenticationSecrets"] == ["perfeng-control-plane-authentication"],
                "Deployment differs from the selected runtime",
            )
        resources.append(
            {
                "kind": kind,
                "name": name,
                "namespace": "perf-platform",
                "uid": observed["uid"],
                "generation": observed["generation"],
                "observedGeneration": observed["observedGeneration"],
            }
        )
    service = observations["service"]
    require(
        service["name"] == "control-plane"
        and service["namespace"] == "perf-platform"
        and service["uid"]
        and service["type"] == "ClusterIP"
        and service["selector"] == WORKLOAD_LABELS
        and service["ports"]
        == [{"name": "http", "port": 8080, "protocol": "TCP", "targetPort": "http"}],
        "Service differs from the reviewed control-plane endpoint",
    )
    return {
        "schemaVersion": 1,
        "kind": "PerfengReleaseReadiness",
        "scope": "control-plane-foundation",
        "status": "readiness-verified",
        "requestDigest": preflight["requestDigest"],
        "requestRevision": preflight["requestRevision"],
        "selectionRevision": selection_revision,
        "clusterUID": preflight["clusterUID"],
        "applicationUID": app["uid"],
        "serviceUID": service["uid"],
        "deploymentRevision": deployment["revision"],
        "image": image,
        "resources": resources,
        "checkedEndpoints": ["livez", "readyz", "metrics"],
    }


def verify_quiescence(
    preflight: dict[str, Any],
    raw_lock: bytes,
    revision: str,
    before: list[dict[str, Any]],
    pods: dict[str, Any],
    after: list[dict[str, Any]],
    namespace: dict[str, Any],
) -> dict[str, Any]:
    lock = selected_lock(preflight, raw_lock, revision)
    deployment = lock["deployment"]
    require(len(before) == len(after) == 3, "incomplete observations")
    resources = []
    for name, value in zip(NAMES, before[:2], strict=True):
        resources.append(application_identity(value, name))
        spec = value["spec"]
        require(
            spec.get("syncPolicy", {}).get("automated", {}).get("enabled") is False,
            "Application is unpaused",
        )
        if name != NAMES[0]:
            require(
                "source" not in spec
                and len(spec["sources"]) == 2
                and [
                    (source["repoURL"], source["targetRevision"], source["path"])
                    for source in spec["sources"]
                ]
                == [
                    (deployment["repository"], deployment["revision"], path)
                    for path in (deployment["chart"]["path"], deployment["runtimePackage"]["path"])
                ],
                "child sources differ from selected lock",
            )
    value = before[2]
    resources.append(identity(value, "Deployment", "perf-platform", "control-plane"))
    labels = value["metadata"].get("labels", {})
    generation = value["metadata"]["generation"]
    require(
        labels.get("app.kubernetes.io/name") == "perfeng-control-plane"
        and labels.get("app.kubernetes.io/instance") == "control-plane"
        and value["spec"]["template"]["spec"]["serviceAccountName"] == "control-plane"
        and value["spec"]["selector"] == {"matchLabels": WORKLOAD_LABELS}
        and type(generation) is int
        and generation > 0
        and value["status"]["observedGeneration"] == generation
        and value["spec"]["replicas"] == 0
        and all(
            value["status"].get(field, 0) == 0
            for field in ("replicas", "readyReplicas", "availableReplicas", "updatedReplicas")
        ),
        "Deployment is foreign, stale or not stopped",
    )
    require(
        pods["apiVersion"] == "v1"
        and pods["kind"] == "PodList"
        and pods["items"] == []
        and not pods["metadata"].get("continue")
        and isinstance(pods["metadata"].get("resourceVersion"), str)
        and pods["metadata"]["resourceVersion"],
        "Pods remain or absence is unproven",
    )
    for expected, value in zip(resources, after, strict=True):
        require(
            identity(value, expected["kind"], expected["namespace"], expected["name"]) == expected,
            "resource changed during checkpoint; inspect and resubmit",
        )
    identity(namespace, "Namespace", "", "kube-system")
    require(namespace["metadata"]["uid"] == preflight["clusterUID"], "cluster incarnation changed")
    return {
        "schemaVersion": 1,
        "kind": "PerfengMaintenanceCheckpoint",
        "status": "quiescence-observed",
        "environment": "perfeng-local",
        "requestDigest": preflight["requestDigest"],
        "requestRevision": preflight["requestRevision"],
        "selectionRevision": revision,
        "clusterUID": preflight["clusterUID"],
        "resources": resources,
        "podListResourceVersion": pods["metadata"]["resourceVersion"],
    }


def labels(purpose: str) -> dict[str, str]:
    return {"app.kubernetes.io/managed-by": OWNER, "perfeng.io/purpose": purpose}


def owned(value: dict[str, Any], name: str, purpose: str) -> None:
    identity(value, "ConfigMap", NAMESPACE, name)
    metadata = value["metadata"]
    require(
        metadata["labels"] == labels(purpose)
        and not metadata.get("finalizers")
        and "argocd.argoproj.io/tracking-id" not in metadata.get("annotations", {}),
        "foreign or incomplete ledger record",
    )


def plan_adoption(
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
    application_identity(root, NAMES[0])
    metadata, status = root["metadata"], root["status"]
    require(
        status.get("operationState", {}).get("phase") in (None, "Succeeded")
        and status["sync"]["status"] == "Synced"
        and status["health"]["status"] == "Healthy"
        and not status.get("conditions")
        and status["sync"]["revision"] == readiness["selectionRevision"]
        and status["sync"]["comparedTo"]["source"] == ROOT_SOURCE,
        "root is stale, syncing or not at the reviewed selection",
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
