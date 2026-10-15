"""Validate the consumer's immutable Metrics Server selection without cluster access."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import Encoding

if __package__:
    from . import infrastructure_lock, infrastructure_release
else:
    import infrastructure_lock
    import infrastructure_release

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = Path("gitops/environments/local")
APPLICATION = "perfeng-local-resource-metrics"
PROJECT = APPLICATION
NAMESPACE = "monitoring"
CHART = "charts/resource-metrics"
IMAGE = "registry.k8s.io/metrics-server/metrics-server:v0.9.0@sha256:d9862115e7c7881280d3d75ca26bda8ffc0fc213315979575bf23ce9826205c0"
SECRET = "substrate-resource-metrics-tls"
LABELS = {"app.kubernetes.io/managed-by": "perfeng-environment", "perfeng.io/environment": "local"}
NAMESPACE_KINDS = [
    {"group": "", "kind": "ServiceAccount"},
    {"group": "", "kind": "Service"},
    {"group": "apps", "kind": "Deployment"},
    {"group": "rbac.authorization.k8s.io", "kind": "RoleBinding"},
]
CLUSTER_KINDS = [
    {"group": "rbac.authorization.k8s.io", "kind": "ClusterRole"},
    {"group": "rbac.authorization.k8s.io", "kind": "ClusterRoleBinding"},
    {"group": "apiregistration.k8s.io", "kind": "APIService"},
]


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a repository manifest, refusing non-object desired state."""
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("Resource metrics manifest must be an object")
    return value


def public_ca(value: Any) -> str:
    """Require one canonical public, self-signed CA; live health checks validity dates.

    Offline verification deliberately uses no wall clock. Private keys, extra PEM
    blocks and invalid trust anchors are never accepted as deployment values.
    """
    if not isinstance(value, str) or not 64 <= len(value) <= 16384:
        raise ValueError("Resource metrics requires a public serving CA")
    try:
        content = base64.b64decode(value, validate=True)
        certificate = x509.load_pem_x509_certificate(content)
        if (
            base64.b64encode(content).decode("ascii") != value
            or certificate.public_bytes(Encoding.PEM) != content
            or not certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
        ):
            raise ValueError()
        certificate.verify_directly_issued_by(certificate)
    except (ValueError, TypeError, x509.ExtensionNotFound, InvalidSignature, UnsupportedAlgorithm):
        raise ValueError(
            "Resource metrics serving CA is not one canonical self-signed CA"
        ) from None
    return value


def require_document(actual: Mapping[str, Any], expected: Mapping[str, Any], label: str) -> None:
    """Compare desired specs and owned metadata, allowing API-managed metadata/status only."""
    if not isinstance(actual, Mapping) or not isinstance(actual.get("metadata"), Mapping):
        raise ValueError(f"Resource metrics {label} metadata is invalid")
    metadata = actual["metadata"]
    if not isinstance(metadata.get("labels", {}), Mapping) or not isinstance(
        metadata.get("annotations", {}), Mapping
    ):
        raise ValueError(f"Resource metrics {label} labels/annotations are invalid")
    wanted = expected["metadata"]
    if (
        actual.get("apiVersion") != expected["apiVersion"]
        or actual.get("kind") != expected["kind"]
        or actual.get("spec") != expected["spec"]
        or metadata.get("name") != wanted["name"]
        or metadata.get("namespace") != wanted["namespace"]
        or metadata.get("finalizers")
        or any(metadata.get("labels", {}).get(k) != v for k, v in wanted["labels"].items())
        or any(
            metadata.get("annotations", {}).get(k) != v
            for k, v in wanted.get("annotations", {}).items()
        )
    ):
        raise ValueError(f"Resource metrics {label} differs from the reviewed selection")


def validate_configuration(root: Path = ROOT) -> dict[str, Any]:
    """Require exact source, placement, public TLS and separate least-privilege project offline.

    The checked-in Application is authoritative for the public CA; it is not
    duplicated in a second lock. Source revision must equal the infrastructure lock.
    """
    application = read_yaml(root / DIRECTORY / "resource-metrics-application.yaml")
    source = application["spec"]["source"]
    values = source["helm"]["valuesObject"]
    expected_values = {
        "tls": {"secretName": SECRET, "caBundle": public_ca(values["tls"]["caBundle"])},
        "nodeSelector": {"kubernetes.io/os": "linux", "workload": "control-plane"},
        "tolerations": [
            {
                "key": "node-role.kubernetes.io/control-plane",
                "operator": "Exists",
                "effect": "NoSchedule",
            }
        ],
    }
    require_document(
        application,
        {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {
                "name": APPLICATION,
                "namespace": "argocd",
                "labels": LABELS | {"perfeng.io/workload": "resource-metrics"},
            },
            "spec": {
                "project": PROJECT,
                "source": {
                    "repoURL": infrastructure_lock.INFRASTRUCTURE_REPOSITORY,
                    "targetRevision": infrastructure_lock.load_lock(
                        root / "deployments/local-infrastructure.json"
                    ).revision,
                    "path": CHART,
                    "helm": {"releaseName": "resource-metrics", "valuesObject": expected_values},
                },
                "destination": {"server": "https://kubernetes.default.svc", "namespace": NAMESPACE},
                "syncPolicy": {
                    "automated": {"enabled": True, "prune": False, "selfHeal": False},
                    "syncOptions": ["CreateNamespace=false"],
                },
            },
        },
        "Application",
    )
    require_document(
        read_yaml(root / DIRECTORY / "resource-metrics-project.yaml"),
        {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "AppProject",
            "metadata": {
                "name": PROJECT,
                "namespace": "argocd",
                "annotations": {"argocd.argoproj.io/sync-wave": "-1"},
                "labels": LABELS,
            },
            "spec": {
                "description": "Verified-TLS local resource metrics; separate from Perfeng workload authority",
                "sourceRepos": [infrastructure_lock.INFRASTRUCTURE_REPOSITORY],
                "destinations": [
                    {"server": "https://kubernetes.default.svc", "namespace": name}
                    for name in (NAMESPACE, "kube-system")
                ],
                "clusterResourceWhitelist": CLUSTER_KINDS,
                "namespaceResourceWhitelist": NAMESPACE_KINDS,
            },
        },
        "AppProject",
    )
    return application


def validate_rendered(objects: list[dict[str, Any]], values: dict[str, Any]) -> None:
    """Check consumer placement/trust and the exact eight reviewed resource identities."""
    expected = {
        ("ServiceAccount", "metrics-server", NAMESPACE),
        ("Deployment", "metrics-server", NAMESPACE),
        ("Service", "metrics-server", NAMESPACE),
        ("APIService", "v1beta1.metrics.k8s.io", None),
        ("ClusterRole", "substrate-metrics-server", None),
        ("ClusterRoleBinding", "substrate-metrics-server", None),
        ("ClusterRoleBinding", "substrate-metrics-server-auth-delegator", None),
        ("RoleBinding", "substrate-metrics-server-auth-reader", "kube-system"),
    }
    if (
        len(objects) != len(expected)
        or {(o["kind"], o["metadata"]["name"], o["metadata"].get("namespace")) for o in objects}
        != expected
    ):
        raise ValueError("Resource metrics rendered resource scope differs")
    api = next(o for o in objects if o["kind"] == "APIService")["spec"]
    pod = next(o for o in objects if o["kind"] == "Deployment")["spec"]["template"]["spec"]
    container = pod["containers"][0]
    if (
        api.get("insecureSkipTLSVerify", False) is not False
        or api.get("caBundle") != values["tls"]["caBundle"]
        or api.get("service") != {"name": "metrics-server", "namespace": NAMESPACE, "port": 443}
        or pod["nodeSelector"] != values["nodeSelector"]
        or pod["tolerations"] != values["tolerations"]
        or container["image"] != IMAGE
        or "--kubelet-insecure-tls" in container["args"]
        or "--kubelet-certificate-authority=/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
        not in container["args"]
        or next(v for v in pod["volumes"] if v["name"] == "serving")["secret"]["secretName"]
        != SECRET
    ):
        raise ValueError("Resource metrics rendered TLS, image or placement differs")


def validate(root: Path = ROOT, infrastructure_root: Path | None = None) -> None:
    """Read only selected Git objects and render that chart offline with the real public values."""
    application = validate_configuration(root)
    lock = infrastructure_release.validate(
        root / "deployments/local-infrastructure.json",
        infrastructure_root or root.parent / "k8s-infrastructure",
    )
    read = infrastructure_release.revision_reader(
        infrastructure_root or root.parent / "k8s-infrastructure", lock.revision
    )
    release = json.loads(read("addons/resource-metrics/release.json"))
    if (
        release.get("image") != IMAGE
        or release.get("version") != "0.9.0"
        or release.get("chart") != CHART
    ):
        raise ValueError("Pinned resource metrics release identity differs")
    values = application["spec"]["source"]["helm"]["valuesObject"]
    with tempfile.TemporaryDirectory(prefix="perfeng-metrics-chart-") as directory:
        chart = Path(directory)
        for name in (
            "Chart.yaml",
            "values.yaml",
            "values.schema.json",
            "templates/workload.yaml",
            "templates/rbac.yaml",
        ):
            target = chart / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(read(f"{CHART}/{name}"))
        identity = read_yaml(chart / "Chart.yaml")
        if identity.get("version") != "1.0.0" or identity.get("appVersion") != "0.9.0":
            raise ValueError("Pinned resource metrics chart version differs")
        for arguments in (
            ["lint", str(chart), "--strict", "-f", "-"],
            [
                "template",
                "resource-metrics",
                str(chart),
                "--namespace",
                NAMESPACE,
                "--kube-version",
                "1.35.0",
                "-f",
                "-",
            ],
        ):
            completed = subprocess.run(
                ["helm", *arguments],
                input=json.dumps(values),
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=60,
                check=False,
            )
            if completed.returncode:
                raise ValueError("Helm rejected the pinned resource metrics chart/values")
        validate_rendered(list(yaml.safe_load_all(completed.stdout)), values)


def main() -> int:
    """Expose offline pinned source/render verification, never deployment or certificate creation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["validate"])
    parser.add_argument("--infrastructure-root", type=Path)
    args = parser.parse_args()
    try:
        validate(infrastructure_root=args.infrastructure_root)
    except (
        OSError,
        ValueError,
        TypeError,
        KeyError,
        subprocess.SubprocessError,
        yaml.YAMLError,
    ) as error:
        print(f"Resource metrics selection error: {error}")
        return 1
    print(
        "Validated pinned resource metrics chart, public CA, placement and restricted project offline."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
