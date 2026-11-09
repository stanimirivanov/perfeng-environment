"""Exercise the materializer tool release boundary without hosted publication."""

import hashlib
import io
import platform
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.contracts_tool_release import archive_bytes, restore


class ToolReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.archive = self.root / "tool.tar.gz"
        self.bundle = self.root / "attestation.jsonl"
        self.bundle.write_bytes(b"test bundle")
        self.target = self.root / "restored"
        self.verifier = self.root / "trusted-gh"

    def pins(self) -> dict[str, str]:
        return {
            "archiveSha256": hashlib.sha256(self.archive.read_bytes()).hexdigest(),
            "platform": f"{sys.platform}-{platform.machine()}",
            "sourceDigest": "a" * 40,
            "signerDigest": "b" * 40,
        }

    @patch("scripts.contracts_tool_release.verify_publisher")
    def test_restores_only_authenticated_bytes_into_new_directory(self, verify) -> None:
        files = {"app/tool.py": b"tool", "bin/gh": b"verifier"}
        data = archive_bytes(files)
        self.assertEqual(data, archive_bytes(dict(reversed(list(files.items())))))
        self.archive.write_bytes(data)
        pins = self.pins()
        verify.side_effect = lambda *_: self.archive.write_bytes(b"replacement")
        restore(self.archive, self.bundle, self.verifier, pins, self.target)
        self.assertEqual((self.target / "app/tool.py").read_bytes(), b"tool")
        self.assertTrue((self.target / "bin/gh").stat().st_mode & 0o100)
        self.archive.write_bytes(data)
        with self.assertRaises(FileExistsError):
            restore(self.archive, self.bundle, self.verifier, pins, self.target)

    @patch("scripts.contracts_tool_release.verify_publisher")
    def test_rejects_wrong_platform_digest_and_publisher_before_writes(self, verify) -> None:
        self.archive.write_bytes(archive_bytes({"app/tool.py": b"tool"}))
        for key, value in (("platform", "linux-arm64"), ("archiveSha256", "0" * 64)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                restore(
                    self.archive,
                    self.bundle,
                    self.verifier,
                    {**self.pins(), key: value},
                    self.target,
                )
        verify.assert_not_called()
        verify.side_effect = ValueError("publisher")
        with self.assertRaisesRegex(ValueError, "publisher"):
            restore(self.archive, self.bundle, self.verifier, self.pins(), self.target)
        self.assertFalse(self.target.exists())

    @patch("scripts.contracts_tool_release.verify_publisher")
    def test_rejects_unsafe_archive_before_writes(self, _verify) -> None:
        data = io.BytesIO()
        with tarfile.open(fileobj=data, mode="w:gz") as archive:
            archive.addfile(tarfile.TarInfo("../escape"))
        self.archive.write_bytes(data.getvalue())
        with self.assertRaisesRegex(ValueError, "member"):
            restore(self.archive, self.bundle, self.verifier, self.pins(), self.target)
        self.assertFalse(self.target.exists())

    def test_workflow_separates_preparation_attestation_and_publication(self) -> None:
        import yaml

        workflow_path = (
            Path(__file__).resolve().parents[1] / ".github/workflows/contracts-tool-release.yml"
        )
        workflow = yaml.load(workflow_path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
        self.assertEqual(set(workflow["on"]), {"workflow_dispatch"})
        self.assertEqual(workflow["permissions"], {})
        jobs = workflow["jobs"]
        self.assertEqual(jobs["prepare"]["permissions"], {"contents": "read"})
        self.assertEqual(jobs["attest"]["permissions"]["id-token"], "write")
        self.assertEqual(jobs["publish"]["permissions"], {"contents": "write"})
        self.assertEqual(jobs["publish"]["needs"], "attest")
        self.assertEqual(jobs["publish"]["environment"], "contract-tool-publication")
        for job in jobs.values():
            self.assertIn("github.ref_protected", job["if"])
            self.assertIn("github.ref == 'refs/heads/main'", job["if"])
            self.assertIn("github.sha == inputs.expected_sha", job["if"])
            self.assertIn("PERFENG_TOOL_PUBLICATION_ENABLED == 'true'", job["if"])
        publish = jobs["publish"]["steps"][-1]["run"]
        self.assertLess(publish.index("--draft "), publish.index("--draft=false"))
        self.assertNotIn("--clobber", publish)
