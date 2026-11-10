"""Exercise the updater's publication boundary without external services."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import contracts_update
from scripts.contracts_shadow import LOCK, SCHEMA
from scripts.kubernetes_benchmark_contract import ROOT, SOURCE


class ContractUpdateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / LOCK.parent).mkdir()
        (self.root / LOCK).write_bytes((ROOT / LOCK).read_bytes())
        self.content = (ROOT / SCHEMA).read_bytes()
        (self.root / SCHEMA).write_bytes(b"old bytes")

    @patch("scripts.contracts_update.project")
    def test_preview_then_apply_changes_only_the_snapshot(self, project) -> None:
        project.return_value = self.content
        result = contracts_update.update(self.root, self.root, execute=False, root=self.root)
        self.assertEqual(result["status"], "preview")
        self.assertEqual((self.root / SCHEMA).read_bytes(), b"old bytes")
        lock = (self.root / LOCK).read_bytes()
        result = contracts_update.update(self.root, self.root, execute=True, root=self.root)
        self.assertEqual(result["status"], "applied")
        self.assertEqual((self.root / SCHEMA).read_bytes(), self.content)
        self.assertEqual((self.root / LOCK).read_bytes(), lock)
        self.assertEqual(
            contracts_update.update(self.root, self.root, execute=True, root=self.root)["status"],
            "unchanged",
        )

    @patch("scripts.contracts_update.project", return_value=b"unverified")
    def test_bad_projected_bytes_leave_existing_snapshot_in_place(self, _project) -> None:
        with self.assertRaisesRegex(ValueError, "checksum"):
            contracts_update.update(self.root, self.root, execute=True, root=self.root)
        self.assertEqual((self.root / SCHEMA).read_bytes(), b"old bytes")

    @patch("scripts.contracts_update.project", side_effect=ValueError("wrong source"))
    def test_bad_source_fails_without_falling_back(self, _project) -> None:
        with self.assertRaisesRegex(ValueError, "wrong source"):
            contracts_update.update(self.root, self.root, execute=True, root=self.root)
        self.assertEqual((self.root / SCHEMA).read_bytes(), b"old bytes")

    @patch("scripts.contracts_update.project")
    def test_bad_runtime_lock_fails_before_projection(self, project) -> None:
        lock = json.loads((self.root / LOCK).read_bytes())
        lock["revision"] = "0" * 40
        (self.root / LOCK).write_text(json.dumps(lock), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "lock"):
            contracts_update.update(self.root, self.root, execute=True, root=self.root)
        project.assert_not_called()

    @patch("scripts.contracts_update.restore")
    def test_bad_distribution_pin_fails_before_restoring_tool(self, restore) -> None:
        history = self.root / "history.json"
        lock = json.loads(contracts_update.HISTORY_LOCK.read_bytes())
        for key, value in (("sourceRevision", "0" * 40), ("sha256", "0" * 64)):
            with self.subTest(key=key):
                selection = json.loads(json.dumps(lock["selection"]))
                target = selection if key == "sourceRevision" else selection["members"][SOURCE]
                target[key] = value
                history.write_text(json.dumps({"selection": selection}), encoding="utf-8")
                with (
                    patch("scripts.contracts_update.HISTORY_LOCK", history),
                    self.assertRaisesRegex(ValueError, "selection"),
                ):
                    contracts_update.project(self.root, self.root)
        restore.assert_not_called()
