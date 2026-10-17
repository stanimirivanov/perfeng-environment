"""Pause owned GitOps reconcilers and quiesce the control plane before migrations."""

import argparse
import json
from pathlib import Path
from typing import Any

if __package__:
    from . import benchmark_authority, cluster
    from .control_plane_release import load_lock
    from .control_plane_release import validate as validate_release
else:
    import benchmark_authority
    import cluster
    from control_plane_release import load_lock
    from control_plane_release import validate as validate_release

APPLICATIONS = ("perfeng-local-root", "perfeng-local-control-plane")
SELECTOR = "app.kubernetes.io/name=perfeng-control-plane,app.kubernetes.io/instance=control-plane"
REPOSITORIES = {
    APPLICATIONS[0]: (
        "https://github.com/stanimirivanov/perfeng-environment.git",
        "gitops/environments/local",
    ),
    APPLICATIONS[1]: ("https://github.com/stanimirivanov/perfeng-k8s.git", "charts/control-plane"),
}


def kube(root: Path, namespace: str) -> list[str]:
    """Bind every read and patch to the dedicated kubeconfig and namespace."""
    return [
        "kubectl",
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--context",
        cluster.CONTEXT,
        "--namespace",
        namespace,
        "--request-timeout=30s",
    ]


def read(root: Path, namespace: str, kind: str, name: str) -> dict[str, Any] | None:
    """Read an optional object; malformed responses fail closed."""
    text = cluster.run(
        [*kube(root, namespace), "get", kind, name, "--ignore-not-found", "-o", "json"]
    )
    if not text.strip():
        return None
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object while reading {kind}/{name}")
    return value


def validate_application(value: dict[str, Any], name: str) -> None:
    """Refuse foreign identities, ApplicationSet ownership and active sync operations."""
    metadata = value.get("metadata", {})
    spec = value.get("spec", {})
    source = spec.get("source", {})
    sources = spec.get("sources")
    if name == APPLICATIONS[1] and isinstance(sources, list):
        release = load_lock()
        source_matches = (
            "source" not in spec
            and len(sources) == 2
            and all(isinstance(item, dict) for item in sources)
            and [
                (item.get("repoURL"), item.get("path"), item.get("targetRevision"))
                for item in sources
            ]
            == [
                (*REPOSITORIES[name], release.deployment_revision),
                (REPOSITORIES[name][0], release.package_path, release.deployment_revision),
            ]
        )
    else:
        source_matches = (
            sources is None
            and isinstance(source, dict)
            and (source.get("repoURL"), source.get("path")) == REPOSITORIES[name]
        )
    if (
        metadata.get("name") != name
        or metadata.get("namespace") != "argocd"
        or metadata.get("labels", {}).get("app.kubernetes.io/managed-by") != "perfeng-environment"
        or metadata.get("ownerReferences")
        or metadata.get("deletionTimestamp")
        or not source_matches
        or spec.get("destination")
        != {
            "server": "https://kubernetes.default.svc",
            "namespace": "argocd" if name == APPLICATIONS[0] else "perf-platform",
        }
        or value.get("operation")
        or value.get("status", {}).get("operationState", {}).get("phase")
        in {"Running", "Terminating"}
    ):
        raise ValueError(
            f"Application {name} has unexpected ownership, source or an active sync; inspect before retry"
        )


def validate_deployment(value: dict[str, Any]) -> None:
    """Require the original Helm/GitOps identity before changing its replica count."""
    metadata = value.get("metadata", {})
    if (
        metadata.get("name") != "control-plane"
        or metadata.get("namespace") != "perf-platform"
        or metadata.get("labels", {}).get("app.kubernetes.io/name") != "perfeng-control-plane"
        or metadata.get("labels", {}).get("app.kubernetes.io/instance") != "control-plane"
        or metadata.get("deletionTimestamp")
        or value.get("spec", {}).get("template", {}).get("spec", {}).get("serviceAccountName")
        != "control-plane"
    ):
        raise ValueError("Control-plane Deployment identity differs; refusing maintenance")


def patch(
    root: Path, namespace: str, kind: str, value: dict[str, Any], operation: dict[str, Any]
) -> None:
    """Apply one optimistic-concurrency patch without replacing an object."""
    metadata = value["metadata"]
    operations = [
        {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
        {"op": "test", "path": "/metadata/resourceVersion", "value": metadata["resourceVersion"]},
        operation,
    ]
    cluster.run(
        [
            *kube(root, namespace),
            "patch",
            kind,
            metadata["name"],
            "--type=json",
            "-p",
            json.dumps(operations),
        ]
    )


def verify_quiescent(root: Path) -> None:
    """Fail before migration writes unless both reconcilers and all service Pods are stopped."""
    for name in APPLICATIONS:
        value = read(root, "argocd", "application", name)
        if value is not None:
            validate_application(value, name)
            if (
                value.get("spec", {}).get("syncPolicy", {}).get("automated", {}).get("enabled")
                is not False
            ):
                raise ValueError(
                    f"Pause Application {name} before migrations: control_plane_maintenance.py pause --execute"
                )
    deployment = read(root, "perf-platform", "deployment", "control-plane")
    if deployment is not None:
        validate_deployment(deployment)
        if (
            deployment.get("spec", {}).get("replicas", 1) != 0
            or deployment.get("status", {}).get("replicas", 0) != 0
        ):
            raise ValueError("Control plane is not quiescent; pause and wait before migrations")
    pods = json.loads(
        cluster.run([*kube(root, "perf-platform"), "get", "pods", "-l", SELECTOR, "-o", "json"])
    )
    if pods.get("items") != []:
        raise ValueError("Control-plane Pods remain; migration is refused until they stop")


def pause(root: Path = cluster.ROOT) -> None:
    """Pause root first, then child; scale only the owned service and wait without force deletion."""
    release = validate_release()
    with cluster.command_deadline(60):
        cluster.verify_local_docker()
        cluster.verify_context(root)
        benchmark_authority.verify_target(root, release)
    for name in APPLICATIONS:
        value = read(root, "argocd", "application", name)
        if value is None:
            continue
        validate_application(value, name)
        policy = dict(value.get("spec", {}).get("syncPolicy", {}))
        policy["automated"] = {**policy.get("automated", {}), "enabled": False}
        print(f"Pausing Application {name} with UID/resourceVersion guards...", flush=True)
        patch(
            root,
            "argocd",
            "application",
            value,
            {"op": "add", "path": "/spec/syncPolicy", "value": policy},
        )
    value = read(root, "perf-platform", "deployment", "control-plane")
    if value is not None:
        validate_deployment(value)
        print("Stopping owned Deployment perf-platform/control-plane for migration...", flush=True)
        patch(
            root,
            "perf-platform",
            "deployment",
            value,
            {"op": "add", "path": "/spec/replicas", "value": 0},
        )
    pods = json.loads(
        cluster.run([*kube(root, "perf-platform"), "get", "pods", "-l", SELECTOR, "-o", "json"])
    )
    names = [f"pod/{pod['metadata']['name']}" for pod in pods["items"]]
    if names:
        print("Waiting at most 180s for existing control-plane Pods to stop...", flush=True)
        cluster.run(
            [*kube(root, "perf-platform"), "wait", "--for=delete", *names, "--timeout=180s"]
        )
    verify_quiescent(root)
    print(
        "Control plane quiescent. GitOps root and child remain paused until explicit cutover.",
        flush=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["pause"])
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            pause()
        else:
            print(
                "Preview: ownership-check and pause root/child sync, scale the control plane to zero and wait (180s)."
            )
            print(
                "No credentials, Jobs or data are deleted. Add --execute to perform the maintenance pause."
            )
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Control-plane maintenance error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
