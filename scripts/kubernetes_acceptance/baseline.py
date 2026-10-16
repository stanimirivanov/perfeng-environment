"""Explicit baseline acceptance transitions, never implicit approval or replacement."""

from __future__ import annotations

from typing import Any

from scripts.kubernetes_acceptance import evidence
from scripts.kubernetes_acceptance.errors import AcceptanceError
from scripts.kubernetes_acceptance.selection import BASELINE_ID, BASELINE_VERSION, Selection
from scripts.kubernetes_acceptance.transport import Session


def create_request(
    run_id: str,
    artifact: dict[str, Any],
    selected: Selection,
) -> dict[str, Any]:
    """Bind the integration-only reference to its verified source and exact cohort."""
    return {
        "id": BASELINE_ID,
        "version": BASELINE_VERSION,
        "testId": selected.entry["testId"],
        "sourceRunId": run_id,
        "artifact": artifact,
        "software": selected.request["candidate"],
        "workload": selected.entry["workload"],
        "environment": selected.entry["environment"],
        "dataset": selected.entry["dataset"],
        "reason": "Explicit M04 integration acceptance; not a production-performance baseline.",
    }


def match(record: dict[str, Any], expected: dict[str, Any]) -> None:
    """Refuse conflicting identity/provenance even when a same-name baseline is approved."""
    if any(record.get(key) != value for key, value in expected.items() if key != "reason"):
        raise AcceptanceError("Existing baseline differs; it will not be overwritten or relabelled")
    if type(record.get("revision")) is not int or record["revision"] < 1:
        raise AcceptanceError("Baseline revision is invalid")


def approve(
    session: Session,
    selected: Selection,
    expected: dict[str, Any],
    qualification: dict[str, Any],
    existing: dict[str, Any] | None,
    *,
    authorized: bool,
) -> dict[str, Any]:
    """Advance only the exact evidence-backed version, under explicit operator consent."""
    if existing is not None:
        match(existing, expected)
        if existing.get("state") == "APPROVED":
            if existing.get("qualification") != qualification:
                raise AcceptanceError(
                    "Approved baseline qualification differs from actual evidence"
                )
            return existing
    if not authorized:
        raise AcceptanceError("Baseline approval requires explicit --approve-baseline consent")
    record = existing
    if record is None:
        record = session.call("POST", "/v1/baselines", expected, statuses=(201,))["body"]
        match(record, expected)
        if record.get("state") != "CANDIDATE" or record["revision"] != 1:
            raise AcceptanceError("Unexpected baseline creation response")
    for source, target in (("CANDIDATE", "QUALIFIED"), ("QUALIFIED", "APPROVED")):
        if record.get("state") != source:
            continue
        if source == "QUALIFIED" and record.get("qualification") != qualification:
            raise AcceptanceError("Pending qualification differs; do not replace its evidence")
        change = {
            "expectedRevision": record["revision"],
            "state": target,
            "reason": "Explicit operator authorization after automated M04 integration evidence checks.",
        }
        if target == "QUALIFIED":
            change["qualification"] = qualification
        updated = session.call("POST", selected.baseline_path + "/transitions", change)["body"]
        match(updated, expected)
        if (
            updated.get("state") != target
            or updated["revision"] != record["revision"] + 1
            or updated.get("qualification") != qualification
        ):
            raise AcceptanceError(
                "Baseline transition response differs; retain state for inspection"
            )
        record = updated
    if record.get("state") != "APPROVED":
        raise AcceptanceError("Baseline version is retired or cannot be approved")
    return record


def source_qualification(
    documents: dict[str, dict[str, Any]], selected: Selection
) -> dict[str, Any]:
    """Separate environmental eligibility from analysis quality and observed variability."""
    evidence.decision(documents["analysis-result/v1"], selected, None)
    return evidence.qualification(documents["normalized-result/v1"], selected)


def receipt(record: dict[str, Any]) -> dict[str, Any]:
    """Retain structured baseline evidence, never free-text audit reasons/actors from an API body."""
    return {
        key: record[key]
        for key in (
            "id",
            "version",
            "revision",
            "state",
            "testId",
            "sourceRunId",
            "artifact",
            "software",
            "workload",
            "environment",
            "dataset",
            "qualification",
        )
    }
