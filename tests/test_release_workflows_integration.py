"""Keep the live CI fixture isolated from offline checks and retained environments."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/integration/release-workflows"
SCRIPT = FIXTURE / "run.sh"
GIT_ROOT = Path(shutil.which("git") or ".").parent.parent
BASH = next(
    (
        str(path)
        for path in (
            GIT_ROOT / "bin/bash.exe",
            GIT_ROOT / "usr/bin/bash.exe",
            Path(shutil.which("bash") or "."),
        )
        if path.is_file()
    ),
    None,
)


class ReleaseWorkflowsIntegrationTests(unittest.TestCase):
    def test_ci_authority_and_artifact_scope_are_bounded(self):
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/release-workflows-kind.yml").read_text()
        )
        events = workflow.get("on", workflow.get(True))
        self.assertEqual(set(events), {"pull_request", "workflow_dispatch"})
        self.assertEqual(workflow["permissions"], {"contents": "read"})
        job = workflow["jobs"]["maintenance-verifier"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")
        self.assertLessEqual(job["timeout-minutes"], 30)
        self.assertIn("head.repo.full_name == github.repository", job["if"])
        checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout"))
        self.assertFalse(checkout["with"]["persist-credentials"])
        run = next(s for s in job["steps"] if s.get("run") == "make release-workflows-integration")
        self.assertEqual(run["env"], {"GITHUB_TOKEN": "${{ github.token }}"})
        upload = next(
            s for s in job["steps"] if s.get("uses", "").startswith("actions/upload-artifact")
        )
        self.assertEqual(upload["if"], "always()")
        self.assertEqual(upload["with"]["path"], "${{ runner.temp }}/release-workflows-results/")
        self.assertLessEqual(upload["with"]["retention-days"], 7)
        makefile = (ROOT / "Makefile").read_text()
        self.assertIn("validate: verify render-check supply-chain", makefile)
        self.assertIn(
            "release-workflows-integration:\n\tbash tests/integration/release-workflows/run.sh",
            makefile,
        )

    def test_fixture_selects_the_existing_restricted_child_and_pinned_tools(self):
        workflow = yaml.safe_load((FIXTURE / "workflow.yaml").read_text())
        child = yaml.safe_load(
            (ROOT / "gitops/release-workflows/verify-release-quiescence-v2.yaml").read_text()
        )
        self.assertEqual(workflow["spec"]["serviceAccountName"], "release-maintenance-checkpoint")
        self.assertEqual(workflow["spec"]["activeDeadlineSeconds"], 300)
        self.assertEqual(
            workflow["spec"]["templates"][0]["steps"][0][0]["templateRef"],
            {"name": child["metadata"]["name"], "template": "run"},
        )
        deployment = yaml.safe_load((FIXTURE / "deployment.yaml").read_text())
        self.assertEqual(deployment["spec"]["replicas"], 0)
        self.assertFalse(deployment["spec"]["template"]["spec"]["automountServiceAccountToken"])
        toolchain = json.loads((FIXTURE / "toolchain.json").read_text())
        self.assertEqual(set(toolchain), {"kind", "kubectl", "argo", "workflows"})
        self.assertEqual(toolchain["argo"]["version"], toolchain["workflows"]["version"])
        for tool in toolchain.values():
            self.assertIn(tool["version"], tool["url"])
            digest = tool.get("sha256") or (ROOT / tool["checksumFile"]).read_text().strip()
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertTrue(tool["url"].startswith(("https://github.com/", "https://dl.k8s.io/")))
        self.assertEqual(toolchain["workflows"]["version"], "v4.1.5")
        self.assertNotIn(b"\r", SCRIPT.read_bytes())
        cases = re.findall(r"^run_case (\S+) (success|failure) ", SCRIPT.read_text(), re.M)
        self.assertEqual(
            cases,
            [
                ("first", "success"),
                ("repeat", "success"),
                ("wrong-uid", "failure"),
                ("unpaused", "failure"),
                ("matching-pod", "failure"),
                ("missing-read-grant", "failure"),
                ("recovered", "success"),
            ],
        )

    @unittest.skipUnless(BASH, "Bash is unavailable; Linux CI checks the live fixture shell")
    def test_shell_syntax_and_unsupported_runners_refuse_before_side_effects(self):
        assert BASH is not None
        syntax = subprocess.run([BASH, "-n", SCRIPT.as_posix()], capture_output=True, timeout=20)
        self.assertEqual(syntax.returncode, 0, syntax.stderr)
        for authority in (
            {},
            {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "self-hosted", "RUNNER_OS": "Linux"},
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Windows",
            },
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                "GITHUB_TOKEN": "fixture-token-must-not-be-logged",
                "DOCKER_HOST": "tcp://retained.example:2376",
            },
            {
                "GITHUB_ACTIONS": "true",
                "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Linux",
                "GITHUB_TOKEN": "fixture-token-must-not-be-logged",
                "DOCKER_CONTEXT": "retained-context",
            },
        ):
            with self.subTest(authority=authority), tempfile.TemporaryDirectory() as temp:
                environment = dict(os.environ)
                for key in (
                    "GITHUB_ACTIONS",
                    "RUNNER_ENVIRONMENT",
                    "RUNNER_OS",
                    "RUNNER_TEMP",
                    "GITHUB_TOKEN",
                    "DOCKER_HOST",
                    "DOCKER_CONTEXT",
                ):
                    environment.pop(key, None)
                environment.update(authority)
                environment["RUNNER_TEMP"] = Path(temp).as_posix()
                result = subprocess.run(
                    [BASH, SCRIPT.as_posix()],
                    cwd=temp,
                    env=environment,
                    capture_output=True,
                    timeout=20,
                )
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn(b"Refusing", result.stderr)
                self.assertNotIn(b"fixture-token-must-not-be-logged", result.stderr + result.stdout)
                self.assertEqual(list(Path(temp).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
