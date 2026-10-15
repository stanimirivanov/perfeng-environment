from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

from scripts import infrastructure_lock, infrastructure_runtime


def archive_with(name: str, content: bytes = b"", *, symbolic_link: bool = False) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        member = tarfile.TarInfo(name)
        if symbolic_link:
            member.type = tarfile.SYMTYPE
            member.linkname = "outside"
            archive.addfile(member)
        else:
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


class InfrastructureRuntimeTests(unittest.TestCase):
    def test_metrics_wrapper_exposes_only_read_only_health(self) -> None:
        command = infrastructure_runtime.provider_command(
            Path("selected-source"), "metrics", "health", execute=True
        )
        self.assertIn("scripts.resource_metrics", command)
        self.assertIn("--execute", command)
        self.assertNotIn("--confirm", command)
        for action in ("approve", "serving-tls", "enable-serving", "bootstrap", "deploy"):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "Unsupported"):
                infrastructure_runtime.provider_command(
                    Path("selected-source"), "metrics", action, execute=True
                )

    def test_archive_extraction_rejects_links_and_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)
            with self.assertRaisesRegex(ValueError, "link or special file"):
                infrastructure_runtime.extract_archive(
                    archive_with("scripts/runtime.py", symbolic_link=True), destination
                )
            with self.assertRaisesRegex(ValueError, "archive path"):
                infrastructure_runtime.extract_archive(
                    archive_with("../outside.py", b"changed"), destination
                )

        self.assertFalse((destination.parent / "outside.py").exists())

    def test_provider_command_uses_isolated_python_and_environment_state(self) -> None:
        source = Path("selected-source")

        command = infrastructure_runtime.provider_command(
            source,
            "cluster",
            "down",
            execute=True,
            confirm_delete="perfeng-local",
        )

        self.assertEqual(
            command[:4],
            [infrastructure_runtime.sys.executable, "-I", "-c", infrastructure_runtime.RUN_MODULE],
        )
        self.assertIn("scripts.local_kind", command)
        self.assertIn(str(infrastructure_runtime.ROOT), command)
        self.assertEqual(command[-3:], ["--execute", "--confirm-delete", "perfeng-local"])

    def test_provider_invocation_materializes_the_selected_git_archive(self) -> None:
        lock = infrastructure_lock.load_lock()
        content = archive_with("scripts/selected.py", b"selected = True\n")
        observed: dict[str, object] = {}

        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            source = Path(str(kwargs["cwd"]))
            observed["source"] = (source / "scripts/selected.py").read_text(encoding="utf-8")
            observed["command"] = command
            return subprocess.CompletedProcess(command, 0)

        with (
            tempfile.TemporaryDirectory() as temporary,
            patch("scripts.infrastructure_runtime.validate", return_value=lock) as validate,
            patch("scripts.infrastructure_runtime.git", return_value=content) as git,
            patch("scripts.infrastructure_runtime.subprocess.run", side_effect=run),
        ):
            result = infrastructure_runtime.invoke_provider(
                "cluster",
                "health",
                execute=False,
                infrastructure_root=Path(temporary),
            )

        self.assertEqual(result, 0)
        self.assertEqual(observed["source"], "selected = True\n")
        self.assertIn("scripts.local_kind", cast(list[str], observed["command"]))
        validate.assert_called_once_with(infrastructure_root=Path(temporary))
        self.assertEqual(git.call_args.args[-3:], ("archive", "--format=tar", lock.revision))


if __name__ == "__main__":
    unittest.main()
