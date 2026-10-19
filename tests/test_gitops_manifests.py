"""Offline rendering contracts for GitOps ownership and staged release permissions."""

import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from scripts import validate_cluster

ROOT = Path(__file__).resolve().parents[1]


class GitOpsManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        bootstrap = ROOT / "gitops/release-workflows-bootstrap"
        self.project = yaml.safe_load((bootstrap / "project.yaml").read_text(encoding="utf-8"))
        self.application = yaml.safe_load(
            (bootstrap / "application.yaml").read_text(encoding="utf-8")
        )
        self.configmap: dict[str, Any] = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "perfeng-external-state-scripts-v1",
                "namespace": "perf-platform",
            },
            "data": {"bucket.sh": "echo fixture\n"},
        }
        self.bucket = yaml.safe_load(
            (ROOT / "gitops/release-workflows/prepare-bucket.yaml").read_text(encoding="utf-8")
        )

    def test_release_project_allows_generated_script_configmap(self) -> None:
        validate_cluster.validate_release_project(
            self.project, self.application, [self.configmap, self.bucket]
        )

    def test_missing_configmap_permission_fails_before_sync(self) -> None:
        self.project["spec"]["namespaceResourceWhitelist"] = [
            item
            for item in self.project["spec"]["namespaceResourceWhitelist"]
            if item["kind"] != "ConfigMap"
        ]
        with self.assertRaisesRegex(ValueError, "does not allow.*ConfigMap"):
            validate_cluster.validate_release_project(
                self.project, self.application, [self.configmap, self.bucket]
            )

    def test_unallowed_kind_and_destination_fail_before_sync(self) -> None:
        for field, value, error in (
            ("kind", "Secret", "Secret"),
            ("namespace", "foreign", "destination.*foreign"),
        ):
            changed = deepcopy(self.configmap)
            if field == "kind":
                changed[field] = value
            else:
                changed["metadata"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, error):
                validate_cluster.validate_release_project(self.project, self.application, [changed])

    def test_unallowed_cluster_kind_and_mismatched_project_are_rejected(self) -> None:
        cluster_object = {
            "apiVersion": "v1",
            "kind": "Namespace",
            "metadata": {"name": "foreign"},
        }
        with self.assertRaisesRegex(ValueError, "does not allow.*Namespace"):
            validate_cluster.validate_release_project(
                self.project, self.application, [cluster_object]
            )
        self.application["spec"]["project"] = "default"
        with self.assertRaisesRegex(ValueError, "Project identity"):
            validate_cluster.validate_release_project(self.project, self.application, [])

    def test_script_configmap_must_exist_in_job_namespace_with_required_key(self) -> None:
        for mutation in ("missing", "namespace", "key"):
            configmap = deepcopy(self.configmap)
            if mutation == "namespace":
                configmap["metadata"]["namespace"] = "argo-workflows"
            elif mutation == "key":
                configmap["data"] = {}
            objects = [self.bucket] if mutation == "missing" else [configmap, self.bucket]
            with (
                self.subTest(mutation=mutation),
                self.assertRaisesRegex(ValueError, "Job ConfigMap"),
            ):
                validate_cluster.validate_release_project(self.project, self.application, objects)

    def test_duplicate_identity_ignores_api_version_but_respects_namespace(self) -> None:
        other_version = deepcopy(self.configmap)
        other_version["apiVersion"] = "v2"
        with self.assertRaisesRegex(ValueError, "Duplicate GitOps object"):
            validate_cluster.manifest_inventory([self.configmap, other_version])
        other_namespace = deepcopy(self.configmap)
        other_namespace["metadata"]["namespace"] = "argo-workflows"
        self.assertEqual(
            len(validate_cluster.manifest_inventory([self.configmap, other_namespace])), 2
        )

    def test_verifier_template_requires_its_own_namespaced_configmap_key(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / "gitops/release-workflows/verify-release-readiness-v2.yaml").read_text()
        )
        configmap = deepcopy(self.configmap)
        configmap["metadata"].update(
            name="perfeng-release-verifiers-v2", namespace="argo-workflows"
        )
        with self.assertRaisesRegex(ValueError, "Workflow ConfigMap"):
            validate_cluster.validate_release_project(
                self.project, self.application, [workflow, configmap]
            )
        configmap["data"] = {
            "release_policy_v2.py": "# fixture",
            "release_runtime_v2.py": "# fixture",
        }
        validate_cluster.validate_release_project(
            self.project, self.application, [workflow, configmap]
        )

    def test_secret_objects_and_malformed_yaml_are_rejected(self) -> None:
        for obj, error in (
            ({**self.configmap, "kind": "Secret"}, "Secret"),
            ({"kind": "ConfigMap"}, "Invalid GitOps object"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(ValueError, error):
                validate_cluster.manifest_inventory([obj])

    def test_native_ledger_creates_are_limited_to_the_reviewed_configmap_shape(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / "gitops/release-workflows/adoption-records.yaml").read_text()
        )
        validate_cluster.validate_release_project(self.project, self.application, [workflow])
        for change in ("namespace", "immutable", "owner", "data"):
            changed = deepcopy(workflow)
            resource = changed["spec"]["templates"][3]["resource"]
            obj = yaml.safe_load(resource["manifest"])
            if change == "namespace":
                obj["metadata"]["namespace"] = "argo-workflows"
            elif change == "immutable":
                obj["immutable"] = False
            elif change == "owner":
                resource["setOwnerReference"] = True
            else:
                obj["binaryData"]["unexpected"] = "value"
            resource["manifest"] = yaml.safe_dump(obj)
            with (
                self.subTest(change=change),
                self.assertRaisesRegex(ValueError, "ledger ConfigMap"),
            ):
                validate_cluster.validate_release_project(self.project, self.application, [changed])


if __name__ == "__main__":
    unittest.main()
