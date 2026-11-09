"""Exercise offline inspection with disposable manifest and archive inputs."""

import gzip
import hashlib
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.contracts_inspect import inspect, publisher_policy


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ContractInspectTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.manifest = self.root / "manifest.json"
        self.archive = self.root / "archive.tar.gz"
        self.policy = self.root / "policy.json"
        self.bundle = self.root / "bundle.jsonl"
        self.bundle.write_bytes(b"fixture bundle")
        self.members = [
            ("LICENSE", b"test licence", tarfile.REGTYPE),
            ("schema.json", b'{"$ref":"other.json#/$defs/example"}', tarfile.REGTYPE),
            ("other.json", b'{"$defs":{"example":{}}}', tarfile.REGTYPE),
        ]

    def prepare(self, *, kind: str = "promoted", suffix: bytes = b"") -> None:
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for name, data, mode in self.members:
                info = tarfile.TarInfo(name)
                info.type = mode
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        compressed = gzip.compress(output.getvalue() + suffix)
        self.archive.write_bytes(compressed)
        value = {
            "schemaVersion": 1,
            "kind": kind,
            "releaseId": "fixture",
            "archiveSha256": sha(compressed),
            "source": {
                "repository": "https://github.com/stanimirivanov/perfeng-contracts.git",
                "revision": "a" * 40,
                "version": "0.22.0",
            },
            "members": {
                name: sha(data) for name, data, _ in self.members if not name.startswith("..")
            },
        }
        if kind == "promoted":
            value["admissionSha256"] = "b" * 64
        manifest = json.dumps(value).encode()
        self.manifest.write_bytes(manifest)
        self.policy.write_text(
            json.dumps(
                {
                    "manifestSha256": sha(manifest),
                    "sourceDigest": "c" * 40,
                    "sourceRef": "refs/heads/main",
                    "signerWorkflow": "stanimirivanov/perfeng-contracts/.github/workflows/publish.yml",
                    "signerDigest": "d" * 40,
                }
            ),
            encoding="utf-8",
        )

    def inspect(self) -> dict[str, str]:
        return inspect(self.manifest, self.archive, self.bundle, self.policy, self.root / "gh")

    @patch("scripts.contracts_inspect.verify_publisher")
    def test_returns_only_verified_inventory(self, verify) -> None:
        self.prepare()
        original = self.manifest.read_bytes()
        verify.side_effect = lambda *_: self.manifest.write_bytes(b"changed on disk")
        self.assertEqual(self.inspect(), {name: sha(data) for name, data, _ in self.members})
        self.assertEqual(verify.call_args.args[1], original)
        self.assertEqual(verify.call_args.args[3]["sourceDigest"], "c" * 40)
        self.assertFalse((self.root / "LICENSE").exists())

    @patch("scripts.contracts_inspect.verify_publisher")
    def test_refuses_unapproved_and_modified_inputs(self, verify) -> None:
        self.prepare(kind="candidate")
        with self.assertRaisesRegex(ValueError, "promoted"):
            self.inspect()
        self.prepare()
        self.manifest.write_bytes(self.manifest.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.inspect()
        verify.assert_not_called()
        self.prepare()
        verify.side_effect = ValueError("publisher verification failed")
        with self.assertRaisesRegex(ValueError, "publisher"):
            self.inspect()

    @patch("scripts.contracts_inspect.verify_publisher")
    def test_refuses_unsafe_archive_content(self, _verify) -> None:
        original = self.members.copy()
        for name, mode, error in (
            ("../outside", tarfile.REGTYPE, "undeclared"),
            ("LICENSE", tarfile.REGTYPE, "duplicate"),
            ("extra", tarfile.SYMTYPE, "regular"),
        ):
            with self.subTest(name=name, mode=mode):
                self.members = [
                    *original,
                    (name, b"test licence" if name == "LICENSE" else b"unsafe", mode),
                ]
                self.prepare()
                with self.assertRaisesRegex(ValueError, error):
                    self.inspect()
        self.members = original
        self.prepare(suffix=b"hidden payload")
        with self.assertRaisesRegex(ValueError, "trailing"):
            self.inspect()
        self.prepare()
        with (
            patch("scripts.contracts_inspect.TAR_LIMIT", 1024),
            patch("scripts.contracts_inspect.tarfile.open") as parser,
            self.assertRaisesRegex(ValueError, "expanded size"),
        ):
            self.inspect()
        parser.assert_not_called()

    @patch("scripts.contracts_inspect.verify_publisher")
    def test_rejects_archive_substitution(self, _verify) -> None:
        self.prepare()
        self.archive.write_bytes(self.archive.read_bytes() + b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            self.inspect()

    @patch("scripts.contracts_inspect.verify_publisher")
    def test_checks_json_reference_closure(self, _verify) -> None:
        for reference in ("missing.json", "other.json#/missing", "https://example.org/schema.json"):
            with self.subTest(reference=reference):
                self.members[1] = (
                    "schema.json",
                    json.dumps({"$ref": reference}).encode(),
                    tarfile.REGTYPE,
                )
                self.prepare()
                with self.assertRaisesRegex(ValueError, "reference"):
                    self.inspect()

    def test_policy_cannot_admit_other_repositories(self) -> None:
        self.prepare()
        value = json.loads(self.policy.read_bytes())
        value["repository"] = "malancas/attest-demo"
        self.policy.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "policy"):
            publisher_policy(self.policy)
