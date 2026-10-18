"""Offline contract checks for the staged external-state preparation workflow."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"


class ReleaseExternalStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load((DIRECTORY / "release-external-state.yaml").read_text())
        cls.templates = {item["name"]: item for item in cls.workflow["spec"]["templates"]}
        cls.children = {
            name: yaml.safe_load((DIRECTORY / filename).read_text())
            for name, filename in (
                ("task-selection", "release-task-selection.yaml"),
                ("bucket", "prepare-bucket.yaml"),
                ("database-roles", "prepare-database-roles.yaml"),
                ("execution-registry", "prepare-execution-registry.yaml"),
            )
        }
        cls.rbac = list(yaml.safe_load_all((DIRECTORY / "external-state-rbac.yaml").read_text()))

    def test_preflight_precedes_all_mutation_and_selection_is_bound(self) -> None:
        spec = self.workflow["spec"]
        self.assertEqual(spec["serviceAccountName"], "release-external-state")
        self.assertEqual(
            spec["synchronization"]["mutexes"], [{"name": "perfeng-local-external-state"}]
        )
        steps = [group[0] for group in self.templates["prepare"]["steps"]]
        self.assertEqual(
            [step["name"] for step in steps],
            ["preflight", "task-selection", "bucket", "database-roles", "execution-registry"],
        )
        self.assertEqual(
            steps[0]["templateRef"],
            {"name": "perfeng-release-preflight-v1", "template": "preflight"},
        )
        self.assertEqual(set(self.templates), {"prepare"})
        self.assertEqual(
            {item["name"] for item in self.templates["prepare"]["outputs"]["parameters"]},
            {"preflight-result", "bucket-job", "database-job", "registry-job"},
        )
        for step in steps[1:]:
            child = self.children[step["name"]]
            self.assertEqual(
                step["templateRef"],
                {"name": child["metadata"]["name"], "template": "run"},
            )
            self.assertEqual(child["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"], "-1")
        self.assertEqual(
            self.workflow["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"], "1"
        )
        script = self.children["task-selection"]["spec"]["templates"][0]["script"]["source"]
        lock_digest = "sha256:866280f8538e86c4f02cc437ff9ef293cf9e14d8b1c9930129bc7f57bfef0f5f"
        selection_args = {
            item["name"]: item["value"] for item in steps[1]["arguments"]["parameters"]
        }
        self.assertEqual(selection_args["expected-environment"], "perfeng-local")
        self.assertEqual(
            selection_args["expected-lock-path"], "deployments/local-control-plane.json"
        )
        self.assertEqual(selection_args["expected-lock-digest"], lock_digest)
        for environment, digest, expected in (
            ("perfeng-local", lock_digest, 0),
            ("other", lock_digest, 1),
            ("perfeng-local", "sha256:" + "0" * 64, 1),
        ):
            with self.subTest(environment=environment, digest=digest):
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    env={
                        **os.environ,
                        "PREFLIGHT_RESULT": json.dumps(
                            {
                                "environment": environment,
                                "lockDigests": {"deployments/local-control-plane.json": digest},
                            }
                        ),
                        "EXPECTED_ENVIRONMENT": selection_args["expected-environment"],
                        "EXPECTED_LOCK_PATH": selection_args["expected-lock-path"],
                        "EXPECTED_LOCK_DIGEST": selection_args["expected-lock-digest"],
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, expected)

    def test_jobs_use_existing_secrets_and_retain_failure_evidence(self) -> None:
        jobs = {
            name: self.children[name]["spec"]["templates"][0]["resource"]
            for name in ("bucket", "database-roles", "execution-registry")
        }
        for resource in jobs.values():
            self.assertEqual(resource["action"], "create")
            self.assertEqual(resource["successCondition"], "status.succeeded > 0")
            self.assertEqual(resource["failureCondition"], "status.failed > 0")
            manifest = yaml.safe_load(resource["manifest"])
            self.assertEqual(manifest["kind"], "Job")
            self.assertEqual(manifest["spec"]["backoffLimit"], 0)
            self.assertNotIn("ttlSecondsAfterFinished", manifest["spec"])
            self.assertFalse(manifest["spec"]["template"]["spec"]["automountServiceAccountToken"])
            self.assertIn("generateName", manifest["metadata"])
            for container in manifest["spec"]["template"]["spec"]["containers"]:
                self.assertIn("@sha256:", container["image"])
                self.assertEqual(container["resources"]["limits"]["memory"], "256Mi")
        bucket = yaml.safe_load(jobs["bucket"]["manifest"])
        database = yaml.safe_load(jobs["database-roles"]["manifest"])
        registry = yaml.safe_load(jobs["execution-registry"]["manifest"])
        self.assertEqual(bucket["metadata"]["namespace"], "perf-platform")
        self.assertEqual(database["metadata"]["namespace"], "perf-platform")
        self.assertEqual(registry["metadata"]["namespace"], "perf-generators")
        self.assertIn("perfeng-s3-auth", jobs["bucket"]["manifest"])
        self.assertIn("perfeng-control-plane-database", jobs["database-roles"]["manifest"])
        self.assertIn("perfeng-execution-registry", jobs["execution-registry"]["manifest"])
        self.assertEqual(len(registry["spec"]["template"]["spec"]["containers"]), 3)
        self.assertNotIn("kind: Secret", (DIRECTORY / "release-external-state.yaml").read_text())

    def test_script_configmap_is_versioned_immutable_and_mounted(self) -> None:
        kustomization = yaml.safe_load((DIRECTORY / "kustomization.yaml").read_text())
        self.assertEqual(
            [item["name"] for item in kustomization["configMapGenerator"]],
            ["perfeng-external-state-scripts-v1"],
        )
        generator = kustomization["configMapGenerator"][0]
        self.assertEqual(generator["namespace"], "perf-platform")
        self.assertTrue(generator["options"]["disableNameSuffixHash"])
        self.assertEqual(generator["options"]["annotations"]["argocd.argoproj.io/sync-wave"], "-2")
        self.assertIn("/immutable", kustomization["patches"][0]["patch"])
        for name in ("bucket", "database-roles"):
            script_file = name + ".sh"
            self.assertIn("scripts/" + script_file, generator["files"])
            script = (DIRECTORY / "scripts" / script_file).read_bytes()
            self.assertNotIn(b"\r", script)
            self.assertGreater(len(script), 100)
            resource = self.children[name]["spec"]["templates"][0]["resource"]
            job = yaml.safe_load(resource["manifest"])
            pod = job["spec"]["template"]["spec"]
            self.assertEqual(pod["volumes"][0]["configMap"]["name"], generator["name"])
            self.assertEqual(
                pod["containers"][0]["command"],
                ["/bin/sh", "-e", "/opt/perfeng-release/" + script_file],
            )
            self.assertEqual(
                pod["containers"][0]["volumeMounts"][0]["mountPath"], "/opt/perfeng-release"
            )
            self.assertNotIn("args", pod["containers"][0])

    def test_all_workflow_templates_are_linted_together(self) -> None:
        workflow_files = [
            "gitops/release-workflows/" + file.name
            for file in DIRECTORY.glob("*.yaml")
            if any(
                document.get("kind") == "WorkflowTemplate"
                for document in yaml.safe_load_all(file.read_text())
            )
        ]
        lint_lines = [
            line
            for line in (ROOT / "Makefile").read_text().splitlines()
            if line.startswith("\targo lint --offline")
        ]
        self.assertEqual(len(lint_lines), 1)
        for file in workflow_files:
            self.assertIn(file, lint_lines[0])

    @unittest.skipUnless(os.environ.get("GITHUB_BASE_REF"), "PR-only immutable child check")
    def test_published_child_and_script_versions_are_not_edited_in_place(self) -> None:
        base_ref = "origin/" + os.environ["GITHUB_BASE_REF"]
        base = subprocess.run(
            ["git", "merge-base", "HEAD", base_ref],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        versioned = [
            "gitops/release-workflows/release-preflight.yaml",
            *(
                "gitops/release-workflows/" + name
                for name in (
                    "release-task-selection.yaml",
                    "prepare-bucket.yaml",
                    "prepare-database-roles.yaml",
                    "prepare-execution-registry.yaml",
                    "scripts/bucket.sh",
                    "scripts/database-roles.sh",
                )
            ),
        ]
        for file in versioned:
            original = subprocess.run(
                ["git", "show", f"{base}:{file}"], cwd=ROOT, capture_output=True, check=False
            )
            if original.returncode:
                continue  # A new versioned object may be introduced in this PR.
            current = subprocess.run(
                ["git", "show", f"HEAD:{file}"], cwd=ROOT, capture_output=True, check=True
            )
            self.assertEqual(
                original.stdout, current.stdout, f"Versioned release object changed: {file}"
            )

    def test_rbac_is_namespaced_for_job_writes(self) -> None:
        self.assertEqual(
            [item["kind"] for item in self.rbac],
            [
                "ServiceAccount",
                "Role",
                "RoleBinding",
                "ClusterRole",
                "ClusterRoleBinding",
                "Role",
                "RoleBinding",
                "Role",
                "RoleBinding",
            ],
        )
        for role in (self.rbac[5], self.rbac[7]):
            self.assertIn(role["metadata"]["namespace"], {"perf-platform", "perf-generators"})
            self.assertEqual(
                role["rules"],
                [
                    {
                        "apiGroups": ["batch"],
                        "resources": ["jobs"],
                        "verbs": ["create", "get", "watch"],
                    }
                ],
            )
        self.assertEqual(self.rbac[3]["rules"][0]["resourceNames"], ["kube-system"])


if __name__ == "__main__":
    unittest.main()
