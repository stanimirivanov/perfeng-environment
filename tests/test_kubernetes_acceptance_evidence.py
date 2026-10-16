"""Deterministic evidence/approval negatives; synthetic fixtures are not live acceptance."""

import copy
import json
import unittest
from unittest.mock import Mock, patch

from kubernetes_acceptance_fixtures import ROOT, RUN, fixture
from referencing.exceptions import Unresolvable

from scripts.kubernetes_acceptance import baseline, evidence, selection
from scripts.kubernetes_acceptance.schemas import Schemas


class AcceptanceEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.selected, self.claims, self.values, self.contents = fixture()

    def test_complete_closure_checks_bytes_through_transport_and_all_product_schemas(self):
        session, schemas = Mock(), Mock()
        session.call.return_value = {"body": {"artifacts": list(self.claims.values())}}
        session.read.side_effect = lambda claim, run_id: self.contents[claim["format"]]
        claims, documents = evidence.artifacts(session, RUN, self.selected, schemas)
        self.assertEqual(claims, self.claims)
        self.assertEqual(session.read.call_count, 9)
        self.assertEqual(schemas.validate.call_count, 12)
        self.assertEqual(documents["raw-result/v2"], self.values["raw-result/v2"])
        self.assertEqual(
            evidence.qualification(documents["normalized-result/v1"], self.selected)["sampleCount"],
            10,
        )

    def test_incomplete_duplicate_and_foreign_closures_are_not_success(self):
        for label, mutate in {
            "missing": lambda claims: claims.pop(),
            "duplicate": lambda claims: claims.__setitem__(0, claims[1]),
            "unexpected": lambda claims: claims[0].update(format="unreviewed"),
        }.items():
            with self.subTest(label=label):
                claims = copy.deepcopy(list(self.claims.values()))
                mutate(claims)
                session = Mock()
                session.call.return_value = {"body": {"artifacts": claims}}
                with self.assertRaises(ValueError):
                    evidence.artifacts(session, RUN, self.selected, Mock())
                session.read.assert_not_called()

    def test_product_provenance_and_exact_reference_closure_cannot_be_relabelled(self):
        for label, mutate in {
            "raw source": lambda values: values["raw-result/v2"]["producer"].update(
                version="other"
            ),
            "raw closure": lambda values: values["raw-result/v2"]["artifacts"].pop(),
            "normalized workload": lambda values: values["normalized-result/v1"]["workload"].update(
                id="other"
            ),
            "normalized source": lambda values: values["normalized-result/v1"].update(
                sourceArtifacts=[]
            ),
            "candidate": lambda values: values["kubernetes-benchmark-inputs-json"].update(
                candidateImage="other"
            ),
            "blocking": lambda values: values["analysis-result/v1"].update(blocking=True),
            "report policy": lambda values: values["analysis-result/v1"]["policy"].update(
                sha256="0" * 64
            ),
        }.items():
            with self.subTest(label=label):
                values = copy.deepcopy(self.values)
                mutate(values)
                session = Mock()
                session.call.return_value = {"body": {"artifacts": list(self.claims.values())}}
                session.read.side_effect = lambda claim, run_id, values=values: (
                    values[claim["format"]].encode()
                    if isinstance(values[claim["format"]], str)
                    else json.dumps(values[claim["format"]]).encode()
                )
                with self.assertRaises(ValueError):
                    evidence.artifacts(session, RUN, self.selected, Mock())

    def test_measurement_not_promotion_receipts_and_observed_eligibility_required(self):
        original = self.values["kubernetes-benchmark-evidence/v1"]
        raw = self.values["raw-result/v2"]
        evidence.eligibility(original, raw, self.selected)
        mutations = {
            "unknown": lambda value: value.update(status="UNKNOWN"),
            "rejected": lambda value: value.update(status="REJECTED"),
            "preflight": lambda value: value.update(scope={"kind": "preflight"}),
            "foreign run": lambda value: value["scope"].update(runId=RUN[:-1] + "2"),
            "partial capture": lambda value: value["captureWindow"].update(
                end="2026-10-16T12:00:19Z"
            ),
            "contradictory readiness": lambda value: value["samples"][0]["conditions"].update(
                ready="FALSE"
            ),
            "contradictory pressure": lambda value: value["samples"][0]["conditions"].update(
                memoryPressure="TRUE"
            ),
            "over-budget": lambda value: value["samples"][0]["usage"].update(cpuMilliCores=4000),
            "inventory drift": lambda value: value["nodes"][0].update(
                containerRuntimeVersion="containerd://other"
            ),
            "missing samples": lambda value: value.update(samples=value["samples"][:3]),
            "out of order": lambda value: value.update(samples=list(reversed(value["samples"]))),
        }
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                value = copy.deepcopy(original)
                mutate(value)
                with self.assertRaises(ValueError):
                    evidence.eligibility(value, raw, self.selected)

    def test_real_sample_count_and_cv_are_required_without_fabrication(self):
        for field, value in (
            ("samples", None),
            ("samples", 9),
            ("samples", True),
            ("cv", None),
            ("cv", float("inf")),
            ("cv", -1),
        ):
            with self.subTest(field=field, value=value):
                normalized = copy.deepcopy(self.values["normalized-result/v1"])
                normalized["results"][0]["distribution"][field] = value
                with self.assertRaises(ValueError):
                    evidence.qualification(normalized, self.selected)

    def test_observe_mode_fail_is_evaluated_but_inconclusive_or_wrong_baseline_is_not(self):
        report = copy.deepcopy(self.values["analysis-result/v1"])
        record = {"artifact": self.claims["normalized-result/v1"]}
        report["referenceArtifacts"] = [record["artifact"]]
        regression = report["evaluations"][0]["regression"]
        regression["referenceArtifactId"] = record["artifact"]["id"]
        for status in ("PASS", "FAIL"):
            regression["status"] = status
            self.assertEqual(evidence.decision(report, self.selected, record), status)
        for status in ("INCONCLUSIVE", "NOT_APPLICABLE", "UNKNOWN"):
            regression["status"] = status
            with self.assertRaises(ValueError):
                evidence.decision(report, self.selected, record)
        regression.update(status="PASS", referenceArtifactId="foreign")
        with self.assertRaises(ValueError):
            evidence.decision(report, self.selected, record)

    def test_stale_or_foreign_run_snapshots_fail_before_following_their_ids(self):
        current = {"id": RUN, "request": self.selected.request, "revision": 4}
        evidence.run_snapshot(current, RUN, self.selected, 4)
        for patch_value in ({"revision": 3}, {"revision": True}, {"id": "other"}, {"request": {}}):
            with self.assertRaises(ValueError):
                evidence.run_snapshot({**current, **patch_value}, RUN, self.selected, 4)

    def test_schema_registry_is_closed_and_enforces_real_schema_keywords(self):
        uri = "https://example.invalid/acceptance.schema.json"
        schemas = Schemas(
            {
                "test": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": uri,
                    "type": "object",
                    "required": ["valid"],
                    "additionalProperties": False,
                    "properties": {"valid": {"const": True}},
                }
            }
        )
        schemas.validate("test", {"valid": True})
        with self.assertRaises(ValueError):
            schemas.validate("test", {"valid": False})
        remote = Schemas(
            {
                "test": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "$id": uri,
                    "$ref": "https://outside.invalid/schema",
                }
            }
        )
        with self.assertRaises(Unresolvable):
            remote.validate("test", {})

    @patch("scripts.kubernetes_acceptance.selection.release.revision_reader")
    @patch("scripts.kubernetes_acceptance.selection.release.validate")
    def test_selection_reads_pinned_objects_and_keeps_actual_candidate_revision(
        self, validate, reader
    ):
        validate.return_value = self.selected.lock
        prefix = self.selected.lock.bundle_path + "/"
        reader.return_value = {
            prefix + "registry.json": json.dumps({"entries": [self.selected.entry]}).encode(),
            prefix + self.selected.entry["policy"]: json.dumps(self.selected.policy).encode(),
        }.__getitem__
        loaded = selection.load(ROOT, "d" * 40)
        self.assertEqual(loaded.request["candidate"]["gitSha"], "d" * 40)
        self.assertEqual(
            loaded.request["candidate"]["image"], self.selected.entry["candidateImages"][0]
        )
        reader.assert_called_once_with(
            ROOT.parent / "perfeng-k8s",
            selection.release.DEPLOYMENT_REPOSITORY,
            self.selected.lock.deployment_revision,
        )


class AcceptanceBaselineTests(unittest.TestCase):
    def setUp(self):
        self.selected, claims, values, _ = fixture()
        self.expected = baseline.create_request(RUN, claims["normalized-result/v1"], self.selected)
        self.qualification = evidence.qualification(values["normalized-result/v1"], self.selected)

    def record(self, state, revision):
        return {
            **self.expected,
            "state": state,
            "revision": revision,
            "qualification": self.qualification,
        }

    def test_missing_or_pending_baseline_is_never_implicitly_approved(self):
        for existing in (None, self.record("CANDIDATE", 1), self.record("QUALIFIED", 2)):
            session = Mock()
            with self.assertRaises(ValueError):
                baseline.approve(
                    session,
                    self.selected,
                    self.expected,
                    self.qualification,
                    existing,
                    authorized=False,
                )
            session.call.assert_not_called()

    def test_explicit_approval_uses_observed_statistics_and_revision_fenced_transitions(self):
        session = Mock()
        session.call.side_effect = [
            {"body": self.record("CANDIDATE", 1)},
            {"body": self.record("QUALIFIED", 2)},
            {"body": self.record("APPROVED", 3)},
        ]
        record = baseline.approve(
            session, self.selected, self.expected, self.qualification, None, authorized=True
        )
        self.assertEqual(record["state"], "APPROVED")
        qualified = session.call.call_args_list[1].args[2]
        self.assertEqual(qualified["qualification"], self.qualification)
        self.assertEqual(qualified["expectedRevision"], 1)
        self.assertEqual(session.call.call_args_list[2].args[2]["expectedRevision"], 2)

    def test_approved_exact_baseline_is_read_only_and_conflicts_are_preserved(self):
        session = Mock()
        record = self.record("APPROVED", 3)
        self.assertEqual(
            baseline.approve(
                session, self.selected, self.expected, self.qualification, record, authorized=False
            ),
            record,
        )
        for field, value in (
            ("environment", {}),
            ("artifact", {}),
            ("sourceRunId", "foreign"),
            ("qualification", {}),
            ("state", "RETIRED"),
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                baseline.approve(
                    session,
                    self.selected,
                    self.expected,
                    self.qualification,
                    {**record, field: value},
                    authorized=True,
                )
        session.call.assert_not_called()

    def test_explicit_retry_can_resume_only_exact_pending_qualification(self):
        session = Mock()
        session.call.return_value = {"body": self.record("APPROVED", 3)}
        baseline.approve(
            session,
            self.selected,
            self.expected,
            self.qualification,
            self.record("QUALIFIED", 2),
            authorized=True,
        )
        self.assertEqual(session.call.call_count, 1)
        self.assertEqual(session.call.call_args.args[2]["state"], "APPROVED")
