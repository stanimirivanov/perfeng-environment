import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import artifact_upload, kube_burner_upload

RUN_ID = "perf-20260908-120000-a1b2c3d4"


class KubeBurnerUploadTests(unittest.TestCase):
    def capture(self, root: Path) -> Path:
        measurements = []
        for name in kube_burner_upload.ARTIFACT_TYPES:
            path = root / name
            path.write_text(json.dumps({"file": name}), encoding="utf-8")
            content = path.read_bytes()
            measurements.append(
                {
                    "path": name,
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "sizeBytes": len(content),
                }
            )
        execution = {
            "schemaVersion": 1,
            "kind": "KubernetesBenchmarkExecution",
            "contractsVersion": "0.13.0",
            "runId": RUN_ID,
            "benchmark": "pod-startup",
            "exitCode": 0,
            "namespaceRemoved": True,
            "tool": {"name": "kube-burner", "artifact": {"kind": "release-archive"}},
            "workload": {"id": "pod-startup-smoke"},
            "measurements": measurements,
        }
        (root / "execution.json").write_text(json.dumps(execution), encoding="utf-8")
        return root

    def test_builds_assembler_ready_receipts_from_exact_capture_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            run_id, sources = kube_burner_upload.capture_sources(self.capture(Path(directory)))

            request = kube_burner_upload.manifest_request(sources)

            self.assertEqual(run_id, RUN_ID)
            self.assertEqual(request["testId"], "pod-startup-k8s")
            self.assertEqual(
                [receipt["file"] for receipt in request["receipts"]],
                list(kube_burner_upload.ARTIFACT_TYPES),
            )
            for receipt in request["receipts"]:
                artifact = receipt["artifact"]
                self.assertEqual(artifact["runId"], RUN_ID)
                self.assertEqual(artifact["kind"], "raw")
                self.assertEqual(artifact["mediaType"], "application/json")
                self.assertEqual(
                    artifact["format"], kube_burner_upload.ARTIFACT_TYPES[receipt["file"]]
                )
                self.assertEqual(
                    artifact["uri"],
                    f"s3://perfeng-artifacts/runs/{RUN_ID}/raw/kubernetes/{receipt['file']}",
                )

    def test_rejects_failed_or_changed_captures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.capture(Path(directory))
            execution_path = root / "execution.json"
            execution = json.loads(execution_path.read_text())
            execution["exitCode"] = 1
            execution_path.write_text(json.dumps(execution), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "successful supported"):
                kube_burner_upload.capture_sources(root)

            execution["exitCode"] = 0
            execution_path.write_text(json.dumps(execution), encoding="utf-8")
            (root / "jobSummary.json").write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "does not match"):
                kube_burner_upload.capture_sources(root)

    def test_rejects_unclaimed_measurement_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = self.capture(Path(directory))
            (root / "unexpected.json").write_text("{}", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "exactly the required"):
                kube_burner_upload.capture_sources(root)

    def test_execute_conditionally_publishes_and_writes_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id, sources = kube_burner_upload.capture_sources(self.capture(root))
            receipt = root / "receipts/manifest-request.json"
            commands = []

            def run(command, *, input_data=None):
                commands.append((command, input_data))
                return ""

            with (
                patch.object(artifact_upload.cluster, "verify_local_docker"),
                patch.object(artifact_upload.cluster, "verify_context"),
                patch.object(artifact_upload.storage_access, "ensure_credentials"),
                patch.object(artifact_upload.cluster, "run", side_effect=run),
            ):
                result = kube_burner_upload.execute(run_id, sources, receipt)

            self.assertEqual(json.loads(receipt.read_text()), result)
            self.assertEqual(len(result["receipts"]), 3)
            scripts = [command[-1] for command, _ in commands if command[-2:-1] == ["-c"]]
            self.assertEqual(sum("--if-none-match" in script for script in scripts), 3)
            self.assertEqual(sum("sha256sum -c" in script for script in scripts), 3)
            with self.assertRaises(FileExistsError):
                kube_burner_upload.execute(run_id, sources, receipt)

    def test_changed_source_fails_before_cluster_access(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id, sources = kube_burner_upload.capture_sources(self.capture(root))
            sources[0].path.write_bytes(b"changed after validation")
            receipt = root / "manifest-request.json"

            with (
                patch.object(artifact_upload.cluster, "verify_local_docker") as verify,
                self.assertRaisesRegex(ValueError, "changed after reference"),
            ):
                kube_burner_upload.execute(run_id, sources, receipt)

            verify.assert_not_called()
            self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
