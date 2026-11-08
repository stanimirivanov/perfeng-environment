"""Exercise the independent verifier bootstrap boundary."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.contracts_distribution_tools import checked_bytes


class ContractDistributionToolsTests(unittest.TestCase):
    def test_missing_and_altered_bytes_fail_closed(self) -> None:
        expected = hashlib.sha256(b"reviewed verifier").hexdigest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "verifier"
            with self.assertRaisesRegex(ValueError, "unavailable"):
                checked_bytes(path, expected, limit=1024)
            path.write_bytes(b"untrusted verifier")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                checked_bytes(path, expected, limit=1024)
            path.write_bytes(b"reviewed verifier")
            self.assertEqual(checked_bytes(path, expected, limit=1024), b"reviewed verifier")
            with self.assertRaisesRegex(ValueError, "size"):
                checked_bytes(path, expected, limit=1)
