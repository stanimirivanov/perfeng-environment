import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CheckoutOperatorTests(unittest.TestCase):
    def test_owner_lock_mismatch_stops_before_live_access(self):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/checkout_operator.py"),
                "check",
                "--execute",
                "--lock-sha256",
                "0" * 64,
                "--cluster-uid",
                "foreign",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Deployment Lock digest differs", result.stderr)

    def test_live_selection_refuses_replaced_or_unready_cluster_before_application_reads(self):
        from unittest.mock import patch

        from scripts import checkout_operator as operator

        for reads, reason in ((["replacement"], "UID"), (["retained", "unready"], "readiness")):
            with (
                self.subTest(reads=reads),
                patch.object(operator, "select"),
                patch.object(operator.cluster, "verify_local_docker"),
                patch.object(operator.cluster, "verify_context"),
                patch.object(operator.cluster, "run", side_effect=reads),
                patch.object(operator.control_plane_maintenance, "read") as read_application,
            ):
                with self.assertRaisesRegex(ValueError, reason):
                    operator.verify_selection(ROOT, "0" * 64, "retained")
                read_application.assert_not_called()

    def test_checkpoint_is_private_and_excludes_another_writer(self):
        import tempfile

        from scripts.checkout_checkpoint import Checkpoint

        with tempfile.TemporaryDirectory() as home:
            directory = Path(home) / "retained"
            with Checkpoint(directory, create=True) as record:
                record.save({"schemaVersion": 1, "requestKey": "original"})
                self.assertEqual((directory / "checkpoint.json").stat().st_mode & 0o777, 0o600)
                with self.assertRaisesRegex(ValueError, "another writer"):
                    with Checkpoint(directory, create=False):
                        pass
            with self.assertRaisesRegex(ValueError, "existing"):
                with Checkpoint(directory, create=True):
                    pass
            with Checkpoint(directory, create=False) as resumed:
                self.assertEqual(resumed.load()["requestKey"], "original")

    def test_lost_post_response_stays_uncertain_and_resume_never_posts(self):
        import json
        import tempfile
        from unittest.mock import Mock

        from scripts import checkout_operator as operator
        from scripts.checkout_checkpoint import Checkpoint

        selection = Mock(
            identity={"principal": "local-operator"}, request={"testSuite": "checkout-api"}
        )
        with tempfile.TemporaryDirectory() as home:
            directory = Path(home) / "retained"

            def lost(path, *, request=None, key=None):
                state = json.loads((directory / "checkpoint.json").read_bytes())
                self.assertEqual(state["requestKey"], key)
                self.assertEqual(state["request"], request)
                self.assertEqual(path, "/v1/runs")
                raise ValueError("Connection lost")

            with Checkpoint(directory, create=True) as record:
                with self.assertRaisesRegex(ValueError, "Connection lost"):
                    operator.submit(record, selection, lost)
            with Checkpoint(directory, create=False) as record:
                with self.assertRaisesRegex(ValueError, "independent"):
                    operator.observe(
                        record, selection, Mock(side_effect=AssertionError), None, None
                    )
                self.assertIsNone(record.load()["runId"])

    def test_acknowledgement_survives_denied_authenticated_run_read(self):
        import tempfile
        from unittest.mock import Mock

        from scripts import checkout_operator as operator
        from scripts.checkout_checkpoint import Checkpoint

        selection = Mock(identity={"principal": "local-operator"}, request={"profile": "smoke"})
        run_id = "perf-20261125-120000-abcdef12"
        accepted = {"id": run_id, "request": selection.request, "state": "CREATED"}
        with tempfile.TemporaryDirectory() as home:
            with Checkpoint(Path(home) / "retained", create=True) as record:
                operator.submit(record, selection, Mock(return_value=accepted))
                denied = Mock(side_effect=ValueError("HTTP 403"))
                with self.assertRaisesRegex(ValueError, "403"):
                    operator.observe(record, selection, denied, None, None)
                self.assertEqual(record.load()["runId"], run_id)
                denied.assert_called_once_with("/v1/runs/" + run_id)
                different = Mock(identity={"principal": "other"}, request=selection.request)
                with self.assertRaisesRegex(ValueError, "selection"):
                    operator.observe(record, different, denied, None, None)

    def test_later_terminal_revision_cannot_rewrite_verified_checkpoint(self):
        import tempfile
        from unittest.mock import Mock

        from scripts import checkout_operator as operator
        from scripts.checkout_checkpoint import Checkpoint

        selection = Mock(identity={"principal": "local-operator"}, request={"profile": "smoke"})
        run_id = "perf-20261125-120000-abcdef12"
        with tempfile.TemporaryDirectory() as home:
            with Checkpoint(Path(home) / "retained", create=True) as record:
                original = {
                    "schemaVersion": 1,
                    "selection": selection.identity,
                    "request": selection.request,
                    "runId": run_id,
                    "revision": 8,
                    "terminalState": "COMPLETED",
                    "phase": "verified",
                    "observedAt": "2026-11-25T12:03:00Z",
                    "decision": {"verdict": "MET"},
                }
                record.save(original)
                api = Mock(
                    return_value={
                        "id": run_id,
                        "revision": 9,
                        "state": "COMPLETED",
                        "request": selection.request,
                    }
                )
                with self.assertRaisesRegex(ValueError, "terminal"):
                    operator.observe(record, selection, api, None, None)
                self.assertEqual(record.load(), original)

    def test_uncertain_attachment_requires_key_principal_and_authenticated_original(self):
        import json
        import tempfile
        from unittest.mock import Mock

        from scripts import checkout_operator as operator
        from scripts.checkout_checkpoint import Checkpoint

        selection = Mock(identity={"principal": "local-operator"}, request={"profile": "smoke"})
        run_id = "perf-20261125-120000-abcdef12"
        source = {
            "requestKey": "original",
            "principal": "local-operator",
            "runId": run_id,
            "source": "owner audit entry 123",
        }
        with tempfile.TemporaryDirectory() as home:
            path = Path(home) / "correlation.json"
            path.touch(mode=0o600)
            with Checkpoint(Path(home) / "retained", create=True) as record:
                record.save(
                    {
                        "schemaVersion": 1,
                        "selection": selection.identity,
                        "request": selection.request,
                        "requestKey": "original",
                        "startedAt": "2026-11-25T12:00:00Z",
                        "runId": None,
                    }
                )
                current = {
                    "id": run_id,
                    "state": "COMPLETED",
                    "createdAt": "2026-11-25T12:00:01Z",
                    "revision": 6,
                    "request": selection.request,
                }
                for change in ({"requestKey": "similar"}, {"principal": "someone-else"}):
                    path.write_text(json.dumps({**source, **change}))
                    with self.assertRaisesRegex(ValueError, "correlation"):
                        operator.observe(
                            record, selection, Mock(side_effect=AssertionError), run_id, path
                        )
                    self.assertIsNone(record.load()["runId"])
                path.write_text(json.dumps(source))
                for change in (
                    {"request": {"profile": "another"}},
                    {"createdAt": "2026-11-24T12:00:00Z"},
                ):
                    with self.assertRaises(ValueError):
                        operator.observe(
                            record,
                            selection,
                            Mock(return_value={**current, **change}),
                            run_id,
                            path,
                        )
                    self.assertIsNone(record.load()["runId"])

                def get(path):
                    if path == "/v1/runs/" + run_id:
                        return current
                    self.assertEqual(path, "/v1/runs/" + run_id + "/artifacts")
                    raise ValueError("HTTP 403")

                with self.assertRaisesRegex(ValueError, "403"):
                    operator.observe(record, selection, get, run_id, path)
                self.assertEqual(record.load()["runId"], run_id)
                self.assertEqual(record.load()["originalRunSource"], source["source"])
                self.assertNotIn("decision", record.load())
