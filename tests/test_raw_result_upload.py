import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import artifact_upload, raw_result_upload

RUN_ID = "perf-20260908-120000-a1b2c3d4"


class RawResultUploadTests(unittest.TestCase):
    def manifest(self, root: Path, contracts_version: str = "0.13.0") -> Path:
        path = root / "raw-result.json"
        path.write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "kind": "RawResult",
                    "contractsVersion": contracts_version,
                    "runId": RUN_ID,
                    "artifacts": [
                        {
                            "id": "11111111-1111-4111-8111-111111111111",
                            "runId": RUN_ID,
                            "kind": "raw",
                            "uri": f"s3://perfeng-artifacts/runs/{RUN_ID}/raw/kubernetes/input.json",
                            "sha256": "a" * 64,
                            "sizeBytes": 123,
                            "mediaType": "application/json",
                            "format": "kube-burner-pod-latency-json",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_accepts_reviewed_contract_versions_and_builds_discovery_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for version in ("0.12.0", "0.13.0"):
                path = self.manifest(root, version)

                run_id, resolved = raw_result_upload.manifest_input(path)
                source = raw_result_upload.source(run_id, resolved)

                self.assertEqual(run_id, RUN_ID)
                self.assertEqual(
                    source.reference["uri"],
                    f"s3://perfeng-artifacts/runs/{RUN_ID}/raw-result.json",
                )
                self.assertEqual(source.reference["format"], "raw-result/v2")

    def test_rejects_unknown_contracts_foreign_sources_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.manifest(Path(directory), "0.14.0")
            with self.assertRaisesRegex(ValueError, "supported RawResult"):
                raw_result_upload.manifest_input(path)

            manifest = json.loads(path.read_text())
            manifest["contractsVersion"] = "0.13.0"
            manifest["artifacts"][0]["runId"] = "perf-20260908-120000-deadbeef"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "run-scoped"):
                raw_result_upload.manifest_input(path)

            manifest["artifacts"][0]["runId"] = RUN_ID
            manifest["artifacts"].append(dict(manifest["artifacts"][0]))
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                raw_result_upload.manifest_input(path)

    def test_publishes_once_with_verified_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id, path = raw_result_upload.manifest_input(self.manifest(root))
            source = raw_result_upload.source(run_id, path)
            receipt_path = root / "receipts/raw-result.json"
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
                result = raw_result_upload.execute(run_id, source, receipt_path)

            self.assertEqual(json.loads(receipt_path.read_text()), result)
            self.assertEqual(result["schema"], "local-raw-result-upload/v1")
            self.assertEqual(result["artifact"], source.reference)
            scripts = [command[-1] for command, _ in commands if command[-2:-1] == ["-c"]]
            self.assertEqual(sum("--if-none-match" in script for script in scripts), 1)
            self.assertEqual(sum("sha256sum -c" in script for script in scripts), 1)
            with self.assertRaises(FileExistsError):
                raw_result_upload.execute(run_id, source, receipt_path)


if __name__ == "__main__":
    unittest.main()
