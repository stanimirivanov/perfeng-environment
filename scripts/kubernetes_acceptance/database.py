"""Bounded read-only inspection of cleanup evidence unavailable through the Run API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from scripts import cluster, postgres
from scripts.kubernetes_acceptance.errors import AcceptanceError
from scripts.kubernetes_acceptance.transport import RUN
from scripts.kubernetes_benchmark_contract import document


class Database:
    """Use existing in-cluster PostgreSQL credentials without copying them to the host."""

    def __init__(self, root: Path) -> None:
        """Retain only the dedicated context command prefix."""
        self.kube = postgres.kubectl(root)

    def query(self, sql: str) -> dict[str, Any]:
        """Execute fixed acceptance SELECTs with server-enforced read-only and time limits."""
        command = [
            *self.kube,
            "exec",
            "-i",
            "statefulset/postgres",
            "--",
            "sh",
            "-c",
            'export PGPASSWORD="$(cat "$POSTGRES_PASSWORD_FILE")" PGCONNECT_TIMEOUT=10; '
            "exec psql -X -w -qAt -h postgres.perf-platform.svc.cluster.local "
            "-U postgres -d perfeng -v ON_ERROR_STOP=1 -f -",
        ]
        content = (
            "BEGIN READ ONLY; SET LOCAL statement_timeout = '5s'; "
            "SET LOCAL lock_timeout = '2s'; " + sql + "; COMMIT;\n"
        )
        with cluster.command_deadline(20):
            return document(cluster.run(command, input_data=content.encode()).encode())

    def exclusive(self, allowed: str | None = None) -> None:
        """Refuse unrelated active Runs/holds; this is a check, not an inter-operator lock."""
        if allowed is not None and RUN.fullmatch(allowed) is None:
            raise AcceptanceError("Invalid acceptance Run identity")
        exclude = "" if allowed is None else f" AND run_id <> '{allowed}'"
        value = self.query(
            "SELECT json_build_object('runs', (SELECT count(*) FROM perfeng_control.runs "
            "WHERE snapshot->>'state' NOT IN ('COMPLETED','INVALID','ABORTED',"
            f"'INFRASTRUCTURE_FAILURE','TEST_FAILURE'){exclude}), "
            "'holds', (SELECT count(*) FROM perfeng_control.benchmark_target_reservations "
            f"WHERE released_at IS NULL{exclude}))"
        )
        if value != {"runs": 0, "holds": 0}:
            raise AcceptanceError(
                "An exclusive operator window is required; unrelated Runs/holds remain"
            )

    def reservation(self, run_id: str) -> dict[str, Any]:
        """Read one durable hold, including its internal attestation; never update/release it."""
        if RUN.fullmatch(run_id) is None:
            raise AcceptanceError("Invalid acceptance Run identity")
        return self.query(
            "SELECT COALESCE((SELECT json_build_object("
            "'runId',run_id,'targetId',target_id,'clusterUid',cluster_uid,"
            "'namespaceUid',namespace_uid,'planSha256',plan_sha256,"
            "'startedAt',cleanup_started_at,'fencedAt',cleanup_fenced_at,"
            "'notBefore',cleanup_not_before,'releasedAt',released_at,"
            "'proof',convert_from(cleanup_receipt,'UTF8')::json) "
            f"FROM perfeng_control.benchmark_target_reservations WHERE run_id='{run_id}'), '{{}}'::json)"
        )

    def execution(self, run_id: str) -> dict[str, Any]:
        """Read the controller-bound executor UID/spec hash, never infer ownership from labels alone."""
        if RUN.fullmatch(run_id) is None:
            raise AcceptanceError("Invalid acceptance execution identity")
        return self.query(
            "SELECT COALESCE((SELECT json_build_object('namespace',namespace,"
            "'jobName',job_name,'jobUid',job_uid,'specSha256',spec_sha256) "
            f"FROM perfeng_control.kubernetes_executions WHERE run_id='{run_id}'), '{{}}'::json)"
        )
