"""Exercise command route policy through the offline guard CLI."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR = ROOT / "harness/command_routes.py"


class CommandRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.checkout = Path(directory.name)
        subprocess.run(["git", "init", "--quiet"], cwd=self.checkout, check=True, timeout=10)
        self.write("harness/command-routes.toml", "routes = []\n")

    def write(self, path: str, source: str) -> Path:
        target = self.checkout / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")
        return target

    def guard(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(VALIDATOR)],
            cwd=self.checkout,
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )

    def test_accepts_reviewed_non_executable_shell_script_without_running_it(self) -> None:
        self.write("launch.sh", "touch unexpected-marker\n").chmod(0o644)
        self.write("Makefile", "fixture:\n\tbash launch.sh\n")
        self.write(
            "harness/command-routes.toml",
            """
[[routes]]
id = "script:launch.sh"
owner = "Fixture owner"
purpose = "Exercise a non-executable shell entrypoint."
definition = "sha256:0705999b86cf5187492f0c7c70e4dcf4ecbb38d72d83e5aa503907277bdda284"
""",
        )
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("command-routes: reviewed command routes match", result.stdout)
        self.assertFalse((self.checkout / "unexpected-marker").exists())

    def test_reports_unreviewed_file_and_module_routes_in_working_tree(self) -> None:
        self.write(".gitignore", "ignored-*\n")
        self.write("ignored-entry.sh", "#!/bin/sh\nexit 0\n")
        self.write("plain_runner.py", "print('directly called')\n").chmod(0o644)
        self.write("Makefile", "fixture:\n\tpython3 -B plain_runner.py\n")
        self.write("package/__main__.py", "print('module entrypoint')\n")
        self.write("package/helper.py", "def imported():\n    return 42\n")
        self.write(
            "tests/test_value.py",
            'import unittest\n\nif __name__ == "__main__":\n    unittest.main()\n',
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unreviewed-route: plain_runner.py:1", result.stderr)
        self.assertIn("unreviewed-route: package/__main__.py:1", result.stderr)
        self.assertIn("fix: reuse an existing route", result.stderr)
        self.assertIn("harness.md#reviewing-command-routes", result.stderr)
        self.assertNotIn("ignored-entry.sh", result.stderr)
        self.assertNotIn("helper.py", result.stderr)
        self.assertNotIn("test_value.py", result.stderr)

    def test_accepts_imported_helpers_and_flags_a_new_main_guard(self) -> None:
        self.write("package/helper.py", "def imported():\n    return 42\n")
        self.write("package/caller.py", "from .helper import imported\n")
        initial = self.guard()
        self.assertEqual(initial.returncode, 0, initial.stderr)
        self.write(
            "package/caller.py",
            'from .helper import imported\n\nif "__main__" == __name__:\n    print(imported())\n',
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unreviewed-route: package/caller.py:3", result.stderr)

    def test_reports_changed_and_removed_working_tree_definitions(self) -> None:
        self.write("launch.sh", "touch unexpected-marker\n")
        self.write("removed.sh", "touch unexpected-marker\n")
        self.write(
            "harness/command-routes.toml",
            """
[[routes]]
id = "script:launch.sh"
owner = "Fixture owner"
purpose = "Exercise a changed working-tree script."
definition = "sha256:0705999b86cf5187492f0c7c70e4dcf4ecbb38d72d83e5aa503907277bdda284"

[[routes]]
id = "script:removed.sh"
owner = "Fixture owner"
purpose = "Exercise working-tree deletion of a tracked route."
definition = "sha256:0705999b86cf5187492f0c7c70e4dcf4ecbb38d72d83e5aa503907277bdda284"
""",
        )
        subprocess.run(
            ["git", "add", "launch.sh", "removed.sh"], cwd=self.checkout, check=True, timeout=10
        )
        self.assertEqual(self.guard().returncode, 0)
        self.write("launch.sh", "exit 0\n")
        (self.checkout / "removed.sh").unlink()
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("changed-definition: launch.sh:1", result.stderr)
        self.assertIn(
            "missing-route: harness/command-routes.toml: 'script:removed.sh'", result.stderr
        )

    def test_discovers_a_separately_invoked_pod_payload_from_an_imported_file(self) -> None:
        self.write("package/client.py", "print('pod client')\n")
        self.write(
            "package/transport.py",
            """
from pathlib import Path

from package import client

def run_in_pod():
    return run(["kubectl", "exec", "pod", "--", "python", "-c",
                Path(client.__file__).read_text(encoding="utf-8")])
""",
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unreviewed-route: package/transport.py:8", result.stderr)
        self.assertIn("'payload:package/transport.py#package.client'", result.stderr)

    def test_discovers_named_payloads_passed_to_the_running_python_executable(self) -> None:
        self.write(
            "runner.py",
            """import sys

RUN_MODULE = "import runpy; " "runpy.run_module('provider')"
OFFLINE = "print('offline check')"

def commands():
    return (
        [sys.executable, "-I", "-c", RUN_MODULE],
        ["unshare", "-Urn", sys.executable, "-IB", "-c", OFFLINE],
    )
""",
        )
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(
            "unreviewed-route: runner.py:8: 'payload:runner.py#RUN_MODULE'", result.stderr
        )
        self.assertIn("unreviewed-route: runner.py:9: 'payload:runner.py#OFFLINE'", result.stderr)

    def test_detects_changes_to_the_reviewed_artifact_upload_pod_programs(self) -> None:
        source = """def upload_script(reference):
    return f"printf '%s' '{reference}'"

def publish(kube, pod_name, source):
    cluster.run([*kube, "exec", "-i", pod_name, "--", "sh", "-c", "cat > /tmp/source"],
                input_data=source.content)
    cluster.run([*kube, "exec", pod_name, "--", "sh", "-c", upload_script(source.reference)])
"""
        self.write("scripts/artifact_upload.py", source)
        self.write(
            "harness/command-routes.toml",
            """
[[routes]]
id = "payload:scripts/artifact_upload.py#stage_source"
owner = "Fixture owner"
purpose = "Stage the fixture source."
definition = "sha256:2d20c7f8f60786cb258c0d91a8e50fcdacf5d9b0155e83b1f1ff6b05cda481a4"

[[routes]]
id = "payload:scripts/artifact_upload.py#upload_script"
owner = "Fixture owner"
purpose = "Build the fixture program."
definition = "sha256:8a29d638a866ceb2f5a2bf619154915082f9fb7c011b371fe10635f5c35ea1cc"
""",
        )
        accepted = self.guard()
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        for original, replacement, line, name in (
            ("cat > /tmp/source", "cat > /tmp/other", 5, "stage_source"),
            ("printf '%s' '{reference}'", "echo '{reference}'", 7, "upload_script"),
        ):
            with self.subTest(name=name):
                self.write("scripts/artifact_upload.py", source.replace(original, replacement))
                result = self.guard()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(
                    f"changed-definition: scripts/artifact_upload.py:{line}: "
                    f"'payload:scripts/artifact_upload.py#{name}'",
                    result.stderr,
                )

    def test_output_only_recipes_do_not_make_import_helpers_callable(self) -> None:
        self.write("helper.py", "def value():\n    return 42\n")
        self.write(
            "Makefile",
            "help:\n"
            "\t@echo 'For development, python helper.py prints its value.'\n"
            '\t@printf "%s\\n" "Try python helper.py for details."\n',
        )
        result = self.guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.write("Makefile", "run:\n\t@echo 'Starting'; python helper.py\n")
        result = self.guard()
        self.assertIn("unreviewed-route: helper.py:1", result.stderr)

    def test_oversized_source_fails_with_its_path_and_no_route_proposal(self) -> None:
        checkout_file = self.write("oversized.py", "pass\n")
        with checkout_file.open("ab") as stream:
            stream.truncate(2 * 1024 * 1024 + 1)
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("scan-failed: .: oversized.py exceeds the 2 MiB source limit", result.stderr)
        self.assertNotIn("unreviewed-route", result.stderr)

    def test_source_only_shell_fragment_stays_with_its_caller(self) -> None:
        self.write("shell/caller.sh", '#!/bin/sh\n. "./shell/library.sh"\n')
        self.write("shell/library.sh", "#!/bin/sh\nhello() { echo hello; }\n")
        result = self.guard()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("unreviewed-route: shell/caller.sh:1", result.stderr)
        self.assertNotIn("shell/library.sh", result.stderr)
        self.write("Makefile", "fixture:\n\tbash shell/library.sh\n")
        result = self.guard()
        self.assertIn("unreviewed-route: shell/library.sh:1", result.stderr)
