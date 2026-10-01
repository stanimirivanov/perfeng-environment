"""Focused tests for repository policy validation."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts import repository_policy


class RepositoryPolicyTests(unittest.TestCase):
    def test_current_repository_conforms(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(repository_policy.check_repository(root), [])

    def test_reports_broken_link_and_non_major_action(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / ".github/workflows").mkdir(parents=True)
            (root / "docs/guide.md").write_text(
                "# Guide\n\n[missing](absent.md)\n", encoding="utf-8"
            )
            (root / ".github/workflows/validate.yml").write_text(
                "jobs:\n  check:\n    steps:\n      - uses: owner/tool@0123456789abcdef\n",
                encoding="utf-8",
            )

            rules = {item.rule for item in repository_policy.check_repository(root)}

            self.assertIn("docs.link", rules)
            self.assertIn("workflow.action-version", rules)

    def test_accepts_existing_heading_anchor_and_semantic_major(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / ".github/workflows").mkdir(parents=True)
            (root / "docs/target.md").write_text("# Target\n\n## Exact heading\n", encoding="utf-8")
            (root / "docs/guide.md").write_text(
                "# Guide\n\n[target](target.md#exact-heading)\n", encoding="utf-8"
            )
            (root / ".github/workflows/validate.yml").write_text(
                "jobs:\n  check:\n    steps:\n      - uses: owner/tool@v9\n",
                encoding="utf-8",
            )

            rules = {item.rule for item in repository_policy.check_repository(root)}

            self.assertNotIn("docs.link", rules)
            self.assertNotIn("docs.anchor", rules)
            self.assertNotIn("workflow.action-version", rules)

    def test_action_policy_handles_quotes_blocks_and_composites(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github/workflows/validate.yml"
            composite = root / ".github/actions/example/action.yml"
            workflow.parent.mkdir(parents=True)
            composite.parent.mkdir(parents=True)
            workflow.write_text(
                'steps:\n  - uses: "owner/tool@v9" # valid\n'
                "  - run: |\n      echo 'uses: owner/not-an-action@abcdef'\n",
                encoding="utf-8",
            )
            composite.write_text(
                "runs:\n  using: composite\n  steps:\n    - uses: owner/tool@abcdef\n",
                encoding="utf-8",
            )

            violations = [
                item
                for item in repository_policy.check_repository(root)
                if item.rule == "workflow.action-version"
            ]

            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].path, ".github/actions/example/action.yml")


if __name__ == "__main__":
    unittest.main()
