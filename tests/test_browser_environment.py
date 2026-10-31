import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import browser_environment, browser_host


class BrowserEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = browser_host.load_profile()
        cls.snapshot = json.loads(
            (browser_host.ROOT / "tests/fixtures/linux-browser-host.json").read_text()
        )

    def qualification(self):
        value = browser_host.evaluate(self.profile, copy.deepcopy(self.snapshot))
        value["capturedAt"] = "2026-09-02T12:59:58Z"
        return value

    def measurements(self):
        return {
            "schemaVersion": 2,
            "kind": "PlaywrightMeasurements",
            "runId": "perf-20260902-130000-a1b2c3d5",
            "environment": {
                "profile": dict(self.profile["metadata"]),
                "fingerprint": browser_host.fingerprint(self.profile),
            },
            "runtime": {
                "playwrightVersion": "1.62.1",
                "nodeVersion": "24.18.0",
                "platform": "linux",
                "architecture": "x64",
            },
            "browser": {
                "name": "chromium",
                "version": "140.0.7339.16",
                "headless": False,
                "viewport": {"width": 1280, "height": 720},
                "deviceScaleFactor": 1.25,
            },
            "measurementWindow": {
                "start": "2026-09-02T13:00:00Z",
                "end": "2026-09-02T13:01:00Z",
            },
        }

    def test_assembles_contract_shaped_environment_from_observed_values(self):
        artifact = browser_environment.assemble(
            self.profile,
            self.qualification(),
            self.measurements(),
        )

        self.assertEqual(artifact["schemaVersion"], 1)
        self.assertEqual(artifact["kind"], "BrowserEnvironment")
        self.assertEqual(artifact["runId"], "perf-20260902-130000-a1b2c3d5")
        self.assertEqual(artifact["profile"], self.profile["metadata"])
        self.assertEqual(artifact["host"]["os"]["build"], "6")
        self.assertEqual(artifact["host"]["cpu"]["availableLogicalCores"], 12)
        self.assertEqual(artifact["host"]["cpu"]["physicalCores"], 6)
        self.assertNotIn("idleCpuPercent", artifact["host"]["cpu"])
        self.assertNotIn("softwareRendering", artifact["host"]["gpu"])
        self.assertEqual(
            artifact["browser"],
            {
                "name": "chromium",
                "channel": "chrome-for-testing",
                "version": "140.0.7339.16",
                "headless": False,
            },
        )
        self.assertEqual(artifact["calibration"]["status"], "ACCEPTED")
        self.assertEqual(artifact["calibration"]["idleCpuPercent"], 3.2)
        self.assertEqual(artifact["capturedAt"], "2026-09-02T12:59:58Z")

    def test_rejects_unqualified_mismatched_and_non_linux_runs(self):
        cases = []
        rejected = self.qualification()
        rejected["status"] = "REJECTED"
        rejected["reasons"] = ["background CPU is too high"]
        cases.append((rejected, self.measurements(), "not accepted"))

        wrong_fingerprint = self.measurements()
        wrong_fingerprint["environment"]["fingerprint"] = "a" * 64
        cases.append((self.qualification(), wrong_fingerprint, "fingerprint"))

        wrong_browser = self.measurements()
        wrong_browser["browser"]["headless"] = True
        cases.append((self.qualification(), wrong_browser, "browser profile"))

        wrong_platform = self.measurements()
        wrong_platform["runtime"]["platform"] = "darwin"
        cases.append((self.qualification(), wrong_platform, "native Linux"))

        late_qualification = self.qualification()
        late_qualification["capturedAt"] = "2026-09-02T13:00:01Z"
        cases.append((late_qualification, self.measurements(), "must precede"))

        for qualification, measurements, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(ValueError, message):
                    browser_environment.assemble(
                        self.profile,
                        qualification,
                        measurements,
                    )

    def test_cli_writes_once_and_reports_exact_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            qualification_path = root / "qualification.json"
            measurements_path = root / "measurements.json"
            output_path = root / "artifacts/browser-environment.json"
            qualification_path.write_text(json.dumps(self.qualification()), encoding="utf-8")
            measurements_path.write_text(json.dumps(self.measurements()), encoding="utf-8")
            arguments = [
                "browser_environment.py",
                "assemble",
                "--qualification",
                str(qualification_path),
                "--measurements",
                str(measurements_path),
                "--output",
                str(output_path),
            ]

            with patch("sys.argv", arguments), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(browser_environment.main(), 0)
            content = output_path.read_bytes()
            receipt = json.loads(output.getvalue())
            self.assertEqual(receipt["sha256"], hashlib.sha256(content).hexdigest())
            self.assertEqual(receipt["sizeBytes"], len(content))
            self.assertEqual(json.loads(content)["kind"], "BrowserEnvironment")

            with patch("sys.argv", arguments), redirect_stdout(io.StringIO()):
                self.assertEqual(browser_environment.main(), 1)
            self.assertEqual(output_path.read_bytes(), content)

    def test_laptop_evidence_is_bound_to_its_selected_cohort(self):
        profile = browser_host.load_profile(
            browser_host.ROOT / "profiles/browser/linux-four-core-4k.yaml"
        )
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["cpu"].update(logicalCores=4, physicalCores=4, availableLogicalCores=4)
        snapshot["display"] = dict(profile["spec"]["display"])
        qualification = browser_host.evaluate(profile, snapshot)
        qualification["capturedAt"] = "2026-09-02T12:59:58Z"
        measurements = self.measurements()
        measurements["environment"] = {
            "profile": dict(profile["metadata"]),
            "fingerprint": browser_host.fingerprint(profile),
        }
        measurements["browser"]["deviceScaleFactor"] = 1
        artifact = browser_environment.assemble(profile, qualification, measurements)
        self.assertEqual(artifact["profile"], profile["metadata"])
        self.assertEqual(artifact["host"]["display"], snapshot["display"])
        self.assertEqual(artifact["fingerprint"], browser_host.fingerprint(profile))
        with self.assertRaisesRegex(ValueError, "profile identity"):
            browser_environment.assemble(self.profile, qualification, measurements)
        with self.assertRaisesRegex(ValueError, "profile identity"):
            browser_environment.assemble(profile, qualification, self.measurements())
        measurements["environment"]["fingerprint"] = browser_host.fingerprint(self.profile)
        with self.assertRaisesRegex(ValueError, "profile fingerprint"):
            browser_environment.assemble(profile, qualification, measurements)


if __name__ == "__main__":
    unittest.main()
