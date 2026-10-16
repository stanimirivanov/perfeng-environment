"""Acceptance ordering, explicit consent, offline previews and retained partial evidence."""

import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from kubernetes_acceptance_fixtures import (
    CANCELLED,
    CANDIDATE,
    POD_UID,
    RUN,
    fixture,
    reservation,
)

from scripts.kubernetes_acceptance import __main__ as cli
from scripts.kubernetes_acceptance import baseline
from scripts.kubernetes_acceptance.journal import Journal
from scripts.kubernetes_acceptance.workflow import Acceptance


class AcceptanceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.selected, self.claims, self.documents, _ = fixture()
        self.session, self.lifecycle, self.journal = Mock(), Mock(), Mock()
        self.acceptance = Acceptance(
            self.session, self.lifecycle, self.selected, Mock(), self.journal
        )

    def current(self, run_id, state, revision=4):
        return {
            "id": run_id,
            "state": state,
            "revision": revision,
            "request": self.selected.request,
        }

    def test_missing_baseline_without_consent_creates_no_run_and_no_success_receipt(self):
        self.session.call.return_value = {"status": 404, "body": {"code": "NOT_FOUND"}}
        with self.assertRaisesRegex(ValueError, "approve-baseline"):
            self.acceptance.run(approve_baseline=False)
        self.assertEqual(self.session.call.call_count, 1)
        self.assertEqual(self.session.call.call_args.args[0], "GET")
        self.journal.finish.assert_not_called()

    def test_submission_intent_is_flushed_before_the_outcome_uncertain_write(self):
        events = []
        self.journal.record.side_effect = lambda *args, **kwargs: events.append((args[0], kwargs))

        def unavailable(*args, **kwargs):
            self.assertEqual(events[0][0], "submission-intent")
            self.assertEqual(events[0][1]["idempotencyKey"], kwargs["key"])
            raise ValueError("outcome unknown")

        self.session.call.side_effect = unavailable
        with self.assertRaises(ValueError):
            self.acceptance.submit("reference")
        self.assertEqual(self.session.call.call_count, 1)
        self.journal.finish.assert_not_called()

    def test_full_sequence_approves_only_observed_reference_then_proves_post_restart_decision(self):
        expected = baseline.create_request(RUN, self.claims["normalized-result/v1"], self.selected)
        qualification = {"status": "PASSED", "reasons": [], "sampleCount": 10, "maximumCv": 0.2}

        def record(state, revision):
            return {
                **expected,
                "state": state,
                "revision": revision,
                "qualification": qualification,
            }

        approved = record("APPROVED", 3)
        self.session.call.side_effect = [
            {"status": 404, "body": {"code": "NOT_FOUND"}},
            {"body": record("CANDIDATE", 1)},
            {"body": record("QUALIFIED", 2)},
            {"body": approved},
            {"body": self.current(CANCELLED, "CANCELLING", 5)},
            {"body": approved},
        ]
        self.session.name = "m04-acceptance-0123456789abcdef"
        self.lifecycle.executor.return_value = {"metadata": {"uid": POD_UID}}
        self.lifecycle.database.reservation.return_value = reservation()
        self.lifecycle.cleanup.return_value = reservation(released=True)
        self.lifecycle.restart.return_value = {"beforeUid": POD_UID, "afterUid": "new-pod-uid"}
        candidate_documents = copy.deepcopy(self.documents)
        report = candidate_documents["analysis-result/v1"]
        report["referenceArtifacts"] = [approved["artifact"]]
        report["evaluations"][0]["regression"] = {
            "status": "FAIL",
            "referenceArtifactId": approved["artifact"]["id"],
        }
        with (
            patch.object(
                self.acceptance, "submit", side_effect=[RUN, CANCELLED, CANDIDATE]
            ) as submit,
            patch.object(
                self.acceptance,
                "completed",
                side_effect=[(self.claims, self.documents), (self.claims, candidate_documents)],
            ) as completed,
            patch.object(
                self.acceptance,
                "observe",
                side_effect=[
                    self.current(CANCELLED, "PROVISIONING"),
                    self.current(CANCELLED, "CANCELLING", 5),
                    self.current(CANCELLED, "ABORTED", 6),
                    self.current(RUN, "COMPLETED", 10),
                    self.current(CANCELLED, "ABORTED", 6),
                ],
            ),
        ):
            self.acceptance.revisions[CANCELLED] = 4
            result = self.acceptance.run(approve_baseline=True)
        self.assertEqual(result["status"], "PASSED")
        self.assertEqual(result["regression"], "FAIL")
        self.assertEqual(submit.call_args_list[-1].args[0], "post-recovery-decision")
        self.assertEqual(completed.call_args_list[-1].args[0], CANDIDATE)
        self.lifecycle.restart.assert_called_once_with(CANCELLED, reservation())
        self.journal.finish.assert_called_once_with(result)
        self.assertEqual(self.journal.record.call_args.args[0], "acceptance-passed")

    def test_restart_failure_never_submits_successor_or_writes_success(self):
        self.lifecycle.executor.return_value = {"metadata": {"uid": POD_UID}}
        self.lifecycle.database.reservation.return_value = reservation()
        self.lifecycle.restart.side_effect = ValueError("stale UID")
        self.session.call.return_value = {"body": self.current(CANCELLED, "CANCELLING", 5)}
        with (
            patch.object(self.acceptance, "submit", return_value=CANCELLED) as submit,
            patch.object(
                self.acceptance,
                "observe",
                side_effect=[
                    self.current(CANCELLED, "RUNNING"),
                    self.current(CANCELLED, "CANCELLING", 5),
                ],
            ),
        ):
            self.acceptance.revisions[CANCELLED] = 4
            with self.assertRaisesRegex(ValueError, "stale UID"):
                self.acceptance.cancel_and_recover()
        self.assertEqual(submit.call_count, 1)
        self.lifecycle.cleanup.assert_not_called()
        self.journal.finish.assert_not_called()

    def test_missed_cancellation_window_does_not_turn_completed_run_into_acceptance(self):
        with (
            patch.object(self.acceptance, "submit", return_value=CANCELLED),
            patch.object(
                self.acceptance, "observe", return_value=self.current(CANCELLED, "COLLECTING")
            ),
            self.assertRaisesRegex(ValueError, "Missed"),
        ):
            self.acceptance.cancel_and_recover()
        self.lifecycle.restart.assert_not_called()
        self.session.call.assert_not_called()
        self.journal.finish.assert_not_called()

    def test_polling_has_monotonic_budget_and_does_not_sleep_in_tests(self):
        now = [0.0]
        self.acceptance.clock = lambda: now[0]
        self.acceptance.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
        with self.assertRaisesRegex(ValueError, "5s budget"):
            self.acceptance.wait("fixture", lambda: None, 5)
        self.assertEqual(now[0], 5)
        self.journal.finish.assert_not_called()


class AcceptanceEntryPointTests(unittest.TestCase):
    @patch("scripts.kubernetes_acceptance.__main__.execute")
    @patch("scripts.cluster.run", side_effect=AssertionError("Preview must not start processes"))
    def test_preview_is_offline_and_bad_confirmation_fails_before_execution(self, run, execute):
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main([]), 0)
            self.assertEqual(cli.main(["--execute", "--confirm", "another-cluster"]), 1)
            self.assertEqual(cli.main(["--execute", "--confirm", "perfeng-local"]), 1)
        self.assertIn('"preview": true', output.getvalue())
        self.assertIn("Supply --candidate-git-sha", output.getvalue())
        run.assert_not_called()
        execute.assert_not_called()

    @patch("scripts.kubernetes_acceptance.__main__.execute")
    def test_execution_dispatches_only_complete_explicit_inputs(self, execute):
        self.assertEqual(
            cli.main(
                [
                    "--execute",
                    "--confirm",
                    "perfeng-local",
                    "--candidate-git-sha",
                    "a" * 40,
                    "--approve-baseline",
                    "--reference-run",
                    RUN,
                ]
            ),
            0,
        )
        execute.assert_called_once_with("a" * 40, True, RUN)

    @patch("scripts.kubernetes_acceptance.__main__.selection.load")
    @patch("scripts.kubernetes_acceptance.__main__.schemas.load")
    @patch(
        "scripts.kubernetes_acceptance.__main__.control_plane_health.verify_resources",
        side_effect=ValueError("not activated"),
    )
    @patch("scripts.kubernetes_acceptance.__main__.Session")
    @patch("scripts.kubernetes_acceptance.__main__.Journal")
    @patch("scripts.kubernetes_acceptance.__main__.cluster.verify_context")
    @patch("scripts.kubernetes_acceptance.__main__.cluster.verify_local_docker")
    def test_missing_cutover_fails_before_client_or_local_session_writes(
        self, docker, context, journal, session, health, schemas, selected
    ):
        with self.assertRaises(ValueError):
            cli.execute("a" * 40, False, None)
        journal.assert_not_called()
        session.assert_not_called()

    def test_journal_is_create_only_append_only_and_failure_does_not_publish_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            name = "m04-acceptance-0123456789abcdef"
            with redirect_stdout(io.StringIO()):
                journal = Journal(root, name)
                journal.record("submission-intent", runId=RUN)
                journal.record("acceptance-incomplete")
                journal.close()
            self.assertFalse(journal.receipt.exists())
            events = [json.loads(line)["event"] for line in journal.path.read_text().splitlines()]
            self.assertEqual(events, ["submission-intent", "acceptance-incomplete"])
            with self.assertRaises(FileExistsError):
                Journal(root, name)
            journal = Journal(root, "m04-acceptance-fedcba9876543210")
            journal.finish({"status": "PASSED"})
            original = journal.receipt.read_bytes()
            saved = json.loads(original)
            self.assertEqual(
                saved["journal"]["sha256"], hashlib.sha256(journal.path.read_bytes()).hexdigest()
            )
            with self.assertRaises(ValueError):
                journal.finish({"status": "different"})
            self.assertEqual(journal.receipt.read_bytes(), original)
            journal.close()

    def test_api_free_text_is_not_copied_into_public_baseline_receipts(self):
        selected, claims, _, _ = fixture()
        record = {
            **baseline.create_request(RUN, claims["normalized-result/v1"], selected),
            "revision": 3,
            "state": "APPROVED",
            "qualification": {
                "status": "PASSED",
                "reasons": [],
                "sampleCount": 10,
                "maximumCv": 0.2,
            },
            "lifecycle": [{"actor": "private actor", "reason": "private audit text"}],
            "reason": "private audit text",
        }
        receipt = baseline.receipt(record)
        self.assertNotIn("lifecycle", receipt)
        self.assertNotIn("reason", receipt)
        self.assertNotIn("private audit text", json.dumps(receipt))
