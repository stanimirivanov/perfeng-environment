"""Offline composition/authority gates; not an Argo or SQL execution simulator."""

import json
import unittest
from pathlib import Path

import yaml

from scripts import validate_cluster

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"
PHASES = (
    ("migrate", "release-schema-migration-v2.yaml"),
    ("qualify", "release-schema-qualification-v1.yaml"),
    ("grant", "release-runtime-grants-v2.yaml"),
)


def manifest(name):
    return yaml.safe_load((DIRECTORY / name).read_text())


class DatabasePreparationTests(unittest.TestCase):
    def setUp(self):
        self.parent = manifest("release-database-prepare-v1.yaml")
        self.spec = self.parent["spec"]
        self.flow = self.spec["templates"][0]

    def test_serial_fail_closed_composition_has_no_phase_bypass_or_new_execution(self):
        self.assertEqual(self.parent["metadata"]["name"], "perfeng-release-database-prepare-v1")
        self.assertEqual(self.parent["metadata"]["namespace"], "argo-workflows")
        self.assertEqual(self.spec["entrypoint"], "prepare")
        self.assertEqual(len(self.spec["templates"]), 1)
        self.assertEqual(set(self.flow), {"name", "steps", "outputs"})
        self.assertEqual(self.flow["name"], "prepare")
        # Singleton sequential groups and exact refs use Argo's default stop-on-failure.
        # No fake scheduler substitutes for separately approved controller acceptance.
        self.assertEqual(
            self.flow["steps"],
            [
                [
                    {
                        "name": phase,
                        "templateRef": {
                            "name": manifest(path)["metadata"]["name"],
                            "template": phase,
                        },
                    }
                ]
                for phase, path in PHASES
            ],
        )
        self.assertEqual(
            set(self.spec),
            {
                "serviceAccountName",
                "entrypoint",
                "activeDeadlineSeconds",
                "synchronization",
                "arguments",
                "templates",
            },
        )
        for forbidden in (
            "continueOn",
            "when",
            "retryStrategy",
            "hooks",
            "onExit",
            "resource",
            "container",
            "script",
        ):
            self.assertNotIn(forbidden, json.dumps(self.parent))

    def test_one_caller_mutex_deadline_and_identical_pinned_inputs(self):
        self.assertEqual(self.spec["serviceAccountName"], "release-runtime-grants")
        self.assertEqual(self.spec["activeDeadlineSeconds"], 1800)
        self.assertEqual(
            self.spec["synchronization"], {"mutexes": [{"name": "perfeng-local-external-state"}]}
        )
        self.assertEqual(
            self.spec["arguments"],
            {
                "parameters": [
                    {"name": name}
                    for name in (
                        "request-revision",
                        "request-path",
                        "request-sha256",
                        "selection-revision",
                    )
                ]
            },
        )
        for phase, path in PHASES:
            child = manifest(path)["spec"]
            self.assertEqual(child["entrypoint"], phase)
            self.assertEqual(child["arguments"], self.spec["arguments"])
            self.assertEqual(child["synchronization"], self.spec["synchronization"])
            flow = next(t for t in child["templates"] if t["name"] == phase)
            self.assertFalse(flow.get("inputs"))
            self.assertEqual(
                [group[0]["name"] for group in flow["steps"]],
                [
                    "preflight",
                    "task-selection",
                    "migration-intent",
                    "candidate",
                    "pointer",
                    "receipt",
                    "accepted-bytes",
                    "quiescence",
                    "receipt-stable",
                    "admission",
                    phase,
                ],
            )
            for group in flow["steps"]:
                self.assertEqual(len(group), 1)
                self.assertFalse(
                    set(group[0]) & {"when", "continueOn", "hooks", "withItems", "withParam"}
                )
            self.assertEqual(
                flow["steps"][-2][0]
                .get("templateRef", {})
                .get("template", flow["steps"][-2][0].get("template")),
                "admission",
            )

    def test_outputs_require_all_phases_and_job_names_are_distinct_in_one_workflow(self):
        expected = [
            {
                "name": name,
                "valueFrom": {
                    "parameter": "{{steps." + phase + ".outputs.parameters." + output + "}}"
                },
            }
            for name, phase, output in (
                ("migration-job-name", "migrate", "job-name"),
                ("qualification-job-name", "qualify", "job-name"),
                ("grants-job-name", "grant", "job-name"),
                ("checkpoint", "grant", "checkpoint"),
            )
        ]
        self.assertEqual(self.flow["outputs"], {"parameters": expected})
        names = []
        for phase, path in PHASES:
            flow = next(t for t in manifest(path)["spec"]["templates"] if t["name"] == phase)
            reference = flow["steps"][-1][0]["templateRef"]
            leaf = manifest(reference["name"].removeprefix("perfeng-") + ".yaml")
            native = next(
                t for t in leaf["spec"]["templates"] if t["name"] == reference["template"]
            )
            self.assertEqual(native["resource"]["action"], "create")
            self.assertFalse(native["resource"].get("setOwnerReference", False))
            job = yaml.safe_load(native["resource"]["manifest"])
            self.assertEqual(job["kind"], "Job")
            self.assertEqual(job["metadata"]["namespace"], "perf-platform")
            name = job["metadata"]["name"]
            self.assertIn("{{workflow.uid}}", name)
            self.assertLessEqual(len(name.replace("{{workflow.uid}}", "0" * 36)), 63)
            names.append(name)
        self.assertEqual(len(set(names)), 3)

    def test_existing_caller_authority_covers_migration_without_new_rbac(self):
        for name in ("platform-observation.yaml", "workflow-access.yaml", "cluster-identity.yaml"):
            grants = list(
                yaml.safe_load_all((DIRECTORY / "rbac/runtime-grants" / name).read_text())
            )
            migration = list(
                yaml.safe_load_all((DIRECTORY / "rbac/schema-migration" / name).read_text())
            )
            self.assertEqual(
                [o["rules"] for o in grants if "rules" in o],
                [o["rules"] for o in migration if "rules" in o],
            )
        ledger = list(
            yaml.safe_load_all(
                (
                    ROOT / "gitops/release-workflows-bootstrap/runtime-grants-ledger-access.yaml"
                ).read_text()
            )
        )
        self.assertEqual(
            ledger[0]["rules"], [{"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get"]}]
        )
        self.assertEqual(
            ledger[1]["subjects"],
            [
                {
                    "kind": "ServiceAccount",
                    "name": self.spec["serviceAccountName"],
                    "namespace": "argo-workflows",
                }
            ],
        )

    def test_staging_selection_and_project_allow_composition_without_activation(self):
        selection = yaml.safe_load((DIRECTORY / "kustomization.yaml").read_text())
        self.assertIn("release-database-prepare-v1.yaml", selection["resources"])
        bootstrap = ROOT / "gitops/release-workflows-bootstrap"
        validate_cluster.validate_release_project(
            yaml.safe_load((bootstrap / "project.yaml").read_text()),
            yaml.safe_load((bootstrap / "application.yaml").read_text()),
            [self.parent],
        )
        for directory in (
            ROOT / "gitops/environments/local",
            ROOT / ".github/workflows",
        ):
            for path in directory.rglob("*"):
                if path.suffix in {".yaml", ".yml", ".sh"}:
                    self.assertNotIn(
                        "perfeng-release-database-prepare-v1", path.read_text(), str(path)
                    )


if __name__ == "__main__":
    unittest.main()
