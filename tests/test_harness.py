"""Exercise the offline harness against the repository policy CLI."""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = Path("scripts/repository_policy.py")
ISSUE_FORM = Path(".github/ISSUE_TEMPLATE/work-item.md")
PR_FORM = Path(".github/PULL_REQUEST_TEMPLATE.md")
PR_FIX_FORM = Path(".github/PR_FIX_COMMENT_TEMPLATE.md")


class HarnessTests(unittest.TestCase):
    def test_offline_stage_preserves_repository_policy_result(self) -> None:
        for status, message in (
            (0, b"repo-policy: repository policy checks passed"),
            (1, b"include Issue, Resolution and Verification fields"),
        ):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory:
                checkout = Path(directory)
                shutil.copytree(
                    ROOT / "harness",
                    checkout / "harness",
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
                for path in (POLICY, ISSUE_FORM, PR_FORM, PR_FIX_FORM):
                    (checkout / path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ROOT / path, checkout / path)
                if status == 1:
                    (checkout / PR_FIX_FORM).unlink()

                direct = subprocess.run(
                    [sys.executable, str(POLICY)],
                    cwd=checkout,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                harness = subprocess.run(
                    [sys.executable, "-m", "harness", "offline"],
                    cwd=checkout,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                self.assertEqual(direct.returncode, status)
                self.assertIn(message, direct.stdout)
                self.assertEqual(harness.returncode, direct.returncode, harness.stderr)
                self.assertEqual(harness.stdout, direct.stdout)
                self.assertEqual(harness.stderr, direct.stderr)
