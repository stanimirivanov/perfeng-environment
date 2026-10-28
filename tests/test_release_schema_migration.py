"""Offline boundaries for the staged native migration parent and its authority."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import yaml
from jsonschema import Draft202012Validator
from test_release_workflows_integration import BASH

from scripts import validate_cluster
from tests.release_verifier_support import policy, runtime

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "gitops/release-workflows"


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        self.parent = yaml.safe_load((DIRECTORY / "release-schema-migration-v1.yaml").read_text())
        self.templates = {t["name"]: t for t in self.parent["spec"]["templates"]}
        self.leaf = yaml.safe_load((DIRECTORY / "schema-migration-job-v1.yaml").read_text())
        self.job = yaml.safe_load(self.leaf["spec"]["templates"][0]["resource"]["manifest"])
        self.configmap = yaml.safe_load((DIRECTORY / "schema-migration-intent-v1.yaml").read_text())
        self.policy = json.loads(self.configmap["data"]["schema-migration-intent-v1.schema.json"])

    def test_only_exact_reviewed_intent_allows_migration(self):
        validator = Draft202012Validator(self.policy)
        validator.check_schema(self.policy)
        request: dict[str, Any] = {
            "schemaVersion": 1,
            "promotion": {
                "activation": "manual",
                "migration": {"mode": "forward-only", "id": "0018"},
            },
        }
        validator.validate(request)
        for migration in (
            {"mode": "none"},
            {"mode": "forward-only", "id": "0017"},
            {"mode": "forward-only"},
            {"mode": "forward-only", "id": "0018", "command": "other"},
            None,
        ):
            bad = deepcopy(request)
            bad["promotion"]["migration"] = migration
            self.assertFalse(validator.is_valid(bad))
        for field, value in (("activation", "automated"), ("activation", None)):
            bad = deepcopy(request)
            bad["promotion"][field] = value
            self.assertFalse(validator.is_valid(bad))
        contract = json.loads(
            (ROOT / "tests/integration/release-workflows/migration-image.json").read_text()
        )
        self.assertEqual(
            max(contract["migrations"])[:4],
            self.policy["properties"]["promotion"]["properties"]["migration"]["properties"]["id"][
                "const"
            ],
        )

    def test_job_is_last_after_exact_selection_intent_and_fresh_quiescence(self):
        spec = self.parent["spec"]
        self.assertEqual(spec["serviceAccountName"], "release-schema-migration")
        self.assertEqual(
            spec["synchronization"], {"mutexes": [{"name": "perfeng-local-external-state"}]}
        )
        self.assertEqual(spec["activeDeadlineSeconds"], 1200)
        steps = self.templates["migrate"]["steps"]
        self.assertTrue(all(len(group) == 1 for group in steps))
        self.assertEqual(
            [g[0]["name"] for g in steps],
            ["preflight", "task-selection", "migration-intent", "quiescence", "migrate"],
        )
        refs = [g[0].get("templateRef") for g in steps]
        self.assertEqual(refs[0], {"name": "perfeng-release-preflight-v3", "template": "preflight"})
        self.assertEqual(
            refs[3], {"name": "perfeng-verify-release-quiescence-v3", "template": "run"}
        )
        self.assertEqual(
            refs[4],
            {
                "name": self.leaf["metadata"]["name"],
                "template": self.leaf["spec"]["templates"][0]["name"],
            },
        )
        parameters = {p["name"]: p["value"] for p in steps[1][0]["arguments"]["parameters"]}
        self.assertEqual(
            parameters["expected-lock-digest"],
            "sha256:"
            + hashlib.sha256(
                (ROOT / "deployments/local-control-plane.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(parameters["expected-environment"], "perfeng-local")
        self.assertEqual(parameters["expected-lock-path"], "deployments/local-control-plane.json")
        for group in steps:
            self.assertNotIn("continueOn", group[0])
            self.assertNotIn("when", group[0])
        serialized = json.dumps(self.parent)
        for forbidden in ("retryStrategy", "onExit", "hooks"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(len(spec["arguments"]["parameters"]), 4)

    def test_exact_lock_gate_refuses_other_environment_or_lock_before_quiescence(self):
        step = self.templates["migrate"]["steps"][1][0]
        expected = {p["name"]: p["value"] for p in step["arguments"]["parameters"]}
        valid = {
            "environment": expected["expected-environment"],
            "lockDigests": {expected["expected-lock-path"]: expected["expected-lock-digest"]},
        }
        environment = {
            "EXPECTED_ENVIRONMENT": expected["expected-environment"],
            "EXPECTED_LOCK_PATH": expected["expected-lock-path"],
            "EXPECTED_LOCK_DIGEST": expected["expected-lock-digest"],
        }
        with (
            patch.dict(os.environ, environment),
            patch.object(runtime, "inputs", return_value={"preflight_result": valid}),
        ):
            runtime.run_selection()
        for bad in (
            {**valid, "environment": "other"},
            {**valid, "lockDigests": {}},
            {**valid, "lockDigests": {expected["expected-lock-path"]: "sha256:" + "0" * 64}},
        ):
            with (
                patch.dict(os.environ, environment),
                patch.object(runtime, "inputs", return_value={"preflight_result": bad}),
                self.assertRaises(policy.ReadinessRefused),
            ):
                runtime.run_selection()

    @unittest.skipUnless(BASH, "Bash required")
    def test_actual_request_hash_gate_stops_before_validator_and_propagates_refusal(self):
        assert BASH
        script = self.templates["migration-intent"]["container"]["args"][0].replace(
            "/usr/local/bin/jsonschema", '"$VALIDATOR"'
        )
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            request = directory / "deployments/release-requests/migration.json"
            request.parent.mkdir(parents=True)
            request.write_bytes(b'{"fixture":"exact bytes"}\n')
            validator = directory / "validator.sh"
            validator.write_text(
                '#!/bin/sh\nprintf validated > "$MARKER"\nexit "$VALIDATOR_EXIT"\n', newline="\n"
            )
            validator.chmod(0o700)
            marker = directory / "marker"
            for label, digest, exit_code in (
                ("valid", hashlib.sha256(request.read_bytes()).hexdigest(), 0),
                ("wrong-bytes", "0" * 64, 0),
                ("policy-refusal", hashlib.sha256(request.read_bytes()).hexdigest(), 23),
            ):
                if marker.exists():
                    marker.unlink()
                process = subprocess.run(
                    [BASH, "-ec", script],
                    env={
                        **os.environ,
                        "BASH_ENV": "",
                        "REQUEST_ROOT": directory.as_posix(),
                        "REQUEST_PATH": "deployments/release-requests/migration.json",
                        "REQUEST_SHA256": "sha256:" + digest,
                        "VALIDATOR": validator.as_posix(),
                        "MARKER": marker.as_posix(),
                        "VALIDATOR_EXIT": str(exit_code),
                    },
                    capture_output=True,
                    timeout=30,
                )
                with self.subTest(label=label):
                    self.assertEqual(
                        process.returncode,
                        1 if label == "wrong-bytes" else exit_code,
                        process.stderr,
                    )
                    self.assertEqual(marker.exists(), label != "wrong-bytes")

    def test_request_bytes_and_pinned_validator_use_immutable_policy(self):
        intent = self.templates["migration-intent"]
        git = intent["inputs"]["artifacts"][0]["git"]
        self.assertEqual(git["revision"], "{{workflow.parameters.request-revision}}")
        self.assertTrue(git["disableSubmodules"])
        self.assertEqual(git["passwordSecret"]["name"], "release-git-read")
        container = intent["container"]
        source = yaml.safe_load((DIRECTORY / "release-preflight-v3.yaml").read_text())
        self.assertEqual(container["image"], source["spec"]["templates"][1]["container"]["image"])
        script = container["args"][0]
        self.assertLess(script.index("sha256sum -c -"), script.index("jsonschema validate"))
        self.assertEqual(container["command"], ["/bin/sh", "-ec"])
        self.assertNotIn("{{", script)
        self.assertEqual(intent["activeDeadlineSeconds"], 60)
        kustomization = yaml.safe_load((DIRECTORY / "kustomization.yaml").read_text())
        self.assertIn("schema-migration-intent-v1.yaml", kustomization["resources"])
        self.assertTrue(self.configmap["immutable"])
        configmap = deepcopy(self.configmap)
        bootstrap = ROOT / "gitops/release-workflows-bootstrap"
        project = yaml.safe_load((bootstrap / "project.yaml").read_text())
        application = yaml.safe_load((bootstrap / "application.yaml").read_text())
        validate_cluster.validate_release_project(
            project, application, [self.parent, self.leaf, configmap]
        )
        configmap["data"] = {}
        with self.assertRaisesRegex(ValueError, "Workflow ConfigMap"):
            validate_cluster.validate_release_project(
                project, application, [self.parent, self.leaf, configmap]
            )

    def test_native_job_is_fixed_hardened_bounded_and_retained(self):
        template = self.leaf["spec"]["templates"][0]
        resource = template["resource"]
        self.assertEqual(resource["action"], "create")
        self.assertEqual(resource["successCondition"], "status.succeeded > 0")
        self.assertEqual(resource["failureCondition"], "status.failed > 0")
        self.assertEqual(template["activeDeadlineSeconds"], 240)
        self.assertNotIn("setOwnerReference", resource)
        self.assertEqual(self.job["metadata"]["name"], "perfeng-schema-{{workflow.uid}}")
        self.assertNotIn("ownerReferences", self.job["metadata"])
        self.assertEqual(self.job["spec"]["backoffLimit"], 0)
        self.assertEqual(self.job["spec"]["activeDeadlineSeconds"], 180)
        self.assertNotIn("ttlSecondsAfterFinished", self.job["spec"])
        pod = self.job["spec"]["template"]["spec"]
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertEqual(pod["restartPolicy"], "Never")
        self.assertEqual(pod["imagePullSecrets"], [{"name": "perfeng-control-plane-registry"}])
        self.assertEqual(pod["nodeSelector"], {"workload": "control-plane"})
        self.assertEqual(len(pod["containers"]), 1)
        container = pod["containers"][0]
        lock = json.loads((ROOT / "deployments/local-control-plane.json").read_text())
        self.assertEqual(container["image"], lock["controlPlane"]["image"])
        self.assertEqual(container["command"], ["/usr/local/bin/migrate"])
        self.assertEqual(
            container["env"][0]["valueFrom"]["secretKeyRef"],
            {"name": "perfeng-control-plane-database", "key": "migration-url"},
        )
        self.assertTrue(container["securityContext"]["readOnlyRootFilesystem"])
        self.assertFalse(container["securityContext"]["allowPrivilegeEscalation"])
        self.assertEqual(container["securityContext"]["capabilities"], {"drop": ["ALL"]})
        self.assertTrue(pod["securityContext"]["runAsNonRoot"])

    def test_authority_is_separate_and_staging_excludes_active_root(self):
        objects = [
            o
            for path in (DIRECTORY / "rbac/schema-migration").glob("*.yaml")
            for o in yaml.safe_load_all(path.read_text())
        ]
        roles = {o["metadata"]["namespace"]: o["rules"] for o in objects if o["kind"] == "Role"}
        platform = roles["perf-platform"]
        self.assertEqual(
            platform,
            [
                {
                    "apiGroups": ["apps"],
                    "resources": ["deployments"],
                    "resourceNames": ["control-plane"],
                    "verbs": ["get"],
                },
                {"apiGroups": [""], "resources": ["pods"], "verbs": ["list"]},
                {
                    "apiGroups": ["batch"],
                    "resources": ["jobs"],
                    "verbs": ["create", "get", "watch"],
                },
            ],
        )
        secret = next(r for r in roles["argo-workflows"] if "secrets" in r["resources"])
        self.assertEqual(
            secret,
            {
                "apiGroups": [""],
                "resources": ["secrets"],
                "resourceNames": ["release-git-read"],
                "verbs": ["get"],
            },
        )
        cluster = next(o for o in objects if o["kind"] == "ClusterRole")
        self.assertEqual(
            cluster["rules"],
            [
                {
                    "apiGroups": [""],
                    "resources": ["namespaces"],
                    "resourceNames": ["kube-system"],
                    "verbs": ["get"],
                }
            ],
        )
        for binding in (o for o in objects if o["kind"].endswith("Binding")):
            self.assertEqual(
                binding["subjects"],
                [
                    {
                        "kind": "ServiceAccount",
                        "name": "release-schema-migration",
                        "namespace": "argo-workflows",
                    }
                ],
            )
        bootstrap = list(
            yaml.safe_load_all(
                (
                    ROOT
                    / "gitops/release-workflows-bootstrap/schema-migration-application-access.yaml"
                ).read_text()
            )
        )
        self.assertEqual(
            bootstrap[0]["rules"],
            [
                {
                    "apiGroups": ["argoproj.io"],
                    "resources": ["applications"],
                    "resourceNames": ["perfeng-local-root", "perfeng-local-control-plane"],
                    "verbs": ["get"],
                }
            ],
        )
        active = (ROOT / "gitops/environments/local/kustomization.yaml").read_text()
        self.assertNotIn("release-workflows", active)


if __name__ == "__main__":
    unittest.main()
