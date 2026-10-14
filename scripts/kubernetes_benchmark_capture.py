"""Bounded, read-only local-cluster collection and normalized observation parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, DecimalException
from pathlib import Path
from typing import Any
from uuid import UUID

from scripts import cluster
from scripts.kubernetes_benchmark_contract import ROOT, document
from scripts.kubernetes_benchmark_profile import Profile

QUANTITY = re.compile(
    r"([+]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?)(n|u|m|Ki|Mi|Gi|Ti|Pi|Ei|k|K|M|G|T|P|E)?"
)
DURATION = re.compile(r"(\d+(?:\.\d+)?)(h|ms|us|µs|ns|m|s)")


def quantity(value: object, *, cpu: bool, usage: bool = False) -> int:
    """Convert Kubernetes quantities conservatively: floor capacity, ceil usage, bound exact integers."""
    if (
        not isinstance(value, str)
        or len(value) > 64
        or (match := QUANTITY.fullmatch(value)) is None
    ):
        raise ValueError("Invalid resource quantity")
    units = {
        "": Decimal(1),
        "n": Decimal("1e-9"),
        "u": Decimal("1e-6"),
        "m": Decimal("1e-3"),
        "k": Decimal(1000),
        "K": Decimal(1000),
    }
    units.update(
        {
            suffix: Decimal(1000) ** power
            for power, suffix in enumerate(("M", "G", "T", "P", "E"), 2)
        }
    )
    units.update(
        {
            suffix: Decimal(1024) ** power
            for power, suffix in enumerate(("Ki", "Mi", "Gi", "Ti", "Pi", "Ei"), 1)
        }
    )
    try:
        number = Decimal(match[1]) * units[match[2] or ""] * (1000 if cpu else 1)
        if not number.is_finite() or not 0 <= number <= 2**53 - 1:
            raise ValueError("Resource quantity exceeds exact supported range")
        return int(number.to_integral_value(rounding=ROUND_CEILING if usage else ROUND_FLOOR))
    except DecimalException:
        raise ValueError("Invalid resource quantity") from None


def duration(value: object) -> float:
    """Decode a positive bounded Kubernetes metrics Go-duration window."""
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Invalid metric window")
    parts = list(DURATION.finditer(value))
    if not parts or "".join(part[0] for part in parts) != value:
        raise ValueError("Invalid metric window")
    scales = {
        "h": 3600,
        "m": 60,
        "s": 1,
        "ms": 0.001,
        "us": 0.000001,
        "µs": 0.000001,
        "ns": 0.000000001,
    }
    result = sum(float(part[1]) * scales[part[2]] for part in parts)
    if not 0.000001 <= result <= 300:
        raise ValueError("Invalid metric window")
    return result


def stamp(value: datetime) -> str:
    """Emit UTC timestamps in the shared six-decimal precision boundary."""
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def kubectl(root: Path) -> list[str]:
    """Use only dedicated local credentials and a bounded API request timeout."""
    return [
        "kubectl",
        "--kubeconfig",
        str(cluster.kubeconfig(root)),
        "--context",
        cluster.CONTEXT,
        "--request-timeout=10s",
    ]


def identify(root: Path = ROOT) -> str:
    """Refuse resource reads until local engine, named cluster and exact credentials agree."""
    cluster.verify_local_docker()
    if cluster.run(["kind", "version"]).split()[:2] != ["kind", "v0.31.0"]:
        raise ValueError("Use the reviewed kind v0.31.0 toolchain")
    cluster.verify_context(root)
    kube = kubectl(root)
    if cluster.run([*kube, "get", "--raw", "/readyz"]).strip() != "ok":
        raise ValueError("Dedicated cluster API is not ready; inspect task status")
    uid = cluster.run(
        [*kube, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"]
    ).strip()
    try:
        return str(UUID(uid))
    except ValueError:
        raise ValueError(
            "Cannot verify the cluster incarnation; no qualification receipt written"
        ) from None


def nodes(value: dict[str, Any], profile: Profile) -> tuple[list[dict[str, Any]], bool, bool]:
    """Normalize bounded observed inventory while retaining placement/capacity failures."""
    items = value.get("items")
    if not isinstance(items, list) or not 1 <= len(items) <= 256:
        raise ValueError("Invalid bounded node inventory")
    result = []
    names: set[str] = set()
    uids: set[str] = set()
    placement = True
    capacity = True
    for item in items:
        metadata, status = item["metadata"], item["status"]
        name, uid = metadata["name"], str(UUID(metadata["uid"]))
        if not isinstance(name, str) or name in names or uid in uids:
            raise ValueError("Duplicate or invalid observed node identity")
        names.add(name)
        uids.add(uid)
        info = status["nodeInfo"]
        if any(
            not isinstance(info[key], str) or not info[key]
            for key in ("kubeletVersion", "containerRuntimeVersion", "architecture")
        ):
            raise ValueError("Invalid observed node runtime")
        allocation = {
            "cpuMilliCores": quantity(status["allocatable"]["cpu"], cpu=True),
            "memoryBytes": quantity(status["allocatable"]["memory"], cpu=False),
        }
        if not all(allocation.values()):
            raise ValueError("Invalid observed allocatable resources")
        conditions = {}
        for condition in status.get("conditions", []):
            kind = condition.get("type")
            if kind in {"Ready", "MemoryPressure", "DiskPressure", "PIDPressure"}:
                if kind in conditions or condition.get("status") not in {
                    "True",
                    "False",
                    "Unknown",
                }:
                    raise ValueError("Invalid node condition observation")
                conditions[kind] = condition["status"].upper()
        placement &= (
            name in profile.nodes
            and metadata.get("labels", {}).get("workload") == profile.nodes.get(name)
            and not item.get("spec", {}).get("unschedulable", False)
        )
        capacity &= (
            info["kubeletVersion"] == profile.kubernetes_version
            and info.get("operatingSystem") == "linux"
            and info["architecture"] == "amd64"
            and allocation["cpuMilliCores"] >= profile.cpu_minimum
            and allocation["memoryBytes"] >= profile.memory_minimum
        )
        result.append(
            {
                "name": name,
                "uid": uid,
                "kubernetesVersion": info["kubeletVersion"],
                "containerRuntimeVersion": info["containerRuntimeVersion"],
                "architecture": info["architecture"],
                "allocatable": allocation,
                "conditions": {
                    key: conditions.get(kind, "UNKNOWN")
                    for key, kind in (
                        ("ready", "Ready"),
                        ("memoryPressure", "MemoryPressure"),
                        ("diskPressure", "DiskPressure"),
                        ("pidPressure", "PIDPressure"),
                    )
                },
            }
        )
    return (
        result,
        placement and names == set(profile.nodes),
        capacity and names == set(profile.nodes),
    )


def metrics(value: dict[str, Any], now: datetime, profile: Profile) -> dict[str, dict[str, Any]]:
    """Accept only fresh bounded provider windows; no fabricated zeros or query-time timestamps."""
    items = value.get("items")
    if not isinstance(items, list) or len(items) > 256:
        raise ValueError("Invalid bounded metrics inventory")
    result = {}
    for item in items:
        name = item["metadata"]["name"]
        if not isinstance(name, str) or name in result:
            raise ValueError("Invalid metric node identity")
        end = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
        seconds = duration(item["window"])
        if (
            end.tzinfo is None
            or not 0 <= (now - end).total_seconds() <= profile.metric_age
            or seconds > profile.metric_window
        ):
            raise ValueError("Metrics are stale, future-dated or outside the reviewed window")
        result[name] = {
            "source": "metrics-api",
            "windowStart": stamp(end - timedelta(seconds=seconds)),
            "windowEnd": stamp(end),
            "cpuMilliCores": quantity(item["usage"]["cpu"], cpu=True, usage=True),
            "memoryBytes": quantity(item["usage"]["memory"], cpu=False, usage=True),
        }
    return result


def pod_namespaces(output: str) -> tuple[set[str], bool]:
    """Read only placement/phase/namespace columns, never Pod environments or Secret values."""
    active: set[str] = set()
    lines = output.splitlines()
    if len(lines) > 4096:
        raise ValueError("Pod inventory exceeds its bound")
    for line in lines:
        fields = line.split()
        if len(fields) != 3 or fields[1] not in {
            "Pending",
            "Running",
            "Succeeded",
            "Failed",
            "Unknown",
        }:
            raise ValueError("Invalid Pod placement observation")
        if (
            len(fields[2]) > 63
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", fields[2]) is None
        ):
            raise ValueError("Invalid observed namespace identity")
        if fields[1] in {"Pending", "Running", "Unknown"}:
            active.add(fields[2])
    return active, "perf-generators" in active


@dataclass(frozen=True)
class Capture:
    """Observed cluster incarnation and independent probes; None means a probe was unavailable."""

    uid: str
    inventory: list[dict[str, Any]]
    placement: bool | None
    capacity: bool | None
    measured: dict[str, dict[str, Any]]
    active: set[str] | None
    start: datetime
    end: datetime


def collect(root: Path, profile: Profile) -> Capture:
    """Collect guarded non-secret observations within one 120-second overall command budget."""
    with cluster.command_deadline(120):
        print("[1/4] Verifying local engine, dedicated cluster and API identity...", flush=True)
        uid = identify(root)
        start = datetime.now(UTC)
        kube = kubectl(root)
        print("[2/4] Reading node placement/capacity and active namespace inventory...", flush=True)
        try:
            inventory, placement, capacity = nodes(
                document(cluster.run([*kube, "get", "nodes", "-o", "json"]).encode()), profile
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            inventory, placement, capacity = [], None, None
        try:
            image = profile.node_image
            for name in profile.nodes:
                observed = document(
                    (
                        '{"image":'
                        + cluster.run(
                            ["docker", "inspect", "--format", "{{json .Config.Image}}", name]
                        ).strip()
                        + "}"
                    ).encode()
                )["image"]
                if observed not in {image, "kindest/node@" + image.split("@", 1)[1]}:
                    capacity = False
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            if capacity is not False:
                capacity = None
        try:
            active, _ = pod_namespaces(
                cluster.run(
                    [
                        *kube,
                        "get",
                        "pods",
                        "--all-namespaces",
                        "-o",
                        "custom-columns=NODE:.spec.nodeName,PHASE:.status.phase,NAMESPACE:.metadata.namespace",
                        "--no-headers",
                    ]
                )
            )
        except (OSError, ValueError):
            active = None
        print("[3/4] Reading metrics without installing or repairing a service...", flush=True)
        try:
            measured = metrics(
                document(
                    cluster.run(
                        [*kube, "get", "--raw", "/apis/metrics.k8s.io/v1beta1/nodes"]
                    ).encode()
                ),
                datetime.now(UTC),
                profile,
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            measured = {}
        end = datetime.now(UTC)
        if inventory:
            current_nodes = document(
                cluster.run([*kube, "get", "nodes", "-o", "json"]).encode()
            ).get("items")
            if not isinstance(current_nodes, list) or len(current_nodes) > 256:
                raise ValueError("Cannot recheck node identities")
            current_pairs = [
                (item["metadata"]["name"], str(UUID(item["metadata"]["uid"])))
                for item in current_nodes
            ]
            if len(set(current_pairs)) != len(current_pairs) or set(current_pairs) != {
                (node["name"], node["uid"]) for node in inventory
            }:
                raise ValueError("Node identity changed during qualification; no receipt written")
        current_uid = cluster.run(
            [*kube, "get", "namespace", "kube-system", "-o", "jsonpath={.metadata.uid}"]
        ).strip()
        if str(UUID(current_uid)) != uid:
            raise ValueError("Cluster incarnation changed during qualification; no receipt written")
        return Capture(uid, inventory, placement, capacity, measured, active, start, end)
