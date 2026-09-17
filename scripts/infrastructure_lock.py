"""Parse and validate the local Kubernetes infrastructure lock."""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

if __package__:
    from .workspace import unique_object
else:
    from workspace import unique_object

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "deployments/local-infrastructure.json"
INFRASTRUCTURE_REPOSITORY = "https://github.com/stanimirivanov/k8s-infrastructure.git"
REVISION = re.compile(r"[0-9a-f]{40}")
PROFILE = re.compile(r"[a-z0-9-]+/[a-z0-9-]+")
REPOSITORY_PATH = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
NODE_IMAGE = re.compile(r"kindest/node:v[0-9]+\.[0-9]+\.[0-9]+@sha256:[0-9a-f]{64}")
ARGOCD_VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
DORMANT_MESSAGE = (
    "Live execution remains owned by perfeng-environment until the reviewed ownership cutover"
)


@dataclass(frozen=True)
class InfrastructureLock:
    """Immutable source and compatibility identities for the local substrate."""

    revision: str
    profile: str
    cluster_configuration: str
    cluster_lifecycle: str
    argocd_bootstrap: str
    argocd_lifecycle: str
    cluster_name: str
    context: str
    kubeconfig: str
    node_image: str
    argocd_version: str
    argocd_namespace: str


def mapping(value: Any, fields: set[str], location: str) -> Mapping[str, Any]:
    """Return an exact string-keyed object or reject it."""
    if (
        not isinstance(value, Mapping)
        or any(not isinstance(key, str) for key in value)
        or set(value) != fields
    ):
        raise ValueError(f"Invalid {location} fields")
    return value


def string(value: Any, pattern: re.Pattern[str], location: str) -> str:
    """Return a string matching the complete required syntax."""
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise ValueError(f"Invalid {location}")
    return value


def portable_path(value: Any, location: str) -> str:
    """Return a normalized, non-traversing repository-relative path."""
    if not isinstance(value, str):
        raise ValueError(f"Invalid {location}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or ".." in path.parts
        or str(path) != value
        or REPOSITORY_PATH.fullmatch(value) is None
    ):
        raise ValueError(f"Invalid {location}")
    return value


def load_lock(path: Path = DEFAULT_LOCK) -> InfrastructureLock:
    """Load and strictly validate the local-infrastructure lock."""
    content = path.resolve(strict=True).read_text(encoding="utf-8")
    value = json.loads(content, object_pairs_hook=unique_object)
    root = mapping(value, {"schemaVersion", "source", "profile", "compatibility"}, "lock")
    if type(root["schemaVersion"]) is not int or root["schemaVersion"] != 1:
        raise ValueError("Unsupported infrastructure lock version")

    source = mapping(root["source"], {"repository", "revision"}, "source")
    if source["repository"] != INFRASTRUCTURE_REPOSITORY:
        raise ValueError("Invalid infrastructure source repository")
    revision = string(source["revision"], REVISION, "infrastructure revision")

    profile = mapping(
        root["profile"],
        {
            "name",
            "clusterConfiguration",
            "clusterLifecycle",
            "argoCDBootstrap",
            "argoCDLifecycle",
        },
        "profile",
    )
    profile_name = string(profile["name"], PROFILE, "infrastructure profile")
    cluster_configuration = portable_path(
        profile["clusterConfiguration"], "cluster configuration path"
    )
    cluster_lifecycle = portable_path(profile["clusterLifecycle"], "cluster lifecycle path")
    argocd_bootstrap = portable_path(profile["argoCDBootstrap"], "Argo CD bootstrap path")
    argocd_lifecycle = portable_path(profile["argoCDLifecycle"], "Argo CD lifecycle path")

    compatibility = mapping(
        root["compatibility"],
        {
            "clusterName",
            "context",
            "kubeconfig",
            "nodeImage",
            "argoCDVersion",
            "argoCDNamespace",
        },
        "compatibility",
    )
    cluster_name = string(compatibility["clusterName"], re.compile(r"[a-z0-9-]+"), "cluster name")
    context = string(compatibility["context"], re.compile(r"kind-[a-z0-9-]+"), "context")
    kubeconfig = portable_path(compatibility["kubeconfig"], "kubeconfig path")
    node_image = string(compatibility["nodeImage"], NODE_IMAGE, "node image")
    argocd_version = string(compatibility["argoCDVersion"], ARGOCD_VERSION, "Argo CD version")
    argocd_namespace = string(
        compatibility["argoCDNamespace"], re.compile(r"[a-z0-9-]+"), "Argo CD namespace"
    )
    if context != f"kind-{cluster_name}":
        raise ValueError("Infrastructure context differs from the cluster name")
    if profile_name != f"local-kind/{cluster_name}":
        raise ValueError("Infrastructure profile differs from the cluster name")
    if kubeconfig != f".local/{cluster_name}.kubeconfig":
        raise ValueError("Infrastructure kubeconfig differs from the cluster name")
    profile_root = f"clusters/{profile_name}"
    if (
        cluster_configuration != f"{profile_root}/cluster-config.yaml"
        or cluster_lifecycle != "scripts/local_kind.py"
        or argocd_bootstrap != f"{profile_root}/addons/argocd/kustomization.yaml"
        or argocd_lifecycle != "scripts/argocd.py"
    ):
        raise ValueError("Infrastructure source paths differ from the selected profile")

    return InfrastructureLock(
        revision=revision,
        profile=profile_name,
        cluster_configuration=cluster_configuration,
        cluster_lifecycle=cluster_lifecycle,
        argocd_bootstrap=argocd_bootstrap,
        argocd_lifecycle=argocd_lifecycle,
        cluster_name=cluster_name,
        context=context,
        kubeconfig=kubeconfig,
        node_image=node_image,
        argocd_version=argocd_version,
        argocd_namespace=argocd_namespace,
    )


def constants(content: bytes, location: str) -> dict[str, Any]:
    """Read literal module constants without importing or executing source."""
    try:
        tree = ast.parse(content.decode("utf-8"), filename=location)
    except (UnicodeError, SyntaxError) as error:
        raise ValueError(f"Invalid Python source: {location}") from error
    result = {}
    for statement in tree.body:
        if (
            isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
        ):
            try:
                result[statement.targets[0].id] = ast.literal_eval(statement.value)
            except (ValueError, TypeError):
                continue
    return result


def validate_source(lock: InfrastructureLock, read: Callable[[str], bytes]) -> None:
    """Require the pinned profile to preserve the local compatibility boundary."""
    cluster = yaml.safe_load(read(lock.cluster_configuration))
    if not isinstance(cluster, Mapping):
        raise ValueError("Pinned cluster configuration is not a mapping")
    expected_nodes = [
        {
            "role": "control-plane",
            "image": lock.node_image,
            "labels": {"workload": "control-plane"},
        },
        {
            "role": "worker",
            "image": lock.node_image,
            "labels": {
                "workload": "performance-generator",
                "perfeng.io/node-type": "generator",
            },
        },
        {
            "role": "worker",
            "image": lock.node_image,
            "labels": {"workload": "sut", "perfeng.io/node-type": "sut"},
        },
    ]
    if (
        cluster.get("apiVersion") != "kind.x-k8s.io/v1alpha4"
        or cluster.get("kind") != "Cluster"
        or cluster.get("name") != lock.cluster_name
        or cluster.get("networking") != {"apiServerAddress": "127.0.0.1"}
        or cluster.get("nodes") != expected_nodes
    ):
        raise ValueError("Pinned local-kind profile differs from the environment contract")

    cluster_constants = constants(read(lock.cluster_lifecycle), lock.cluster_lifecycle)
    if (
        cluster_constants.get("NAME") != lock.cluster_name
        or cluster_constants.get("NODE_IMAGE") != lock.node_image
        or cluster_constants.get("ACTIONS") != ("up", "ensure", "health", "down")
        or cluster_constants.get("DORMANT_MESSAGE") != DORMANT_MESSAGE
    ):
        raise ValueError("Pinned cluster lifecycle differs from the dormant environment contract")

    kustomization = yaml.safe_load(read(lock.argocd_bootstrap))
    expected_manifest = (
        "https://raw.githubusercontent.com/argoproj/argo-cd/"
        f"{lock.argocd_version}/manifests/install.yaml"
    )
    if not isinstance(kustomization, Mapping) or dict(kustomization) != {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "namespace": lock.argocd_namespace,
        "resources": ["namespace.yaml", expected_manifest],
    }:
        raise ValueError("Pinned Argo CD bootstrap differs from the environment contract")

    namespace_path = str(PurePosixPath(lock.argocd_bootstrap).with_name("namespace.yaml"))
    namespace = yaml.safe_load(read(namespace_path))
    expected_namespace = {
        "apiVersion": "v1",
        "kind": "Namespace",
        "metadata": {
            "name": lock.argocd_namespace,
            "labels": {
                "app.kubernetes.io/name": lock.argocd_namespace,
                "app.kubernetes.io/managed-by": "k8s-infrastructure",
                "app.kubernetes.io/part-of": "kubernetes-substrate",
            },
        },
    }
    if not isinstance(namespace, Mapping) or dict(namespace) != expected_namespace:
        raise ValueError("Pinned Argo CD namespace differs from the infrastructure contract")

    argocd_constants = constants(read(lock.argocd_lifecycle), lock.argocd_lifecycle)
    if (
        argocd_constants.get("NAMESPACE") != lock.argocd_namespace
        or argocd_constants.get("OWNER") != "k8s-infrastructure"
        or argocd_constants.get("ACTIONS") != ("validate", "bootstrap", "health")
        or argocd_constants.get("DORMANT_MESSAGE") != DORMANT_MESSAGE
    ):
        raise ValueError("Pinned Argo CD lifecycle differs from the dormant environment contract")
