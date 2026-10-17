"""Validate, bootstrap and inspect the local Argo CD GitOps control plane."""

from __future__ import annotations

import argparse
import base64
import json
import os
import uuid
from pathlib import Path
from typing import Any

import yaml

if __package__:
    from . import (
        benchmark_authority,
        cluster,
        control_plane_health,
        gitops_status,
        native_target_release,
        object_store,
        postgres,
        resource_metrics_health,
        resource_metrics_release,
    )
    from .control_plane_registry import valid_token
    from .control_plane_release import (
        CONTROL_PLANE_HTTP_PORT,
        DEPLOYMENT_REPOSITORY,
        DeploymentLock,
        load_lock,
    )
else:
    import benchmark_authority
    import cluster
    import control_plane_health
    import gitops_status
    import native_target_release
    import object_store
    import postgres
    import resource_metrics_health
    import resource_metrics_release
    from control_plane_registry import valid_token
    from control_plane_release import (
        CONTROL_PLANE_HTTP_PORT,
        DEPLOYMENT_REPOSITORY,
        DeploymentLock,
        load_lock,
    )

NAMESPACE = "argocd"
OWNER = "perfeng-environment"
INFRASTRUCTURE_OWNER = "k8s-infrastructure"
ROOT_APPLICATION = "perfeng-local-root"
ACTIONS = ("validate", "bootstrap", "health")
SAMPLE_SUT_APPLICATION = "perfeng-local-sample-sut"
POSTGRES_APPLICATION = "perfeng-local-postgres"
SEAWEEDFS_APPLICATION = "perfeng-local-seaweedfs"
CONTROL_PLANE_APPLICATION = "perfeng-local-control-plane"
CONTROL_PLANE_NAMESPACE = "perf-platform"
REPOSITORY_CREDENTIALS = "perfeng-github-repository-credentials"
REPOSITORY_PREFIX = "https://github.com/stanimirivanov"
REPOSITORY_URL = REPOSITORY_PREFIX + "/perfeng-environment.git"
TOKEN_ENVIRONMENT_VARIABLE = "PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN"
CONTROL_PLANE_REGISTRY_ITEMS = [
    {"key": "identities--environment.json", "path": "identities/environment.json"},
    {"key": "jobs--analysis.yaml", "path": "jobs/analysis.yaml"},
    {"key": "jobs--benchmark.json", "path": "jobs/benchmark.json"},
    {"key": "jobs--checkout.json", "path": "jobs/checkout.json"},
    {"key": "jobs--reporting.yaml", "path": "jobs/reporting.yaml"},
    {"key": "policies--checkout-smoke.json", "path": "policies/checkout-smoke.json"},
    {"key": "policies--pod-startup.json", "path": "policies/pod-startup.json"},
    {"key": "registry.json", "path": "registry.json"},
]


def kubectl(root: Path, *, namespace: str = NAMESPACE) -> list[str]:
    """Return the explicitly bound local-cluster command prefix."""
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


def load_yaml(path: Path) -> dict[str, Any]:
    """Load one mapping from a reviewed YAML file."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Invalid YAML document: {path.name}")
    return value


def validate_control_plane_application(
    application: dict[str, Any], release: DeploymentLock
) -> None:
    """Require the control-plane child to match the reviewed deployment lock."""
    metadata = application.get("metadata", {})
    spec = application.get("spec", {})
    if (
        application.get("apiVersion") != "argoproj.io/v1alpha1"
        or application.get("kind") != "Application"
        or set(metadata) != {"name", "namespace", "labels"}
        or metadata.get("name") != CONTROL_PLANE_APPLICATION
        or metadata.get("namespace") != NAMESPACE
        or metadata.get("labels")
        != {
            "app.kubernetes.io/managed-by": OWNER,
            "perfeng.io/environment": "local",
            "perfeng.io/workload": "control-plane",
        }
        or set(spec) != {"project", "sources", "destination", "syncPolicy"}
        or spec.get("project") != "perfeng-local"
        or spec.get("sources")
        != [
            {
                "repoURL": DEPLOYMENT_REPOSITORY,
                "targetRevision": release.deployment_revision,
                "path": release.chart_path,
                "helm": {
                    "releaseName": control_plane_health.RELEASE,
                    "valuesObject": {
                        "image": release.image,
                        "imagePullSecret": control_plane_health.IMAGE_PULL_SECRET,
                        "executionNamespace": control_plane_health.EXECUTION_NAMESPACE,
                        "httpPort": CONTROL_PLANE_HTTP_PORT,
                        "runtimeConfigMap": release.runtime_config_map,
                        "runtimeSecret": "perfeng-control-plane-runtime",
                        "authenticationSecret": "perfeng-control-plane-authentication",
                        "registryConfigMap": release.registry_config_map,
                        "registryItems": CONTROL_PLANE_REGISTRY_ITEMS,
                    },
                },
            },
            {
                "repoURL": DEPLOYMENT_REPOSITORY,
                "targetRevision": release.deployment_revision,
                "path": release.package_path,
            },
        ]
        or spec.get("destination")
        != {
            "server": "https://kubernetes.default.svc",
            "namespace": CONTROL_PLANE_NAMESPACE,
        }
        or spec.get("syncPolicy", {}).get("automated")
        != {"enabled": False, "prune": False, "selfHeal": False}
        or set(spec.get("syncPolicy", {})) != {"automated", "syncOptions"}
        or spec.get("syncPolicy", {}).get("syncOptions") != ["CreateNamespace=false"]
    ):
        raise ValueError("Invalid local control-plane Application")


def validate_postgres_application(application: dict[str, Any]) -> None:
    """Require conservative adoption of the existing PostgreSQL release."""
    metadata = application.get("metadata", {})
    spec = application.get("spec", {})
    if (
        application.get("apiVersion") != "argoproj.io/v1alpha1"
        or application.get("kind") != "Application"
        or set(metadata) != {"name", "namespace", "labels"}
        or metadata.get("name") != POSTGRES_APPLICATION
        or metadata.get("namespace") != NAMESPACE
        or metadata.get("labels")
        != {
            "app.kubernetes.io/managed-by": OWNER,
            "perfeng.io/environment": "local",
            "perfeng.io/workload": "postgres",
        }
        or set(spec) != {"project", "source", "destination", "syncPolicy"}
        or spec.get("project") != "perfeng-local"
        or spec.get("source")
        != {
            "repoURL": REPOSITORY_URL,
            "targetRevision": "main",
            "path": "charts/postgres",
            "helm": {"releaseName": postgres.RELEASE},
        }
        or spec.get("destination")
        != {"server": "https://kubernetes.default.svc", "namespace": postgres.NAMESPACE}
        or spec.get("syncPolicy", {}).get("automated")
        != {"enabled": True, "prune": False, "selfHeal": True}
        or set(spec.get("syncPolicy", {})) != {"automated", "syncOptions"}
        or spec.get("syncPolicy", {}).get("syncOptions") != ["CreateNamespace=false"]
    ):
        raise ValueError("Invalid local PostgreSQL Application")


def validate_seaweedfs_application(application: dict[str, Any]) -> None:
    """Require reviewed self-healing ownership of the existing SeaweedFS release."""
    metadata = application.get("metadata", {})
    spec = application.get("spec", {})
    if (
        application.get("apiVersion") != "argoproj.io/v1alpha1"
        or application.get("kind") != "Application"
        or set(metadata) != {"name", "namespace", "labels"}
        or metadata.get("name") != SEAWEEDFS_APPLICATION
        or metadata.get("namespace") != NAMESPACE
        or metadata.get("labels")
        != {
            "app.kubernetes.io/managed-by": OWNER,
            "perfeng.io/environment": "local",
            "perfeng.io/workload": "seaweedfs",
        }
        or set(spec) != {"project", "source", "destination", "syncPolicy"}
        or spec.get("project") != "perfeng-local"
        or spec.get("source")
        != {
            "repoURL": REPOSITORY_URL,
            "targetRevision": "main",
            "path": "charts/seaweedfs",
            "helm": {"releaseName": "seaweedfs"},
        }
        or spec.get("destination")
        != {"server": "https://kubernetes.default.svc", "namespace": object_store.NAMESPACE}
        or spec.get("syncPolicy", {}).get("automated")
        != {"enabled": True, "prune": False, "selfHeal": True}
        or set(spec.get("syncPolicy", {})) != {"automated", "syncOptions"}
        or spec.get("syncPolicy", {}).get("syncOptions") != ["CreateNamespace=false"]
    ):
        raise ValueError("Invalid local SeaweedFS Application")


def validate_project_sources(
    project_spec: dict[str, Any], applications: list[dict[str, Any]]
) -> None:
    """Require least-privilege source access for the reviewed child Applications."""
    expected: set[str] = set()
    for application in applications:
        spec = application.get("spec")
        if not isinstance(spec, dict) or ("source" in spec) == ("sources" in spec):
            raise ValueError("Invalid local Application source repository")
        sources = [spec["source"]] if "source" in spec else spec["sources"]
        if not isinstance(sources, list) or not sources:
            raise ValueError("Invalid local Application source repository")
        for source in sources:
            repository = source.get("repoURL") if isinstance(source, dict) else None
            if not isinstance(repository, str):
                raise ValueError("Invalid local Application source repository")
            expected.add(repository)

    configured = project_spec.get("sourceRepos")
    if (
        not isinstance(configured, list)
        or not all(isinstance(repository, str) for repository in configured)
        or len(configured) != len(set(configured))
        or set(configured) != expected
    ):
        raise ValueError("Invalid local AppProject source repositories")


def validate_configuration(root: Path = cluster.ROOT) -> None:
    """Validate the consumer-owned local GitOps objects without external access."""
    application = load_yaml(root / "gitops/bootstrap/local-root-application.yaml")
    metadata = application.get("metadata", {})
    spec = application.get("spec", {})
    if (
        application.get("kind") != "Application"
        or metadata.get("name") != ROOT_APPLICATION
        or metadata.get("namespace") != NAMESPACE
        or metadata.get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER
        or spec.get("source")
        != {
            "repoURL": REPOSITORY_URL,
            "targetRevision": "main",
            "path": "gitops/environments/local",
        }
        or spec.get("destination")
        != {"server": "https://kubernetes.default.svc", "namespace": NAMESPACE}
        or spec.get("syncPolicy", {}).get("automated")
        != {"enabled": True, "prune": False, "selfHeal": False}
    ):
        raise ValueError("Invalid local root Application")

    root_kustomization = load_yaml(root / "gitops/environments/local/kustomization.yaml")
    project = load_yaml(root / "gitops/environments/local/project.yaml")
    sample_sut = load_yaml(root / "gitops/environments/local/sample-sut-application.yaml")
    postgres_application = load_yaml(root / "gitops/environments/local/postgres-application.yaml")
    seaweedfs_application = load_yaml(root / "gitops/environments/local/seaweedfs-application.yaml")
    control_plane = load_yaml(root / "gitops/environments/local/control-plane-application.yaml")
    native_search = load_yaml(root / "gitops/environments/local/native-search-application.yaml")
    native_selection = native_target_release.load_selection(
        root / "deployments/local-native-target.json"
    )
    release = load_lock(root / "deployments/local-control-plane.json")
    project_spec = project.get("spec", {})
    validate_project_sources(
        project_spec,
        [sample_sut, postgres_application, seaweedfs_application, control_plane, native_search],
    )
    if (
        root_kustomization.get("resources")
        != [
            "namespaces.yaml",
            "project.yaml",
            "resource-metrics-project.yaml",
            "benchmark-authority-project.yaml",
            "sample-sut-application.yaml",
            "postgres-application.yaml",
            "seaweedfs-application.yaml",
            "control-plane-application.yaml",
            "native-search-application.yaml",
            "resource-metrics-application.yaml",
            "benchmark-authority-application.yaml",
        ]
        or project.get("kind") != "AppProject"
        or project.get("metadata", {}).get("name") != "perfeng-local"
        or project.get("metadata", {}).get("annotations", {}).get("argocd.argoproj.io/sync-wave")
        != "-1"
        or set(project_spec)
        != {
            "description",
            "sourceRepos",
            "destinations",
            "namespaceResourceWhitelist",
        }
        or project_spec.get("destinations")
        != [
            {"server": "https://kubernetes.default.svc", "namespace": namespace}
            for namespace in ("monitoring", "perf-generators", "perf-platform", "perf-sut")
        ]
        or project_spec.get("namespaceResourceWhitelist") != [{"group": "*", "kind": "*"}]
    ):
        raise ValueError("Invalid local root Kustomize configuration")
    sample_metadata = sample_sut.get("metadata", {})
    sample_spec = sample_sut.get("spec", {})
    if (
        sample_sut.get("apiVersion") != "argoproj.io/v1alpha1"
        or sample_sut.get("kind") != "Application"
        or set(sample_metadata) != {"name", "namespace", "labels"}
        or sample_metadata.get("name") != SAMPLE_SUT_APPLICATION
        or sample_metadata.get("namespace") != NAMESPACE
        or sample_metadata.get("labels")
        != {
            "app.kubernetes.io/managed-by": OWNER,
            "perfeng.io/environment": "local",
            "perfeng.io/workload": "sample-sut",
        }
        or set(sample_spec) != {"project", "source", "destination", "syncPolicy"}
        or sample_spec.get("project") != "perfeng-local"
        or sample_spec.get("source")
        != {
            "repoURL": REPOSITORY_URL,
            "targetRevision": "main",
            "path": "charts/sample-sut",
            "helm": {"releaseName": "sample-sut"},
        }
        or sample_spec.get("destination")
        != {"server": "https://kubernetes.default.svc", "namespace": "perf-sut"}
        or sample_spec.get("syncPolicy", {}).get("automated")
        != {"enabled": True, "prune": False, "selfHeal": True}
        or set(sample_spec.get("syncPolicy", {})) != {"automated", "syncOptions"}
        or sample_spec.get("syncPolicy", {}).get("syncOptions") != ["CreateNamespace=false"]
    ):
        raise ValueError("Invalid local sample SUT Application")
    validate_postgres_application(postgres_application)
    validate_seaweedfs_application(seaweedfs_application)
    validate_control_plane_application(control_plane, release)
    native_target_release.validate_application(native_search, native_selection)
    native_target_release.expected_files(root)
    resource_metrics_release.validate_configuration(root)
    benchmark_authority.validate_configuration(root)


def credential_document(token: str) -> dict[str, Any]:
    """Build immutable private-repository credentials for the GitHub owner prefix."""
    if not valid_token(token):
        raise ValueError("Invalid private-repository credential input")
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": REPOSITORY_CREDENTIALS,
            "namespace": NAMESPACE,
            "labels": {
                "argocd.argoproj.io/secret-type": "repo-creds",
                "app.kubernetes.io/managed-by": OWNER,
                "perfeng.io/purpose": "private-repository-read",
            },
        },
        "immutable": True,
        "type": "Opaque",
        "stringData": {
            "type": "git",
            "url": REPOSITORY_PREFIX,
            "username": "token",
            "password": token,
        },
    }


def validate_stored_credentials(value: dict[str, Any]) -> None:
    """Reject an existing credential Secret that is not the exact owned shape."""
    expected = credential_document("x" * 20)
    try:
        data = {
            key: base64.b64decode(encoded, validate=True).decode("utf-8")
            for key, encoded in value["data"].items()
        }
        if (
            value["metadata"]["name"] != REPOSITORY_CREDENTIALS
            or value["metadata"]["namespace"] != NAMESPACE
            or value["metadata"]["labels"] != expected["metadata"]["labels"]
            or value["immutable"] is not True
            or value["type"] != "Opaque"
            or data.get("type") != "git"
            or data.get("url") != REPOSITORY_PREFIX
            or data.get("username") != "token"
            or set(data) != {"type", "url", "username", "password"}
            or not valid_token(data["password"])
        ):
            raise ValueError()
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise ValueError(
            f"Invalid owned immutable Secret {REPOSITORY_CREDENTIALS}; "
            "replace it through explicit recovery"
        ) from None


def ensure_credentials(kube: list[str], *, create: bool) -> None:
    """Validate and reuse Git credentials, or create them from the environment."""
    current = cluster.run(
        [*kube, "get", "secret", REPOSITORY_CREDENTIALS, "--ignore-not-found", "-o", "json"]
    )
    if current.strip():
        validate_stored_credentials(json.loads(current))
        print("Reusing existing Argo CD repository credentials (not rotated).", flush=True)
        return
    if not create:
        raise ValueError("Argo CD repository credentials are missing; run bootstrap first")
    token = os.environ.get(TOKEN_ENVIRONMENT_VARIABLE)
    if token is None:
        raise ValueError(f"{TOKEN_ENVIRONMENT_VARIABLE} is required to read the private repository")
    print(f"Using {TOKEN_ENVIRONMENT_VARIABLE} from the environment.", flush=True)
    cluster.run(
        [*kube, "create", "-f", "-"],
        input_data=json.dumps(credential_document(token)).encode(),
    )


def verify_namespace(kube: list[str]) -> None:
    """Require the infrastructure-owned GitOps namespace before composition."""
    current = cluster.run(
        [*kube, "get", "namespace", NAMESPACE, "--ignore-not-found", "-o", "json"]
    )
    if not current.strip():
        raise ValueError("Argo CD namespace is missing; run infrastructure bootstrap first")
    labels = json.loads(current).get("metadata", {}).get("labels", {})
    if (
        labels.get("app.kubernetes.io/managed-by") != INFRASTRUCTURE_OWNER
        or labels.get("app.kubernetes.io/name") != NAMESPACE
        or labels.get("app.kubernetes.io/part-of") != "kubernetes-substrate"
    ):
        raise ValueError("Existing argocd namespace is not owned by k8s-infrastructure")


def ensure_root(kube: list[str], root: Path, *, apply: bool) -> None:
    """Ownership-check and optionally apply the local root Application."""
    current = cluster.run(
        [*kube, "get", "application", ROOT_APPLICATION, "--ignore-not-found", "-o", "json"]
    )
    if current.strip():
        labels = json.loads(current).get("metadata", {}).get("labels", {})
        if labels.get("app.kubernetes.io/managed-by") != OWNER:
            raise ValueError("Existing root Application is not owned by perfeng-environment")
    elif not apply:
        raise ValueError("Local root Application is missing; run bootstrap first")
    if apply:
        content = (root / "gitops/bootstrap/local-root-application.yaml").read_bytes()
        cluster.run([*kube, "apply", "-f", "-"], input_data=content)


def execute(action: str, root: Path = cluster.ROOT) -> None:
    """Bootstrap or health-check consumer-owned GitOps composition."""
    if action not in {"bootstrap", "health"}:
        raise ValueError("Unknown executable GitOps action")
    validate_configuration(root)
    cluster.verify_context(root)
    kube = kubectl(root)
    mutating = action == "bootstrap"
    verify_namespace(kube)
    ensure_credentials(kube, create=mutating)
    ensure_root(kube, root, apply=mutating)
    gitops_status.wait_for_application(kube, ROOT_APPLICATION, require_health=action != "bootstrap")
    gitops_status.wait_for_application(kube, SAMPLE_SUT_APPLICATION)
    gitops_status.wait_for_application(kube, POSTGRES_APPLICATION)
    gitops_status.wait_for_application(kube, SEAWEEDFS_APPLICATION)
    if action == "bootstrap":
        postgres.execute("health", root)
        print("Local Argo CD GitOps action completed: bootstrap", flush=True)
        return
    gitops_status.wait_for_application(kube, CONTROL_PLANE_APPLICATION)
    gitops_status.wait_for_application(kube, benchmark_authority.APPLICATION)
    gitops_status.wait_for_application(kube, native_target_release.APPLICATION)
    gitops_status.wait_for_application(kube, resource_metrics_release.APPLICATION)
    resource_metrics_health.verify(root)
    cluster.run(
        [
            *kubectl(root, namespace="perf-sut"),
            "rollout",
            "status",
            "deployment/sample-sut",
            "--timeout=180s",
        ]
    )
    postgres.execute("health", root)
    object_store.execute("health", uuid.uuid4().hex, root)
    control_plane_health.verify(root)
    print(f"Local Argo CD GitOps action completed: {action}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "validate":
            validate_configuration()
            print("Validated local GitOps Applications and composition.")
        elif args.execute:
            execute(args.action)
        else:
            print(f"Preview: {args.action} the local consumer GitOps composition.")
            if args.action == "bootstrap":
                print(f"Missing Git credentials use {TOKEN_ENVIRONMENT_VARIABLE}.")
            print("Preview only. Add --execute to access the network or local cluster.")
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        json.JSONDecodeError,
        yaml.YAMLError,
    ) as error:
        print(f"Local GitOps error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
