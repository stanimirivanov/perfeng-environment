import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts.cluster import (
    ACTIONS,
    CONTEXT,
    NAME,
    bootstrap_command,
    execute,
    run,
    verify_context,
    verify_local_docker,
)


class ClusterTests(unittest.TestCase):
    def test_cli_exposes_only_consumer_namespace_bootstrap(self) -> None:
        self.assertEqual(ACTIONS, ("bootstrap",))

    def test_private_stdin_is_delivered_without_printing(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured):
            output = run(
                [sys.executable, "-c", "import sys; print(len(sys.stdin.buffer.read()))"],
                input_data=b"private-password",
            )
        self.assertEqual(output.strip(), "16")
        self.assertNotIn("private-password", captured.getvalue())

    def test_private_stdin_is_not_resubmitted_after_timeout(self) -> None:
        with patch("scripts.cluster.subprocess.Popen") as popen, redirect_stdout(io.StringIO()):
            process = popen.return_value.__enter__.return_value
            process.returncode = 0
            process.communicate.side_effect = [subprocess.TimeoutExpired("kubectl", 10), (b"", b"")]
            run(["kubectl", "create", "-f", "-"], input_data=b"private-password")
            self.assertEqual(
                process.communicate.call_args_list[0].kwargs["input"], b"private-password"
            )
            self.assertIsNone(process.communicate.call_args_list[1].kwargs["input"])
            self.assertEqual(popen.call_args.kwargs["stdin"], subprocess.PIPE)

    def test_utf8_output_and_non_locale_stderr(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured):
            output = run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os; os.write(1, b'caf\\xc3\\xa9'); "
                        "os.write(2, b'private-progress-\\xe2\\x8f\\xb3\\x8f')"
                    ),
                ]
            )
        self.assertEqual(output, "caf\u00e9")
        self.assertIn("Starting", captured.getvalue())
        self.assertIn("Completed", captured.getvalue())
        self.assertNotIn("private-progress", captured.getvalue())

    def test_waiting_progress_preserves_private_output(self) -> None:
        captured = io.StringIO()
        with patch("scripts.cluster.subprocess.Popen") as popen, redirect_stdout(captured):
            process = popen.return_value.__enter__.return_value
            process.returncode = 0
            process.communicate.side_effect = [
                subprocess.TimeoutExpired("kind", 10),
                (b"private-kubeconfig", b"private-stderr"),
            ]
            self.assertEqual(run(["kind", "get", "kubeconfig"]), "private-kubeconfig")
        self.assertIn("Still running kind command", captured.getvalue())
        self.assertNotIn("private-", captured.getvalue())
        self.assertEqual(process.communicate.call_count, 2)
        self.assertNotIn("text", popen.call_args.kwargs)

    def test_failure_withholds_output_and_completion(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured), self.assertRaisesRegex(ValueError, "exit 7"):
            run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os; os.write(1, b'private-output'); "
                        "os.write(2, b'private-error'); raise SystemExit(7)"
                    ),
                ]
            )
        self.assertNotIn("private-", captured.getvalue())
        self.assertNotIn("Completed", captured.getvalue())

    def test_invalid_stdout_fails_without_false_completion(self) -> None:
        captured = io.StringIO()
        with redirect_stdout(captured), self.assertRaisesRegex(ValueError, "invalid UTF-8"):
            run([sys.executable, "-c", "import os; os.write(1, b'\\x8f')"])
        self.assertNotIn("Completed", captured.getvalue())

    def test_remote_docker_endpoints_are_rejected(self) -> None:
        with (
            patch.dict(os.environ, {"DOCKER_HOST": "tcp://remote:2376"}),
            patch("scripts.cluster.run") as command,
        ):
            with self.assertRaises(ValueError):
                verify_local_docker()
            command.assert_not_called()
        with patch.dict(os.environ, {}, clear=True):
            with patch("scripts.cluster.run", return_value='"ssh://remote"'):
                with self.assertRaises(ValueError):
                    verify_local_docker()
            with patch("scripts.cluster.run", return_value='"unix:///var/run/docker.sock"'):
                verify_local_docker()

    def test_bootstrap_command_applies_only_declarative_namespaces(self) -> None:
        command = bootstrap_command()

        self.assertIn("--kubeconfig", command)
        self.assertEqual(command[command.index("--context") + 1], CONTEXT)
        self.assertEqual(command[-3:-1], ["apply", "-f"])
        self.assertEqual(
            Path(command[-1]).parts[-4:],
            ("gitops", "environments", "local", "namespaces.yaml"),
        )

    def test_bootstrap_requires_existing_provider_cluster(self) -> None:
        with (
            patch("scripts.cluster.run", side_effect=["kind v0.31.0", ""]),
            patch("scripts.cluster.verify_local_docker") as docker,
        ):
            with self.assertRaisesRegex(ValueError, "infrastructure bootstrap"):
                execute()
        docker.assert_not_called()

    def test_bootstrap_validates_provider_identity_before_apply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            with (
                patch("scripts.cluster.run", side_effect=["kind v0.31.0", NAME, ""]) as command,
                patch("scripts.cluster.verify_local_docker") as docker,
                patch("scripts.cluster.verify_context") as context,
            ):
                execute(root)

        docker.assert_called_once_with()
        context.assert_called_once_with(root)
        self.assertEqual(command.call_args.args[0], bootstrap_command(root))

    def test_wrong_kind_version_is_rejected(self) -> None:
        with patch("scripts.cluster.run", return_value="kind v0.30.0") as command:
            with self.assertRaises(ValueError):
                execute()
        command.assert_called_once_with(["kind", "version"])

    def test_foreign_kubeconfig_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".local").mkdir()
            config = root / ".local/perfeng-local.kubeconfig"
            config.write_text("clusters: [{name: production}]")
            with patch("scripts.cluster.run", return_value="clusters: [{name: local}]"):
                with self.assertRaises(ValueError):
                    verify_context(root)

    def test_matching_kubeconfig_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".local").mkdir()
            config = root / ".local/perfeng-local.kubeconfig"
            value = {"clusters": [{"name": CONTEXT}], "users": [], "contexts": []}
            config.write_text(yaml.safe_dump(value))
            with patch("scripts.cluster.run", return_value=yaml.safe_dump(value)):
                verify_context(root)


if __name__ == "__main__":
    unittest.main()
