"""Cold recovery must preserve the independently selected bytes."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.contracts_restore import verify_projection


class ContractRestoreTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Path(directory.name)
        self.generation = self.store / ("a" * 64)
        self.generation.mkdir()
        (self.store / "current").write_text(self.generation.name + "\n")
        self.members = {
            "source.json": {
                "destination": "output.json",
                "sha256": hashlib.sha256(b"{}").hexdigest(),
            }
        }

    def test_restored_generation_matches_reviewed_members(self) -> None:
        (self.generation / "output.json").write_bytes(b"{}")
        self.assertEqual(verify_projection(self.store, self.members), self.generation.name)

    def test_corrupt_or_missing_projection_cannot_pass(self) -> None:
        path = self.generation / "output.json"
        with self.assertRaisesRegex(ValueError, "unavailable"):
            verify_projection(self.store, self.members)
        path.write_bytes(b"wrong")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            verify_projection(self.store, self.members)

    def test_pointer_cannot_select_another_directory(self) -> None:
        (self.store / "current").write_text("../outside\n")
        with self.assertRaisesRegex(ValueError, "pointer"):
            verify_projection(self.store, self.members)

    def test_extra_projected_content_cannot_pass(self) -> None:
        (self.generation / "output.json").write_bytes(b"{}")
        (self.generation / "unexpected.json").write_bytes(b"{}")
        with self.assertRaisesRegex(ValueError, "unrelated"):
            verify_projection(self.store, self.members)
