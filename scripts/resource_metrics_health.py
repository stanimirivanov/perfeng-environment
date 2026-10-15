"""Check the selected metrics owner and TLS binding, then delegate generic health."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

if __package__:
    from . import cluster, infrastructure_runtime
    from . import resource_metrics_release as selection
else:
    import cluster
    import infrastructure_runtime
    import resource_metrics_release as selection


def verify(root: Path = selection.ROOT) -> None:
    """Require the selected Application/Project and API CA before read-only provider health.

    No Secret data is read and no resource is applied, refreshed or repaired.
    Available metrics do not grant benchmark admission or scientific eligibility.
    """
    expected = selection.validate_configuration(root)
    cluster.verify_context(root)
    kube = [
        "kubectl",
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--context",
        cluster.CONTEXT,
        "--namespace",
        "argocd",
        "--request-timeout=10s",
    ]
    for kind, name, desired in (
        ("application", selection.APPLICATION, expected),
        (
            "appproject",
            selection.PROJECT,
            selection.read_yaml(root / selection.DIRECTORY / "resource-metrics-project.yaml"),
        ),
    ):
        value = json.loads(cluster.run([*kube, "get", kind, name, "-o", "json"]))
        selection.require_document(value, desired, kind)
    api = json.loads(
        cluster.run(
            [*kube, "get", "apiservice", "v1beta1.metrics.k8s.io", "-o", "json"],
        )
    )
    spec = api["spec"]
    if (
        spec.get("caBundle")
        != expected["spec"]["source"]["helm"]["valuesObject"]["tls"]["caBundle"]
        or spec.get("insecureSkipTLSVerify", False) is not False
        or spec.get("service")
        != {"name": "metrics-server", "namespace": selection.NAMESPACE, "port": 443}
    ):
        raise ValueError("Live Metrics API TLS binding differs from the reviewed selection")
    if infrastructure_runtime.invoke_provider("metrics", "health", execute=True) != 0:
        raise ValueError(
            "Selected resource metrics provider health failed; inspect its named phase"
        )
    print(
        "Selected resource metrics owner/TLS and fresh node usage verified; qualification remains separate."
    )


def main() -> int:
    """Default to an offline preview; only --execute reads the dedicated cluster."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        if args.execute:
            verify()
        else:
            selection.validate_configuration()
            print(
                "Preview only: verify selected metrics owner/TLS and delegate provider health; no cluster access."
            )
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as error:
        print(f"Resource metrics health error: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
