from __future__ import annotations

import io
import json
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


def namespace(owner: str) -> str:
    labels = {"app.kubernetes.io/managed-by": owner}
    if owner == infrastructure_runtime.INFRASTRUCTURE_OWNER:
        labels.update(
            {
                "app.kubernetes.io/name": infrastructure_runtime.NAMESPACE,
                "app.kubernetes.io/part-of": "kubernetes-substrate",
            }
        )
    else:
        labels["perfeng.io/purpose"] = "gitops-control-plane"
    return json.dumps(
        {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": infrastructure_runtime.NAMESPACE, "labels": labels},
        }
    )


class InfrastructureRuntimeTests(unittest.TestCase):
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

    def test_legacy_owner_restoration_is_exact_and_ordered(self) -> None:
        kube = ["kubectl"]
        with (
            patch("scripts.infrastructure_runtime.invoke_provider", return_value=0) as provider,
            patch("scripts.infrastructure_runtime.kubectl", return_value=kube),
            patch(
                "scripts.infrastructure_runtime.run_private",
                side_effect=[
                    namespace(infrastructure_runtime.INFRASTRUCTURE_OWNER),
                    "",
                    namespace(infrastructure_runtime.LEGACY_OWNER),
                ],
            ) as run,
        ):
            result = infrastructure_runtime.restore_legacy_owner(Path("infrastructure"))

        self.assertEqual(result, 0)
        provider.assert_called_once_with(
            "cluster",
            "health",
            execute=True,
            infrastructure_root=Path("infrastructure"),
        )
        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            run.call_args_list[1].args[0][-5:],
            ["apply", "--server-side", "--force-conflicts", "-f", "-"],
        )
        self.assertIn(
            b"managed-by: perfeng-environment", run.call_args_list[1].kwargs["input_data"]
        )

    def test_foreign_namespace_cannot_be_restored_as_legacy_owner(self) -> None:
        with self.assertRaisesRegex(ValueError, "unexpected ownership"):
            infrastructure_runtime.validate_namespace_owner(
                namespace("another-owner"),
                infrastructure_runtime.INFRASTRUCTURE_OWNER,
            )


if __name__ == "__main__":
    unittest.main()
