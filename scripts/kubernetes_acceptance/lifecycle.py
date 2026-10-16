"""Observe owned cleanup and restart only an exact verified controller Pod."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from scripts import cluster, control_plane_health, postgres
from scripts.kubernetes_acceptance.database import Database
from scripts.kubernetes_acceptance.errors import AcceptanceError
from scripts.kubernetes_acceptance.evidence import timestamp
from scripts.kubernetes_acceptance.selection import Selection
from scripts.kubernetes_acceptance.transport import RUN
from scripts.kubernetes_benchmark_contract import document

OWNER = "perfeng-control-plane"


def same_reservation(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Require stable durable identity across restart, not just a terminal lifecycle state."""
    keys = (
        "runId",
        "targetId",
        "clusterUid",
        "namespaceUid",
        "planSha256",
        "startedAt",
        "fencedAt",
        "notBefore",
    )
    if any(before.get(key) is None or after.get(key) != before[key] for key in keys):
        raise AcceptanceError("Durable benchmark cleanup identity changed across restart")


def cleanup_proof(row: dict[str, Any], run_id: str, selected: Selection) -> None:
    """Require released-after-drain evidence, bound to the same plan and namespace incarnation."""
    if (
        row.get("runId") != run_id
        or row.get("targetId") != "perfeng-local"
        or row.get("clusterUid") != selected.entry["benchmark"]["clusterUid"]
        or not row.get("namespaceUid")
        or not row.get("planSha256")
        or not all(row.get(key) for key in ("startedAt", "fencedAt", "notBefore", "releasedAt"))
    ):
        raise AcceptanceError("Cleanup reservation is absent, unreleased or foreign")
    UUID(row["namespaceUid"])
    start, fence, not_before, released = (
        timestamp(row[key]) for key in ("startedAt", "fencedAt", "notBefore", "releasedAt")
    )
    if fence < start or (not_before - fence).total_seconds() < 120 or released < not_before:
        raise AcceptanceError("Cleanup released before its durable two-minute drain boundary")
    expected = {
        "schemaVersion": 1,
        "runId": run_id,
        "planSha256": row["planSha256"],
        "clusterUid": row["clusterUid"],
        "namespaceUid": row["namespaceUid"],
        "executorStopped": True,
        "workloadStopped": True,
        "authorityRemoved": True,
        "namespaceFenced": True,
    }
    if row.get("proof") != expected or any(
        type(row["proof"].get(key)) is not bool
        for key in ("executorStopped", "workloadStopped", "authorityRemoved", "namespaceFenced")
    ):
        raise AcceptanceError("Cleanup attestation is incomplete or not bound to the retained hold")


class Lifecycle:
    """Dedicated-cluster observations; never repair benchmark resources or desired state."""

    def __init__(self, root: Path, selected: Selection) -> None:
        """Keep a frozen release and dedicated kubeconfig for every operation."""
        self.root, self.selected = root, selected
        self.kube = postgres.kubectl(root)
        self.database = Database(root)

    def get(self, kind: str, name: str, namespace: str | None) -> dict[str, Any]:
        """Read one exact object; absence is empty, while API failures remain failures."""
        kube = [item for item in self.kube]
        index = kube.index("--namespace")
        del kube[index : index + 2]
        if namespace is not None:
            kube.extend(["--namespace", namespace])
        print(f"M04 inspection: {kind} {namespace or 'cluster'}/{name}", flush=True)
        output = cluster.run([*kube, "get", kind, name, "--ignore-not-found", "-o", "json"])
        return document(output.encode()) if output.strip() else {}

    def executor(self, run_id: str) -> dict[str, Any]:
        """Return a real owned execution Job; a missed dispatch window is not acceptance."""
        if RUN.fullmatch(run_id) is None:
            raise AcceptanceError("Invalid cancellation Run identity")
        job = self.get("job", run_id, "perf-generators")
        if job:
            bound = self.database.execution(run_id)
            if not bound:
                return {}
            meta = job["metadata"]
            labels = meta.get("labels", {})
            pod = job.get("spec", {}).get("template", {}).get("spec", {})
            containers = [*pod.get("initContainers", []), *pod.get("containers", [])]
            if (
                labels.get("perfeng.io/run-id") != run_id
                or labels.get("app.kubernetes.io/managed-by") != OWNER
                or meta.get("name") != run_id
                or meta.get("namespace") != "perf-generators"
                or meta.get("uid") != bound.get("jobUid")
                or bound.get("jobName") != run_id
                or bound.get("namespace") != "perf-generators"
                or meta.get("annotations", {}).get("perfeng.io/execution-spec-sha256")
                != bound.get("specSha256")
                or pod.get("serviceAccountName") != run_id + "-benchmark"
                or len(containers) != 2
                or any(
                    item.get("image") != self.selected.entry["rawProducer"]["artifact"]["image"]
                    for item in containers
                )
            ):
                raise AcceptanceError("Cancellation executor is foreign")
        return job

    def cleanup(self, run_id: str) -> dict[str, Any]:
        """Verify durable proof, retained fence and actual absence of owned workload/authority."""
        row = self.database.reservation(run_id)
        cleanup_proof(row, run_id, self.selected)
        namespace = "perfeng-benchmark-" + run_id
        meta = self.get("namespace", namespace, None).get("metadata", {})
        if (
            meta.get("uid") != row["namespaceUid"]
            or meta.get("labels", {}).get("perfeng.io/run-id") != run_id
            or meta.get("labels", {}).get("app.kubernetes.io/managed-by") != OWNER
            or meta.get("annotations", {}).get("perfeng.io/benchmark-plan-sha256")
            != row["planSha256"]
            or meta.get("ownerReferences")
            or meta.get("deletionTimestamp")
        ):
            raise AcceptanceError("Cleanup namespace tombstone is missing or replaced")
        quota = self.get("resourcequota", "benchmark-budget", namespace)
        if (
            quota.get("metadata", {}).get("annotations", {}).get("perfeng.io/benchmark-plan-sha256")
            != row["planSha256"]
            or quota.get("metadata", {}).get("labels", {}).get("perfeng.io/run-id") != run_id
            or quota.get("metadata", {}).get("labels", {}).get("app.kubernetes.io/managed-by")
            != OWNER
            or quota.get("metadata", {}).get("ownerReferences")
            or quota.get("metadata", {}).get("deletionTimestamp")
            or quota.get("spec", {}).get("hard", {}).get("pods") != "0"
            or quota.get("status", {}).get("hard", {}).get("pods") != "0"
        ):
            raise AcceptanceError("Retained cleanup quota is not a closed observed fence")
        resource = run_id + "-benchmark"
        for kind, name, ns in (
            ("job", run_id, "perf-generators"),
            ("serviceaccount", resource, "perf-generators"),
            ("configmap", resource, "perf-generators"),
            ("role", resource, namespace),
            ("rolebinding", resource, namespace),
            ("clusterrole", resource, None),
            ("clusterrolebinding", resource, None),
        ):
            if self.get(kind, name, ns):
                raise AcceptanceError("Cleanup left an executor or per-Run authority object")
        for ns in (namespace, "perf-generators"):
            kube = self.kube.copy()
            kube[kube.index("--namespace") + 1] = ns
            command = [*kube, "get", "--raw", f"/api/v1/namespaces/{ns}/pods?limit=4096"]
            pods = document(cluster.run(command).encode())
            # Analysis/report Pods are not benchmark executors and may remain for evidence.
            items = (
                pods["items"]
                if ns == namespace
                else [
                    pod
                    for pod in pods["items"]
                    if pod.get("spec", {}).get("serviceAccountName") == resource
                    or any(
                        owner.get("kind") == "Job" and owner.get("name") == run_id
                        for owner in pod.get("metadata", {}).get("ownerReferences", [])
                    )
                ]
            )
            if items or pods.get("metadata", {}).get("continue"):
                raise AcceptanceError("Cleanup cannot prove executor/workload Pod termination")
        return row

    def controller(self) -> dict[str, Any]:
        """Validate selected Deployment/Pod and the complete Pod-to-Deployment owner chain."""
        deployment = self.get("deployment", "control-plane", postgres.NAMESPACE)
        control_plane_health.validate_deployment(deployment, self.selected.lock)
        pods = document(
            cluster.run(
                [
                    *self.kube,
                    "get",
                    "pods",
                    "-l",
                    control_plane_health.SELECTOR,
                    "-o",
                    "json",
                ]
            ).encode()
        )
        control_plane_health.validate_pods(pods, self.selected.lock)
        pod = pods["items"][0]
        meta = pod["metadata"]
        owners = meta.get("ownerReferences", [])
        if (
            len(owners) != 1
            or owners[0].get("controller") is not True
            or owners[0].get("kind") != "ReplicaSet"
        ):
            raise AcceptanceError("Controller Pod has an unexpected owner")
        replica = self.get("replicaset", owners[0]["name"], postgres.NAMESPACE)
        replica_owners = replica.get("metadata", {}).get("ownerReferences", [])
        if (
            replica.get("metadata", {}).get("uid") != owners[0]["uid"]
            or len(replica_owners) != 1
            or replica_owners[0].get("kind") != "Deployment"
            or replica_owners[0].get("controller") is not True
            or replica_owners[0].get("uid") != deployment["metadata"]["uid"]
            or replica_owners[0].get("name") != "control-plane"
            or not meta.get("resourceVersion")
            or meta.get("deletionTimestamp")
            or re.fullmatch(r"control-plane-[a-z0-9-]{1,49}", meta.get("name", "")) is None
        ):
            raise AcceptanceError("Controller ownership chain or deletion identity differs")
        UUID(meta["uid"])
        return pod

    def restart(self, run_id: str, held: dict[str, Any]) -> dict[str, str]:
        """Delete only the exact current Pod, preserving its template, volumes and all evidence."""
        self.database.exclusive(run_id)
        pod = self.controller()
        current = self.database.reservation(run_id)
        same_reservation(held, current)
        if current.get("releasedAt") is not None:
            raise AcceptanceError(
                "Cleanup already released; active restart recovery was not exercised"
            )
        meta = pod["metadata"]
        options = {
            "apiVersion": "v1",
            "kind": "DeleteOptions",
            "preconditions": {"uid": meta["uid"], "resourceVersion": meta["resourceVersion"]},
        }
        path = f"/api/v1/namespaces/{postgres.NAMESPACE}/pods/{meta['name']}"
        print(
            f"Restarting verified control-plane Pod {meta['name']} with UID/RV preconditions...",
            flush=True,
        )
        cluster.run(
            [*self.kube, "delete", "--raw", path, "-f", "-"],
            input_data=json.dumps(options).encode(),
        )
        cluster.run([*self.kube, "wait", "--for=delete", f"pod/{meta['name']}", "--timeout=180s"])
        cluster.run([*self.kube, "rollout", "status", "deployment/control-plane", "--timeout=300s"])
        replacement = self.controller()
        if replacement["metadata"]["uid"] == meta["uid"]:
            raise AcceptanceError("Controller Pod was not actually replaced")
        return {"beforeUid": meta["uid"], "afterUid": replacement["metadata"]["uid"]}
