"""Load the reviewed local integration cohort and construct its stable environment identity."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts import infrastructure_lock
from scripts.kubernetes_benchmark_contract import ROOT, document

PROFILE = ROOT / "profiles/kubernetes/local-kind-integration.json"
ID = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")
VERSION = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)")


def exact(value: Any, keys: set[str], name: str) -> dict[str, Any]:
    """Normalize an exact JSON object without echoing untrusted values."""
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"Invalid {name} fields")
    return value


def integer(value: Any, name: str, maximum: int) -> int:
    """Require bounded positive integers rather than bools or fractional budgets."""
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"Invalid {name} budget")
    return value


@dataclass(frozen=True)
class Profile:
    """Reviewed qualification policy; its digest excludes all transient observations."""

    id: str
    version: str
    sha256: str
    node_image: str
    kubernetes_version: str
    nodes: dict[str, str]
    cpu_minimum: int
    memory_minimum: int
    cpu_ratio: float
    memory_ratio: float
    metric_age: int
    metric_window: int
    namespaces: frozenset[str]

    def identity(self) -> dict[str, str]:
        """Return the contract policy identity; integration never asserts production calibration."""
        return {
            "id": self.id,
            "version": self.version,
            "sha256": self.sha256,
            "purpose": "integration",
        }


def load(path: Path = PROFILE, root: Path = ROOT) -> Profile:
    """Read and strictly validate a cohort against the selected infrastructure compatibility."""
    content = path.read_bytes()
    value = exact(
        document(content), {"schemaVersion", "kind", "metadata", "purpose", "spec"}, "profile"
    )
    if (
        type(value["schemaVersion"]) is not int
        or value["schemaVersion"] != 1
        or value["kind"] != "KubernetesBenchmarkProfile"
        or value["purpose"] != "integration"
    ):
        raise ValueError("Only the reviewed local integration profile is supported")
    metadata = exact(value["metadata"], {"id", "version"}, "profile metadata")
    for key, pattern in (("id", ID), ("version", VERSION)):
        if not isinstance(metadata[key], str) or not pattern.fullmatch(metadata[key]):
            raise ValueError("Invalid profile identity")
    spec = exact(
        value["spec"],
        {
            "clusterName",
            "kubernetesVersion",
            "nodes",
            "architecture",
            "minimumAllocatableCpuMilliCores",
            "minimumAllocatableMemoryBytes",
            "maximumCpuUsageRatio",
            "maximumMemoryUsageRatio",
            "maximumMetricAgeSeconds",
            "maximumMetricWindowSeconds",
            "allowedActiveNamespaces",
        },
        "profile spec",
    )
    infrastructure = infrastructure_lock.load_lock(root / "deployments/local-infrastructure.json")
    if (
        infrastructure.cluster_name != "perfeng-local"
        or spec["clusterName"] != infrastructure.cluster_name
        or spec["architecture"] != "amd64"
        or not infrastructure.node_image.startswith(f"kindest/node:{spec['kubernetesVersion']}@")
    ):
        raise ValueError("Profile differs from the selected infrastructure compatibility")
    expected_nodes = {
        "perfeng-local-control-plane": "control-plane",
        "perfeng-local-worker": "performance-generator",
        "perfeng-local-worker2": "sut",
    }
    if spec["nodes"] != expected_nodes:
        raise ValueError("Profile must select the reviewed three-node placement")
    ratios = []
    for key in ("maximumCpuUsageRatio", "maximumMemoryUsageRatio"):
        ratio = spec[key]
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0 < ratio <= 1:
            raise ValueError("Invalid usage ratio budget")
        ratios.append(float(ratio))
    namespaces = spec["allowedActiveNamespaces"]
    if (
        not isinstance(namespaces, list)
        or not namespaces
        or any(not isinstance(name, str) or not ID.fullmatch(name) for name in namespaces)
        or len(set(namespaces)) != len(namespaces)
        or "perf-generators" in namespaces
    ):
        raise ValueError(
            "Invalid active namespace allowlist; benchmark generators must remain exclusive"
        )
    return Profile(
        id=metadata["id"],
        version=metadata["version"],
        sha256=hashlib.sha256(content).hexdigest(),
        node_image=infrastructure.node_image,
        kubernetes_version=spec["kubernetesVersion"],
        nodes=dict(expected_nodes),
        cpu_minimum=integer(spec["minimumAllocatableCpuMilliCores"], "CPU", 1000000),
        memory_minimum=integer(spec["minimumAllocatableMemoryBytes"], "memory", 2**53 - 1),
        cpu_ratio=ratios[0],
        memory_ratio=ratios[1],
        metric_age=integer(spec["maximumMetricAgeSeconds"], "metric age", 300),
        metric_window=integer(spec["maximumMetricWindowSeconds"], "metric window", 300),
        namespaces=frozenset(namespaces),
    )


def environment(
    profile: Profile, kube_system_uid: str, inventory: list[dict[str, Any]]
) -> tuple[dict[str, str], bytes]:
    """Persistable expected-environment bytes bound to policy, node image and cluster incarnation.

    These bytes describe selected integration conditions, not transient health;
    qualification separately checks the actual observations against them.
    """
    basis = {
        "profileSha256": profile.sha256,
        "nodeImage": profile.node_image,
        "kubeSystemUid": kube_system_uid,
        "observedNodes": [
            {key: value for key, value in node.items() if key != "conditions"}
            for node in sorted(inventory, key=lambda node: node["name"])
        ],
    }
    fingerprint = hashlib.sha256(
        json.dumps(basis, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    descriptor = {
        "cluster": "perfeng-local",
        "fingerprint": fingerprint,
        "kubernetes": {
            "version": profile.kubernetes_version,
            "nodeCount": len(profile.nodes),
            "nodePools": [
                {"name": role, "cpuArchitecture": "amd64"} for role in profile.nodes.values()
            ],
        },
        "application": {"configurationHash": profile.sha256},
    }
    content = (json.dumps(descriptor, indent=2) + "\n").encode("utf-8")
    return {
        "id": profile.id,
        "version": profile.version,
        "sha256": hashlib.sha256(content).hexdigest(),
        "fingerprint": fingerprint,
    }, content
