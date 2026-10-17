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
        script = self.templates["task-selection"]["script"]["source"]
        lock_digest = "sha256:866280f8538e86c4f02cc437ff9ef293cf9e14d8b1c9930129bc7f57bfef0f5f"
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
                    },
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=30,
                )
                self.assertEqual(result.returncode, expected)

    def test_jobs_use_existing_secrets_and_retain_failure_evidence(self) -> None:
        jobs = {
            name: self.templates[name]["resource"]
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
