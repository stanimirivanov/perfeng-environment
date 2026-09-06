import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import browser_host


class BrowserHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.profile = browser_host.load_profile()
        cls.snapshot = json.loads(
            (browser_host.ROOT / "tests/fixtures/windows-browser-host.json").read_text()
        )

    def test_profile_has_stable_identity_and_accepts_reference_host(self):
        result = browser_host.evaluate(self.profile, copy.deepcopy(self.snapshot))
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["profile"], {"id": "windows-mainstream", "version": "1.0.0"})
        self.assertRegex(result["fingerprint"], r"^[0-9a-f]{64}$")

    def test_environment_and_calibration_drift_are_all_reported(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["virtualization"] = "virtual"
        snapshot["memory"]["availableBytes"] = 1
        snapshot["cpu"]["availableLogicalCores"] = 4
        snapshot["cpu"]["idleCpuPercent"] = 20
        snapshot["power"]["source"] = "battery"
        snapshot["clockSynchronized"] = False
        result = browser_host.evaluate(self.profile, snapshot)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(
            result["reasons"],
            [
                "virtualization differs",
                "available CPU capacity is too low",
                "available memory is too low",
                "power source differs",
                "background CPU is too high",
                "clock synchronization differs",
            ],
        )

    def test_profile_rejects_unknown_fields_and_invalid_thresholds(self):
        profile = copy.deepcopy(self.profile)
        profile["spec"]["extra"] = True
        with patch.object(browser_host.yaml, "safe_load", return_value=profile):
            with self.assertRaisesRegex(ValueError, "missing or unknown"):
                browser_host.load_profile()

    def test_snapshot_rejects_unknown_fields_and_ambiguous_types(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["extra"] = True
        with self.assertRaisesRegex(ValueError, "missing or unknown"):
            browser_host.evaluate(self.profile, snapshot)
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["os"]["build"] = "26100"
        with self.assertRaisesRegex(ValueError, "must be an integer"):
            browser_host.evaluate(self.profile, snapshot)
        profile = copy.deepcopy(self.profile)
        profile["spec"]["calibration"]["maximumIdleCpuPercent"] = 101
        with patch.object(browser_host.yaml, "safe_load", return_value=profile):
            with self.assertRaisesRegex(ValueError, "must not exceed"):
                browser_host.load_profile()

    def test_validate_is_offline_and_check_can_use_a_recorded_snapshot(self):
        fixture = browser_host.ROOT / "tests/fixtures/windows-browser-host.json"
        for arguments in (
            ["browser_host.py", "validate"],
            ["browser_host.py", "check", "--snapshot", str(fixture)],
        ):
            with (
                patch("sys.argv", arguments),
                patch.object(browser_host.subprocess, "run") as run,
                redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(browser_host.main(), 0)
                run.assert_not_called()
                self.assertIn("windows-mainstream", output.getvalue())

    def test_live_inspection_fails_closed_outside_windows(self):
        with patch.object(browser_host.platform, "system", return_value="Linux"):
            with self.assertRaisesRegex(ValueError, "requires Windows"):
                browser_host.live_snapshot()

    def test_check_can_write_an_immutable_utf8_qualification(self):
        fixture = browser_host.ROOT / "tests/fixtures/windows-browser-host.json"
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "qualification.json"
            arguments = [
                "browser_host.py",
                "check",
                "--snapshot",
                str(fixture),
                "--output",
                str(output_path),
            ]
            with patch("sys.argv", arguments), redirect_stdout(io.StringIO()) as output:
                self.assertEqual(browser_host.main(), 0)
            receipt = json.loads(output.getvalue())
            self.assertEqual(receipt["status"], "ACCEPTED")
            self.assertEqual(json.loads(output_path.read_bytes())["status"], "ACCEPTED")

            with patch("sys.argv", arguments), redirect_stdout(io.StringIO()):
                self.assertEqual(browser_host.main(), 1)


if __name__ == "__main__":
    unittest.main()
