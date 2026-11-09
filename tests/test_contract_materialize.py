"""Exercise activation, ownership and interruption against real directories."""

import fcntl
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.contracts_inspect import VerifiedArchive
from scripts.contracts_materialize import materialize


class MaterializeTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "projections"
        self.root.mkdir(mode=0o700)

    def inputs(self, content: bytes) -> tuple[VerifiedArchive, dict]:
        members = {"LICENSE": b"licence", "schema.json": content}
        revision = "a" * 40
        selection = {
            "sourceRevision": revision,
            "members": {
                name: {
                    "destination": "contracts/" + name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
                for name, data in members.items()
            },
        }
        return VerifiedArchive(revision, members), selection

    def test_installs_exact_bytes_and_reuses_immutable_projection(self) -> None:
        archive, selection = self.inputs(b"schema")
        path = materialize(self.root, archive, selection)
        self.assertEqual((path / "contracts/schema.json").read_bytes(), b"schema")
        self.assertEqual((self.root / "current").read_text(), path.name + "\n")
        self.assertEqual(materialize(self.root, archive, selection), path)

    def test_interruption_before_activation_preserves_previous_projection(self) -> None:
        old, selection = self.inputs(b"old")
        previous = materialize(self.root, old, selection)
        new, selection = self.inputs(b"new")
        with (
            patch("scripts.contracts_materialize.os.replace", side_effect=OSError("interrupted")),
            self.assertRaisesRegex(OSError, "interrupted"),
        ):
            materialize(self.root, new, selection)
        self.assertEqual((self.root / "current").read_text(), previous.name + "\n")
        self.assertEqual((previous / "contracts/schema.json").read_bytes(), b"old")
        self.assertNotEqual(materialize(self.root, new, selection), previous)

    def test_rejects_substitution_and_unsafe_mapping_before_writes(self) -> None:
        old, selection = self.inputs(b"old")
        alternate, _ = self.inputs(b"alternate")
        with self.assertRaisesRegex(ValueError, "digest"):
            materialize(self.root, alternate, selection)
        selection["members"]["LICENSE"]["destination"] = "../outside"
        with self.assertRaisesRegex(ValueError, "destination"):
            materialize(self.root, old, selection)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_refuses_unrelated_content_symlink_and_competing_writer(self) -> None:
        archive, selection = self.inputs(b"schema")
        marker = self.root / "unrelated"
        marker.write_text("keep")
        with self.assertRaisesRegex(ValueError, "unrelated"):
            materialize(self.root, archive, selection)
        self.assertEqual(marker.read_text(), "keep")
        marker.unlink()
        link = self.root.parent / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            materialize(link, archive, selection)
        fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with self.assertRaisesRegex(ValueError, "busy"):
                materialize(self.root, archive, selection)
        finally:
            os.close(fd)
        self.assertEqual(list(self.root.iterdir()), [])
