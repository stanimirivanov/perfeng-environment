"""Verify registered checkout report evidence against the selected product contract."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.control_plane_release import revision_reader
from scripts.kubernetes_acceptance.schemas import Schemas
from scripts.kubernetes_acceptance.transport import RUN
from scripts.kubernetes_benchmark_contract import REPOSITORY, document, finite_float, invalid_number
from scripts.workspace import unique_object

MAX_REPORT_BYTES = 16 * 1024 * 1024
UUID = re.compile(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}")
SHA256 = re.compile(r"[a-f0-9]{64}")


def _registered(run_id: str, registrations: object, kind: str, filename: str) -> dict[str, Any]:
    claims = registrations.get("artifacts") if isinstance(registrations, dict) else None
    if not RUN.fullmatch(run_id) or not isinstance(claims, list):
        raise ValueError("Missing Run identity or artifact registrations")
    ids: set[str] = set()
    uris: set[str] = set()
    matches = []
    for item in claims:
        if not isinstance(item, dict) or item.get("runId") != run_id:
            raise ValueError("Malformed or foreign artifact registration")
        identity, uri = item.get("id"), item.get("uri")
        if (
            not isinstance(identity, str)
            or not UUID.fullmatch(identity)
            or identity in ids
            or not isinstance(uri, str)
            or uri in uris
        ):
            raise ValueError("Invalid or ambiguous registered artifact identity")
        ids.add(identity)
        uris.add(uri)
        if item.get("format") == kind:
            matches.append(item)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {kind} registration")
    claim = matches[0]
    if (
        claim["uri"] != f"s3://perfeng-artifacts/runs/{run_id}/{filename}"
        or claim.get("kind") != "normalized"
        or claim.get("mediaType") != "application/json"
        or type(claim.get("sizeBytes")) is not int
        or claim["sizeBytes"] <= 0
        or not isinstance(claim.get("sha256"), str)
        or not SHA256.fullmatch(claim["sha256"])
    ):
        raise ValueError(f"Invalid {kind} artifact registration")
    return claim


def report_registration(run_id: str, registrations: object) -> dict[str, Any]:
    """Get the one bounded report registration before requesting an owner's original bytes."""
    claim = _registered(run_id, registrations, "analysis-result/v1", "analysis-result.json")
    if claim["sizeBytes"] > MAX_REPORT_BYTES:
        raise ValueError("Registered report exceeds the 16 MiB limit")
    return claim


# perfeng-analysis's checkout contract lock for the selected registry version.
CONTRACT_REVISION = "badd9206a88a48042dda54a25c6e362088d23858"
REPORT = "schemas/analysis/v1/analysis-result.schema.json"
ARTIFACT = "schemas/artifact/v1/artifact-reference.schema.json"
POLICY = "schemas/policy/v1/performance-policy.schema.json"
METRIC = {"name": "api.http.duration", "statistic": "p95", "unit": "ms"}


def load_schemas(root: Path, version: str) -> Schemas:
    """Resolve the reviewed schema registry from immutable local Git objects."""
    if version != "0.13.0":
        raise ValueError("Checkout contracts version does not match the reviewed revision")
    read = revision_reader(root.parent / "perfeng-contracts", REPOSITORY, CONTRACT_REVISION)
    manifest = document(read("contracts.json"))
    if manifest["version"] != version:
        raise ValueError("Checkout contracts release version mismatch")
    return Schemas(
        {item["schema"]: document(read(item["schema"])) for item in manifest["contracts"]}
    )


def validate_report(
    *,
    root: Path,
    run_id: str,
    request: dict,
    entry: dict,
    policy: dict,
    run: dict,
    registrations: dict,
    content: bytes,
) -> dict:
    """Return a single-Run checkout observation only for verified original report bytes."""
    claim = report_registration(run_id, registrations)
    if len(content) != claim["sizeBytes"] or hashlib.sha256(content).hexdigest() != claim["sha256"]:
        raise ValueError("Report bytes differ from the authenticated size or SHA-256")
    try:
        report = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=unique_object,
            parse_float=finite_float,
            parse_constant=invalid_number,
        )
    except (ValueError, RecursionError):
        raise ValueError("Report bytes are not a valid UTF-8 JSON document") from None
    schemas = load_schemas(root, entry["contractsVersion"])
    schemas.validate(REPORT, report)
    schemas.validate(POLICY, policy)
    for artifact in registrations["artifacts"]:
        schemas.validate(ARTIFACT, artifact)
    datetime.fromisoformat(report["createdAt"])
    candidate = _registered(run_id, registrations, "normalized-result/v1", "normalized-result.json")
    if (
        not isinstance(run, dict)
        or run.get("id") != run_id
        or run.get("state") != "COMPLETED"
        or run.get("request") != request
        or type(run.get("revision")) is not int
        or run["revision"] < 1
        or report["runId"] != run_id
        or report["testId"] != entry["testId"]
        or entry["testId"] != "checkout-api"
        or request.get("testSuite") != entry["testId"]
        or request.get("profile") != entry["profile"]
        or entry["profile"] != "smoke"
        or report["contractsVersion"] != entry["contractsVersion"]
        or report["producer"] != entry["reportProducer"]
        or report["candidateArtifact"] != candidate
        or report["policy"] != {**request["policy"], "mode": policy["spec"]["mode"]}
        or report["policy"]["id"] != policy["metadata"]["name"]
        or report["policy"]["version"] != policy["metadata"]["version"]
        or report["blocking"] is not False
    ):
        raise ValueError(
            "Run, request, producer, candidate registration or selected policy differs"
        )
    rules = {rule["id"]: rule for rule in policy["spec"]["rules"]}
    outcomes = {item["ruleId"]: item for item in report["evaluations"]}
    if (
        len(rules) != len(policy["spec"]["rules"])
        or len(outcomes) != len(report["evaluations"])
        or rules.keys() != outcomes.keys()
    ):
        raise ValueError("Report must account for every selected policy rule exactly once")
    if report["referenceArtifacts"]:
        raise ValueError(
            "Checkout report claims baseline evidence outside this single-Run observation"
        )
    for rule_id, rule in rules.items():
        _check_evaluation(rule, outcomes[rule_id])
    rule = rules.get("checkout-latency", {})
    if rule.get("metric") != METRIC or set(rule.get("slo", {})) != {"max"}:
        raise ValueError("Missing selected checkout p95 maximum rule")
    evaluation = outcomes[rule["id"]]
    slo = evaluation["slo"]
    observation = {
        "verdict": {"PASS": "MET", "FAIL": "EXCEEDED"}.get(slo["status"], "UNRESOLVED"),
        "ruleId": rule["id"],
        "metric": rule["metric"],
        "quality": evaluation["quality"]["status"],
        "slo": slo["status"],
        "p95Ms": slo.get("value"),
        "maxMs": rule["slo"]["max"],
    }
    if observation["verdict"] == "UNRESOLVED":
        observation["qualityReasons"] = evaluation["quality"]["reasons"]
        observation["sloReasons"] = slo["reasons"]
    return observation


def _check_evaluation(rule: dict, evaluation: dict) -> None:
    if evaluation["metric"] != rule["metric"]:
        raise ValueError("A rule evaluation differs from its policy metric selector")
    quality, slo, regression = (evaluation[key] for key in ("quality", "slo", "regression"))
    if any(
        section["status"] != "PASS" and not section["reasons"]
        for section in (quality, slo, regression)
    ):
        raise ValueError("Non-passing outcomes must record their reasons")
    expected_regression = "INCONCLUSIVE" if "regression" in rule else "NOT_EVALUATED"
    if regression["status"] != expected_regression or set(regression) != {"status", "reasons"}:
        raise ValueError("Report claims baseline evidence outside the selected checkout policy")
    if quality["status"] == "PASS":
        required = rule.get("quality", {})
        if (
            "minSamples" in required
            and quality.get("samples", 0) < required["minSamples"]
            or "maxCv" in required
            and quality.get("cv", float("inf")) > required["maxCv"]
        ):
            raise ValueError("Quality PASS contradicts selected sampling or variability bounds")
    bounds = rule.get("slo")
    if bounds is None:
        if slo["status"] != "NOT_EVALUATED":
            raise ValueError("Rule without an SLO claims an SLO evaluation")
    elif quality["status"] == "PASS":
        if slo["status"] not in {"PASS", "FAIL"}:
            raise ValueError("Passing measurement lacks the configured SLO outcome")
        value = slo["value"]
        if rule["metric"] == METRIC and value < 0:
            raise ValueError("Checkout latency must be non-negative")
        inside = bounds.get("min", float("-inf")) <= value <= bounds.get("max", float("inf"))
        if (slo["status"] == "PASS") != inside:
            raise ValueError("SLO status contradicts the measured value or selected bounds")
    elif slo["status"] != "INCONCLUSIVE":
        raise ValueError("Unsuitable measurement quality cannot yield a decisive SLO outcome")
