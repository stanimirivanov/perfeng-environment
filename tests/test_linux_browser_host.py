"""Linux collection refuses ambiguous desktop evidence before qualification."""

import subprocess
import unittest
from unittest.mock import patch

from scripts import linux_browser_host as host

DISPLAY = """eDP-1 connected primary 1920x1080+0+0 (normal)
    Transform: 1.000000 0.000000 0.000000
               0.000000 1.000000 0.000000
               0.000000 0.000000 1.000000
  1920x1080 (0x48) 148.500MHz +HSync +VSync *current +preferred
        h: width 1920 start 2008 end 2052 total 2200 skew 0 clock 67.50KHz
        v: height 1080 start 1084 end 1089 total 1125 clock 60.00Hz
"""


class LinuxHostTests(unittest.TestCase):
    def test_observes_exact_display_without_rounding(self):
        self.assertEqual(
            host.display_state(DISPLAY),
            {
                "width": 1920,
                "height": 1080,
                "scaleFactor": 1,
                "refreshRateHz": 60,
            },
        )
        self.assertEqual(
            host.display_state(DISPLAY.replace("60.00Hz", "59.94Hz"))["refreshRateHz"], 59.94
        )

    def test_unknown_multi_output_and_transformed_displays_refuse(self):
        for value in (
            "",
            DISPLAY + DISPLAY,
            DISPLAY.replace("Transform: 1.000000", "Transform: 1.250000"),
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                host.display_state(value)

    def test_wayland_is_not_misreported_as_x11(self):
        with (
            patch.object(host.platform, "system", return_value="Linux"),
            patch.dict(host.os.environ, {"XDG_SESSION_TYPE": "wayland"}),
        ):
            with self.assertRaisesRegex(ValueError, "X11"):
                host.snapshot()

    def test_failed_probe_is_bounded_and_does_not_leak_output(self):
        with patch.object(
            host.subprocess,
            "run",
            return_value=subprocess.CompletedProcess([], 1, "private", "secret"),
        ) as run:
            with self.assertRaisesRegex(ValueError, "Linux host probe failed: glxinfo"):
                host.command("glxinfo", "-B")
            self.assertEqual(run.call_args.kwargs["timeout"], 10)
