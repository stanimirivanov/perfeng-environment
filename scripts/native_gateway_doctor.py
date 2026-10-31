"""Collect independent native prerequisites with safe, actionable classifications."""

from __future__ import annotations

import socket
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from scripts import cluster
from scripts import native_connectivity as connectivity
from scripts import native_gateway_host as host
from scripts.native_gateway_tool import GatewayError, installed

PORTS = (18080, 18333, 18081, 8443, 9443, 10443)


@dataclass(frozen=True)
class Result:
    """One diagnostic observation; trust actions are distinct from startup-blocking failures."""

    name: str
    status: str
    detail: str


def port_available(port: int) -> None:
    """Detect conflicts without connecting to, adopting or stopping the existing listener."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
    except OSError:
        raise GatewayError(
            "PORT_CONFLICT",
            f"Loopback port {port} is unavailable",
            f"inspect Get-NetTCPConnection -LocalPort {port}; stop only your reviewed session",
        ) from None


def cluster_ready(root: Path) -> None:
    """Require the exact kind version/name, dedicated credentials and a ready API."""
    if cluster.run(["kind", "version"]).split()[:2] != ["kind", "v0.31.0"]:
        raise GatewayError(
            "KIND_VERSION", "Expected kind v0.31.0", "use the reviewed infrastructure toolchain"
        )
    cluster.verify_context(root)
    value = cluster.run(
        [
            "kubectl",
            "--kubeconfig",
            str(cluster.kubeconfig(root)),
            "--context",
            cluster.CONTEXT,
            "--request-timeout=10s",
            "get",
            "--raw",
            "/readyz",
        ]
    )
    if value.strip() != "ok":
        raise GatewayError(
            "API_UNREADY",
            "Dedicated Kubernetes API is not ready",
            "follow docs/troubleshooting.md; do not recreate the cluster",
        )


def collect(root: Path) -> list[Result]:
    """Run independent read-only probes; skip resource reads if cluster identity is unverified."""
    results: list[Result] = []

    def probe(name: str, operation: Callable[[], object], remedy: str) -> bool:
        try:
            print(f"Checking {name} (45s command budget)...", flush=True)
            with cluster.command_deadline(45):
                operation()
        except GatewayError as error:
            results.append(Result(name, "FAIL", str(error)))
            return False
        except (OSError, ValueError, subprocess.SubprocessError, yaml.YAMLError):
            results.append(
                Result(name, "FAIL", f"Probe failed; next: {remedy}. Vendor output withheld.")
            )
            return False
        results.append(Result(name, "PASS", "verified"))
        return True

    probe("Caddy binary/checksum/build", lambda: installed(root), "task native:setup")
    probe(
        "Private gateway state",
        lambda: host.private_permissions(root),
        "review gateway Security settings",
    )
    docker = probe(
        "Local Docker engine",
        lambda: (
            cluster.verify_local_docker(),
            cluster.run(["docker", "info", "--format", "{{json .ServerVersion}}"]),
        ),
        "inspect Docker Engine; no automatic restart or reset",
    )
    ready = probe(
        "Named cluster/dedicated API",
        lambda: cluster_ready(root),
        "inspect the dedicated kubeconfig and API /readyz; see docs/troubleshooting.md",
    )
    for action in connectivity.FORWARDS:
        if docker and ready:
            probe(
                action,
                lambda action=action: connectivity.verify_forward(action, root),
                "task status or task native:target-health",
            )
        else:
            results.append(Result(action, "NOT CHECKED", "Docker/cluster identity must pass first"))
    for port in PORTS:
        probe(
            f"Loopback port {port}",
            lambda port=port: port_available(port),
            "inspect the existing listener",
        )
    try:
        identity = host.fingerprint(root)
        if identity is None:
            results.append(
                Result(
                    "Public CA",
                    "ACTION REQUIRED",
                    "Created on first native:up after private-state verification; no trust is installed",
                )
            )
        else:
            results.append(Result("Public CA SHA-256", "PASS", identity))
            for name, trusted in (
                ("Linux system trust", host.system_trust(root)),
                ("Node trust in this terminal", host.node_trust(root)),
            ):
                results.append(
                    Result(
                        name,
                        "PASS" if trusted else "ACTION REQUIRED",
                        "reviewed CA configured"
                        if trusted
                        else "follow native-worker-connectivity.md#review-trust-explicitly; no TLS bypass",
                    )
                )
    except (OSError, ValueError, subprocess.SubprocessError):
        results.append(
            Result(
                "CA/trust inspection",
                "FAIL",
                "Inspect retained public CA and configured Linux certificate store; no implicit repair",
            )
        )
    return results


def report(results: list[Result]) -> bool:
    """Print every observation and return whether connectivity prerequisites permit startup."""
    for result in results:
        print(f"{result.status:15} {result.name}: {result.detail}", flush=True)
    good = not any(result.status == "FAIL" for result in results)
    print(
        "Connectivity preflight passed; browser/Node trust actions remain explicit."
        if good
        else "Preflight failed; no services were started. Resolve the FAIL entries and rerun task native:doctor.",
        flush=True,
    )
    return good
