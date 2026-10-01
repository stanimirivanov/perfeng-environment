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

    def test_reports_broken_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs/guide.md").write_text(
                "# Guide\n\n[missing](absent.md)\n", encoding="utf-8"
            )

            rules = {item.rule for item in repository_policy.check_repository(root)}

            self.assertIn("docs.link", rules)

    def test_accepts_existing_heading_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs/target.md").write_text("# Target\n\n## Exact heading\n", encoding="utf-8")
            (root / "docs/guide.md").write_text(
                "# Guide\n\n[target](target.md#exact-heading)\n", encoding="utf-8"
            )

            rules = {item.rule for item in repository_policy.check_repository(root)}

            self.assertNotIn("docs.link", rules)
            self.assertNotIn("docs.anchor", rules)

    def test_accepts_commit_pinned_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflow = root / ".github/workflows/validate.yml"
            workflow.parent.mkdir(parents=True)
            workflow.write_text(
                "jobs:\n  check:\n    steps:\n"
                "      - uses: owner/tool@0123456789abcdef0123456789abcdef01234567\n",
                encoding="utf-8",
            )

            self.assertEqual(
                [
                    item
                    for item in repository_policy.check_repository(root)
                    if item.path == ".github/workflows/validate.yml"
                ],
                [],
            )


if __name__ == "__main__":
    unittest.main()
