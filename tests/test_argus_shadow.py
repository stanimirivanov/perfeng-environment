"""Shadow CI reports only verified, explicitly public receipt fields."""

import importlib
import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ArgusShadowTests(unittest.TestCase):
    def test_workflow_is_independent_and_main_only(self):
        source = (ROOT / ".github/workflows/argus-shadow.yml").read_text()
        workflow = yaml.safe_load(source)
        self.assertNotIn("secrets.", source)
        triggers = workflow[True]  # PyYAML interprets the YAML 1.1 key "on" as a boolean.
        self.assertEqual(set(triggers), {"push", "workflow_dispatch"})
        self.assertEqual(triggers["push"]["branches"], ["main"])
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        [job] = workflow["jobs"].values()
        self.assertTrue(job["continue-on-error"])
        self.assertEqual(job["if"], "github.ref == 'refs/heads/main'")
        self.assertNotIn("needs", job)
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        deployment = json.loads((ROOT / "deployments/local-control-plane.json").read_text())
        [checkout] = [
            step["with"]
            for step in job["steps"]
            if step.get("with", {}).get("repository") == "stanimirivanov/perfeng-k8s"
        ]
        self.assertEqual(checkout["ref"], deployment["deployment"]["revision"])
        [upload] = [
            step for step in job["steps"] if "actions/upload-artifact@" in step.get("uses", "")
        ]
        self.assertEqual(upload["if"], "${{ !cancelled() }}")
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/argus-shadow-receipt.json")

    def test_success_requires_recovery_cleanup_and_only_projects_allowlisted_values(self):
        shadow = importlib.import_module("tests.integration.argus-perfeng.shadow")
        evidence = {
            "productAcceptance": "passed",
            "cleanup": "passed",
            "servingCleanup": "passed",
            "checkoutSmoke": {"runId": "perf-20261104-182431-8ad05269", "k6ExitCode": 0},
            "jointAcceptance": {
                "runId": "perf-20261104-182542-e2499765",
                "reportSha256": "a" * 64,
                "projectionSha256": "b" * 64,
            },
            "recovery": {"retainedAnalysisEvents": 1, "publicationLate": False},
            "credentials": {"token": "private"},
        }
        receipt = shadow.receipt("success", evidence)
        self.assertEqual(receipt["status"], "passed")
        self.assertNotIn("private", json.dumps(receipt))
        self.assertEqual(receipt["referenceRunId"], evidence["checkoutSmoke"]["runId"])
        evidence["cleanup"] = "unresolved"
        self.assertEqual(shadow.receipt("success", evidence)["status"], "invalid-evidence")
        self.assertEqual(
            shadow.receipt("failure", evidence), {"schemaVersion": 1, "status": "failed"}
        )

    def test_evidence_reader_refuses_symlinks_oversized_files_and_multiple_runs(self):
        import tempfile

        shadow = importlib.import_module("tests.integration.argus-perfeng.shadow")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            actual = root / "argus-perfeng-serving-one"
            actual.mkdir()
            evidence = actual / "evidence.json"
            evidence.write_text("{}")
            self.assertEqual(shadow.read_evidence(root), {})
            evidence.write_bytes(b" " * (shadow.LIMIT + 1))
            self.assertIsNone(shadow.read_evidence(root))
            evidence.unlink()
            private = root / "private.json"
            private.write_text("{}")
            evidence.symlink_to(private)
            self.assertIsNone(shadow.read_evidence(root))
            (root / "argus-perfeng-serving-two").mkdir()
            self.assertIsNone(shadow.read_evidence(root))
