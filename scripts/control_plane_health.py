"""Verify the GitOps-managed local control-plane workload."""

import argparse
import json
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster, control_plane_migration, control_plane_runtime, postgres
    from .control_plane_registry import SECRET as IMAGE_PULL_SECRET
    from .control_plane_release import DeploymentLock
    from .control_plane_release import validate as validate_release
else:
    import cluster
    import control_plane_migration
    import control_plane_runtime
    import postgres
    from control_plane_registry import SECRET as IMAGE_PULL_SECRET
    from control_plane_release import DeploymentLock
    from control_plane_release import validate as validate_release

RELEASE = "control-plane"
EXECUTION_NAMESPACE = "perf-generators"
SELECTOR = "app.kubernetes.io/name=perfeng-control-plane,app.kubernetes.io/instance=control-plane"


def registry_items(release: DeploymentLock) -> list[dict[str, str]]:
    """Map immutable ConfigMap keys back to their reviewed mount paths."""
    return [
        {"key": path.replace("/", "--"), "path": path}
        for path in control_plane_runtime.registry_paths(release)
    ]


def validate_deployment(value: dict[str, Any], release: DeploymentLock) -> None:
    """Require the live Deployment to use the selected image and runtime resources."""
    metadata = value.get("metadata", {})
    spec = value.get("spec", {})
    template = spec.get("template", {}).get("spec", {})
    containers = template.get("containers", [])
    if len(containers) != 1:
        raise ValueError("Control-plane Deployment must have exactly one container")
    container = containers[0]
    volumes = {item.get("name"): item for item in template.get("volumes", [])}
    if (
        metadata.get("name") != RELEASE
        or metadata.get("namespace") != postgres.NAMESPACE
        or spec.get("replicas") != 1
        or container.get("name") != "control-plane"
        or container.get("image") != release.image
        or container.get("envFrom")
        != [
            {"configMapRef": {"name": release.runtime_config_map}},
            {"secretRef": {"name": control_plane_runtime.RUNTIME_SECRET}},
        ]
        or template.get("imagePullSecrets") != [{"name": IMAGE_PULL_SECRET}]
        or template.get("nodeSelector") != {"workload": "control-plane"}
        or template.get("tolerations")
        != [
            {
                "key": "node-role.kubernetes.io/control-plane",
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        ]
        or volumes.get("authentication", {}).get("secret", {}).get("secretName")
        != control_plane_runtime.AUTHENTICATION_SECRET
        or volumes.get("registry", {}).get("configMap", {}).get("name")
        != release.registry_config_map
        or volumes.get("registry", {}).get("configMap", {}).get("items") != registry_items(release)
    ):
        raise ValueError("Control-plane Deployment differs from the reviewed release")
    status = value.get("status", {})
    if (
        status.get("observedGeneration") != metadata.get("generation")
        or status.get("updatedReplicas") != 1
        or status.get("availableReplicas") != 1
        or status.get("unavailableReplicas", 0) != 0
    ):
        raise ValueError("Control-plane Deployment is not fully available")


def validate_service(value: dict[str, Any]) -> None:
    """Require the internal Service to expose only the named control-plane HTTP port."""
    if (
        value.get("metadata", {}).get("name") != RELEASE
        or value.get("metadata", {}).get("namespace") != postgres.NAMESPACE
        or value.get("spec", {}).get("type") != "ClusterIP"
        or value.get("spec", {}).get("selector")
        != {
            "app.kubernetes.io/name": "perfeng-control-plane",
            "app.kubernetes.io/instance": RELEASE,
        }
        or value.get("spec", {}).get("ports")
        != [{"name": "http", "port": 8080, "protocol": "TCP", "targetPort": "http"}]
    ):
        raise ValueError("Control-plane Service differs from the reviewed release")


def validate_pods(value: dict[str, Any], release: DeploymentLock) -> None:
    """Require one ready Pod running the exact image selected in the deployment lock."""
    items = value.get("items", [])
    if len(items) != 1:
        raise ValueError("Expected exactly one control-plane Pod")
    pod = items[0]
    containers = pod.get("spec", {}).get("containers", [])
    statuses = pod.get("status", {}).get("containerStatuses", [])
    ready = {
        condition.get("type"): condition.get("status")
        for condition in pod.get("status", {}).get("conditions", [])
    }
    if (
        pod.get("status", {}).get("phase") != "Running"
        or ready.get("Ready") != "True"
        or len(containers) != 1
        or containers[0].get("image") != release.image
        or len(statuses) != 1
        or statuses[0].get("ready") is not True
        or not str(statuses[0].get("imageID", "")).startswith(
            "ghcr.io/stanimirivanov/perfeng-control-plane@sha256:"
        )
    ):
        raise ValueError("Control-plane Pod is not ready with the selected image")


def validate_endpoint(path: str, response: str) -> None:
    """Validate one operational endpoint response without accepting an API fallback."""
    if path in {"livez", "readyz"}:
        if response != "ok\n":
            raise ValueError(f"Control-plane /{path} response is invalid")
    elif path == "metrics":
        if "perfeng_control_plane_ready 1\n" not in response:
            raise ValueError("Control-plane metrics do not report readiness")
    else:
        raise ValueError("Unknown control-plane operational endpoint")


def verify_resources(kube: list[str], release: DeploymentLock) -> None:
    """Verify resource identity, Pod readiness and operational responses."""
    cluster.run([*kube, "rollout", "status", f"deployment/{RELEASE}", "--timeout=300s"])
    deployment = json.loads(cluster.run([*kube, "get", "deployment", RELEASE, "-o", "json"]))
    service = json.loads(cluster.run([*kube, "get", "service", RELEASE, "-o", "json"]))
    pods = json.loads(cluster.run([*kube, "get", "pods", "-l", SELECTOR, "-o", "json"]))
    validate_deployment(deployment, release)
    validate_service(service)
    validate_pods(pods, release)
    proxy = f"/api/v1/namespaces/{postgres.NAMESPACE}/services/http:{RELEASE}:http/proxy"
    for path in ("livez", "readyz", "metrics"):
        validate_endpoint(path, cluster.run([*kube, "get", "--raw", f"{proxy}/{path}"]))


def verify(root: Path = cluster.ROOT) -> None:
    """Verify prerequisites and the GitOps-managed workload without consulting Helm history."""
    release = validate_release(
        root / "deployments/local-control-plane.json",
        root.parent / "perfeng-k8s",
        root.parent / "perfeng-control-plane",
    )
    control_plane_migration.execute("health", root)
    control_plane_runtime.execute("health", root)
    verify_resources(postgres.kubectl(root), release)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            verify()
            print("Local GitOps-managed control-plane workload is healthy.", flush=True)
        else:
            print("Preview: verify control-plane prerequisites, resources and endpoints.")
            print("Preview only. Add --execute to access the local cluster.")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        yaml.YAMLError,
        json.JSONDecodeError,
    ) as error:
        print(f"Control-plane health error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
