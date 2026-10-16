"""The M04 acceptance sequence, separated from API, Kubernetes and storage adapters."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from scripts.kubernetes_acceptance import baseline, evidence
from scripts.kubernetes_acceptance.errors import AcceptanceError
from scripts.kubernetes_acceptance.journal import Journal
from scripts.kubernetes_acceptance.lifecycle import Lifecycle, same_reservation
from scripts.kubernetes_acceptance.schemas import Schemas
from scripts.kubernetes_acceptance.selection import Selection
from scripts.kubernetes_acceptance.transport import RUN, Session

TERMINAL = {"COMPLETED", "INVALID", "ABORTED", "INFRASTRUCTURE_FAILURE", "TEST_FAILURE"}


class Acceptance:
    """Coordinate a reference, cancellation/restart and a post-recovery decision Run."""

    def __init__(
        self,
        session: Session,
        lifecycle: Lifecycle,
        selected: Selection,
        schemas: Schemas,
        journal: Journal,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Inject clocks and adapters so timeout/order/partial-failure tests require no live services."""
        self.session, self.lifecycle, self.selected = session, lifecycle, selected
        self.schemas, self.journal, self.clock, self.sleep = schemas, journal, clock, sleep
        self.revisions: dict[str, int] = {}

    def observe(self, run_id: str) -> dict[str, Any]:
        """Read only the selected Run and reject backward revision or changed request."""
        value = self.session.call("GET", f"/v1/runs/{run_id}")["body"]
        evidence.run_snapshot(value, run_id, self.selected, self.revisions.get(run_id, 1))
        self.revisions[run_id] = value["revision"]
        return value

    def submit(self, phase: str) -> str:
        """Checkpoint the immutable request and idempotency key before the first submission."""
        key = "m04-" + uuid4().hex
        self.journal.record(
            "submission-intent", phase=phase, idempotencyKey=key, request=self.selected.request
        )
        value = self.session.call(
            "POST", "/v1/runs", self.selected.request, key=key, statuses=(201,)
        )["body"]
        run_id = value.get("id")
        if not isinstance(run_id, str) or RUN.fullmatch(run_id) is None:
            raise AcceptanceError("Submission returned an invalid Run identity")
        evidence.run_snapshot(value, run_id, self.selected)
        if value.get("state") != "CREATED" or value["revision"] != 1:
            raise AcceptanceError("Submission was not a new immutable Run")
        self.journal.record("run-created", phase=phase, runId=run_id)
        return run_id

    def wait(self, label: str, probe: Callable[[], Any], seconds: int = 900) -> Any:
        """Poll within a monotonic budget; a failed probe is never silently converted to success."""
        deadline = self.clock() + seconds
        last_notice = self.clock()
        while self.clock() < deadline:
            value = probe()
            if value:
                return value
            now = self.clock()
            if now - last_notice >= 20:
                print(f"Waiting for M04 {label}...", flush=True)
                last_notice = now
            self.sleep(min(2, max(0, deadline - self.clock())))
        raise AcceptanceError(f"M04 {label} exceeded its {seconds}s budget; state retained")

    def terminal(self, run_id: str, state: str) -> dict[str, Any]:
        """Require the actual expected lifecycle terminal, not tool exit or Job success alone."""

        def probe():
            value = self.observe(run_id)
            if value["state"] in TERMINAL:
                if value["state"] != state:
                    raise AcceptanceError(
                        "Run reached an unexpected terminal state; inspect retained evidence"
                    )
                return value
            return None

        return self.wait(state, probe)

    def completed(self, run_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
        """Attest the full decision pipeline and cleanup proof for one completed benchmark."""
        self.terminal(run_id, "COMPLETED")
        self.journal.record("artifact-verification-started", runId=run_id)
        claims, documents = evidence.artifacts(self.session, run_id, self.selected, self.schemas)
        cleanup = self.lifecycle.cleanup(run_id)
        self.journal.record(
            "completed-evidence-verified",
            runId=run_id,
            artifacts=list(claims.values()),
            cleanup=cleanup,
        )
        return claims, documents

    def cancel_and_recover(self) -> tuple[str, dict[str, str]]:
        """Restart during durable cancellation cleanup and prove that exact hold recovers."""
        run_id = self.submit("cancellation-recovery")

        def dispatched():
            current = self.observe(run_id)
            if current["state"] not in {
                "CREATED",
                "VALIDATING",
                "PROVISIONING",
                "WARMING_UP",
                "RUNNING",
            }:
                raise AcceptanceError(
                    "Missed the dispatched cancellation window; no recovery success claimed"
                )
            job = self.lifecycle.executor(run_id)
            return job if job else None

        job = self.wait("owned executor dispatch", dispatched, 300)
        self.journal.record("owned-executor-observed", runId=run_id, jobUid=job["metadata"]["uid"])
        current = self.session.call("POST", f"/v1/runs/{run_id}/cancel", statuses=(202,))["body"]
        evidence.run_snapshot(current, run_id, self.selected, self.revisions[run_id])
        if current.get("state") != "CANCELLING":
            raise AcceptanceError("Cancellation did not enter the owned cleanup lifecycle")
        self.revisions[run_id] = current["revision"]

        def fenced():
            if self.observe(run_id)["state"] != "CANCELLING":
                raise AcceptanceError(
                    "Cancellation already finished; active restart recovery was not exercised"
                )
            row = self.lifecycle.database.reservation(run_id)
            if row.get("releasedAt"):
                raise AcceptanceError("Cancellation hold was released before restart acceptance")
            return row if row.get("fencedAt") else None

        held = self.wait("durable cancellation fence", fenced, 120)
        self.journal.record("cleanup-held-before-restart", reservation=held)
        restarted = self.lifecycle.restart(run_id, held)
        same_reservation(held, self.lifecycle.database.reservation(run_id))
        self.journal.record("controller-replaced", runId=run_id, **restarted)
        self.terminal(run_id, "ABORTED")
        cleaned = self.lifecycle.cleanup(run_id)
        same_reservation(held, cleaned)
        self.journal.record("cancelled-cleanup-recovered", runId=run_id, reservation=cleaned)
        return run_id, restarted

    def run(self, *, approve_baseline: bool, reference_run: str | None = None) -> dict[str, Any]:
        """Execute all gates; no final success document exists if any phase fails."""
        self.lifecycle.database.exclusive()
        self.journal.record("baseline-inspection")
        response = self.session.call("GET", self.selected.baseline_path, statuses=(200, 404))
        existing = response["body"] if response["status"] == 200 else None
        if existing is None or existing.get("state") != "APPROVED":
            if not approve_baseline:
                raise AcceptanceError(
                    "A missing/pending baseline needs explicit --approve-baseline consent"
                )
        if existing is not None:
            source_id = existing.get("sourceRunId")
            if reference_run is not None and reference_run != source_id:
                raise AcceptanceError("Explicit source Run conflicts with the retained baseline")
        else:
            source_id = reference_run or self.submit("reference")
        if not isinstance(source_id, str) or RUN.fullmatch(source_id) is None:
            raise AcceptanceError("Baseline source Run identity is invalid")
        claims, documents = self.completed(source_id)
        expected = baseline.create_request(source_id, claims["normalized-result/v1"], self.selected)
        qualification = baseline.source_qualification(documents, self.selected)
        self.journal.record(
            "baseline-approval-intent",
            sourceRunId=source_id,
            identity={"id": expected["id"], "version": expected["version"]},
            approvalAuthorized=approve_baseline,
        )
        approved = baseline.approve(
            self.session,
            self.selected,
            expected,
            qualification,
            existing,
            authorized=approve_baseline,
        )
        self.journal.record("baseline-approved-verified", baseline=baseline.receipt(approved))
        self.lifecycle.database.exclusive()
        cancelled_id, restarted = self.cancel_and_recover()
        after = self.session.call("GET", self.selected.baseline_path)["body"]
        if after != approved:
            raise AcceptanceError("Approved baseline did not persist unchanged across restart")
        self.lifecycle.database.exclusive()
        candidate_id = self.submit("post-recovery-decision")
        candidate_claims, candidate_documents = self.completed(candidate_id)
        verdict = evidence.decision(
            candidate_documents["analysis-result/v1"], self.selected, approved
        )
        self.terminal(source_id, "COMPLETED")
        self.terminal(cancelled_id, "ABORTED")
        self.lifecycle.database.exclusive()
        result = {
            "schema": "m04-kubernetes-acceptance/v1",
            "status": "PASSED",
            "purpose": "integration",
            "deploymentRevision": self.selected.lock.deployment_revision,
            "controlPlaneImage": self.selected.lock.image,
            "request": self.selected.request,
            "clientPod": self.session.name,
            "referenceRunId": source_id,
            "cancelledRunId": cancelled_id,
            "candidateRunId": candidate_id,
            "baseline": baseline.receipt(approved),
            "restart": restarted,
            "regression": verdict,
            "candidateArtifacts": list(candidate_claims.values()),
        }
        self.journal.record("acceptance-passed", candidateRunId=candidate_id, regression=verdict)
        self.journal.finish(result)
        return result
