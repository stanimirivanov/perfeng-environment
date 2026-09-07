import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import browser_diagnostics, browser_upload

RUN_ID = "perf-20260902-130000-a1b2c3d5"
TEST_ID = "search-browser"
FINGERPRINT = "f" * 64


class BrowserDiagnosticsTests(unittest.TestCase):
    def inputs(self, root: Path, mode: str, *, data_loss: bool = False):
        paths = {
            "environment": root / "browser-environment.json",
            "measurements": root / "playwright-measurements.json",
        }
        measurements = {
            "schemaVersion": 2,
            "kind": "PlaywrightMeasurements",
            "runId": RUN_ID,
            "testId": TEST_ID,
            "workload": {
                "id": "search-browser-smoke",
                "version": "1.0.0",
                "profile": "smoke",
                "sha256": "d" * 64,
            },
            "scenario": {
                "cacheProfile": "warm",
                "contextReuse": "per-run",
                "pageReuse": "per-run" if mode == "memory" else "per-iteration",
                "warmupIterations": 1,
                "measurementIterations": 3,
            },
            "diagnosticMode": mode,
            "environment": {
                "profile": {"id": "windows-mainstream", "version": "1.0.0"},
                "fingerprint": FINGERPRINT,
            },
            "measurementWindow": {
                "start": "2026-09-02T13:00:00Z",
                "end": "2026-09-02T13:01:00Z",
            },
            "runtime": {"playwrightVersion": "1.62.1"},
            "createdAt": "2026-09-02T13:01:01Z",
        }
        environment = {
            "schemaVersion": 1,
            "kind": "BrowserEnvironment",
            "runId": RUN_ID,
            "profile": measurements["environment"]["profile"],
            "fingerprint": FINGERPRINT,
        }
        paths["measurements"].write_text(json.dumps(measurements), encoding="utf-8")
        paths["environment"].write_text(json.dumps(environment), encoding="utf-8")

        if mode == "lightweight":
            paths["observations"] = root / "browser-observations.json"
            paths["observations"].write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "kind": "BrowserObservations",
                        "runId": RUN_ID,
                        "testId": TEST_ID,
                        "workload": measurements["workload"],
                        "environment": measurements["environment"],
                        "execution": {
                            "mode": "lightweight",
                            "contextReuse": "per-run",
                            "pageReuse": "per-iteration",
                            "captureIterations": [1, 2, 3],
                        },
                        "captureWindow": {
                            "start": "2026-09-02T13:00:05Z",
                            "end": "2026-09-02T13:00:55Z",
                        },
                    }
                ),
                encoding="utf-8",
            )
        elif mode in {"trace", "smoothness"}:
            paths["trace"] = root / "chrome-trace.json.gz"
            paths["trace"].write_bytes(b"trace bytes")
        elif mode == "memory":
            paths["heap-before"] = root / "before.heapsnapshot.gz"
            paths["heap-after"] = root / "after.heapsnapshot.gz"
            paths["heap-before"].write_bytes(b"before heap")
            paths["heap-after"].write_bytes(b"after heap")

        def integrity(path):
            content = path.read_bytes()
            return {
                "sha256": hashlib.sha256(content).hexdigest(),
                "sizeBytes": len(content),
            }

        runner_artifacts = {"measurements": integrity(paths["measurements"])}
        if mode == "lightweight":
            runner_artifacts["observations"] = integrity(paths["observations"])
        elif mode in {"trace", "smoothness"}:
            runner_artifacts["trace"] = {
                **integrity(paths["trace"]),
                "iteration": 2,
                "format": "chrome-trace-json-gzip",
                "mediaType": "application/gzip",
                "dataLossOccurred": data_loss,
                "startedAt": "2026-09-02T13:00:20Z",
                "finishedAt": "2026-09-02T13:00:40Z",
            }
        else:
            runner_artifacts["memory"] = {
                "captureIterations": [1, 2, 3],
                "before": {
                    **integrity(paths["heap-before"]),
                    "capturedAt": "2026-09-02T13:00:10Z",
                    "format": "chrome-heap-snapshot-json-gzip",
                    "mediaType": "application/gzip",
                    "uncompressedSizeBytes": 100,
                    "census": {"capturedAt": "2026-09-02T13:00:10Z"},
                },
                "after": {
                    **integrity(paths["heap-after"]),
                    "capturedAt": "2026-09-02T13:00:50Z",
                    "format": "chrome-heap-snapshot-json-gzip",
                    "mediaType": "application/gzip",
                    "uncompressedSizeBytes": 120,
                    "census": {"capturedAt": "2026-09-02T13:00:50Z"},
                },
            }
        runner_receipt = {
            "schema": "playwright-runner-receipt/v2",
            "runId": RUN_ID,
            "testId": TEST_ID,
            "producer": {
                "name": "playwright",
                "version": "1.62.1",
                "artifact": {
                    "kind": "source-checkout",
                    "repository": "https://github.com/stanimirivanov/perfeng-playwright",
                    "gitSha": "a" * 40,
                    "dependencyLock": {"path": "pnpm-lock.yaml", "sha256": "b" * 64},
                },
            },
            "artifacts": runner_artifacts,
        }
        ordered_inputs = [(role, path) for role, path in paths.items()]
        upload_receipt = {
            "schema": "local-browser-upload/v1",
            "runId": RUN_ID,
            "artifacts": [
                browser_upload.artifact_reference(RUN_ID, role, path)
                for role, path in ordered_inputs
            ],
        }
        return ordered_inputs, runner_receipt, upload_receipt

    def assemble(self, root: Path, mode: str, *, data_loss: bool = False):
        inputs, runner, upload = self.inputs(root, mode, data_loss=data_loss)
        return browser_diagnostics.assemble(
            inputs,
            runner,
            upload,
            "2026-09-02T13:01:02Z",
        )

    def test_assembles_lightweight_manifest_from_observed_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.assemble(Path(directory), "lightweight")

        self.assertEqual(manifest["kind"], "BrowserDiagnostics")
        self.assertEqual(manifest["execution"]["captureIterations"], [1, 2, 3])
        self.assertEqual(manifest["sources"], ["playwright", "web-performance", "windows"])
        self.assertEqual(manifest["captures"][0]["type"], "observations")
        self.assertEqual(manifest["captures"][0]["status"], "COMPLETE")
        self.assertEqual(manifest["environment"]["artifact"]["format"], "browser-environment/v1")

    def test_trace_data_loss_is_explicitly_incomplete(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.assemble(Path(directory), "trace", data_loss=True)

        capture = manifest["captures"][0]
        self.assertEqual(manifest["execution"]["captureIterations"], [2])
        self.assertEqual(capture["scope"], {"kind": "iteration", "iteration": 2})
        self.assertEqual(capture["status"], "INCOMPLETE")
        self.assertTrue(capture["dataLossOccurred"])
        self.assertTrue(capture["sensitive"])
        self.assertEqual(len(capture["reasons"]), 1)

    def test_assembles_paired_memory_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.assemble(Path(directory), "memory")

        self.assertEqual(manifest["captureWindow"]["start"], "2026-09-02T13:00:10Z")
        self.assertEqual(manifest["captureWindow"]["end"], "2026-09-02T13:00:50Z")
        self.assertEqual(
            [capture["type"] for capture in manifest["captures"]],
            ["heap-snapshot-before", "heap-snapshot-after"],
        )
        self.assertTrue(all(capture["sensitive"] for capture in manifest["captures"]))

    def test_rejects_changed_bytes_and_receipt_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, runner, upload = self.inputs(root, "smoothness")
            changed_runner = copy.deepcopy(runner)
            changed_runner["artifacts"]["trace"]["sha256"] = "0" * 64
            changed_upload = copy.deepcopy(upload)
            changed_upload["artifacts"][0]["runId"] = "perf-20260902-130000-deadbeef"

            for candidate_runner, candidate_upload, message in (
                (changed_runner, upload, "local artifact bytes"),
                (runner, changed_upload, "verified local input"),
            ):
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    browser_diagnostics.assemble(
                        inputs,
                        candidate_runner,
                        candidate_upload,
                        "2026-09-02T13:01:02Z",
                    )

    def test_rejects_invalid_capture_scope_and_duplicate_artifact_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, runner, upload = self.inputs(root, "trace")
            invalid_iteration = copy.deepcopy(runner)
            invalid_iteration["artifacts"]["trace"]["iteration"] = 4
            early_capture = copy.deepcopy(runner)
            early_capture["artifacts"]["trace"]["startedAt"] = "2026-09-02T12:59:59Z"
            duplicate_id = copy.deepcopy(upload)
            duplicate_id["artifacts"][1]["id"] = duplicate_id["artifacts"][0]["id"]

            for candidate_runner, candidate_upload, message in (
                (invalid_iteration, upload, "capture iterations"),
                (early_capture, upload, "not chronological"),
                (runner, duplicate_id, "duplicate artifact IDs"),
            ):
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    browser_diagnostics.assemble(
                        inputs,
                        candidate_runner,
                        candidate_upload,
                        "2026-09-02T13:01:02Z",
                    )

    def test_cli_writes_an_immutable_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, runner, upload = self.inputs(root, "trace")
            paths = dict(inputs)
            runner_path = root / "runner-receipt.json"
            upload_path = root / "upload-receipt.json"
            output_path = root / "browser-diagnostics.json"
            runner_path.write_text(json.dumps(runner), encoding="utf-8")
            upload_path.write_text(json.dumps(upload), encoding="utf-8")
            arguments = [
                "browser_diagnostics.py",
                "assemble",
                "--measurements",
                str(paths["measurements"]),
                "--environment",
                str(paths["environment"]),
                "--runner-receipt",
                str(runner_path),
                "--upload-receipt",
                str(upload_path),
                "--trace",
                str(paths["trace"]),
                "--output",
                str(output_path),
            ]

            with (
                patch("sys.argv", arguments),
                patch.object(browser_diagnostics, "now", return_value="2026-09-02T13:01:02Z"),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(browser_diagnostics.main(), 0)
            content = output_path.read_bytes()
            result = json.loads(output.getvalue())
            self.assertEqual(result["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(result["sizeBytes"], len(content))

            with patch("sys.argv", arguments), redirect_stdout(io.StringIO()):
                self.assertEqual(browser_diagnostics.main(), 1)
            self.assertEqual(output_path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
