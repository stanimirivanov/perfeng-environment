"""Owner policy confirmation at the two-policy command boundary."""

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import checkout_operator, checkout_policies, control_plane_release

UID = "257a288d-9e0f-4956-a0e3-9c4104de6476"


class CheckoutPoliciesTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.evidence = self.root / "private"
        self.evidence.mkdir(mode=0o700)
        lock = self.root / "deployments/local-control-plane.json"
        lock.parent.mkdir()
        lock.write_bytes(control_plane_release.DEFAULT_LOCK.read_bytes())
        self.selection = SimpleNamespace(release=control_plane_release.load_lock(lock))
        selection = patch.object(checkout_operator, "select", return_value=self.selection)
        selection.start()
        self.addCleanup(selection.stop)
        self.args = [
            "--lock-sha256",
            hashlib.sha256(lock.read_bytes()).hexdigest(),
            "--cluster-uid",
            UID,
            "--evidence-dir",
            str(self.evidence),
        ]
        self.sources = {}
        entries = []
        for test_id in ["checkout-api", "pod-startup-k8s"]:
            path = f"policies/{test_id}.json"
            self.sources[path] = json.dumps(
                {
                    "apiVersion": "performance.perfeng.io/v1",
                    "kind": "PerformancePolicy",
                    "metadata": {"name": test_id, "version": "1.0.0"},
                    "spec": {"mode": "observe"},
                }
            ).encode()
            entry = {
                "testId": test_id,
                "profile": "smoke",
                "contractsVersion": "1.0",
                "policy": path,
            }
            if test_id == "pod-startup-k8s":
                entry["benchmark"] = {"clusterUid": UID}
            entries.append(entry)
        self.sources["registry.json"] = json.dumps(
            {"schemaVersion": 1, "entries": entries}
        ).encode()
        read = patch.object(
            control_plane_release,
            "revision_reader",
            return_value=lambda path: self.sources[
                path.removeprefix("bundles/local-kind-integration/1.0.1/")
            ],
        )
        read.start()
        self.addCleanup(read.stop)

    def command(self, *extra):
        with redirect_stdout(io.StringIO()) as output:
            status = checkout_policies.main([*self.args, *extra], root=self.root)
        return status, output.getvalue()

    def test_offline_preview_identifies_both_uris_and_separate_files(self):
        status, output = self.command()
        self.assertEqual(status, 0, output)
        for test_id in ["checkout-api", "pod-startup-k8s"]:
            self.assertIn(f"policies/{test_id}/1.0.0/performance-policy.json", output)
            self.assertIn(f"{test_id}.published.json", output)
            self.assertIn(f"{test_id}.receipt.json", output)
        self.assertEqual(list(self.evidence.iterdir()), [])

    def test_refuses_policy_evidence_in_a_git_checkout(self):
        (self.root / ".git").mkdir()
        (self.root / ".git/HEAD").write_text("ref: refs/heads/main\n")
        status, message = self.command()
        self.assertEqual(status, 1)
        self.assertIn("outside Git", message)

    def owner_delivery(self, test_id):
        path = self.evidence / f"{test_id}.published.json"
        path.write_bytes(self.sources[f"policies/{test_id}.json"])
        path.chmod(0o600)
        return path

    def test_each_owner_delivery_must_match_its_own_policy_before_completion(self):
        self.owner_delivery("checkout-api")
        wrong = self.owner_delivery("pod-startup-k8s")
        wrong.write_bytes(self.sources["policies/checkout-api.json"])
        with patch.object(checkout_operator, "verify_substrate", return_value=self.selection):
            status, output = self.command("--execute")
        self.assertEqual(status, 1, output)
        checkout_receipt = self.evidence / "checkout-api.receipt.json"
        self.assertEqual(json.loads(checkout_receipt.read_bytes())["policy"]["id"], "checkout-api")
        self.assertFalse((self.evidence / "pod-startup-k8s.receipt.json").exists())

    def test_only_explicit_missing_key_selects_conditional_publication(self):
        self.owner_delivery("checkout-api")
        receipt = self.evidence / "pod-startup-k8s.receipt.json"
        with (
            patch.object(checkout_operator, "verify_substrate", return_value=self.selection),
            patch("scripts.policy_publish.execute") as publish,
        ):
            status, output = self.command("--execute", "--publish-missing", "pod-startup-k8s")
        self.assertEqual(status, 0, output)
        source, path, root = publish.call_args.args
        self.assertEqual((source.policy_id, path, root), ("pod-startup-k8s", receipt, self.root))
        self.assertIn("Both selected policies are confirmed", output)

    def test_bad_cluster_identity_stops_before_confirming_either_policy(self):
        self.owner_delivery("checkout-api")
        with (
            patch.object(checkout_operator, "verify_substrate", side_effect=ValueError("UID")),
            patch("scripts.policy_publish.execute") as publish,
        ):
            status, output = self.command("--execute", "--publish-missing", "pod-startup-k8s")
        self.assertEqual(status, 1, output)
        self.assertFalse(list(self.evidence.glob("*.receipt.json")))
        publish.assert_not_called()

    def test_both_existing_policies_confirm_and_preserve_receipts_on_repeat(self):
        for test_id in ["checkout-api", "pod-startup-k8s"]:
            self.owner_delivery(test_id)
        with patch.object(checkout_operator, "verify_substrate", return_value=self.selection):
            status, output = self.command("--execute")
            self.assertEqual(status, 0, output)
            policy = self.evidence / "checkout-api.receipt.json"
            prior = policy.read_bytes()
            self.assertEqual(json.loads(prior)["policy"]["id"], "checkout-api")
            benchmark = self.evidence / "pod-startup-k8s.receipt.json"
            self.assertEqual(json.loads(benchmark.read_bytes())["policy"]["id"], "pod-startup-k8s")
            self.assertEqual(self.command("--execute")[0], 1)
            self.assertEqual(prior, policy.read_bytes())
