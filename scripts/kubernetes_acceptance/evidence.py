"""Acceptance assertions over exact product evidence; not a second analysis engine."""

from __future__ import annotations

import json
import math
from datetime import datetime
from itertools import pairwise
from typing import Any

from scripts import kubernetes_benchmark_contract as contract
from scripts import kubernetes_benchmark_profile as cohort
from scripts.kubernetes_acceptance.errors import AcceptanceError
from scripts.kubernetes_acceptance.schemas import Schemas
from scripts.kubernetes_acceptance.selection import Selection
from scripts.kubernetes_acceptance.transport import RUN, Session
from scripts.workspace import unique_object

FORMATS = {
    "kube-burner-job-summary-json",
    "kube-burner-pod-latency-json",
    "kube-burner-pod-latency-quantiles-json",
    "kubernetes-benchmark-inputs-json",
    "kubernetes-benchmark-evidence/v1",
    "runner-log",
    "raw-result/v2",
    "normalized-result/v1",
    "analysis-result/v1",
}


def timestamp(value: str) -> datetime:
    """Require an offset-qualified instant for coverage and cleanup comparisons."""
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise AcceptanceError("Acceptance timestamp must carry its timezone")
    return result


def run_snapshot(
    value: dict[str, Any], run_id: str, selected: Selection, revision: int = 1
) -> None:
    """Fence every observation to the same submitted request and monotonic revision."""
    if (
        RUN.fullmatch(run_id) is None
        or value.get("id") != run_id
        or value.get("request") != selected.request
        or type(value.get("revision")) is not int
        or value["revision"] < revision
    ):
        raise AcceptanceError("Run identity, immutable request or revision changed")


def eligibility(value: dict[str, Any], raw: dict[str, Any], selected: Selection) -> None:
    """Require accepted measurement scope and full-window coverage, never promotion/preflight evidence."""
    contract.validate(value)
    checks = value["checks"]
    if len(checks) != 6 or {item["id"] for item in checks} != {
        "cluster-identity",
        "node-readiness",
        "placement",
        "capacity",
        "resource-pressure",
        "interference",
    }:
        raise AcceptanceError("Measurement must contain each required check exactly once")
    if (
        value["status"] != "ACCEPTED"
        or any(item["status"] != "PASS" for item in value["checks"])
        or value["scope"]
        != {
            "kind": "measurement",
            "runId": raw["runId"],
            "testId": selected.entry["testId"],
            "workload": selected.entry["workload"],
        }
        or value["environment"] != selected.entry["environment"]
        or value["target"]
        != {
            "clusterId": "perfeng-local",
            "kubeSystemUid": selected.entry["benchmark"]["clusterUid"],
        }
        or timestamp(value["captureWindow"]["start"]) > timestamp(raw["measurementWindow"]["start"])
        or timestamp(value["captureWindow"]["end"]) < timestamp(raw["measurementWindow"]["end"])
    ):
        raise AcceptanceError(
            "Measurement eligibility is unknown, rejected, foreign or does not cover execution"
        )
    profile = cohort.load()
    identity, _ = cohort.environment(profile, value["target"]["kubeSystemUid"], value["nodes"])
    if identity != selected.entry["environment"] or value["profile"] != profile.identity():
        raise AcceptanceError(
            "Measurement inventory/profile does not reconstruct the selected fingerprint"
        )
    samples: dict[str, list[datetime]] = {node["uid"]: [] for node in value["nodes"]}
    nodes = {node["uid"]: node for node in value["nodes"]}
    if len(samples) != 3 or {node["name"] for node in nodes.values()} != set(profile.nodes):
        raise AcceptanceError("Measurement inventory is duplicated or incomplete")
    for sample in value["samples"]:
        uid, usage = sample["nodeUid"], sample["usage"]
        at = timestamp(sample["observedAt"])
        if uid not in nodes or usage["source"] != "metrics-api":
            raise AcceptanceError("Measurement has unknown node or unavailable usage")
        conditions = sample["conditions"]
        start, end = (timestamp(usage[key]) for key in ("windowStart", "windowEnd"))
        if (
            conditions["ready"] != "TRUE"
            or any(
                conditions[key] != "FALSE"
                for key in ("memoryPressure", "diskPressure", "pidPressure")
            )
            or not timestamp(value["captureWindow"]["start"])
            <= start
            < end
            <= at
            <= timestamp(value["captureWindow"]["end"])
            or (at - end).total_seconds() > profile.metric_age
            or (end - start).total_seconds() > profile.metric_window
            or usage["cpuMilliCores"]
            > nodes[uid]["allocatable"]["cpuMilliCores"] * profile.cpu_ratio
            or usage["memoryBytes"]
            > nodes[uid]["allocatable"]["memoryBytes"] * profile.memory_ratio
            or (samples[uid] and at <= samples[uid][-1])
        ):
            raise AcceptanceError("Accepted eligibility contradicts its node/usage observations")
        samples[uid].append(at)
    first, last = (timestamp(raw["measurementWindow"][key]) for key in ("start", "end"))
    if first >= last:
        raise AcceptanceError("Raw measurement window is not ordered")
    for times in samples.values():
        if (
            len(times) < 2
            or (times[0] - first).total_seconds() > 30
            or (last - times[-1]).total_seconds() > 30
            or any((right - left).total_seconds() > 30 for left, right in pairwise(times))
        ):
            raise AcceptanceError("Measurement lacks complete bounded before/after node coverage")


def qualification(normalized: dict[str, Any], selected: Selection) -> dict[str, Any]:
    """Record observed sample/CV evidence for the policy metric without inventing thresholds."""
    rule = selected.policy["spec"]["rules"][0]
    matches = [
        item for item in normalized["results"] if item["metric"]["name"] == rule["metric"]["name"]
    ]
    if len(matches) != 1 or matches[0]["metric"]["unit"] != rule["metric"]["unit"]:
        raise AcceptanceError("Required normalized policy metric is missing or ambiguous")
    distribution = matches[0]["distribution"]
    count, cv = distribution.get("samples"), distribution.get("cv")
    if (
        type(count) is not int
        or count < rule["quality"]["minSamples"]
        or isinstance(cv, bool)
        or not isinstance(cv, (int, float))
        or not math.isfinite(cv)
        or cv < 0
    ):
        raise AcceptanceError("Baseline requires actual sufficient samples and finite observed CV")
    return {"status": "PASSED", "reasons": [], "sampleCount": count, "maximumCv": cv}


def artifacts(
    session: Session,
    run_id: str,
    selected: Selection,
    schemas: Schemas,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Read and attest all nine artifacts, their schema/provenance and the raw-to-normalized closure."""
    collection = session.call("GET", f"/v1/runs/{run_id}/artifacts")["body"]
    claims = collection.get("artifacts")
    if not isinstance(claims, list) or len(claims) != len(FORMATS):
        raise AcceptanceError("Incomplete benchmark artifact set")
    by_format = {item.get("format"): item for item in claims}
    if (
        set(by_format) != FORMATS
        or len({item.get("id") for item in claims}) != len(claims)
        or len({item.get("uri") for item in claims}) != len(claims)
    ):
        raise AcceptanceError("Duplicate or unexpected benchmark artifact identities/formats")
    documents = {}
    for kind, claim in by_format.items():
        schemas.validate("artifact/v1/artifact-reference.schema.json", claim)
        expected_kind = (
            "normalized" if kind in {"normalized-result/v1", "analysis-result/v1"} else "raw"
        )
        if claim["kind"] != expected_kind:
            raise AcceptanceError("Artifact kind differs from its evidence role")
        content = session.read(claim, run_id)
        if kind.startswith("kube-burner-"):
            observations = json.loads(
                content,
                object_pairs_hook=unique_object,
                parse_constant=contract.invalid_number,
                parse_float=contract.finite_float,
            )
            if (
                not isinstance(observations, list)
                or not observations
                or any(
                    not isinstance(item, dict) or item.get("uuid") != run_id
                    for item in observations
                )
            ):
                raise AcceptanceError("Kube-burner observations are empty or foreign")
            if kind == "kube-burner-job-summary-json" and (
                len(observations) != 1 or observations[0].get("passed") is not True
            ):
                raise AcceptanceError("Kube-burner job summary is not successful")
            if kind == "kube-burner-pod-latency-json" and (
                len(observations) != 10
                or {item.get("jobIteration") for item in observations} != set(range(10))
            ):
                raise AcceptanceError(
                    "Contained workload does not contain its ten unique observations"
                )
        elif kind != "runner-log":
            documents[kind] = contract.document(content)
    raw, normalized, report = (
        documents[name] for name in ("raw-result/v2", "normalized-result/v1", "analysis-result/v1")
    )
    for name, value in (
        ("raw-result/v2/raw-result.schema.json", raw),
        ("normalized-result/v1/normalized-result.schema.json", normalized),
        ("analysis/v1/analysis-result.schema.json", report),
    ):
        schemas.validate(name, value)
        if value["runId"] != run_id or value["testId"] != selected.entry["testId"]:
            raise AcceptanceError("Foreign benchmark product document")
    expected_raw = [
        item
        for kind, item in by_format.items()
        if kind not in {"raw-result/v2", "normalized-result/v1", "analysis-result/v1"}
    ]
    if (
        raw["contractsVersion"] != "0.22.0"
        or raw["workload"] != selected.entry["workload"]
        or raw["producer"] != selected.entry["rawProducer"]
        or sorted(raw["artifacts"], key=lambda item: item["id"])
        != sorted(expected_raw, key=lambda item: item["id"])
        or normalized["contractsVersion"] != "0.22.0"
        or normalized["workload"] != raw["workload"]
        or normalized["measurementWindow"] != raw["measurementWindow"]
        or normalized["producer"] != selected.entry["normalizerProducer"]
        or normalized["sourceArtifacts"] != [by_format["kube-burner-pod-latency-json"]]
        or report["candidateArtifact"] != by_format["normalized-result/v1"]
        or report["producer"] != selected.entry["reportProducer"]
        or report["policy"] != {**selected.request["policy"], "mode": "observe"}
        or report["contractsVersion"] != "0.22.0"
        or report["blocking"] is not False
    ):
        raise AcceptanceError(
            "Product evidence provenance, input closure or policy binding differs"
        )
    resolved = documents["kubernetes-benchmark-inputs-json"]
    if (
        resolved["runId"] != run_id
        or resolved["workload"] != raw["workload"]
        or resolved["candidateImage"] != selected.request["candidate"]["image"]
        or resolved["templateSha256"] != selected.entry["benchmark"]["templateSha256"]
        or resolved["substitutions"]["namespace"] != "perfeng-benchmark-" + run_id
    ):
        raise AcceptanceError("Resolved benchmark candidate/recipe differs")
    eligibility(documents["kubernetes-benchmark-evidence/v1"], raw, selected)
    count = qualification(normalized, selected)["sampleCount"]
    if (
        count != 10
        or len(report["evaluations"]) != 1
        or report["evaluations"][0]["quality"].get("samples") != count
    ):
        raise AcceptanceError("Report sample count differs from normalized observations")
    return by_format, documents


def decision(report: dict[str, Any], selected: Selection, baseline: dict[str, Any] | None) -> str:
    """Require rule coverage and an evaluated exact reference; an observe-mode regression may FAIL."""
    rule = selected.policy["spec"]["rules"][0]
    evaluations = report.get("evaluations")
    if not isinstance(evaluations, list) or len(evaluations) != 1:
        raise AcceptanceError("Incomplete report rule coverage")
    result = evaluations[0]
    if (
        result["ruleId"] != rule["id"]
        or result["metric"] != rule["metric"]
        or result["quality"]["status"] != "PASS"
    ):
        raise AcceptanceError("Policy evaluation is foreign or quality is not PASS")
    regression = result["regression"]
    if baseline is None:
        if report["referenceArtifacts"] or regression["status"] != "INCONCLUSIVE":
            raise AcceptanceError("Missing reference must remain explicitly inconclusive")
    elif (
        report["referenceArtifacts"] != [baseline["artifact"]]
        or regression.get("referenceArtifactId") != baseline["artifact"]["id"]
        or regression["status"] not in {"PASS", "FAIL"}
    ):
        raise AcceptanceError("Decision lacks an evaluated exact approved baseline")
    return regression["status"]
