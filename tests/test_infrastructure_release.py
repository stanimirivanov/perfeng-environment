from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from scripts import infrastructure_lock, infrastructure_release


class InfrastructureReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_revision_reader_accepts_parent_relative_checkout_path(self) -> None:
        environment = self.root / "perfeng-environment"
        repository = self.root / "k8s-infrastructure"
        environment.mkdir()
        repository.mkdir()

        with patch(
            "scripts.infrastructure_release.git",
            side_effect=[infrastructure_lock.INFRASTRUCTURE_REPOSITORY.encode(), b"commit\n"],
        ) as git:
            read = infrastructure_release.revision_reader(
                environment / ".." / "k8s-infrastructure", "1" * 40
            )

        self.assertTrue(callable(read))
        self.assertEqual(git.call_args_list[0].args[0], repository.resolve())

    def test_ci_checkout_matches_the_infrastructure_lock(self) -> None:
        lock = infrastructure_lock.load_lock()
        workflow = yaml.safe_load(
            (infrastructure_release.ROOT / ".github/workflows/validate.yml").read_text(
                encoding="utf-8"
            )
        )
        steps = workflow["jobs"]["validate"]["steps"]
        checkouts = {step.get("name"): step.get("with") for step in steps if "name" in step}

        self.assertEqual(
            checkouts["Check out pinned Kubernetes infrastructure"],
            {
                "repository": "stanimirivanov/k8s-infrastructure",
                "ref": lock.revision,
                "token": "${{ secrets.PERFENG_REPOSITORIES_CONTENTS_READ_TOKEN }}",
                "path": "k8s-infrastructure",
                "persist-credentials": False,
            },
        )
        validation = next(
            step
            for step in steps
            if step.get("name") == "Validate immutable local infrastructure selection"
        )
        self.assertEqual(validation.get("if"), "runner.os == 'Linux'")
        self.assertIn("scripts/infrastructure_release.py validate", validation.get("run", ""))
        self.assertIn("../k8s-infrastructure", validation.get("run", ""))


if __name__ == "__main__":
    unittest.main()
