"""Exercise the offline harness against the standalone validation CLIs."""

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
GITOPS = Path("scripts/gitops.py")
UNREVIEWED_SCRIPT = Path("unreviewed.sh")
ROOT_APPLICATION = Path("gitops/bootstrap/local-root-application.yaml")
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

    def test_offline_stage_preserves_standalone_validation_results(self) -> None:
        for invalid_input in (None, ROOT_APPLICATION, PR_FIX_FORM, UNREVIEWED_SCRIPT):
            with (
                self.subTest(invalid_input=invalid_input),
                tempfile.TemporaryDirectory() as directory,
            ):
                checkout = Path(directory)
                # The script inventory compares the full checkout with the reviewed baseline.
                tracked = subprocess.check_output(
                    ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                    cwd=ROOT,
                    timeout=15,
                )
                for entry in filter(None, tracked.split(b"\0")):
                    path = Path(entry.decode())
                    if not (ROOT / path).is_file():
                        continue
                    (checkout / path).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(ROOT / path, checkout / path)
                subprocess.run(["git", "init", "--quiet"], cwd=checkout, check=True, timeout=15)

                policy_status = 0
                policy_message = b"repo-policy: repository policy checks passed"
                gitops_status = 0
                gitops_message = b"Validated local GitOps Applications and composition."
                guard_status = 0
                if invalid_input == ROOT_APPLICATION:
                    manifest = checkout / ROOT_APPLICATION
                    manifest.write_text(
                        manifest.read_text(encoding="utf-8").replace(
                            "name: perfeng-local-root", "name: unexpected-root"
                        ),
                        encoding="utf-8",
                    )
                    gitops_status = 1
                    gitops_message = b"Local GitOps error: Invalid local root Application"
                elif invalid_input == PR_FIX_FORM:
                    (checkout / PR_FIX_FORM).unlink()
                    policy_status = 1
                    policy_message = b"include Issue, Resolution and Verification fields"
                elif invalid_input == UNREVIEWED_SCRIPT:
                    (checkout / UNREVIEWED_SCRIPT).write_text("exit 0\n", encoding="utf-8")
                    guard_status = 1

                policy = subprocess.run(
                    [sys.executable, str(POLICY)],
                    cwd=checkout,
                    capture_output=True,
                    check=False,
                    timeout=15,
                )
                gitops = subprocess.run(
                    [sys.executable, str(GITOPS), "validate"],
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
                self.assertEqual(policy.returncode, policy_status, policy.stdout + policy.stderr)
                self.assertIn(policy_message, policy.stdout)
                self.assertEqual(gitops.returncode, gitops_status, gitops.stdout + gitops.stderr)
                self.assertIn(gitops_message, gitops.stdout)
                expected_status = policy.returncode or gitops.returncode or guard_status
                expected_stdout = policy.stdout + (b"" if policy.returncode else gitops.stdout)
                expected_stderr = policy.stderr + (b"" if policy.returncode else gitops.stderr)
                self.assertEqual(
                    harness.returncode, expected_status, harness.stdout + harness.stderr
                )
                if not expected_status:
                    self.assertTrue(harness.stdout.startswith(expected_stdout), harness.stdout)
                    self.assertIn(b"command-routes: reviewed command routes match", harness.stdout)
                else:
                    self.assertEqual(harness.stdout, expected_stdout)
                if guard_status:
                    self.assertIn(
                        b"command-routes/unreviewed-route: unreviewed.sh:1", harness.stderr
                    )
                else:
                    self.assertEqual(harness.stderr, expected_stderr)
