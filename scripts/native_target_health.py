"""Verify the owned image-backed target before opening tunnels or accepting HTTPS content."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

if __package__:
    from . import cluster
    from . import control_plane_registry as registry
    from . import native_target_release as release
    from .workspace import unique_object
else:
    import cluster
    import control_plane_registry as registry
    import native_target_release as release
    from workspace import unique_object

LABELS = {
    "app.kubernetes.io/name": "perfeng-native-search",
    "app.kubernetes.io/instance": release.RELEASE,
}
SELECTOR = ",".join(f"{name}={value}" for name, value in LABELS.items())
MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}


def object_value(value: Any, location: str) -> dict[str, Any]:
    """Reject malformed nested API values before inspecting ownership or readiness."""
    if not isinstance(value, dict):
        raise ValueError(f"Invalid native target {location}")
    return value


def kubectl(root: Path, namespace: str = release.NAMESPACE) -> list[str]:
    """Bind all reads to the dedicated cluster and namespace, never the ambient context."""
    return [
        "kubectl",
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--context",
        cluster.CONTEXT,
        "--request-timeout=10s",
        "-n",
        namespace,
    ]


def platform_digest(selection: release.TargetSelection, content: bytes) -> str:
    """Resolve the authenticated manifest/index to one Linux AMD64 manifest, not a config ID."""
    digest = selection.image.split("@", 1)[1]
    if "sha256:" + hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("Candidate registry manifest bytes differ from the selected digest")
    value = json.loads(content, object_pairs_hook=unique_object)
    if (
        not isinstance(value, dict)
        or type(value.get("schemaVersion")) is not int
        or value["schemaVersion"] != 2
    ):
        raise ValueError("Invalid candidate registry manifest")
    if value.get("mediaType") in MANIFEST_TYPES:
        return digest
    if value.get("mediaType") not in INDEX_TYPES or not isinstance(value.get("manifests"), list):
        raise ValueError("Candidate must have a supported OCI/Docker v2 manifest or index")
    matches = [
        item
        for item in value["manifests"]
        if isinstance(item, dict)
        and item.get("platform") == {"os": "linux", "architecture": "amd64"}
    ]
    if (
        len(matches) != 1
        or matches[0].get("mediaType") not in MANIFEST_TYPES
        or re.fullmatch(r"sha256:[0-9a-f]{64}", str(matches[0].get("digest"))) is None
    ):
        raise ValueError("Candidate index must identify exactly one Linux AMD64 manifest")
    return matches[0]["digest"]


def validate_service(value: dict[str, Any]) -> None:
    """Require the internal target Service and exact selector/port, with no external address exposure."""
    metadata = object_value(value.get("metadata"), "Service metadata")
    spec = object_value(value.get("spec"), "Service spec")
    labels = object_value(metadata.get("labels"), "Service labels")
    if (
        metadata.get("name") != release.RELEASE
        or metadata.get("namespace") != release.NAMESPACE
        or any(labels.get(key) != item for key, item in LABELS.items())
        or spec.get("type") != "ClusterIP"
        or spec.get("selector") != LABELS
        or spec.get("ports")
        != [{"name": "http", "port": 8080, "targetPort": "http", "protocol": "TCP"}]
        or spec.get("externalIPs")
        or spec.get("externalName")
        or spec.get("clusterIP") in (None, "None", "")
    ):
        raise ValueError("Native target Service differs from the reviewed internal release")


def validate_pod_spec(value: object, selection: release.TargetSelection) -> None:
    """Reject credentials, extra containers, overlays, identity drift and weakened execution policy."""
    value = object_value(value, "Pod spec")
    security = object_value(value.get("securityContext"), "Pod security context")
    containers = value.get("containers", [])
    if (
        value.get("serviceAccountName") != release.RELEASE
        or value.get("automountServiceAccountToken") is not False
        or value.get("imagePullSecrets") != [{"name": release.PULL_SECRET}]
        or value.get("nodeSelector") != {"kubernetes.io/os": "linux", "kubernetes.io/arch": "amd64"}
        or value.get("hostNetwork", False)
        or value.get("hostPID", False)
        or value.get("hostIPC", False)
        or value.get("initContainers")
        or value.get("ephemeralContainers")
        or security.get("runAsNonRoot") is not True
        or security.get("runAsUser") != 101
        or security.get("runAsGroup") != 101
        or security.get("fsGroup") != 101
        or security.get("seccompProfile") != {"type": "RuntimeDefault"}
        or value.get("volumes")
        != [{"name": "temporary", "emptyDir": {"medium": "Memory", "sizeLimit": "16Mi"}}]
        or not isinstance(containers, list)
        or len(containers) != 1
    ):
        raise ValueError("Native target Pod isolation or identity differs")
    container = object_value(containers[0], "container")
    if (
        container.get("name") != "search"
        or container.get("image") != selection.image
        or container.get("imagePullPolicy") != "IfNotPresent"
        or container.get("env")
        or container.get("envFrom")
        or container.get("command")
        or container.get("args")
        or container.get("securityContext")
        != {
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
        }
        or container.get("volumeMounts") != [{"name": "temporary", "mountPath": "/tmp"}]
        or container.get("resources")
        != {
            "requests": {"cpu": "50m", "memory": "32Mi", "ephemeral-storage": "16Mi"},
            "limits": {"cpu": "500m", "memory": "64Mi", "ephemeral-storage": "32Mi"},
        }
    ):
        raise ValueError(
            "Native target container image, content boundary or resource policy differs"
        )
    if container.get("ports") != [{"name": "http", "containerPort": 8080, "protocol": "TCP"}]:
        raise ValueError("Native target container port differs")
    for name, period, failures in (
        ("startupProbe", 2, 30),
        ("readinessProbe", 5, 2),
        ("livenessProbe", 10, 3),
    ):
        probe = object_value(container.get(name), name)
        http = dict(object_value(probe.get("httpGet"), "probe HTTP target"))
        scheme = http.pop("scheme", "HTTP")
        if (
            set(probe)
            - {"httpGet", "periodSeconds", "timeoutSeconds", "failureThreshold", "successThreshold"}
            or http != {"path": "/healthz", "port": "http"}
            or scheme != "HTTP"
            or probe.get("periodSeconds") != period
            or probe.get("timeoutSeconds") != 1
            or probe.get("failureThreshold") != failures
            or probe.get("successThreshold", 1) != 1
        ):
            raise ValueError("Native target health probe differs")


def validate_resources(
    deployment: dict[str, Any],
    pods: dict[str, Any],
    selection: release.TargetSelection,
    digest: str,
) -> str:
    """Require one current ready owned Pod with the registry-resolved manifest digest."""
    _validate_deployment(deployment, selection)
    items = pods.get("items")
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise ValueError("Expected exactly one native target Pod")
    return _validate_ready_pod(items[0], selection, digest, deployment["metadata"].get("uid"))


def _validate_deployment(deployment: dict[str, Any], selection: release.TargetSelection) -> None:
    """Require the selected single-replica rollout to be fully observed and available."""
    metadata = object_value(deployment.get("metadata"), "Deployment metadata")
    spec = object_value(deployment.get("spec"), "Deployment spec")
    status = object_value(deployment.get("status"), "Deployment status")
    labels = object_value(metadata.get("labels"), "Deployment labels")
    template = object_value(spec.get("template"), "Pod template")
    template_metadata = object_value(template.get("metadata"), "Pod template metadata")
    if (
        metadata.get("name") != release.RELEASE
        or metadata.get("namespace") != release.NAMESPACE
        or any(labels.get(key) != item for key, item in LABELS.items())
        or spec.get("replicas") != 1
        or spec.get("strategy") != {"type": "Recreate"}
        or spec.get("selector") != {"matchLabels": LABELS}
        or template_metadata.get("labels") != LABELS
        or type(metadata.get("generation")) is not int
        or status.get("observedGeneration") != metadata["generation"]
        or status.get("updatedReplicas") != 1
        or status.get("availableReplicas") != 1
        or status.get("replicas") != 1
        or status.get("unavailableReplicas", 0) != 0
    ):
        raise ValueError(
            "Native target Deployment is not current and fully available with the selected identity"
        )
    validate_pod_spec(template.get("spec", {}), selection)


def _validate_ready_pod(
    pod: dict[str, Any], selection: release.TargetSelection, digest: str, deployment_uid: object
) -> str:
    """Reject termination, spec drift and image IDs outside the selected manifest graph."""
    validate_pod_spec(pod.get("spec", {}), selection)
    metadata = object_value(pod.get("metadata"), "Pod metadata")
    status = object_value(pod.get("status"), "Pod status")
    labels = object_value(metadata.get("labels"), "Pod labels")
    statuses = status.get("containerStatuses", [])
    conditions = status.get("conditions", [])
    if not isinstance(conditions, list) or not all(isinstance(item, dict) for item in conditions):
        raise ValueError("Invalid native target Pod conditions")
    image_id = release.REPOSITORY + "@" + digest
    allowed_images = {image_id, selection.image}
    allowed_images.update("docker-pullable://" + value for value in tuple(allowed_images))
    if (
        not isinstance(deployment_uid, str)
        or not deployment_uid
        or metadata.get("namespace") != release.NAMESPACE
        or metadata.get("deletionTimestamp")
        or not isinstance(metadata.get("name"), str)
        or re.fullmatch(r"native-search-[a-z0-9-]+", metadata["name"]) is None
        or any(labels.get(key) != item for key, item in LABELS.items())
        or status.get("phase") != "Running"
        or not any(
            item.get("type") == "Ready" and item.get("status") == "True" for item in conditions
        )
        or not isinstance(statuses, list)
        or len(statuses) != 1
        or not isinstance(statuses[0], dict)
        or statuses[0].get("name") != "search"
        or statuses[0].get("ready") is not True
        or not isinstance(statuses[0].get("state"), dict)
        or not statuses[0]["state"].get("running")
        or statuses[0].get("imageID") not in allowed_images
    ):
        raise ValueError("Native target Pod is not ready with the selected platform image digest")
    return metadata["name"]


def read_object(command: list[str]) -> dict[str, Any]:
    """Parse a bounded Kubernetes read into a mapping without exposing raw payloads on failure."""
    value = json.loads(cluster.run(command), object_pairs_hook=unique_object)
    if not isinstance(value, dict):
        raise ValueError("Invalid native target Kubernetes object")
    return value


def verify(root: Path = release.ROOT) -> str:
    """Read-only proof of selection, registry access and ready resources; return the exact Pod name."""
    selection = release.validate(root)
    cluster.verify_local_docker()
    cluster.verify_context(root)
    application = read_object(
        [*kubectl(root, "argocd"), "get", "application", release.APPLICATION, "-o", "json"]
    )
    release.validate_application(application, selection)
    status = object_value(application.get("status"), "Application status")
    sync = object_value(status.get("sync"), "Application sync")
    health = object_value(status.get("health"), "Application health")
    if (
        sync.get("status") != "Synced"
        or sync.get("revision") != selection.deployment_revision
        or health.get("status") != "Healthy"
    ):
        raise ValueError(
            "Native target Application must be Synced and Healthy at the selected revision"
        )
    kube = kubectl(root)
    secret = read_object([*kube, "get", "secret", release.PULL_SECRET, "-o", "json"])
    credentials = registry.decode_secret(secret)
    digest = platform_digest(
        selection, registry.verify_registry_credentials(*credentials, selection.image)
    )
    deployment = read_object([*kube, "get", "deployment", release.RELEASE, "-o", "json"])
    service = read_object([*kube, "get", "service", release.RELEASE, "-o", "json"])
    pods = read_object([*kube, "get", "pods", "-l", SELECTOR, "-o", "json"])
    validate_service(service)
    name = validate_resources(deployment, pods, selection, digest)
    owners = pods["items"][0].get("metadata", {}).get("ownerReferences", [])
    if (
        not isinstance(owners, list)
        or len(owners) != 1
        or not isinstance(owners[0], dict)
        or owners[0].get("kind") != "ReplicaSet"
        or owners[0].get("controller") is not True
        or not isinstance(owners[0].get("uid"), str)
        or re.fullmatch(r"native-search-[a-z0-9-]+", str(owners[0].get("name"))) is None
    ):
        raise ValueError("Native target Pod has unexpected controller ownership")
    replica = read_object([*kube, "get", "replicaset", owners[0]["name"], "-o", "json"])
    replica_metadata = object_value(replica.get("metadata"), "ReplicaSet metadata")
    replica_owners = replica_metadata.get("ownerReferences")
    if not isinstance(replica_owners, list) or not all(
        isinstance(owner, dict) for owner in replica_owners
    ):
        raise ValueError("Invalid native target ReplicaSet owners")
    if (
        replica_metadata.get("uid") != owners[0].get("uid")
        or replica_metadata.get("namespace") != release.NAMESPACE
        or not any(
            owner.get("kind") == "Deployment"
            and owner.get("uid") == deployment["metadata"]["uid"]
            and owner.get("controller") is True
            for owner in replica_owners
        )
    ):
        raise ValueError("Native target Pod is not controlled by the selected Deployment")
    print(f"Verified image-backed native target: {selection.image}; Pod {name}.", flush=True)
    return name


def main(argv: list[str] | None = None) -> int:
    """Preview offline; explicit execution reads Kubernetes/GHCR without applying or rotating anything."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.execute:
            verify()
        else:
            selection = release.load_selection()
            print(
                f"Preview: verify selected native candidate {selection.image} and its owned ready Pod."
            )
            print("Preview only. Add --execute for read-only GHCR/Kubernetes access.")
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Native target health error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
