"""Checkout operator evidence uses authenticated registrations and original report bytes."""

import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import checkout_report

RUN = "perf-20261125-192800-abcdef12"


def claim(filename, kind, identity):
    return {
        "id": identity,
        "runId": RUN,
        "kind": "normalized",
        "format": kind,
        "mediaType": "application/json",
        "uri": f"s3://perfeng-artifacts/runs/{RUN}/{filename}",
        "sha256": hashlib.sha256(b"fixture").hexdigest(),
        "sizeBytes": 7,
    }


class ReportRegistrationTests(unittest.TestCase):
    def test_read_route_accepts_only_one_exact_registered_report(self):
        report = claim(
            "analysis-result.json", "analysis-result/v1", "aaaa0000-1111-4222-8333-000000000001"
        )
        self.assertEqual(checkout_report.report_registration(RUN, {"artifacts": [report]}), report)
        for change in (
            {"runId": "foreign"},
            {"uri": report["uri"] + ".other"},
            {"sizeBytes": True},
            {"sha256": "x"},
            {"id": "fake"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                checkout_report.report_registration(RUN, {"artifacts": [{**report, **change}]})
        with self.assertRaises(ValueError):
            checkout_report.report_registration(RUN, {"artifacts": [report, report]})


def fixture_contracts(root, origin, revision):
    """Use a small upstream schema fixture in the standalone offline test tier."""
    assert root.name == "perfeng-contracts"
    assert origin == "https://github.com/stanimirivanov/perfeng-contracts.git"
    assert revision == "badd9206a88a48042dda54a25c6e362088d23858"
    schema_files = {
        "schemas/analysis/v1/analysis-result.schema.json": ["createdAt"],
        "schemas/policy/v1/performance-policy.schema.json": ["spec"],
        "schemas/artifact/v1/artifact-reference.schema.json": ["uri"],
    }
    manifest = {"version": "0.13.0", "contracts": [{"schema": name} for name in schema_files]}

    def read(path):
        if path == "contracts.json":
            return json.dumps(manifest).encode()
        return json.dumps(
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "$id": "https://perfeng.dev/" + path,
                "type": "object",
                "required": schema_files[path],
            }
        ).encode()

    return read


class CheckoutReportTests(unittest.TestCase):
    def setUp(self):
        if os.environ.get("PERFENG_TEST_CHECKOUT_CONTRACTS") != "1":
            self.enterContext(patch("scripts.checkout_report.revision_reader", fixture_contracts))
        self.candidate = claim(
            "normalized-result.json", "normalized-result/v1", "cccc0000-1111-4222-8333-000000000001"
        )
        self.registration = claim(
            "analysis-result.json", "analysis-result/v1", "aaaa0000-1111-4222-8333-000000000001"
        )
        self.metric = {"name": "api.http.duration", "statistic": "p95", "unit": "ms"}
        self.rule: dict = {"id": "checkout-latency", "metric": self.metric, "slo": {"max": 500}}
        self.policy: dict = {
            "kind": "PerformancePolicy",
            "apiVersion": "performance.perfeng.io/v1",
            "metadata": {"name": "checkout-api", "version": "1.0.0", "owner": "platform-team"},
            "spec": {"mode": "observe", "missingData": "inconclusive", "rules": [self.rule]},
        }
        binding = {"id": "checkout-api", "version": "1.0.0", "sha256": "b" * 64}
        self.request = {"testSuite": "checkout-api", "profile": "smoke", "policy": binding}
        self.run_snapshot = {
            "id": RUN,
            "revision": 5,
            "request": self.request,
            "state": "COMPLETED",
        }
        producer = {
            "name": "perfeng-analysis",
            "version": "0.8.1",
            "image": "example.invalid/analysis@sha256:" + "a" * 64,
        }
        self.entry = {
            "testId": "checkout-api",
            "profile": "smoke",
            "contractsVersion": "0.13.0",
            "reportProducer": producer,
        }
        self.evaluation: dict = {
            "ruleId": "checkout-latency",
            "metric": self.metric,
            "slo": {"status": "PASS", "reasons": [], "value": 500},
            "quality": {"status": "PASS", "reasons": []},
            "regression": {"status": "NOT_EVALUATED", "reasons": ["No baseline rule"]},
        }
        self.report: dict = {
            "kind": "AnalysisResult",
            "schemaVersion": 1,
            "contractsVersion": "0.13.0",
            "runId": RUN,
            "testId": "checkout-api",
            "createdAt": "2026-11-25T19:28:00Z",
            "producer": producer,
            "policy": {**binding, "mode": "observe"},
            "blocking": False,
            "candidateArtifact": dict(self.candidate),
            "referenceArtifacts": [],
            "evaluations": [self.evaluation],
        }

    def decide(self, content=None, register=True):
        body = json.dumps(self.report).encode() if content is None else content
        if register:
            self.registration.update(sizeBytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        return checkout_report.validate_report(
            root=Path(__file__).resolve().parents[1],
            run_id=RUN,
            request=self.request,
            entry=self.entry,
            policy=self.policy,
            run=self.run_snapshot,
            registrations={"artifacts": [self.registration, self.candidate]},
            content=body,
        )

    def test_boundary_value_is_met_and_verified_by_digest_and_size(self):
        self.assertEqual(self.decide()["verdict"], "MET")
        for body in (b"X", b"X" * self.registration["sizeBytes"]):
            with self.subTest(size=len(body)), self.assertRaises(ValueError):
                self.decide(body, register=False)

    def test_negative_checkout_latency_cannot_meet_the_slo(self):
        self.evaluation["slo"]["value"] = -1
        with self.assertRaises(ValueError):
            self.decide()

    def test_invalid_measurement_preserves_quality_and_slo_reasons(self):
        self.evaluation["quality"] = {"status": "INVALID", "reasons": ["Invalid measurement"]}
        self.evaluation["slo"] = {"status": "INCONCLUSIVE", "reasons": ["No usable sample"]}
        outcome = self.decide()
        self.assertEqual(outcome["verdict"], "UNRESOLVED")
        self.assertEqual(outcome["quality"], "INVALID")
        self.assertEqual(outcome["qualityReasons"], ["Invalid measurement"])
        self.assertEqual(outcome["sloReasons"], ["No usable sample"])

    def test_selects_target_by_id_and_selector_with_other_rules_in_any_order(self):
        other = {
            "id": "checkout-maximum",
            "metric": {**self.metric, "statistic": "max"},
            "slo": {"max": 2000},
        }
        self.policy["spec"]["rules"].insert(0, other)
        other_evaluation = {**self.evaluation, "ruleId": other["id"], "metric": other["metric"]}
        self.report["evaluations"].append(other_evaluation)
        self.evaluation["slo"] = {"status": "FAIL", "reasons": ["above max"], "value": 501}
        self.assertEqual(
            self.decide(),
            {
                "verdict": "EXCEEDED",
                "ruleId": "checkout-latency",
                "metric": self.metric,
                "quality": "PASS",
                "slo": "FAIL",
                "p95Ms": 501,
                "maxMs": 500,
            },
        )
        for evaluations in (
            [self.evaluation],
            [other_evaluation, other_evaluation],
            [self.evaluation, {**other_evaluation, "metric": self.metric}],
            [
                self.evaluation,
                {**other_evaluation, "slo": {"status": "PASS", "value": 2001, "reasons": []}},
            ],
        ):
            self.report["evaluations"] = evaluations
            with self.subTest(evaluations=evaluations), self.assertRaises(ValueError):
                self.decide()

    def test_only_coherent_quality_evidence_can_produce_slo_verdict(self):
        self.rule["quality"] = {"minSamples": 10, "maxCv": 0.1}
        self.evaluation["quality"] = {
            "status": "INCONCLUSIVE",
            "samples": 5,
            "reasons": ["Too few samples"],
        }
        with self.assertRaises(ValueError):
            self.decide()
        self.evaluation["slo"] = {"status": "INCONCLUSIVE", "reasons": ["Insufficient quality"]}
        self.assertEqual(self.decide()["verdict"], "UNRESOLVED")
        self.evaluation["slo"] = {"status": "PASS", "value": 220, "reasons": []}
        for quality in (
            {"status": "PASS", "reasons": []},
            {"status": "PASS", "reasons": [], "samples": 11, "cv": 0.2},
            {"status": "PASS", "reasons": [], "samples": 9, "cv": 0.1},
        ):
            self.evaluation["quality"] = quality
            with self.subTest(quality=quality), self.assertRaises(ValueError):
                self.decide()
        self.evaluation["quality"] = {"status": "PASS", "reasons": [], "samples": 10, "cv": 0.1}
        self.assertEqual(self.decide()["verdict"], "MET")

    def test_foreign_candidate_and_structurally_invalid_met_report_give_no_verdict(self):
        self.report["candidateArtifact"]["sha256"] = "d" * 64
        with self.assertRaises(ValueError):
            self.decide()
        self.report["candidateArtifact"] = self.candidate
        del self.report["createdAt"]
        with self.assertRaises(ValueError):
            self.decide()

    def test_original_run_request_producer_and_policy_must_match(self):
        for target, field, value in (
            (self.run_snapshot, "state", "FAILED"),
            (self.run_snapshot, "id", "another-run"),
            (self.run_snapshot, "request", {**self.request, "profile": "load"}),
            (self.report, "policy", {**self.report["policy"], "sha256": "a" * 64}),
            (self.report, "producer", {**self.report["producer"], "version": "1.2.3"}),
            (self.report, "contractsVersion", "0.22.0"),
            (self.report, "referenceArtifacts", [self.candidate]),
        ):
            with (
                self.subTest(field=field),
                patch.dict(target, {field: value}),
                self.assertRaises(ValueError),
            ):
                self.decide()

    def test_strict_json_and_unknown_contract_selection_have_no_verdict(self):
        body = json.dumps(self.report).encode()
        for content in (body[:-1] + b',"blocking":false}', body.replace(b"500", b"1e999"), b"[]"):
            with self.subTest(content=content), self.assertRaises(ValueError):
                self.decide(content)
        self.entry["contractsVersion"] = "0.22.0"
        with self.assertRaises(ValueError):
            self.decide()

    def test_malformed_registration_and_deeply_nested_report_fail_with_private_diagnostics(self):
        for invalid in (None, [], {"artifacts": [None]}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                checkout_report.report_registration(RUN, invalid)
        for content in (b'{"private-marker":0,"private-marker":1}', b"[" * 2000 + b"]" * 2000):
            with self.assertRaises(ValueError) as error:
                self.decide(content)
            self.assertNotIn("private-marker", str(error.exception))

    def test_secondary_regression_rule_may_be_inconclusive_without_a_baseline(self):
        other = {
            "id": "checkout-comparison",
            "metric": self.metric,
            "regression": {
                "direction": "lower-is-better",
                "practicalDifference": {"kind": "absolute", "value": 20},
                "reference": {"baselineId": "approved-checkout", "version": "1.0.0"},
            },
        }
        self.policy["spec"]["rules"].append(other)
        self.report["evaluations"].append(
            {
                **self.evaluation,
                "ruleId": other["id"],
                "slo": {"status": "NOT_EVALUATED", "reasons": ["No configured SLO"]},
                "regression": {"status": "INCONCLUSIVE", "reasons": ["No baseline available"]},
            }
        )
        self.assertEqual(self.decide()["verdict"], "MET")
