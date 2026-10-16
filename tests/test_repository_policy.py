"""Focused tests for repository policy validation."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts import repository_policy


class RepositoryPolicyTests(unittest.TestCase):
    def test_pr_metadata_requires_one_issue_identity(self) -> None:
        self.assertEqual(
            repository_policy.check_pull_request_metadata(
                "[#42] Improve pull request metadata checks",
                "Closes #42\n\nVerified with make verify.",
                "feature/issue-42",
            ),
            [],
        )
        for title, body, branch, expected in (
            (
                "Add PR validation",
                "Closes #42\n\nDetails",
                "feature/issue-42",
                "PR title must start",
            ),
            (
                "[#42] Add PR validation",
                "Summary first\nCloses #42",
                "feature/issue-42",
                "PR body must begin",
            ),
            (
                "[#42] Add PR validation",
                "Closes #43\n\nDetails",
                "feature/issue-42",
                "must reference the same issue",
            ),
            (
                "[#42] Add PR validation",
                "Closes #42\n\nDetails",
                "feature/issue-43",
                "must reference the same issue",
            ),
        ):
            with self.subTest(title=title, body=body, branch=branch):
                errors = repository_policy.check_pull_request_metadata(title, body, branch)
                self.assertTrue(any(expected in error for error in errors))

    def test_pr_event_rejects_mismatch_without_disclosing_body(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.json"
            path.write_text(
                json.dumps(
                    {
                        "pull_request": {
                            "title": "[#42] Check metadata",
                            "body": "Closes #999\nsecret-looking text",
                            "head": {"ref": "feature/issue-42"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            errors = repository_policy.check_pull_request_event(path)
            self.assertTrue(errors)
            self.assertNotIn("secret-looking text", " ".join(errors))
            path.write_text('{"pull_request": {"title": "[#42] Check metadata"}}', encoding="utf-8")
            self.assertIn(
                "branch metadata is missing",
                " ".join(repository_policy.check_pull_request_event(path)),
            )

    def test_pr_event_preserves_dependabot_update_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "event.json"
            path.write_text(
                json.dumps(
                    {
                        "pull_request": {
                            "user": {"login": "dependabot[bot]"},
                            "title": "deps(actions): bump checkout",
                            "body": "Automated dependency update",
                            "head": {"ref": "dependabot/github_actions/checkout-7"},
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(repository_policy.check_pull_request_event(path), [])

    def write_templates(self, root: Path) -> None:
        """Provide only local forms; policy checks must never need gh or network access."""
        issue = root / ".github/ISSUE_TEMPLATE/work-item.md"
        issue.parent.mkdir(parents=True)
        issue.write_text(
            "**Milestone:** Existing GitHub title\n\n"
            + "\n\n".join(
                f"## {heading}\n\nDetails." for heading in repository_policy.ISSUE_HEADINGS
            ),
            encoding="utf-8",
        )
        pr = root / ".github/PULL_REQUEST_TEMPLATE.md"
        pr.write_text(
            "Closes #<issue-number>\n\n<!-- PR title: [#<issue-number>] Summary -->\n\n- Milestone: Existing GitHub title\n\n"
            + "\n\n".join(
                f"## {heading}\n\nDetails." for heading in repository_policy.PULL_REQUEST_HEADINGS
            )
            + "\n\n- [ ] Human review and merge: wait for the owner\n",
            encoding="utf-8",
        )
        (root / ".github/PR_FIX_COMMENT_TEMPLATE.md").write_text(
            "### Fix Applied\n\n**Issue:** Failed check.\n\n**Resolution:** Scoped correction.\n\n**Verification:** Passed focused tests.\n",
            encoding="utf-8",
        )

    def test_workflow_templates_are_checked_offline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            self.assertEqual(repository_policy.check_repository(root), [])

    def test_pr_template_requires_closing_reference_title_prefix_and_review_pause(self) -> None:
        for old, new in (
            ("Closes #<issue-number>\n", ""),
            ("[#<issue-number>]", "Summary"),
            ("- [ ] Human review and merge:", "- [ ] Ready:"),
        ):
            with self.subTest(missing=old), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.write_templates(root)
                path = root / ".github/PULL_REQUEST_TEMPLATE.md"
                path.write_text(
                    path.read_text(encoding="utf-8").replace(old, new), encoding="utf-8"
                )
                violations = repository_policy.check_repository(root)
                self.assertEqual(
                    [value.rule for value in violations], ["template.pull-request.workflow"]
                )

    def test_fix_comment_requires_heading_and_all_structured_fields(self) -> None:
        for missing in ("### Fix Applied", "**Issue:**", "**Resolution:**", "**Verification:**"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                self.write_templates(root)
                path = root / ".github/PR_FIX_COMMENT_TEMPLATE.md"
                path.write_text(
                    path.read_text(encoding="utf-8").replace(missing, "Missing"), encoding="utf-8"
                )
                self.assertEqual(
                    [value.rule for value in repository_policy.check_repository(root)],
                    ["template.fix-comment"],
                )

    def test_missing_fix_comment_template_has_actionable_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write_templates(root)
            (root / ".github/PR_FIX_COMMENT_TEMPLATE.md").unlink()
            violations = repository_policy.check_repository(root)
            self.assertEqual(len(violations), 1)
            self.assertEqual(violations[0].rule, "template.fix-comment")
            self.assertIn("Issue, Resolution and Verification", violations[0].fix)

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
