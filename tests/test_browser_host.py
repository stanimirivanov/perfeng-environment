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

    def test_four_core_4k_profile_is_a_distinct_explicit_cohort(self):
        profile = browser_host.load_profile(
            browser_host.ROOT / "profiles/browser/windows-four-core-4k.yaml"
        )
        self.assertEqual(profile["metadata"], {"id": "windows-four-core-4k", "version": "1.0.0"})
        self.assertNotEqual(
            browser_host.fingerprint(profile), browser_host.fingerprint(self.profile)
        )
        expected = copy.deepcopy(self.profile)
        expected["metadata"]["id"] = "windows-four-core-4k"
        expected["spec"]["cpu"]["minimumLogicalCores"] = 4
        expected["spec"]["display"] = {
            "width": 3840,
            "height": 2160,
            "scaleFactor": 1,
            "refreshRateHz": 59,
        }
        self.assertEqual(profile, expected)
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["cpu"].update(logicalCores=4, physicalCores=4, availableLogicalCores=4)
        snapshot["display"] = dict(profile["spec"]["display"])
        self.assertEqual(browser_host.evaluate(profile, snapshot)["status"], "ACCEPTED")
        self.assertEqual(browser_host.evaluate(self.profile, snapshot)["status"], "REJECTED")

    def test_laptop_profile_does_not_relax_os_or_idle_cpu_gates(self):
        profile = browser_host.load_profile(
            browser_host.ROOT / "profiles/browser/windows-four-core-4k.yaml"
        )
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["cpu"].update(
            logicalCores=4, physicalCores=4, availableLogicalCores=4, idleCpuPercent=20
        )
        snapshot["display"] = dict(profile["spec"]["display"])
        snapshot["os"]["build"] = 22621
        snapshot["power"]["plan"] = "{" + snapshot["power"]["plan"] + "}"
        result = browser_host.evaluate(profile, snapshot)
        self.assertEqual(result["status"], "REJECTED")
        self.assertEqual(result["reasons"], ["OS build is too old", "background CPU is too high"])

    def test_laptop_profile_still_rejects_capacity_and_display_drift(self):
        profile = browser_host.load_profile(
            browser_host.ROOT / "profiles/browser/windows-four-core-4k.yaml"
        )
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["display"] = dict(profile["spec"]["display"])
        for section, field, value, reason in (
            ("cpu", "availableLogicalCores", 3, "available CPU capacity is too low"),
            ("display", "scaleFactor", 1.25, "display scaleFactor differs"),
            ("display", "refreshRateHz", 60, "display refreshRateHz differs"),
        ):
            with self.subTest(field=field):
                changed = copy.deepcopy(snapshot)
                changed[section][field] = value
                self.assertIn(reason, browser_host.evaluate(profile, changed)["reasons"])

    def test_explicit_laptop_profile_validation_is_offline(self):
        profile_path = browser_host.ROOT / "profiles/browser/windows-four-core-4k.yaml"
        with (
            patch("sys.argv", ["browser_host.py", "validate", "--profile", str(profile_path)]),
            patch.object(browser_host.subprocess, "run") as run,
            redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(browser_host.main(), 0)
            run.assert_not_called()
            self.assertIn("windows-four-core-4k", output.getvalue())

    def test_balanced_plan_braces_and_case_do_not_change_identity(self):
        snapshot = copy.deepcopy(self.snapshot)
        snapshot["power"]["plan"] = "{" + snapshot["power"]["plan"].upper() + "}"
        self.assertEqual(browser_host.evaluate(self.profile, snapshot)["status"], "ACCEPTED")
        snapshot["power"]["plan"] = "00000000-0000-0000-0000-000000000000"
        self.assertIn(
            "power plan differs", browser_host.evaluate(self.profile, snapshot)["reasons"]
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
