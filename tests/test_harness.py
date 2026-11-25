"""Exercise the offline harness against the repository policy CLI."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import harness.__main__ as harness_cli

ROOT = Path(__file__).resolve().parents[1]
POLICY = Path("scripts/repository_policy.py")
ISSUE_FORM = Path(".github/ISSUE_TEMPLATE/work-item.md")
PR_FORM = Path(".github/PULL_REQUEST_TEMPLATE.md")
PR_FIX_FORM = Path(".github/PR_FIX_COMMENT_TEMPLATE.md")


class HarnessTests(unittest.TestCase):
    def test_invalid_manifest_prevents_offline_dispatch(self) -> None:
        check = """
[[checks]]
id = "{id}"
stage = "{stage}"
invocation = {invocation}
guide = "docs/development/harness.md"
"""
        command = """["-c", "print('valid check ran')"]"""
        valid_check = check.format(id="valid", stage="offline", invocation=command)
        unknown_stage = check.format(id="typo", stage="offlien", invocation=command)
        invalid_invocation = check.format(id="invalid", stage="offline", invocation='["-m", 42]')
        for manifest, diagnostic in (
            (unknown_stage + valid_check, b"checks[1].stage"),
            (valid_check + invalid_invocation, b"checks[2].invocation"),
            ("checks = {}", b"register at least one check"),
            ("[[checks]", b"cannot load TOML"),
        ):
            with self.subTest(diagnostic=diagnostic), tempfile.TemporaryDirectory() as directory:
                checkout = Path(directory)
                shutil.copytree(
                    ROOT / "harness",
                    checkout / "harness",
                    ignore=shutil.ignore_patterns("__pycache__"),
                )
                (checkout / "harness/checks.toml").write_text(manifest, encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, "-m", "harness", "offline"],
                    cwd=checkout,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, b"")
                self.assertIn(b"harness/checks.toml", result.stderr)
                self.assertIn(diagnostic, result.stderr)

    def test_offline_stage_reports_wrapper_timeout(self) -> None:
        stderr = io.StringIO()
        with (
            patch.object(sys, "argv", ["harness", "offline"]),
            patch("subprocess.run", side_effect=subprocess.TimeoutExpired("policy", 120)) as run,
            redirect_stderr(stderr),
        ):
            self.assertEqual(harness_cli.main(), 124)
        self.assertEqual(run.call_args.kwargs["timeout"], 120)
        self.assertEqual(
            stderr.getvalue(),
            "repository-policy: exceeded 120s; see docs/development/harness.md#offline-harness-pilot\n",
        )

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
