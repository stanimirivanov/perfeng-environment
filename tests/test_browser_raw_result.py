import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import browser_diagnostics, browser_raw_result, browser_upload

RUN_ID = "perf-20260902-130000-a1b2c3d5"


class BrowserRawResultTests(unittest.TestCase):
    def inputs(self, root: Path, mode: str):
        measurements_path = root / "playwright-measurements.json"
        environment_path = root / "browser-environment.json"
        measurements = {
            "schemaVersion": 2,
            "kind": "PlaywrightMeasurements",
            "runId": RUN_ID,
            "testId": "search-browser",
            "workload": {
                "id": "search-browser-smoke",
                "version": "1.0.0",
                "profile": "smoke",
                "sha256": "d" * 64,
            },
            "scenario": {
                "cacheProfile": "warm",
                "contextReuse": "per-run",
                "pageReuse": "per-iteration",
                "warmupIterations": 1,
                "measurementIterations": 3,
            },
            "diagnosticMode": mode,
            "environment": {
                "profile": {"id": "windows-mainstream", "version": "1.0.0"},
                "fingerprint": "f" * 64,
            },
            "runtime": {"playwrightVersion": "1.62.1"},
            "measurementWindow": {
                "start": "2026-09-02T13:00:00Z",
                "end": "2026-09-02T13:01:00Z",
            },
            "createdAt": "2026-09-02T13:01:01Z",
        }
        environment = {
            "schemaVersion": 1,
            "kind": "BrowserEnvironment",
            "runId": RUN_ID,
            "profile": measurements["environment"]["profile"],
            "fingerprint": measurements["environment"]["fingerprint"],
        }
        measurements_path.write_text(json.dumps(measurements), encoding="utf-8")
        environment_path.write_text(json.dumps(environment), encoding="utf-8")
        paths = {"environment": environment_path, "measurements": measurements_path}
        if mode == "trace":
            trace_path = root / "chrome-trace.json.gz"
            trace_path.write_bytes(b"trace bytes")
            paths["trace"] = trace_path

        def integrity(path: Path):
            content = path.read_bytes()
            return {"sha256": hashlib.sha256(content).hexdigest(), "sizeBytes": len(content)}

        runner_artifacts = {"measurements": integrity(measurements_path)}
        if mode == "trace":
            runner_artifacts["trace"] = {
                **integrity(paths["trace"]),
                "iteration": 2,
                "format": "chrome-trace-json-gzip",
                "mediaType": "application/gzip",
                "dataLossOccurred": False,
                "startedAt": "2026-09-02T13:00:20Z",
                "finishedAt": "2026-09-02T13:00:40Z",
            }
        runner = {
            "schema": "playwright-runner-receipt/v2",
            "runId": RUN_ID,
            "testId": "search-browser",
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
        ordered = list(paths.items())
        upload = {
            "schema": "local-browser-upload/v1",
            "runId": RUN_ID,
            "artifacts": [
                browser_upload.artifact_reference(RUN_ID, role, path) for role, path in ordered
            ],
        }
        return ordered, runner, upload

    def test_assembles_baseline_from_attested_runner_and_verified_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs, runner, upload = self.inputs(Path(directory), "baseline")
            manifest = browser_raw_result.assemble(
                inputs, runner, upload, None, None, "2026-09-02T13:01:02Z"
            )

        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual(manifest["contractsVersion"], "0.12.0")
        self.assertEqual(manifest["producer"], runner["producer"])
        self.assertEqual(manifest["artifacts"], upload["artifacts"])
        self.assertEqual(
            manifest["workload"],
            {"id": "search-browser-smoke", "version": "1.0.0", "sha256": "d" * 64},
        )

    def test_assembles_diagnostic_manifest_last_over_complete_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, runner, upload = self.inputs(root, "trace")
            diagnostics = browser_diagnostics.assemble(
                inputs, runner, upload, "2026-09-02T13:01:02Z"
            )
            diagnostics_path = root / "browser-diagnostics.json"
            diagnostics_path.write_text(json.dumps(diagnostics), encoding="utf-8")
            diagnostics_upload = {
                "schema": "local-browser-upload/v1",
                "runId": RUN_ID,
                "artifacts": [
                    browser_upload.artifact_reference(RUN_ID, "diagnostics", diagnostics_path)
                ],
            }

            manifest = browser_raw_result.assemble(
                inputs,
                runner,
                upload,
                diagnostics_path,
                diagnostics_upload,
                "2026-09-02T13:01:03Z",
            )

        self.assertEqual(manifest["artifacts"][:-1], upload["artifacts"])
        self.assertEqual(manifest["artifacts"][-1], diagnostics_upload["artifacts"][0])

    def test_rejects_missing_diagnostics_and_changed_producer_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            inputs, runner, upload = self.inputs(Path(directory), "trace")
            changed = copy.deepcopy(runner)
            changed["producer"]["artifact"]["gitSha"] = "main"

            with self.assertRaisesRegex(ValueError, "require a published"):
                browser_raw_result.assemble(
                    inputs, runner, upload, None, None, "2026-09-02T13:01:03Z"
                )
            with self.assertRaisesRegex(ValueError, "provenance is invalid"):
                browser_raw_result.assemble(
                    inputs, changed, upload, None, None, "2026-09-02T13:01:03Z"
                )

    def test_cli_writes_one_immutable_raw_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs, runner, upload = self.inputs(root, "baseline")
            paths = dict(inputs)
            runner_path = root / "runner-receipt.json"
            upload_path = root / "upload-receipt.json"
            output_path = root / "raw-result.json"
            runner_path.write_text(json.dumps(runner), encoding="utf-8")
            upload_path.write_text(json.dumps(upload), encoding="utf-8")
            arguments = [
                "browser_raw_result.py",
                "assemble",
                "--measurements",
                str(paths["measurements"]),
                "--environment",
                str(paths["environment"]),
                "--runner-receipt",
                str(runner_path),
                "--upload-receipt",
                str(upload_path),
                "--output",
                str(output_path),
            ]
            with (
                patch("sys.argv", arguments),
                patch.object(browser_raw_result, "now", return_value="2026-09-02T13:01:02Z"),
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(browser_raw_result.main(), 0)
            content = output_path.read_bytes()
            result = json.loads(output.getvalue())
            self.assertEqual(result["sha256"], hashlib.sha256(content).hexdigest())
            with patch("sys.argv", arguments), redirect_stdout(io.StringIO()):
                self.assertEqual(browser_raw_result.main(), 1)
            self.assertEqual(output_path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
