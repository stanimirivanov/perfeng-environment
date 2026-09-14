"""Deploy and verify the immutable local control-plane release."""

import argparse
import io
import json
import tarfile
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

if __package__:
    from . import cluster, control_plane_migration, control_plane_runtime, postgres
    from .control_plane_registry import SECRET as IMAGE_PULL_SECRET
    from .control_plane_release import DEFAULT_K8S_ROOT, DeploymentLock, git
    from .control_plane_release import validate as validate_release
else:
    import cluster
    import control_plane_migration
    import control_plane_runtime
    import postgres
    from control_plane_registry import SECRET as IMAGE_PULL_SECRET
    from control_plane_release import DEFAULT_K8S_ROOT, DeploymentLock, git
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


def helm_command(chart: Path, release: DeploymentLock, root: Path) -> list[str]:
    """Build the credential-free Helm invocation for the locked release."""
    command = [
        "helm",
        "upgrade",
        "--install",
        RELEASE,
        str(chart),
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--kube-context",
        cluster.CONTEXT,
        "--namespace",
        postgres.NAMESPACE,
        "--wait",
        "--timeout",
        "300s",
    ]
    values = {
        "image": release.image,
        "imagePullSecret": IMAGE_PULL_SECRET,
        "executionNamespace": EXECUTION_NAMESPACE,
        "runtimeConfigMap": control_plane_runtime.RUNTIME_CONFIG,
        "runtimeSecret": control_plane_runtime.RUNTIME_SECRET,
        "authenticationSecret": control_plane_runtime.AUTHENTICATION_SECRET,
        "registryConfigMap": control_plane_runtime.REGISTRY_CONFIG,
    }
    for name, value in values.items():
        command.extend(["--set-string", f"{name}={value}"])
    for index, item in enumerate(registry_items(release)):
        command.extend(["--set-string", f"registryItems[{index}].key={item['key']}"])
        command.extend(["--set-string", f"registryItems[{index}].path={item['path']}"])
    return command


def helm_status_command(root: Path) -> list[str]:
    """Build the read-only Helm release-identity query."""
    return [
        "helm",
        "list",
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--kube-context",
        cluster.CONTEXT,
        "--namespace",
        postgres.NAMESPACE,
        "--filter",
        f"^{RELEASE}$",
        "--output",
        "json",
    ]


@contextmanager
def locked_chart(release: DeploymentLock, k8s_root: Path = DEFAULT_K8S_ROOT) -> Iterator[Path]:
    """Extract the selected chart from Git without consulting working-tree bytes."""
    archive = git(
        k8s_root,
        "archive",
        "--format=tar",
        release.deployment_revision,
        release.chart_path,
    )
    with tempfile.TemporaryDirectory(prefix="perfeng-control-plane-") as temporary:
        destination = Path(temporary)
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as bundle:
            for member in bundle.getmembers():
                path = PurePosixPath(member.name)
                chart_path = PurePosixPath(release.chart_path)
                inside_chart = path.is_relative_to(chart_path)
                chart_ancestor = chart_path.is_relative_to(path) and member.isdir()
                if (
                    not (inside_chart or chart_ancestor)
                    or member.issym()
                    or member.islnk()
                    or not (member.isfile() or member.isdir())
                ):
                    raise ValueError("Pinned chart archive contains an unsafe member")
            bundle.extractall(destination, filter="data")
        chart = destination.joinpath(*PurePosixPath(release.chart_path).parts)
        if not chart.is_dir():
            raise ValueError("Pinned chart archive is incomplete")
        yield chart


def validate_deployment(value: dict[str, Any], release: DeploymentLock) -> None:
    """Require the live Deployment to use the selected image and runtime resources."""
    metadata = value.get("metadata", {})
    spec = value.get("spec", {})
    template = spec.get("template", {}).get("spec", {})
    containers = template.get("containers", [])
    if len(containers) != 1:
        raise ValueError("Control-plane Deployment must have exactly one container")
    container = containers[0]
    env_from = container.get("envFrom")
    volumes = {item.get("name"): item for item in template.get("volumes", [])}
    expected_env_from = [
        {"configMapRef": {"name": control_plane_runtime.RUNTIME_CONFIG}},
        {"secretRef": {"name": control_plane_runtime.RUNTIME_SECRET}},
    ]
    expected_registry_items = registry_items(release)
    if (
        metadata.get("name") != RELEASE
        or metadata.get("namespace") != postgres.NAMESPACE
        or spec.get("replicas") != 1
        or container.get("name") != "control-plane"
        or container.get("image") != release.image
        or env_from != expected_env_from
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
        != control_plane_runtime.REGISTRY_CONFIG
        or volumes.get("registry", {}).get("configMap", {}).get("items") != expected_registry_items
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


def validate_helm_status(value: list[dict[str, Any]], release: DeploymentLock) -> None:
    """Require Helm to report the selected chart release as deployed."""
    if len(value) != 1:
        raise ValueError("Expected exactly one Helm control-plane release")
    status = value[0]
    if (
        status.get("name") != RELEASE
        or status.get("namespace") != postgres.NAMESPACE
        or status.get("status") != "deployed"
        or status.get("chart") != f"perfeng-control-plane-{release.chart_version}"
    ):
        raise ValueError("Helm release identity or status differs from the deployment lock")


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


def verify_workload(kube: list[str], release: DeploymentLock, root: Path) -> None:
    """Verify the legacy Helm release and its live workload resources."""
    status = json.loads(cluster.run(helm_status_command(root)))
    validate_helm_status(status, release)
    verify_resources(kube, release)


def verify_managed_workload(root: Path = cluster.ROOT) -> None:
    """Verify prerequisites and resources without assuming a deployment reconciler."""
    release = validate_release(
        root / "deployments/local-control-plane.json",
        root.parent / "perfeng-k8s",
        root.parent / "perfeng-control-plane",
    )
    control_plane_migration.execute("health", root)
    control_plane_runtime.execute("health", root)
    verify_resources(postgres.kubectl(root), release)


def execute(action: str, root: Path = cluster.ROOT) -> None:
    """Deploy or verify the selected control-plane release and its prerequisites."""
    release = validate_release()
    control_plane_migration.execute("health", root)
    control_plane_runtime.execute("health", root)
    kube = postgres.kubectl(root)
    if action == "deploy":
        with locked_chart(release) as chart:
            cluster.run(helm_command(chart, release, root))
    verify_workload(kube, release, root)
    print(f"Local control-plane deployment validated: {action}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["deploy", "health"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            execute(args.action)
        else:
            print("Preview: deploy the locked control-plane chart and verify its endpoints.")
            print("Preview only. Add --execute to access Kubernetes or install the release.")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        tarfile.TarError,
        yaml.YAMLError,
        json.JSONDecodeError,
    ) as error:
        print(f"Control-plane deployment error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
