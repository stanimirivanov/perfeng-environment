import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import artifact_upload, browser_upload

RUN_ID = "perf-20260902-130000-a1b2c3d5"


class BrowserUploadTests(unittest.TestCase):
    def write_inputs(self, root: Path, mode: str = "baseline"):
        measurements = root / "measurements.json"
        environment = root / "environment.json"
        measurements.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "kind": "PlaywrightMeasurements",
                    "runId": RUN_ID,
                    "diagnosticMode": mode,
                }
            ),
            encoding="utf-8",
        )
        environment.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "BrowserEnvironment",
                    "runId": RUN_ID,
                }
            ),
            encoding="utf-8",
        )
        return measurements, environment

    def test_validates_mode_specific_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            measurements, environment = self.write_inputs(root, "memory")
            before = root / "before.gz"
            after = root / "after.gz"
            before.write_bytes(b"before")
            after.write_bytes(b"after")

            run_id, inputs = browser_upload.browser_inputs(
                measurements,
                environment,
                heap_before_path=before,
                heap_after_path=after,
            )

            self.assertEqual(run_id, RUN_ID)
            self.assertEqual(
                [role for role, _ in inputs],
                ["environment", "measurements", "heap-before", "heap-after"],
            )

    def test_rejects_mismatched_diagnostic_files_and_run_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            measurements, environment = self.write_inputs(root, "trace")
            trace = root / "trace.gz"
            trace.write_bytes(b"trace")
            with self.assertRaisesRegex(ValueError, "diagnostic mode"):
                browser_upload.browser_inputs(measurements, environment)

            value = json.loads(environment.read_text())
            value["runId"] = "perf-20260902-130000-deadbeef"
            environment.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "measurement run"):
                browser_upload.browser_inputs(measurements, environment, trace_path=trace)

    def test_reference_uses_exact_bytes_and_stable_object_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "measurements.json"
            path.write_bytes(b"exact bytes\n")

            reference = browser_upload.artifact_reference(RUN_ID, "measurements", path)

            self.assertEqual(reference["runId"], RUN_ID)
            self.assertEqual(reference["sha256"], hashlib.sha256(b"exact bytes\n").hexdigest())
            self.assertEqual(reference["sizeBytes"], 12)
            self.assertEqual(
                reference["uri"],
                f"s3://perfeng-artifacts/runs/{RUN_ID}/raw/browser/playwright-measurements.json",
            )
            self.assertEqual(reference["format"], "playwright-measurements/v2")

            with self.assertRaisesRegex(ValueError, "unsafe or outside"):
                artifact_upload.artifact_reference(
                    RUN_ID,
                    path,
                    f"s3://perfeng-artifacts/outside/{RUN_ID}/measurements.json",
                    "application/json",
                    "playwright-measurements/v2",
                )

    def test_accepts_diagnostic_manifest_with_run_scoped_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "browser-diagnostics.json"
            evidence = {"runId": RUN_ID, "kind": "raw"}
            path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "BrowserDiagnostics",
                        "runId": RUN_ID,
                        "environment": {"artifact": evidence},
                        "captures": [{"artifact": evidence}],
                    }
                ),
                encoding="utf-8",
            )

            run_id, inputs = browser_upload.diagnostic_input(path)
            reference = browser_upload.artifact_reference(run_id, *inputs[0])

            self.assertEqual(run_id, RUN_ID)
            self.assertEqual(inputs, [("diagnostics", path.resolve())])
            self.assertEqual(
                reference["uri"],
                f"s3://perfeng-artifacts/runs/{RUN_ID}/raw/browser/browser-diagnostics.json",
            )
            self.assertEqual(reference["format"], "browser-diagnostics/v1")

    def test_raw_result_uses_the_control_plane_discovery_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw-result.json"
            path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 2,
                        "kind": "RawResult",
                        "contractsVersion": "0.12.0",
                        "runId": RUN_ID,
                        "artifacts": [{"runId": RUN_ID, "kind": "raw"}],
                    }
                ),
                encoding="utf-8",
            )

            run_id, inputs = browser_upload.raw_result_input(path)
            reference = browser_upload.artifact_reference(run_id, *inputs[0])

            self.assertEqual(
                reference["uri"], f"s3://perfeng-artifacts/runs/{RUN_ID}/raw-result.json"
            )
            self.assertEqual(reference["format"], "raw-result/v2")

    def test_rejects_diagnostic_manifest_with_foreign_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "browser-diagnostics.json"
            path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "BrowserDiagnostics",
                        "runId": RUN_ID,
                        "environment": {
                            "artifact": {
                                "runId": "perf-20260902-130000-deadbeef",
                                "kind": "raw",
                            }
                        },
                        "captures": [{"artifact": {"runId": RUN_ID, "kind": "raw"}}],
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not belong"):
                browser_upload.diagnostic_input(path)

    def test_upload_pod_uses_only_restricted_credentials(self):
        pod = browser_upload.upload_pod("browser-upload-preview", browser_upload.cluster.ROOT)
        container = pod["spec"]["containers"][0]
        references = [
            entry["valueFrom"]["secretKeyRef"]["name"]
            for entry in container["env"]
            if "valueFrom" in entry
        ]

        self.assertEqual(references, ["perfeng-s3-uploader-auth", "perfeng-s3-uploader-auth"])
        self.assertFalse(pod["spec"]["automountServiceAccountToken"])
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertEqual(container["securityContext"]["capabilities"], {"drop": ["ALL"]})

    def test_upload_script_persists_and_verifies_reference_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw-result.json"
            path.write_bytes(b"exact raw result bytes\n")
            reference = browser_upload.artifact_reference(RUN_ID, "raw-result", path)

            script = browser_upload.upload_script(reference)

            self.assertIn('--content-type "application/json"', script)
            self.assertIn(
                '--metadata "artifact-id='
                f'{reference["id"]},sha256={reference["sha256"]},format=raw-result/v2"',
                script,
            )
            self.assertIn("--query ContentLength", script)
            self.assertIn("--query ContentType", script)
            self.assertIn("--query 'Metadata.\"artifact-id\"'", script)
            self.assertIn("--query Metadata.sha256", script)
            self.assertIn("--query Metadata.format", script)
            self.assertEqual(script.count("head-object"), 5)

    def test_execute_uploads_conditionally_verifies_and_writes_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            measurements, environment = self.write_inputs(root)
            run_id, inputs = browser_upload.browser_inputs(measurements, environment)
            receipt = root / "result/upload.json"
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
                result = browser_upload.execute(run_id, inputs, receipt)

            self.assertEqual(json.loads(receipt.read_text()), result)
            scripts = [command[-1] for command, _ in commands if command[-2:-1] == ["-c"]]
            self.assertEqual(sum("--if-none-match" in script for script in scripts), 2)
            self.assertEqual(sum("sha256sum -c" in script for script in scripts), 2)
            self.assertEqual(sum("--content-type" in script for script in scripts), 2)
            self.assertEqual(sum("--metadata" in script for script in scripts), 2)
            self.assertEqual(sum(script.count("head-object") for script in scripts), 10)
            self.assertTrue(
                any(
                    any(
                        pair == ("delete", "pod")
                        for pair in zip(command, command[1:], strict=False)
                    )
                    for command, _ in commands
                )
            )
            with self.assertRaises(FileExistsError):
                browser_upload.execute(run_id, inputs, receipt)

    def test_failed_upload_removes_receipt_reservation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            measurements, environment = self.write_inputs(root)
            run_id, inputs = browser_upload.browser_inputs(measurements, environment)
            receipt = root / "upload.json"

            with (
                patch.object(
                    artifact_upload.cluster,
                    "verify_local_docker",
                    side_effect=ValueError("offline"),
                ),
                self.assertRaisesRegex(ValueError, "offline"),
            ):
                browser_upload.execute(run_id, inputs, receipt)

            self.assertFalse(receipt.exists())


if __name__ == "__main__":
    unittest.main()
