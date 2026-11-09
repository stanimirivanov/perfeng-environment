"""Test the consumer-owned boundary of the distribution shadow check."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.contracts_shadow import verify_parity
from scripts.kubernetes_benchmark_contract import ROOT

SCHEMA = "contracts/kubernetes-benchmark-evidence.schema.json"


class ContractDistributionTests(unittest.TestCase):
    def test_rejects_substitution_before_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "authoritative snapshot"):
            verify_parity(b"substituted schema", b"{}")

    def test_uses_consumer_lock_and_requires_both_expected_outcomes(self) -> None:
        with patch("scripts.contracts_shadow.validate") as validate:

            def check(evidence: dict, root: Path) -> None:
                lock = "contracts/kubernetes-benchmark-lock.json"
                self.assertEqual((root / lock).read_bytes(), (ROOT / lock).read_bytes())
                self.assertEqual((root / SCHEMA).read_bytes(), (ROOT / SCHEMA).read_bytes())
                if evidence.get("scope") is None:
                    raise ValueError("invalid evidence")

            validate.side_effect = check
            verify_parity((ROOT / SCHEMA).read_bytes(), b'{"scope":{}}')
            self.assertEqual(validate.call_count, 4)
            validate.side_effect = None
            with self.assertRaisesRegex(ValueError, "accepted invalid"):
                verify_parity((ROOT / SCHEMA).read_bytes(), b'{"scope":{}}')

    def test_invalid_positive_fixture_fails(self) -> None:
        with self.assertRaisesRegex(ValueError, "violates the pinned contract"):
            verify_parity((ROOT / SCHEMA).read_bytes(), b"{}")

    def test_missing_assets_fail_without_changing_consumer_inputs(self) -> None:
        from scripts.contracts_shadow import shadow

        baseline = (ROOT / SCHEMA).read_bytes()
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            shadow(Path(tmp), Path(tmp) / "gh")
        self.assertEqual((ROOT / SCHEMA).read_bytes(), baseline)
