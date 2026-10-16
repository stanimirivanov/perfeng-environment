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
            {
                "default",
                "bootstrap",
                "status",
                "smoke",
                "destroy",
                "native:api",
                "native:s3",
                "native:gateway",
                "native:check",
                "native:target",
                "native:target-health",
                "native:setup",
                "native:doctor",
                "native:permissions",
                "native:up",
                "kubernetes:qualify",
                "kubernetes:metrics-health",
                "kubernetes:prepare",
            },
        )

    def test_bootstrap_sequences_existing_safety_boundaries(self) -> None:
        self.assertEqual(
            self.tasks["bootstrap"]["cmds"],
            [
                "uv run --locked python scripts/infrastructure_runtime.py cluster ensure --execute",
                "uv run --locked python scripts/cluster.py bootstrap --execute",
                "uv run --locked python scripts/postgres.py bootstrap --execute",
                "uv run --locked python scripts/object_store.py bootstrap --execute",
                "uv run --locked python scripts/control_plane_registry.py deploy --execute",
                "uv run --locked python scripts/infrastructure_runtime.py argocd bootstrap --execute",
                "uv run --locked python scripts/gitops.py bootstrap --execute",
                "uv run --locked python scripts/object_store.py initialize --execute",
                "uv run --locked python scripts/storage_access.py --execute",
            ],
        )

    def test_kubernetes_qualification_is_explicit_and_not_an_aggregate_dependency(self) -> None:
        self.assertEqual(
            self.tasks["kubernetes:qualify"]["cmds"],
            ["uv run --locked python -m scripts.kubernetes_benchmark check --execute"],
        )
        for name in ("bootstrap", "status", "smoke"):
            self.assertNotIn("kubernetes:qualify", str(self.tasks[name]))

    def test_m04_preparation_pauses_before_migrations_and_does_not_activate(self) -> None:
        self.assertEqual(
            self.tasks["kubernetes:prepare"]["cmds"],
            [
                "uv run --locked python scripts/control_plane_release.py validate",
                "uv run --locked python scripts/control_plane_maintenance.py pause --execute",
                "uv run --locked python scripts/control_plane_registry.py deploy --execute",
                "uv run --locked python scripts/control_plane_database.py deploy --execute",
                "uv run --locked python scripts/control_plane_migration.py deploy --execute",
                "uv run --locked python scripts/control_plane_runtime.py deploy --execute",
                "uv run --locked python scripts/execution_registry.py deploy --execute",
            ],
        )
        for name in ("bootstrap", "status", "smoke"):
            self.assertNotIn("kubernetes:prepare", str(self.tasks[name]))

    def test_metrics_health_is_a_read_only_capability_not_certificate_setup(self) -> None:
        self.assertEqual(
            self.tasks["kubernetes:metrics-health"]["cmds"],
            ["uv run --locked python scripts/resource_metrics_health.py --execute"],
        )
        for name in ("bootstrap", "status", "smoke", "kubernetes:metrics-health"):
            for mutation in ("enable-serving", "serving-tls", "approve"):
                self.assertNotIn(mutation, str(self.tasks[name]))

    def test_status_is_diagnostic_and_smoke_requires_it(self) -> None:
        status = self.tasks["status"]["cmds"]
        self.assertEqual(
            status,
            [
                "uv run --locked python scripts/workspace.py status",
                "uv run --locked python scripts/infrastructure_runtime.py cluster health --execute",
                "uv run --locked python scripts/infrastructure_runtime.py argocd health --execute",
                "uv run --locked python scripts/gitops.py health --execute",
            ],
        )
        self.assertEqual(self.tasks["smoke"]["cmds"][0], {"task": "status"})
        self.assertIn("control_plane_smoke.py --execute", self.tasks["smoke"]["cmds"][1])

    def test_destroy_retains_prompt_and_exact_script_confirmation(self) -> None:
        destroy = self.tasks["destroy"]
        self.assertIn("permanently deletes", destroy["prompt"])
        self.assertEqual(
            destroy["cmds"],
            [
                "uv run --locked python scripts/infrastructure_runtime.py cluster down --execute "
                "--confirm-delete perfeng-local"
            ],
        )

    def test_native_connectivity_remains_opt_in_and_uses_existing_safety_boundary(self) -> None:
        for name, action in (
            ("api", "forward-api"),
            ("s3", "forward-s3"),
            ("gateway", "gateway"),
            ("target", "forward-target"),
        ):
            self.assertEqual(
                self.tasks[f"native:{name}"]["cmds"],
                [f"uv run --locked python scripts/native_connectivity.py {action} --execute"],
            )
        self.assertIn("--ca-file .local/native-gateway/", self.tasks["native:check"]["cmds"][0])
        self.assertEqual(
            self.tasks["native:target-health"]["cmds"],
            ["uv run --locked python scripts/native_target_health.py --execute"],
        )
        for name in ("bootstrap", "status", "smoke"):
            self.assertNotIn("native:", str(self.tasks[name]))

    def test_managed_native_workflow_has_explicit_execution_and_permission_review(self) -> None:
        for action in ("setup", "doctor", "permissions", "up"):
            self.assertEqual(
                self.tasks[f"native:{action}"]["cmds"],
                [f"uv run --locked python -m scripts.native_gateway {action} --execute"],
            )
        self.assertIn("Restrict .local/native-gateway", self.tasks["native:permissions"]["prompt"])


if __name__ == "__main__":
    unittest.main()
