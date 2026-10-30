"""Prove grant-v2 wiring reuses admission policy without invoking schema migration."""

import json
import os
import unittest
from copy import deepcopy
from pathlib import Path

import test_schema_migration_admission as admission
import yaml

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"
SHARED = {"candidate", "pointer", "receipt", "accepted-bytes", "receipt-stable", "admission"}


class RuntimeGrantsAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.parent = yaml.safe_load((DIRECTORY / "release-runtime-grants-v2.yaml").read_text())
        self.legacy = yaml.safe_load((DIRECTORY / "release-runtime-grants-v1.yaml").read_text())
        self.schema = yaml.safe_load((DIRECTORY / "release-schema-migration-v2.yaml").read_text())
        self.steps = self.parent["spec"]["templates"][0]["steps"]

    def resolve(self, step):
        reference = step["templateRef"]
        owners = [
            obj
            for path in DIRECTORY.glob("*.yaml")
            if (obj := yaml.safe_load(path.read_text()))["kind"] == "WorkflowTemplate"
            and obj["metadata"]["name"] == reference["name"]
        ]
        self.assertEqual(len(owners), 1)
        templates = [
            t for t in owners[0]["spec"]["templates"] if t["name"] == reference["template"]
        ]
        self.assertEqual(len(templates), 1)
        return templates[0]

    def test_entire_admission_chain_and_arguments_match_published_schema_v2(self):
        expected = deepcopy(self.schema["spec"]["templates"][0]["steps"][:-1])
        for group in expected:
            step = group[0]
            if step["name"] in SHARED:
                step["templateRef"] = {
                    "name": self.schema["metadata"]["name"],
                    "template": step.pop("template"),
                }
        self.assertEqual(self.steps[:-1], expected)
        self.assertEqual(self.steps[-1], self.legacy["spec"]["templates"][0]["steps"][-1])
        for group in self.steps:
            self.assertEqual(len(group), 1)
            for bypass in ("when", "continueOn", "withItems", "withParam", "inline"):
                self.assertNotIn(bypass, group[0])

    def test_same_identity_inputs_mutex_and_output_contract_without_new_policy(self):
        spec, legacy = self.parent["spec"], self.legacy["spec"]
        for key in ("serviceAccountName", "entrypoint", "synchronization", "arguments"):
            self.assertEqual(spec[key], legacy[key])
        self.assertEqual(spec["activeDeadlineSeconds"], 1800)
        self.assertEqual(
            set(spec),
            {
                "serviceAccountName",
                "entrypoint",
                "activeDeadlineSeconds",
                "synchronization",
                "arguments",
                "templates",
            },
        )
        self.assertEqual(len(spec["templates"]), 1)
        parent = spec["templates"][0]
        self.assertEqual(set(parent), {"name", "steps", "outputs"})
        self.assertEqual(parent["outputs"], legacy["templates"][0]["outputs"])
        for key in ("retryStrategy", "hooks", "onExit"):
            self.assertNotIn(key, json.dumps(self.parent))

    def test_shared_leaves_cannot_switch_identity_acquire_mutex_or_invoke_migration(self):
        for group in self.steps:
            step = group[0]
            target = self.resolve(step)
            supplied = {p["name"] for p in step.get("arguments", {}).get("parameters", [])}
            required = {
                p["name"]
                for p in target.get("inputs", {}).get("parameters", [])
                if "default" not in p
            }
            self.assertEqual(supplied, required)
            if step["name"] not in SHARED:
                continue
            self.assertEqual(step["templateRef"]["template"], step["name"])
            self.assertNotIn("steps", target)
            self.assertNotIn("dag", target)
            self.assertNotIn("serviceAccountName", target)
            self.assertNotIn("synchronization", target)
            self.assertNotIn("serviceAccountName", target.get("podSpecPatch", ""))
            if "resource" in target:
                self.assertEqual(target["resource"]["action"], "get")
        self.assertEqual(
            self.steps[-1][0]["templateRef"],
            {"name": "perfeng-runtime-grants-job-v1", "template": "run"},
        )
        self.assertFalse(
            any(group[0]["templateRef"].get("template") == "migrate" for group in self.steps)
        )

    def test_standalone_authority_is_only_get_and_requires_explicit_installation(self):
        bootstrap = ROOT / "gitops/release-workflows-bootstrap"
        path = bootstrap / "runtime-grants-ledger-access.yaml"
        role, binding = list(yaml.safe_load_all(path.read_text()))
        self.assertEqual(
            role["rules"], [{"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get"]}]
        )
        for obj in (role, binding):
            self.assertEqual(
                obj["metadata"],
                {"name": "release-runtime-grants", "namespace": "perfeng-release-ledger"},
            )
        self.assertEqual(
            binding["roleRef"],
            {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": "release-runtime-grants",
            },
        )
        self.assertEqual(
            binding["subjects"],
            [
                {
                    "kind": "ServiceAccount",
                    "name": "release-runtime-grants",
                    "namespace": "argo-workflows",
                }
            ],
        )
        self.assertNotIn(path.name, (bootstrap / "kustomization.yaml").read_text())
        self.assertNotIn(
            "release-workflows", (ROOT / "gitops/environments/local/kustomization.yaml").read_text()
        )
        selected = yaml.safe_load((DIRECTORY / "kustomization.yaml").read_text())["resources"]
        self.assertIn("release-runtime-grants-v1.yaml", selected)
        self.assertIn("release-runtime-grants-v2.yaml", selected)

    @unittest.skipIf(
        os.name == "nt" and not admission.JQ, "jq unavailable on Windows; required on Linux CI"
    )
    def test_actual_referenced_policy_accepts_baseline_and_refuses_conflicting_records(self):
        fixture = admission.SchemaAdmissionTests()
        fixture.setUp()
        fixture.templates = {
            group[0]["name"]: self.resolve(group[0])
            for group in self.steps
            if group[0]["name"] in SHARED
        }
        self.assertEqual(fixture.replay("pointer", fixture.pointer), fixture.pointer)
        self.assertEqual(fixture.replay("receipt", fixture.receipt), fixture.receipt)
        self.assertEqual(
            fixture.replay("admission", fixture.pointer), {"status": "retained-baseline-verified"}
        )
        for field in ("requestDigest", "receiptUID", "clusterUID"):
            wrong = deepcopy(fixture.pointer)
            wrong["data"][field] = "conflict"
            target = "pointer" if field != "receiptUID" else "admission"
            with self.subTest(field=field), self.assertRaises(ValueError):
                fixture.replay(target, wrong)
        replaced = deepcopy(fixture.receipt)
        replaced["metadata"]["uid"] = "replacement"
        with self.assertRaises(ValueError):
            fixture.replay("receipt", replaced)
        with self.assertRaises(ValueError):
            fixture.replay("receipt-stable", replaced)
        with self.assertRaises(ValueError):
            fixture.replay(
                "admission",
                fixture.pointer,
                checkpoint={**fixture.checkpoint, "clusterUID": "foreign"},
            )


if __name__ == "__main__":
    unittest.main()
