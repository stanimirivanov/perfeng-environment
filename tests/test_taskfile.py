import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class TaskfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.document = yaml.safe_load((ROOT / "Taskfile.yml").read_text(encoding="utf-8"))
        self.tasks = self.document["tasks"]

    def test_exposes_only_the_reviewed_operator_surface(self) -> None:
        self.assertEqual(self.document["version"], "3")
        self.assertEqual(
            set(self.tasks),
            {"default", "bootstrap", "status", "smoke", "destroy"},
        )

    def test_bootstrap_sequences_existing_safety_boundaries(self) -> None:
        self.assertEqual(
            self.tasks["bootstrap"]["cmds"],
            [
                "uv run --locked python scripts/cluster.py ensure --execute",
                "uv run --locked python scripts/cluster.py bootstrap --execute",
                "uv run --locked python scripts/postgres.py bootstrap --execute",
                "uv run --locked python scripts/object_store.py bootstrap --execute",
                "uv run --locked python scripts/control_plane_registry.py deploy --execute",
                "uv run --locked python scripts/gitops.py bootstrap --execute",
                "uv run --locked python scripts/object_store.py initialize --execute",
                "uv run --locked python scripts/storage_access.py --execute",
            ],
        )

    def test_status_is_read_only_and_smoke_requires_it(self) -> None:
        status = self.tasks["status"]["cmds"]
        self.assertTrue(
            all("--execute" in command or "workspace.py status" in command for command in status)
        )
        self.assertEqual(self.tasks["smoke"]["cmds"][0], {"task": "status"})
        self.assertIn("control_plane_smoke.py --execute", self.tasks["smoke"]["cmds"][1])

    def test_destroy_retains_prompt_and_exact_script_confirmation(self) -> None:
        destroy = self.tasks["destroy"]
        self.assertIn("permanently deletes", destroy["prompt"])
        self.assertEqual(
            destroy["cmds"],
            [
                "uv run --locked python scripts/cluster.py down --execute "
                "--confirm-delete perfeng-local"
            ],
        )


if __name__ == "__main__":
    unittest.main()
