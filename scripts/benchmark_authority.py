"""Validate the explicit dedicated-cluster benchmark authority, never deploy it."""

import json
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import cluster, control_plane_registry, control_plane_runtime
    from .control_plane_release import DeploymentLock
else:
    import cluster
    import control_plane_registry
    import control_plane_runtime
    from control_plane_release import DeploymentLock

APPLICATION = "perfeng-local-benchmark-authority"
NAME = "perfeng-local-benchmark-controller"
GROUP = "rbac.authorization.k8s.io"
REPOSITORY = "https://github.com/stanimirivanov/perfeng-environment.git"
LABELS = {"app.kubernetes.io/managed-by": "perfeng-environment", "perfeng.io/environment": "local"}


def expected_objects() -> list[dict[str, Any]]:
    """Specify the reviewed controller ceiling independently of manifest input."""
    rules = [
        {"apiGroups": [group], "resources": resources, "verbs": verbs}
        for group, resources, verbs in [
            ("", ["namespaces"], ["get", "create"]),
            ("", ["nodes"], ["get", "list"]),
            ("", ["pods"], ["get", "list", "watch", "create", "delete"]),
            ("", ["events"], ["get", "list", "watch"]),
            ("", ["resourcequotas"], ["get", "create", "update"]),
            ("metrics.k8s.io", ["nodes"], ["get", "list"]),
            (
                GROUP,
                ["roles", "rolebindings", "clusterroles", "clusterrolebindings"],
                ["get", "create", "delete"],
            ),
        ]
    ]
    rules.append(
        {"nonResourceURLs": ["/api", "/api/*", "/apis", "/apis/*", "/version"], "verbs": ["get"]}
    )
    documents = []
    for kind in ("ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding"):
        metadata: dict[str, Any] = {"name": NAME, "labels": dict(LABELS)}
        if not kind.startswith("Cluster"):
            metadata["namespace"] = "perf-generators"
        document: dict[str, Any] = {"apiVersion": GROUP + "/v1", "kind": kind, "metadata": metadata}
        if kind == "ClusterRole":
            document["rules"] = rules
        elif kind == "Role":
            document["rules"] = [
                {
                    "apiGroups": [""],
                    "resources": ["serviceaccounts", "configmaps"],
                    "verbs": ["get", "create", "delete"],
                }
            ]
        else:
            document["roleRef"] = {
                "apiGroup": GROUP,
                "kind": kind.removesuffix("Binding"),
                "name": NAME,
            }
            document["subjects"] = [
                {"kind": "ServiceAccount", "name": "control-plane", "namespace": "perf-platform"}
            ]
        documents.append(document)
    return documents


def require_object(actual: dict[str, Any], expected: dict[str, Any]) -> None:
    """Accept API-managed metadata only; reject expanded authority or adoption."""
    metadata = actual.get("metadata", {})
    if (
        {key: value for key, value in actual.items() if key != "metadata"}
        != {key: value for key, value in expected.items() if key != "metadata"}
        or metadata.get("name") != NAME
        or metadata.get("namespace") != expected["metadata"].get("namespace")
        or any(metadata.get("labels", {}).get(key) != value for key, value in LABELS.items())
        or metadata.get("ownerReferences")
        or metadata.get("deletionTimestamp")
    ):
        raise ValueError(
            f"Benchmark authority {expected['kind']} differs from the dedicated-cluster ceiling"
        )


def validate_gitops(project: dict[str, Any], application: dict[str, Any]) -> None:
    """Require the separate source/destination/resource ceiling in desired or live objects."""
    for value in (project, application):
        metadata = value.get("metadata", {})
        if (
            value.get("apiVersion") != "argoproj.io/v1alpha1"
            or metadata.get("ownerReferences")
            or metadata.get("deletionTimestamp")
            or metadata.get("finalizers")
            or any(metadata.get("labels", {}).get(key) != wanted for key, wanted in LABELS.items())
        ):
            raise ValueError("Benchmark authority Project/Application ownership differs")

    def kinds(names: list[str]) -> list[dict[str, str]]:
        return [{"group": GROUP, "kind": name} for name in names]

    if (
        project.get("kind") != "AppProject"
        or project.get("metadata", {}).get("name") != APPLICATION
        or project.get("metadata", {}).get("namespace") != "argocd"
        or project.get("spec")
        != {
            "description": "Dedicated disposable kind benchmark controller authority; never shared or cloud",
            "sourceRepos": [REPOSITORY],
            "destinations": [
                {"server": "https://kubernetes.default.svc", "namespace": "perf-generators"}
            ],
            "clusterResourceWhitelist": kinds(["ClusterRole", "ClusterRoleBinding"]),
            "namespaceResourceWhitelist": kinds(["Role", "RoleBinding"]),
        }
        or application.get("kind") != "Application"
        or application.get("metadata", {}).get("name") != APPLICATION
        or application.get("metadata", {}).get("namespace") != "argocd"
        or application.get("spec")
        != {
            "project": APPLICATION,
            "source": {
                "repoURL": REPOSITORY,
                "targetRevision": "main",
                "path": "gitops/benchmark-authority",
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": "perf-generators",
            },
            "syncPolicy": {
                "automated": {"enabled": True, "prune": False, "selfHeal": False},
                "syncOptions": ["CreateNamespace=false"],
            },
        }
    ):
        raise ValueError(
            "Benchmark authority Project/Application exceeds the dedicated local boundary"
        )


def validate_configuration(root: Path = cluster.ROOT) -> None:
    """Check the four static resources and separate Project/Application offline."""
    directory = root / "gitops/environments/local"
    project = yaml.safe_load(
        (directory / "benchmark-authority-project.yaml").read_text(encoding="utf-8")
    )
    application = yaml.safe_load(
        (directory / "benchmark-authority-application.yaml").read_text(encoding="utf-8")
    )
    validate_gitops(project, application)
    kustomization = yaml.safe_load(
        (root / "gitops/benchmark-authority/kustomization.yaml").read_text(encoding="utf-8")
    )
    if kustomization != {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": ["rbac.yaml"],
    }:
        raise ValueError(
            "Benchmark authority Kustomization contains unreviewed resources or transforms"
        )
    objects = list(
        yaml.safe_load_all(
            (root / "gitops/benchmark-authority/rbac.yaml").read_text(encoding="utf-8")
        )
    )
    if len(objects) != 4:
        raise ValueError("Expected exactly four benchmark authority resources")
    for actual, expected in zip(objects, expected_objects(), strict=True):
        require_object(actual, expected)


def verify_target(root: Path, release: DeploymentLock) -> None:
    """Require the promoted benchmark's exact cluster incarnation before preparation."""
    kube = control_plane_registry.kubectl(root, namespace="perf-generators")
    manifest = json.loads(control_plane_runtime.registry_files(release)["registry.json"])
    targets = [entry["benchmark"] for entry in manifest["entries"] if "benchmark" in entry]
    identity = json.loads(cluster.run([*kube, "get", "namespace", "kube-system", "-o", "json"]))
    if (
        len(targets) != 1
        or targets[0]["clusterId"] != "perfeng-local"
        or targets[0]["clusterUid"] != identity.get("metadata", {}).get("uid")
    ):
        raise ValueError(
            "Benchmark authority target incarnation differs; do not rebind evidence or recreate the cluster"
        )


def verify(root: Path, release: DeploymentLock) -> None:
    """Read exact static RBAC and its GitOps boundary; never repair."""
    verify_target(root, release)
    kube = control_plane_registry.kubectl(root, namespace="argocd")
    project = json.loads(cluster.run([*kube, "get", "appproject", APPLICATION, "-o", "json"]))
    application = json.loads(cluster.run([*kube, "get", "application", APPLICATION, "-o", "json"]))
    validate_gitops(project, application)
    kube = control_plane_registry.kubectl(root, namespace="perf-generators")
    for expected in expected_objects():
        actual = json.loads(cluster.run([*kube, "get", expected["kind"], NAME, "-o", "json"]))
        require_object(actual, expected)
