"""Campaign reports retain missing observations and explicit shadow pairs."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from scripts import argus_campaign as campaign

REPOSITORY = "stanimirivanov/perfeng-control-plane"
REPO = {
    "provider": "github",
    "host": "github.com",
    "providerRepositoryId": "1354503944",
    "owner": "stanimirivanov",
    "name": "perfeng-control-plane",
}
HEAD, BASE = "a" * 40, "b" * 40
KEYS = ("baseline-lifecycle", "integration-admission", "run-authentication", "run-lifecycle")


def evidence() -> dict[str, Any]:
    decisions = [
        {
            "testRepository": REPO,
            "suite": {
                "key": "control-plane-api",
                "family": "functional-api",
                "adapter": "generic-http",
            },
            "test": {"key": key},
            "outcome": "RUN_REQUIRED" if key == KEYS[0] else "SKIP_FOR_NOW",
        }
        for key in KEYS
    ]

    def revision(digest):
        return {"algorithm": "git-sha1", "digest": digest}

    manifest = {
        "apiVersion": "argus.dev/execution-manifest/v1",
        "family": "functional-api",
        "mode": "targeted",
        "change": {
            "sourceRepository": REPO,
            "baseRevision": revision(BASE),
            "headRevision": revision(HEAD),
            "pullRequest": {"number": 319},
            "trigger": {"deliveryId": "github-123-1"},
        },
        "catalog": {"sourceRepository": REPO, "revision": revision(BASE)},
        "decisions": decisions,
    }
    reference = {
        "apiVersion": manifest["apiVersion"],
        "sha256": hashlib.sha256(
            json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest(),
    }
    attempts = {}
    for stage, keys in (("selected", KEYS[:1]), ("full-suite", KEYS)):
        attempts[stage] = {
            "apiVersion": "argus.dev/execution-attempt/v1",
            "stage": stage,
            "attemptId": "github-123-1-" + stage,
            "manifest": reference,
            "testRepository": REPO,
            "testRevision": revision(HEAD),
            "adapter": {"id": "generic-http", "version": "1.0.0"},
            "outcome": "passed",
            "results": [
                {
                    "suiteKey": "control-plane-api",
                    "testKey": key,
                    "outcome": "passed",
                    "durationMs": 10,
                }
                for key in keys
            ],
        }
    return {
        "manifest": manifest,
        "plan": {
            "apiVersion": "argus.dev/functional-api-execution-plan/v1",
            "manifest": reference,
            "jobs": [
                {
                    "stage": stage,
                    "groupKey": "control-plane-api",
                    "testRepository": REPO,
                    "testRevision": revision(HEAD),
                    "adapter": "generic-http",
                    "testCount": count,
                }
                for stage, count in (("selected", 1), ("full-suite", 4))
            ],
        },
        "observation": {
            "schemaVersion": 1,
            "pullRequest": 319,
            "deliveryId": "github-123-1",
            "baseRevision": BASE,
            "testRevision": HEAD,
            "manifestSha256": reference["sha256"],
            "selected": {"status": "observed", "elapsedMs": 30, "exitCode": 0},
            "full": {"status": "observed", "elapsedMs": 20, "exitCode": 0},
        },
        **attempts,
    }


class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "artifacts"
        self.directory.mkdir()
        self.entry = {
            "repository": REPOSITORY,
            "kind": "functional-api",
            "runId": 123,
            "runAttempt": 1,
            "headSha": HEAD,
            "artifact": "artifacts",
        }
        self.documents: dict[str, Any] = evidence()

    def report(self, entries=None):
        for name, value in self.documents.items():
            (self.directory / (name + ".json")).write_text(json.dumps(value))
        path = self.root / "campaign.json"
        path.write_text(json.dumps({"schemaVersion": 1, "observations": entries or [self.entry]}))
        return campaign.aggregate(path)

    def test_paired_metrics_keep_native_and_runner_times_separate(self):
        report = self.report()
        row = report["observations"][0]
        self.assertEqual(row["status"], "paired")
        self.assertEqual(row["durationReductionMs"], 30)
        self.assertEqual(row["runnerElapsedReductionMs"], -10)
        self.assertIsNone(report["functional"]["failureRecallBasisPoints"])
        self.assertEqual(report["functional"]["pairedCount"], 1)
        self.assertNotIn("promotion", report)

    def test_misses_distinguish_not_selected_and_not_reproduced(self):
        self.documents["full-suite"]["outcome"] = "failed"
        self.documents["observation"]["full"]["exitCode"] = 1
        for row in self.documents["full-suite"]["results"][:2]:
            row["outcome"] = "failed"
        row = self.report()["observations"][0]
        self.assertEqual(
            [miss["reason"] for miss in row["missedFailures"]], ["not-reproduced", "not-selected"]
        )
        self.assertEqual(row["failureRecallBasisPoints"], 0)

    def test_foreign_or_incomplete_attempt_is_invalid(self):
        for field in ("manifest", "testRevision", "results"):
            with self.subTest(field=field):
                self.documents = evidence()
                self.documents["full-suite"][field] = {} if field != "results" else []
                report = self.report()
                self.assertEqual(report["observations"][0]["status"], "invalid")
                self.assertEqual(report["functional"]["pairedCount"], 0)

    def test_missing_evidence_stays_in_denominator(self):
        other = self.entry | {"runId": 124, "artifact": "missing"}
        report = self.report([self.entry, other])
        self.assertEqual(report["observationCount"], 2)
        self.assertEqual(report["observations"][1]["status"], "missing")
        self.assertEqual(report["functional"]["pairedCount"], 1)

    def test_duplicates_and_traversal_are_rejected(self):
        for entries in ([self.entry, self.entry], [self.entry | {"artifact": "../outside"}]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                self.report(entries)

    def test_symlink_evidence_is_invalid(self):
        self.documents.pop("selected")
        outside = self.root / "private.json"
        outside.write_text('{"secret":"private"}')
        (self.directory / "selected.json").symlink_to(outside)
        report = self.report()
        self.assertEqual(report["observations"][0]["status"], "invalid")
        self.assertNotIn("private", json.dumps(report))

    def test_full_fallback_is_unpaired(self):
        self.documents = {"observation": self.documents["observation"]}
        self.documents["observation"]["selected"]["status"] = "missing"
        self.documents["observation"]["full"]["status"] = "fallback"
        report = self.report()
        self.assertEqual(report["observations"][0]["status"], "unpaired")
        self.assertEqual(report["functional"]["pairedCount"], 0)

    def test_failed_and_successful_integration_receipts_are_separate(self):
        self.documents = {"argus-shadow-receipt": {"schemaVersion": 1, "status": "failed"}}
        entry = self.entry | {
            "kind": "performance-integration",
            "repository": "stanimirivanov/perfeng-environment",
        }
        report = self.report([entry])
        self.assertEqual(report["integration"]["failedCount"], 1)
        self.assertEqual(report["functional"]["pairedCount"], 0)

    def test_empty_campaign_and_boolean_run_identity_are_rejected(self):
        with self.assertRaises(ValueError):
            self.report([self.entry | {"runId": True}])
        path = self.root / "empty.json"
        path.write_text('{"schemaVersion":1,"observations":[]}')
        with self.assertRaises(ValueError):
            campaign.aggregate(path)

    def test_skipped_control_is_incomplete_and_keeps_null_failure_recall(self):
        self.documents["full-suite"]["results"][0]["outcome"] = "skipped"
        self.documents["full-suite"]["outcome"] = "incomplete"
        self.documents["observation"]["full"]["exitCode"] = 1
        report = self.report()
        self.assertEqual(report["functional"]["pairedCount"], 1)
        self.assertIsNone(report["functional"]["failureRecallBasisPoints"])

    def test_recall_counts_caught_failures(self):
        self.documents["selected"]["results"][0]["outcome"] = "error"
        self.documents["selected"]["outcome"] = "error"
        self.documents["observation"]["selected"]["exitCode"] = 1
        self.documents["full-suite"]["results"][0]["outcome"] = "failed"
        self.documents["full-suite"]["outcome"] = "failed"
        self.documents["observation"]["full"]["exitCode"] = 1
        report = self.report()
        self.assertEqual(report["functional"]["failureRecallBasisPoints"], 10000)
        self.assertEqual(report["observations"][0]["missedFailures"], [])

    def test_passing_receipt_requires_all_reviewed_public_fields(self):
        self.documents = {
            "argus-shadow-receipt": {
                "schemaVersion": 1,
                "status": "passed",
                "referenceRunId": "perf-20261104-182431-8ad05269",
                "reviewedRunId": "perf-20261104-182542-e2499765",
                "reportSha256": "a" * 64,
                "projectionSha256": "b" * 64,
                "publicationLate": False,
                "retainedAnalysisEvents": 1,
            }
        }
        entry = self.entry | {
            "kind": "performance-integration",
            "repository": "stanimirivanov/perfeng-environment",
        }
        self.assertEqual(self.report([entry])["integration"]["passedCount"], 1)
        self.documents["argus-shadow-receipt"]["retainedAnalysisEvents"] = True
        self.assertEqual(self.report([entry])["integration"]["invalidCount"], 1)

    def test_empty_selection_keeps_complete_control_visible(self):
        for decision in self.documents["manifest"]["decisions"]:
            decision["outcome"] = "SKIP_FOR_NOW"
        manifest = self.documents["manifest"]
        digest = hashlib.sha256(
            json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        self.documents["plan"]["manifest"]["sha256"] = digest
        self.documents["full-suite"]["manifest"]["sha256"] = digest
        self.documents["observation"]["manifestSha256"] = digest
        self.documents["plan"]["jobs"] = self.documents["plan"]["jobs"][1:]
        self.documents["observation"]["selected"] = {"status": "empty"}
        row = self.report()["observations"][0]
        self.assertEqual(row["status"], "paired")
        self.assertEqual(row["selectedTestCount"], 0)
        self.assertEqual(row["durationReductionMs"], 40)

    def test_rerun_remains_visible_as_a_separate_observation(self):
        rerun = self.entry | {"runAttempt": 2, "artifact": "missing-rerun"}
        report = self.report([self.entry, rerun])
        self.assertEqual(report["observationCount"], 2)
        self.assertEqual([row["runAttempt"] for row in report["observations"]], [1, 2])

    def test_oversized_document_is_invalid(self):
        self.report()
        (self.directory / "manifest.json").write_bytes(b" " * (campaign.LIMIT + 1))
        path = self.root / "campaign.json"
        report = campaign.aggregate(path)
        self.assertEqual(report["observations"][0]["status"], "invalid")
